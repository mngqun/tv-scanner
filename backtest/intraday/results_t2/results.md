# T2 results (protocol 2026-11-06, frozen)

## T2a: VWAP trend continuation (high-vol top 50, RVOL15 >= 1.5)

**Design 2017–2021 (all variants)**

| Variant | n | days | avg R | t (by day) | CI by day | win | longs avg R (n) | shorts avg R (n) | 2× cost avg R |
|---|---|---|---|---|---|---|---|---|---|
| a1 | 4606 | 1196 | -0.034 | -2.0 | [-0.0671, -0.0002] | 0.327 | -0.0453 (2343) | -0.0217 (2263) | -0.0643 |
| a2 | 4606 | 1196 | -0.039 | -1.98 | [-0.0778, -0.0009] | 0.42 | -0.0435 (2343) | -0.0349 (2263) | -0.0728 |
| a3 | 4606 | 1196 | -0.040 | -2.71 | [-0.0687, -0.0106] | 0.366 | -0.0518 (2343) | -0.0279 (2263) | -0.0709 |

Best design variant: **a1** (avg -0.034R, t -2.0).
**Verdict: FAIL (design: best variant avg R <= 0 or t < 3).** Validation not run.

## T2b: opening drive, first pull-back to VWAP (gap >= 1 ATR)

**Design 2017–2021 (all variants)**

| Variant | n | days | avg R | t (by day) | CI by day | win | longs avg R (n) | shorts avg R (n) | 2× cost avg R |
|---|---|---|---|---|---|---|---|---|---|
| b1 | 317 | 230 | +0.027 | 0.34 | [-0.1229, 0.1802] | 0.404 | -0.0155 (172) | 0.077 (145) | -0.0117 |
| b2 | 317 | 230 | -0.015 | -0.25 | [-0.1369, 0.1101] | 0.438 | -0.0756 (172) | 0.0558 (145) | -0.0342 |
| b3 | 317 | 230 | -0.049 | -0.79 | [-0.1702, 0.0771] | 0.423 | -0.1604 (172) | 0.0822 (145) | -0.0687 |

Best design variant: **b1** (avg +0.027R, t 0.34).
**Verdict: FAIL (design: best variant avg R <= 0 or t < 3).** Validation not run.

## T2c: VWAP over-extension fade (X x ATR, RSI14 5-min)

**Design 2017–2021 (all variants)**

| Variant | n | days | avg R | t (by day) | CI by day | win | longs avg R (n) | shorts avg R (n) | 2× cost avg R |
|---|---|---|---|---|---|---|---|---|---|
| c1 | 435 | 293 | -0.007 | -0.06 | [-0.2299, 0.2326] | 0.221 | -0.0054 (224) | -0.0089 (211) | -0.1046 |
| c2 | 160 | 122 | -0.098 | -0.44 | [-0.5047, 0.3621] | 0.181 | -0.3338 (89) | 0.1969 (71) | -0.1915 |
| c3 | 69 | 56 | +0.929 | 2.11 | [0.1146, 1.8464] | 0.304 | 1.1698 (42) | 0.554 (27) | 0.8375 |

Best design variant: **c3** (avg +0.929R, t 2.11).
**Verdict: FAIL (design: best variant avg R <= 0 or t < 3).** Validation not run.

## Coverage

```
{
 "requested": 726,
 "no_data": 1,
 "stock_days": 1602197,
 "failed_chunks": 0,
 "top50_rows_below_prefilter": 0,
 "top50_rows": 125800,
 "top50_with_rvol": 17685
}
```
