"""T1: riding short-term trends on daily bars (protocol claude/research-protocol-2026-11-06.md, frozen).

T1a pull-back inside a trend (RSI(2)), T1b breakout continuation (20-day closing high + ADX + volume).
Entry at the signal close; costs 0.05% per side (stress 0.10%). Writes results/ (md + json).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "backtest" / "intraday"))
from evaluate import judge, pit_filter  # noqa: E402

COST = 0.0005


def wilder(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(alpha=1 / n, adjust=False).mean()


def indicators(g: pd.DataFrame) -> pd.DataFrame:
    c, h, l = g.close, g.high, g.low
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    g = g.assign(atr=wilder(tr, 14), sma5=c.rolling(5).mean(), sma50=c.rolling(50).mean(),
                 sma200=c.rolling(200).mean(), ema10=c.ewm(span=10, adjust=False).mean())
    d = c.diff()
    up, dn = wilder(d.clip(lower=0), 2), wilder((-d).clip(lower=0), 2)
    g["rsi2"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    g.loc[dn == 0, "rsi2"] = 100.0
    pdm = (h.diff()).where((h.diff() > -l.diff()) & (h.diff() > 0), 0.0)
    ndm = (-l.diff()).where((-l.diff() > h.diff()) & (-l.diff() > 0), 0.0)
    atr14 = wilder(tr, 14)
    pdi, ndi = 100 * wilder(pdm, 14) / atr14, 100 * wilder(ndm, 14) / atr14
    g["adx"] = wilder(100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan), 14)
    g["sma50_10"] = g.sma50.shift(10)
    g["hi20"] = c.shift(1).rolling(20).max()
    g["lo20"] = c.shift(1).rolling(20).min()
    g["vr"] = g.volume / g.volume.shift(1).rolling(20).mean()
    return g


def simulate(g: pd.DataFrame, sig: np.ndarray, side: np.ndarray, kstop: float, exit_rule: str) -> list:
    """One position per ticker per variant at a time. Returns trade dicts."""
    O, H, L, C = g.open.values, g.high.values, g.low.values, g.close.values
    S5, E10, A = g.sma5.values, g.ema10.values, g.atr.values
    dates = g.date.values
    out, busy_until = [], -1
    for i in np.flatnonzero(sig):
        if i <= busy_until or i + 1 >= len(C):
            continue
        s = side[i]
        E, R = C[i], kstop * A[i]
        stop = E - s * R
        hc = E
        x, j = None, i
        for k in range(1, 5):
            j = i + k
            if j >= len(C):
                j = len(C) - 1
                x = C[j]
                break
            if exit_rule == "chand":
                stop = max(stop, hc - 2 * A[i]) if s > 0 else min(stop, hc + 2 * A[i])
            if s * (O[j] - stop) <= 0:
                x = O[j]
                break
            if (L[j] <= stop) if s > 0 else (H[j] >= stop):
                x = stop
                break
            hc = max(hc, C[j]) if s > 0 else min(hc, C[j])
            if exit_rule == "sma5" and s * (C[j] - S5[j]) > 0:
                x = C[j]
                break
            if exit_rule == "ema10" and s * (C[j] - E10[j]) < 0:
                x = C[j]
                break
            if k == 4:
                x = C[j]
        busy_until = j
        gross = s * (x - E)
        out.append({"i": i, "date": pd.Timestamp(dates[i]), "exit_date": pd.Timestamp(dates[j]), "side": int(s),
                    "entry": E, "risk_ps": R, "R": (gross - COST * (E + x)) / R,
                    "R2": (gross - 2 * COST * (E + x)) / R})
    return out


def sleeve_daily(df: pd.DataFrame, risk=100, cap=5000, per_day=2, max_open=4, day_stop=-200) -> dict:
    """2 new a day (best rank first), at most 4 open, no new entry after the entry day's realised P/L <= -200."""
    df = df.sort_values(["date", "rank"])
    open_pos, pnl_day, n = [], defaultdict(float), 0
    for d, g in df.groupby("date"):
        open_pos = [e for e in open_pos if e > d]
        k = 0
        for r in g.itertuples():
            if k >= per_day or len(open_pos) >= max_open or pnl_day[d] <= day_stop:
                break
            sh = int(min(risk / r.risk_ps, cap / r.entry)) if r.risk_ps > 0 else 0
            if sh < 1:
                continue
            pnl_day[r.exit_date] += sh * r.risk_ps * r.R
            open_pos.append(r.exit_date)
            k += 1
            n += 1
    vals = [pnl_day[k] for k in sorted(pnl_day)] or [0]
    eq = np.cumsum(vals)
    dd = float(np.max(np.maximum.accumulate(np.r_[0, eq])[1:] - eq)) if len(eq) else 0.0
    return {"trades": n, "total_usd": round(float(sum(vals)), 0), "worst_day": round(float(min(vals)), 0),
            "max_dd_usd": round(dd, 0)}


def main():
    px = pd.read_parquet(ROOT / "research" / "data" / "ohlcv.parquet").dropna(subset=["open", "high", "low", "close"])
    px = px[(px.open > 0) & (px.high >= px.low)].sort_values(["ticker", "date"])
    px = px[~px.ticker.isin(["SPY", "QQQ"])]
    parts = [indicators(g) for _, g in px.groupby("ticker")]
    px = pd.concat(parts, ignore_index=True)
    px = px.rename(columns={"ticker": "sym"})
    px["date"] = pd.to_datetime(px["date"])
    px = pit_filter(px)                       # members only (signals and ranking)
    px["atrp"] = px.atr / px.close
    px["atr_terc"] = px.groupby("date")["atrp"].rank(pct=True)
    trades = defaultdict(list)
    for sym, g in px.groupby("sym"):
        g = g.reset_index(drop=True)
        up = (g.close > g.sma200) & (g.sma50 > g.sma50_10)
        dn = (g.close < g.sma200) & (g.sma50 < g.sma50_10)
        for v, th, hv in (("a1", 10, False), ("a2", 5, False), ("a3", 10, True)):
            lo, sh = up & (g.rsi2 < th), dn & (g.rsi2 > 100 - th)
            if hv:
                lo, sh = lo & (g.atr_terc >= 2 / 3), sh & (g.atr_terc >= 2 / 3)
            sig = (lo | sh).values
            side = np.where(lo, 1, -1)
            for t in simulate(g, sig, side, 2.5, "sma5"):
                t.update(sym=sym, rank=float(g.rsi2.iat[t["i"]] if t["side"] > 0 else 100 - g.rsi2.iat[t["i"]]))
                trades["T1a:" + v].append(t)
        blo = (g.close > g.hi20) & (g.adx > 25) & (g.vr > 1.5) & (g.close > g.sma50)
        bsh = (g.close < g.lo20) & (g.adx > 25) & (g.vr > 1.5) & (g.close < g.sma50)
        sig = (blo | bsh).values
        side = np.where(blo, 1, -1)
        for v, rule in (("b1", "ema10"), ("b2", "chand"), ("b3", "fixed")):
            for t in simulate(g, sig, side, 2.0, rule):
                t.update(sym=sym, rank=-float(g.vr.iat[t["i"]]))
                trades["T1b:" + v].append(t)
    out, md = {}, ["# T1 results (protocol 2026-11-06, frozen)", ""]
    for hyp, title in (("T1a", "T1a: pull-back inside a trend (RSI(2), exit above SMA5, max 4 sessions)"),
                       ("T1b", "T1b: breakout continuation (20-day closing high, ADX>25, volume 1.5x)")):
        vs = {k.split(":")[1]: pd.DataFrame(v) for k, v in trades.items() if k.startswith(hyp)}
        r, m = judge(title, vs, 200, t_min=3.0, sleeve_fn=sleeve_daily)
        out[hyp], md = r, md + m
    res = HERE / "results"
    res.mkdir(exist_ok=True)
    (res / "results.json").write_text(json.dumps(out, indent=1, default=str))
    (res / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
