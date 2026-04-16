# Options Flow Scanner 📊

> Institutional options flow intelligence — tracks smart money across 47 symbols every 15 minutes.
> Silent alerts only on Golden Flow or ⭐⭐⭐ high confluence. AI daily briefs with memory.

**Goal:** Flow + news + GEX + price trend → confluence score → probability → edge measurement → trade

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     TWO-REPO SYSTEM                                  │
│                                                                      │
│  options-flow-scanner          alpaca-news-bot                      │
│  ─────────────────────         ───────────────                      │
│  ANALYSIS ENGINE               TRADING ENGINE                       │
│                                                                      │
│  • Options flow (Alpaca)       • News digest (FinBERT)              │
│  • Signal scoring 1-10         • Reddit sentiment                   │
│  • Confluence detection        • Paper trading strategies           │
│  • GEX / Max Pain / OI         • P&L tracking                      │
│  • FinBERT news sentiment      • 5 isolated paper accounts          │
│  • Reddit buzz                 • Daily P&L report                   │
│  • 3-AI daily brief            • PERFORMANCE_LOG                    │
│  • Signal accuracy tracking                                         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## options-flow-scanner — Full Pipeline

```
cron-job.org
│
├── Every 15 min (Mon-Fri 16:00-22:45 Berlin)
│   └── SCAN (silent mode)
│       ├── Alpaca: fetch options chain (47+ symbols, 45 DTE max)
│       ├── Score each contract 1-10
│       ├── Detect: Golden Flow, Net Premium, Sector Rotation
│       ├── Confluence: flow + news + GEX
│       ├── Write → SYMBOL_TRACKER, UNUSUAL_ALERTS, SIGNAL_HISTORY
│       └── Telegram ONLY if: Golden Flow OR ⭐⭐⭐ HIGH confluence
│           (deduped: once per contract per day)
│
├── 14:00 UTC (8am ET / 10:00 Berlin) — Morning Brief
│   └── DAILY_BRIEF --morning
│       ├── Reads: last 18h flow + 3-day history + GEX + news + Reddit
│       ├── Chain-of-Thought: price trend → filter hedges → score confluence
│       ├── Gemini (Analyst 1) + Groq (Analyst 2) + Gemini (Verifier)
│       └── Telegram: ✅ CONSENSUS / ⚠️ UNCERTAIN / 💡 UNIQUE / 📊 FINAL BRIEF
│
├── 15:00 UTC (9am ET) — Pre-market scan
│
├── 20:30 UTC (4:30pm ET / 22:30 Berlin) — EOD Bundle (5 jobs parallel)
│   ├── SCAN --afterhours
│   ├── DAILY_BRIEF --evening
│   ├── OI_TRACKER → OI_SNAPSHOT (real OI per strike, yfinance)
│   ├── GAMMA_LEVELS → Max Pain, Call Wall, Put Wall, GEX time series
│   └── SIGNAL_OUTCOMES → was the signal right? 1d/3d price check
│
└── 22:00 UTC Friday — Weekly Summary → Telegram digest
```

---

## Telegram Alerts

**High Conviction Alert** (fires when Golden Flow OR ⭐⭐⭐ confluence):
```
🚨 High Conviction Alert — Apr 16 16:01

⭐ Golden Flow
🐂 CALL CoreWeave (CRWV) $120 May08  ⭐8  💰 $7,753K
  └ ⭐ Low (flow🐂)
🐂 CALL MicroStrategy (MSTR) $136 Apr17  ⭐8  💰 $4,675K
  └ ⭐⭐ Medium (flow🐂 + news🟢)

SPY P/C 1.19 | VIX 18.59
```

**Morning/Evening Brief** (2x daily):
```
📊 FINAL BRIEF:
Market is cautious (SPY P/C 1.47). GLD puts are hedges (gold up +0.5%).
Real signal: ARKK calls swept 9x over 2 days = institutional conviction.
IWM puts ($12.7M) align with bearish P/C — small caps under pressure.
Watch: ARKK CALL $71 Apr17 (expires tomorrow, already ITM at $76).
```

---

## Google Sheets — Options Flow Tracker

| Tab | What's stored | Updated |
|-----|--------------|---------|
| `SYMBOL_TRACKER` | symbol, name, type, interpretation, P/C, net premium, price, price_chg | Every 15 min |
| `UNUSUAL_ALERTS` | All $5M+ flows or sweeps with score, buy/sell, OI | Every 15 min |
| `SIGNAL_HISTORY` | Signal flips, sweep≥8, 3-day persistence | Every 15 min |
| `OI_SNAPSHOT` | Real OI per strike (yfinance) | EOD |
| `GAMMA_LEVELS` | Max Pain, Call Wall, Put Wall, GEX per symbol/expiry | EOD |
| `SIGNAL_OUTCOMES` | Was signal right? 1d/3d price + OI confirmation | EOD |
| `BRIEF_LOG` | AI brief history | 2x daily |
| `EARNINGS_TRACKER` | Pre/post earnings flow accuracy | On earnings |

### SYMBOL_TRACKER columns
```
symbol | name | type | interpretation | pc_ratio | net_premium_k | price | price_chg | call_vol | put_vol

Interpretation (P/C + price direction):
  🛡️ Hedging    = puts high BUT price rising → protecting longs (not bearish)
  😨 Fear        = puts high AND price falling → real bearish conviction
  🔥 Greed       = calls high AND price rising → pure bullish
  ⚠️ Complacency = calls high BUT price falling → ignoring risk
  🟢 Call bias   = neutral P/C but call $ dominates
  🔴 Put bias    = neutral P/C but put $ dominates
```

---

## Signal Intelligence Layers

```
Layer 1: FLOW      → Vol > OI = new positions (not closing)
Layer 2: PREMIUM   → Score 1-10 (size + sweep + IV + DTE + delta)
Layer 3: DIRECTION → P/C ratio + net premium (call$ - put$)
Layer 4: CONTEXT   → Price trend (hedge vs directional)
Layer 5: NEWS      → FinBERT sentiment (Alpaca News API)
Layer 6: SOCIAL    → Reddit buzz (WSB/stocks/investing)
Layer 7: STRUCTURE → GEX (positive=pinned, negative=amplified)
Layer 8: HISTORY   → Signal outcomes (was it right before?)
Layer 9: STORY     → AI brief (what does it all mean?)

⭐⭐⭐ HIGH confluence = layers 1+5+7 all agree = highest probability
```

---

## Scoring (1–10)

| Points | Condition |
|--------|-----------|
| +5 | Premium ≥ $20M |
| +4 | Premium ≥ $10M |
| +3 | Premium ≥ $5M |
| +2 | Premium ≥ $1M |
| +1 | Premium ≥ $100K |
| +2 | Sweep (≥500 contracts) |
| +2 | IV spike >80% on call |
| +2 | 0–7 DTE (expires this week) |
| +1 | 8–30 DTE |
| +1 | OTM delta |

**Golden Flow** = sweep + score≥8 + premium≥$1M → triggers Telegram alert

---

## GEX (Gamma Exposure)

```
GEX = gamma × OI × 100 × spot²  (per strike, summed across chain)

Positive GEX → MMs buy dips / sell rips → price PINNED (low vol)
Negative GEX → MMs amplify moves → price TRENDING (high vol)

SPY GEX = -3.84M today → negative → moves will be amplified
```

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
| Dynamic | Top 10 most active (Alpaca Screener, each scan) |

---

## Schedule (cron-job.org → GitHub Actions)

| Job ID | Berlin time | ET | Input | Runs |
|--------|------------|-----|-------|------|
| 7485766 | 16:00–22:45 every 15min | 10am–4:45pm | `scan` | Silent scan |
| 7485841 | 14:00 | 8:00am | `brief` | Morning AI brief |
| 7485847 | 15:00 | 9:00am | `premarket` | Pre-market scan |
| 7485848 | 22:30 | 4:30pm | `eod` | EOD bundle (5 jobs) |
| 7485849 | 22:00 Fri | 4:00pm Fri | `weekly` | Weekly summary |

---

## Files

| File | Purpose |
|------|---------|
| `options_flow_scanner.py` | Main scanner: fetch, score, confluence, silent alert |
| `sheets.py` | All Google Sheets read/write |
| `daily_brief.py` | 3-AI council: CoT prompts, FinBERT, Reddit, price trend, memory |
| `gamma_levels.py` | Max Pain, Call Wall, Put Wall, GEX time series |
| `oi_tracker.py` | Real OI per strike (yfinance), EOD |
| `signal_outcomes.py` | Signal accuracy: 1d/3d price + OI confirmation |
| `earnings.py` | Upcoming earnings (Yahoo Finance) |
| `earnings_tracker.py` | Pre/post earnings accuracy |
| `weekly_summary.py` | Friday EOD digest |
| `notifier.py` | Telegram sender |
| `telegram_trigger.py` | Bot commands: /status /scan /brief /help |

---

## GitHub Secrets

```
ALPACA_API_KEY / ALPACA_SECRET_KEY   Alpaca paper trading (options)
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
GOOGLE_CREDENTIALS                   Service account JSON
GOOGLE_OPTIONS_SHEET_ID              Options Flow Tracker sheet
GOOGLE_AI_API / GOOGLE_AI_API_2      Gemini (aistudio.google.com, free)
GROQ_API_KEY                         Groq (console.groq.com, free)
OPENROUTER_API_KEY                   OpenRouter (openrouter.ai, free)
HF_TOKEN                             HuggingFace (FinBERT sentiment)
```

## Local Setup

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
cd ~/stocks/options-flow-scanner
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
source ~/.alpaca/options-paper.env

python options_flow_scanner.py --force   # full report
python daily_brief.py --morning          # morning brief
python gamma_levels.py                   # EOD gamma levels
python signal_outcomes.py                # EOD accuracy check
python oi_tracker.py                     # EOD OI snapshot
```

---

## Related
[alpaca-news-bot](https://github.com/chiju/alpaca-news-bot) — Paper trading strategies (Wheel/CSP/Bull-Put/Iron-Condor/Covered-Call) that will consume signals from this repo when edge is proven.

---

## Disclaimer
Educational and research purposes only. Options trading involves significant risk.
Past flow patterns do not guarantee future price movements. Not financial advice.
