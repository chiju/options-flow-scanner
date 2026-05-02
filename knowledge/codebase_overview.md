# Codebase Overview — Every File Explained

## Core Scanner

### `options_flow_scanner.py` (1093 lines)
**Main scanner** — the heart of the system. Runs every 15 minutes.
- Scans 54 symbols × 720 contracts = ~38,880 contracts per run
- Scores each contract (0-10) using 10+ factors
- Score ≥7 → logged to UNUSUAL_ALERTS sheet
- Score ≥9 → Telegram alert (Golden Flow / High Conviction)
- Handles divergence warnings, GEX, earnings calendar, price changes
- Contains: `score_alert()`, `run_scan()`, `golden_flow()`, `filter_new_golden_flow()`

### `schwab_scanner.py` (146 lines)
**Schwab data fetcher** — replaces Alpaca for options data.
- Fetches real Greeks (delta, gamma, theta, vega) from Schwab API
- Real open interest per strike (not delayed)
- Calculates sweep detection, IV spike, buy/sell direction
- Called by `options_flow_scanner.py` for each symbol

### `flow_trader.py` (671 lines)
**Trade executor** — reads signals and executes spreads.
- Reads SIGNAL_HISTORY sheet for confirmed signals (3+ sweeps, score ≥9)
- Applies: market clock check, earnings filter, 30% capital limit, dedup
- Executes bull put spreads on Alpaca paper account
- Manages exits: 50% profit, 2x stop loss, 7 DTE close
- Logs to FLOW_TRADE_LOG_15K and TRADE_RESULTS sheets
- Sends Telegram on trade executed / signal blocked / no strike found

---

## Data & Storage

### `sheets.py` (575 lines)
**Google Sheets filesystem** — all read/write to Google Sheets.
- `_service()` — authenticated Google Sheets client
- `_append()` — append rows to any tab
- `_ensure_tabs()` — create tabs if they don't exist
- Used by every other module as the persistence layer

### `schwab_token_store.py` (70 lines)
**Schwab OAuth token persistence.**
- Saves token to Google Sheets `SCHWAB_TOKEN` tab (base64 encoded)
- Loads token from sheets so GitHub Actions can authenticate
- Re-auth command: `python schwab_cli.py auth && python schwab_token_store.py save`

### `schwab_cli.py` (135 lines)
**Schwab terminal tool** — manual inspection from command line.
- `python schwab_cli.py account` — show account + positions
- `python schwab_cli.py quote AAPL` — get live quote
- `python schwab_cli.py options TSLA` — show options chain
- `python schwab_cli.py auth` — re-authenticate OAuth

---

## Reports & Alerts

### `daily_brief.py` (796 lines)
**Morning/evening report** — 3-AI pipeline.
- Fetches news from Alpaca News API (50+ symbols)
- Fetches macro news (Fed, inflation, geopolitics)
- Generates portfolio summary with P&L
- Sends formatted Telegram digest
- Runs: morning brief (pre-market) + EOD report (after close)

### `weekly_summary.py` (201 lines)
**Saturday summary** — weekly digest.
- Reads UNUSUAL_ALERTS for the week
- Shows: total alerts, call/put split, top symbols with bias, top flows
- Shows: sweeps breakdown per symbol (bullish/bearish)
- Shows: earnings this week with price + options flow bias + portfolio indicator
- Runs every Saturday at 10:00 UTC via cron job 7502338

### `notifier.py` (30 lines)
**Telegram sender** — multi-recipient.
- Sends to primary (326834093) + secondary (8774206796)
- Used by all modules for alerts

### `telegram_trigger.py` (154 lines)
**Telegram bot** — on-demand commands.
- Handles commands sent to the bot from Telegram chat
- Triggers scans, reports, account checks on demand

---

## Analytics

### `gamma_levels.py` (299 lines)
**GEX calculator** — options market structure.
- Calculates Max Pain (strike where most options expire worthless)
- Call Wall (strike with highest call OI = resistance)
- Put Wall (strike with highest put OI = support)
- Uses real Schwab gamma × OI (not estimated)
- Logs to GAMMA_LEVELS sheet

### `oi_tracker.py` (284 lines)
**Open Interest day-over-day tracker.**
- Snapshots OI per strike daily
- Detects OI increases (new positions) vs decreases (closing)
- Uses Schwab real OI (primary) → yfinance fallback
- Logs to OI_SNAPSHOT sheet

### `signal_outcomes.py` (168 lines)
**Signal accuracy tracker** — proves edge.
- After each alert, checks price 1 day and 3 days later
- Records: was the signal correct? by how much?
- Logs to SIGNAL_OUTCOMES sheet
- Used to calculate actual win rate over time

### `earnings_tracker.py` (135 lines)
**Earnings flow snapshot.**
- Captures options positioning (IV, OI, call/put ratio) before earnings
- Compares to post-earnings to measure IV crush and direction accuracy
- Logs to EARNINGS_TRACKER sheet

### `earnings.py` (31 lines)
**Earnings calendar fetcher.**
- Gets upcoming earnings dates for all watchlist symbols via yfinance
- Used by flow_trader for earnings filter
- Used by weekly_summary for earnings section

---

## Tests

### `tests/test_critical_logic.py` (220 lines)
**15 critical tests** — all passing.
- Scoring logic (deep ITM cap, ATM bonus, IV rank, ascending vol)
- Profit/loss rules (50% profit trigger, 2x stop loss)
- Spread math (width, max loss calculation)
- Dedup logic (blocks existing symbols)
- New rules: earnings filter, 30% capital limit, dynamic risk scaling

---

## Google Sheets Tabs

| Tab | Purpose |
|-----|---------|
| `UNUSUAL_ALERTS` | Every contract score ≥7, every scan (~8,600+ rows) |
| `SIGNAL_HISTORY` | Score ≥9 alerts, deduped (4h window) |
| `FLOW_TRADE_LOG` | CSP/FlowTrader executed trades |
| `FLOW_TRADE_LOG_15K` | Flow-15K executed + skipped trades |
| `TRADE_RESULTS` | Closed trade P&L (both legs) |
| `GAMMA_LEVELS` | Max Pain, Call/Put walls per symbol |
| `OI_SNAPSHOT` | OI day-over-day per strike |
| `SIGNAL_OUTCOMES` | Signal accuracy (price 1d/3d after alert) |
| `EARNINGS_TRACKER` | Pre/post earnings options snapshot |
| `EARNINGS_CALENDAR` | Upcoming earnings dates |
| `SCHWAB_TOKEN` | OAuth token (base64, persists across CI runs) |
| `NEWS_SEEN` | News dedup (prevents re-alerting same article) |
| `BRIEF_LOG` | Daily brief history |
| `MY_HOLDINGS` | Portfolio holdings for "YOU OWN THIS" alerts |
