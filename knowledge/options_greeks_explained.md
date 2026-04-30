# Options Greeks — How They Work in Our Flow Trader
**Reference document with real trade example**

---

## The 4 Greeks

| Greek | Measures | Simple explanation | Buyer wants | Seller wants |
|-------|---------|-------------------|------------|-------------|
| **Delta** | Stock price sensitivity | "If stock moves $1, option moves this much" | High (0.5+) | Low |
| **Gamma** | Delta acceleration | "How fast delta is changing" | High near expiry | Low (dangerous) |
| **Theta** | Time decay per day | "I lose this much every day just from time passing" | Low | **High (collect daily)** |
| **Vega** | IV sensitivity | "I change this much per 1% IV move" | High IV | **Low IV (after earnings)** |

---

## Greeks Change by Strike Price

```
JOBY at $8.70:

Strike  Type    Delta   Gamma   Theta   Vega
$8C     ITM     0.66    0.20    -0.08   0.01   ← safer for buyer, expensive
$9C     ATM     0.46    0.25    -0.05   0.01   ← most sensitive, balanced
$10C    OTM     0.14    0.10    -0.02   0.005  ← cheap lottery ticket

Pattern:
  Delta:  increases going ITM (0.1 → 0.5 → 0.9)
  Gamma:  highest at ATM (bell curve)
  Theta:  highest at ATM (decays fastest)
  Vega:   highest at ATM (most IV sensitive)
```

---

## Safer and Cheaper — For Whom?

```
BUYER perspective:
  Safer  = ITM (delta 0.8+) — already has value, won't expire worthless easily
  Cheaper = OTM (delta 0.1) — costs $0.05 but needs 20% move to profit
  Best value = ATM (delta 0.5) — balanced risk/reward

SELLER perspective:
  Safer  = OTM (delta 0.1) — stock needs to move a LOT to hurt you
  More income = ATM (delta 0.5) — collects most theta per day
  Dangerous = ITM (delta 0.8+) — already losing money

KEY RULE: What's safe for sellers is risky for buyers, and vice versa.
OTM is CHEAP for buyers but SAFE for sellers.
```

---

## Real Trade Example: OKLO Bull Put Spread

**Setup:**
```
OKLO stock at $70 | Sold $62P / Bought $52P
Net credit collected: $2.21 per share = $221 per contract
```

**Greeks at entry:**
```
Short $62P: delta -0.49, gamma -0.04, theta +0.13, vega -0.08
Long $52P:  delta +0.22, gamma +0.02, theta -0.06, vega +0.04

Net position:
  Delta: -0.27  (we lose $0.27 per $1 OKLO drops)
  Gamma: -0.02  (small, manageable — spread protects us)
  Theta: +0.07  (we COLLECT $0.07 every day ← our income)
  Vega:  -0.04  (we LOSE if IV rises, WIN if IV drops)
```

**How each Greek affected the trade daily:**

```
THETA (our income):
  Day 1:  spread worth $2.21
  Day 7:  spread worth $2.21 - (7 × $0.07) = $1.72
  Day 14: spread worth $2.21 - (14 × $0.07) = $1.23
  Day 21: spread worth $2.21 - (21 × $0.07) = $0.74
  
  At 70% profit target → close → keep $1.55 profit ✅

DELTA (stock movement risk):
  OKLO drops $1 → we lose $0.27
  OKLO rises $1 → we gain $0.27
  Stock stayed above $62 → we kept full premium ✅

GAMMA (acceleration risk):
  Small (-0.02) because we used a SPREAD
  Naked put would have gamma -0.04 (2x more dangerous)
  Spread structure cuts gamma risk in half ✅

VEGA (IV risk):
  We entered when IV was HIGH (IVR 70+)
  IV dropped over time → spread lost value → we profited ✅
```

**Result:**
```
Entry: collected $221 credit
Theta worked for us: 15 days × $0.07 = $1.05 collected
Closed at 70% profit: kept $155 profit (+70%)
Win rate: 75% across all trades
```

---

## How Our System Uses Greeks

| Greek | How we use it | Where in code |
|-------|--------------|---------------|
| **Delta** | Score +2 for ATM (0.35-0.65) = directional bet | `score_alert()` |
| **Delta** | Cap deep ITM (>0.85) at score 4 = hedge filter | `score_alert()` |
| **Delta** | Buy/sell detection: last price vs mid | `scan_symbol()` |
| **Gamma** | GEX = gamma × OI × spot² = call/put walls | `gamma_levels.py` |
| **Theta** | Score +1 for high theta puts = good spread timing | `score_alert()` |
| **IV/Vega** | IV rank: sell when IVR≥70, buy when IVR≤30 | `score_alert()` |

**flow_trader uses Greeks to:**
```
1. Sell 30-delta puts (delta ~0.30) → 70% probability of profit
2. Collect theta every day → passive income
3. Enter when IV rank ≥50 → fat premium (vega advantage)
4. Close at 70% profit → theta has done its job
5. Close at 7 DTE → avoid gamma spike near expiry
```

---

## Quick Memory Tricks

```
Delta  = "How much does my option care about stock price?"
Gamma  = "Acceleration — how fast is delta changing?"
Theta  = "Time is money — I lose/gain this per day"
Vega   = "Volatility sensitivity — IV up = option up"

For our spreads:
  Theta is our FRIEND (we collect it)
  Gamma is CONTROLLED (spread limits it)
  Vega we MANAGE (enter at high IV, profit as it drops)
  Delta is our RISK (stock must stay above short strike)
```
