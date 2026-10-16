"""TradingView screener scans for the daily pre-market trade brief.

Runs on GitHub Actions (which can reach scanner.tradingview.com) and commits results to
scans/latest.json (+ a dated copy) so the Claude daily brief can read them.

Universe rules (from the trading framework): any stock listed on NYSE or Nasdaq and tradable in
US hours (ADRs included), market cap >= $10B, price >= $20, liquid. No preferreds, funds or ETFs.
"""
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from curl_cffi import requests

import tv_auth

URL = "https://scanner.tradingview.com/america/scan?label-product=screener-stock"

COLUMNS = [
    "name", "description", "update_mode", "country", "type", "typespecs", "exchange", "sector", "industry",
    "market_cap_basic", "close", "change", "high", "low", "volume",
    "premarket_open", "premarket_high", "premarket_low", "premarket_close",
    "premarket_change", "premarket_gap", "premarket_volume",
    "postmarket_open", "postmarket_high", "postmarket_low", "postmarket_close",
    "postmarket_change", "postmarket_volume",
    "average_volume_10d_calc", "average_volume_30d_calc", "relative_volume_10d_calc",
    "RSI", "ATR", "ADX", "MACD.macd", "MACD.signal", "VWAP",
    "EMA20", "EMA50", "EMA200", "SMA20", "SMA50",
    "High.1M", "Low.1M", "price_52_week_high", "price_52_week_low",
    "Perf.W", "Perf.1M", "earnings_release_next_date",
    "earnings_per_share_basic_ttm", "price_earnings_ttm",
]

# Any stock listed on NYSE/Nasdaq (incl. ADRs / foreign-domiciled issuers); excludes preferreds, funds/ETFs, pre-IPO.
COMMON_ONLY = {
    "operator": "and",
    "operands": [
        {"expression": {"left": "type", "operation": "in_range", "right": ["stock", "dr"]}},  # dr = ADRs
        {"expression": {"left": "typespecs", "operation": "has_none_of", "right": ["pre-ipo", "preferred"]}},
    ],
}

BASE_FILTERS = [
    {"left": "close", "operation": "egreater", "right": 20},
    {"left": "market_cap_basic", "operation": "egreater", "right": 10_000_000_000},
    {"left": "average_volume_10d_calc", "operation": "greater", "right": 1_000_000},
    {"left": "exchange", "operation": "in_range", "right": ["NYSE", "NASDAQ", "AMEX"]},
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


HEADERS = {"Content-Type": "application/json",
           "Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"}


def run_scan(spec: dict, cookies: dict | None) -> list[dict]:
    payload = {
        "columns": COLUMNS,
        "filter": spec["filter"],
        **({"filter2": spec.get("filter2", COMMON_ONLY)} if spec.get("filter2", COMMON_ONLY) else {}),
        "ignore_unknown_fields": False,
        "options": {"lang": "en"},
        "range": [0, spec.get("limit", 50)],
        "sort": spec["sort"],
        "symbols": {},
        "markets": ["america"],
    }
    r = requests.post(URL, data=json.dumps(payload), headers=HEADERS, cookies=cookies,
                      impersonate=random.choice(["chrome", "firefox", "safari"]), timeout=30)
    r.raise_for_status()
    data = r.json()
    return [dict(zip(COLUMNS, item["d"]), symbol=item["s"]) for item in data.get("data", [])]


# Index & sector ETFs for the market-regime read (always included).
ETFS = ["SPY", "QQQ", "IWM", "DIA", "SOXX", "SMH", "XLF", "XLK", "XLE", "XLV", "XLY", "XLP",
        "XLI", "XLU", "XLRE", "XLB", "XLC", "TLT"]
SCANS["market_etfs"] = {
    "filter": [{"left": "name", "operation": "in_range", "right": ETFS},
               {"left": "exchange", "operation": "in_range", "right": ["AMEX", "NASDAQ", "NYSE", "CBOE"]}],
    "filter2": None,
    "sort": {"sortBy": "name", "sortOrder": "asc"},
    "limit": 60,
}


def watchlist_spec(tickers: list[str]) -> dict:
    return {"filter": [{"left": "name", "operation": "in_range", "right": tickers},
                       {"left": "exchange", "operation": "in_range", "right": ["AMEX", "NASDAQ", "NYSE", "CBOE"]}],
            "filter2": None,
            "sort": {"sortBy": "name", "sortOrder": "asc"}, "limit": len(tickers) * 3}


def main() -> int:
    tickers = [t.strip().upper() for t in os.environ.get("TICKERS", "").replace(" ", ",").split(",") if t.strip()]
    if tickers:
        SCANS["watchlist"] = watchlist_spec(tickers)
    now_utc = datetime.now(timezone.utc)
    auth = tv_auth.retrieve_tradingview_cookie()

    def scan_all(cookies):
        res, errs = {}, {}
        for name, spec in SCANS.items():
            try:
                res[name] = run_scan(spec, cookies)
            except Exception as e:  # keep other scans if one fails
                errs[name] = f"{type(e).__name__}: {e}"
        return res, errs

    def is_realtime(res):
        modes = [r.get("update_mode") for rows in res.values() for r in rows]
        return any(m == "streaming" for m in modes)

    scans, errors = scan_all(auth["cookies"])
    # Same as stock-screener: if logged in but data is still delayed, clear the stored cookies,
    # log in again once and re-scan.
    if auth["cookies"] and scans and not is_realtime(scans) and auth["_id"] is not None:
        tv_auth.invalidate(auth["_id"])
        auth = tv_auth.retrieve_tradingview_cookie()
        if auth["cookies"]:
            scans, errors = scan_all(auth["cookies"])
    realtime = bool(scans) and is_realtime(scans)
    if auth["cookies"] and scans and not realtime and auth["_id"] is not None:
        tv_auth.invalidate(auth["_id"])  # leave it cleared for the next run
    out = {
        "generated_utc": now_utc.isoformat(timespec="seconds"),
        "generated_new_york": now_utc.astimezone(ZoneInfo("America/New_York")).isoformat(timespec="seconds"),
        "authenticated": bool(auth["cookies"]),
        "auth_source": auth["source"],
        "realtime": realtime,
        "scans": scans,
        "errors": errors,
    }

    scans_dir = Path(__file__).parent / "scans"
    scans_dir.mkdir(exist_ok=True)
    stamp = now_utc.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d_%H%M")
    text = json.dumps(out, indent=1, default=str)
    (scans_dir / "latest.json").write_text(text)
    (scans_dir / f"{stamp}.json").write_text(text)
    print({k: len(v) for k, v in out["scans"].items()}, out["errors"],
          f"auth={out['auth_source']} realtime={out['realtime']}")
    return 0 if out["scans"] else 1


if __name__ == "__main__":
    sys.exit(main())
