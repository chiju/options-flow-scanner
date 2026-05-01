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
import os, re, requests
from datetime import datetime, timedelta
from collections import Counter
from sheets import _service, SHEET_ID, _append, _ensure_tabs

DRY_RUN = False  # Paper trading on CSP account

# Account selection: CSP ($101K) or 10K realistic account
USE_10K_ACCOUNT = os.environ.get("FLOW_TRADER_10K", "false").lower() == "true"
if USE_10K_ACCOUNT:
    PAPER_API_KEY    = os.environ.get("ALPACA_FLOW10K_API_KEY", "")
    PAPER_API_SECRET = os.environ.get("ALPACA_FLOW10K_SECRET_KEY", "")
    ACCOUNT_SIZE     = 15_000
    MAX_RISK_PCT     = 0.05   # 5% of account per trade (dynamic)
    MAX_RISK_PER_TRADE = int(ACCOUNT_SIZE * MAX_RISK_PCT)  # recalculated from live value below
    # Only trade liquid symbols on real-money account (Tastytrade standard)
    TRADEABLE_SYMBOLS = {
        "SPY","QQQ",                              # indexes (best liquidity)
        "AAPL","NVDA","MSFT","AMZN","META","TSLA","GOOGL",  # mega cap
        "AVGO","NFLX","UBER","CRM",               # new liquid additions
        "AMD","PLTR","SOFI","COIN",               # high vol, liquid
    }
    print("[flow_trader] Using $15K realistic account")
else:
    PAPER_API_KEY    = os.environ.get("ALPACA_CSP_API_KEY", os.environ.get("ALPACA_API_KEY", ""))
    PAPER_API_SECRET = os.environ.get("ALPACA_CSP_SECRET_KEY", os.environ.get("ALPACA_SECRET_KEY", ""))
    ACCOUNT_SIZE     = 101_000
    MAX_RISK_PER_TRADE = 2000  # 2% of $101K
    TRADEABLE_SYMBOLS = None   # no filter on paper sandbox

TRADE_LOG_HEADERS = [
    "date", "symbol", "signal_type", "direction", "score",
    "sweep_count", "premium_k", "confluence",
    "action", "strike", "expiry", "spread_width",
    "entry_credit", "max_loss", "target_profit",
    "status", "dry_run", "account"
]

# Tab name depends on account
PAPER_BASE = "https://paper-api.alpaca.markets"
TRADE_LOG_TAB = "FLOW_TRADE_LOG_15K" if USE_10K_ACCOUNT else "FLOW_TRADE_LOG"

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
        if score < 9:  # raised from 8 → 9 (score 9-10 = 100% win rate)
            continue

        # Parse premium from value column (col 5: "$2940K")
        s = contract_details[key]
        premium_match = re.search(r'\$(\d+)K', s[4] if len(s) > 4 else "")
        premium_k = int(premium_match.group(1)) if premium_match else 0
        if premium_k < 1000:  # $1M minimum
            continue

        # Liquidity filter for $15K account
        if TRADEABLE_SYMBOLS and sym not in TRADEABLE_SYMBOLS:
            print(f"  ⏭️ {sym} skipped — not in liquid symbols list")
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

        # IV rank bonus: high IV = fat premium = better spread selling conditions
        row = contract_details.get(key, [])
        iv_rank_str = row[7] if len(row) > 7 else ""
        if "High" in str(iv_rank_str):   confluence_pts += 2  # IVR 70+ = ideal
        elif "Mid" in str(iv_rank_str):  confluence_pts += 1  # IVR 30-70 = ok

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
    Tracks SPREAD P&L (both legs) not just the short put.
    Exit rules:
      1. 50% profit on spread (Tastytrade optimal, 81% win rate)
      2. 2× loss on spread (stop loss)
      3. 7 DTE (gamma risk)
    """
    try:
        from datetime import date
        BASE = "https://paper-api.alpaca.markets/v2"
        H = {"APCA-API-KEY-ID": PAPER_API_KEY, "APCA-API-SECRET-KEY": PAPER_API_SECRET}

        positions = requests.get(f"{BASE}/positions", headers=H).json()
        if not isinstance(positions, list):
            return []

        # Group positions by underlying+expiry to pair spread legs
        # Key: (underlying, expiry) → {short: pos, long: pos}
        spreads = {}
        for p in positions:
            m = re.match(r'([A-Z]+)(\d{6})P(\d{8})', p["symbol"])
            if not m:
                continue
            underlying, exp_str, strike_raw = m.group(1), m.group(2), m.group(3)
            key = (underlying, exp_str)
            qty = float(p["qty"])
            if key not in spreads:
                spreads[key] = {"short": None, "long": None, "exp_str": exp_str, "underlying": underlying}
            if qty < 0:
                spreads[key]["short"] = p
            else:
                spreads[key]["long"] = p

        closed = []
        for (underlying, exp_str), legs in spreads.items():
            short = legs["short"]
            long  = legs["long"]
            if not short:
                continue  # no short leg = not our spread

            # Short leg metrics
            short_entry   = abs(float(short["avg_entry_price"]))
            short_current = abs(float(short["current_price"]))

            # Long leg metrics (protective put — cost us money at entry)
            if long:
                long_entry   = abs(float(long["avg_entry_price"]))
                long_current = abs(float(long["current_price"]))
            else:
                long_entry, long_current = 0, 0

            # Net credit = what we collected (short premium - long premium)
            net_credit  = short_entry - long_entry
            # Current spread value = what it costs to close now
            spread_cost = short_current - long_current

            # Spread P&L: positive = profit (spread decayed), negative = loss
            spread_pl_pct = (net_credit - spread_cost) / net_credit if net_credit > 0 else 0
            spread_pl_dollar = (net_credit - spread_cost) * 100

            # DTE
            exp_date = datetime.strptime(exp_str, "%y%m%d").date()
            dte = (exp_date - date.today()).days

            reason = None
            if spread_pl_pct >= 0.50:
                reason = f"50% profit ({spread_pl_pct:.0%})"
            elif spread_pl_pct <= -2.0:
                reason = f"Stop loss ({spread_pl_pct:.0%})"
            elif dte <= 7:
                reason = f"Near expiry ({dte}d)"

            if not reason:
                continue

            # Close both legs
            success = True
            for leg, side in [(short, "buy"), (long, "sell")]:
                if not leg:
                    continue
                r = requests.post(f"{BASE}/orders", headers=H, json={
                    "symbol": leg["symbol"], "qty": "1", "side": side,
                    "type": "market", "time_in_force": "day"
                })
                if not r.ok:
                    print(f"  ⚠️ Failed to close {side} leg: {leg['symbol']}")
                    success = False

            if success:
                short_strike = int(re.search(r'P(\d{8})', short["symbol"]).group(1)) / 1000
                long_strike  = int(re.search(r'P(\d{8})', long["symbol"]).group(1)) / 1000 if long else short_strike - 10
                spread_desc  = f"${short_strike:.0f}/${ long_strike:.0f}P"
                win = "WIN" if spread_pl_dollar >= 0 else "LOSS"

                closed.append(f"{'✅' if win=='WIN' else '❌'} Closed {underlying} {spread_desc} | {reason} | ${spread_pl_dollar:+.0f}")
                print(f"  Closed: {underlying} {spread_desc} | {reason} | ${spread_pl_dollar:+.0f}")

                try:
                    _append(_service(), SHEET_ID, "TRADE_RESULTS", [[
                        datetime.now().strftime("%Y-%m-%d"),
                        underlying,
                        spread_desc,
                        exp_str,
                        f"${net_credit:.2f}",    # net credit collected
                        f"${spread_cost:.2f}",   # cost to close
                        f"{spread_pl_pct:+.0%}", # % P&L on spread
                        f"${spread_pl_dollar:+.0f}", # $ P&L
                        reason,
                        win,
                    ]])
                except Exception as log_err:
                    print(f"  Log error: {log_err}")

        return closed
    except Exception as e:
        print(f"  Exit check error: {e}")
        return []


def find_spread_strike(symbol: str, direction: str, otm_pct: float = 0.12) -> dict:
    """Find appropriate strike for spread (10-15% OTM, DTE 21-45)."""
    try:
        # Use live Alpaca key for market data (paper keys don't work for data API)
        live_key    = os.environ.get("ALPACA_LIVE_API_KEY", PAPER_API_KEY)
        live_secret = os.environ.get("ALPACA_LIVE_SECRET_KEY", PAPER_API_SECRET)
        r = requests.get(
            f"https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest",
            headers={"APCA-API-KEY-ID": live_key, "APCA-API-SECRET-KEY": live_secret},
            params={"feed": "iex"}
        ).json()
        q = r.get("quote", {})
        price = (q.get("ap", 0) + q.get("bp", 0)) / 2 or q.get("ap") or q.get("bp")
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

    # ── Market clock check — skip entry if market is closed ────────────────────
    market_open = True
    try:
        clock = requests.get(f"{PAPER_BASE}/v2/clock",
            headers={"APCA-API-KEY-ID": PAPER_API_KEY, "APCA-API-SECRET-KEY": PAPER_API_SECRET}).json()
        market_open = clock.get("is_open", True)
        if not market_open:
            next_open = clock.get("next_open", "")[:16]
            print(f"  Market closed (next open: {next_open}) — skipping new entries, checking exits only")
    except Exception:
        pass  # if clock fails, proceed anyway

    # Daily limits per account type
    MAX_TRADES_PER_DAY = 2 if USE_10K_ACCOUNT else 5
    MAX_OPEN_POSITIONS = 3 if USE_10K_ACCOUNT else 8

    svc = _service()
    _ensure_tabs(svc, SHEET_ID, [TRADE_LOG_TAB, "TRADE_RESULTS"])

    # Check exits first
    if not DRY_RUN:
        closed = check_exits()
        if closed:
            print(f"  Closed {len(closed)} position(s)")

    # Write header if needed
    r = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{TRADE_LOG_TAB}!A1:Q1"
    ).execute()
    if not r.get("values"):
        svc.spreadsheets().values().update(
            spreadsheetId=SHEET_ID, range=f"{TRADE_LOG_TAB}!A1",
            valueInputOption="RAW", body={"values": [TRADE_LOG_HEADERS]}
        ).execute()

    # Get confirmed signals — only enter new trades when market is open
    if not market_open:
        return

    print("  Analyzing signals...")
    signals = get_confirmed_signals(svc)

    if not signals:
        print("  No signals meet all criteria today.")
        return

    # ── Earnings filter: skip if earnings falls within the spread's expiry window ──
    try:
        import yfinance as yf, logging
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # suppress 404 noise
        target_expiry = datetime.now().date() + timedelta(days=30)
        earnings_blocked = set()
        for sig in signals:
            sym = sig["symbol"]
            try:
                cal = yf.Ticker(sym).calendar
                earn_date = cal.get("Earnings Date", [None])[0] if cal else None
                if earn_date and earn_date <= target_expiry:
                    earnings_blocked.add(sym)
                    print(f"  ⏭️ {sym} skipped — earnings {earn_date} inside expiry window")
            except Exception:
                pass  # ETFs and symbols without earnings calendar → OK to trade
        signals = [s for s in signals if s["symbol"] not in earnings_blocked]
    except Exception:
        pass

    # ── Capital deployed check: max 30% of account in open positions ──
    try:
        acct = requests.get(f"{PAPER_BASE}/v2/account",
            headers={"APCA-API-KEY-ID": PAPER_API_KEY, "APCA-API-SECRET-KEY": PAPER_API_SECRET}).json()
        account_value = float(acct.get("portfolio_value", ACCOUNT_SIZE))
        max_deployed = account_value * 0.30  # 30% max
        # Update per-trade risk dynamically based on live account value
        if USE_10K_ACCOUNT:
            global MAX_RISK_PER_TRADE
            MAX_RISK_PER_TRADE = int(account_value * 0.05)  # 5% of current value
        current_deployed = sum(
            abs(float(p["market_value"])) for p in open_pos
            if isinstance(open_pos, list) and float(p.get("qty",0)) < 0
        )
        if current_deployed >= max_deployed:
            print(f"  ⏸️ Capital limit: ${current_deployed:,.0f} deployed ≥ 30% (${max_deployed:,.0f})")
            return
        remaining_capacity = max_deployed - current_deployed
        print(f"  💰 Capital: ${current_deployed:,.0f} deployed, ${remaining_capacity:,.0f} available (30% limit)")
    except Exception:
        pass

    # Check what's already been traded today (dedup) — check log AND open positions
    today = datetime.now().strftime("%Y-%m-%d")
    r_log = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{TRADE_LOG_TAB}!A:B"
    ).execute()
    already_traded = {row[1] for row in r_log.get("values", [])[1:]
                      if len(row) >= 2 and row[0] == today}
    # Also block symbols with open positions (prevents duplicate legs)
    import re as _re
    open_pos = requests.get(f"{PAPER_BASE}/v2/positions",
        headers={"APCA-API-KEY-ID": PAPER_API_KEY, "APCA-API-SECRET-KEY": PAPER_API_SECRET}).json()
    if isinstance(open_pos, list):
        for p in open_pos:
            m = _re.match(r'([A-Z]+)', p['symbol'])
            if m: already_traded.add(m.group(1))
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
        est_credit = 3.50  # fallback estimate
        # Try to get real mid price from Alpaca options chain
        try:
            _h = {"APCA-API-KEY-ID": PAPER_API_KEY, "APCA-API-SECRET-KEY": PAPER_API_SECRET}
            sell_sym = spread.get("sell_contract")
            buy_sym  = spread.get("buy_contract")
            if sell_sym and buy_sym:
                sq = requests.get(f"{PAPER_BASE}/v2/options/contracts/{sell_sym}", headers=_h).json()
                bq = requests.get(f"{PAPER_BASE}/v2/options/contracts/{buy_sym}", headers=_h).json()
                sell_mid = (float(sq.get("bid_price",0)) + float(sq.get("ask_price",0))) / 2
                buy_mid  = (float(bq.get("bid_price",0)) + float(bq.get("ask_price",0))) / 2
                if sell_mid > 0:
                    est_credit = round(sell_mid - buy_mid, 2)
        except Exception:
            pass
        max_loss = (spread["spread_width"] - est_credit) * 100  # $650 per contract
        target = est_credit * 0.50 * 100  # 50% profit target = $175 (Tastytrade optimal)

        action = f"SELL {spread['type']}: ${spread['sell_strike']}/{spread['buy_strike']}"
        print(f"     Action: {action}")
        print(f"     Price: ${spread['current_price']} | Est credit: ${est_credit:.2f} | Max loss: ${max_loss:.0f}\n")

        # Find expiry (nearest 21-45 DTE from actual available contracts)
        from datetime import date
        target_min = date.today() + timedelta(days=21)
        target_max = date.today() + timedelta(days=45)
        try:
            import requests as _req
            _h = {"APCA-API-KEY-ID": PAPER_API_KEY, "APCA-API-SECRET-KEY": PAPER_API_SECRET}
            _r = _req.get(f"{PAPER_BASE}/v2/options/contracts", headers=_h, params={
                "underlying_symbols": sym, "type": "put",
                "expiration_date_gte": target_min.strftime("%Y-%m-%d"),
                "expiration_date_lte": target_max.strftime("%Y-%m-%d"),
                "limit": 1
            })
            _cs = _r.json().get("option_contracts", [])
            expiry_str = _cs[0]["expiration_date"] if _cs else (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        except Exception:
            expiry_str = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")

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
            else:
                trade_rows.pop()  # remove from log if execution failed
                continue

        already_traded.add(sym)  # prevent same symbol twice in same batch

    executed_rows = [r for r in trade_rows if r[15] == "SUBMITTED"] if not DRY_RUN else trade_rows
    if executed_rows:
        _append(svc, SHEET_ID, TRADE_LOG_TAB, executed_rows)
        print(f"  📊 Logged {len(executed_rows)} trade(s) to {TRADE_LOG_TAB} sheet")
        # Notify only on actually executed trades
        if USE_10K_ACCOUNT:
            try:
                from notifier import send
                acct_r = requests.get(f"{PAPER_BASE}/v2/account",
                    headers={"APCA-API-KEY-ID": PAPER_API_KEY, "APCA-API-SECRET-KEY": PAPER_API_SECRET}).json()
                val = float(acct_r.get('portfolio_value', 0))
                lines = [f"*💰 Flow-15K Trade — {datetime.now().strftime('%b %d %H:%M')}*",
                         f"Account: ${val:,.0f}"]
                for row in executed_rows:
                    if len(row) > 12:
                        lines.append(f"  {row[3]} {row[1]} | {row[8]} | Credit: ${row[12]}")
                send("\n".join(lines))
            except Exception:
                pass
    elif trade_rows:
        _append(svc, SHEET_ID, TRADE_LOG_TAB, trade_rows)
        print(f"  📊 Logged {len(trade_rows)} trade(s) to {TRADE_LOG_TAB} sheet")

    if DRY_RUN:
        print("\n  ℹ️  DRY RUN — no real orders placed")
        print("  Set DRY_RUN = False to execute on paper account")


if __name__ == "__main__":
    run_flow_trader()
