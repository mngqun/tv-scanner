# Core bucket results (protocol 2026-11-07, Part B)

Synthetic vs real TQQQ since 2010-02-12: correlation 0.999, real minus synthetic -1.02 %/yr.

## design 1999-03-10 → 2012-12-31

| Strategy | CAGR % | Vol % | Sharpe | Max DD % | Worst year % | $10k → | Switches/yr | In market % |
|---|---|---|---|---|---|---|---|---|
| B0 SPY buy&hold | 2.6 | 21.35 | 0.12 | -55.2 | -36.8 | 14245.0 |  |  |
| B0 QQQ buy&hold | 2.22 | 31.79 | 0.16 | -83.0 | -41.7 | 13537.0 |  |  |
| B0 TQQQ(syn) buy&hold | -25.27 | 95.36 | 0.15 | -100.0 | -91.6 | 180.0 |  |  |
| B1 QQQ / SMA200 | 0.42 | 18.09 | -0.0 | -55.8 | -19.6 | 10562.0 | 9.8 | 62.0 |
| B2 TQQQ(syn) / QQQ SMA200 | -11.69 | 54.28 | 0.01 | -95.1 | -73.1 | 1987.0 | 9.8 | 62.0 |
| B3 TQQQ(syn) / QQQ SMA200 2% band | -6.92 | 54.94 | 0.11 | -91.7 | -72.7 | 3937.0 | 2.8 | 62.1 |
| B4 SPY / SMA200 | 2.15 | 11.68 | 0.05 | -24.1 | -15.5 | 13413.0 | 9.0 | 64.4 |

## validation 2013-01-01 → 2026-10-30

| Strategy | CAGR % | Vol % | Sharpe | Max DD % | Worst year % | $10k → | Switches/yr | In market % |
|---|---|---|---|---|---|---|---|---|
| B0 SPY buy&hold | 14.77 | 16.72 | 0.8 | -33.7 | -18.2 | 66988.0 |  |  |
| B0 QQQ buy&hold | 19.62 | 20.85 | 0.88 | -35.1 | -32.6 | 118675.0 |  |  |
| B0 TQQQ(syn) buy&hold | 43.3 | 62.55 | 0.86 | -81.3 | -78.7 | 1435164.0 |  |  |
| B1 QQQ / SMA200 | 16.02 | 15.93 | 0.9 | -22.3 | -15.6 | 77782.0 | 4.2 | 85.3 |
| B2 TQQQ(syn) / QQQ SMA200 | 38.91 | 47.78 | 0.89 | -56.5 | -42.9 | 933940.0 | 4.2 | 85.3 |
| B3 TQQQ(syn) / QQQ SMA200 2% band | 33.58 | 48.08 | 0.81 | -70.9 | -30.5 | 544366.0 | 1.8 | 84.2 |
| B4 SPY / SMA200 | 11.7 | 11.67 | 0.85 | -19.0 | -13.9 | 46073.0 | 4.6 | 85.4 |

## Pass bar (both windows)

- **B1 QQQ / SMA200: FAIL** (design: DD ok, Sharpe fail, CAGR ok; validation: DD ok, Sharpe ok, CAGR fail)
- **B2 TQQQ(syn) / QQQ SMA200: FAIL** (design: DD fail, Sharpe fail, CAGR fail; validation: DD ok, Sharpe ok, CAGR ok)
- **B3 TQQQ(syn) / QQQ SMA200 2% band: FAIL** (design: DD fail, Sharpe fail, CAGR fail; validation: DD fail, Sharpe fail, CAGR ok)
- **B4 SPY / SMA200: FAIL** (design: DD ok, Sharpe fail, CAGR ok; validation: DD ok, Sharpe ok, CAGR fail)
