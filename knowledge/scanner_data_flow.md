# Scanner Data Flow — How Alerts Are Logged and Compared

## The Full Pipeline

```
Schwab API (real-time, every 15 min)
  ↓ 38,880 contracts checked (54 symbols × 720 contracts each)
  ↓ Filter: premium ≥ $25K
Calculate per contract:
  premium, sweep, delta, IV rank, vol/OI, ascending vol, score
  ↓
Score ≥ 7 → UNUSUAL_ALERTS sheet (always logged)
Score ≥ 9 + not seen in 4h → SIGNAL_HISTORY + Telegram alert
  ↓
flow_trader reads SIGNAL_HISTORY → finds 3+ sweeps → executes trade
```

---

## Sheet Roles

| Sheet | What it stores | Used for |
|-------|---------------|---------|
| `UNUSUAL_ALERTS` | Every contract with score ≥7, every scan | Raw database, baseline comparison |
| `SIGNAL_HISTORY` | Score ≥9 alerts (deduped, 4h window) | Golden Flow dedup, flow_trader signals |
| `FLOW_TRADE_LOG_15K` | Executed + skipped trades | Trade tracking, dedup |
| `TRADE_RESULTS` | Closed trade P&L | Performance tracking |

---

## Unusual Alert Logic

### Step 1: Minimum premium filter
```python
premium = mid_price × volume × 100  # total notional
if premium < $25,000: skip           # eliminates retail noise
```

### Step 2: Metrics calculated per contract
```python
vol_oi_ratio    = volume / open_interest
                # 10x = 10x more contracts traded than exist = very unusual

iv_spike        = iv% > 80% AND it's a CALL
                # high IV on calls = someone paying up urgently

buy_sell        = "BUY"  if last_price >= mid  # hitting ask = urgent buyer
buy_sell        = "SELL" if last_price < mid   # hitting bid = urgent seller

vol_vs_baseline = today_volume / avg_30day_volume_for_this_symbol
                # 10x = 10x more than normal for THIS specific stock

ascending_vol   = "strong" if same contract volume grew in 3+ consecutive scans
ascending_vol   = "weak"   if grew in 2 scans
                # same bet being added to repeatedly = accumulation
```

### Step 3: Sweep detection
```python
sweep = (mid × volume × 100) >= $1,000,000 AND it's a CALL
```
A sweep = single contract with $1M+ notional. Institutional size filter.
Note: We use notional threshold as proxy. Professional tools also check
multi-exchange routing and sub-second fill time (requires paid data feed).

### Step 4: Scoring (0-10)
```
Premium:          $20M+ → +5 | $10M+ → +4 | $5M+ → +3 | $1M+ → +2 | $100K+ → +1
Sweep ($1M+):     +2
IV rank High:     +2 (calls only — buying urgency)
IV rank Low:      +1 (cheap options = better edge for buyers)
Ascending vol:    strong +3 | weak +1
Vol/OI ratio:     10x+ → +2 | 5x+ → +1
Vol vs baseline:  10x+ → +3 | 5x+ → +2 | 3x+ → +1
DTE 0-7 days:     +2 (urgency — expires this week)
DTE 8-30 days:    +1
OTM delta <0.4:   +1 (directional bet, not hedge)
ATM delta 0.35-0.65: +2 (pure directional conviction)
Deep ITM cap:     delta > 0.85 → max score 4 (it's a hedge, not signal)
Theta high (puts): +1 (good spread selling opportunity)
```

### Step 5: Logging
```
Score ≥ 7 → logged to UNUSUAL_ALERTS (always, every scan)
Score ≥ 9 → also logged to SIGNAL_HISTORY + Telegram alert
Score ≤ 6 → silent (discarded)
```

---

## Three Comparison Mechanisms

### 1. Volume baseline (30-day history)
```python
_alerts_30d = last 30 days from UNUSUAL_ALERTS sheet
baseline = avg daily volume for this symbol from those rows
vol_vs_baseline = today_volume / baseline
```
Answers: "Is today's volume unusual vs this stock's own history?"

### 2. Ascending volume (same contract across scans)
```python
# Checks if SAME contract (sym+strike+expiry) appeared in previous scans
# and if volume is growing each time
ascending_vol = "strong" if 3+ scans with growing volume
```
Answers: "Is the same institutional bet being added to repeatedly?"

### 3. Golden Flow dedup (SIGNAL_HISTORY)
```python
filter_new_golden_flow() → reads SIGNAL_HISTORY sheet
# Only alerts if this exact contract hasn't been alerted in last 4 hours
```
Answers: "Have we already told you about this contract today?"

---

## What Makes a Score 10 Signal

Example: NVDA $180C May 29 — $24M premium, sweep, ascending vol, ATM delta
```
$24M premium:    +5
Sweep:           +2
Ascending vol:   +3 (strong — same contract growing for 3+ scans)
ATM delta 0.5:   +2
Total:           12 → capped at 10
```

Score 10 signals are rare (1-2 per week) because they need multiple
independent factors all pointing the same direction simultaneously.

---

## Telegram Alert Types

| Alert | Trigger | Sheet logged |
|-------|---------|-------------|
| 🚨 High Conviction / Golden Flow | Score ≥9, new in 4h | SIGNAL_HISTORY |
| 🔍 Divergence Warning | Stock up 5%+ but calls SOLD | SIGNAL_HISTORY |
| 💰 Flow-15K Trade | Spread executed | FLOW_TRADE_LOG_15K |
| ⏭️ Signals Blocked (Earnings) | Score ≥9 but earnings within 30d | FLOW_TRADE_LOG_15K |
| ⚠️ No Strike Found | Score ≥9 but price lookup failed | FLOW_TRADE_LOG_15K |
