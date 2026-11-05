# Intraday / earnings research results (protocol 2026-11-05)

## H1: current ORB arms (15-min range, OR-mid stop, 2R)

**Design 2017–2021 (all variants)**

| Variant | n | days | avg R | t (by day) | CI by day | win | longs avg R (n) | shorts avg R (n) | 2× cost avg R |
|---|---|---|---|---|---|---|---|---|---|
| a | 7784 | 1254 | -0.071 | -4.91 | [-0.099, -0.0426] | 0.417 | -0.0559 (3922) | -0.0868 (3862) | -0.17 |
| b | 20144 | 1259 | -0.148 | -14.8 | [-0.167, -0.1285] | 0.37 | -0.1289 (10072) | -0.1668 (10072) | -0.2435 |
| c | 9088 | 1136 | -0.145 | -7.9 | [-0.1798, -0.108] | 0.371 | -0.1253 (6992) | -0.2096 (2096) | -0.2398 |
| d | 20144 | 1259 | -0.151 | -18.39 | [-0.167, -0.1351] | 0.205 | -0.1329 (10072) | -0.1688 (10072) | -0.2529 |

Best design variant: **a** (avg -0.071R, t -4.91).
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
"sym": "AMZN",
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
"date": "2026-11-03",
"sym": "LRCX",
"side": -1,
"filled": true,
"R": -1.0588478991596642,
"exit": "stop"
},
{
"date": "2026-11-04",
"sym": "LRCX",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-02",
"sym": "LRCX",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
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
"date": "2026-11-04",
"sym": "NOW",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "NOW",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-02",
"sym": "NOW",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-04",
"sym": "COST",
"side": 1,
"filled": true,
"R": 1.9322135152696551,
"exit": "target"
},
{
"date": "2026-11-03",
"sym": "COST",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "XOM",
"side": -1,
"filled": true,
"R": 0.16485081967212095,
"exit": "flat"
},
{
"date": "2026-11-02",
"sym": "COST",
"side": 1,
"filled": true,
"R": 1.459772465060968,
"exit": "flat"
},
{
"date": "2026-11-04",
"sym": "PANW",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-02",
"sym": "PANW",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-02",
"sym": "JNJ",
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
"date": "2026-11-03",
"sym": "BA",
"side": 1,
"filled": true,
"R": -1.0782616005354935,
"exit": "stop"
},
{
"date": "2026-11-02",
"sym": "BA",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-02",
"sym": "TMO",
"side": -1,
"filled": true,
"R": -0.07597087912088142,
"exit": "flat"
},
{
"date": "2026-11-03",
"sym": "VRT",
"side": 1,
"filled": true,
"R": -1.0258892040256176,
"exit": "stop"
},
{
"date": "2026-11-03",
"sym": "TMO",
"side": 1,
"filled": true,
"R": 0.08630800619998502,
"exit": "flat"
},
{
"date": "2026-11-02",
"sym": "VRT",
"side": -1,
"filled": true,
"R": 1.9748858695652172,
"exit": "target"
},
{
"date": "2026-11-04",
"sym": "TMO",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "MRK",
"side": -1,
"filled": true,
"R": 1.9441730769230783,
"exit": "target"
},
{
"date": "2026-11-02",
"sym": "MRK",
"side": -1,
"filled": true,
"R": -1.0587885950413227,
"exit": "stop"
},
{
"date": "2026-11-04",
"sym": "HD",
"side": -1,
"filled": true,
"R": -1.0561218623481783,
"exit": "stop"
},
{
"date": "2026-11-02",
"sym": "HD",
"side": 1,
"filled": true,
"R": 0.7761136738055875,
"exit": "flat"
},
{
"date": "2026-11-03",
"sym": "PEP",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-04",
"sym": "PEP",
"side": 1,
"filled": true,
"R": 0.9592055310348984,
"exit": "flat"
},
{
"date": "2026-11-02",
"sym": "PEP",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-04",
"sym": "MCD",
"side": 1,
"filled": true,
"R": 0.7123776444929213,
"exit": "flat"
},
{
"date": "2026-11-04",
"sym": "WFC",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-02",
"sym": "IBM",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "T",
"side": -1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-04",
"sym": "AMGN",
"side": -1,
"filled": true,
"R": -1.0378270903010032,
"exit": "stop"
},
{
"date": "2026-11-03",
"sym": "HPE",
"side": 1,
"filled": false,
"R": NaN,
"exit": NaN
},
{
"date": "2026-11-03",
"sym": "DHR",
"side": 1,
"filled": true,
"R": -0.892296466093598,
"exit": "flat"
},
{
"date": "2026-11-04",
"sym": "DHR",
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
| k0.10 | 18178 | 1251 | -0.627 | -29.99 | [-0.668, -0.5856] | 0.099 | -0.6168 (9135) | -0.6378 (9043) | -0.9054 |
| k0.25 | 18178 | 1251 | -0.120 | -7.23 | [-0.151, -0.0863] | 0.306 | -0.1172 (9135) | -0.122 (9043) | -0.2259 |
| k0.50 | 18178 | 1251 | -0.040 | -3.82 | [-0.0603, -0.0192] | 0.431 | -0.0302 (9135) | -0.0497 (9043) | -0.0904 |

Best design variant: **k0.50** (avg -0.040R, t -3.82).
**Verdict: FAIL (design: best variant avg R <= 0 or t < 2).** Validation not run.

## Coverage

Symbols requested 182, without Alpaca data 0, stock-days 401982.

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

