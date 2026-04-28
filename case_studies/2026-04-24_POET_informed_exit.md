# Case Study: POET Technologies (POET) — April 2026
**The Informed Exit Signal: How Smart Money Sold Into a 27% Rally**

---

## Summary

POET Technologies surged +27% on April 24, 2026 on news of a Marvell/Celestial AI order.
Three days later it crashed -47%. Our scanner caught the informed exit signal in real time —
calls being sold aggressively while the stock was still rising.

**Trade result:** Sold 454 shares at $15.25 → +238% profit (+$4,875)
**Stock 3 days later:** $7.95 (-47% from exit)

---

## The Full Timeline

| Date | Event | Stock Price | Signal |
|------|-------|-------------|--------|
| Apr 21 | POET CFO publicly announces Marvell/Celestial AI order | ~$10 | Pump begins |
| Apr 22 | Retail FOMO buying | $12 | Stock rising |
| Apr 23 | **Marvell sends written cancellation** (confidentiality breach) | $12-13 | Private bad news |
| Apr 23 15:30 | $11C option price drops $1.65 → $1.00 while stock rising | $12 | **⚠️ Divergence** |
| Apr 24 14:16 | Our scanner: CALL $11 May01, vol:600, delta:0.79, **SELL** | $14-15 | **Exit signal** |
| Apr 24 14:46 | Our scanner: CALL $18 May15, vol:895, delta:0.35, **SELL** | $15 | Confirmed |
| Apr 24 close | Stock closes at $15.10 (+27%) | $15.10 | Exit at $15.25 ✅ |
| Apr 27 | POET publicly discloses cancellation | Opens at $8 | -47% crash |

---

## What Our Scanner Captured

```
2026-04-24 14:16 | POET | CALL $11 May 01 | vol:600 | $151K | delta:0.79 | SELL
2026-04-24 14:46 | POET | CALL $18 May 15 | vol:895 | $94K  | delta:0.35 | SELL
```

**Key observations:**
- `delta:0.79` = deep ITM call = existing long holders exiting, not new buyers
- `SELL` tag = last trade price below mid = sellers hitting bid aggressively
- Both alerts on the same day the stock was up 27%
- No BUY flow to counter the selling

---

## The Option Price History (Schwab data)

POET $11C May 01 — price vs stock direction:

```
Apr 23 15:30: Option $1.65 → drops to $1.00 by close
  Stock: rising from $12 → $13
  Signal: DIVERGENCE — option falling while stock rising
  Reason: Marvell sent cancellation notice this day (private)

Apr 24 15:45: Option $2.10, volume 607 contracts ← our scanner alert
  Stock: $14-15 (+27%)
  Signal: SELL — informed money exiting into retail buying

Apr 27 15:30: Option $0.73 → crashes to $0.09
  Stock: -47% on public disclosure
  Option: -98% in one day
```

---

## The Mechanism: How Informed Money Exited

```
Apr 23: Marvell sends cancellation (PRIVATE)
  → POET management knows deal is dead
  → Insiders start selling calls (quiet, $34-40 contracts/bar)
  → Option price drops despite stock rising = DIVERGENCE

Apr 24: Retail FOMO buying pushes stock to $15
  → Insiders sell MORE calls into the rally (600 contracts)
  → Our scanner catches it: SELL flow, deep ITM, high delta
  → Smart money fully exited by end of day

Apr 27: POET discloses publicly
  → Retail trapped, stock -47%
  → Law firm Block & Leviton opens securities fraud investigation
```

---

## The Signal Pattern: "Informed Exit"

**Conditions that define this pattern:**

1. **Stock up 5%+** on positive news/momentum
2. **Options showing SELL flow** (last price < mid = sellers hitting bid)
3. **Deep ITM calls being sold** (delta > 0.7 = existing longs exiting)
4. **No offsetting BUY flow** (sell premium > buy premium by 1.5x+)
5. **Divergence**: option price falling while stock rising

**This is NOT normal profit-taking.** Normal profit-taking = stock up, calls up, people selling to lock in gains. The signal here is calls FALLING in price while stock is rising = someone selling aggressively below market price = urgency to exit.

---

## How to Spot This in Future

### Automated (now implemented in scanner)

The divergence warning fires when:
```python
stock_up_pct >= 5%
AND call_sell_premium > call_buy_premium * 1.5
AND len(call_sells) >= 2
```

Telegram alert:
```
🔍 Divergence Warning — Possible Informed Exit

⚠️ POET up +27.0% but calls SOLD ($151K sell vs $12K buy)

Stock rising but smart money selling calls = potential bad news ahead.
Consider reducing position.
```

### Manual checklist when a stock is up 10%+

- [ ] Check options buy_sell column in UNUSUAL_ALERTS
- [ ] Are deep ITM calls (delta > 0.7) being sold?
- [ ] Is option price rising with the stock or falling?
- [ ] Is there a recent news catalyst that could be "too good to be true"?
- [ ] Check insider activity (SEC Form 4 filings)

### Red flags that preceded the POET crash

- CFO publicly disclosed confidential order details (unusual)
- The "order" was only $5M — tiny for a company claiming hyperscaler partnerships
- Short float was 8% — not high enough for a sustained squeeze
- No institutional call buying — only retail lottery tickets ($15C at $0.03)
- ainvest headline: "AI Optical Hype Is Outpacing Intrinsic Value" (Apr 22)

---

## Lessons Learned

**1. News-driven pumps need verification**
The Marvell "order" was $5M — barely material. The stock went from $4 to $15 on a $5M order.
Valuation: 715x EV/Revenue. The hype outpaced reality.

**2. The options chain tells the truth**
While CNBC and retail were buying, the options chain showed SELL.
The options market is harder to fake than stock price.

**3. Divergence is the key signal**
Stock up + options down = someone knows something you don't.
This is the most reliable informed exit signal.

**4. Sell into strength, not weakness**
Exiting at $15.25 (the top) was only possible because we read the options.
Waiting for "confirmation" of the drop would have meant selling at $8.

**5. The 80/20 rule in action**
This single trade = 27% of all realized profits in the portfolio.
One correct read of the options chain = more than months of other trades.

---

## Data Sources Used

| Source | Data | Used for |
|--------|------|---------|
| Our scanner (UNUSUAL_ALERTS) | CALL $11 SELL, vol:600, delta:0.79 | Exit signal |
| Schwab API | $11C price history (15-min candles) | Divergence confirmation |
| Finnhub news | Marvell order announcement | Context |
| ainvest.com | "Insiders sold while deal was crumbling" | Post-mortem |
| Morningstar | Cancellation notice dated Apr 23 | Timeline |

---

## Impact on System

This case study led to two system improvements:

1. **Divergence Warning alert** added to scanner (Apr 28, 2026)
   - Fires when stock up 5%+ but calls being sold
   - Requires Schwab real-time buy/sell detection

2. **Historical option price data** now available via Schwab
   - Can reconstruct option price history for any contract
   - Enables backtesting divergence patterns

---

*Case study documented: April 28, 2026*
*Trade executed: April 24, 2026*
*System: options-flow-scanner*
