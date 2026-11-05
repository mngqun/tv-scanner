# Intraday / earnings research results (protocol 2026-11-05)

## H1: current ORB arms (15-min range, OR-mid stop, 2R)

**Design 2017–2021 (all variants)**

| Variant | n | days | avg R | t (by day) | CI by day | win | longs avg R (n) | shorts avg R (n) | 2× cost avg R |
|---|---|---|---|---|---|---|---|---|---|
| a | 7931 | 1255 | -0.067 | -4.38 | [-0.0974, -0.0366] | 0.415 | -0.0682 (3909) | -0.0666 (4022) | -0.1598 |
| b | 20144 | 1259 | -0.145 | -13.18 | [-0.1663, -0.1238] | 0.367 | -0.1252 (10072) | -0.165 (10072) | -0.233 |
| c | 9088 | 1136 | -0.151 | -7.41 | [-0.1908, -0.111] | 0.366 | -0.1368 (6992) | -0.1996 (2096) | -0.2396 |
| d | 20144 | 1259 | -0.144 | -16.06 | [-0.1614, -0.1262] | 0.206 | -0.1266 (10072) | -0.161 (10072) | -0.2374 |

Best design variant: **a** (avg -0.067R, t -4.38).
**Verdict: FAIL (design: best variant avg R <= 0 or t < 2).** Validation not run.

Calibration (variant a, 2–4 Nov; compare with the paper ledger):

```
[
{
"date": "2026-11-02",
"sym": "MU",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "META",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-02",
"sym": "META",
"side": 1,
"filled": true,
"R": -1.0743554216867484,
"exit": "stop"
},
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
"date": "2026-11-04",
"sym": "TSLA",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-02",
"sym": "TSLA",
"side": -1,
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
"date": "2026-11-04",
"sym": "AMD",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
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
},
{
"date": "2026-11-02",
"sym": "AMD",
"side": -1,
"filled": true,
"R": 1.959419858156029,
"exit": "target"
},
{
"date": "2026-11-04",
"sym": "GOOGL",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "GOOGL",
"side": -1,
"filled": true,
"R": -1.0961517241379304,
"exit": "stop"
},
{
"date": "2026-11-03",
"sym": "INTC",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-04",
"sym": "AMZN",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "AMZN",
"side": -1,
"filled": true,
"R": -1.0821459833795024,
"exit": "stop"
},
{
"date": "2026-11-02",
"sym": "GOOGL",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-02",
"sym": "AMZN",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-02",
"sym": "AVGO",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-04",
"sym": "GOOG",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "GOOG",
"side": -1,
"filled": true,
"R": -1.1007338368580042,
"exit": "stop"
},
{
"date": "2026-11-02",
"sym": "GOOG",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-04",
"sym": "PLTR",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "ORCL",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-02",
"sym": "ORCL",
"side": -1,
"filled": true,
"R": 1.965167264748558,
"exit": "target"
},
{
"date": "2026-11-02",
"sym": "STX",
"side": -1,
"filled": true,
"R": 0.8218004574755658,
"exit": "flat"
},
{
"date": "2026-11-03",
"sym": "STX",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "BE",
"side": 1,
"filled": true,
"R": -1.0227071866783526,
"exit": "stop"
},
{
"date": "2026-11-02",
"sym": "WDC",
"side": -1,
"filled": true,
"R": 1.484122807017535,
"exit": "flat"
},
{
"date": "2026-11-02",
"sym": "BE",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-04",
"sym": "LLY",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "WDC",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-02",
"sym": "AMAT",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "NFLX",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "LRCX",
"side": -1,
"filled": true,
"R": -1.0588478991596642,
"exit": "stop"
},
{
"date": "2026-11-04",
"sym": "GEV",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-02",
"sym": "MDT",
"side": 1,
"filled": true,
"R": 1.8547125433526017,
"exit": "target"
},
{
"date": "2026-11-02",
"sym": "V",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "UNH",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-04",
"sym": "NOW",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "WMT",
"side": -1,
"filled": true,
"R": 0.9033112987013253,
"exit": "flat"
},
{
"date": "2026-11-04",
"sym": "CAT",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "JPM",
"side": -1,
"filled": true,
"R": 1.0612416107382485,
"exit": "flat"
},
{
"date": "2026-11-04",
"sym": "GS",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-04",
"sym": "JNJ",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-04",
"sym": "GE",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-04",
"sym": "TMO",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
}
]
```

## H2: Stocks-in-Play 5-min ORB (top 20 RVOL, k x ATR stop, exit 15:40)

**Design 2017–2021 (all variants)**

| Variant | n | days | avg R | t (by day) | CI by day | win | longs avg R (n) | shorts avg R (n) | 2× cost avg R |
|---|---|---|---|---|---|---|---|---|---|
| k0.10 | 20260 | 1259 | -0.704 | -37.69 | [-0.7397, -0.6673] | 0.08 | -0.6904 (10399) | -0.719 (9861) | -0.9698 |
| k0.25 | 20260 | 1259 | -0.201 | -13.31 | [-0.2305, -0.1715] | 0.265 | -0.21 (10399) | -0.1918 (9861) | -0.3033 |
| k0.50 | 20260 | 1259 | -0.058 | -5.73 | [-0.0773, -0.0382] | 0.413 | -0.063 (10399) | -0.052 (9861) | -0.1062 |

Best design variant: **k0.50** (avg -0.058R, t -5.73).
**Verdict: FAIL (design: best variant avg R <= 0 or t < 2).** Validation not run.

## Coverage

Symbols requested 726, without Alpaca data 1, stock-days 1602197, failed fetch chunks 0 (10 symbols x 1 year each), failed symbol-years 0.

## H3: post-earnings drift on the surprise (daily)
Data: {"tickers": 634, "failed": 10, "events": 52571, "events_in_range": 24267, "with_surprise": 24146, "share_with_surprise": 0.995, "trades_per_variant": {"N1": 6699, "N2": 6699, "N4": 6698}, "status": "run"}

**Design 2017–2021 (all variants)**

| Variant | n | days | avg R | t (by day) | CI by day | win | longs avg R (n) | shorts avg R (n) | 2× cost avg R |
|---|---|---|---|---|---|---|---|---|---|
| N1 | 3161 | 679 | -0.022 | -2.09 | [-0.0414, -0.0016] | 0.481 | -0.0192 (2506) | -0.0309 (655) | -0.0461 |
| N2 | 3161 | 679 | -0.001 | -0.04 | [-0.0281, 0.0263] | 0.504 | 0.0148 (2506) | -0.0593 (655) | -0.025 |
| N4 | 3161 | 679 | +0.010 | 0.49 | [-0.0288, 0.0489] | 0.502 | 0.0283 (2506) | -0.0615 (655) | -0.0147 |

Best design variant: **N4** (avg +0.010R, t 0.49).
**Verdict: FAIL (design: best variant avg R <= 0 or t < 2).** Validation not run.

## H4: mechanised TQQQ plan

**Design 2017–2021 (all variants)**

| Variant | n | days | avg R | t (by day) | CI by day | win | longs avg R (n) | shorts avg R (n) | 2× cost avg R |
|---|---|---|---|---|---|---|---|---|---|
| j0.00 | 1104 | 1104 | -0.016 | -0.86 | [-0.0537, 0.0204] | 0.524 | -0.0164 (1104) | None (0) | -0.0317 |
| j0.25 | 451 | 451 | -0.015 | -0.46 | [-0.0772, 0.0467] | 0.51 | -0.0145 (451) | None (0) | -0.0261 |
| j0.50 | 168 | 168 | -0.009 | -0.15 | [-0.1256, 0.1073] | 0.554 | -0.0088 (168) | None (0) | -0.0204 |

Best design variant: **j0.50** (avg -0.009R, t -0.15).
**Verdict: FAIL (design: best variant avg R <= 0 or t < 2).** Validation not run.

