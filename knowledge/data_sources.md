# Data Sources — Why Each Is Used

## Summary

| Source | Used For | Why |
|--------|----------|-----|
| **Schwab** | Options chain, Greeks, OI, IV, GEX | Real-time, real Greeks — best quality |
| **Alpaca** | Quotes, news, historical bars, paper execution | Fast, free, paper trading support |
| **yfinance** | Earnings calendar only | Only available source for this data |

---

## Schwab (Primary for Options Data)

**Files:** `options_flow_scanner.py`, `schwab_scanner.py`, `gamma_levels.py`, `oi_tracker.py`

**Provides:**
- Real-time options chain with actual Greeks (delta, gamma, theta, vega)
- Real open interest per strike
- Real implied volatility
- Real-time stock quotes
- Historical price bars (OHLCV)
- Account info + live trade execution

**Does NOT provide:**
- Earnings calendar → use yfinance
- News feed → use Alpaca
- Paper trading → use Alpaca paper accounts
- Historical options pricing data

**Why Schwab over Alpaca for options:**
Alpaca options data has delayed Greeks and estimated OI. Schwab provides real-time
Greeks from the actual market maker feed — critical for accurate scoring.

---

## Alpaca (Execution + News + Quotes)

**Files:** `flow_trader.py`, `daily_brief.py`, `options_flow_scanner.py`

**Used for:**
- Paper trade execution (orders, positions, account management)
- News API (real-time news feed for 50+ symbols)
- Stock quotes (fast, free via IEX feed)
- Historical price bars for daily brief
- Market clock (is market open?)

**Why Alpaca for execution:**
Schwab has no paper trading. Alpaca paper accounts are free, realistic, and
support options order types (buy/sell to open/close).

**Why Alpaca for news:**
Schwab has no news API. Alpaca News API covers all major symbols with
real-time headlines and sentiment.

---

## yfinance (Earnings Calendar Only)

**Files:** `flow_trader.py` (earnings filter), `earnings.py`

**Used for:**
- Earnings dates only (`yf.Ticker(sym).calendar`)

**Why yfinance:**
Neither Schwab nor Alpaca provide earnings calendars. yfinance is the
simplest free source. ETFs (SPY, QQQ, GLD) return 404 — handled gracefully
with `logging.getLogger("yfinance").setLevel(logging.CRITICAL)`.

**NOT used for:**
- Price data → Alpaca (faster, already authenticated)
- Options data → Schwab (real Greeks)
- OI data → Schwab (real OI)

---

## Decision Rule

```
Options data (Greeks, OI, IV)?  → Schwab
Execution / account?            → Alpaca paper
News?                           → Alpaca News API
Quotes / price?                 → Alpaca (IEX feed)
Earnings dates?                 → yfinance (only option)
```
