# Options Flow Scanner 📊

> Track institutional options activity ("smart money") across 47+ symbols every 15 minutes.
> Sends scored Telegram alerts, stores time-series data in Google Sheets, and generates AI-powered daily briefs.

**Goal:** Options flow + news context → calculated probability → simple swing trade → managed risk.

---

## Architecture Overview

```
cron-job.org (reliable scheduler)
│
├── Every 15 min (market hours) ──→ GitHub Actions: scan job
├── 8am ET daily ─────────────────→ GitHub Actions: daily-brief (morning)
├── 9am ET daily ─────────────────→ GitHub Actions: scan (pre-market)
├── 4:30pm ET daily ──────────────→ GitHub Actions: scan + daily-brief (evening) + oi-tracker
└── Friday 4pm ET ────────────────→ GitHub Actions: weekly-summary
         │
         ▼
    workflow_dispatch (input: job type)
         │
         ▼
    GitHub Actions (public repo, free unlimited)
         │
    ┌────┴────────────────────────────────────┐
    │  scan job          │  daily-brief job   │
    │  weekly-summary    │  oi-tracker job    │
    └────────────────────┴────────────────────┘
         │
    ┌────┴──────────────────────────────────────────┐
    │  Alpaca API (options chain, 47 symbols)        │
    │  yfinance (OI per strike, EOD)                 │
    │  Yahoo Finance (VIX, earnings calendar)        │
    │  Gemini 2.5 Flash + Groq Llama + OpenRouter    │
    └────────────────────────────────────────────────┘
         │
    ┌────┴──────────────────────────────────────────┐
    │  Telegram alerts                               │
    │  Google Sheets (Options Flow Tracker)          │
    └────────────────────────────────────────────────┘
```

---

## Full Pipeline — Step by Step

### Step 1: Trigger (cron-job.org)
cron-job.org fires HTTP POST to GitHub API every 15 min with `{"ref":"main","inputs":{"job":"scan"}}`.
Each cron-job.org job passes a different `job` input so only the relevant GitHub Actions job runs.

### Step 2: Options Chain Scan (Alpaca API)
`scan_symbol()` fetches the full options chain for each symbol:
- Bid/ask, last trade, greeks (delta, gamma, theta, vega), implied volatility
- Filters: MAX_DTE=45 days, MIN_PREMIUM=$25k

### Step 3: Signal Detection
Each contract is scored 1–10 and tagged:

```
Score breakdown:
  +5  premium ≥ $20M  (massive institutional)
  +4  premium ≥ $10M
  +3  premium ≥ $5M
  +2  premium ≥ $1M
  +2  sweep (≥500 contracts, bought urgently)
  +2  IV spike >80% on call (buying urgency)
  +2  0–7 DTE (expires this week)
  +1  8–30 DTE
  +1  OTM delta (directional bet)

Tags:
  🚨 sweep    = large block, institutional urgency
  ⚡ iv_spike = IV >80%, someone buying fast
  📈 BUY      = last trade ≥ mid-price (buyer aggressive)
  📉 SELL     = last trade < mid-price (seller aggressive)
```

### Step 4: Market Intelligence
- **P/C ratio** per symbol → 🔥🟢🟡🟠🔴 signal
- **Net premium** = total call $ − total put $ (stronger than P/C)
- **Golden Flow** = sweep + score≥8 + premium≥$1M (highest conviction)
- **Sector rotation** = XLK/XLF/XLE/GLD/TLT/ITA/USO/UUP/XBI P/C
- **VIX** from Yahoo Finance
- **Screener API** = Alpaca most-active stocks added dynamically

### Step 5: Momentum Detection
Compares current scan to previous scan:
- Same $5M+ contract seen again → 🔁 "position being built"
- P/C ratio jumped 30%+ → ⚠️ "hedging accelerating"
- Signal flipped (bullish→bearish) → 🔄 logged to SIGNAL_HISTORY
- Same contract 3+ days → 📌 PERSISTENCE

### Step 6: Telegram Alert
Sent only if score ≥ 7 OR new signals detected (dedup suppression):
```
📊 Options Flow — Apr 14 14:00

Market Mood: 🟠 Cautious
SPY P/C 1.19 | QQQ P/C 1.02  VIX 18.2🟡
Tech🟡0.74  Finance🟡1.05  Gold🔴4.34  Defence🟢0.52

⭐ Golden Flow (sweep + score≥8 + $1M+)
🐂 CALL SPY (S&P 500) $608 May15  ⭐9  💰 $7,859K 🚨

💵 Net Premium (call $ minus put $)
  GLD Gold 🐻 Bearish  -$218,843K
  SPY S&P 500 🐂 Bullish  +$35,134K

🐳 Smart Money Flows (score ≥ 7)
🐻 PUT GLD (Gold) $540 Apr17  Vol 3,200  ⭐7  📉SELL  💰 $31,324K
🐂 CALL TSLA (Tesla) $500 Apr17  Vol 1,925  ⭐7  📈BUY  💰 $26,145K

💼 Your Portfolio
Bullish:  UUUU 0.55
Neutral:  NVDA 1.13  AMZN 0.66  IONQ 0.76
Bearish:  MSFT 4.11  TSLA 1.7  PATH 2.41

🔁 Momentum
⚠️ AAPL hedging ↑ P/C 2.68 → 8.91
🚀 GOOGL bullish ↑ P/C 1.63 → 0.54
```

### Step 7: Google Sheets Storage
All data stored in [Options Flow Tracker](https://docs.google.com/spreadsheets/d/1zhF6uyyoJpfbcjQvTqIQ11hbLQ17fO_4mKv1W5H4q8g):

| Tab | Type | Columns | Purpose |
|-----|------|---------|---------|
| `SYMBOL_TRACKER` | Upsert | symbol, signal, pc_ratio, call_vol, put_vol, top flows | Current state per symbol |
| `UNUSUAL_ALERTS` | Prepend | timestamp, symbol, type, strike, expiry, dte_bucket, volume, premium_k, iv, delta, sweep, iv_spike, signal, price_at_alert, score, buy_sell | All $5M+ alerts |
| `SIGNAL_HISTORY` | Append | timestamp, event_type, symbol, detail, value, prev_value | Signal flips, sweeps≥8, 3-day persistence |
| `OI_SNAPSHOT` | Prepend | date, symbol, expiry, strike, type, oi, oi_change, vol, price, signal | Contract-level OI from yfinance (EOD) |
| `EARNINGS_TRACKER` | Append | symbol, earnings_date, pre_pc_ratio, pre_signal, pre_top_flow_k, pre_sweep, actual_eps_surprise, price_before, price_after, price_change_pct, flow_correct | Accuracy measurement |
| `BRIEF_LOG` | Append | timestamp, type, analyst1, analyst2, verifier, brief | Daily AI brief history |
| `SPY`, `QQQ`, `MSFT`... (47 tabs) | Prepend | timestamp, type, strike, expiry, dte_bucket, volume, premium_k, iv, delta, sweep, iv_spike | Full per-symbol history (newest at top) |

### Step 8: AI Daily Brief (3-Model Council)
Runs at 8am ET (morning) and 4:30pm ET (evening):

```
Data from Google Sheets (last 18h)
    ↓
Gemini 2.5 Flash (Google)    → Analysis A
Groq Llama 3.3 70B (Meta)    → Analysis B
    ↓
OpenRouter Gemma 4 31B       → Verifier (catches hallucinations)
    ↓
Telegram: ✅ CONSENSUS / ⚠️ UNCERTAIN / 💡 UNIQUE / 📊 FINAL BRIEF
    ↓
Logged to BRIEF_LOG sheet
```

Fallback chain per role (different priority = no single service gets all 3 calls):
- Analyst 1: Gemini → Groq 70B → OpenRouter
- Analyst 2: Groq 70B → OpenRouter → Gemini
- Verifier: OpenRouter → Groq 8B → Gemini

### Step 9: OI Tracker (EOD, 4:30pm ET)
Uses yfinance to fetch real Open Interest per strike (not available free from Alpaca):
- ATM ±15% strikes only
- Top 5 by OI per symbol
- Nearest weekly + nearest monthly expiry
- Compares to yesterday → detects Long Buildup / Short Buildup / Short Covering / Long Unwinding

### Step 10: Weekly Summary (Friday 4pm ET)
Reads week's UNUSUAL_ALERTS → sends digest:
- Total alerts, call vs put split, overall bias
- Top 5 unique flows (deduplicated)
- Most active symbols, sweep count

---

## What Each File Does

| File | Purpose |
|------|---------|
| `options_flow_scanner.py` | Main scanner: fetch chain, score, detect signals, format, send, store |
| `sheets.py` | All Google Sheets read/write operations |
| `daily_brief.py` | 3-AI council: Gemini + Groq + OpenRouter verifier |
| `oi_tracker.py` | yfinance OI per strike, EOD snapshot |
| `earnings.py` | Upcoming earnings from Yahoo Finance |
| `earnings_tracker.py` | Pre/post earnings accuracy measurement |
| `weekly_summary.py` | Friday EOD digest |
| `notifier.py` | Telegram sender |
| `telegram_trigger.py` | Telegram bot commands (/scan, /brief, /status) |

---

## Watchlist (47 Symbols)

| Group | Symbols | Why |
|-------|---------|-----|
| Index ETFs | SPY, QQQ, IWM | Macro direction — read first |
| Sector ETFs | XLK (Tech), XLF (Finance), XLE (Energy), XLV (Health), GLD (Gold), TLT (Bonds), ITA (Defence), USO (Oil), UUP (Dollar), XBI (Biotech), ARKK (Innovation) | Sector rotation |
| Defence | LMT (Lockheed), RTX (Raytheon), NOC (Northrop), GD (Gen Dynamics) | War/geopolitical signal |
| Cyber | CRWD (CrowdStrike), PANW (Palo Alto), ZS (Zscaler) | Cyber attacks follow conflict |
| Mega Caps | AAPL, GOOGL, MSFT, NVDA, AMZN, META, TSLA | 30% of S&P 500 |
| High Vol | AMD, COIN, MSTR, HOOD, SMCI, ARM, SNOW | Most active options |
| Portfolio | PLTR, CRWV, IONQ, OKLO, ACHR, DUOL, SOFI, PYPL, PATH, JOBY, UUUU, POET | Personal holdings |
| Dynamic | Top 10 most active (Alpaca Screener API, daily) | Catches unusual stocks |

---

## Signal Logic

### Alert Score (1–10) — Only ≥7 sent to Telegram

| Points | Condition |
|--------|-----------|
| +5 | Premium ≥ $20M |
| +4 | Premium ≥ $10M |
| +3 | Premium ≥ $5M |
| +2 | Premium ≥ $1M |
| +2 | Sweep (≥500 contracts) |
| +2 | IV spike >80% on call |
| +2 | 0–7 DTE (this week) |
| +1 | 8–30 DTE |
| +1 | OTM delta |

### Put/Call Ratio
```
P/C < 0.3  → 🔥 Very Bullish
P/C < 0.6  → 🟢 Bullish
P/C < 1.0  → 🟡 Neutral
P/C < 1.5  → 🟠 Cautious
P/C ≥ 1.5  → 🔴 Bearish
```

### DTE Buckets
```
0–7 days   🔥  Expires this week — highest urgency
8–30 days  🟢  This month
31–90 days 🟡  Next 1–3 months
90+ days   🟠  LEAPS — structural hedge
```

### OI Signal (from yfinance, EOD)
```
OI↑ + price↑ = 🐂 Long Buildup    (new buyers entering)
OI↑ + price↓ = 🐻 Short Buildup   (new sellers entering)
OI↓ + price↑ = 🟡 Short Covering  (bears giving up)
OI↓ + price↓ = 🟡 Long Unwinding  (bulls giving up)
```

### Buy/Sell Inference (mid-price rule)
```
Last trade ≥ mid-price → 📈 BUY  (buyer was aggressive)
Last trade < mid-price  → 📉 SELL (seller was aggressive)
```

---

## Scheduling (cron-job.org → GitHub Actions)

All schedules run via cron-job.org which sends `workflow_dispatch` with a `job` input.
Each GitHub Actions job only runs when its specific input is received.

| cron-job.org Job | Time (UTC) | Days | Input | GitHub Jobs |
|-----------------|-----------|------|-------|-------------|
| 15-min scan | */15 14-20 | Mon-Fri | `scan` | scan |
| Morning Brief | 12:00 | Mon-Fri | `brief` | daily-brief (--morning) |
| Pre-market | 13:00 | Mon-Fri | `premarket` | scan (--premarket) |
| EOD + OI | 20:30 | Mon-Fri | `eod` | scan (--afterhours) + daily-brief (--evening) + oi-tracker |
| Weekly Summary | 20:00 | Friday | `weekly` | weekly-summary |

---

## How to Read the Alerts

**Step 1 — Market mood first**
- SPY/QQQ P/C < 0.7 = bullish → look for call opportunities
- SPY/QQQ P/C > 1.3 = cautious → reduce risk
- VIX > 25 🔴 = fear. VIX < 15 🟢 = calm

**Step 2 — Sector rotation**
- Defence🟢 + Tech🔴 = money rotating into defence
- Gold🔴 = gold being sold (risk-on)

**Step 3 — Golden Flow (⭐ section)**
- All 3 conditions: sweep + score≥8 + $1M+ = highest conviction
- Follow the direction

**Step 4 — Check buy/sell tag**
- 📈 BUY = buyer was aggressive = opening new position
- 📉 SELL = seller was aggressive = closing or writing

**Step 5 — Momentum (🔁 section)**
- Same contract 3+ scans = institutional, not noise
- P/C acceleration = conviction growing

**Step 6 — Your portfolio**
- Bullish flow = hold or add
- Bearish flow = consider reducing

---

## The Trading Framework

```
OPTIONS FLOW  →  tells you WHAT smart money is doing
NEWS CONTEXT  →  tells you WHY (from alpaca-news-bot)
ALIGNMENT     →  both bullish? Higher probability
TRADE         →  simple, defined risk, know max loss before entering
NOISE FILTER  →  score < 7? Skip. Deep ITM put? Likely hedge, not signal.
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
GOOGLE_AI_API            Gemini API key (aistudio.google.com, free)
GOOGLE_AI_API_2          Gemini API key fallback (second Google account)
GROQ_API_KEY             Groq API key (console.groq.com, free)
OPENROUTER_API_KEY       OpenRouter API key (openrouter.ai, free)
```

### cron-job.org Setup
5 jobs configured at cron-job.org (IDs: 7485766, 7485841, 7485847, 7485848, 7485849).
Each sends POST to GitHub API with `{"ref":"main","inputs":{"job":"<type>"}}`.

### Run Locally
```bash
source ~/.alpaca/options-paper.env
pip install -r requirements.txt

python options_flow_scanner.py           # single scan
python options_flow_scanner.py --force   # force send even if no new signals
python options_flow_scanner.py --premarket   # pre-market mode
python options_flow_scanner.py --afterhours  # after-hours mode
python daily_brief.py --morning          # morning AI brief
python daily_brief.py --evening          # evening AI brief
python oi_tracker.py                     # EOD OI snapshot
python weekly_summary.py                 # weekly digest
```

---

## Disclaimer
Educational and research purposes only. Options trading involves significant risk of loss.
Past flow patterns do not guarantee future price movements. Not financial advice.
