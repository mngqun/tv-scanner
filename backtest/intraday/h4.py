"""H4: mechanised TQQQ plan (protocol 2026-11-05). Writes out/h4.parquet.

Regime on QQQ's prior close: above EMA20 and SMA50 -> TQQQ, below both -> SQQQ, else flat.
p0 = the instrument's 10:00 bar open; buy-limit p0 - j x ATR14 valid to 11:30 (j=0: buy at p0);
stop entry - 0.75 ATR; half at +1R, half at +2R with the stop on that half at breakeven after T1; flat 15:40.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import common as C  # noqa: E402

END = "2026-11-05T00:00:00Z"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="A")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    key = C.keys(a.key)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    syms = ["TQQQ", "SQQQ", "QQQ"]
    daily = C.fetch(syms, "1Day", "2016-01-01T00:00:00Z", END, key)
    feats = {s: C.daily_features(daily[s]) for s in syms}
    q = feats["QQQ"]
    regime = np.where((q.prev_close > q.ema20) & (q.prev_close > q.sma50), 1,
                      np.where((q.prev_close < q.ema20) & (q.prev_close < q.sma50), -1, 0))
    regime = pd.Series(regime, index=q.index)
    arrays = {}
    for y in range(2017, 2027):
        b5 = C.fetch(["TQQQ", "SQQQ"], "5Min", f"{y}-01-01T00:00:00Z", f"{y}-12-31T23:59:59Z" if y < 2026 else END, key)
        for s, df in b5.items():
            days, A, early = C.session_arrays(df)
            for i, d in enumerate(days):
                arrays[(s, d)] = ({f: A[f][i] for f in "ohlc"}, bool(early[i]))
    rows = []
    s0, s1 = C.slot("10:00"), C.slot("11:30")
    for d, rg in regime.items():
        if rg == 0 or pd.isna(rg):
            continue
        sym = "TQQQ" if rg > 0 else "SQQQ"
        if (sym, d) not in arrays or d not in feats[sym].index:
            continue
        A, early = arrays[(sym, d)]
        atr = feats[sym].loc[d, "atr"]
        if not np.isfinite(atr) or atr <= 0:
            continue
        o, h, l = A["o"], A["h"], A["l"]
        flat = C.EARLY_FLAT_SLOT if early else C.FLAT_SLOT
        p0 = o[s0]
        for j in (0.0, 0.25, 0.5):
            if j == 0:
                jj, fill, kin, E = s0, p0, "market", p0
            else:
                E = p0 - j * atr
                jj, fill, kin = C.entry_limit(o, l, E, s0, s1)
                if jj < 0:
                    continue
            R = 0.75 * atr
            S = E - R
            x1, k1, _ = C.exit_path(o, h, l, jj, fill, S, E + R, flat)
            x2, k2, _ = C.exit_path(o, h, l, jj, fill, S, E + 2 * R, flat, be_at=E + R)
            r = (C.net_R(fill, kin, x1, k1, R) + C.net_R(fill, kin, x2, k2, R)) / 2
            r2 = (C.net_R(fill, kin, x1, k1, R, 2.0) + C.net_R(fill, kin, x2, k2, R, 2.0)) / 2
            rows.append({"date": d, "sym": sym, "var": f"j{j:.2f}", "side": 1, "fill_slot": jj, "entry": fill,
                         "risk_ps": R, "R": r, "R2": r2, "exit": f"{k1}/{k2}"})
    pd.DataFrame(rows).to_parquet(out / "h4.parquet")
    print(pd.DataFrame(rows).groupby("var").R.describe())


if __name__ == "__main__":
    main()
