# Intraday / earnings research results (protocol 2026-11-05)

## H1: current ORB arms (15-min range, OR-mid stop, 2R)

**Design 2017–2021 (all variants)**

| Variant | n | days | avg R | t (by day) | CI by day | win | longs avg R (n) | shorts avg R (n) | 2× cost avg R |
|---|---|---|---|---|---|---|---|---|---|
| a | 0 | | | | | | | | |
| b | 0 | | | | | | | | |
| c | 0 | | | | | | | | |
| d | 0 | | | | | | | | |

No trades.

Calibration (variant a, 2–4 Nov; compare with the paper ledger):

```
[
{
"date": "2026-11-04",
"sym": "AAPL",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "AAPL",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-02",
"sym": "AAPL",
"side": -1,
"filled": true,
"R": -1.1126316606140627,
"exit": "stop"
},
{
"date": "2026-11-03",
"sym": "MSFT",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-04",
"sym": "MSFT",
"side": 1,
"filled": true,
"R": 0.6609196811120304,
"exit": "flat"
}
]
```

## H2: Stocks-in-Play 5-min ORB (top 20 RVOL, k x ATR stop, exit 15:40)

**Design 2017–2021 (all variants)**

| Variant | n | days | avg R | t (by day) | CI by day | win | longs avg R (n) | shorts avg R (n) | 2× cost avg R |
|---|---|---|---|---|---|---|---|---|---|
| k0.10 | 0 | | | | | | | | |
| k0.25 | 0 | | | | | | | | |
| k0.50 | 0 | | | | | | | | |

No trades.

## Coverage

Symbols requested 3, without Alpaca data 0, stock-days 636.

## H4: mechanised TQQQ plan

**Design 2017–2021 (all variants)**

| Variant | n | days | avg R | t (by day) | CI by day | win | longs avg R (n) | shorts avg R (n) | 2× cost avg R |
|---|---|---|---|---|---|---|---|---|---|
| j0.00 | 1104 | 1104 | -0.016 | -0.86 | [-0.0537, 0.0204] | 0.524 | -0.0164 (1104) | None (0) | -0.0317 |
| j0.25 | 451 | 451 | -0.015 | -0.46 | [-0.0772, 0.0467] | 0.51 | -0.0145 (451) | None (0) | -0.0261 |
| j0.50 | 168 | 168 | -0.009 | -0.15 | [-0.1256, 0.1073] | 0.554 | -0.0088 (168) | None (0) | -0.0204 |

Best design variant: **j0.50** (avg -0.009R, t -0.15).
**Verdict: FAIL (design: best variant avg R <= 0 or t < 2).** Validation not run.

