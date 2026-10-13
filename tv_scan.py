"""TradingView screener scans for the daily pre-market trade brief.

Runs on GitHub Actions (which can reach scanner.tradingview.com) and commits results to
scans/latest.json (+ a dated copy) so the Claude daily brief can read them.

Universe rules (from the trading framework): US-listed common stock only (no ADRs/DRs,
preferreds or funds), market cap >= $10B, price >= $20, liquid.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

URL = "https://scanner.tradingview.com/america/scan?label-product=screener-stock"

COLUMNS = [
    "name", "description", "update_mode", "country", "type", "typespecs", "exchange", "sector", "industry",
    "market_cap_basic", "close", "change", "high", "low", "volume",
    "premarket_open", "premarket_high", "premarket_low", "premarket_close",
    "premarket_change", "premarket_gap", "premarket_volume",
    "average_volume_10d_calc", "average_volume_30d_calc", "relative_volume_10d_calc",
    "RSI", "ATR", "ADX", "MACD.macd", "MACD.signal", "VWAP",
    "EMA20", "EMA50", "EMA200", "SMA20", "SMA50",
    "High.1M", "Low.1M", "price_52_week_high", "price_52_week_low",
    "Perf.W", "Perf.1M", "earnings_release_next_date",
    "earnings_per_share_basic_ttm", "price_earnings_ttm",
]

# US common stock only: excludes DRs (foreign ADRs), preferreds, funds, pre-IPO.
COMMON_ONLY = {
    "operator": "and",
    "operands": [
        {"expression": {"left": "type", "operation": "equal", "right": "stock"}},
        {"expression": {"left": "typespecs", "operation": "has", "right": ["common"]}},
        {"expression": {"left": "typespecs", "operation": "has_none_of", "right": ["pre-ipo"]}},
    ],
}

BASE_FILTERS = [
    {"left": "close", "operation": "egreater", "right": 20},
    {"left": "market_cap_basic", "operation": "egreater", "right": 10_000_000_000},
    {"left": "average_volume_10d_calc", "operation": "greater", "right": 1_000_000},
    {"left": "exchange", "operation": "in_range", "right": ["NYSE", "NASDAQ", "AMEX"]},
    {"left": "country", "operation": "equal", "right": "United States"},  # excludes foreign ADRs (AZN, DB...)
]

SCANS = {
    # Pre-market movers up >= 1.5% vs prior close with real pre-market participation (no upper cap: earnings gaps can exceed 8%)
    "premarket_gap_up": {
        "filter": BASE_FILTERS + [
            {"left": "premarket_change", "operation": "egreater", "right": 1.5},
            {"left": "premarket_volume", "operation": "greater", "right": 50_000},
        ],
        "sort": {"sortBy": "premarket_change", "sortOrder": "desc"},
    },
    # Pre-market movers down <= -1.5% vs prior close
    "premarket_gap_down": {
        "filter": BASE_FILTERS + [
            {"left": "premarket_change", "operation": "eless", "right": -1.5},
            {"left": "premarket_volume", "operation": "greater", "right": 50_000},
        ],
        "sort": {"sortBy": "premarket_change", "sortOrder": "asc"},
    },
    # Prior-session unusual participation in trend (above EMA20 & EMA50, rel. volume >= 1.5)
    "momentum_rvol": {
        "filter": BASE_FILTERS + [
            {"left": "relative_volume_10d_calc", "operation": "egreater", "right": 1.5},
            {"left": "close", "operation": "greater", "right": "EMA20"},
            {"left": "close", "operation": "greater", "right": "EMA50"},
        ],
        "sort": {"sortBy": "relative_volume_10d_calc", "sortOrder": "desc"},
    },
}


def headers() -> dict:
    h = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0",
         "Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"}
    sid = os.environ.get("TV_SESSIONID", "").strip()
    if sid:  # logged-in session -> real-time data where the account is entitled to it
        h["Cookie"] = f"sessionid={sid}"
    return h


def run_scan(spec: dict) -> list[dict]:
    payload = {
        "columns": COLUMNS,
        "filter": spec["filter"],
        "filter2": COMMON_ONLY,
        "ignore_unknown_fields": False,
        "options": {"lang": "en"},
        "range": [0, spec.get("limit", 50)],
        "sort": spec["sort"],
        "symbols": {},
        "markets": ["america"],
    }
    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode(),
        headers=headers(),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    return [dict(zip(COLUMNS, item["d"]), symbol=item["s"]) for item in data.get("data", [])]


def watchlist_spec(tickers: list[str]) -> dict:
    return {"filter": [{"left": "name", "operation": "in_range", "right": tickers}],
            "sort": {"sortBy": "name", "sortOrder": "asc"}, "limit": len(tickers) * 3}


def main() -> int:
    tickers = [t.strip().upper() for t in os.environ.get("TICKERS", "").replace(" ", ",").split(",") if t.strip()]
    if tickers:
        SCANS["watchlist"] = watchlist_spec(tickers)
    now_utc = datetime.now(timezone.utc)
    out = {
        "generated_utc": now_utc.isoformat(timespec="seconds"),
        "generated_new_york": now_utc.astimezone(ZoneInfo("America/New_York")).isoformat(timespec="seconds"),
        "authenticated": bool(os.environ.get("TV_SESSIONID", "").strip()),
        "scans": {},
        "errors": {},
    }
    for name, spec in SCANS.items():
        try:
            out["scans"][name] = run_scan(spec)
        except Exception as e:  # keep other scans if one fails
            out["errors"][name] = f"{type(e).__name__}: {e}"

    scans_dir = Path(__file__).parent / "scans"
    scans_dir.mkdir(exist_ok=True)
    stamp = now_utc.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d_%H%M")
    text = json.dumps(out, indent=1, default=str)
    (scans_dir / "latest.json").write_text(text)
    (scans_dir / f"{stamp}.json").write_text(text)
    print({k: len(v) for k, v in out["scans"].items()}, out["errors"])
    return 0 if out["scans"] else 1


if __name__ == "__main__":
    sys.exit(main())
