# Scanner Logic — How Signals Are Detected

## What Gets Scanned

Every 15 minutes, the scanner checks every contract across every expiry and strike for all 54 symbols.

```
Per symbol (e.g. CRWV at $119):
  MAX_DTE:      90 days out
  Strikes:      40 per expiry (20 above + 20 below current price)
  Sides:        CALL + PUT
  Expiries:     ~9 (weekly + monthly out to 90 days)

  Per symbol: 9 expiries × 40 strikes × 2 sides = ~720 contracts

Total per run:
  54 symbols × 720 contracts = ~38,880 contracts checked every 15 min
```

## How Sweeps Are Detected

A sweep is flagged when ALL of the following are true:

```
1. Bought at ASK price (not mid) → urgency, not passive limit order
2. Filled across multiple exchanges in <1 second → sweep order
3. Large single order (not retail size)
4. Schwab marks it as a sweep in the options chain data
```

Schwab provides the sweep flag directly — we don't calculate it ourselves.

## How Unusual Volume Is Detected

```python
volume_ratio = current_volume / avg_daily_volume_baseline

3x  baseline → unusual (score +1)
5x  baseline → very unusual (score +2)
10x baseline → extreme (score +3)
```

The baseline is the symbol's own historical average — so a 10x spike on CRWV
means 10x CRWV's normal volume, not the market average.

## Scoring Per Contract (0-10)

Every contract gets scored. Only high scores get alerted.

```
Premium size:
  ≥$20M notional → +5
  ≥$10M          → +4
  ≥$5M           → +3
  ≥$1M           → +2
  ≥$100K         → +1

Volume vs baseline:
  10x → +3 | 5x → +2 | 3x → +1

Ascending volume (increasing each hour): +3 strong / +1 weak
Sweep order: +2
ATM delta (0.35-0.65): +2
IV rank High (≥70): +2 | IV rank Low (≤30): +1
DTE: 0-7 days → +2 | 8-30 days → +1
Theta high: +1

Cap: deep ITM (delta > 0.85) → max score 4
```

## Filtering

```
Score ≥ 9  → Telegram alert (Golden Flow / High Conviction)
Score 7-8  → Logged to UNUSUAL_ALERTS sheet only
Score ≤ 6  → Silent (discarded)

Out of ~38,880 contracts per run:
  ~50-100 score ≥7 (logged)
  ~5-10   score ≥9 (alerted)
```

## Signal vs Trade DTE

The scanner detects signals from ANY expiry (0-90 DTE).
The flow trader only executes on 21-45 DTE monthly contracts.

A 0-DTE sweep on TSLA $385C tells us direction (bullish).
We then sell a TSLA put spread expiring in 30 days to monetize that direction.

See [flow_trader_execution.md](flow_trader_execution.md) for full trade logic.

## Example: CRWV May 1, 2026

```
Stock at $119.67
14:03  CALL $120 May 01  $6,905K  SWEEP  → volume 20x baseline
14:18  CALL $120 May 01  $7,979K  SWEEP  → same strike, 15 min later

Score: premium $7M (+4) + sweep (+2) + 10x vol (+3) + ATM delta (+2) = 11 → capped at 10
Result: Golden Flow alert fired, Telegram sent
```
