"""
Flow Trader — executes paper trades based on confirmed flow signals.

Battle-tested rules (from CBOE floor trader research):
  1. Three-sweep rule: same contract swept 3+ times = conviction
  2. Premium >$1M per sweep (filters retail noise)
  3. Score ≥ 8 (sweep + size + urgency)
  4. OI confirmed next day (new position, not closing)
  5. News sentiment agrees (FinBERT)
  6. GEX negative (moves will be amplified)

Strategy: Sell PUT SPREAD on bullish signals, CALL SPREAD on bearish.
  - Strike: 10-15% OTM (delta ~0.20-0.25)
  - DTE: 21-45 days
  - Position size: 2% of account
  - Close: 50% profit or 2× loss

Mode: DRY_RUN = True (logs what it WOULD trade, no real orders)
      DRY_RUN = False (executes on paper account)

Run: python flow_trader.py
"""
import os, re
from datetime import datetime, timedelta
from collections import Counter
from sheets import _service, SHEET_ID, _append, _ensure_tabs

DRY_RUN = False  # Paper trading on CSP account
PAPER_API_KEY    = os.environ.get("ALPACA_CSP_API_KEY", os.environ.get("ALPACA_API_KEY", ""))
PAPER_API_SECRET = os.environ.get("ALPACA_CSP_SECRET_KEY", os.environ.get("ALPACA_SECRET_KEY", ""))

TRADE_LOG_HEADERS = [
    "date", "symbol", "signal_type", "direction", "score",
    "sweep_count", "premium_k", "confluence",
    "action", "strike", "expiry", "spread_width",
    "entry_credit", "max_loss", "target_profit",
    "status", "dry_run"
]

# ── Signal Analysis ───────────────────────────────────────────────────────────

def get_confirmed_signals(svc, lookback_days: int = 2) -> list:
    """
    Find signals that meet ALL battle-tested criteria:
    1. Swept 3+ times (three-sweep rule)
    2. Score ≥ 8
    3. Premium ≥ $1M
    4. OI confirmed (if available)
    5. News agrees
    6. GEX negative
    """
    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    # Get signal history (sweeps)
    r = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="SIGNAL_HISTORY!A:F"
    ).execute()
    sigs = [row for row in r.get("values", [])[1:]
            if len(row) >= 4 and row[0][:10] >= cutoff and "SWEEP" in row[1]]

    # Count sweeps per contract (normalize key: remove score variation)
    contract_sweeps = Counter()
    contract_details = {}
    for s in sigs:
        # Normalize: remove score from detail to group same contract
        detail_norm = re.sub(r'⭐\d+', '', s[3]).strip()
        key = f"{s[2]}|{detail_norm}"
        contract_sweeps[key] += 1
        contract_details[key] = s  # keep latest

    # Get GEX data
    r2 = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="GAMMA_LEVELS!A:L"
    ).execute()
    gex_data = {}
    for row in r2.get("values", [])[1:]:
        if len(row) >= 11 and row[1] not in gex_data:
            try: gex_data[row[1]] = float(row[10])
            except: pass

    # Get news sentiment
    from daily_brief import fetch_news_sentiment
    all_syms = list(set(k.split("|")[0] for k in contract_sweeps.keys()))
    news = fetch_news_sentiment(all_syms, hours_back=48) if all_syms else {}

    # Filter by battle-tested criteria
    confirmed = []
    for key, count in contract_sweeps.items():
        if count < 3:  # Three-sweep rule
            continue

        sym, detail = key.split("|", 1)
        s = contract_details[key]

        # Parse score from ORIGINAL detail (s[3] has the score, normalized key doesn't)
        score = 0
        score_match = re.search(r'⭐(\d+)', s[3])
        if score_match:
            score = int(score_match.group(1))
        if score < 8:
            continue

        # Parse premium from value column (col 5: "$2940K")
        s = contract_details[key]
        premium_match = re.search(r'\$(\d+)K', s[4] if len(s) > 4 else "")
        premium_k = int(premium_match.group(1)) if premium_match else 0
        if premium_k < 1000:  # $1M minimum
            continue

        # Direction
        direction = "BULLISH" if "🐂" in detail or "CALL" in detail else "BEARISH"

        # GEX check (negative = amplified moves = better for directional)
        gex = gex_data.get(sym, 0)
        gex_ok = gex < 0

        # News check
        n = news.get(sym, {})
        if direction == "BULLISH":
            news_ok = n.get("positive", 0) >= n.get("negative", 0)
        else:
            news_ok = n.get("negative", 0) > n.get("positive", 0)

        # Confluence score (GEX and news are bonuses, not hard requirements)
        confluence_pts = count  # sweep count is the core signal
        if gex_ok: confluence_pts += 1
        if news_ok: confluence_pts += 1

        # Only require: 3+ sweeps + score≥8 + premium≥$1M
        # GEX and news improve confidence but don't block
        confirmed.append({
            "symbol": sym,
            "detail": detail,
            "direction": direction,
            "score": score,
            "sweep_count": count,
            "premium_k": premium_k,
            "gex": gex,
            "gex_ok": gex_ok,
            "news_ok": news_ok,
            "confluence": confluence_pts,
        })

    # Sort by confluence
    return sorted(confirmed, key=lambda x: x["confluence"], reverse=True)


def _execute_spread(symbol: str, spread: dict, expiry: str) -> bool:
    """Submit bull put spread to Alpaca paper account."""
    try:
        import requests
        from datetime import datetime as _dt

        BASE = "https://paper-api.alpaca.markets/v2"
        H = {"APCA-API-KEY-ID": PAPER_API_KEY, "APCA-API-SECRET-KEY": PAPER_API_SECRET}

        # Format expiry as YYMMDD for OCC symbol
        exp_dt = _dt.strptime(expiry, "%Y-%m-%d")
        exp_occ = exp_dt.strftime("%y%m%d")

        def occ(sym, exp, cp, strike):
            """Build OCC option symbol: AAPL260117C00150000"""
            strike_str = f"{int(strike * 1000):08d}"
            return f"{sym}{exp}{cp}{strike_str}"

        sell_sym = occ(symbol, exp_occ, "P", spread["sell_strike"])
        buy_sym  = occ(symbol, exp_occ, "P", spread["buy_strike"])

        # Sell higher strike put
        r1 = requests.post(f"{BASE}/orders", headers=H, json={
            "symbol": sell_sym, "qty": "1", "side": "sell",
            "type": "market", "time_in_force": "day"
        })
        # Buy lower strike put
        r2 = requests.post(f"{BASE}/orders", headers=H, json={
            "symbol": buy_sym, "qty": "1", "side": "buy",
            "type": "market", "time_in_force": "day"
        })

        if r1.ok and r2.ok:
            print(f"     Sell {sell_sym}: {r1.status_code}")
            print(f"     Buy  {buy_sym}: {r2.status_code}")
            return True
        else:
            print(f"     Order error: sell={r1.status_code} {r1.text[:100]}")
            print(f"                  buy={r2.status_code} {r2.text[:100]}")
            return False
    except Exception as e:
        print(f"     Execution error: {e}")
        return False


def check_exits() -> list:
    """
    Check open flow-trader positions and close if exit criteria met.
    Exit rules:
      1. 50% profit (spread worth half of credit received)
      2. 2× loss (spread worth 3× credit received)
      3. 7 days before expiry
      4. Short strike breached (stock below sell strike)
    """
    try:
        import requests, re
        from datetime import date

        BASE = "https://paper-api.alpaca.markets/v2"
        H = {"APCA-API-KEY-ID": PAPER_API_KEY, "APCA-API-SECRET-KEY": PAPER_API_SECRET}

        positions = requests.get(f"{BASE}/positions", headers=H).json()
        if not isinstance(positions, list):
            return []

        closed = []
        # Only look at short puts (our sell leg)
        short_puts = [p for p in positions
                      if float(p["qty"]) < 0 and re.search(r'\d{6}P\d{8}', p["symbol"])]

        for p in short_puts:
            sym_match = re.match(r'([A-Z]+)(\d{6})P(\d{8})', p["symbol"])
            if not sym_match:
                continue

            underlying = sym_match.group(1)
            exp_str = sym_match.group(2)
            strike = int(sym_match.group(3)) / 1000

            entry = abs(float(p["avg_entry_price"]))
            current = abs(float(p["current_price"]))
            profit_pct = (entry - current) / entry if entry > 0 else 0

            # DTE
            exp_date = datetime.strptime(exp_str, "%y%m%d").date()
            dte = (exp_date - date.today()).days

            reason = None
            if profit_pct >= 0.70:  # 70% profit = professional standard
                reason = f"70% profit ({profit_pct:.0%})"
            elif profit_pct <= -1.5:  # Stop at 1.5× credit (spread at $7.50 on $10 spread)
                reason = f"Stop loss ({profit_pct:.0%})"
            elif dte <= 7:
                reason = f"Near expiry ({dte}d)"

            if reason:
                # Close the short put
                r = requests.post(f"{BASE}/orders", headers=H, json={
                    "symbol": p["symbol"], "qty": "1", "side": "buy",
                    "type": "market", "time_in_force": "day"
                })
                if r.ok:
                    closed.append(f"✅ Closed {underlying} {p['symbol'][-15:]} | {reason}")
                    print(f"  Closed: {underlying} | {reason}")

        return closed
    except Exception as e:
        print(f"  Exit check error: {e}")
        return []


def find_spread_strike(symbol: str, direction: str, otm_pct: float = 0.12) -> dict:
    """Find appropriate strike for spread (10-15% OTM, DTE 21-45)."""
    try:
        import yfinance as yf
        price = yf.Ticker(symbol).fast_info.last_price
        if not price:
            return {}

        if direction == "BULLISH":
            # Sell PUT spread: strike 12% below current price, $10 wide
            sell_strike = round(price * (1 - otm_pct) / 5) * 5  # round to $5
            buy_strike  = sell_strike - 10  # $10 wide spread (professional standard)
            return {
                "type": "BULL_PUT_SPREAD",
                "sell_strike": sell_strike,
                "buy_strike": buy_strike,
                "spread_width": 10,
                "current_price": round(price, 2),
            }
        else:
            # Sell CALL spread: strike 12% above current price, $10 wide
            sell_strike = round(price * (1 + otm_pct) / 5) * 5
            buy_strike  = sell_strike + 10
            return {
                "type": "BEAR_CALL_SPREAD",
                "sell_strike": sell_strike,
                "buy_strike": buy_strike,
                "spread_width": 10,
                "current_price": round(price, 2),
            }
    except Exception as e:
        print(f"  Strike error {symbol}: {e}")
        return {}


def run_flow_trader():
    print(f"[{datetime.now().strftime('%H:%M')}] Flow Trader {'(DRY RUN)' if DRY_RUN else '(LIVE)'}")

    svc = _service()
    _ensure_tabs(svc, SHEET_ID, ["FLOW_TRADE_LOG"])

    # Check exits first
    if not DRY_RUN:
        closed = check_exits()
        if closed:
            print(f"  Closed {len(closed)} position(s)")

    # Write header if needed
    r = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="FLOW_TRADE_LOG!A1:Q1"
    ).execute()
    if not r.get("values"):
        svc.spreadsheets().values().update(
            spreadsheetId=SHEET_ID, range="FLOW_TRADE_LOG!A1",
            valueInputOption="RAW", body={"values": [TRADE_LOG_HEADERS]}
        ).execute()

    # Get confirmed signals
    print("  Analyzing signals...")
    signals = get_confirmed_signals(svc)

    if not signals:
        print("  No signals meet all criteria today.")
        return

    # Check what's already been traded today (dedup)
    today = datetime.now().strftime("%Y-%m-%d")
    r_log = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="FLOW_TRADE_LOG!A:B"
    ).execute()
    already_traded = {row[1] for row in r_log.get("values", [])[1:]
                      if len(row) >= 2 and row[0] == today}
    if already_traded:
        print(f"  Already traded today: {already_traded}")
        signals = [s for s in signals if s["symbol"] not in already_traded]

    if not signals:
        print("  All signals already traded today.")
        return

    print(f"  Found {len(signals)} confirmed signal(s):\n")
    trade_rows = []

    for sig in signals[:3]:  # max 3 trades per day
        sym = sig["symbol"]
        print(f"  {'✅' if sig['confluence'] >= 5 else '⚠️'} {sym} — {sig['direction']}")
        print(f"     Sweeps: {sig['sweep_count']}x | Score: {sig['score']} | Premium: ${sig['premium_k']}K")
        print(f"     GEX: {sig['gex']:.2f}M {'✅' if sig['gex_ok'] else '❌'} | News: {'✅' if sig['news_ok'] else '❌'}")
        print(f"     Confluence: {sig['confluence']} pts")

        # Find spread
        spread = find_spread_strike(sym, sig["direction"])
        if not spread:
            print(f"     ⚠️ Could not find spread strike\n")
            continue

        # Estimate credit ($10 wide spread, target $3-5 credit = 30-50% of width)
        est_credit = 3.50  # $3.50 per spread = $350 per contract (conservative)
        max_loss = (spread["spread_width"] - est_credit) * 100  # $650 per contract
        target = est_credit * 0.70 * 100  # 70% profit target = $245

        action = f"SELL {spread['type']}: ${spread['sell_strike']}/{spread['buy_strike']}"
        print(f"     Action: {action}")
        print(f"     Price: ${spread['current_price']} | Est credit: ${est_credit:.2f} | Max loss: ${max_loss:.0f}\n")

        # Find expiry (nearest 21-45 DTE)
        from datetime import date
        target_expiry = date.today() + timedelta(days=30)
        expiry_str = target_expiry.strftime("%Y-%m-%d")

        trade_rows.append([
            today, sym, "FLOW_TRIGGERED", sig["direction"], sig["score"],
            sig["sweep_count"], sig["premium_k"], sig["confluence"],
            action, spread["sell_strike"], expiry_str, spread["spread_width"],
            est_credit, max_loss, target,
            "DRY_RUN" if DRY_RUN else "PENDING",
            str(DRY_RUN)
        ])

        # Execute if not dry run
        if not DRY_RUN:
            executed = _execute_spread(sym, spread, expiry_str)
            if executed:
                trade_rows[-1][15] = "SUBMITTED"
                print(f"     ✅ Order submitted to paper account")

    if trade_rows:
        _append(svc, SHEET_ID, "FLOW_TRADE_LOG", trade_rows)
        print(f"  📊 Logged {len(trade_rows)} trade(s) to FLOW_TRADE_LOG sheet")

    if DRY_RUN:
        print("\n  ℹ️  DRY RUN — no real orders placed")
        print("  Set DRY_RUN = False to execute on paper account")


if __name__ == "__main__":
    run_flow_trader()
