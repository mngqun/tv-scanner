"""Protocol #4 part A (claude/research-protocol-2026-11-07.md): Stocks-in-Play 5-min ORB on all US stocks >= $5.

Stage 1 (`daily`): Alpaca asset list (active + inactive), filters out funds/warrants, fetches daily bars for all
  -> out/daily_feat.parquet (date, sym, open, atr, avgvol14, adv20, prev_close) for days with open > 5 and
     14-day average volume >= 500k (pre-eligible: wide enough to build the 14-day first-bar average).
Stage 2+3 (`intraday --part i --parts n`): for each date in this part's share of dates:
  2. the 09:30 bar of every pre-eligible symbol (date-windowed multi-symbol requests) -> RVOL
  3. the full 5-min session of the day's top 20 eligible by RVOL -> simulate k = 0.10 / 0.25 / 0.50
  -> out/trades_<i>.parquet, out/first_<i>.parquet
`evaluate`: protocol pass bar -> results/.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "intraday"))
import common as C  # noqa: E402

NY = ZoneInfo("America/New_York")
END = "2026-11-05T00:00:00Z"
KS = (0.10, 0.25, 0.50)
FUND = re.compile(r"\b(ETF|ETN|FUND|TRUST|SHARES|PROSHARES|DIREXION|ISHARES|SPDR|INVESCO|VANGUARD|INDEX|NOTES)\b", re.I)
WRU = re.compile(r"\b(WARRANTS?|RIGHTS?|UNITS?|PREFERRED|DEPOSITARY SHARES REPRESENTING)\b", re.I)


def assets(key) -> list:
    hdr = {"APCA-API-KEY-ID": key[0], "APCA-API-SECRET-KEY": key[1]}
    out = []
    for status in ("active", "inactive"):
        url = f"https://paper-api.alpaca.markets/v2/assets?status={status}&asset_class=us_equity"
        with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=120) as r:
            out += json.loads(r.read())
    keep = []
    for a in out:
        s, n = a.get("symbol", ""), a.get("name") or ""
        if a.get("exchange") not in ("NYSE", "NASDAQ", "AMEX", "ARCA", "BATS"):
            continue
        if not re.fullmatch(r"[A-Z]{1,5}", s) or FUND.search(n) or WRU.search(n):
            continue
        keep.append(s)
    return sorted(set(keep))


def fetch_split(syms, tf, a, b, key) -> dict:
    """Multi-symbol fetch; on an error (one bad symbol makes Alpaca answer 400 for the batch) split and retry."""
    try:
        return C.fetch(syms, tf, a, b, key)
    except Exception as e:  # noqa: BLE001
        if len(syms) == 1:
            print("symbol skipped", syms[0], str(e)[:60], flush=True)
            return {}
        h = len(syms) // 2
        return {**fetch_split(syms[:h], tf, a, b, key), **fetch_split(syms[h:], tf, a, b, key)}


def stage_daily(key, out: Path):
    syms = assets(key)
    print("universe", len(syms), flush=True)
    feats, have = [], 0
    for i in range(0, len(syms), 100):
        b = syms[i:i + 100]
        d = fetch_split(b, "1Day", "2016-05-01T00:00:00Z", END, key)
        for s, df in d.items():
            f = C.daily_features(df)
            f["open"] = df.assign(date=df["t"].dt.tz_convert("America/New_York").dt.date).set_index("date")["o"]
            f["sym"] = s
            f = f[(f.open > 5) & (f.avgvol14 >= 5e5)]
            if len(f):
                have += 1
                feats.append(f.reset_index()[["date", "sym", "open", "atr", "avgvol14", "adv20", "prev_close"]])
        if i % 2000 == 0:
            print(i, len(syms), have, flush=True)
    df = pd.concat(feats, ignore_index=True)
    df.to_parquet(out / "daily_feat.parquet")
    (out / "daily_meta.json").write_text(json.dumps({"universe": len(syms), "with_preeligible_days": have,
                                                     "rows": len(df)}))
    print("daily rows", len(df), flush=True)


def ny_window(d, a="09:30", b="09:35"):
    t0 = datetime.combine(d, datetime.strptime(a, "%H:%M").time(), NY)
    t1 = datetime.combine(d, datetime.strptime(b, "%H:%M").time(), NY) - timedelta(seconds=1)
    f = lambda x: x.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    return f(t0), f(t1)


def first_bars(day, syms, key) -> dict:
    a, b = ny_window(day)
    out = {}
    for i in range(0, len(syms), 200):
        res = fetch_split(syms[i:i + 200], "5Min", a, b, key)
        for s, df in res.items():
            r = df.iloc[0]
            out[s] = (float(r.o), float(r.h), float(r.l), float(r.c), float(r.v))
    return out


def tier_cost(price, adv):
    small = price < 20 or (adv or 0) < 2e7
    return {"stop": 0.0010 if small else 0.0003, "market": 0.0005 if small else 0.0002,
            "limit": 0.0001}


def simulate(A_row, atr, cost):
    o, h, l, c = (A_row[f] for f in "ohlc")
    if c[0] == o[0]:
        return []
    side = 1 if c[0] > o[0] else -1
    O, H, L, Cc = C.view(o, h, l, c, side)
    trig = H[0]
    j, fill, kin = C.entry_stop(O, H, trig, 1, C.FLAT_SLOT)
    rows = []

    def net(fill, kin, px, kout, R, mult=1.0):     # costs passed in: no shared global (threads)
        cin = cost[kin] * abs(fill) * mult
        cout = cost["limit" if kout == "target" else "stop" if kout == "stop" else "market"] * abs(px) * mult
        return (px - fill - cin - cout) / R

    for k in KS:
        R = k * atr
        r = {"side": side, "var": f"k{k:.2f}", "risk_ps": R, "filled": j >= 0}
        if j >= 0:
            px, kout, _ = C.exit_path(O, H, L, j, fill, trig - R, None, C.FLAT_SLOT)
            r.update(fill_slot=j, entry=abs(fill), R=net(fill, kin, px, kout, R), R2=net(fill, kin, px, kout, R, 2.0))
        rows.append(r)
    return rows


def stage_intraday(key, inp: Path, out: Path, part: int, parts: int):
    feat = pd.read_parquet(inp / "daily_feat.parquet")
    feat["date"] = pd.to_datetime(feat["date"]).dt.date
    dates = sorted(d for d in feat.date.unique() if d >= datetime(2016, 12, 1).date())
    n = len(dates)
    mine = dates[part * n // parts:(part + 1) * n // parts]          # contiguous block: only 20 dates of overlap
    # stage 2 needs 14 prior sessions of first-bar volume: fetch first bars for my dates AND the 20 before each
    need = set()
    for d in mine:
        k = dates.index(d)
        need.update(dates[max(0, k - 20):k + 1])
    need = sorted(need)
    by_date = {d: g for d, g in feat.groupby("date")}
    first = {}

    def get(d):
        try:
            return d, first_bars(d, sorted(by_date[d].sym), key)
        except Exception as e:  # noqa: BLE001
            print("first-bar fetch failed", d, str(e)[:100], flush=True)
            return d, None

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=3) as ex:
        for n, (d, fb) in enumerate(ex.map(get, need)):
            if fb is not None:
                first[d] = fb
            if n % 100 == 0:
                print(f"first bars {n}/{len(need)} {time.time() - t0:.0f}s", flush=True)
    fr = pd.DataFrame([(d, s, *v) for d, m in first.items() for s, v in m.items()],
                      columns=["date", "sym", "o", "h", "l", "c", "v"]).sort_values(["sym", "date"])
    fr["v14"] = fr.groupby("sym")["v"].transform(lambda x: x.shift(1).rolling(14, min_periods=10).mean())
    fr = fr.merge(feat, on=["date", "sym"], how="left")
    fr.to_parquet(out / f"first_{part}.parquet")
    elig = fr[fr.date.isin(set(mine)) & (fr.open > 5) & (fr.avgvol14 >= 1e6) & (fr.atr > 0.5) & (fr.v14 > 0)].copy()
    elig["rvol"] = elig.v / elig.v14
    elig = elig[elig.rvol >= 1.0].sort_values("rvol", ascending=False).groupby("date").head(20)
    trades = []

    def day(d):
        g = elig[elig.date == d]
        if g.empty:
            return []
        a, b = ny_window(d, "09:30", "16:00")
        try:
            bars = C.fetch(sorted(g.sym), "5Min", a, b, key)
        except Exception as e:  # noqa: BLE001
            print("session fetch failed", d, str(e)[:100], flush=True)
            return [{"date": d, "failed": True}]
        rows = []
        for r in g.itertuples():
            if r.sym not in bars:
                continue
            ds, A, early = C.session_arrays(bars[r.sym])
            if not ds or early[0]:
                continue
            for t in simulate({f: A[f][0] for f in "ohlc"}, r.atr, tier_cost(r.open, r.adv20)):
                rows.append(dict(t, date=d, sym=r.sym, rvol=r.rvol, price=r.open, adv20=r.adv20))
        return rows

    with ThreadPoolExecutor(max_workers=3) as ex:
        for n, rows in enumerate(ex.map(day, sorted(elig.date.unique()))):
            trades += rows
            if n % 100 == 0:
                print(f"sessions {n} {time.time() - t0:.0f}s", flush=True)
    pd.DataFrame(trades).to_parquet(out / f"trades_{part}.parquet")
    (out / f"meta_{part}.json").write_text(json.dumps({"dates": len(mine), "first_bar_days": len(first),
                                                       "selected": int(len(elig)), "trades": len(trades)}))


def evaluate(inp: Path, parts: int):
    from evaluate import judge
    metas = glob.glob(str(inp / "meta_*.json"))
    if len(metas) < parts:
        print("incomplete", len(metas))
        return
    tr = pd.concat([pd.read_parquet(f) for f in glob.glob(str(inp / "trades_*.parquet"))], ignore_index=True)
    fails = int(tr.get("failed", pd.Series(dtype=bool)).fillna(False).sum()) if "failed" in tr else 0
    tr = tr[tr.get("filled", False) == True].copy()  # noqa: E712
    tr["date"] = pd.to_datetime(tr["date"])
    vs = {v: g.copy() for v, g in tr.groupby("var")}
    r, md = judge("H5: Stocks-in-Play 5-min ORB, all US stocks >= $5 (top 20 RVOL)", vs, 200, t_min=3.0)
    # labelled diagnostic: $25k notional cap (the paper's leverage), never a pass criterion
    d = {v: C.sleeve(g[C.in_window(g.date, "design")], cap=25000) for v, g in vs.items()}
    meta = json.loads((inp / "daily_meta.json").read_text()) if (inp / "daily_meta.json").exists() else {}
    meta["session_fetch_failures"] = fails
    meta["parts"] = [json.loads(Path(f).read_text()) for f in sorted(metas)]
    md += ["", "Diagnostic only (design window, $25k notional cap):", "```", json.dumps(d, indent=1), "```",
           "", "Coverage:", "```", json.dumps(meta, indent=1), "```"]
    res = HERE / "results"
    res.mkdir(exist_ok=True)
    (res / "results.json").write_text(json.dumps({"H5": r, "diag_25k": d, "coverage": meta}, indent=1, default=str))
    (res / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["daily", "intraday", "evaluate"])
    ap.add_argument("--key", default="A")
    ap.add_argument("--part", type=int, default=0)
    ap.add_argument("--parts", type=int, default=4)
    ap.add_argument("--inp", default="out")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.stage == "daily":
        stage_daily(C.keys(a.key), out)
    elif a.stage == "intraday":
        stage_intraday(C.keys(a.key), Path(a.inp), out, a.part, a.parts)
    else:
        evaluate(Path(a.inp), a.parts)


if __name__ == "__main__":
    main()
