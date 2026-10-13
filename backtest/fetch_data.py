"""Download daily OHLCV history for the backtest (runs on GitHub Actions).

Universe: every stock/ADR listed on NYSE/Nasdaq/AMEX with market cap >= $5B *today*
(wider than the live $10B rule to soften survivorship bias; the backtest applies
point-in-time price/liquidity filters itself), plus SPY/QQQ for the regime filter.

Output (committed): backtest/data/ohlcv.csv.gz  (date,ticker,open,high,low,close,volume)
                    backtest/data/universe.json
Prices are split- and dividend-adjusted (yfinance auto_adjust=True).
"""
import gzip
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

START = "2023-06-01"
TV_URL = "https://scanner.tradingview.com/america/scan?label-product=screener-stock"
OUT = Path(__file__).parent / "data"


def universe() -> list[dict]:
    payload = {
        "columns": ["name", "description", "type", "exchange", "market_cap_basic", "close", "sector"],
        "filter": [
            {"left": "market_cap_basic", "operation": "egreater", "right": 5_000_000_000},
            {"left": "exchange", "operation": "in_range", "right": ["NYSE", "NASDAQ", "AMEX"]},
        ],
        "filter2": {"operator": "and", "operands": [
            {"expression": {"left": "type", "operation": "in_range", "right": ["stock", "dr"]}},
            {"expression": {"left": "typespecs", "operation": "has_none_of", "right": ["pre-ipo", "preferred"]}},
        ]},
        "options": {"lang": "en"}, "markets": ["america"], "symbols": {},
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"}, "range": [0, 1500],
    }
    req = urllib.request.Request(TV_URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    cols = payload["columns"]
    return [dict(zip(cols, row["d"])) for row in data["data"]]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    uni = universe()
    tickers = sorted({u["name"] for u in uni}) + ["SPY", "QQQ"]
    yf_tickers = [t.replace(".", "-") for t in tickers]  # BRK.B -> BRK-B
    print(f"universe: {len(tickers)} tickers")

    frames = []
    for i in range(0, len(yf_tickers), 100):
        batch = yf_tickers[i:i + 100]
        for attempt in range(3):
            try:
                df = yf.download(batch, start=START, auto_adjust=True, group_by="ticker",
                                 progress=False, threads=True)
                break
            except Exception as e:  # rate limit / transient
                print("retry", attempt, e)
                time.sleep(10)
        for t in batch:
            try:
                sub = df[t].dropna(how="all")
            except KeyError:
                continue
            if sub.empty:
                continue
            sub = sub.reset_index().rename(columns=str.lower)
            sub["ticker"] = t.replace("-", ".")
            frames.append(sub[["date", "ticker", "open", "high", "low", "close", "volume"]])
        time.sleep(2)

    allp = pd.concat(frames, ignore_index=True)
    allp["date"] = pd.to_datetime(allp["date"]).dt.strftime("%Y-%m-%d")
    for c in ["open", "high", "low", "close"]:
        allp[c] = allp[c].round(4)
    with gzip.open(OUT / "ohlcv.csv.gz", "wt") as f:
        allp.to_csv(f, index=False)
    meta = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "start": START, "tickers_requested": len(tickers),
            "tickers_with_data": int(allp["ticker"].nunique()), "rows": len(allp),
            "universe": uni}
    (OUT / "universe.json").write_text(json.dumps(meta, indent=1))
    print({k: v for k, v in meta.items() if k != "universe"})
    return 0 if len(allp) else 1


if __name__ == "__main__":
    sys.exit(main())
