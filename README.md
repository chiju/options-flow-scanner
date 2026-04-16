# Options Flow Scanner 📊

> Track institutional options activity ("smart money") across 47+ symbols every 15 minutes.
> Silent mode — only alerts on Golden Flow or ⭐⭐⭐ high confluence. AI-powered daily briefs with 3-day memory.

**Goal:** Options flow + news + GEX + price trend → confluence score → calculated probability → swing trade → managed risk.

---

## Architecture Overview

```
cron-job.org (reliable scheduler)
│
├── Every 15 min (market hours) ──→ scan (SILENT — sheets only, alert on Golden Flow/⭐⭐⭐ only)
├── 8am ET daily ─────────────────→ daily-brief --morning
├── 9am ET daily ─────────────────→ scan --premarket
├── 4:30pm ET daily ──────────────→ scan + daily-brief + oi-tracker + gamma-levels + signal-outcomes
└── Friday 4pm ET ────────────────→ weekly-summary
         │
         ▼
    workflow_dispatch (input: scan | brief | premarket | eod | weekly)
         │
         ▼
    GitHub Actions (public repo, free)
         │
    ┌────┴──────────────────────────────────────────┐
    │  Alpaca API (options chain, 47+ symbols)       │
    │  Alpaca News API + FinBERT (HuggingFace)       │
    │  Reddit (WSB/stocks/investing, free)           │
    │  yfinance (OI per strike, EOD)                 │
    │  Yahoo Finance (VIX, earnings calendar)        │
    │  Gemini 2.5 Flash + Groq Llama + OpenRouter    │
    └────────────────────────────────────────────────┘
         │
    ┌────┴──────────────────────────────────────────┐
    │  Telegram (high-conviction alerts + briefs)    │
    │  Google Sheets (9 tabs, time-series data)      │
    └────────────────────────────────────────────────┘
```

---

## Full Pipeline

### Step 1: Trigger (cron-job.org)
cron-job.org fires HTTP POST to GitHub API with `{"ref":"main","inputs":{"job":"scan"}}`.
5 jobs: scan (every 15 min), brief (8am), premarket (9am), eod (4:30pm), weekly (Friday).

### Step 2: Options Chain Scan (Alpaca API)
`scan_symbol()` fetches the full options chain for each symbol:
- Bid/ask, last trade, greeks (delta, gamma), implied volatility
- Filters: MAX_DTE=45 days, MIN_PREMIUM=$25k
- Note: Alpaca greeks only available for 8-45 DTE (not 0-7 DTE)
- Note: OI not in Alpaca chain — fetched separately via yfinance EOD

### Step 3: Signal Scoring (1–10)

```
+5  premium ≥ $20M
+4  premium ≥ $10M
+3  premium ≥ $5M
+2  premium ≥ $1M
+1  premium ≥ $100K
+2  sweep (≥500 contracts, institutional block)
+2  IV spike >80% on call (buying urgency)
+2  0–7 DTE (expires this week)
+1  8–30 DTE
+1  OTM delta (directional bet, not ITM hedge)

Tags:
  🚨 sweep    = large block, institutional urgency
  ⚡ iv_spike = IV >80%, someone buying fast
  📈 BUY      = last trade ≥ mid-price (buyer aggressive)
  📉 SELL     = last trade < mid-price (seller aggressive)
```

### Step 4: Confluence Score (NEW)
Each Golden Flow signal is scored across 3 independent dimensions:

```
⭐⭐⭐ HIGH    = all 3 agree → highest probability
⭐⭐   Medium  = 2 agree
⭐    Low     = 1 signal only

Dimension 1: Options flow direction (P/C ratio confirms)
Dimension 2: News sentiment (FinBERT-scored Alpaca News)
Dimension 3: GEX regime (negative GEX = move will be amplified)
Bonus:       Reddit buzz (WSB/stocks/investing mentions)
```

### Step 5: Silent Mode Telegram Alerts
15-min scan writes to sheets always. Telegram only fires when:
- **Golden Flow** = sweep + score≥8 + premium≥$1M
- **⭐⭐⭐ HIGH confluence** = flow + news + GEX all agree

Alert format:
```
🚨 High Conviction Alert — Apr 16 14:00

⭐ Golden Flow
🐂 CALL ARK Innovation (ARKK) $71 Apr17  ⭐10  💰 $11,051K
  └ ⭐⭐⭐ HIGH (flow🐂 + news🟢 + gex🔴trending)

SPY P/C 1.47 | VIX 18.1
```

### Step 6: Google Sheets Storage

| Tab | Purpose | Updated |
|-----|---------|---------|
| `SYMBOL_TRACKER` | Current P/C ratio + signal per symbol | Every 15 min (upsert) |
| `UNUSUAL_ALERTS` | All $5M+ flows or sweeps with score | Every 15 min (append) |
| `SIGNAL_HISTORY` | Signal flips, sweep≥8, 3-day persistence | Every 15 min (append) |
| `OI_SNAPSHOT` | Real OI per strike from yfinance | EOD only |
| `GAMMA_LEVELS` | Max Pain, Call Wall, Put Wall, GEX per symbol | EOD only |
| `SIGNAL_OUTCOMES` | Was the signal right? 1d/3d price check | EOD only |
| `BRIEF_LOG` | AI brief history | 2x daily |
| `EARNINGS_TRACKER` | Pre/post earnings flow accuracy | On earnings |
| `EARNINGS_CALENDAR` | Upcoming earnings dates | On scan |

### Step 7: AI Daily Brief (3-Model Council with Memory)
Runs at 8am ET (morning) and 4:30pm ET (evening).

**Data fed to AI:**
- Price trend (SPY/QQQ/IWM/GLD/TLT — actual % move today + 5d)
- 3-day historical context (what signals fired, were they right?)
- Current flow data (UNUSUAL_ALERTS, SYMBOL_TRACKER)
- GEX regime per symbol (GAMMA_LEVELS)
- FinBERT news sentiment (Alpaca News API)
- Reddit buzz (WSB/stocks/investing)

**Chain-of-Thought reasoning:**
```
Step 1: Read price trend — is market up or down?
Step 2: Filter hedges — puts during rally = hedges (ignore), per symbol
Step 3: Check history — what's been building 3+ days?
Step 4: Score confluence — flow + news + GEX alignment
Step 5: Write decisive brief with specific $ amounts
```

**3-model pipeline:**
```
Gemini 2.5 Flash  → Analyst 1 (best quality)
Groq Llama 70B    → Analyst 2 (fastest, different perspective)
Gemini 2.5 Flash  → Verifier (catches hallucinations, decisive)
    ↓
Telegram: ✅ CONSENSUS / ⚠️ UNCERTAIN / 💡 UNIQUE / 📊 FINAL BRIEF
```

### Step 8: EOD Bundle (4:30pm ET, 5 jobs in parallel)
- `scan --afterhours` — after-hours flow
- `daily-brief --evening` — evening digest
- `oi-tracker` — real OI per strike (yfinance), detects Long/Short Buildup
- `gamma-levels` — Max Pain, Call Wall, Put Wall, GEX time series
- `signal-outcomes` — fetch 1d/3d prices, record if signal was correct

### Step 9: Signal Outcomes Tracker
Every EOD, for each score≥7 alert from UNUSUAL_ALERTS:
- Fetch price 1 day and 3 days after alert (Alpaca historical data)
- Record: was the direction correct? (✅/❌)
- Check OI_SNAPSHOT: did OI increase next day? (new position = real signal, OI decrease = closing/hedge)

After 4-6 weeks: accuracy % by score/type/symbol = your edge measurement.

### Step 10: Gamma Levels (NEW)
Daily EOD snapshot per symbol (SPY, QQQ, IWM, AAPL, NVDA, TSLA, MSFT, AMZN, META):

```
Max Pain  = strike where most options expire worthless (MM pinning target near expiry)
Call Wall = strike with highest call OI (resistance level)
Put Wall  = strike with highest put OI (support level)
GEX       = net gamma exposure across all strikes
  Positive GEX → MMs stabilize price (low vol, mean-reverting)
  Negative GEX → MMs amplify moves (trending, volatile)
```

---

## What Each File Does

| File | Purpose |
|------|---------|
| `options_flow_scanner.py` | Main scanner: fetch chain, score, confluence, silent alert |
| `sheets.py` | All Google Sheets read/write |
| `daily_brief.py` | 3-AI council with CoT prompts, FinBERT news, Reddit, price trend, memory |
| `oi_tracker.py` | yfinance OI per strike, EOD snapshot |
| `gamma_levels.py` | Max Pain, Call Wall, Put Wall, GEX time series |
| `signal_outcomes.py` | Signal accuracy tracker (1d/3d price outcomes + OI confirmation) |
| `earnings.py` | Upcoming earnings from Yahoo Finance |
| `earnings_tracker.py` | Pre/post earnings accuracy measurement |
| `weekly_summary.py` | Friday EOD digest |
| `notifier.py` | Telegram sender |

---

## Watchlist (47 Fixed + Dynamic)

| Group | Symbols |
|-------|---------|
| Index ETFs | SPY, QQQ, IWM |
| Sector ETFs | XLK, XLF, XLE, XLV, GLD, TLT, ITA, USO, UUP, XBI, ARKK |
| Defence | LMT, RTX, NOC, GD |
| Cyber | CRWD, PANW, ZS |
| Mega Caps | AAPL, GOOGL, MSFT, NVDA, AMZN, META, TSLA |
| High Vol | AMD, COIN, MSTR, HOOD, SMCI, ARM, SNOW |
| Portfolio | PLTR, CRWV, IONQ, OKLO, ACHR, DUOL, SOFI, PYPL, PATH, JOBY, UUUU, POET |
| Dynamic | Top 10 most active (Alpaca Screener API) |

---

## Signal Logic

### Score (1–10) — Golden Flow requires score≥8 + sweep + $1M+

### Put/Call Ratio
```
P/C < 0.3  → 🔥 Very Bullish
P/C < 0.6  → 🟢 Bullish
P/C < 1.0  → 🟡 Neutral
P/C < 1.5  → 🟠 Cautious
P/C ≥ 1.5  → 🔴 Bearish
```

### Hedge vs Directional (KEY RULE)
```
Symbol UP + high put volume  → puts are HEDGES on longs (ignore for direction)
Symbol DOWN + high call volume → calls are HEDGES on shorts (ignore for direction)
Flow AGAINST price trend = real directional conviction
```

### OI Confirmation (from oi_tracker.py EOD)
```
OI↑ next day = new position = real signal ✅
OI↓ next day = closing/hedge = ignore ⚠️
```

### GEX Regime
```
Positive GEX → price pinned, low vol (MMs buy dips, sell rips)
Negative GEX → price amplified, trending (MMs add fuel to moves)
```

---

## Scheduling (cron-job.org → GitHub Actions)

| Job ID | Time (UTC) | Days | Input | Runs |
|--------|-----------|------|-------|------|
| 7485766 | */15 14-20 | Mon-Fri | `scan` | scan (silent) |
| 7485841 | 12:00 | Mon-Fri | `brief` | daily-brief --morning |
| 7485847 | 13:00 | Mon-Fri | `premarket` | scan --premarket |
| 7485848 | 20:30 | Mon-Fri | `eod` | scan + brief + oi-tracker + gamma-levels + signal-outcomes |
| 7485849 | 20:00 | Friday | `weekly` | weekly-summary |

---

## How to Read Alerts

**15-min alert fires only when:**
- Golden Flow (sweep + score≥8 + $1M+), OR
- ⭐⭐⭐ HIGH confluence (flow + news + GEX all agree)

**Morning/Evening brief tells you:**
1. Is the market actually up or down? (price trend)
2. Which flows are real signals vs hedges?
3. What's been building for 3+ days? (persistence = conviction)
4. Single highest-probability setup with reasoning

**The trading framework:**
```
Signal fires (⭐⭐⭐ HIGH confluence)
  → OI increased next day? (new position, not closing)
  → News aligned?
  → GEX negative? (move will be amplified)
  → If all yes: small defined-risk trade, know max loss upfront
  → Size: 1-2% of account max
```

---

## Setup

### GitHub Secrets
```
ALPACA_API_KEY           Alpaca paper trading API key
ALPACA_SECRET_KEY        Alpaca paper trading secret key
TELEGRAM_BOT_TOKEN       Telegram bot token
TELEGRAM_CHAT_ID         Your Telegram chat ID
GOOGLE_CREDENTIALS       Google service account JSON
GOOGLE_OPTIONS_SHEET_ID  Google Sheet ID
GOOGLE_AI_API            Gemini API key (aistudio.google.com, free)
GOOGLE_AI_API_2          Gemini API key fallback
GROQ_API_KEY             Groq API key (console.groq.com, free)
OPENROUTER_API_KEY       OpenRouter API key (openrouter.ai, free)
HF_TOKEN                 HuggingFace token (FinBERT sentiment)
```

### Local Setup
```bash
# Install uv (fast Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

cd ~/stocks/options-flow-scanner
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt

# Load credentials
source ~/.alpaca/options-paper.env

# Run
python options_flow_scanner.py --force   # force send full report
python daily_brief.py --morning          # morning AI brief
python daily_brief.py --evening          # evening AI brief
python oi_tracker.py                     # EOD OI snapshot
python gamma_levels.py                   # EOD gamma levels
python signal_outcomes.py                # EOD signal accuracy
python weekly_summary.py                 # weekly digest
```

---

## Related Repo
[alpaca-news-bot](https://github.com/chiju/alpaca-news-bot) — Paper trading strategies (Wheel/CSP/Iron Condor) that will eventually consume signals from this repo.

---

## Disclaimer
Educational and research purposes only. Options trading involves significant risk of loss.
Past flow patterns do not guarantee future price movements. Not financial advice.
