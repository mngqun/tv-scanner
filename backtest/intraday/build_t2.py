"""T2: day-trade setups for high-volatility large caps (protocol claude/research-protocol-2026-11-06.md).

Per stock-day: features for the universe (ATR%, 20d $ volume, first-15-min relative volume) for ALL days, and
T2a / T2b / T2c trades only on pre-filtered days (ATR% >= 1.5%, $vol >= 100M, RVOL15 >= 1.5); evaluate_t2.py
then keeps the top 50 by ATR% per day. Also saves the session bar arrays (npz) for later research.
usage: python build_t2.py --part 0 --parts 4 --key A --out out_t2
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import common as C  # noqa: E402
from build import END, universe  # noqa: E402


def day_indicators(A):
    """VWAP (typical price), EMA9/21 and RSI14 on 5-min closes, per day, vectorised across days."""
    o, h, l, c, v = (A[f] for f in "ohlcv")
    tp = (h + l + c) / 3
    cv = np.cumsum(np.nan_to_num(v), axis=1)
    vwap = np.where(cv > 0, np.cumsum(np.nan_to_num(tp * v), axis=1) / np.where(cv > 0, cv, 1), tp)
    e9, e21, rsi = np.empty_like(c), np.empty_like(c), np.full_like(c, 50.0)
    e9[:, 0] = e21[:, 0] = c[:, 0]
    a9, a21 = 2 / 10, 2 / 22
    ag = np.zeros(c.shape[0])
    al = np.zeros(c.shape[0])
    for j in range(1, c.shape[1]):
        e9[:, j] = a9 * c[:, j] + (1 - a9) * e9[:, j - 1]
        e21[:, j] = a21 * c[:, j] + (1 - a21) * e21[:, j - 1]
        d = c[:, j] - c[:, j - 1]
        ag = ag + (np.clip(d, 0, None) - ag) / 14
        al = al + (np.clip(-d, 0, None) - al) / 14
        rsi[:, j] = np.where(al > 0, 100 - 100 / (1 + ag / np.where(al > 0, al, 1)), 100.0)
    return vwap, e9, e21, rsi


def exit_cross(o, h, l, c, j, fill, stop, flat, line):
    """Long view: stop (intrabar, counts in the fill bar) or the first close below `line` -> next bar open."""
    for k in range(j, flat):
        if l[k] <= stop:
            return (min(o[k], stop) if k > j else stop), "stop"
        if line is not None and c[k] < line[k]:
            return (o[k + 1] if k + 1 < len(o) else c[k]), "market"
    return o[flat], "market"


def t2a(o, h, l, c, vw, e9, e21, atr, flat):
    orh = h[:3].max()
    rows = []
    sig = C._first((c > vw) & (c > orh) & (e9 > e21), 5, 24)
    if sig < 0 or sig + 1 >= flat:
        return rows
    j = sig + 1
    E = o[j]
    stop = vw[sig] - 0.10 * atr
    R = E - stop
    if R < 0.05 * atr:
        return rows
    for var, line in (("a1", vw), ("a2", None), ("a3", e21)):
        x, kind = exit_cross(o, h, l, c, j, E, stop, flat, line)
        rows.append({"var": var, "sig_slot": sig, "fill_slot": j, "entry": abs(E), "risk_ps": R,
                     "R": C.net_R(E, "market", x, kind, R), "R2": C.net_R(E, "market", x, kind, R, 2.0)})
    return rows


def t2b(o, h, l, c, vw, gap_atr, atr, flat):
    if gap_atr < 1.0 or not c[5] > o[0]:
        return []
    rows = []
    for k in range(6, min(30, flat)):
        lim = vw[k - 1]
        if l[k] <= lim and c[5] > lim:
            fill = min(o[k], lim)
            for var, m, tgt in (("b1", 0.25, True), ("b2", 0.50, True), ("b3", 0.50, False)):
                R = m * atr
                px, kout, _ = C.exit_path(o, h, l, k, fill, lim - R, lim + 2 * R if tgt else None, flat)
                rows.append({"var": var, "sig_slot": k, "fill_slot": k, "entry": abs(fill), "risk_ps": R,
                             "R": C.net_R(fill, "limit", px, kout, R), "R2": C.net_R(fill, "limit", px, kout, R, 2.0)})
            break
    return rows


def t2c(o, h, l, c, vw, rsi_long_view, atr, flat):
    """Long view of the fade: price far BELOW VWAP with RSI < 20 -> buy, target VWAP, stop low of day - 0.1 ATR."""
    rows = []
    for var, X in (("c1", 0.75), ("c2", 1.00), ("c3", 1.25)):
        sig = C._first((c < vw - X * atr) & (rsi_long_view < 20), 11, 60)
        if sig < 0 or sig + 1 >= flat:
            continue
        j = sig + 1
        E = o[j]
        stop = l[:sig + 1].min() - 0.10 * atr
        tgt = vw[sig]
        R = E - stop
        if R <= 0 or tgt <= E:
            continue
        px, kout, _ = C.exit_path(o, h, l, j, E, stop, tgt, flat)
        rows.append({"var": var, "sig_slot": sig, "fill_slot": j, "entry": abs(E), "risk_ps": R,
                     "R": C.net_R(E, "market", px, kout, R), "R2": C.net_R(E, "market", px, kout, R, 2.0)})
    return rows


def process(sym, bars5, daily, bars_out=None):
    days, A, early = C.session_arrays(bars5)
    if not days:
        return [], []
    feat = C.daily_features(daily).reindex(days)
    v15 = np.nansum(A["v"][:, :3], axis=1)
    v15avg = pd.Series(v15).rolling(14).mean().shift(1).values
    vw, e9, e21, rsi = day_indicators(A)
    if bars_out is not None:
        bars_out[sym] = (np.array([str(d) for d in days]), *(A[f].astype(np.float32) for f in "ohlcv"), early)
    drows, trades = [], []
    for i, d in enumerate(days):
        f = feat.iloc[i]
        atr, pc, adv = f["atr"], f["prev_close"], f["adv20"]
        if not (np.isfinite(atr) and atr > 0 and np.isfinite(pc) and pc > 0):
            continue
        rv = v15[i] / v15avg[i] if np.isfinite(v15avg[i]) and v15avg[i] > 0 else np.nan
        atrp = atr / pc
        drows.append({"date": d, "sym": sym, "atrp": atrp, "adv20": adv, "rvol15": rv})
        if not (atrp >= 0.015 and (adv or 0) >= 1e8 and np.isfinite(rv) and rv >= 1.5):
            continue
        flat = C.EARLY_FLAT_SLOT if early[i] else C.FLAT_SLOT
        o, h, l, c = A["o"][i], A["h"][i], A["l"][i], A["c"][i]
        gap = (o[0] - pc) / atr
        for side in (1, -1):
            O, H, L, Cc = C.view(o, h, l, c, side)
            VW, E9, E21 = side * vw[i], side * e9[i], side * e21[i]
            R_long = rsi[i] if side > 0 else 100 - rsi[i]
            base = {"date": d, "sym": sym, "side": side}
            for r in t2a(O, H, L, Cc, VW, E9, E21, atr, flat):
                trades.append(dict(base, hyp="T2a", **r))
            for r in t2b(O, H, L, Cc, VW, side * gap, atr, flat):
                trades.append(dict(base, hyp="T2b", **r))
            # fade: the long view buys an over-extension DOWN; the short side (view -1) fades an extension UP
            for r in t2c(O, H, L, Cc, VW, R_long, atr, flat):
                trades.append(dict(base, hyp="T2c", **r))
    return drows, trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", type=int, default=0)
    ap.add_argument("--parts", type=int, default=1)
    ap.add_argument("--key", default="A")
    ap.add_argument("--out", default="out_t2")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--years", default="2016-2026")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--save-bars", action="store_true")
    a = ap.parse_args()
    key = C.keys(a.key)
    out = Path(a.out)
    (out / "bars").mkdir(parents=True, exist_ok=True)
    syms = a.symbols.split(",") if a.symbols else [s for i, s in enumerate(universe()) if i % a.parts == a.part]
    y0, y1 = (int(x) for x in a.years.split("-")) if "-" in a.years else (int(a.years), int(a.years))
    D, T, cov = [], [], {"requested": len(syms), "no_data": [], "rows": 0}
    t0 = time.time()
    for bi in range(0, len(syms), a.batch):
        batch = syms[bi:bi + a.batch]
        try:
            daily = C.fetch(batch, "1Day", f"{y0 - 1}-06-01T00:00:00Z", END, key)
        except Exception as e:  # noqa: BLE001
            cov.setdefault("failed_chunks", []).append({"symbols": batch, "year": "daily", "error": str(e)[:200]})
            cov["no_data"] += batch
            continue
        got = set()

        def chunk(y):
            start = f"{y - 1}-12-01T00:00:00Z" if y > 2016 else "2016-11-01T00:00:00Z"
            end = f"{y}-12-31T23:59:59Z" if y < 2026 else END
            return y, C.fetch(batch, "5Min", start, end, key)

        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(chunk, y): y for y in range(y0, y1 + 1)}
            for fut in as_completed(futs):
                try:
                    y, b5 = fut.result()
                except Exception as e:  # noqa: BLE001
                    cov.setdefault("failed_chunks", []).append({"symbols": batch, "year": futs[fut], "error": str(e)[:200]})
                    continue
                bars = {} if a.save_bars else None
                for s, df in b5.items():
                    if s not in daily:
                        continue
                    try:
                        d, t = process(s, df, daily[s], bars)
                    except Exception as e:  # noqa: BLE001
                        cov.setdefault("failed_symbol_years", []).append({"symbol": s, "year": y, "error": str(e)[:200]})
                        continue
                    lo = date(y, 1, 1)
                    D += [r for r in d if r["date"] >= lo]
                    T += [r for r in t if r["date"] >= lo]
                    got.add(s)
                if bars:
                    np.savez_compressed(out / "bars" / f"{y}_{batch[0]}.npz",
                                        **{f"{s}__{k}": arr for s, tup in bars.items()
                                           for k, arr in zip(("dates", "o", "h", "l", "c", "v", "early"), tup)})
                del b5
        cov["no_data"] += [s for s in batch if s not in got]
        print(f"{bi + len(batch)}/{len(syms)} symbols, {len(D)} stock-days, {len(T)} trades, {time.time() - t0:.0f}s", flush=True)
    cov["rows"] = len(D)
    pd.DataFrame(D).to_parquet(out / f"t2days_{a.part}.parquet")
    pd.DataFrame(T).to_parquet(out / f"t2trades_{a.part}.parquet")
    (out / f"t2coverage_{a.part}.json").write_text(json.dumps(cov, indent=1))
    print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in cov.items()}))


if __name__ == "__main__":
    main()
