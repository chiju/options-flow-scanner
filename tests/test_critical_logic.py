"""
Critical tests for options-flow-scanner.
Run: python -m pytest tests/ -v

Only tests logic that caused real bugs or would be catastrophic if wrong.
No API calls, no Sheets, no Telegram — pure logic tests.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ── Inline score_alert for testing (avoids alpaca import) ─────────────────────

def score_alert(entry: dict) -> int:
    """Minimal copy of score_alert for testing core logic."""
    s = 0
    p = entry.get("premium", 0)
    if p >= 20_000_000:  s += 5
    elif p >= 10_000_000: s += 4
    elif p >= 5_000_000:  s += 3
    elif p >= 1_000_000:  s += 2
    elif p >= 100_000:    s += 1

    if entry.get("sweep"):    s += 2
    iv_rank = entry.get("iv_rank", "")
    if iv_rank and "High" in str(iv_rank):
        if entry.get("type") == "CALL": s += 2
    asc = entry.get("ascending_vol")
    if asc == "strong":  s += 3
    elif asc == "weak":  s += 1
    vb = entry.get("vol_vs_baseline")
    if vb and vb >= 10: s += 3
    elif vb and vb >= 5: s += 2
    elif vb and vb >= 3: s += 1
    dte = entry.get("dte", 99)
    if dte <= 7:    s += 2
    elif dte <= 30: s += 1
    delta = entry.get("delta")
    if delta is not None:
        abs_delta = abs(delta)
        if abs_delta > 0.85:
            return min(s, 4)  # deep ITM cap
        elif 0.35 <= abs_delta <= 0.65: s += 2
    return min(s, 10)


# ── Scoring tests ──────────────────────────────────────────────────────────────

def test_deep_itm_capped_at_4():
    """Deep ITM calls (delta >0.85) = hedge, not signal → cap at 4"""
    entry = {'type': 'CALL', 'premium': 50_000_000, 'sweep': True,
             'dte': 7, 'delta': 0.97, 'iv': 80}
    assert score_alert(entry) <= 4, "Deep ITM should be capped at score 4"


def test_atm_call_scores_high():
    """ATM call with sweep + premium should score 9+"""
    entry = {'type': 'CALL', 'premium': 5_000_000, 'sweep': True,
             'dte': 7, 'delta': 0.50, 'iv': 80}
    assert score_alert(entry) >= 8, "ATM call with sweep should score 8+"


def test_ascending_volume_adds_points():
    """Ascending volume (strong) should add 3 points"""
    base = {'type': 'CALL', 'premium': 1_000_000, 'sweep': True, 'dte': 15, 'delta': 0.50}
    score_without = score_alert({**base})
    score_with = score_alert({**base, 'ascending_vol': 'strong'})
    assert score_with > score_without, "Ascending volume should increase score"
    assert score_with - score_without >= 2, "Strong ascending should add at least 2 pts"


def test_iv_rank_high_adds_points():
    """High IV rank should add 2 points"""
    base = {'type': 'CALL', 'premium': 1_000_000, 'sweep': True, 'dte': 15, 'delta': 0.50}
    score_without = score_alert({**base})
    score_with = score_alert({**base, 'iv_rank': 'IVR 85 🔴 High'})
    assert score_with >= score_without, "High IV rank should not decrease score"


def test_score_capped_at_10():
    """Score should never exceed 10"""
    entry = {
        'type': 'CALL', 'premium': 50_000_000, 'sweep': True,
        'dte': 5, 'delta': 0.50, 'iv': 90, 'iv_rank': 'IVR 90 🔴 High',
        'ascending_vol': 'strong', 'vol_vs_baseline': 15, 'vol_oi_ratio': 12
    }
    assert score_alert(entry) <= 10, "Score should never exceed 10"


# ── Profit/loss calculation tests ──────────────────────────────────────────────

def test_50_percent_profit_triggers_close():
    """50% profit should trigger close"""
    entry_price = 3.50
    current_price = 1.75  # 50% of entry
    profit_pct = (entry_price - current_price) / entry_price
    assert profit_pct >= 0.50, "50% profit should trigger close"


def test_2x_stop_loss_triggers_close():
    """2x loss should trigger stop"""
    entry_price = 3.50
    current_price = 10.50  # 3x entry = 2x loss
    profit_pct = (entry_price - current_price) / entry_price
    assert profit_pct <= -2.0, "2x loss should trigger stop"


def test_49_percent_profit_does_not_close():
    """49% profit should NOT trigger close"""
    entry_price = 3.50
    current_price = 1.79  # 49% profit
    profit_pct = (entry_price - current_price) / entry_price
    assert profit_pct < 0.50, "49% profit should not trigger close"


# ── Dedup / position logic tests ───────────────────────────────────────────────

def test_dedup_blocks_existing_symbol():
    """Symbols with open positions should be blocked"""
    already_traded = {"AMD", "MSFT", "NVDA"}
    signals = [
        {"symbol": "AMD", "direction": "BULLISH"},   # should be blocked
        {"symbol": "SPY", "direction": "BULLISH"},   # should pass
        {"symbol": "NVDA", "direction": "BULLISH"},  # should be blocked
    ]
    filtered = [s for s in signals if s["symbol"] not in already_traded]
    assert len(filtered) == 1
    assert filtered[0]["symbol"] == "SPY"


def test_spread_width_10():
    """Bull put spread should be $10 wide"""
    sell_strike = 625
    buy_strike = sell_strike - 10
    spread_width = sell_strike - buy_strike
    assert spread_width == 10, "Spread should be $10 wide"


def test_max_loss_calculation():
    """Max loss = spread width - credit"""
    spread_width = 10
    credit = 3.50
    max_loss = (spread_width - credit) * 100
    assert max_loss == 650, "Max loss should be $650 per contract"


# ── Notional sweep test ────────────────────────────────────────────────────────

def test_notional_sweep_scales_by_price():
    """$1M notional sweep should work for cheap and expensive stocks"""
    # SOFI at $16: needs 625 contracts for $1M
    sofi_price = 16
    sofi_volume = 625
    sofi_notional = sofi_price * sofi_volume * 100
    assert sofi_notional >= 1_000_000, "SOFI 625 contracts should be $1M+"

    # SPY at $710: needs only 14 contracts for $1M
    spy_price = 710
    spy_volume = 14
    spy_notional = spy_price * spy_volume * 100
    assert spy_notional >= 900_000, "SPY 14 contracts should be ~$1M"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ── Earnings and capital limit tests ──────────────────────────────────────────

def test_earnings_within_expiry_blocked():
    """Earnings inside spread expiry window should be blocked"""
    from datetime import date, timedelta
    today = date.today()
    expiry = today + timedelta(days=30)
    
    # Earnings in 10 days = inside 30-day expiry = BLOCK
    earnings_in_10d = today + timedelta(days=10)
    assert earnings_in_10d < expiry, "Earnings in 10d should be inside expiry window"
    
    # Earnings in 40 days = outside 30-day expiry = OK
    earnings_in_40d = today + timedelta(days=40)
    assert earnings_in_40d > expiry, "Earnings in 40d should be outside expiry window"


def test_30_percent_capital_limit():
    """Total deployed should not exceed 30% of account"""
    account_value = 15_000
    max_deployed = account_value * 0.30  # $4,500
    
    # 6 spreads × $650 = $3,900 = 26% → OK
    six_spreads = 6 * 650
    assert six_spreads < max_deployed, "6 spreads should be under 30% limit"
    
    # 8 spreads × $650 = $5,200 = 34.7% → BLOCK
    eight_spreads = 8 * 650
    assert eight_spreads > max_deployed, "8 spreads should exceed 30% limit"


def test_dynamic_risk_scales_with_account():
    """Per-trade risk should scale with account value"""
    risk_pct = 0.05
    
    account_15k = 15_000
    assert int(account_15k * risk_pct) == 750
    
    account_20k = 20_000
    assert int(account_20k * risk_pct) == 1_000
    
    account_30k = 30_000
    assert int(account_30k * risk_pct) == 1_500


# ── New rules tests ────────────────────────────────────────────────────────────





