# Flow Trader — Execution Flow

Runs every 15 minutes via cron-job.org → GitHub Actions.

```
Every 15 minutes
│
├── 1. LOAD SIGNALS (Google Sheets → UNUSUAL_ALERTS tab)
│      Look back 2 days for high-score alerts
│      Filter: score ≥ 9, option_type = CALL (bullish only)
│      Filter: premium ≥ $1M, volume ≥ 3x baseline
│
├── 2. EARNINGS FILTER (live yfinance check)
│      For each signal symbol → check earnings date
│      If earnings < today + 30 days → SKIP (binary event risk)
│      ETFs (SPY/QQQ) → no earnings → always OK
│
├── 3. CAPITAL CHECK (live Alpaca account)
│      Fetch current account value (e.g. $15,000)
│      Sum all open SHORT positions market value
│      If deployed ≥ 30% ($4,500) → STOP, no new trades
│      Update MAX_RISK_PER_TRADE = account_value × 5% (dynamic)
│
├── 4. DEDUP CHECK
│      Already traded today? (check FLOW_TRADE_LOG_15K sheet)
│      Already have open position in this symbol? (check Alpaca positions)
│      If yes → SKIP
│
├── 5. FIND STRIKE (Alpaca quote → calculate OTM strike)
│      Get current price via Alpaca IEX feed
│      Sell strike = price × 88% (12% OTM), rounded to $5
│      Buy strike = sell strike - $10 (protective leg)
│      Find actual Alpaca options contract matching strike + 21-45 DTE
│
├── 6. EXECUTE SPREAD (2 orders)
│      Order 1: BUY protective leg first (long put, lower strike)
│               → prevents naked short if sell order fails
│      Order 2: SELL short leg (short put, higher strike)
│               → collect premium
│      Both: limit orders at mid price
│
├── 7. LOG TO SHEETS (FLOW_TRADE_LOG_15K tab)
│      date, symbol, strikes, credit, max_loss, score, expiry
│
├── 8. TELEGRAM NOTIFICATION
│      Sent to both recipients:
│      "🎯 Flow-15K: SOLD TSLA $355/$345P spread for $2.10 credit"
│
└── 9. MANAGE EXISTING POSITIONS (runs every time regardless of new signals)
       For each open spread:
       ├── Profit ≥ 50%? → CLOSE (buy back spread) — 81% win rate
       ├── Loss ≥ 2x credit? → CLOSE (stop loss)
       ├── DTE ≤ 7 days? → CLOSE (gamma risk too high)
       └── Otherwise → HOLD
```

## Key Design Decisions

**Buy protective leg first** — prevents naked short put if the sell order fails.
A naked short put has unlimited downside; the long put caps it at $10 max loss.

**Score ≥ 9 threshold** — only institutional-grade signals fire a trade.
In practice this means 1-2 trades per week, not every scan.

**30% capital limit** — max 4-5 spreads open at once on a $15K account.
Prevents overexposure if multiple positions move against us simultaneously.

**Earnings blocked** — no trades if earnings fall within the 30-day expiry window.
Earnings = binary event. IV looks attractive but actual gap risk is much higher.

**50% profit target** — Tastytrade research: closing at 50% profit achieves
81% win rate vs 67% if held to expiration. Takes theta off the table early.

**Dynamic risk sizing** — MAX_RISK_PER_TRADE = 5% of live account value.
As account grows from $15K → $20K → $30K, position size scales automatically.

## Accounts

| Account | Purpose | Max risk/trade |
|---------|---------|----------------|
| Flow-15K (PA3KU3B4THVE) | Realistic constraints, 11 liquid symbols | 5% = $750 |
| CSP/FlowTrader ($101K) | All 54 symbols, higher capacity | 2% = $2,000 |
