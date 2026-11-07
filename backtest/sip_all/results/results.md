## H5: Stocks-in-Play 5-min ORB, all US stocks >= $5 (top 20 RVOL)

**Design 2017–2021 (all variants)**

| Variant | n | days | avg R | t (by day) | CI by day | win | longs avg R (n) | shorts avg R (n) | 2× cost avg R |
|---|---|---|---|---|---|---|---|---|---|
| k0.10 | 20197 | 1259 | -0.722 | -38.33 | [-0.7583, -0.6848] | 0.073 | -0.7112 (10223) | -0.7329 (9974) | -0.9773 |
| k0.25 | 20197 | 1259 | -0.232 | -16.05 | [-0.26, -0.203] | 0.241 | -0.23 (10223) | -0.2334 (9974) | -0.3299 |
| k0.50 | 20197 | 1259 | -0.055 | -5.35 | [-0.0755, -0.0348] | 0.394 | -0.0429 (10223) | -0.0678 (9974) | -0.1016 |

Best design variant: **k0.50** (avg -0.055R, t -5.35).
**Verdict: FAIL (design: best variant avg R <= 0 or t < 3).** Validation not run.


Diagnostic only (design window, $25k notional cap):
```
{
 "k0.10": {
  "trades": 2514,
  "total_usd": -140965.0,
  "worst_day": -690.0,
  "max_dd_usd": 140965.0
 },
 "k0.25": {
  "trades": 2518,
  "total_usd": -79331.0,
  "worst_day": -335.0,
  "max_dd_usd": 79373.0
 },
 "k0.50": {
  "trades": 2518,
  "total_usd": -19868.0,
  "worst_day": -239.0,
  "max_dd_usd": 20548.0
 }
}
```

Coverage:
```
{
 "universe": 5441,
 "with_preeligible_days": 4033,
 "rows": 3720635,
 "session_fetch_failures": 0,
 "parts": [
  {
   "dates": 623,
   "first_bar_days": 623,
   "selected": 12260,
   "trades": 36210
  },
  {
   "dates": 624,
   "first_bar_days": 644,
   "selected": 12480,
   "trades": 36999
  },
  {
   "dates": 624,
   "first_bar_days": 644,
   "selected": 12480,
   "trades": 37062
  },
  {
   "dates": 624,
   "first_bar_days": 644,
   "selected": 12480,
   "trades": 37224
  }
 ]
}
```
