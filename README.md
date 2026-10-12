# tv-scanner

Data feeds for the daily pre-market trade brief (Claude project "Professional. Trade Analyst").

| Workflow | Schedule (UTC, weekdays) | Output |
|---|---|---|
| `premarket-scan.yml` → `tv_scan.py` | 12:20 and 13:20 (≈08:20 ET in both EDT/EST) | `scans/latest.json` — TradingView pre-market gap-up, gap-down and momentum scans |
| `options-scan.yml` → `options_scan.py` | 20:45 and 21:45 (≈16:45 ET) | `scans/options_latest.json` — unusual options activity from Cboe delayed quotes |

Universe: US-listed common stock, market cap ≥ $10B, price ≥ $20 (no ADRs/DRs, preferreds, funds).

Run manually: Actions tab → pick the workflow → "Run workflow".

Note: the TradingView scanner endpoint is unofficial and may change without notice.
