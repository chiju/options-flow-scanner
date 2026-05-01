# Flow Trader — Code Explained Line by Line

## Overview

`flow_trader.py` runs every 15 minutes via GitHub Actions. It reads signals from Google Sheets, applies filters, and executes bull put spreads on Alpaca paper account.

---

## Section 1: Configuration

```python
DRY_RUN = False
```
`True` = logs what it WOULD do, no real orders. `False` = live paper trading.

```python
USE_10K_ACCOUNT = os.environ.get("FLOW_TRADER_10K", "false").lower() == "true"
```
GitHub Actions sets `FLOW_TRADER_10K=true` for the $15K account run. Otherwise uses $101K CSP account.

```python
TRADEABLE_SYMBOLS = {"SPY","QQQ","AAPL",...}  # 17 liquid symbols
```
$15K account only trades these. $101K account has no filter.

```python
PAPER_BASE = "https://paper-api.alpaca.markets"
TRADE_LOG_TAB = "FLOW_TRADE_LOG_15K" if USE_10K_ACCOUNT else "FLOW_TRADE_LOG"
```
Separate sheet tabs per account so logs don't mix.

---

## Section 2: `get_confirmed_signals()` — Signal Detection

Reads SIGNAL_HISTORY sheet (last 2 days) and finds signals passing ALL filters.

**Step 1: Read sweeps**
```python
sigs = [row for row in rows if "SWEEP" in row[1] and row[0] >= cutoff]
```
Gets all sweep events from last 2 days.

**Step 2: Count sweeps per contract**
```python
contract_sweeps = Counter()
key = f"{sym}|{detail_norm}"
contract_sweeps[key] += 1
```
Groups by symbol+contract. NVDA $180C appearing 5 times = 5 sweeps.

**Step 3: Four hard filters**
```python
if count < 3: continue          # Three-sweep rule (conviction)
if score < 9: continue          # Score ≥9 (top 0.03% of signals)
if premium_k < 1000: continue   # $1M+ premium (institutional size)
if sym not in TRADEABLE_SYMBOLS: continue  # liquid symbols only
```
These eliminate ~99.97% of all signals. Only 1-2 per week pass.

**Step 4: Confluence scoring**
```python
confluence_pts = count          # sweep count = core signal
if gex_ok: confluence_pts += 1  # negative GEX = moves amplified
if news_ok: confluence_pts += 1 # news agrees with direction
if iv_rank == "High": confluence_pts += 2  # high IV = fat premium
```
Higher confluence = more conviction. Signals sorted by this.

---

## Section 3: `_execute_spread()` — Order Execution

**OCC symbol format:**
```python
def occ(sym, exp, cp, strike):
    return f"{sym}{exp}{cp}{strike_str}"
# Example: AAPL260117P00150000 = AAPL, Jan 17 2026, Put, $150 strike
```

**Two orders per spread:**
```python
r1 = requests.post(BASE/orders, {"symbol": sell_sym, "side": "sell"})  # collect premium
r2 = requests.post(BASE/orders, {"symbol": buy_sym,  "side": "buy"})   # protection
```
Sell higher strike put (collect premium), buy lower strike put (cap max loss at $10).

---

## Section 4: `check_exits()` — Position Management

**Pair spread legs by (underlying + expiry):**
```python
spreads = {}
key = (underlying, exp_str)
if qty < 0: spreads[key]["short"] = p  # our sell leg
else:       spreads[key]["long"]  = p  # our protective leg
```

**Real spread P&L (both legs):**
```python
net_credit  = short_entry - long_entry      # what we collected originally
spread_cost = short_current - long_current  # what it costs to close now
spread_pl_pct = (net_credit - spread_cost) / net_credit
```
Example: net_credit=$2.00, spread_cost=$1.00 → 50% profit → CLOSE.

**Three exit conditions:**
```python
if spread_pl_pct >= 0.50:  reason = "50% profit"   # Tastytrade: 81% win rate
elif spread_pl_pct <= -2.0: reason = "Stop loss"    # 2x credit = stop
elif dte <= 7:              reason = "Near expiry"  # gamma risk too high
```

---

## Section 5: `find_spread_strike()` — Strike Calculation

**Uses LIVE Alpaca key for market data** (paper key doesn't work for data API):
```python
live_key = os.environ.get("ALPACA_LIVE_API_KEY", PAPER_API_KEY)
r = requests.get("https://data.alpaca.markets/v2/stocks/{sym}/quotes/latest", ...)
price = (bid + ask) / 2
```

**Strike calculation:**
```python
sell_strike = round(price * 0.88 / 5) * 5  # 12% OTM, rounded to $5
buy_strike  = sell_strike - 10              # $10 wide spread
```
Example: TSLA at $389 → sell_strike=$340, buy_strike=$330.

---

## Section 6: `run_flow_trader()` — Main Flow

Every 15 minutes, in this exact order:

```
1. Market clock check (Alpaca /v2/clock)
   → Market CLOSED: run exits only, skip entry
   → Market OPEN: proceed

2. check_exits() — always runs
   → 50% profit? → close both legs
   → 2x loss? → close both legs
   → 7 DTE? → close both legs
   → Log to TRADE_RESULTS sheet

3. get_confirmed_signals() — reads SIGNAL_HISTORY sheet
   → 3+ sweeps + score≥9 + $1M+ premium + liquid symbol

4. Earnings filter (live yfinance)
   → earnings_date < today + 30 days → SKIP
   → Telegram: "⏭️ AMD BULLISH | Score:10 | $8500K | Earnings:2026-05-05"
   → Log to FLOW_TRADE_LOG_15K as SKIPPED

5. Capital check (live Alpaca account)
   → deployed ≥ 30% of account → STOP
   → MAX_RISK_PER_TRADE = account_value × 5% (dynamic)

6. Dedup check
   → Already traded today? (sheet log)
   → Already have open position? (Alpaca positions)
   → Same symbol twice in this batch? (intra-batch dedup)

7. Execute (max 3 per run)
   → find_spread_strike() → get current price → calculate OTM strike
   → _execute_spread() → 2 orders (buy protective leg, sell short leg)
   → If failed → remove from log, no notification

8. Log + Notify
   → SUBMITTED: log to sheet + Telegram "💰 Flow-15K Trade"
   → SKIPPED: log to sheet + Telegram "⚠️ No Strike Found"
```

---

## Key Design Decisions

| Decision | Why |
|----------|-----|
| Buy protective leg first | Prevents naked short if sell order fails |
| Score ≥9 threshold | Only top 0.03% of signals (1-2/week) |
| 30% capital limit | Max 4-5 spreads open at once |
| Earnings blocked | Binary events can gap through strikes |
| 50% profit target | Tastytrade research: 81% win rate |
| Monthly expiry (21-45 DTE) | Best theta/gamma ratio, 78% win rate |
| Live key for data API | Paper keys only work for execution |
| Sheet-based dedup | Persists across GitHub Actions runs |

---

## Data Flow

```
Scanner (every 15 min)
  → Schwab API (38,880 contracts checked)
  → Score each contract
  → Score ≥7: log to UNUSUAL_ALERTS sheet
  → Score ≥9: log to SIGNAL_HISTORY + Telegram alert

Flow Trader (every 15 min)
  → Read SIGNAL_HISTORY (last 2 days)
  → Count sweeps per contract
  → Apply 4 filters
  → Apply earnings/capital/dedup filters
  → Execute spread on Alpaca paper
  → Log to FLOW_TRADE_LOG_15K
  → Log closed trades to TRADE_RESULTS
```

---

## Telegram Alert Types

| Alert | When |
|-------|------|
| `💰 Flow-15K Trade` | Trade successfully executed |
| `⏭️ Signals Blocked (Earnings)` | Signal passed but earnings within 30 days |
| `⚠️ Signal — No Strike Found` | Signal passed but couldn't get price/contract |
