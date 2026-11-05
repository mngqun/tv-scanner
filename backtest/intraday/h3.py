"""H3: post-earnings drift on the size of the EPS surprise (protocol 2026-11-05). Writes out/h3.parquet + meta.

Daily bars: research/data/ohlcv.parquet (Yahoo, adjusted, 2014 - 13 Oct 2026). Surprises: yfinance
get_earnings_dates (EPS estimate, reported, surprise %). Day 0 = report day if released before 12:00 ET,
else the next session. Long: surprise >= +5% and day-0 close > prior close; short: <= -5% and close < prior.
Enter at the day-0 close; stop 2 ATR14 (prior days) on later daily bars, gap through -> open; exit at the
close of day N (1, 2, 4). R = 2 ATR. Costs 0.05% per side (stress 0.10%).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from evaluate import pit_filter  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
COST = 0.0005


def surprises(tickers: list) -> pd.DataFrame:
    import yfinance as yf
    rows, failed = [], 0
    for n, t in enumerate(tickers):
        try:
            ed = yf.Ticker(t.replace(".", "-")).get_earnings_dates(limit=60)
            if ed is not None and len(ed):
                for ts, r in ed.iterrows():
                    ts = pd.Timestamp(ts)
                    ts = ts.tz_convert("America/New_York").tz_localize(None) if ts.tzinfo else ts
                    rows.append({"sym": t, "dt": ts, "est": r.get("EPS Estimate"), "rep": r.get("Reported EPS"),
                                 "surp": r.get("Surprise(%)")})
            else:
                failed += 1
        except Exception:  # noqa: BLE001
            failed += 1
        if n % 50 == 0:
            print("earnings", n, len(rows), flush=True)
        time.sleep(0.6)
    df = pd.DataFrame(rows)
    df.attrs["failed"] = failed
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    px = pd.read_parquet(ROOT / "research" / "data" / "ohlcv.parquet").dropna(subset=["open", "high", "low", "close"])
    px = px.sort_values(["ticker", "date"])
    tick = sorted(set(px.ticker) - {"SPY", "QQQ"})
    if a.limit:
        tick = tick[:a.limit]
    ev = surprises(tick)
    meta = {"tickers": len(tick), "failed": ev.attrs.get("failed"), "events": len(ev)}
    ev = ev[(ev.dt >= "2016-06-01") & (ev.dt <= "2026-10-30")]
    has = ev.dropna(subset=["surp", "rep", "est"])
    meta.update(events_in_range=len(ev), with_surprise=len(has),
                share_with_surprise=round(len(has) / max(1, len(ev)), 3))
    if meta["share_with_surprise"] < 0.5:
        meta["status"] = "not run: surprise history missing for most events"
        (out / "h3_meta.json").write_text(json.dumps(meta, indent=1))
        print(meta)
        return
    rows = []
    for t, g in px[px.ticker.isin(set(has.sym))].groupby("ticker"):
        g = g.reset_index(drop=True)
        dates = g.date.values.astype("datetime64[D]")
        pc = g.close.shift(1)
        tr = pd.concat([g.high - g.low, (g.high - pc).abs(), (g.low - pc).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / 14, adjust=False).mean().shift(1).values
        O, H, L, Cl, PC = g.open.values, g.high.values, g.low.values, g.close.values, pc.values
        for e in has[has.sym == t].itertuples():
            d = np.datetime64(e.dt.date(), "D")
            i = int(np.searchsorted(dates, d, side="left"))
            if e.dt.hour >= 12 and i < len(dates) and dates[i] == d:
                i += 1
            if i >= len(dates) or i < 15 or not np.isfinite(atr[i]):
                continue
            s = 1 if (e.surp >= 5 and Cl[i] > PC[i]) else -1 if (e.surp <= -5 and Cl[i] < PC[i]) else 0
            if not s:
                continue
            E, R = Cl[i], 2 * atr[i]
            stop = E - s * R
            for N in (1, 2, 4):
                if i + N >= len(dates):
                    continue
                x = Cl[i + N]
                for k in range(i + 1, i + N + 1):
                    if s * (O[k] - stop) <= 0:
                        x = O[k]
                        break
                    if (L[k] <= stop) if s > 0 else (H[k] >= stop):
                        x = stop
                        break
                gross = s * (x - E)
                rows.append({"date": pd.Timestamp(dates[i]), "sym": t, "var": f"N{N}", "side": s,
                             "fill_slot": -abs(e.surp), "entry": E, "risk_ps": R, "surp": e.surp,
                             "R": (gross - COST * (E + x)) / R, "R2": (gross - 2 * COST * (E + x)) / R})
    df = pit_filter(pd.DataFrame(rows))
    meta["trades_per_variant"] = df.groupby("var").size().to_dict()
    meta["status"] = "run"
    df.to_parquet(out / "h3.parquet")
    (out / "h3_meta.json").write_text(json.dumps(meta, indent=1, default=str))
    print(meta)


if __name__ == "__main__":
    main()
