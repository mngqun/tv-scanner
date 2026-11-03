"""Free market context for the daily brief and the TQQQ plan (replaces the Bigdata.com connector, 3 Nov 2026).

Runs on GitHub Actions (the Claude workspace cannot reach these sites) and writes scans/context_latest.json.
Every section records its source, fetch time and status, so the brief can cite it or list it under Data gaps.

Sections
  snapshot   VIX, 10-yr / 5-yr / 13-week yields, dollar index, WTI, Brent, gold, natural gas, S&P / Nasdaq / Dow /
             Russell futures, Bitcoin (Yahoo chart JSON, last price vs prior close); 2-yr and 10-yr Treasury
             constant-maturity yields (FRED CSV, official, end of day)
  economic   US economic calendar, today + 4 trading days, impact, forecast, previous, actual
             (TradingView economic calendar; fallback ForexFactory weekly JSON)
  earnings   US earnings for today and the next session with time (before open / after close) and EPS estimate
             (Nasdaq calendar API), filtered to our universe when the TradingView universe list is available
  news       latest market headlines + headlines for TICKERS (Alpaca news API = Benzinga; free with the paper
             account keys), with source, author, publish time, symbols and URL
"""
import csv
import io
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "scans"
NY = ZoneInfo("America/New_York")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def stamp():
    return datetime.now(timezone.utc).astimezone(NY).isoformat(timespec="seconds")


# ---------------------------------------------------------------- snapshot
YAHOO = {"VIX": "^VIX", "US10Y_yahoo": "^TNX", "US5Y_yahoo": "^FVX", "US13W": "^IRX", "DXY": "DX-Y.NYB",
         "WTI": "CL=F", "Brent": "BZ=F", "Gold": "GC=F", "NatGas": "NG=F", "ES_fut": "ES=F", "NQ_fut": "NQ=F",
         "YM_fut": "YM=F", "RTY_fut": "RTY=F", "BTC": "BTC-USD"}


def snapshot():
    out = {"source": "Yahoo Finance chart JSON (free, unofficial, may lag); FRED (official, end of day)",
           "fetched": stamp(), "items": {}, "errors": {}}
    for name, sym in YAHOO.items():
        try:
            d = json.loads(get(f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
                               f"?range=5d&interval=1d"))["chart"]["result"][0]
            m = d["meta"]
            last, prev = m.get("regularMarketPrice"), m.get("chartPreviousClose") or m.get("previousClose")
            t = m.get("regularMarketTime")
            out["items"][name] = {"symbol": sym, "last": last, "prev_close": prev,
                                  "chg_pct": round((last / prev - 1) * 100, 2) if last and prev else None,
                                  "as_of": datetime.fromtimestamp(t, NY).isoformat(timespec="minutes") if t else None}
        except Exception as e:  # noqa: BLE001
            out["errors"][name] = str(e)[:120]
    for sid in ("DGS2", "DGS10"):
        try:
            rows = [r for r in csv.reader(io.StringIO(get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}")))
                    if r and r[-1] not in (".", "")]
            last, prev = rows[-1], rows[-2]
            out["items"][f"FRED_{sid}"] = {"date": last[0], "value": float(last[1]), "prev_date": prev[0],
                                          "prev": float(prev[1]), "chg_bp": round((float(last[1]) - float(prev[1])) * 100, 1)}
        except Exception as e:  # noqa: BLE001
            out["errors"][f"FRED_{sid}"] = str(e)[:120]
    return out


# ---------------------------------------------------------------- economic calendar
def economic(days: int = 6):
    now = datetime.now(timezone.utc)
    out = {"fetched": stamp(), "events": [], "errors": {}}
    try:
        q = urllib.parse.urlencode({"from": now.strftime("%Y-%m-%dT00:00:00.000Z"),
                                    "to": (now + timedelta(days=days)).strftime("%Y-%m-%dT23:59:59.000Z"),
                                    "countries": "US"})
        d = json.loads(get(f"https://economic-calendar.tradingview.com/events?{q}",
                           {"Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"}))
        for e in d.get("result", []):
            imp = {-1: "LOW", 0: "MEDIUM", 1: "HIGH"}.get(e.get("importance"), str(e.get("importance")))
            out["events"].append({"time_utc": e.get("date"), "time_ny": datetime.fromisoformat(
                e["date"].replace("Z", "+00:00")).astimezone(NY).strftime("%a %d %b %H:%M") if e.get("date") else None,
                "title": e.get("title"), "impact": imp, "forecast": e.get("forecast"), "previous": e.get("previous"),
                "actual": e.get("actual"), "unit": e.get("unit"), "period": e.get("period")})
        out["source"] = "TradingView economic calendar"
    except Exception as e:  # noqa: BLE001
        out["errors"]["tradingview"] = str(e)[:160]
    if not out["events"]:
        try:
            d = json.loads(get("https://nfs.faireconomy.media/ff_calendar_thisweek.json"))
            for e in d:
                if e.get("country") != "USD":
                    continue
                t = datetime.fromisoformat(e["date"])
                out["events"].append({"time_utc": t.astimezone(timezone.utc).isoformat(),
                                      "time_ny": t.astimezone(NY).strftime("%a %d %b %H:%M"), "title": e.get("title"),
                                      "impact": (e.get("impact") or "").upper(), "forecast": e.get("forecast"),
                                      "previous": e.get("previous"), "actual": None})
            out["source"] = "ForexFactory weekly calendar (faireconomy.media)"
        except Exception as e:  # noqa: BLE001
            out["errors"]["forexfactory"] = str(e)[:160]
    out["events"].sort(key=lambda x: x.get("time_utc") or "")
    return out


# ---------------------------------------------------------------- earnings calendar
def universe() -> set:
    p = ROOT / "trader_state" / "trader" / "largecaps.txt"
    try:
        import tv_scan
        rows = tv_scan.run_scan({"filter": tv_scan.BASE_FILTERS, "sort": {"sortBy": "market_cap_basic",
                                 "sortOrder": "desc"}, "limit": 2000}, None, ["name"])
        return {r["name"] for r in rows}
    except Exception:  # noqa: BLE001
        return set(p.read_text().split()) if p.exists() else set()


def earnings(days: int = 2):
    out = {"source": "Nasdaq earnings calendar API (api.nasdaq.com)", "fetched": stamp(), "days": {}, "errors": {}}
    uni = universe()
    out["universe_size"] = len(uni)
    d0 = datetime.now(NY).date()
    n, d = 0, d0
    while n < days:
        if d.weekday() < 5:
            try:
                j = json.loads(get(f"https://api.nasdaq.com/api/calendar/earnings?date={d.isoformat()}",
                                   {"Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}))
                rows = ((j.get("data") or {}).get("rows")) or []
                keep = [{"symbol": r.get("symbol"), "name": r.get("name"),
                         "time": {"time-pre-market": "before open", "time-after-hours": "after close"}.get(
                             r.get("time"), r.get("time")), "eps_forecast": r.get("epsForecast"),
                         "market_cap": r.get("marketCap"), "fiscal_quarter": r.get("fiscalQuarterEnding")}
                        for r in rows if not uni or r.get("symbol") in uni]
                out["days"][d.isoformat()] = {"all": len(rows), "in_universe": keep}
            except Exception as e:  # noqa: BLE001
                out["errors"][d.isoformat()] = str(e)[:160]
            n += 1
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------- news (Alpaca / Benzinga)
def news(tickers: list, hours: int = 18):
    key, sec = os.environ.get("ALPACA_PAPER_KEY_ID", ""), os.environ.get("ALPACA_PAPER_SECRET_KEY", "")
    out = {"source": "Alpaca news API (Benzinga)", "fetched": stamp(), "market": [], "tickers": {}, "errors": {}}
    if not (key and sec):
        out["errors"]["auth"] = "Alpaca keys missing"
        return out
    hdr = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}
    start = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def pull(symbols=None, limit=50):
        q = {"start": start, "limit": limit, "sort": "desc", "include_content": "false"}
        if symbols:
            q["symbols"] = ",".join(symbols)
        d = json.loads(get("https://data.alpaca.markets/v1beta1/news?" + urllib.parse.urlencode(q), hdr))
        return [{"time_ny": datetime.fromisoformat(n["created_at"].replace("Z", "+00:00")).astimezone(NY)
                 .strftime("%a %d %b %H:%M"), "headline": n.get("headline"), "summary": (n.get("summary") or "")[:280],
                 "source": n.get("source"), "author": n.get("author"), "symbols": n.get("symbols"), "url": n.get("url")}
                for n in d.get("news", [])]
    try:
        out["market"] = pull(None, 50)
    except Exception as e:  # noqa: BLE001
        out["errors"]["market"] = str(e)[:160]
    for i in range(0, len(tickers), 10):
        chunk = tickers[i:i + 10]
        try:
            items = pull(chunk, 50)
            for s in chunk:
                out["tickers"][s] = [n for n in items if s in (n["symbols"] or [])][:6]
        except Exception as e:  # noqa: BLE001
            out["errors"][",".join(chunk)] = str(e)[:160]
    return out


def main() -> int:
    tickers = [t.strip().upper() for t in os.environ.get("TICKERS", "").replace(" ", ",").split(",") if t.strip()]
    try:  # plus today's top pre-market gappers from the TradingView scan that just ran
        sc = json.loads((OUT / "latest.json").read_text())["scans"]
        tickers += [r["name"] for k in ("premarket_gap_up", "premarket_gap_down") for r in sc.get(k, [])[:12]]
    except Exception:  # noqa: BLE001
        pass
    tickers = list(dict.fromkeys(tickers))[:40]
    doc = {"generated_new_york": stamp(), "tickers": tickers}
    for name, fn, args in (("snapshot", snapshot, ()), ("economic", economic, ()), ("earnings", earnings, ()),
                           ("news", news, (tickers,))):
        try:
            doc[name] = fn(*args)
        except Exception as e:  # noqa: BLE001
            doc[name] = {"fatal": str(e)[:200]}
    OUT.mkdir(exist_ok=True)
    text = json.dumps(doc, indent=1, default=str)
    (OUT / "context_latest.json").write_text(text)
    print(json.dumps({k: (v.get("errors") if isinstance(v, dict) else v) for k, v in doc.items()
                      if k in ("snapshot", "economic", "earnings", "news")}, default=str))
    print("snapshot", len(doc["snapshot"].get("items", {})), "| economic", len(doc["economic"].get("events", [])),
          doc["economic"].get("source"), "| earnings", {k: len(v["in_universe"]) for k, v in
                                                     doc["earnings"].get("days", {}).items()},
          "| news market", len(doc["news"].get("market", [])), "tickers", len(doc["news"].get("tickers", {})))
    return 0


if __name__ == "__main__":
    sys.exit(main())
