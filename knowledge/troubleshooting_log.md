# Troubleshooting Log

All bugs encountered, root causes, and fixes. Most recent first.

---

## 2026-05-02 — Divergence warning spamming every 15 minutes

**Symptom:** Same MSTR/NBIS divergence warning firing every 15 min all day.

**Root cause:** No dedup on divergence alerts. Every scan run re-checks and re-fires if conditions still met.

**Fix:** Check SIGNAL_HISTORY sheet for today's DIVERGENCE entries before sending. Log each new divergence to SIGNAL_HISTORY so it's not re-sent.

**Commit:** `1765309`

---

## 2026-05-01 — GitHub secret ALPACA_CSP_API_KEY stale (401 on orders)

**Symptom:** CSP/FlowTrader account getting 401 "unauthorized" on every order attempt despite local key working fine.

**Root cause:** GitHub secret `ALPACA_CSP_API_KEY` was outdated — different from the local env file value. GitHub Actions uses the secret, not the local file.

**Fix:** Re-sync GitHub secrets from local env:
```bash
source ~/.alpaca/options-paper.env
gh secret set ALPACA_CSP_API_KEY --body "$ALPACA_CSP_API_KEY" --repo chiju/options-flow-scanner
gh secret set ALPACA_CSP_SECRET_KEY --body "$ALPACA_CSP_SECRET_KEY" --repo chiju/options-flow-scanner
```

**Rule:** When updating keys locally, always update GitHub secrets too. Local env ≠ GitHub secrets.

---

## 2026-05-01 — Spread width too wide for cheap stocks

**Symptom:** SOFI $15/$5P spread — $5P filled at $2.13 (should be ~$0.01). Net debit instead of credit.

**Root cause:** `buy_strike = sell_strike - 10` always used $10 width regardless of stock price. For SOFI at $16, a $5P is 69% below stock price — no real market, paper fills at stale price.

**Fix:** Spread width now scales with stock price (10% of price, min $2, max $10):
```python
spread_width = min(10, max(2, round(price * 0.10 / 2) * 2))
```
SOFI $16 → $2 wide ($14/$12P). SPY $720 → $10 wide ($630/$620P).

**Commit:** `8f8e6c5` | **Journal:** `journal/2026-05-01/SOFI_TRADE_journal.md`

---

## 2026-05-01 — Earnings blocked notification spamming every 15 min

**Symptom:** Same "Signals Blocked (Earnings)" Telegram alert firing every 15 minutes for AMD/NVDA/PLTR.

**Root cause:** `get_confirmed_signals()` looks back 2 days. Old signals keep re-triggering the earnings filter every run. No dedup on the notification.

**Fix:** Check `FLOW_TRADE_LOG_15K` sheet for today's already-logged symbols before sending notification. Only notify for new symbols.

**Commit:** `ffa7fa5`

---

## 2026-05-01 — Paper key used for market data API

**Symptom:** META had score 10 signal (44 sweeps, $6.1M) but trade never executed. Log: `Strike error META: Expecting value: line 1 column 1 (char 0)`

**Root cause:** `find_spread_strike()` used `PAPER_API_KEY` to call `data.alpaca.markets`. Paper keys only work for `paper-api.alpaca.markets` (execution). Market data API requires live key → empty response → JSON parse error.

**Fix:** Use `ALPACA_LIVE_API_KEY` for market data, `PAPER_API_KEY` only for order execution.
```python
live_key = os.environ.get("ALPACA_LIVE_API_KEY", PAPER_API_KEY)
r = requests.get("https://data.alpaca.markets/v2/stocks/{sym}/quotes/latest",
    headers={"APCA-API-KEY-ID": live_key, ...})
```

**Commit:** `486def0` | **Impact:** META trade missed — $6.1M signal not executed

---

## 2026-05-01 — Pre-market phantom trade notifications

**Symptom:** Telegram received trade notifications at 10:38 UTC (pre-market) for MSFT, SPY, QQQ, AAPL, AMZN, TSLA. Account showed $15,000 clean with 0 positions.

**Root cause:** Notification fired based on `trade_rows` being populated, not on actual order execution. Orders sent during pre-market → rejected by Alpaca → but notification still fired.

**Fix:** Added Alpaca market clock check as FIRST step. If market closed → skip entry logic entirely.
```python
clock = requests.get(f"{PAPER_BASE}/v2/clock", ...).json()
if not clock.get("is_open"): return
```

**Commit:** `b12c28f`

---

## 2026-05-01 — Credit price hardcoded at $3.50

**Symptom:** Every trade notification showed "Credit: $3.5" regardless of actual market price.

**Root cause:** `est_credit = 3.50` was hardcoded fallback, never updated with real prices.

**Fix:** Fetch real bid/ask mid from Alpaca options chain for actual contracts.

**Commit:** `4741435`

---

## 2026-05-01 — Intra-batch duplicate trades

**Symptom:** TSLA ×3, AMZN ×2, NFLX ×3 in same Telegram message.

**Root cause:** Dedup checked previous runs but not within the same 15-min batch. Multiple signals for same symbol in same scan all passed through.

**Fix:** `already_traded.add(sym)` after each trade prevents same symbol twice in one run.

**Commit:** `4741435`

---

## 2026-05-01 — `requests` not imported at module level

**Symptom:** `NameError: name 'requests' is not defined` in capital deployed check.

**Root cause:** `requests` was only imported inside functions, not at top of file.

**Fix:** Added `import requests` to top-level imports alongside `import os, re`.

**Commit:** `72a9bfa`

---

## 2026-05-01 — `PAPER_BASE` and `TRADE_LOG_TAB` undefined

**Symptom:** `NameError: name 'PAPER_BASE' is not defined` and `NameError: name 'TRADE_LOG_TAB' is not defined`

**Root cause:** When adding `PAPER_BASE` constant, accidentally replaced the line that defined `TRADE_LOG_TAB`.

**Fix:** Restored both constants at module level:
```python
PAPER_BASE = "https://paper-api.alpaca.markets"
TRADE_LOG_TAB = "FLOW_TRADE_LOG_15K" if USE_10K_ACCOUNT else "FLOW_TRADE_LOG"
```

**Commit:** `3b30e88`

---

## 2026-05-01 — yfinance 404 spam for ETFs in earnings check

**Symptom:** Hundreds of `HTTP Error 404: No fundamentals data found for symbol: SPY/QQQ/GLD` in workflow logs.

**Root cause:** ETFs have no earnings calendar. yfinance throws HTTP 404 for every ETF in the signal list, printing to stdout.

**Fix:** Suppress yfinance logging + catch exception silently:
```python
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
try:
    earn_date = yf.Ticker(sym).calendar.get("Earnings Date")[0]
except Exception:
    pass  # ETFs → OK to trade
```

**Commit:** `fd72de4`

---

## 2026-04-30 — Duplicate spread legs in Flow-15K account

**Symptom:** AMD had 8 orders, TSLA had 4 orders. Account showed -$580 in closed P&L from closing extra legs.

**Root cause:** Dedup only checked `FLOW_TRADE_LOG` sheet, not open Alpaca positions. Same symbol could be traded multiple times if positions weren't in the log yet.

**Fix:** Also check open Alpaca positions before trading:
```python
open_pos = requests.get(f"{PAPER_BASE}/v2/positions", headers=H).json()
for p in open_pos:
    already_traded.add(underlying_symbol)
```

**Commit:** `flow_trader.py` dedup fix

---

## 2026-04-30 — Bull-put buying wrong leg first (naked short)

**Symptom:** 401 error on sell order. Alpaca rejected naked short put.

**Root cause:** `_execute_spread()` was selling the short leg first. If the buy order then failed, we'd have a naked short put with unlimited downside.

**Fix:** Buy protective leg first, then sell:
```python
r1 = buy(buy_sym)   # protective leg first
r2 = sell(sell_sym) # then sell
```

**Commit:** `bull_put.py` fix

---

## 2026-04-29 — News bot 401 error

**Symptom:** News bot failing with 401 Unauthorized on Alpaca news API.

**Root cause:** `fetcher.py` was using `ALPACA_WHEEL_API_KEY` (dead account) for news API.

**Fix:** Changed to use `ALPACA_FLOW10K_API_KEY` (Bull-Put account, active).

**Commit:** `fetcher.py` fix

---

## 2026-04-28 — SIGNAL_OUTCOMES dedup (3,232 → 672 rows)

**Symptom:** SIGNAL_OUTCOMES sheet had 3,232 rows, mostly duplicates.

**Root cause:** Dedup key was only timestamp, not full signal key. Same signal logged multiple times.

**Fix:** Dedup key = `symbol + type + strike + expiry + timestamp`.

---

## 2026-04-28 — EARNINGS_TRACKER dedup (3,398 → 26 rows)

**Symptom:** EARNINGS_TRACKER sheet had 3,398 rows for ~26 unique earnings events.

**Root cause:** No dedup — appended every run.

**Fix:** Check existing rows before appending.

---

## 2026-04-28 — Schwab `netPercentChange` multiplied by 100

**Symptom:** Price changes showing as 500% instead of 5%.

**Root cause:** Schwab `netPercentChange` is already a percentage (e.g., 5.0 = 5%). Code was multiplying by 100 again → 500%.

**Fix:** Use value directly without multiplication.

---

## How to Debug Common Issues

**Workflow failing:**
```bash
gh run list --repo chiju/options-flow-scanner --limit 5
gh run view --log-failed <run_id> 2>&1 | grep "Error\|Traceback"
```

**No trades firing:**
1. Check market clock (is market open?)
2. Check FLOW_TRADE_LOG_15K for SKIPPED rows
3. Check Telegram for "Signals Blocked" alerts
4. Check SIGNAL_HISTORY sheet for recent sweeps

**Bad fill prices:**
- Paper trading fills deep OTM options at stale prices
- Ensure spread width is appropriate for stock price
- $2 wide for stocks <$30, $5 wide for $30-100, $10 wide for $100+

**Schwab token expired:**
```bash
python schwab_cli.py auth && python schwab_token_store.py save
```
