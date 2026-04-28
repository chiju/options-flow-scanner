# Options Flow Scanner 📊

> Institutional options flow intelligence + automated paper trading.
> Tracks smart money across 47 symbols every 15 minutes. Executes bull put spreads on confirmed signals.

**Goal:** Flow + news + GEX + price trend → confluence → automated spread selling → measured edge

---

## What This System Actually Does

**Step by step, every 15 minutes during market hours:**

```
1. Fetch options chain for 47 symbols (Schwab API → real Greeks/OI, or Alpaca fallback)
2. Filter: premium > $25K or sweep > 500 contracts
3. Score each contract 1-10 (premium size + sweep + delta + theta + vol baseline)
4. Detect: Golden Flow (sweep + score≥8 + $1M+) → Telegram alert
5. Detect: ⭐⭐⭐ confluence (flow + FinBERT news + GEX all agree) → Telegram alert
6. Write to Google Sheets: UNUSUAL_ALERTS, SYMBOL_TRACKER, SIGNAL_HISTORY
```

**Every day at market close:**
```
7. OI Tracker: fetch real OI per strike, calculate day-over-day change
   → "Long Buildup / Short Buildup" signals
8. Gamma Levels: calculate Max Pain, Call Wall, Put Wall, GEX per symbol
9. Signal Outcomes: did last week's signals predict price moves?
10. flow_trader: execute/exit bull put spreads on CSP paper account
```

**Every morning:**
```
11. Morning Brief: Finnhub macro news + FinBERT sentiment + Reddit + GEX regime
    → AI (Gemini) synthesizes into actionable brief → Telegram
```

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        DATA SOURCES                             │
│  Schwab API (primary)   Alpaca (fallback)   Finnhub   Reddit   │
│  Real Greeks/OI/prices  Options chain       Macro news  WSB    │
└────────────┬───────────────┬──────────┬────────┬───────────────┘
             │               │          │        │
             ▼               ▼          ▼        ▼
┌─────────────────────────────────────────────────────────────────┐
│                     EVERY 15 MINUTES                            │
│                                                                 │
│  options_flow_scanner.py                                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Score 1-10   │  │ Vol Baseline │  │  Confluence Engine   │  │
│  │ premium      │  │ vs 30d avg   │  │  flow + news + GEX   │  │
│  │ sweep        │  │ 3x → +1pt    │  │  ⭐⭐⭐ = alert       │  │
│  │ delta (ATM)  │  │ 10x → +3pts  │  │                      │  │
│  │ theta decay  │  │              │  │                      │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
│         └─────────────────┴──────────────────────┘             │
│                            │                                    │
│              ┌─────────────▼──────────────┐                    │
│              │     Golden Flow?            │                    │
│              │  sweep + score≥8 + $1M+    │                    │
│              └──────┬──────────┬──────────┘                    │
│                     │          │                                │
│              ┌──────▼──┐  ┌───▼──────────────────────┐        │
│              │Telegram │  │  flow_trader.py           │        │
│              │ Alert   │  │  3-sweep rule → execute   │        │
│              └─────────┘  │  bull put spread          │        │
│                           │  CSP paper account        │        │
│                           └───────────────────────────┘        │
└─────────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      GOOGLE SHEETS                              │
│  UNUSUAL_ALERTS │ SYMBOL_TRACKER │ SIGNAL_HISTORY │ FLOW_TRADE  │
│  GAMMA_LEVELS   │ OI_SNAPSHOT    │ SIGNAL_OUTCOMES│ BRIEF_LOG   │
└─────────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    DAILY INTELLIGENCE                           │
│                                                                 │
│  Morning Brief (14:00 UTC)      EOD Bundle (22:30 UTC)         │
│  ┌─────────────────────────┐    ┌──────────────────────────┐   │
│  │ Finnhub macro news      │    │ OI tracker (yfinance)    │   │
│  │ FinBERT sentiment       │    │ Gamma levels (GEX)       │   │
│  │ Reddit WSB buzz         │    │ Signal outcomes          │   │
│  │ 3-day memory            │    │ Evening brief            │   │
│  │ GEX regime              │    │ flow_trader exits        │   │
│  │ Gemini AI → Telegram    │    └──────────────────────────┘   │
│  └─────────────────────────┘                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Two-Repo System

```
options-flow-scanner  ←  ANALYSIS + TRADING ENGINE (this repo)
alpaca-news-bot       ←  NEWS DIGEST + OTHER STRATEGIES
```

---

## Full Pipeline

```
cron-job.org
│
├── Every 15 min (Mon-Fri 16:00-23:45 Berlin)
│   └── SCAN (silent mode)
│       ├── Fetch options chain (47 symbols, Alpaca API)
│       ├── Score each contract 1-10
│       ├── Detect: Golden Flow, Net Premium, Sector Rotation
│       ├── Confluence: flow + news (FinBERT) + GEX
│       ├── Write → SYMBOL_TRACKER, UNUSUAL_ALERTS, SIGNAL_HISTORY
│       ├── Telegram ONLY if: Golden Flow OR ⭐⭐⭐ HIGH confluence
│       └── flow_trader: execute spread if 3-sweep signal confirmed
│
├── 14:00 UTC (8am ET / 16:00 Berlin) — Morning Brief
│   └── Reads last 18h + 3-day history + GEX + news + Reddit
│       Chain-of-Thought: price trend → filter hedges → score confluence
│       Gemini + Groq + Gemini verifier → Telegram
│
├── 15:00 UTC (9am ET) — Pre-market + market open reminder
│
├── 22:30 UTC (4:30pm ET / 00:30 Berlin) — EOD Bundle
│   ├── scan --afterhours
│   ├── daily-brief --evening
│   ├── oi-tracker (real OI per strike, yfinance)
│   ├── gamma-levels (Max Pain, Call Wall, Put Wall, GEX)
│   ├── signal-outcomes (was signal right? 1d/3d price check)
│   └── flow-trader (execute/exit spreads on CSP paper account)
│
└── 22:00 UTC Friday — Weekly Summary
```

---

## Flow Trader — Automated Paper Trading

Executes bull put spreads on the CSP paper account ($101K) when signals meet all criteria.

### Entry Gates (ALL must pass)

| Gate | Criteria | Why |
|------|---------|-----|
| Three-sweep rule | Same contract swept 3+ times | "One sweep is luck, three is conviction" (CBOE) |
| Score | ≥ 8 | Institutional size + urgency |
| Premium | ≥ $1M per sweep | Filters retail noise |
| Market open | Alpaca clock API | No bad fills |
| Dedup | Not already traded today | Prevents double execution |

### Trade Setup

```
Signal: BULLISH (call sweeps confirmed)
Action: SELL BULL PUT SPREAD
  Sell strike: 12% OTM (delta ~0.20)
  Buy strike:  $10 below sell strike
  DTE:         30 days
  Credit:      ~$3.50 per spread
  Max loss:    $650 per contract
  Position:    2% of account max
```

### Exit Rules (automated, every 15 min)

| Rule | Trigger | Action |
|------|---------|--------|
| 70% profit | Spread worth 30% of credit | Close → keep $245 |
| Stop loss | Spread worth 2.5× credit | Close → limit loss |
| Near expiry | 7 days to expiry | Close → avoid gamma |

### This Week's Results (Apr 14-17)

| Signal | Sweeps | Action | Outcome |
|--------|--------|--------|---------|
| ARKK CALL $71 Apr17 | 18x | Sell $70/$60 put spread | ARKK +10.1% ✅ |
| AMZN CALL $205 Apr20 | 10x | Sell $220/$210 put spread | AMZN +4.4% ✅ |

## Live Trading Strategy (Starting May 2026)

### Capital Split (€2,000/month salary investment)
```
€1,200 → Long-term stocks (buy & hold 3-5 years)
€800   → Options trading capital (Alpaca live account)
```

### Decision Tree
```
Scanner signal → Strategy

SPY/QQQ BULLISH  → Bull Put Spread on SPY
SPY/QQQ BEARISH  → Bear Call Spread on SPY
SPY/QQQ SIDEWAYS → Iron Condor on SPY/QQQ

Stock BULLISH    → Bull Put Spread on that stock
Stock BEARISH    → Bear Call Spread on that stock
```

### Trading Symbol — SPY only (live account)

**Why SPY only:**
- No single stock risk — 500 companies, one crash doesn't matter
- No earnings risk, no CEO scandal, no product failure
- Most liquid options in the world (penny spreads, always fills)
- Learn one underlying deeply before expanding
- Pattern recognition comes from repetition on one instrument

**Expansion path (only after consistent profitability):**
```
Month 1-6:  SPY only
Month 7-12: Add QQQ (same mechanics, tech-heavy)
Year 2+:    Maybe 1 individual stock
```

Paper account runs multiple stocks for data — live money = SPY only.

### Position Sizing Rules
```
Max risk per trade:  10% of total trading capital
Max open at once:    30% of total trading capital
Close at:            50% of max profit (→ 81% win rate per Tastytrade research)
```

### Capital Growth Plan
```
Month 1: $870  → 1 trade (SOFI), $200 at risk
Month 2: $1,670 → 2 trades, $500 at risk
Month 3: $2,470 → 3 trades, $1,000 at risk
Month 6: $4,870 → 5 trades (full system)
Expected monthly income at full size: $150-300
```

### Emergency Rule
```
If market drops 20%+:
  → Pause options trading
  → Redirect €800 to averaging NVDA/AMZN/CRWV only
  → Resume options next month (higher IV = better premiums anyway)
```

### Why Probability > Risk/Reward
```
Bull put spread looks bad on paper:
  Max profit $150 vs max loss $350 → R/R = 1:2.3 (unfavorable)

But probability makes it work:
  Sell 30-delta put → 70% probability of profit
  Manage at 50% profit → 81% actual win rate (Tastytrade research)
  IV overpricing gives sellers structural edge

91% of options BUYERS lose money (SEBI 2024 study)
Sellers are on the other side of that trade.
```

---

```
Layer 1: FLOW      → Sweep detected (15-min scan)
Layer 2: PREMIUM   → Score 1-10 (size + sweep + IV + DTE)
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

## Google Sheets

| Tab | Purpose | Updated |
|-----|---------|---------|
| `SYMBOL_TRACKER` | symbol, name, type, interpretation, P/C, net premium, price, price_chg | Every 15 min |
| `UNUSUAL_ALERTS` | All $5M+ flows or sweeps with score, IV rank | Every 15 min |
| `SIGNAL_HISTORY` | Signal flips, sweep≥8, 3-day persistence | Every 15 min |
| `OI_SNAPSHOT` | Real OI per strike (yfinance), significant changes only | EOD |
| `GAMMA_LEVELS` | Max Pain, Call Wall, Put Wall, GEX per symbol/expiry | EOD |
| `SIGNAL_OUTCOMES` | Was signal right? 1d/3d price + OI confirmation | EOD |
| `FLOW_TRADE_LOG` | Every trade executed by flow_trader | EOD/scan |
| `BRIEF_LOG` | AI brief history | 2x daily |
| `MY_HOLDINGS` | Your portfolio with cost basis | Manual update |

### SYMBOL_TRACKER Interpretation Column

```
🛡️ Hedging    = puts high BUT price rising → protecting longs (not bearish)
😨 Fear        = puts high AND price falling → real bearish conviction
🔥 Greed       = calls high AND price rising → pure bullish
⚠️ Complacency = calls high BUT price falling → ignoring risk
🟢 Call bias   = neutral P/C but call $ dominates
🔴 Put bias    = neutral P/C but put $ dominates
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
| +1 | IV rank low (buying cheap options) |
| +3 | Volume ≥ 10× 30-day average (informed trading signal) |
| +2 | Volume ≥ 5× 30-day average |
| +1 | Volume ≥ 3× 30-day average |

**Golden Flow** = sweep + score≥8 + premium≥$1M → Telegram alert

---

## Volume Baseline (New — Apr 2026)

Every scan loads the last 30 days of UNUSUAL_ALERTS and computes average daily volume per symbol/type. Current volume is compared against this baseline:

```
Normal day:  MSFT calls avg 400 contracts
Today:       MSFT calls = 4,000 contracts → 10× baseline → +3 pts
```

This catches the exact moment informed money enters — the same methodology used by Unusual Whales and professional flow scanners.

---

## GEX (Gamma Exposure)

```
GEX = gamma × OI × 100 × spot²

Positive GEX → MMs stabilize price (low vol, mean-reverting)
Negative GEX → MMs amplify moves (trending, volatile)

SPY GEX -3.94M this week → moves amplified → Friday was volatile ✅
```

---

## Watchlist (47 Fixed + Dynamic)

| Group | Symbols |
|-------|---------|
| Index ETFs | SPY, QQQ, IWM |
| Mega cap | AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA |
| AI/Cloud | PLTR, CRWV, PATH, AI, SNOW |
| Semis | AMD, ARM, AVGO, QCOM, MU |
| Fintech | COIN, HOOD, SOFI, PYPL |
| High vol | AMD, COIN, MSTR, HOOD, SMCI, ARM, SNOW, ASTS, NBIS |
| Sector ETFs | XLK, XLF, XLE, GLD, TLT, ITA, USO, UUP, XBI |
| + Dynamic | Top 10 most active from Alpaca screener (changes daily) |
| Sector ETFs | XLK, XLF, XLE, XLV, GLD, TLT, ITA, USO, UUP, XBI, ARKK | ✅ ARKK 18x swept |
| Defence | LMT, RTX, NOC, GD | ⚪ Low |
| Cyber | CRWD, PANW, ZS | ❌ None |
| Mega Caps | AAPL, GOOGL, MSFT, NVDA, AMZN, META, TSLA | ✅ Active |
| High Vol | AMD, COIN, MSTR, HOOD, SMCI, ARM, SNOW | ✅ MSTR 3x swept |
| Portfolio | PLTR, CRWV, IONQ, OKLO, ACHR, DUOL, SOFI, PYPL, PATH, JOBY, UUUU, POET | ✅ IONQ +54.9% |
| Dynamic | Top 10 most active (Alpaca Screener) | varies |

---

## Schedule (cron-job.org → GitHub Actions)

| Job ID | Berlin time | ET | Input | Runs |
|--------|------------|-----|-------|------|
| 7485766 | 16:00–23:45 every 15min | 10am–5:45pm | `scan` | Silent scan + flow_trader |
| 7485841 | 14:00 Mon-Fri | 8:00am | `brief` | Morning AI brief |
| 7485847 | 15:00 Mon-Fri | 9:00am | `premarket` | Pre-market + market open alert |
| 7485848 | 22:30 Mon-Fri | 4:30pm | `eod` | EOD bundle (6 jobs) |
| 7485849 | 22:00 Fri | 4:00pm Fri | `weekly` | Weekly summary |
| 7502534 | 23:30 Mon-Fri | 5:30pm | `oi` | OI tracker (1h after close, OCC data ready) |
| 7502338 | 12:00 Sat+Sun | 8:00am | `digest` | Weekend news digest (alpaca-news-bot) |
| 7502340 | 12:00 Sat+Sun | 8:00am | `brief` | Weekend AI brief (macro/geopolitical focus) |

---

## Files

| File | Purpose |
|------|---------|
| `options_flow_scanner.py` | Main scanner: fetch, score, confluence, silent alert |
| `flow_trader.py` | **NEW**: automated bull put spread execution on confirmed signals |
| `sheets.py` | All Google Sheets read/write |
| `daily_brief.py` | 3-AI council: CoT prompts, FinBERT, Reddit, price trend, 3-day memory |
| `gamma_levels.py` | Max Pain, Call Wall, Put Wall, GEX time series |
| `oi_tracker.py` | Real OI per strike (yfinance), significant changes only |
| `signal_outcomes.py` | Signal accuracy: 1d/3d price + OI confirmation |
| `earnings.py` | Upcoming earnings (Yahoo Finance) |
| `weekly_summary.py` | Friday EOD digest |
| `notifier.py` | Telegram sender |
| `telegram_trigger.py` | Bot: /status /scan /brief /help |

---

## GitHub Secrets

```
ALPACA_API_KEY / ALPACA_SECRET_KEY   Options paper account (main)
ALPACA_CSP_API_KEY / SECRET          CSP paper account (flow_trader)
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
GOOGLE_CREDENTIALS / GOOGLE_OPTIONS_SHEET_ID
GOOGLE_AI_API / GOOGLE_AI_API_2      Gemini
GROQ_API_KEY                         Groq Llama
OPENROUTER_API_KEY                   OpenRouter
HF_TOKEN                             HuggingFace (FinBERT)
```

## Local Setup

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
cd ~/stocks/options-flow-scanner
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
source ~/.alpaca/options-paper.env

python options_flow_scanner.py --force   # full report
python flow_trader.py                    # check/execute trades
python daily_brief.py --morning          # morning brief
python gamma_levels.py                   # EOD gamma levels
python signal_outcomes.py                # EOD accuracy check
python oi_tracker.py                     # EOD OI snapshot
```

## News Sources

| Source | What | When | Free? |
|--------|------|------|-------|
| Alpaca/Benzinga | Stock-tagged news (FinBERT scored) | 5x/day + weekends | ✅ (with Alpaca) |
| Finnhub | Macro/geopolitical news (Hormuz, Fed, oil, war) | 24/7 including weekends | ✅ free tier |
| Reddit | WSB/stocks/investing buzz | In daily brief | ✅ no key needed |

Finnhub catches untagged macro events that Alpaca misses (e.g. "Strait of Hormuz blocked").
Bearish keywords (blocked/attack/missile/war) override FinBERT scoring for accuracy.

---

## Weekend Brief

Runs Saturday and Sunday at 12:00 Berlin via cron-job.org.

Uses `WEEKEND_INSTRUCTION` — different from weekday:
- **Macro news overrides stale flow signals** (Hormuz closed = bearish, even if Friday had call sweeps)
- Focuses on: weekend events → Friday OI positioning → earnings this week → Monday setup
- Finnhub provides 24/7 news coverage for weekends

---

## Telegram Recipients

Alerts sent to multiple recipients via `notifier.py`:
- Primary: `TELEGRAM_CHAT_ID` (your personal chat)
- Extra: `TELEGRAM_EXTRA_CHAT_IDS` (comma-separated, e.g. secondary chat)

All send functions (scanner, brief, P&L report, strategies) use `notifier.py`.

---

The system is designed to use the right strategy for each signal type:

```
Signal quality → Strategy selection

⭐⭐⭐ HIGH confluence + 3-sweep + OI confirmed
  + GEX negative (amplified move)
  + Low IV rank (cheap options)
  → BUY the option (asymmetric, 3-5× return)
  → Size: 0.5% of account

⭐⭐ Medium confluence + 3-sweep
  + Stock in uptrend
  → SELL put spread (high probability income)  ← CURRENT
  → Size: 2% of account

⭐ Low confluence / single sweep
  → SKIP

Persistent (same contract 5+ days, consolidating)
  → SELL put spread closer to money
  → Size: 1% of account

Capitulation flip (weeks of puts → sudden calls)
  → BUY calls aggressively
  → Size: 1% of account
```

**Data already collected to support this:**
- Confluence score ✅ | Sweep count ✅ | IV rank ✅
- GEX regime ✅ | Price trend ✅ | OI confirmation ✅

**Missing for full adaptive system:**
- 20/50 day MA (price trend direction)
- 30 days of IV rank history
- Capitulation flip detection

---

## Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| 1 | ✅ Done | Data collection, signal detection, AI brief |
| 2 | 🔄 Now | Validate edge — 6-8 weeks of SIGNAL_OUTCOMES data |
| 3 | 📅 Later | Add buying options on ⭐⭐⭐ signals (real-time websocket) |
| 4 | 📅 Later | Live account trading after paper validation |

---

## Related
[alpaca-news-bot](https://github.com/chiju/alpaca-news-bot) — News digest + Wheel/CSP/Iron-Condor/Bull-Put paper strategies.

---

## Honest Assessment: What We Do vs Professional Tools

### What we do well ✅
| Feature | Our system | Professional ($75-200/mo) |
|---------|-----------|--------------------------|
| Options flow scanning | ✅ Every 15 min | ✅ Real-time (every second) |
| Sweep detection | ✅ 500+ contracts | ✅ Multi-exchange detection |
| GEX calculation | ✅ Real gamma (Schwab) | ✅ Same |
| Greeks | ✅ Real (Schwab) | ✅ Same |
| News sentiment | ✅ FinBERT + Finnhub | ✅ Similar |
| AI brief | ✅ Gemini daily | ✅ Similar |
| Paper trading | ✅ Automated | ❌ Manual |
| Cost | ✅ Free | ❌ $75-200/mo |

### What we're missing ❌
| Feature | Gap | Impact |
|---------|-----|--------|
| Real-time (every second) | We scan every 15 min | Miss fast moves |
| Dark pool prints | Not available free | Miss off-exchange accumulation |
| Opening vs closing tag | Can't detect from chain | Misread some signals |
| Multi-exchange sweep detection | Single chain snapshot | Miss urgency signal |

### The honest truth
**Simple is better.** Professional traders who use Unusual Whales ($75/mo) still lose money
because they chase every signal. Our system's edge is:
1. **Confluence** — only alert when flow + news + GEX all agree
2. **Baseline comparison** — only flag when volume is 3x+ above normal
3. **Automated execution** — no emotion, consistent position sizing
4. **Signal outcomes tracking** — we know which signals actually worked

The 15-minute delay vs real-time is a real gap for day trading but irrelevant for
swing trades (3-30 day holds) which is our focus.

---

## Schwab Integration (Apr 2026)

Schwab API provides real-time OPRA data free with a brokerage account:
- **Real Greeks** (delta, gamma, theta, vega) — not estimated
- **Real OI** — was always 0 with Alpaca
- **Real-time prices** — replaces delayed yfinance
- **Token persistence** — stored in Google Sheets, auto-refreshes

To re-authenticate (needed every ~30 days):
```bash
cd ~/stocks/options-flow-scanner
source ~/.alpaca/options-paper.env
source .venv/bin/activate
python schwab_cli.py auth
python schwab_token_store.py save
```

---

## Disclaimer
Educational and research purposes only. Options trading involves significant risk.
Past flow patterns do not guarantee future price movements. Not financial advice.
