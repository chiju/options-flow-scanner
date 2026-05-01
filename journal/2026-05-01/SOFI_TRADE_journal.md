# SOFI — Flow Trader Trade Journal | May 1, 2026

## Trade Details
- **Account:** Flow-15K (PA3KU3B4THVE)
- **Signal:** SOFI BULLISH | Score:9+ | Sweep confirmed
- **Executed:** 17:48 UTC (7:48pm Berlin)
- **Spread:** SELL $15P / BUY $5P | May 22 expiry

## Fill Prices (Paper Trading)
```
SELL SOFI $15P May 22: filled at $0.23 (should be ~$1.50)
BUY  SOFI $5P  May 22: filled at $2.13 (should be ~$0.01)
Net: -$1.90 debit (should be ~+$1.49 credit)
```

## ⚠️ Issue: Bad Paper Trading Fill

**Root cause:** The $5P strike is $11 below SOFI's current price ($16.11).
Deep OTM options have no real market — Alpaca paper trading fills them at
stale/incorrect prices from the simulation engine.

**In real trading:** $5P would fill at ~$0.01-0.05 (nearly worthless).
**Paper fill:** $2.13 (completely wrong — simulation artifact).

**The spread is directionally correct** — SOFI needs to stay above $15 by May 22.
But the P&L tracking is distorted by the bad fill on the protective leg.

## Why This Happened

The `find_spread_strike()` function calculates:
```python
sell_strike = round(price * 0.88 / 5) * 5  # 12% OTM = $14 → rounded to $15
buy_strike  = sell_strike - 10              # $15 - $10 = $5
```

For SOFI at $16.11:
- sell_strike = round(16.11 × 0.88 / 5) × 5 = round(2.835) × 5 = $15 ✅
- buy_strike = $15 - $10 = $5 ← too far OTM for a $16 stock

**The $10 spread width is too wide for low-priced stocks.**
For a $16 stock, a $10 spread = 62% of stock price. Should be $2-3 wide max.

## Fix Needed

Spread width should scale with stock price:
```python
# Current (wrong for cheap stocks):
buy_strike = sell_strike - 10  # always $10 wide

# Better:
spread_width = min(10, max(2, round(price * 0.10 / 5) * 5))  # 10% of price, min $2, max $10
buy_strike = sell_strike - spread_width
```

For SOFI at $16: spread_width = min(10, max(2, round(1.6/5)×5)) = $2
→ sell $15P / buy $13P (much better, both have real market prices)

## Current Position Status
- SOFI at $16.11, spread expires May 22 (21 DTE)
- Stock needs to stay above $15 (6.3% buffer)
- Paper P&L distorted but direction is correct
- **Action:** Monitor, close at 50% profit or 2x loss per normal rules

## Lesson
**$10 spread width is only appropriate for stocks priced $50+.**
For stocks under $30, use a narrower spread (2-5 wide) to ensure
both legs have real market prices and liquid fills.
