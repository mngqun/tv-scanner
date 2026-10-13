"""Research engine — see project doc research-protocol-2026-10-14.md.

All selection features are computed from data available BEFORE the decision:
  * prior-day indicators are shifted by one bar;
  * the gap uses today's open (known at 09:30 ET);
  * S&P 500 membership uses the latest snapshot dated strictly before the trade date;
  * earnings flag uses the scheduled report date/time (pre-announced).
Outcomes (returns) use only later prices.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

D = Path(__file__).parent / "data"
DESIGN = ("2015-01-01", "2020-12-31")
VALID = ("2021-01-01", "2022-12-31")
LATE = ("2023-01-01", "2026-12-31")
COST = 0.0010  # round trip, baseline (0.05% per side)


def load_panel() -> pd.DataFrame:
    px = pd.read_parquet(D / "ohlcv.parquet")
    px = px.dropna(subset=["open", "high", "low", "close"])
    px = px[(px.open > 0) & (px.high >= px.low)].sort_values(["ticker", "date"]).reset_index(drop=True)
    g = px.groupby("ticker", group_keys=False)
    pc = g["close"].shift(1)
    px["pc"] = pc
    px["tr"] = pd.concat([px.high - px.low, (px.high - pc).abs(), (px.low - pc).abs()], axis=1).max(axis=1)
    g = px.groupby("ticker", group_keys=False)
    roll = lambda col, n: g[col].transform(lambda s: s.rolling(n).mean().shift(1))  # noqa: E731
    px["atr"] = roll("tr", 14)
    px["sma10"] = roll("close", 10)
    px["sma20"] = roll("close", 20)
    px["sma50"] = roll("close", 50)
    px["avgvol20"] = roll("volume", 20)
    px["dv"] = px.close * px.volume
    px["avgdv20"] = px.groupby("ticker")["dv"].transform(lambda s: s.rolling(20).mean().shift(1))
    px["p_low"] = g["low"].shift(1)
    px["p_high"] = g["high"].shift(1)
    px["gap"] = px.open / px.pc - 1
    px["gap_atr"] = (px.open - px.pc) / px.atr
    # forward closes / extremes for outcome measurement (outcomes only, never used for selection)
    for k in range(0, 6):
        px[f"c{k}"] = g["close"].shift(-k)
        px[f"h{k}"] = g["high"].shift(-k)
        px[f"l{k}"] = g["low"].shift(-k)
    px["o1"] = g["open"].shift(-1)
    return px


def add_membership(px: pd.DataFrame) -> pd.DataFrame:
    mem = pd.read_parquet(D / "membership.parquet")
    snaps = np.sort(mem.date.unique())
    members = {d: set(mem[mem.date == d].ticker) for d in snaps}
    dates = np.sort(px.date.unique())
    # snapshot strictly before each trading date
    idx = np.searchsorted(snaps, dates, side="left") - 1
    snap_for = {d: (snaps[i] if i >= 0 else None) for d, i in zip(dates, idx)}
    px["member"] = [t in members[snap_for[d]] if snap_for[d] is not None else False
                    for t, d in zip(px.ticker, px.date)]
    return px


def add_regime(px: pd.DataFrame) -> pd.DataFrame:
    st = {}
    for t in ["SPY", "QQQ"]:
        s = px[px.ticker == t].set_index("date")["close"]
        st[t] = (s > s.ewm(span=20, adjust=False).mean()).shift(1)
    r = pd.DataFrame(st).dropna()
    reg = pd.Series(np.where(r.SPY.astype(bool) & r.QQQ.astype(bool), "long",
                             np.where(~r.SPY.astype(bool) & ~r.QQQ.astype(bool), "short", "mixed")),
                    index=r.index, name="regime")
    return px.join(reg, on="date")


def add_earnings(px: pd.DataFrame) -> pd.DataFrame:
    try:
        er = pd.read_parquet(D / "earnings.parquet")
    except Exception:
        px["earn_day"] = np.nan
        return px
    days = {}
    tdays = np.sort(px.date.unique())
    for t, dt in zip(er.ticker, er.datetime):
        d = pd.Timestamp(dt)
        day = d.normalize()
        if d.hour >= 12:  # after-close report -> gap shows on next trading day
            i = np.searchsorted(tdays, day.to_datetime64(), side="right")
            if i >= len(tdays):
                continue
            day = pd.Timestamp(tdays[i])
        days.setdefault(t, set()).add(day)
    covered = set(er.ticker)
    px["earn_day"] = [(d in days.get(t, ())) if t in covered else np.nan for t, d in zip(px.ticker, px.date)]
    # earnings coverage start per ticker (to avoid labelling pre-coverage gaps as non-earnings)
    first = er.groupby("ticker").datetime.min()
    px["earn_covered"] = [t in covered and d >= first[t].normalize() for t, d in zip(px.ticker, px.date)]
    return px


def universe(px: pd.DataFrame) -> pd.DataFrame:
    u = px[(px.member) & ~px.ticker.isin(["SPY", "QQQ"])]
    u = u.dropna(subset=["pc", "atr", "sma10", "sma20", "avgvol20", "regime"])
    return u[(u.pc >= 20) & ((u.avgvol20 >= 2e6) | (u.avgdv20 >= 2e8))]


def period(df: pd.DataFrame, p) -> pd.DataFrame:
    return df[(df.date >= p[0]) & (df.date <= p[1])]


def stats(ret: pd.Series, dates: pd.Series, label: str = "") -> dict:
    """ret = per-trade net return (fraction). Significance clustered by date."""
    r = pd.DataFrame({"r": ret.values, "d": dates.values}).dropna()
    if len(r) < 5:
        return {"label": label, "n": len(r)}
    daily = r.groupby("d").r.mean()
    t = daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily))) if len(daily) > 2 else np.nan
    return {"label": label, "n": len(r), "days": len(daily), "mean_%": round(100 * r.r.mean(), 3),
            "median_%": round(100 * r.r.median(), 3), "win": round((r.r > 0).mean(), 3),
            "t_daily": round(t, 2)}


def build() -> pd.DataFrame:
    px = load_panel()
    px = add_membership(px)
    px = add_regime(px)
    px = add_earnings(px)
    return px
