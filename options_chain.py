"""Full options-chain snapshot for a few tickers (Cboe delayed quotes) for single-name trade plans.

Writes scans/chain_<TICKER>_latest.json (+ dated copy): per-expiry OI/volume totals, put/call walls,
max pain, ATM straddle (implied move), unusual contracts, and near-the-money quotes.
"""
import json, os, re, sys, urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{t}.json"
OCC = re.compile(r"^(?P<root>[A-Z.]+?)(?P<y>\d{2})(?P<m>\d{2})(?P<d>\d{2})(?P<cp>[CP])(?P<k>\d{8})$")
MAX_DTE = int(os.environ.get("MAX_DTE", "35"))


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def chain(t: str, today: date) -> dict:
    req = urllib.request.Request(CBOE_URL.format(t=t), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = json.load(r)
    data = raw.get("data", {})
    spot = f(data.get("current_price")) or f(data.get("close"))
    by_exp = defaultdict(list)
    for o in data.get("options", []):
        m = OCC.match(o.get("option", ""))
        if not m:
            continue
        exp = date(2000 + int(m["y"]), int(m["m"]), int(m["d"]))
        dte = (exp - today).days
        if dte < 0 or dte > MAX_DTE:
            continue
        by_exp[exp].append({
            "type": m["cp"], "strike": int(m["k"]) / 1000, "bid": f(o.get("bid")), "ask": f(o.get("ask")),
            "last": f(o.get("last_trade_price")), "volume": int(f(o.get("volume"))),
            "oi": int(f(o.get("open_interest"))), "iv": round(f(o.get("iv")), 4),
            "delta": round(f(o.get("delta")), 3), "gamma": round(f(o.get("gamma")), 4),
        })
    out = {"ticker": t, "cboe_timestamp": raw.get("timestamp"), "spot": spot,
           "underlying": {k: data.get(k) for k in ("current_price", "price_change", "open", "high", "low",
                                                    "close", "prev_day_close", "volume", "iv30", "iv30_change")},
           "expiries": []}
    for exp in sorted(by_exp):
        rows = by_exp[exp]
        calls = [r for r in rows if r["type"] == "C"]; puts = [r for r in rows if r["type"] == "P"]
        strikes = sorted({r["strike"] for r in rows})
        # max pain: strike minimising total intrinsic value paid to holders
        def pain(s):
            return sum(max(0, s - r["strike"]) * r["oi"] for r in calls) + sum(max(0, r["strike"] - s) * r["oi"] for r in puts)
        mp = min(strikes, key=pain) if strikes else None
        atm = min(strikes, key=lambda s: abs(s - spot)) if strikes else None
        def mid(r):
            return (r["bid"] + r["ask"]) / 2 if r["ask"] else r["last"]
        c_atm = next((r for r in calls if r["strike"] == atm), None); p_atm = next((r for r in puts if r["strike"] == atm), None)
        straddle = round(mid(c_atm) + mid(p_atm), 2) if c_atm and p_atm else None
        near = [r for r in rows if spot and abs(r["strike"] / spot - 1) <= 0.15]
        unusual = sorted([dict(r, vol_oi=round(r["volume"] / r["oi"], 1) if r["oi"] else None) for r in rows
                          if r["volume"] >= 1000 and (r["oi"] == 0 or r["volume"] >= 3 * r["oi"])],
                         key=lambda r: r["volume"] * mid(r), reverse=True)[:10]
        out["expiries"].append({
            "expiry": exp.isoformat(), "dte": (exp - today).days,
            "call_oi": sum(r["oi"] for r in calls), "put_oi": sum(r["oi"] for r in puts),
            "call_volume": sum(r["volume"] for r in calls), "put_volume": sum(r["volume"] for r in puts),
            "call_premium_usd": round(sum(r["volume"] * mid(r) * 100 for r in calls)),
            "put_premium_usd": round(sum(r["volume"] * mid(r) * 100 for r in puts)),
            "max_pain": mp, "atm_strike": atm, "atm_straddle": straddle,
            "top_call_oi": sorted(calls, key=lambda r: r["oi"], reverse=True)[:6],
            "top_put_oi": sorted(puts, key=lambda r: r["oi"], reverse=True)[:6],
            "unusual": unusual,
            "near_money": sorted(near, key=lambda r: (r["strike"], r["type"])),
        })
    return out


def main() -> int:
    ny = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
    tickers = [t.strip().upper() for t in os.environ.get("TICKERS", "TQQQ").split(",") if t.strip()]
    d = Path(__file__).parent / "scans"; d.mkdir(exist_ok=True)
    ok = 0
    for t in tickers:
        try:
            res = chain(t, ny.date())
        except Exception as e:
            res = {"ticker": t, "error": f"{type(e).__name__}: {e}"}
        else:
            ok += 1
        res["generated_new_york"] = ny.isoformat(timespec="seconds")
        res["source"] = "Cboe delayed quotes (cdn.cboe.com), ~15 min delayed"
        text = json.dumps(res, indent=1, default=str)
        (d / f"chain_{t}_latest.json").write_text(text)
        (d / f"chain_{t}_{ny.strftime('%Y-%m-%d_%H%M')}.json").write_text(text)
        print(t, "ok" if "error" not in res else res["error"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
