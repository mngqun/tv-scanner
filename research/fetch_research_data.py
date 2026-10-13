"""Research dataset (runs on GitHub Actions; the Claude workspace cannot reach market-data sites).

1. Point-in-time S&P 500 membership 2014-today (public dataset github.com/fja05680/sp500,
   fallback: Wikipedia change log) -> reduces survivorship bias: stocks that were later removed /
   delisted are included for the dates they were members.
2. Daily OHLCV (split & dividend adjusted, Yahoo via yfinance) from 2014-01-01 for every ticker
   that was ever a member, plus SPY / QQQ.
3. Historical earnings dates per ticker (yfinance), used only to label whether a gap happened on an
   earnings day (known before the open: companies pre-announce report dates).

Outputs (committed, parquet):
  research/data/ohlcv.parquet, research/data/membership.parquet, research/data/earnings.parquet,
  research/data/meta.json (coverage stats: how many removed names Yahoo could not supply).
"""
import io
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

START = "2014-01-01"
OUT = Path(__file__).parent / "data"
UA = {"User-Agent": "Mozilla/5.0"}


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def membership_fja() -> pd.DataFrame:
    listing = json.loads(get("https://api.github.com/repos/fja05680/sp500/contents"))
    files = [f for f in listing if f["name"].startswith("S&P 500 Historical Components") and f["name"].endswith(".csv")]
    if not files:
        raise RuntimeError("no components file found")
    # pick the most recent file by the date embedded in the name
    def key(f):
        m = re.search(r"(\d{2})-(\d{2})-(\d{4})", f["name"])
        return (m.group(3), m.group(1), m.group(2)) if m else ("0", "0", "0")
    f = sorted(files, key=key)[-1]
    raw = pd.read_csv(io.BytesIO(get(f["download_url"])))
    raw.columns = [c.lower() for c in raw.columns]
    rows = []
    for _, r in raw.iterrows():
        for t in str(r["tickers"]).split(","):
            t = t.strip()
            if not t:
                continue
            m = re.match(r"^(.+)-(\d{6})$", t)  # delisted names carry a -YYYYMM suffix in this dataset
            rows.append((pd.Timestamp(r["date"]), m.group(1) if m else t))
    m = pd.DataFrame(rows, columns=["date", "ticker"])
    m["source"] = f["name"]
    return m


def membership_wiki() -> pd.DataFrame:
    """Rebuild point-in-time membership backwards from today's list using Wikipedia's change log."""
    html = get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies").decode("utf-8")
    tables = pd.read_html(io.StringIO(html))
    current = set(tables[0]["Symbol"].astype(str).str.strip())
    ch = tables[1].copy()
    ch.columns = ["_".join([str(x) for x in c]).strip() if isinstance(c, tuple) else str(c) for c in ch.columns]
    date_col = [c for c in ch.columns if "date" in c.lower()][0]
    add_col = [c for c in ch.columns if "added" in c.lower() and ("ticker" in c.lower() or "symbol" in c.lower())][0]
    rem_col = [c for c in ch.columns if "removed" in c.lower() and ("ticker" in c.lower() or "symbol" in c.lower())][0]
    ch["d"] = pd.to_datetime(ch[date_col], errors="coerce")
    ch = ch.dropna(subset=["d"]).sort_values("d", ascending=False)
    snaps = []
    members = set(current)
    today = pd.Timestamp.today().normalize()
    snaps.append((today, set(members)))
    for d in sorted(ch.d.unique(), reverse=True):
        grp = ch[ch.d == d]
        snaps.append((pd.Timestamp(d), set(members)))  # set valid from d onward
        for a in grp[add_col].dropna().astype(str):
            members.discard(a.strip())
        for r in grp[rem_col].dropna().astype(str):
            if r.strip() and r.strip().lower() != "nan":
                members.add(r.strip())
    snaps.append((pd.Timestamp("1990-01-01"), set(members)))
    rows = [(d, t) for d, ms in snaps for t in ms]
    m = pd.DataFrame(rows, columns=["date", "ticker"])
    # a snapshot dated d lists members valid from d; shift so 'latest snapshot before trade date' is correct
    return m


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "start": START}

    mem = membership_wiki()
    meta["membership_source"] = "Wikipedia List_of_S&P_500_companies (current list + change log, rebuilt backwards)"
    meta["membership_snapshots"] = int(mem.date.nunique())
    # cross-check against fja05680 snapshots (2013-2019) where available
    try:
        fja = membership_fja()
        meta["fja_file"] = fja.source.iloc[0]
        meta["fja_range"] = [str(fja.date.min().date()), str(fja.date.max().date())]
        snaps = sorted(mem.date.unique())
        agree = []
        for d in sorted(fja.date.unique())[::20]:
            if d < pd.Timestamp("2014-01-01"):
                continue
            prior = [s for s in snaps if s <= d]
            if not prior:
                continue
            w = set(mem[mem.date == prior[-1]].ticker)
            f = set(fja[fja.date == d].ticker)
            agree.append(len(w & f) / max(len(f), 1))
        meta["wiki_vs_fja_overlap_mean"] = round(sum(agree) / len(agree), 3) if agree else None
    except Exception as e:
        meta["fja_check_error"] = str(e)
    mem = mem[mem.date >= pd.Timestamp("2013-01-01")]
    mem[["date", "ticker"]].to_parquet(OUT / "membership.parquet", index=False)
    tickers = sorted(set(mem.ticker)) + ["SPY", "QQQ"]
    current = set(mem[mem.date == mem.date.max()].ticker)
    meta["membership_range"] = [str(mem.date.min().date()), str(mem.date.max().date())]
    meta["tickers_ever_members"] = len(tickers) - 2
    meta["tickers_current_members"] = len(current)
    print(meta)

    frames, missing = [], []
    yf_t = [t.replace(".", "-") for t in tickers]
    for i in range(0, len(yf_t), 80):
        batch = yf_t[i:i + 80]
        df = None
        for attempt in range(3):
            try:
                df = yf.download(batch, start=START, auto_adjust=True, group_by="ticker", progress=False, threads=True)
                break
            except Exception as e:
                print("retry", attempt, e)
                time.sleep(15)
        for t in batch:
            try:
                sub = df[t].dropna(how="all")
            except Exception:
                sub = pd.DataFrame()
            if sub.empty:
                missing.append(t.replace("-", "."))
                continue
            sub = sub.reset_index().rename(columns=str.lower)
            sub["ticker"] = t.replace("-", ".")
            frames.append(sub[["date", "ticker", "open", "high", "low", "close", "volume"]])
        time.sleep(3)
    px = pd.concat(frames, ignore_index=True)
    px["date"] = pd.to_datetime(px["date"]).dt.tz_localize(None)
    px.to_parquet(OUT / "ohlcv.parquet", index=False)
    meta["tickers_with_prices"] = int(px.ticker.nunique())
    meta["tickers_missing_prices"] = len(missing)
    meta["missing_removed_names"] = len([t for t in missing if t not in current])
    meta["missing_sample"] = missing[:60]
    meta["price_rows"] = len(px)

    # earnings dates
    rows, failed = [], 0
    have = sorted(set(px.ticker) - {"SPY", "QQQ"})
    for n, t in enumerate(have):
        try:
            ed = yf.Ticker(t.replace(".", "-")).get_earnings_dates(limit=60)
            if ed is not None and len(ed):
                for ts in ed.index:
                    rows.append((t, pd.Timestamp(ts).tz_localize(None) if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts)))
            else:
                failed += 1
        except Exception:
            failed += 1
        if n % 50 == 0:
            print("earnings", n, len(rows))
        time.sleep(0.6)
    er = pd.DataFrame(rows, columns=["ticker", "datetime"])
    er.to_parquet(OUT / "earnings.parquet", index=False)
    meta["earnings_rows"] = len(er)
    meta["earnings_tickers"] = int(er.ticker.nunique()) if len(er) else 0
    meta["earnings_failed"] = failed
    meta["earnings_min_date"] = str(er.datetime.min()) if len(er) else None
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    print({k: v for k, v in meta.items() if k != "missing_sample"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
