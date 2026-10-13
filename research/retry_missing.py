"""Second pass: fetch tickers missing from ohlcv.parquet one at a time (Yahoo rate limits batches)."""
import json
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

D = Path(__file__).parent / "data"
meta = json.loads((D / "meta.json").read_text())
px = pd.read_parquet(D / "ohlcv.parquet")
mem = pd.read_parquet(D / "membership.parquet")
want = sorted(set(mem.ticker)) + ["SPY", "QQQ"]
missing = [t for t in want if t not in set(px.ticker)]
print("missing before:", len(missing))
got, still = [], []
for t in missing:
    df = None
    for attempt in range(3):
        try:
            df = yf.download(t.replace(".", "-"), start=meta["start"], auto_adjust=True, progress=False, threads=False)
            break
        except Exception as e:
            print("retry", t, e)
            time.sleep(10)
    if df is None or df.empty:
        still.append(t)
    else:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(how="all").reset_index().rename(columns=str.lower)
        df["ticker"] = t
        got.append(df[["date", "ticker", "open", "high", "low", "close", "volume"]])
    time.sleep(2)
if got:
    add = pd.concat(got, ignore_index=True)
    add["date"] = pd.to_datetime(add["date"]).dt.tz_localize(None)
    px = pd.concat([px, add], ignore_index=True)
    px.to_parquet(D / "ohlcv.parquet", index=False)
current = set(mem[mem.date == mem.date.max()].ticker)
meta.update({"retry_recovered": len(got), "tickers_with_prices": int(px.ticker.nunique()),
             "tickers_missing_prices": len(still),
             "missing_current_members": [t for t in still if t in current],
             "missing_removed_names": len([t for t in still if t not in current]),
             "missing_sample": still[:80], "price_rows": len(px)})
(D / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
print({k: v for k, v in meta.items() if k != "missing_sample"})
