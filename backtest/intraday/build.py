"""Build per-stock-day features and H1 / H2 trade outcomes from Alpaca 5-min SIP bars.

usage: python build.py --part 0 --parts 2 --key A --out out/   [--symbols AAPL,MSFT --years 2026]
Protocol: claude/research-protocol-2026-11-05.md (frozen 5 Nov 2026).
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

ROOT = Path(__file__).resolve().parents[2]
END = "2026-11-05T00:00:00Z"
KS = (0.10, 0.25, 0.50)


def universe() -> list:
    m = pd.read_parquet(ROOT / "research" / "data" / "membership.parquet")
    m = m[m.date >= "2016-10-01"]
    return sorted(m.ticker.unique())


def sim_h1(A, i, flat, side):
    o, h, l, c = C.view(A["o"][i], A["h"][i], A["l"][i], A["c"][i], side)
    orh, orl = h[:3].max(), l[:3].min()
    mid = (orh + orl) / 2
    rows = []
    sig = C._first(c > orh, 3, C.slot("11:30"))
    if sig < 0:
        return rows
    base = {"side": side, "sig_slot": sig}
    E, S = c[sig], mid
    R = E - S
    # a. as run: signal by the 10:15 bar, placed at the 10:25 bar open, window 0.2R, cancel 11:30
    if sig <= C.slot("10:15") and R >= 0.01:
        pl = C.slot("10:25")
        p = o[pl]
        r = dict(base, var="a", risk_ps=R, filled=False)
        if p - E <= 0.2 * R and p > S:
            if p >= E:
                j, fill, kin = C.entry_limit(o, l, E + 0.1 * R, pl, C.slot("11:30"))
            else:
                j, fill, kin = C.entry_stop_limit(o, h, l, E, E + 0.1 * R, pl, C.slot("11:30"))
            if j >= 0:
                px, kout, xs = C.exit_path(o, h, l, j, fill, S, E + 2 * R, flat)
                r.update(filled=True, fill_slot=j, entry=abs(fill), exit=kout,
                         R=C.net_R(fill, kin, px, kout, R), R2=C.net_R(fill, kin, px, kout, R, 2.0))
        else:
            r["skip"] = "window"
        rows.append(r)
    # b / d. immediate: next bar open
    j = sig + 1
    if j < flat:
        Eb = o[j]
        Rb = Eb - S
        if Rb >= 0.01:
            for var, be in (("b", None), ("d", Eb + 0.5 * Rb)):
                px, kout, xs = C.exit_path(o, h, l, j, Eb, S, Eb + 2 * Rb, flat, be_at=be)
                rows.append(dict(base, var=var, risk_ps=Rb, filled=True, fill_slot=j, entry=abs(Eb), exit=kout,
                                 R=C.net_R(Eb, "market", px, kout, Rb), R2=C.net_R(Eb, "market", px, kout, Rb, 2.0)))
    return rows


def sim_h2(A, i, flat, atr):
    o0, c0 = A["o"][i, 0], A["c"][i, 0]
    if c0 == o0:
        return []
    side = 1 if c0 > o0 else -1
    o, h, l, c = C.view(A["o"][i], A["h"][i], A["l"][i], A["c"][i], side)
    trig = h[0]
    j, fill, kin = C.entry_stop(o, h, trig, 1, flat)
    rows = []
    for k in KS:
        R = k * atr
        r = {"side": side, "var": f"k{k:.2f}", "risk_ps": R, "filled": j >= 0}
        if j >= 0:
            px, kout, xs = C.exit_path(o, h, l, j, fill, trig - R, None, flat)
            r.update(fill_slot=j, entry=abs(fill), exit=kout, R=C.net_R(fill, kin, px, kout, R),
                     R2=C.net_R(fill, kin, px, kout, R, 2.0))
        rows.append(r)
    return rows


def process(sym, bars5, daily):
    days, A, early = C.session_arrays(bars5)
    if not days:
        return [], [], []
    feat = C.daily_features(daily).reindex(days)
    fv = pd.Series(A["v"][:, 0]).rolling(14).mean().shift(1).values
    drows, h1, h2 = [], [], []
    for i, d in enumerate(days):
        f = feat.iloc[i]
        atr = f["atr"]
        if not np.isfinite(atr) or atr <= 0:
            continue
        flat = C.EARLY_FLAT_SLOT if early[i] else C.FLAT_SLOT
        rvol = A["v"][i, 0] / fv[i] if np.isfinite(fv[i]) and fv[i] > 0 else np.nan
        base = {"date": d, "sym": sym}
        drows.append(dict(base, open=A["o"][i, 0], atr=atr, avgvol14=f["avgvol14"], adv20=f["adv20"], rvol=rvol,
                          early=bool(early[i])))
        for side in (1, -1):
            for r in sim_h1(A, i, flat, side):
                h1.append(dict(base, **r))
        if A["o"][i, 0] > 5 and (f["avgvol14"] or 0) >= 1e6 and atr > 0.5 and np.isfinite(rvol) and rvol >= 1.0:
            for r in sim_h2(A, i, flat, atr):
                h2.append(dict(base, rvol=rvol, **r))
    return drows, h1, h2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", type=int, default=0)
    ap.add_argument("--parts", type=int, default=1)
    ap.add_argument("--key", default="A")
    ap.add_argument("--out", default="out")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--years", default="2016-2026")
    ap.add_argument("--batch", type=int, default=8)
    a = ap.parse_args()
    key = C.keys(a.key)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    syms = a.symbols.split(",") if a.symbols else [s for i, s in enumerate(universe()) if i % a.parts == a.part]
    y0, y1 = (int(x) for x in a.years.split("-")) if "-" in a.years else (int(a.years), int(a.years))
    D, H1, H2, cov = [], [], [], {"requested": len(syms), "no_data": [], "rows": 0}
    t0 = time.time()
    for bi in range(0, len(syms), a.batch):
        batch = syms[bi:bi + a.batch]
        daily = C.fetch(batch, "1Day", f"{y0 - 1}-06-01T00:00:00Z", END, key)
        got = set()

        def chunk(y):
            start = f"{y - 1}-12-01T00:00:00Z" if y > 2016 else "2016-11-01T00:00:00Z"
            end = f"{y}-12-31T23:59:59Z" if y < 2026 else END
            return y, C.fetch(batch, "5Min", start, end, key)

        with ThreadPoolExecutor(max_workers=4) as ex:
            for fut in as_completed([ex.submit(chunk, y) for y in range(y0, y1 + 1)]):
                y, b5 = fut.result()
                for s, df in b5.items():
                    if s not in daily:
                        continue
                    d, h1, h2 = process(s, df, daily[s])
                    lo = date(y, 1, 1)
                    D += [r for r in d if r["date"] >= lo]
                    H1 += [r for r in h1 if r["date"] >= lo]
                    H2 += [r for r in h2 if r["date"] >= lo]
                    got.add(s)
                del b5
        cov["no_data"] += [s for s in batch if s not in got]
        print(f"{bi + len(batch)}/{len(syms)} symbols, {len(D)} stock-days, {time.time() - t0:.0f}s", flush=True)
    cov["rows"] = len(D)
    pd.DataFrame(D).to_parquet(out / f"days_{a.part}.parquet")
    pd.DataFrame(H1).to_parquet(out / f"h1_{a.part}.parquet")
    pd.DataFrame(H2).to_parquet(out / f"h2_{a.part}.parquet")
    (out / f"coverage_{a.part}.json").write_text(json.dumps(cov, indent=1))
    if a.part == 0:
        reg = C.fetch(["SPY", "QQQ"], "1Day", "2016-01-01T00:00:00Z", END, key)
        f = {s: C.daily_features(df) for s, df in reg.items()}
        r = pd.DataFrame({"spy_up": f["SPY"]["prev_close"] > f["SPY"]["ema20"],
                          "qqq_up": f["QQQ"]["prev_close"] > f["QQQ"]["ema20"]})
        r["regime"] = np.where(r.spy_up & r.qqq_up, 1, np.where(~r.spy_up & ~r.qqq_up, -1, 0))
        r.reset_index().rename(columns={"index": "date"}).to_parquet(out / "regime.parquet")
    print(json.dumps({k: (v if k != "no_data" else len(v)) for k, v in cov.items()}))


if __name__ == "__main__":
    main()
