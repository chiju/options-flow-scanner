# Options Flow Scanner 📊

> Track institutional options activity ("smart money") across 35 symbols every 5 minutes. Sends Telegram alerts and stores data in Google Sheets for trend analysis.

---

## How It Works — Visual Flow

```
Every 5 minutes (GitHub Actions, free public repo)
│
├── 📡 Fetch options chain via Alpaca API
│   └── 35 symbols: indexes + sectors + mega caps + your portfolio
│
├── 🔍 Analyse each contract
│   ├── Calculate premium (mid price × volume × 100)
│   ├── Tag SWEEP if volume ≥ 500 contracts
│   ├── Tag IV_SPIKE if implied volatility > 80%
│   └── Calculate Put/Call ratio per symbol
│
├── 🧠 Compare to previous scan (momentum)
│   ├── Same $5M+ contract seen again → "position being built"
│   └── P/C ratio jumped 30%+ → "hedging accelerating"
│
├── 📱 Send Telegram alert
│   ├── Market mood (SPY/QQQ P/C ratio)
│   ├── Sector ETF snapshot (XLK/XLF/XLE/GLD/TLT)
│   ├── Top 10 flows sorted by $ size
│   ├── Portfolio P/C ratios
│   └── Momentum signals (repeated positions)
│
└── 📊 Store to Google Sheets
    ├── UNUSUAL_ALERTS  → append all $5M+ signals
    └── SYMBOL_TRACKER  → upsert current state per symbol
        + one tab per symbol (SPY, MSFT, TSLA, etc.)
```

---

## What Each File Does

### `options_flow_scanner.py` — Main Entry Point
The orchestrator. Runs the full pipeline on every execution.

```
Key sections:
  ALL_SYMBOLS     — 35 symbols to scan (indexes, sectors, mega caps, portfolio)
  THRESHOLDS      — Vol/OI ratio, min premium, max DTE, sweep size, IV spike level
  scan_symbol()   — fetches one symbol's options chain, returns calls/puts with signals
  interpret_signal() — converts P/C ratio to human-readable signal (🔥/🟢/🟡/🟠/🔴)
  format_report() — builds the Telegram message
  run_scan()      — main loop: scan all → compare → format → send → store
```

### `sheets.py` — Google Sheets Storage
Handles all read/write operations to the Options Flow Tracker spreadsheet.

```
Key functions:
  get_last_scan()     — reads previous scan's P/C ratios and contracts from sheets
  compare_scans()     — compares current vs previous, returns momentum alerts
  store_results()     — writes to all sheets, returns momentum alerts
  _ensure_tabs()      — auto-creates missing tabs with headers on first run
  _upsert_tracker()   — updates existing symbol row or appends new one
  dte_bucket()        — classifies contract by days-to-expiry (0-7d🔥 / 8-30d🟢 / etc.)
```

### `notifier.py` — Telegram Sender
Simple wrapper that sends messages to Telegram, splitting long messages into chunks.

### `.github/workflows/scanner.yml` — Automation
GitHub Actions workflow. Runs every 5 minutes during market hours (10am–4pm ET, Mon–Fri).

---

## Signal Logic

### Put/Call (P/C) Ratio
```
P/C < 0.3  → 🔥 Very Bullish  (far more calls than puts)
P/C < 0.6  → 🟢 Bullish
P/C < 1.0  → 🟡 Neutral
P/C < 1.5  → 🟠 Cautious
P/C ≥ 1.5  → 🔴 Bearish       (far more puts than calls)
```

### Sweep Detection
A sweep = large block order (≥500 contracts) bought urgently.
Institutions don't wait for best price — they want the position NOW.
This is the strongest signal of institutional conviction.

### IV Spike Detection
Rising implied volatility on a call = demand driving price up = someone buying urgently.
IV > 80% on an OTM call = someone paying a premium to get in fast.

### DTE Buckets (Days to Expiry)
```
0–7 days   🔥  Expires this week — highest urgency, most actionable
8–30 days  🟢  This month — medium conviction
31–90 days 🟡  Next 1–3 months — institutional positioning
90+ days   🟠  LEAPS — long-term structural hedge, less urgent
```

### Deep ITM vs OTM
```
Delta near 1.0  → Deep In The Money → stock replacement / portfolio hedge
Delta near 0.3  → Out of The Money  → directional bet, high leverage
```

---

## Watchlist (35 Symbols)

| Group | Symbols | Why |
|-------|---------|-----|
| Index ETFs | SPY, QQQ, IWM | Macro signal — institutions hedge here first |
| Sector ETFs | XLK, XLF, XLE, XLV, GLD, TLT | Sector rotation signals |
| Mega Caps | AAPL, GOOGL, MSFT, NVDA, AMZN, META, TSLA | 30% of S&P 500 |
| High Vol | AMD, COIN, MSTR, HOOD, SMCI, ARM, SNOW | Most active options markets |
| Portfolio | PLTR, CRWV, IONQ, OKLO, ACHR, DUOL, SOFI, PYPL, PATH, JOBY, UUUU, POET | Personal holdings |

---

## Google Sheets Structure

**Spreadsheet:** [Options Flow Tracker](https://docs.google.com/spreadsheets/d/1zhF6uyyoJpfbcjQvTqIQ11hbLQ17fO_4mKv1W5H4q8g)

| Tab | Type | Purpose |
|-----|------|---------|
| `SYMBOL_TRACKER` | Upsert (22 rows) | Current state per symbol — P/C, signal, top flows |
| `UNUSUAL_ALERTS` | Append | All $5M+ signals with timestamp — builds history |
| `SPY`, `QQQ`, `MSFT`... | Append | Every contract per symbol — full options chain history |

---

## Telegram Alert Format

```
📊 Options Flow — Apr 13 21:15

Market Mood: 🟠 Cautious
SPY P/C 1.16 | QQQ P/C 1.10
XLK 🟡1.2  XLF 🟡0.9  XLE 🟢0.5  GLD 🟢0.4  TLT 🟡1.1

🐳 Smart Money Flows (biggest $ first)
🐻 PUT META $770 May 15  Vol 1,430  IV 49%  💰 $20,313K
🐻 PUT IWM  $295 May 15  Vol 5,000  IV 28%  💰 $15,565K
🐂 CALL SPY $608 May 15  Vol 1,000  IV 29%  💰 $7,859K 🚨

💼 Your Portfolio
Bullish:  SOFI P/C 0.42  UUUU P/C 0.47
Neutral:  NVDA P/C 1.1  IONQ P/C 0.65  OKLO P/C 0.64
Bearish:  MSFT P/C 5.99  TSLA P/C 1.53  PLTR P/C 2.30

🔁 Momentum Signals
🔁 🐻 META PUT $770 May15 — repeated 💰 $20,313K
🔁 🐻 IWM PUT $295 May15 — repeated 💰 $15,565K
⚠️ MSFT hedging ↑ P/C 3.2 → 5.99
```

---

## How to Read the Alerts

**Step 1 — Check Market Mood first**
- SPY/QQQ P/C < 0.7 = market bullish → look for call opportunities
- SPY/QQQ P/C > 1.3 = market cautious → reduce risk

**Step 2 — Find the sweep (🚨)**
- Bought urgently = highest conviction signal
- Follow the direction (call sweep = bullish, put sweep = bearish)

**Step 3 — Check momentum (🔁)**
- Seen 3+ scans = position held for 15+ minutes = institutional, not noise
- One-time = could be a hedge, less actionable

**Step 4 — Check your portfolio**
- Bullish flow on your stock = hold or add
- Bearish flow = consider reducing position

---

## Setup

### Secrets Required (GitHub → Settings → Secrets)
```
ALPACA_API_KEY          Alpaca trading account API key
ALPACA_SECRET_KEY       Alpaca trading account secret key
TELEGRAM_BOT_TOKEN      Telegram bot token (@BotFather)
TELEGRAM_CHAT_ID        Your Telegram chat ID
GOOGLE_CREDENTIALS      Google service account JSON (full content)
GOOGLE_OPTIONS_SHEET_ID Options Flow Tracker spreadsheet ID
```

### Run Locally
```bash
# Set env vars
export ALPACA_API_KEY=...
export ALPACA_SECRET_KEY=...
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
export GOOGLE_OPTIONS_SHEET_ID=1zhF6uyyoJpfbcjQvTqIQ11hbLQ17fO_4mKv1W5H4q8g

pip install -r requirements.txt
python options_flow_scanner.py
```

### Schedule (GitHub Actions)
Runs automatically every 5 minutes, 10am–4pm ET, Monday–Friday.
Public repo = unlimited free minutes.

---

## Disclaimer
This tool is for educational and research purposes only. Options trading involves significant risk. Past flow patterns do not guarantee future price movements. Not financial advice.
