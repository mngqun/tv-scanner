"""Unusual options activity scan from Cboe's delayed-quotes feed (official exchange data, ~15 min delay).

Runs on GitHub Actions after the US close. Universe = liquid US large caps from the TradingView
scanner (same exclusion rules as the trading framework). Writes scans/options_latest.json.

A contract is flagged "unusual" when:
  - volume >= 500 contracts
  - volume / open interest >= 3   (new positioning, not existing OI churning)
  - 2 <= days to expiry <= 45  (0-1 DTE excluded: mostly same-day hedging/gamma noise)
  - premium (volume * last price * 100) >= $250k
Per ticker it also reports call vs put premium and the call/put volume ratio.
"""
import json
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TV_URL = "https://scanner.tradingview.com/america/scan?label-product=screener-stock"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{t}.json"
UA = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
OCC = re.compile(r"^(?P<root>[A-Z.]+)(?P<y>\d\d)(?P<m>\d\d)(?P<d>\d\d)(?P<cp>[CP])(?P<k>\d{8})$")

MIN_VOL, MIN_VOL_OI, MIN_DTE, MAX_DTE, MIN_PREMIUM = 500, 3.0, 2, 45, 250_000


def universe(n: int = 120) -> list[str]:
    """Most actively traded US common stocks, mkt cap >= $10B, price >= $20."""
    payload = {
        "columns": ["name", "close", "market_cap_basic", "volume"],
        "filter": [
            {"left": "close", "operation": "egreater", "right": 20},
            {"left": "market_cap_basic", "operation": "egreater", "right": 10_000_000_000},
            {"left": "average_volume_10d_calc", "operation": "greater", "right": 1_000_000},
            {"left": "exchange", "operation": "in_range", "right": ["NYSE", "NASDAQ", "AMEX"]},
        ],
        "filter2": {"operator": "and", "operands": [
            {"expression": {"left": "type", "operation": "equal", "right": "stock"}},
            {"expression": {"left": "typespecs", "operation": "has", "right": ["common"]}},
        ]},
        "options": {"lang": "en"}, "markets": ["america"], "symbols": {},
        "sort": {"sortBy": "Value.Traded", "sortOrder": "desc"}, "range": [0, n],
    }
    req = urllib.request.Request(TV_URL, data=json.dumps(payload).encode(), headers=UA, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return [row["d"][0] for row in json.load(r).get("data", [])]


def scan_ticker(t: str, today: date) -> dict | None:
    req = urllib.request.Request(CBOE_URL.format(t=t.replace(".", "/")), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    opts = data.get("data", {}).get("options", [])
    call_prem = put_prem = call_vol = put_vol = 0.0
    flagged = []
    for o in opts:
        m = OCC.match(o.get("option", ""))
        if not m:
            continue
        vol, oi = float(o.get("volume") or 0), float(o.get("open_interest") or 0)
        last = float(o.get("last_trade_price") or 0)
        prem = vol * last * 100
        if m["cp"] == "C":
            call_prem += prem; call_vol += vol
        else:
            put_prem += prem; put_vol += vol
        expiry = date(2000 + int(m["y"]), int(m["m"]), int(m["d"]))
        dte = (expiry - today).days
        ratio = vol / oi if oi else float("inf")
        if vol >= MIN_VOL and ratio >= MIN_VOL_OI and MIN_DTE <= dte <= MAX_DTE and prem >= MIN_PREMIUM:
            flagged.append({
                "contract": o["option"], "type": "call" if m["cp"] == "C" else "put",
                "strike": int(m["k"]) / 1000, "expiry": expiry.isoformat(), "dte": dte,
                "volume": int(vol), "open_interest": int(oi),
                "vol_oi": round(ratio, 1) if oi else None, "last": last,
                "bid": o.get("bid"), "ask": o.get("ask"), "premium_usd": round(prem),
                "iv": o.get("iv"), "delta": o.get("delta"),
            })
    if not flagged:
        return None
    flagged.sort(key=lambda x: x["premium_usd"], reverse=True)
    return {
        "ticker": t, "cboe_timestamp": data.get("timestamp"),
        "call_premium_usd": round(call_prem), "put_premium_usd": round(put_prem),
        "call_put_volume_ratio": round(call_vol / put_vol, 2) if put_vol else None,
        "flagged_contracts": flagged[:8],
    }


def main() -> int:
    now = datetime.now(timezone.utc)
    ny = now.astimezone(ZoneInfo("America/New_York"))
    out = {"generated_utc": now.isoformat(timespec="seconds"),
           "generated_new_york": ny.isoformat(timespec="seconds"),
           "source": "Cboe delayed quotes (cdn.cboe.com), ~15 min delayed",
           "criteria": {"min_dte": MIN_DTE, "min_volume": MIN_VOL, "min_vol_oi": MIN_VOL_OI, "max_dte": MAX_DTE, "min_premium_usd": MIN_PREMIUM},
           "results": [], "errors": {}}
    try:
        tickers = universe()
    except Exception as e:
        out["errors"]["universe"] = f"{type(e).__name__}: {e}"
        tickers = []
    for t in tickers:
        try:
            res = scan_ticker(t, ny.date())
            if res:
                out["results"].append(res)
        except Exception as e:
            out["errors"][t] = f"{type(e).__name__}: {e}"
        time.sleep(0.4)
    out["results"].sort(key=lambda r: r["flagged_contracts"][0]["premium_usd"], reverse=True)
    d = Path(__file__).parent / "scans"
    d.mkdir(exist_ok=True)
    text = json.dumps(out, indent=1, default=str)
    (d / "options_latest.json").write_text(text)
    (d / f"options_{ny.strftime('%Y-%m-%d_%H%M')}.json").write_text(text)
    print(f"{len(out['results'])} tickers flagged, {len(out['errors'])} errors")
    return 0 if tickers else 1


if __name__ == "__main__":
    sys.exit(main())
