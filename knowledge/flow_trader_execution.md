# Flow Trader — Execution Flow

Runs every 15 minutes via cron-job.org → GitHub Actions.

```
Every 15 minutes
│
├── 1. MARKET CLOCK CHECK (Alpaca /v2/clock)
│      Market CLOSED → run exit checks only, skip all entry logic
│      Market OPEN → proceed with full logic
│      (prevents pre-market/after-hours phantom trades)
│
├── 2. LOAD SIGNALS (Google Sheets → UNUSUAL_ALERTS tab)
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
│      Credit price: real bid/ask mid from Alpaca (not hardcoded estimate)
│      Order 1: BUY protective leg first (long put, lower strike)
│               → prevents naked short if sell order fails
│      Order 2: SELL short leg (short put, higher strike)
│               → collect premium
│      On failure: removed from log, no Telegram notification sent
│      Intra-batch dedup: same symbol only trades once per 15-min run
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
       Pairs BOTH legs by (underlying + expiry) to calculate real spread P&L:
         net_credit  = short_entry - long_entry   (what we collected)
         spread_cost = short_current - long_current (cost to close now)
         spread_pl   = net_credit - spread_cost
       ├── Spread profit ≥ 50%? → CLOSE both legs (81% win rate)
       ├── Spread loss ≥ 2x credit? → CLOSE both legs (stop loss)
       ├── DTE ≤ 7 days? → CLOSE both legs (gamma risk too high)
       └── Otherwise → HOLD
       On close → log to TRADE_RESULTS sheet:
         date, symbol, spread ($330/$320P), net_credit, close_cost, pl%, pl$, reason, WIN/LOSS
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

---

## Signal DTE vs Trade DTE — Why They're Different

**We detect signals from ANY expiry (0-45 DTE) but only trade monthly (21-45 DTE).**

This is the standard professional approach, confirmed by TradeAlgo, Tastytrade, and Reddit r/options.

### Why signals come from weekly expiry

Institutional traders often use weekly options for:
- Directional bets (0-7 DTE = maximum leverage, cheap premium)
- Earnings plays (expire just after the event)
- Intraday hedging

When we see a $8M sweep on TSLA $385C expiring TODAY, it means:
*"Smart money is bullish on TSLA right now"* — the signal is about **direction**, not the specific contract.

### Why we trade monthly (21-45 DTE)

From TradeAlgo research (March 2026) and Tastytrade backtesting:

| | Weekly (7 DTE) | Monthly (45 DTE) |
|--|----------------|-----------------|
| Win rate (20-delta) | ~71% | ~78% |
| Theta/gamma ratio | Poor | **Best** |
| Recovery time if wrong | None | 3-5 weeks |
| Annual commissions (same capital) | $270 | $62 |
| Management time | 4-6 hrs/week | 1-2 hrs/week |

**Key finding:** Monthly options at 45 DTE deliver **15× the expected value per trade** vs weeklies (Tastytrade Market Measures research).

### The logic

```
Weekly signal (0-DTE TSLA call sweep):
  → Tells us: institutions are bullish on TSLA
  → We use this as directional confirmation

Monthly trade (TSLA $335/$325P, 30 DTE):
  → We sell a put spread 12% below current price
  → Theta decays in our favor every day
  → 30 days for the stock to stay above our strike
  → Close at 50% profit (~15 days in)
```

The weekly signal gives us **conviction on direction**.
The monthly spread gives us **time + theta** to profit from that direction safely.

### When weekly signals are NOT actionable for us

- 0-DTE signals → informational only, we don't trade same-day
- Earnings within 30 days → blocked regardless of signal DTE
- Score < 9 → filtered out regardless of DTE
