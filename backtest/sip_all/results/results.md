## H5: Stocks-in-Play 5-min ORB, all US stocks >= $5 (top 20 RVOL)

**Design 2017–2021 (all variants)**

| Variant | n | days | avg R | t (by day) | CI by day | win | longs avg R (n) | shorts avg R (n) | 2× cost avg R |
|---|---|---|---|---|---|---|---|---|---|
| k0.10 | 20243 | 1259 | -0.643 | -33.72 | [-0.6807, -0.6051] | 0.094 | -0.6376 (10292) | -0.6486 (9951) | -0.8966 |
| k0.25 | 20243 | 1259 | -0.168 | -12.1 | [-0.1947, -0.1404] | 0.289 | -0.1723 (10292) | -0.1625 (9951) | -0.2642 |
| k0.50 | 20243 | 1259 | -0.056 | -6.28 | [-0.073, -0.0386] | 0.424 | -0.0427 (10292) | -0.0694 (9951) | -0.1015 |

Best design variant: **k0.50** (avg -0.056R, t -6.28).
**Verdict: FAIL (design: best variant avg R <= 0 or t < 3).** Validation not run.


Diagnostic only (design window, $25k notional cap):
```
{
 "k0.10": {
  "trades": 2513,
  "total_usd": -120624.0,
  "worst_day": -367.0,
  "max_dd_usd": 121814.0
 },
 "k0.25": {
  "trades": 2518,
  "total_usd": -58760.0,
  "worst_day": -292.0,
  "max_dd_usd": 60433.0
 },
 "k0.50": {
  "trades": 2518,
  "total_usd": -21443.0,
  "worst_day": -245.0,
  "max_dd_usd": 22598.0
 }
}
```

Coverage:
```
{
 "universe": 5750,
 "with_preeligible_days": 920,
 "rows": 839118,
 "session_fetch_failures": 0,
 "parts": [
  {
   "dates": 623,
   "first_bar_days": 623,
   "selected": 11961,
   "trades": 35370
  },
  {
   "dates": 624,
   "first_bar_days": 644,
   "selected": 12369,
   "trades": 36648
  },
  {
   "dates": 624,
   "first_bar_days": 644,
   "selected": 12318,
   "trades": 36597
  },
  {
   "dates": 624,
   "first_bar_days": 644,
   "selected": 12427,
   "trades": 37017
  }
 ]
}
```
