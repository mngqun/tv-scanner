"""Shared pieces for the intraday backtests (protocol: project doc claude/research-protocol-2026-11-05.md).

Alpaca SIP bars (free historical, split-adjusted), 5-min session arrays, the fill model, and statistics.
"""
from __future__ import annotations

import json
import math
import threading
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

import numpy as np
import pandas as pd

DATA = "https://data.alpaca.markets/v2/stocks/bars"
NBARS = 78                      # 09:30 .. 15:55
FLAT_SLOT = 74                  # 15:40 bar open
EARLY_FLAT_SLOT = 38            # 12:40 bar open on a 13:00 close
WINDOWS = {"design": ("2017-01-01", "2021-12-31"), "validation": ("2022-01-01", "2023-12-31"),
           "holdout": ("2024-01-01", "2026-10-30")}
COST = {"stop": 0.0003, "limit": 0.0001, "market": 0.0002}


def slot(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return (h * 60 + m - 570) // 5


# ------------------------------------------------------------------ Alpaca
def keys(which: str):
    sfx = "" if which == "A" else "_B"
    return os.environ[f"ALPACA_PAPER_KEY_ID{sfx}"], os.environ[f"ALPACA_PAPER_SECRET_KEY{sfx}"]


_LOCK = threading.Lock()
_LAST = [0.0]
MIN_INTERVAL = float(os.environ.get("ALPACA_MIN_INTERVAL", "0.65"))   # 2 jobs per key -> ~92 requests/min each


def _throttle():
    with _LOCK:
        wait = _LAST[0] + MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        _LAST[0] = time.time()


def fetch(symbols: list, timeframe: str, start: str, end: str, key) -> dict:
    """{symbol: DataFrame[t(UTC), o, h, l, c, v]}; pages through, backs off on 429."""
    out = defaultdict(list)
    tok = None
    hdr = {"APCA-API-KEY-ID": key[0], "APCA-API-SECRET-KEY": key[1]}
    while True:
        q = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": start, "end": end, "limit": "10000",
             "adjustment": "split", "feed": "sip", "sort": "asc"}
        if tok:
            q["page_token"] = tok
        url = DATA + "?" + urllib.parse.urlencode(q)
        for attempt in range(12):
            _throttle()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=60) as r:
                    d = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    time.sleep(min(60, 3 * 2 ** attempt))
                    continue
                raise
            except Exception:  # noqa: BLE001
                time.sleep(min(60, 3 * 2 ** attempt))
        else:
            raise RuntimeError(f"fetch failed {symbols[:3]}...")
        for s, bars in (d.get("bars") or {}).items():
            out[s].extend(bars)
        tok = d.get("next_page_token")
        if not tok:
            break
    res = {}
    for s, bars in out.items():
        df = pd.DataFrame(bars)
        if df.empty:
            continue
        df = df.rename(columns={"o": "o", "h": "h", "l": "l", "c": "c", "v": "v"})
        df["t"] = pd.to_datetime(df["t"], utc=True)
        res[s] = df[["t", "o", "h", "l", "c", "v"]]
    return res


def session_arrays(df: pd.DataFrame):
    """5-min bars -> dates, and (ndays, 78) arrays o,h,l,c,v (missing bars filled with the prior close, v=0),
    plus a flag for 13:00 early closes. Days without a 09:30 bar are dropped."""
    t = df["t"].dt.tz_convert("America/New_York")
    mins = t.dt.hour * 60 + t.dt.minute
    m = (mins >= 570) & (mins < 960)
    df = df[m.values].copy()
    t = t[m.values]
    if df.empty:
        return [], {}, np.array([], dtype=bool)
    df["d"] = t.dt.date.values
    df["k"] = ((t.dt.hour * 60 + t.dt.minute - 570) // 5).values
    days = sorted(df["d"].unique())
    di = {d: i for i, d in enumerate(days)}
    n = len(days)
    A = {f: np.full((n, NBARS), np.nan) for f in "ohlcv"}
    r = df["d"].map(di).values.astype(int)
    k = df["k"].values
    for f in "ohlcv":
        A[f][r, k] = df[f].values
    last = np.nanmax(np.where(np.isnan(A["c"]), -1, np.arange(NBARS)), axis=1)
    early = last < slot("13:00")
    # fill gaps with the prior close (vectorised: forward-filled close, shifted one bar)
    cf = pd.DataFrame(A["c"]).ffill(axis=1).values
    prev = np.c_[np.full((n, 1), np.nan), cf[:, :-1]]
    miss = np.isnan(A["o"]) & ~np.isnan(prev)
    for f in "ohlc":
        A[f][miss] = prev[miss]
    A["v"][miss] = 0
    keep = ~np.isnan(A["o"][:, 0])
    return [d for d, kk in zip(days, keep) if kk], {f: A[f][keep] for f in "ohlcv"}, early[keep]


def daily_features(df: pd.DataFrame) -> pd.DataFrame:
    """Daily bars -> per date (NY): close, ATR14 Wilder, 14d avg volume, 20d avg $ volume, EMA20, SMA50,
    all known BEFORE that date's open (shifted one day)."""
    d = df.copy()
    d["date"] = d["t"].dt.tz_convert("America/New_York").dt.date
    d = d.sort_values("date").set_index("date")
    pc = d["c"].shift(1)
    tr = pd.concat([d["h"] - d["l"], (d["h"] - pc).abs(), (d["l"] - pc).abs()], axis=1).max(axis=1)
    out = pd.DataFrame(index=d.index)
    out["prev_close"] = pc
    out["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean().shift(1)
    out["avgvol14"] = d["v"].rolling(14).mean().shift(1)
    out["adv20"] = (d["c"] * d["v"]).rolling(20).mean().shift(1)
    out["ema20"] = d["c"].ewm(span=20, adjust=False).mean().shift(1)
    out["sma50"] = d["c"].rolling(50).mean().shift(1)
    out["close"] = d["c"]
    return out


# ------------------------------------------------------------------ fill model
def _first(cond: np.ndarray, a: int, b: int) -> int:
    if b <= a:
        return -1
    seg = cond[a:b]
    i = int(np.argmax(seg))
    return a + i if seg[i] else -1


def view(o, h, l, c, side: int):
    """Shorts are simulated as longs on the negated series (high and low swap)."""
    return (o, h, l, c) if side > 0 else (-o, -l, -h, -c)


def entry_stop(o, h, trig, a, b):
    j = _first(h >= trig, a, b)
    return (j, max(o[j], trig), "stop") if j >= 0 else (-1, None, None)


def entry_limit(o, l, lim, a, b):
    j = _first(l <= lim, a, b)
    return (j, min(o[j], lim), "limit") if j >= 0 else (-1, None, None)


def entry_stop_limit(o, h, l, trig, lim, a, b):
    j = _first(h >= trig, a, b)
    if j < 0:
        return -1, None, None
    px = max(o[j], trig)
    if px <= lim:
        return j, px, "stop"
    k = _first(l <= lim, j, b)                    # triggered above the limit: rests as a limit
    if k < 0:
        return -1, None, None
    return k, (lim if k == j else min(o[k], lim)), "limit"


def exit_path(o, h, l, j, fill, stop, target, flat, be_at=None):
    """From fill bar j (long view). Stop counts in the fill bar; the target only from the next bar.
    A stop and a target in one bar -> stop. be_at: move the stop to the fill price from the bar after the
    one whose high reaches be_at. Returns (exit_px, kind, slot)."""
    stp = np.full(flat, stop, dtype=float)
    if be_at is not None:
        b = _first(h >= be_at, j + 1, flat)
        if b >= 0:
            stp[b + 1:] = max(stop, fill)
    hit_s = l[:flat] <= stp
    i_s = _first(hit_s, j, flat)
    i_t = _first(h >= target, j + 1, flat) if target is not None else -1
    if i_s >= 0 and (i_t < 0 or i_s <= i_t):
        return min(o[i_s], stp[i_s]) if i_s > j else stp[i_s], "stop", i_s
    if i_t >= 0:
        return max(o[i_t], target), "target", i_t
    return o[flat], "flat", flat


def net_R(fill, kind_in, exit_px, kind_out, risk, mult=1.0):
    """Long view. Costs in price terms on |price| (the negated short series keeps magnitudes)."""
    cin = COST[kind_in] * abs(fill) * mult
    cout = COST["limit" if kind_out == "target" else "stop" if kind_out == "stop" else "market"] * abs(exit_px) * mult
    return (exit_px - fill - cin - cout) / risk


# ------------------------------------------------------------------ statistics
def day_stats(df: pd.DataFrame, col: str = "R", seed: int = 7, n: int = 10000) -> dict:
    if df.empty:
        return {"n": 0}
    x = df[col].values
    g = df.groupby("date")[col]
    sums, cnts = g.sum().values, g.count().values
    mean = x.mean()
    resid = sums - cnts * mean
    se = math.sqrt((resid ** 2).sum()) / len(x)
    rnd = np.random.default_rng(seed)
    k = len(sums)
    idx = rnd.integers(0, k, size=(n, k))
    boot = np.sort(sums[idx].sum(1) / cnts[idx].sum(1))
    return {"n": int(len(x)), "days": int(k), "avg_R": round(float(mean), 4), "t_day": round(float(mean / se), 2) if se else None,
            "ci_day": [round(float(boot[int(0.025 * n)]), 4), round(float(boot[int(0.975 * n) - 1]), 4)],
            "win": round(float((x > 0).mean()), 3), "total_R": round(float(x.sum()), 1)}


def sleeve(df: pd.DataFrame, max_per_day: int = 2, risk: float = 100, cap: float = 5000, day_stop: float = -200) -> dict:
    """User sleeve: first 2 fills a day, $100 risk, $5k notional, no new trade after -$200."""
    tot, days, worst = 0.0, defaultdict(float), 0.0
    n = 0
    for d, g in df.sort_values(["date", "fill_slot"]).groupby("date"):
        pnl, k = 0.0, 0
        for r in g.itertuples():
            if k >= max_per_day or pnl <= day_stop:
                break
            sh = math.floor(min(risk / r.risk_ps, cap / r.entry)) if r.risk_ps > 0 else 0
            if sh < 1:
                continue
            p = sh * r.risk_ps * r.R
            pnl += p
            k += 1
            n += 1
        days[d] = pnl
        tot += pnl
    vals = list(days.values()) or [0]
    eq = np.cumsum(vals)
    dd = float(np.max(np.maximum.accumulate(np.r_[0, eq])[1:] - eq)) if len(eq) else 0
    return {"trades": n, "total_usd": round(tot, 0), "worst_day": round(min(vals), 0), "max_dd_usd": round(dd, 0)}


def in_window(dates: pd.Series, w: str) -> pd.Series:
    a, b = WINDOWS[w]
    s = pd.to_datetime(dates)
    return (s >= a) & (s <= b)
