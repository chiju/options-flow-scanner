# Flow Trader — Bugs Fixed & Improvements | May 1, 2026

## Summary
Major debugging session. Found and fixed 6 bugs that were preventing correct operation.
All fixes committed and deployed.

---

## Bug 1: Pre-market phantom trades (FIXED)
**Symptom:** Telegram received trade notifications at 10:38 UTC (pre-market) for MSFT, SPY, QQQ, AAPL, AMZN, TSLA — but account showed $15,000 clean with 0 positions.

**Root cause:** Notification fired based on `trade_rows` being populated, not on actual order execution. Orders were being sent to Alpaca during pre-market hours when options market is closed → orders rejected → but notification still fired.

**Fix:** Added Alpaca market clock check as FIRST step in `run_flow_trader()`. If market is closed → run exit checks only, skip all entry logic.

```python
clock = requests.get(f"{PAPER_BASE}/v2/clock", headers=H).json()
market_open = clock.get("is_open", True)
if not market_open:
    print(f"Market closed — skipping new entries, checking exits only")
    return
```

---

## Bug 2: Notification fires on failed trades (FIXED)
**Symptom:** Telegram showed trades that never actually executed.

**Root cause:** Notification was triggered by `trade_rows` existing, not by `status == "SUBMITTED"`.

**Fix:** Notification now only fires on `executed_rows` (orders confirmed by Alpaca).

---

## Bug 3: Intra-batch duplicate trades (FIXED)
**Symptom:** TSLA ×3, AMZN ×2, NFLX ×3 in same Telegram message.

**Root cause:** Dedup checked previous runs but not within the same 15-min batch. Multiple signals for same symbol in same scan all passed through.

**Fix:** `already_traded.add(sym)` after each trade prevents same symbol twice in one run.

---

## Bug 4: Credit price hardcoded at $3.50 (FIXED)
**Symptom:** Every trade notification showed "Credit: $3.5" regardless of actual market price.

**Root cause:** `est_credit = 3.50` was hardcoded as fallback and never updated.

**Fix:** Now fetches real bid/ask mid from Alpaca options chain for the actual contracts.

---

## Bug 5: `requests` not imported at module level (FIXED)
**Symptom:** `NameError: name 'requests' is not defined` in capital deployed check.

**Root cause:** `requests` was only imported inside functions, not at top of file.

**Fix:** Added `import requests` to top-level imports.

---

## Bug 6: Paper key used for market data API (FIXED — today's key bug)
**Symptom:** META had score 10 signal (44 sweeps, $6.1M premium) but trade never executed.
Log showed: `Strike error META: Expecting value: line 1 column 1 (char 0)`

**Root cause:** `find_spread_strike()` was using `PAPER_API_KEY` to call `data.alpaca.markets`. Paper keys only work for `paper-api.alpaca.markets` (trading). Market data API requires live key → returned empty response → JSON parse error.

**Fix:** Now uses `ALPACA_LIVE_API_KEY` for market data, `PAPER_API_KEY` only for order execution.

```python
live_key = os.environ.get("ALPACA_LIVE_API_KEY", PAPER_API_KEY)
r = requests.get(f"https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest",
    headers={"APCA-API-KEY-ID": live_key, ...})
```

---

## Improvement: Signal skip notifications (NEW)
**Problem:** No visibility into why signals were blocked. Could see trade executed but not why trades didn't fire.

**Fix:** Added two new Telegram alert types:

1. **Earnings blocked:**
   ```
   ⏭️ Flow-15K — Signals Blocked (Earnings)
   NVDA, AMD, PLTR
   Earnings within 30-day expiry window — skipped per Rule 4
   ```

2. **No strike found:**
   ```
   ⚠️ Flow-15K Signal — No Strike Found
   META BULLISH | Score:10 | $6160K
   Could not find 21-45 DTE options contract
   ```

---

## Lesson Learned
**The META trade was missed because of Bug 6.** META had a score 10 signal with 44 sweeps and $6.1M premium — exactly the kind of trade the system is designed for. The paper key / live key mismatch silently failed the price lookup, causing the trade to be skipped with no explanation.

**Always verify:** paper keys = execution only, live keys = market data.

---

## Current State (End of May 1)
- Flow-15K: $15,000 clean, 0 positions
- All 6 bugs fixed and deployed
- 3 Telegram alert types: executed / earnings blocked / no strike
- Market clock check: no more pre-market phantom trades
- Tests: 15/15 passing
