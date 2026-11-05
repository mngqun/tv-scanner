# T1 results (protocol 2026-11-06, frozen)

## T1a: pull-back inside a trend (RSI(2), exit above SMA5, max 4 sessions)

**Design 2017–2021 (all variants)**

| Variant | n | days | avg R | t (by day) | CI by day | win | longs avg R (n) | shorts avg R (n) | 2× cost avg R |
|---|---|---|---|---|---|---|---|---|---|
| a1 | 20729 | 1234 | -0.005 | -0.35 | [-0.0328, 0.0215] | 0.594 | 0.0093 (13693) | -0.0325 (7036) | -0.0238 |
| a2 | 10590 | 1171 | -0.006 | -0.33 | [-0.0438, 0.0296] | 0.593 | 0.0014 (6928) | -0.0206 (3662) | -0.0249 |
| a3 | 7472 | 1125 | -0.017 | -1.27 | [-0.0431, 0.0088] | 0.571 | -0.0095 (4088) | -0.0257 (3384) | -0.0305 |

Best design variant: **a1** (avg -0.005R, t -0.35).
**Verdict: FAIL (design: best variant avg R <= 0 or t < 3).** Validation not run.

## T1b: breakout continuation (20-day closing high, ADX>25, volume 1.5x)

**Design 2017–2021 (all variants)**

| Variant | n | days | avg R | t (by day) | CI by day | win | longs avg R (n) | shorts avg R (n) | 2× cost avg R |
|---|---|---|---|---|---|---|---|---|---|
| b1 | 6750 | 1080 | -0.024 | -0.71 | [-0.0873, 0.0472] | 0.452 | -0.039 (3447) | -0.0089 (3303) | -0.0473 |
| b2 | 6744 | 1078 | -0.024 | -0.75 | [-0.083, 0.0436] | 0.454 | -0.0404 (3443) | -0.0072 (3301) | -0.0472 |
| b3 | 6736 | 1077 | -0.026 | -0.74 | [-0.0876, 0.0483] | 0.458 | -0.0394 (3441) | -0.0115 (3295) | -0.0488 |

Best design variant: **b2** (avg -0.024R, t -0.75).
**Verdict: FAIL (design: best variant avg R <= 0 or t < 3).** Validation not run.

