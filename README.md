# Options Flow Scanner 📊

> Track institutional options activity ("smart money") across 35+ symbols every 5 minutes.
> Sends scored Telegram alerts and stores time-series data in Google Sheets for pattern analysis.

**Goal:** Options flow + news context → calculated probability → simple swing trade → managed risk.

---

## How It Works — Visual Flow

```
Every 5 minutes (GitHub Actions, public repo, free unlimited)
│
├── 📡 Alpaca Screener API
│   └── Fetch today's most active stocks → add to watchlist dynamically
│
├── 📡 Fetch options chain (Alpaca API)
│   └── 35 fixed symbols + dynamic most-active stocks
│
├── 🔍 Score each contract (1–10)
│   ├── +5  premium ≥ $20M  (massive institutional)
│   ├── +4  premium ≥ $10M
│   ├── +3  premium ≥ $5M
│   ├── +2  sweep (large block, bought urgently across exchanges)
│   ├── +2  IV spike > 80% (buying urgency)
│   ├── +2  0–7 DTE (expires this week = highest conviction)
│   ├── +1  8–30 DTE
│   └── +1  OTM delta (directional bet, not hedge)
│
├── 🧠 Compare to previous scan (momentum signals)
│   ├── Same $5M+ contract seen again → "position being built"
│   └── P/C ratio jumped 30%+ → "hedging accelerating"
│
├── 📊 OI change tracking
│   └── Compare today's open interest vs yesterday → detect new positioning
│
├── 📅 Earnings tracking
│   ├── Before earnings: snapshot P/C ratio + top flows
│   └── After earnings: fetch EPS result + price change → mark ✅/❌
│
├── 🌍 VIX + Sector rotation
│   ├── VIX from Yahoo Finance (fear gauge)
│   └── Detect money rotating between sectors (Tech/Finance/Energy/Gold/Bonds)
│
├── 📱 Send Telegram alert (only if score ≥ 7 OR new signals detected)
│   ├── Market mood + VIX
│   ├── Sector ETF P/C snapshot
│   ├── Sector rotation signal
│   ├── Top flows sorted by $ size with scores
│   ├── Portfolio P/C ratios (bullish/neutral/bearish)
│   ├── Momentum signals (repeated positions, P/C changes)
│   └── Earnings this week warning
│
└── 📊 Store to Google Sheets (Options Flow Tracker)
    ├── UNUSUAL_ALERTS  → append all $5M+ signals with price at alert time
    ├── SYMBOL_TRACKER  → upsert current state per symbol (22 rows, always current)
    ├── OI_SNAPSHOT     → daily open interest per symbol
    ├── EARNINGS_TRACKER → pre-earnings flow + post-earnings result + accuracy
    └── SPY / QQQ / MSFT / NVDA / ... → one tab per symbol, full history
```

---

## What Each File Does

### `options_flow_scanner.py` — Main Entry Point
Orchestrates the full pipeline on every execution.

```
Key sections:
  ALL_SYMBOLS          — 35 fixed symbols (indexes + sectors + mega caps + portfolio)
  get_dynamic_symbols()— Alpaca Screener API: adds today's most active stocks
  score_alert()        — scores each contract 1–10 based on 7 signal factors
  scan_symbol()        — fetches one symbol's options chain, tags sweep/IV spike
  interpret_signal()   — P/C ratio → 🔥🟢🟡🟠🔴 signal
  sector_rotation_signal() — detects money moving between sectors
  get_vix()            — fetches VIX from Yahoo Finance
  get_current_prices() — fetches stock prices via Alpaca for tracking
  has_new_signals()    — dedup suppression: skip Telegram if nothing changed
  format_report()      — builds the Telegram message
  run_scan()           — main: scan → score → compare → format → send → store
```

### `sheets.py` — Google Sheets Storage
All read/write to the Options Flow Tracker spreadsheet.

```
Key functions:
  store_results()         — writes all sheets, returns momentum alerts
  get_last_scan()         — reads previous scan for momentum comparison
  compare_scans()         — detects repeated positions + P/C changes
  store_oi_snapshot()     — daily OI per symbol (upsert by date+symbol)
  get_oi_changes()        — compares today vs yesterday OI P/C ratio
  _ensure_tabs()          — auto-creates missing tabs with headers
  _upsert_tracker()       — updates existing symbol row or appends
  dte_bucket()            — 0-7d🔥 / 8-30d🟢 / 31-90d🟡 / 90d+🟠
```

### `earnings.py` — Earnings Calendar
Fetches upcoming earnings dates from Yahoo Finance (free, no API key).

```
get_earnings_this_week(symbols) → {symbol: "Apr 18"} for symbols reporting in 7 days
```

### `earnings_tracker.py` — Earnings Accuracy Tracking
Measures whether options flow predicted earnings outcomes.

```
snapshot_pre_earnings()  — stores P/C ratio + signal + top flow before earnings
update_post_earnings()   — fetches EPS surprise + price change after earnings
                           marks ✅ Correct / ❌ Wrong / ⚪ Neutral
```

### `weekly_summary.py` — Friday Report
Runs every Friday at 4pm ET. Reads the week's UNUSUAL_ALERTS and sends a digest.

```
Shows: total alerts, call vs put split, overall bias, top 5 unique flows,
       most active symbols, sweep count by symbol
```

### `notifier.py` — Telegram Sender
Sends messages to Telegram, splitting long messages into 4000-char chunks.

### `.github/workflows/scanner.yml` — Automation
```
*/5 14-20 UTC Mon-Fri  → Main scan every 5 min (10am–4pm ET)
0 13 UTC Mon-Fri       → Pre-market scan 9am ET (forced send)
30 20 UTC Mon-Fri      → After-hours scan 4:30pm ET (forced send)
0 20 UTC Fridays       → Weekly summary report
```

---

## Signal Logic

### Alert Score (1–10)
Only alerts scoring ≥ 7 appear in Telegram. Lower scores are stored in Sheets but not sent.

| Points | Condition |
|--------|-----------|
| +5 | Premium ≥ $20M |
| +4 | Premium ≥ $10M |
| +3 | Premium ≥ $5M |
| +2 | Premium ≥ $1M |
| +2 | Sweep (≥500 contracts, institutional urgency) |
| +2 | IV spike > 80% on call (buying urgency) |
| +2 | 0–7 DTE (expires this week) |
| +1 | 8–30 DTE |
| +1 | OTM delta (directional bet) |

### Put/Call Ratio
```
P/C < 0.3  → 🔥 Very Bullish
P/C < 0.6  → 🟢 Bullish
P/C < 1.0  → 🟡 Neutral
P/C < 1.5  → 🟠 Cautious
P/C ≥ 1.5  → 🔴 Bearish
```

### Sweep
Large block (≥500 contracts) bought urgently across multiple exchanges.
Institutions don't wait for best price — they want the position NOW.
Strongest signal of institutional conviction. Tagged 🚨 in Telegram.

### IV Spike
IV > 80% on a call = someone paying a premium to get in fast.
Tagged ⚡ in Telegram.

### DTE Buckets
```
0–7 days   🔥  Expires this week — highest urgency
8–30 days  🟢  This month
31–90 days 🟡  Next 1–3 months
90+ days   🟠  LEAPS — structural hedge
```

### Deep ITM vs OTM
```
Delta near 1.0  → Deep ITM → portfolio hedge (less actionable)
Delta near 0.3  → OTM      → directional bet (more actionable)
```

---

## Watchlist (35+ Symbols)

| Group | Symbols | Why |
|-------|---------|-----|
| Index ETFs | SPY, QQQ, IWM | Macro signal — read these first |
| Sector ETFs | XLK, XLF, XLE, XLV, GLD, TLT | Sector rotation signals |
| Mega Caps | AAPL, GOOGL, MSFT, NVDA, AMZN, META, TSLA | 30% of S&P 500 |
| High Vol | AMD, COIN, MSTR, HOOD, SMCI, ARM, SNOW | Most active options |
| Portfolio | PLTR, CRWV, IONQ, OKLO, ACHR, DUOL, SOFI, PYPL, PATH, JOBY, UUUU, POET | Personal holdings |
| Dynamic | Top 10 most active (Alpaca Screener API) | Changes daily |

---

## Google Sheets Structure

**Spreadsheet:** [Options Flow Tracker](https://docs.google.com/spreadsheets/d/1zhF6uyyoJpfbcjQvTqIQ11hbLQ17fO_4mKv1W5H4q8g)

| Tab | Type | Columns | Purpose |
|-----|------|---------|---------|
| `SYMBOL_TRACKER` | Upsert | symbol, signal, pc_ratio, call_vol, put_vol, top flows | Current state per symbol |
| `UNUSUAL_ALERTS` | Append | timestamp, symbol, type, strike, expiry, dte_bucket, volume, premium_k, iv, delta, sweep, iv_spike, signal, **price_at_alert**, **score** | All $5M+ signals with price tracking |
| `OI_SNAPSHOT` | Upsert by date | date, symbol, call_oi, put_oi, pc_oi_ratio | Daily OI for trend detection |
| `EARNINGS_TRACKER` | Append | symbol, earnings_date, pre_pc_ratio, pre_signal, pre_top_flow_k, pre_sweep, actual_eps_surprise, price_before, price_after, price_change_pct, **flow_correct** | Accuracy measurement |
| `SPY`, `QQQ`, `MSFT`... | Append | timestamp, type, strike, expiry, dte_bucket, volume, premium_k, iv, delta, sweep, iv_spike | Full per-symbol history |

---

## Telegram Alert Format

```
📊 Options Flow — Apr 14 09:00

Market Mood: 🟠 Cautious
SPY P/C 1.35 | QQQ P/C 1.21  VIX 19.12🟡
XLK🔴1.64  XLF🟡1.23  XLE🟡0.94  GLD🔴4.25  TLT🔴2.2
🔴 Sector hedging: Tech, Gold, Bonds

🐳 Smart Money Flows (score ≥ 7)
🐻 PUT GLD $540 Apr 17  Vol 3,200  ⭐7  💰 $33,468K
🐻 PUT TSLA $500 Apr 17  Vol 1,925  ⭐7  💰 $28,200K
🐂 CALL SPY $608 May 15  Vol 1,000  IV29%  ⭐7  💰 $7,859K 🚨

💼 Your Portfolio
Bullish:  JOBY 0.24  POET 0.49
Neutral:  NVDA 0.89  AMZN 0.75  IONQ 0.76
Bearish:  MSFT 3.54  TSLA 1.56  PLTR 2.19

🔁 Momentum
🔁 🐻 META PUT $770 May15 — repeated 💰 $19,440K
⚠️ MSFT hedging ↑ P/C 3.2 → 5.99

📅 Earnings This Week
  NFLX reports Apr 15
  TSLA reports Apr 22
```

---

## How to Read the Alerts

**Step 1 — Market mood first**
- SPY/QQQ P/C < 0.7 = market bullish → look for call opportunities
- SPY/QQQ P/C > 1.3 = cautious → reduce risk
- VIX > 25 🔴 = fear, be careful. VIX < 15 🟢 = calm, good for swing trades

**Step 2 — Sector rotation**
- "Rotation: into Energy, out of Tech" = sell tech calls, look at energy
- All sectors hedging = broad market fear

**Step 3 — Find the sweep (🚨) with high score (⭐8+)**
- Bought urgently = highest conviction
- Follow direction (call sweep = bullish, put sweep = bearish)
- OTM delta = directional bet (more actionable than deep ITM hedge)

**Step 4 — Check momentum (🔁)**
- Same contract seen 3+ scans = held for 15+ min = institutional, not noise
- P/C ratio accelerating = conviction growing

**Step 5 — Check your portfolio**
- Bullish flow on your stock = hold or add
- Bearish flow = consider reducing

---

## The Trading Framework

```
OPTIONS FLOW  →  tells you WHAT smart money is doing
NEWS CONTEXT  →  tells you WHY it might be happening
ALIGNMENT     →  both bullish? Higher probability trade
TRADE         →  simple, defined risk, know max loss before entering
NOISE FILTER  →  score < 7? Skip. Not in watchlist? Skip.
```

---

## Setup

### GitHub Secrets Required
```
ALPACA_API_KEY           Alpaca trading account API key
ALPACA_SECRET_KEY        Alpaca trading account secret key
TELEGRAM_BOT_TOKEN       Telegram bot token (@BotFather)
TELEGRAM_CHAT_ID         Your Telegram chat ID
GOOGLE_CREDENTIALS       Google service account JSON (full content)
GOOGLE_OPTIONS_SHEET_ID  Options Flow Tracker spreadsheet ID
```

### Run Locally
```bash
source ~/.alpaca/options-paper.env
pip install -r requirements.txt

python options_flow_scanner.py          # single scan
python options_flow_scanner.py --force  # force send even if no new signals
python weekly_summary.py                # run weekly summary now
```

---

## Disclaimer
Educational and research purposes only. Options trading involves significant risk of loss.
Past flow patterns do not guarantee future price movements. Not financial advice.
