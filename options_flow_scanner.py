"""
Options Flow Scanner — Smart money detector with alert scoring, dedup, price tracking,
VIX correlation, sector rotation, and pre/after-hours support.
"""
import os, sys, time, argparse
from datetime import datetime, timedelta
from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import OptionChainRequest, StockLatestQuoteRequest
import requests
from sheets import store_results
from earnings import get_earnings_this_week
from earnings_tracker import snapshot_pre_earnings, update_post_earnings
from alpaca.data.historical.screener import ScreenerClient
from alpaca.data.requests import MostActivesRequest

# ── Watchlist ─────────────────────────────────────────────────────────────────
INDEX_ETFS  = ["SPY", "QQQ", "IWM"]
SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "GLD", "TLT", "ITA",
               "USO", "UUP", "XBI", "ARKK"]  # Oil, Dollar, Biotech, Innovation
DEFENCE     = ["LMT", "RTX", "NOC", "GD"]
CYBER       = ["CRWD", "PANW", "ZS"]
PORTFOLIO   = ["MSFT","NVDA","AMZN","META","TSLA","PLTR","CRWV","IONQ","OKLO",
               "ACHR","DUOL","SOFI","PYPL","PATH","JOBY","UUUU","POET"]
MEGA_CAPS   = ["AAPL","GOOGL","MSFT","NVDA","AMZN","META","TSLA","AVGO","NFLX","UBER","CRM"]
HIGH_VOL    = ["AMD","COIN","MSTR","HOOD","SMCI","ARM","SNOW","ASTS","NBIS","RMBS","INTC","RDDT"]
SYMBOL_NAMES = {
    # Indexes
    "SPY": "S&P 500", "QQQ": "Nasdaq", "IWM": "Russell 2000",
    # Sectors
    "XLK": "Tech", "XLF": "Finance", "XLE": "Energy", "XLV": "Health",
    "GLD": "Gold", "TLT": "Bonds", "ITA": "Defence",
    "USO": "Oil", "UUP": "Dollar", "XBI": "Biotech", "ARKK": "Innovation",
    # Defence
    "LMT": "Lockheed", "RTX": "Raytheon", "NOC": "Northrop", "GD": "Gen Dynamics",
    # Cyber
    "CRWD": "CrowdStrike", "PANW": "Palo Alto", "ZS": "Zscaler",
    # Mega caps
    "AAPL": "Apple", "GOOGL": "Google", "MSFT": "Microsoft",
    "NVDA": "Nvidia", "AMZN": "Amazon", "META": "Meta", "TSLA": "Tesla",
    "AVGO": "Broadcom", "NFLX": "Netflix", "UBER": "Uber", "CRM": "Salesforce",
    # High vol
    "AMD": "AMD", "COIN": "Coinbase", "MSTR": "MicroStrategy",
    "HOOD": "Robinhood", "SMCI": "SuperMicro", "ARM": "ARM", "SNOW": "Snowflake", "ASTS": "AST SpaceMobile", "NBIS": "Nebius", "RMBS": "Rambus",
    "INTC": "Intel", "RDDT": "Reddit",
}

ALL_SYMBOLS = list(dict.fromkeys(INDEX_ETFS + SECTOR_ETFS + DEFENCE + CYBER + MEGA_CAPS + HIGH_VOL + PORTFOLIO))

# ── Thresholds ────────────────────────────────────────────────────────────────
MIN_PREMIUM       = 25000   # $25k minimum notional per contract
MAX_DTE           = 45      # days to expiry maximum
SWEEP_BLOCK_SIZE  = 500     # legacy — sweep now uses $1M notional threshold
IV_SPIKE_THRESH   = 80.0    # legacy — IV spike now uses IV rank (relative per stock)
_alerts_30d: list = []      # populated at scan start for volume baseline
MIN_ALERT_SCORE   = 7       # only send Telegram if top alert scores >= this

# ── Credentials ───────────────────────────────────────────────────────────────
def _key():    return os.environ.get("ALPACA_CSP_API_KEY") or os.environ.get("ALPACA_API_KEY", "")
def _secret(): return os.environ.get("ALPACA_CSP_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", "")
def _tg_token(): return os.environ.get("TELEGRAM_BOT_TOKEN", "")
def _tg_chat():  return os.environ.get("TELEGRAM_CHAT_ID", "")


# ── Alert Scoring (1–10) ──────────────────────────────────────────────────────
def score_alert(entry: dict) -> int:
    """
    Score an options alert 1–10 based on signal strength.

    +3  premium >= $10M
    +2  premium >= $5M  (cumulative with above — capped)
    +2  sweep (large block)
    +2  iv_spike (buying urgency, calls only)
    +2  0-7 DTE (expires this week)
    +1  8-30 DTE
    +1  OTM (delta < 0.4 for calls, > -0.4 for puts)
    """
    s = 0
    p = entry.get("premium", 0)
    if p >= 20_000_000:  s += 5   # $20M+ = massive institutional
    elif p >= 10_000_000: s += 4  # $10M+
    elif p >= 5_000_000:  s += 3  # $5M+
    elif p >= 1_000_000:  s += 2  # $1M+
    elif p >= 100_000:    s += 1  # $100K+

    if entry.get("sweep"):    s += 2
    # IV spike: use IV rank (relative to stock history) not raw IV
    # IV rank ≥70 = top 30% of that stock's own history = truly elevated
    iv_rank = entry.get("iv_rank", "")
    if iv_rank and "High" in str(iv_rank):
        if entry.get("type") == "CALL": s += 2  # high IV + call = urgency signal
    elif entry.get("iv_spike"):  # fallback to raw if no iv_rank yet
        s += 1

    # IV Rank: low IV = cheap options = better risk/reward for buyers
    iv_rank = entry.get("iv_rank", "")
    if iv_rank and "Low" in iv_rank: s += 1  # buying cheap options = better edge

    # Ascending volume: same contract growing in volume across scans = accumulation
    # Unusual Whales: "repeat action with ascending size" = strongest signal
    asc = entry.get("ascending_vol")
    if asc == "strong":  s += 3  # 3+ scans, each bigger than last
    elif asc == "weak":  s += 1  # 2 scans growing

    # Vol/OI ratio: >5x = fresh unusual positioning (not just rolling existing)
    vol_oi = entry.get("vol_oi_ratio")
    if vol_oi and vol_oi >= 10: s += 2   # 10x+ = extremely unusual
    elif vol_oi and vol_oi >= 5: s += 1  # 5x+ = unusual

    # Volume vs 30-day baseline: how unusual is today's volume historically
    vb = entry.get("vol_vs_baseline")
    if vb and vb >= 10: s += 3   # 10x+ normal = extremely unusual (informed trading signal)
    elif vb and vb >= 5: s += 2  # 5x+ normal = very unusual
    elif vb and vb >= 3: s += 1  # 3x+ normal = unusual

    dte = entry.get("dte", 99)
    if dte <= 7:    s += 2
    elif dte <= 30: s += 1

    delta = entry.get("delta")
    if delta is not None:
        if entry.get("type") == "CALL" and delta < 0.4:   s += 1
        elif entry.get("type") == "PUT" and delta > -0.4: s += 1

    # Delta-weighted signal: ATM options (delta ~0.5) = directional conviction
    # Deep ITM (delta ~1.0) = hedge, not directional — cap score
    if delta is not None:
        abs_delta = abs(delta)
        if abs_delta > 0.85:
            return min(s, 4)  # hard cap: deep ITM = hedge, max score 4
        elif 0.35 <= abs_delta <= 0.65:  s += 2  # ATM = pure directional bet

    # Theta decay signal: high theta = premium decaying fast = good time to sell spreads
    theta = entry.get("theta")
    if theta is not None and entry.get("type") == "PUT":
        abs_theta = abs(theta)
        if abs_theta >= 0.3:  s += 1  # fast decay = good spread selling opportunity

    return min(s, 10)


def get_ascending_volume(contract: str, current_vol: int, rows_30d: list) -> str:
    """
    Check if this contract's volume is ascending across recent scans.
    Uses UNUSUAL_ALERTS as filesystem (Google Sheets).
    Returns: 'strong' (3+ scans ascending), 'weak' (2 scans), '' (no pattern)
    """
    # Find last 3 appearances of this contract in today's alerts
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    contract_rows = [
        int(row[6]) for row in rows_30d
        if len(row) > 6 and row[0][:10] == today
        and len(row) > 1 and row[1] in contract  # match by symbol
        and len(row) > 3 and row[3] in contract  # match by strike
        and row[6].isdigit()
    ]
    if len(contract_rows) < 2:
        return ""
    # Check if ascending (each scan bigger than previous)
    ascending = all(contract_rows[i] < contract_rows[i+1] for i in range(len(contract_rows)-1))
    if ascending and len(contract_rows) >= 3:
        return "strong"
    elif ascending:
        return "weak"
    return ""


def get_volume_baseline(sym: str, opt_type: str, rows_30d: list) -> float | None:
    """Avg daily volume for sym/type over last 30 days. Returns None if insufficient data."""
    from collections import defaultdict
    daily = defaultdict(int)
    for row in rows_30d:
        if len(row) >= 7 and row[1] == sym and row[2] == opt_type:
            try:
                daily[row[0][:10]] += int(row[6])
            except (ValueError, IndexError):
                pass
    if len(daily) < 3:
        return None
    return sum(daily.values()) / len(daily)


def get_iv_rank(sym: str, current_iv: float, svc=None) -> str:
    """
    Calculate IV rank from last 30 days of UNUSUAL_ALERTS data.
    IV Rank = (current - min) / (max - min) × 100
    Returns: "IVR 85 🔴 High" or "IVR 20 🟢 Low" or ""
    """
    if not current_iv or not svc:
        return ""
    try:
        from sheets import _service, SHEET_ID
        if svc is None:
            svc = _service()
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        r = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range="UNUSUAL_ALERTS!A:I"
        ).execute()
        rows = r.get("values", [])[1:]
        ivs = []
        for row in rows:
            if len(row) >= 9 and row[1] == sym and row[0][:10] >= cutoff:
                try:
                    iv = float(row[8])
                    if iv > 0:
                        ivs.append(iv)
                except (ValueError, IndexError):
                    pass
        if len(ivs) < 5:
            return ""
        iv_min, iv_max = min(ivs), max(ivs)
        if iv_max == iv_min:
            return ""
        rank = round((current_iv - iv_min) / (iv_max - iv_min) * 100)
        emoji = "🔴 High" if rank >= 70 else "🟢 Low" if rank <= 30 else "🟡 Mid"
        return f"IVR {rank} {emoji}"
    except Exception:
        return ""


def get_dynamic_symbols(top_n: int = 10) -> list:
    """Get most active stocks via Alpaca Screener API — adds to watchlist dynamically."""
    try:
        # Screener requires live key (not paper)
        live_key = os.environ.get("ALPACA_LIVE_API_KEY") or os.environ.get("ALPACA_API_KEY", "")
        live_secret = os.environ.get("ALPACA_LIVE_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", "")
        screener = ScreenerClient(api_key=live_key, secret_key=live_secret)
        result = screener.get_most_actives(MostActivesRequest(top=top_n))
        syms = [m.symbol for m in result.most_actives if hasattr(m, "symbol")]
        # Filter out already-tracked symbols and non-optionable (low price)
        new = [s for s in syms if s not in ALL_SYMBOLS and len(s) <= 5 and not s.endswith('W')]
        if new:
            print(f"  📡 Screener added: {', '.join(new)}")
        return new
    except Exception as e:
        print(f"  Screener error: {e}")
        return []


# ── Price Tracking ────────────────────────────────────────────────────────────
def get_current_prices(symbols: list) -> dict:
    """Fetch latest price for each symbol. Returns {sym: price}."""
    # Try Schwab first (real-time NBBO), fall back to Alpaca
    if os.environ.get("SCHWAB_APP_KEY"):
        try:
            from schwab_scanner import get_schwab_client
            sc = get_schwab_client()
            r = sc.get_quotes(symbols)
            result = {}
            for sym, data in r.json().items():
                qt = data.get("quote", {})
                bid = qt.get("bidPrice", 0) or 0
                ask = qt.get("askPrice", 0) or 0
                last = qt.get("lastPrice", 0) or 0
                price = (bid + ask) / 2 if bid and ask else last
                if price: result[sym] = round(price, 2)
            if result: return result
        except Exception:
            pass
    try:
        client = StockHistoricalDataClient(api_key=_key(), secret_key=_secret())
        req = StockLatestQuoteRequest(symbol_or_symbols=symbols)
        quotes = client.get_stock_latest_quote(req)
        return {sym: round((q.bid_price + q.ask_price) / 2, 2)
                for sym, q in quotes.items() if q.bid_price and q.ask_price}
    except Exception as e:
        print(f"  Price fetch error: {e}")
        return {}


def get_price_changes(symbols: list) -> dict:
    """Fetch 1d % price change. Uses Schwab if available, else yfinance."""
    import pytz
    et_now = datetime.now(pytz.timezone('America/New_York'))
    et_mins = et_now.hour * 60 + et_now.minute
    market_open = et_now.weekday() < 5 and 570 <= et_mins < 960
    suffix = " (live)" if market_open else " (close)"

    # Try Schwab first
    if os.environ.get("SCHWAB_APP_KEY"):
        try:
            from schwab_scanner import get_schwab_client
            sc = get_schwab_client()
            r = sc.get_quotes(symbols)
            result = {}
            for sym, data in r.json().items():
                qt = data.get("quote", {})
                chg = qt.get("netPercentChange")
                if chg is not None:
                    chg_pct = round(float(chg), 2)  # Schwab returns % already (1.35 = 1.35%)
                    result[sym] = chg_pct
                    result[f"{sym}_display"] = f"{chg_pct:+.2f}%{suffix}"
            if result: return result
        except Exception:
            pass

    # Fall back to yfinance
    try:
        import yfinance as yf
        result = {}
        tickers = yf.download(symbols, period="2d", progress=False, auto_adjust=True)
        close = tickers["Close"]
        if hasattr(close, "columns"):
            for sym in symbols:
                try:
                    s = close[sym].dropna()
                    if len(s) >= 2:
                        chg = round((s.iloc[-1] - s.iloc[-2]) / s.iloc[-2] * 100, 2)
                        result[sym] = chg
                        result[f"{sym}_display"] = f"{chg:+.2f}%{suffix}"
                except Exception:
                    pass
        else:
            s = close.dropna()
            if len(s) >= 2:
                chg = round((s.iloc[-1] - s.iloc[-2]) / s.iloc[-2] * 100, 2)
                result[symbols[0]] = chg
                result[f"{symbols[0]}_display"] = f"{chg:+.2f}%{suffix}"
        return result
    except Exception as e:
        print(f"  Price change error: {e}")
        return {}


# ── VIX Fetch ─────────────────────────────────────────────────────────────────
def get_vix() -> float | None:
    """Fetch VIX from Yahoo Finance (free)."""
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=1d",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=5
        )
        data = r.json()
        price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
        return round(price, 2)
    except Exception:
        return None


# ── Core Scanner ──────────────────────────────────────────────────────────────
def scan_symbol(client: OptionHistoricalDataClient, sym: str) -> dict | None:
    today  = datetime.now().date()
    cutoff = today + timedelta(days=MAX_DTE)
    try:
        chain = client.get_option_chain(OptionChainRequest(
            underlying_symbol=sym,
            expiration_date_gte=today,
            expiration_date_lte=cutoff,
        ))
    except Exception as e:
        print(f"  [{sym}] error: {e}")
        return None

    calls, puts = [], []
    total_call_vol = total_put_vol = 0

    for contract_sym, snap in chain.items():
        if snap.latest_quote is None:
            continue
        try:
            offset     = len(sym)
            cp         = contract_sym[offset + 6]
            strike     = int(contract_sym[offset + 7:]) / 1000
            expiry_str = contract_sym[offset:offset + 6]
            expiry_date = datetime.strptime(expiry_str, "%y%m%d").date()
            expiry_fmt  = expiry_date.strftime("%b %d")
            dte         = (expiry_date - today).days
        except Exception:
            continue

        bid = snap.latest_quote.bid_price or 0
        ask = snap.latest_quote.ask_price or 0
        mid = (bid + ask) / 2 if ask else bid

        volume = snap.latest_trade.size if snap.latest_trade and snap.latest_trade.size else 0
        delta  = snap.greeks.delta if snap.greeks else None
        iv     = snap.implied_volatility

        if cp == "C": total_call_vol += volume
        else:         total_put_vol  += volume

        if volume == 0 or mid == 0: continue
        premium = mid * volume * 100
        if premium < MIN_PREMIUM:   continue

        iv_pct    = round(iv * 100, 1) if iv else None
        # IV spike: use IV rank (relative) not raw IV (absolute)
        # Raw 80% is wrong: IONQ always >80%, SPY rarely >80%
        # Will be updated to iv_rank-based after iv_rank is calculated
        iv_spike  = bool(iv_pct and iv_pct > IV_SPIKE_THRESH and cp == "C")
        # Notional-based sweep: $1M+ in a single block = institutional regardless of stock price
        # Better than flat 500 contracts (500 SOFI = $800K vs 500 SPY = $35M)
        notional  = mid * volume * 100
        sweep     = notional >= 1_000_000 and cp == "C"

        # OI not in Alpaca chain — populated from OI_SNAPSHOT (oi_tracker.py EOD)
        oi = 0
        vol_oi_ratio = None

        # Volume vs 30-day baseline
        opt_type = "CALL" if cp == "C" else "PUT"
        baseline = get_volume_baseline(sym, opt_type, _alerts_30d)
        vol_vs_baseline = round(volume / baseline, 1) if baseline and baseline > 0 else None
        ascending_vol = get_ascending_volume(contract_sym, volume, _alerts_30d)

        # Mid-price rule: trade at/above mid = buyer aggressive (BUY), below = seller (SELL)
        buy_sell = ""
        if snap.latest_trade and mid > 0:
            last = snap.latest_trade.price or 0
            buy_sell = "BUY" if last >= mid else "SELL"

        entry = {
            "symbol": sym, "contract": contract_sym,
            "type": "CALL" if cp == "C" else "PUT",
            "strike": strike, "expiry": expiry_fmt, "dte": dte,
            "volume": int(volume), "premium": int(premium),
            "delta": round(delta, 2) if delta else None,
            "gamma": round(snap.greeks.gamma, 4) if snap.greeks and snap.greeks.gamma else None,
            "iv": iv_pct, "mid": round(mid, 2),
            "oi": oi, "vol_oi_ratio": vol_oi_ratio,
            "sweep": sweep, "iv_spike": iv_spike, "buy_sell": buy_sell,
            "vol_vs_baseline": vol_vs_baseline,
            "ascending_vol": ascending_vol,
        }
        entry["score"] = score_alert(entry)

        if cp == "C": calls.append(entry)
        else:         puts.append(entry)

    pc_ratio = round(total_put_vol / total_call_vol, 2) if total_call_vol > 0 else None
    return {
        "symbol":   sym,
        "calls":    sorted(calls, key=lambda x: x["premium"], reverse=True),
        "puts":     sorted(puts,  key=lambda x: x["premium"], reverse=True),
        "pc_ratio": pc_ratio,
        "call_vol": int(total_call_vol),
        "put_vol":  int(total_put_vol),
    }


def net_premium_sentiment(results: list) -> list:
    """
    Net premium = total call $ - total put $ per symbol.
    Stronger signal than P/C ratio — weights by dollar size not contract count.
    Returns list of (symbol, net_k, signal) sorted by absolute net value.
    """
    out = []
    for r in results:
        call_k = sum(e["premium"] for e in r["calls"]) // 1000
        put_k  = sum(e["premium"] for e in r["puts"])  // 1000
        net_k  = call_k - put_k
        if abs(net_k) < 500:  # ignore if less than $500K net
            continue
        sig = "🐂 Bullish" if net_k > 0 else "🐻 Bearish"
        out.append((r["symbol"], net_k, sig, call_k, put_k))
    return sorted(out, key=lambda x: abs(x[1]), reverse=True)


def golden_flow(results: list) -> list:
    """
    Golden Flow = premium >= $1M + sweep + score >= 8.
    Highest conviction institutional signal — all three conditions must align.
    """
    hits = []
    for r in results:
        for entry in r["calls"] + r["puts"]:
            if (entry["premium"] >= 1_000_000
                    and entry.get("sweep")
                    and entry.get("score", 0) >= 8):
                entry["_sym"] = r["symbol"]
                hits.append(entry)
    return sorted(hits, key=lambda x: x["premium"], reverse=True)


def interpret_signal(result: dict) -> str:
    pc = result["pc_ratio"]
    if pc is None:  return "⚪ No data"
    if pc < 0.3:    return "🔥 Very Bullish"
    if pc < 0.6:    return "🟢 Bullish"
    if pc < 1.0:    return "🟡 Neutral"
    if pc < 1.5:    return "🟠 Cautious"
    return "🔴 Bearish"
    pc = result["pc_ratio"]
    if pc is None:  return "⚪ No data"
    if pc < 0.3:    return "🔥 Very Bullish"
    if pc < 0.6:    return "🟢 Bullish"
    if pc < 1.0:    return "🟡 Neutral"
    if pc < 1.5:    return "🟠 Cautious"
    return "🔴 Bearish"


# ── Sector Rotation ───────────────────────────────────────────────────────────
def sector_rotation_signal(results: list) -> str:
    """Detect money rotating between sectors based on P/C ratios."""
    sectors = {"XLK": "Tech", "XLF": "Finance", "XLE": "Energy",
               "XLV": "Health", "GLD": "Gold", "TLT": "Bonds"}
    bullish, bearish = [], []
    for sym, name in sectors.items():
        r = next((x for x in results if x["symbol"] == sym), None)
        if not r or not r["pc_ratio"]: continue
        if r["pc_ratio"] < 0.6:   bullish.append(name)
        elif r["pc_ratio"] > 1.5: bearish.append(name)

    if bullish and bearish:
        return f"🔄 Rotation: into {', '.join(bullish)} | out of {', '.join(bearish)}"
    elif bullish:
        return f"🟢 Sector buying: {', '.join(bullish)}"
    elif bearish:
        return f"🔴 Sector hedging: {', '.join(bearish)}"
    return ""


# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(text: str):
    from notifier import send as _send
    _send(text)


# ── Confluence Score ──────────────────────────────────────────────────────────
def confluence_score(sym: str, flow_direction: str, results: list, gamma_data: dict = None, news_data: dict = None) -> str:
    """
    Score alignment across 3 independent signals:
      1. Options flow (P/C ratio direction)
      2. News sentiment (positive/negative)
      3. GEX regime (positive=pinned, negative=trending)

    Returns a string like: "⭐⭐⭐ HIGH (flow🐂 + news🟢 + gex🔴)"
    """
    score = 0
    parts = []

    # 1. Flow signal
    r = next((x for x in results if x["symbol"] == sym), None)
    if r and r.get("pc_ratio"):
        pc = r["pc_ratio"]
        flow_bull = pc < 0.7
        flow_bear = pc > 1.3
        if flow_direction == "CALL" and flow_bull:
            score += 1; parts.append("flow🐂")
        elif flow_direction == "PUT" and flow_bear:
            score += 1; parts.append("flow🐻")

    # 2. News sentiment
    if news_data and sym in news_data:
        n = news_data[sym]
        if flow_direction == "CALL" and n["positive"] > n["negative"]:
            score += 1; parts.append("news🟢")
        elif flow_direction == "PUT" and n["negative"] > n["positive"]:
            score += 1; parts.append("news🔴")
        # Reddit buzz as bonus (same direction)
        reddit = n.get("reddit", {})
        if reddit and flow_direction == "CALL" and reddit.get("bullish", 0) > reddit.get("bearish", 0):
            score += 1; parts.append("reddit🟢")
        elif reddit and flow_direction == "PUT" and reddit.get("bearish", 0) > reddit.get("bullish", 0):
            score += 1; parts.append("reddit🔴")

    # 3. GEX regime (negative GEX = trending = good for directional bets)
    if gamma_data and sym in gamma_data:
        gex = gamma_data[sym]
        if gex < 0:  # negative GEX = MMs amplify moves = directional bets work better
            score += 1; parts.append("gex🔴trending")

    if score == 3:   return f"⭐⭐⭐ *HIGH* ({' + '.join(parts)})"
    elif score == 2: return f"⭐⭐ Medium ({' + '.join(parts)})"
    elif score == 1: return f"⭐ Low ({' + '.join(parts)})"
    return ""


# ── Formatter ─────────────────────────────────────────────────────────────────
def format_report(results: list, earnings: dict = None,
                  vix: float = None, momentum: list = None,
                  gamma_data: dict = None, news_data: dict = None) -> str:
    now = datetime.now().strftime("%b %d %H:%M")
    lines = [f"*📊 Options Flow — {now}*", ""]

    # Market mood + VIX
    spy = next((r for r in results if r["symbol"] == "SPY"), None)
    qqq = next((r for r in results if r["symbol"] == "QQQ"), None)
    if spy and qqq:
        lines.append(f"*Market Mood:* {interpret_signal(spy)}")
        vix_str = f"  VIX `{vix}`{'🔴' if vix and vix > 25 else '🟢' if vix and vix < 15 else '🟡'}" if vix else ""
        lines.append(f"SPY P/C `{spy['pc_ratio']}` | QQQ P/C `{qqq['pc_ratio']}`{vix_str}")

    # Sector snapshot
    sector_line = []
    for sym in ["XLK", "XLF", "XLE", "GLD", "TLT", "ITA", "USO", "UUP", "XBI"]:
        r = next((x for x in results if x["symbol"] == sym), None)
        if r and r["pc_ratio"]:
            sig = "🟢" if r["pc_ratio"] < 0.7 else ("🔴" if r["pc_ratio"] > 1.5 else "🟡")
            name = SYMBOL_NAMES.get(sym, sym)
            sector_line.append(f"{name}{sig}{r['pc_ratio']}")
    if sector_line:
        lines.append("  ".join(sector_line))

    # Sector rotation
    rotation = sector_rotation_signal(results)
    if rotation:
        lines.append(rotation)
    lines.append("")

    # ── Golden Flow (highest conviction: sweep + score≥8 + $1M+) ──
    gf = golden_flow(results)
    if gf:
        lines.append("*⭐ Golden Flow* _(sweep + score≥8 + $1M+)_")
        for f in gf[:5]:
            side = "🐂 CALL" if f["type"] == "CALL" else "🐻 PUT"
            conf = confluence_score(f["_sym"], f["type"], results, gamma_data, news_data)
            conf_str = f"\n  └ {conf}" if conf else ""
            lines.append(
                f"{side} *{f['_sym']}* ${f['strike']:.0f} {f['expiry']}"
                f"  ⭐{f['score']}  💰 *${f['premium']//1000}K* 🚨{conf_str}"
            )
        lines.append("")

    # ── Net Premium Sentiment ──
    net = net_premium_sentiment(results)
    if net:
        lines.append("*💵 Net Premium* _(call $ minus put $)_")
        for sym, net_k, sig, call_k, put_k in net[:8]:
            bar  = "+" if net_k > 0 else ""
            name = SYMBOL_NAMES.get(sym, sym)
            lines.append(f"  `{sym}` {name} {sig}  {bar}${net_k:,}K")
        lines.append("")

    # Top flows — only show score >= MIN_ALERT_SCORE
    all_unusual = []
    for r in results:
        for entry in r["calls"][:3] + r["puts"][:3]:
            if entry["volume"] >= 200 and entry["premium"] >= MIN_PREMIUM:
                entry["_sym"] = r["symbol"]
                all_unusual.append(entry)
    all_unusual.sort(key=lambda x: x["premium"], reverse=True)

    high_score = [f for f in all_unusual if f.get("score", 0) >= MIN_ALERT_SCORE]
    show_flows = high_score[:10] if high_score else all_unusual[:5]  # fallback if nothing scores high

    if show_flows:
        lines.append("*🐳 Smart Money Flows* _(score ≥ 7)_")
        for f in show_flows:
            side  = "🐂 CALL" if f["type"] == "CALL" else "🐻 PUT"
            tags  = (" 🚨" if f.get("sweep") else "") + (" ⚡" if f.get("iv_spike") else "")
            iv_s  = f"  IV{f['iv']}%" if f["iv"] else ""
            score = f"  ⭐{f.get('score','?')}"
            bs    = f"  {'📈BUY' if f.get('buy_sell')=='BUY' else '📉SELL' if f.get('buy_sell')=='SELL' else ''}"
            name  = SYMBOL_NAMES.get(f["_sym"], f["_sym"])
            lines.append(
                f"{side} *{f['_sym']}* ({name}) ${f['strike']:.0f} {f['expiry']}"
                f"  Vol {f['volume']:,}{iv_s}{score}{bs}"
                f"  💰 *${f['premium']//1000}K*{tags}"
            )
        lines.append("")

    # Portfolio
    lines.append("*💼 Your Portfolio*")
    bull, bear, neutral = [], [], []
    for r in results:
        if r["symbol"] not in PORTFOLIO or (r["call_vol"] == 0 and r["put_vol"] == 0):
            continue
        sig = interpret_signal(r)
        if "Bullish" in sig:   bull.append((r["symbol"], r["pc_ratio"]))
        elif "Bearish" in sig: bear.append((r["symbol"], r["pc_ratio"]))
        else:                  neutral.append((r["symbol"], r["pc_ratio"]))

    if bull:    lines.append("_Bullish:_  " + "  ".join(f"`{s}` {p}" for s,p in bull))
    if neutral: lines.append("_Neutral:_  " + "  ".join(f"`{s}` {p}" for s,p in neutral))
    if bear:    lines.append("_Bearish:_  " + "  ".join(f"`{s}` {p}" for s,p in bear))
    lines.append("")

    # Momentum
    if momentum:
        lines.append("*🔁 Momentum*")
        lines.extend(momentum[:5])
        lines.append("")

    # Earnings
    if earnings:
        lines.append("*📅 Earnings This Week*")
        for sym, date in earnings.items():
            lines.append(f"  `{sym}` reports {date}")
        lines.append("")

    lines.append("_P/C<0.6=bullish · >1.5=bearish · Not financial advice_")
    return "\n".join(lines)


# ── Duplicate Suppression ─────────────────────────────────────────────────────
_last_top_flows: set = set()
_alerted_today: dict = {"date": "", "contracts": set()}

def has_new_signals(results: list) -> bool:
    global _last_top_flows
    current = set()
    for r in results:
        for entry in r["calls"][:2] + r["puts"][:2]:
            if entry.get("score", 0) >= MIN_ALERT_SCORE:
                current.add(f"{r['symbol']}-{entry['type']}-{entry['strike']}-{entry['expiry']}")
    changed = current != _last_top_flows
    _last_top_flows = current
    return changed


def filter_new_golden_flow(gf: list) -> list:
    """Only return Golden Flow contracts not yet alerted in last 4 hours (sheet-based dedup)."""
    try:
        from sheets import _service, SHEET_ID
        svc = _service()
        r = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="SIGNAL_HISTORY!A:D").execute()
        cutoff = (datetime.now() - timedelta(hours=4)).strftime("%Y-%m-%d %H:%M")
        alerted = {row[3] for row in r.get("values",[])[1:]
                   if len(row)>=4 and row[1]=="GOLDEN_FLOW" and row[0] >= cutoff}
    except Exception:
        alerted = set()
    new = []
    for f in gf:
        key = f"{f['_sym']}-{f['type']}-{f['strike']}-{f['expiry']}"
        if key not in alerted:
            new.append(f)
    return new


def is_market_open() -> bool:
    """Check market status via Alpaca clock API."""
    try:
        # Try CSP key first (always active), fall back to main key
        key = os.environ.get("ALPACA_CSP_API_KEY") or _key()
        secret = os.environ.get("ALPACA_CSP_SECRET_KEY") or _secret()
        r = requests.get(
            "https://paper-api.alpaca.markets/v2/clock",
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            timeout=5
        )
        return r.json().get("is_open", False)
    except Exception:
        return True  # assume open if API fails


# ── Main ──────────────────────────────────────────────────────────────────────
def run_scan(force_send: bool = False):
    if not force_send and not is_market_open():
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Market closed — skipping scan.")
        return

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Scanning {len(ALL_SYMBOLS)} symbols...")
    client = OptionHistoricalDataClient(api_key=_key(), secret_key=_secret())

    # Fetch last 30 days of alerts once for volume baseline comparison
    global _alerts_30d
    try:
        from sheets import _service, SHEET_ID
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        _r = _service().spreadsheets().values().get(spreadsheetId=SHEET_ID, range="UNUSUAL_ALERTS!A:G").execute()
        _alerts_30d = [row for row in _r.get("values", [])[1:] if row and row[0][:10] >= cutoff]
        print(f"  📊 Loaded {len(_alerts_30d)} alerts for baseline comparison")
    except Exception as e:
        _alerts_30d = []
        print(f"  ⚠️ Baseline load failed: {e}")

    # Use Schwab for real Greeks+OI if available, else Alpaca
    use_schwab = bool(os.environ.get("SCHWAB_APP_KEY"))
    if use_schwab:
        try:
            from schwab_token_store import load_token, save_token
            load_token()  # pull latest token from Google Sheets
            from schwab_scanner import get_schwab_client, scan_symbol_schwab
            schwab_c = get_schwab_client()
            print(f"  📡 Using Schwab API (real Greeks + OI)")
        except Exception as e:
            use_schwab = False
            print(f"  ⚠️ Schwab unavailable ({e}), falling back to Alpaca")

    # Dynamically add most active stocks from Screener API
    dynamic = get_dynamic_symbols(top_n=10)
    scan_list = ALL_SYMBOLS + dynamic

    results = []
    for sym in scan_list:
        if use_schwab:
            r = scan_symbol_schwab(schwab_c, sym, _alerts_30d)
        else:
            r = scan_symbol(client, sym)
        if r:
            results.append(r)
    print()

    if not results:
        print("No results.")
        return

    # Fetch VIX + prices + earnings in parallel-ish
    vix           = get_vix()
    prices        = get_current_prices([r["symbol"] for r in results])
    price_changes = get_price_changes([r["symbol"] for r in results])
    # Only fetch earnings once per day (not every 15-min scan)
    from datetime import date as _date
    _earnings_cache_date = getattr(run_scan, '_earnings_date', None)
    if _earnings_cache_date != _date.today():
        earnings = get_earnings_this_week(ALL_SYMBOLS, days_ahead=30)
        run_scan._earnings_date = _date.today()
        run_scan._earnings_cache = earnings
        # Update EARNINGS_CALENDAR sheet
        try:
            from sheets import _service, SHEET_ID, SYMBOL_NAMES
            _svc = _service()
            from datetime import datetime as _dt
            rows = [["symbol", "name", "earnings_date", "days_away"]]
            today = _date.today()
            for sym, date_str in sorted(earnings.items(), key=lambda x: x[1]):
                name = SYMBOL_NAMES.get(sym, sym)
                earn_date = _dt.strptime(f"2026 {date_str}", "%Y %b %d").date()
                rows.append([sym, name, date_str, (earn_date - today).days])
            _svc.spreadsheets().values().clear(spreadsheetId=SHEET_ID, range="EARNINGS_CALENDAR!A:Z").execute()
            _svc.spreadsheets().values().update(spreadsheetId=SHEET_ID, range="EARNINGS_CALENDAR!A1",
                valueInputOption="RAW", body={"values": rows}).execute()
        except Exception as e:
            print(f"  Earnings calendar update error: {e}")
    else:
        earnings = getattr(run_scan, '_earnings_cache', {})

    # Calculate IV rank per symbol (batch read UNUSUAL_ALERTS once)
    try:
        from sheets import _service, SHEET_ID
        from datetime import timedelta
        _svc = _service()
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        r_iv = _svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range="UNUSUAL_ALERTS!A:I"
        ).execute()
        # Build IV history per symbol
        iv_history = {}
        for row in r_iv.get("values", [])[1:]:
            if len(row) >= 9 and row[0][:10] >= cutoff:
                try:
                    iv = float(row[8])
                    if iv > 0:
                        iv_history.setdefault(row[1], []).append(iv)
                except (ValueError, IndexError):
                    pass
        # Apply IV rank to entries
        for r in results:
            sym = r["symbol"]
            ivs = iv_history.get(sym, [])
            if len(ivs) >= 5:
                iv_min, iv_max = min(ivs), max(ivs)
                for entry in r["calls"] + r["puts"]:
                    if entry.get("iv") and iv_max > iv_min:
                        rank = round((entry["iv"] - iv_min) / (iv_max - iv_min) * 100)
                        emoji = "🔴 High" if rank >= 70 else "🟢 Low" if rank <= 30 else "🟡 Mid"
                        entry["iv_rank"] = f"IVR {rank} {emoji}"
                        entry["score"] = score_alert(entry)
    except Exception as e:
        print(f"  IV rank error: {e}")

    # Store to sheets + get momentum
    momentum = store_results(results, prices, price_changes=price_changes, fixed_symbols=set(ALL_SYMBOLS))

    # Earnings tracking — snapshot pre-earnings flow + update post-earnings results
    if earnings:
        try:
            from sheets import _service, SHEET_ID
            from sheets import _ensure_tabs, _append
            svc = _service()
            _ensure_tabs(svc, SHEET_ID, ["EARNINGS_TRACKER"])
            # Snapshot pre-earnings flow for symbols reporting this week
            snap_rows = []
            for sym, date in earnings.items():
                r = next((x for x in results if x["symbol"] == sym), None)
                if r:
                    row = snapshot_pre_earnings(sym, r)
                    row[1] = date  # fill earnings_date
                    snap_rows.append(row)
            if snap_rows:
                # Only append if not already logged today (dedup by symbol+date)
                r_existing = svc.spreadsheets().values().get(
                    spreadsheetId=SHEET_ID, range="EARNINGS_TRACKER!A:B"
                ).execute()
                existing_keys = {f"{row[0]}_{row[1]}" for row in r_existing.get("values",[])[1:] if len(row)>=2}
                new_rows = [row for row in snap_rows if f"{row[0]}_{row[1]}" not in existing_keys]
                if new_rows:
                    _append(svc, SHEET_ID, "EARNINGS_TRACKER", new_rows)
            # Update post-earnings results for symbols that already reported
            for sym in ALL_SYMBOLS:
                update_post_earnings(svc, SHEET_ID, sym)
        except Exception as e:
            print(f"  ⚠️  Earnings tracker error: {e}")

    # Duplicate suppression — only send if something new or forced
    if not force_send and not has_new_signals(results):
        print("⏭️  No new high-score signals — skipping Telegram.")
        return

    # Fetch gamma levels (latest from sheet) and news for confluence scoring
    gamma_data, news_data = {}, {}
    try:
        from sheets import _service, SHEET_ID
        svc = _service()
        # Latest GEX per symbol (nearest expiry)
        gr = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="GAMMA_LEVELS!A:L").execute()
        for row in gr.get("values", [])[1:]:
            if len(row) >= 11 and row[1] not in gamma_data:
                try: gamma_data[row[1]] = float(row[10])  # gex column
                except: pass
    except Exception:
        pass
    try:
        from daily_brief import fetch_news_sentiment
        top_syms = [r["symbol"] for r in results[:20]]
        news_data = fetch_news_sentiment(top_syms, hours_back=4)
    except Exception:
        pass

    # ── Divergence Warning: stock up 5%+ but calls being SOLD (informed exit signal) ──
    if os.environ.get("SCHWAB_APP_KEY"):
        try:
            divergence_alerts = []
            # Load already-warned symbols today to prevent spam
            today_str = datetime.now().strftime("%Y-%m-%d")
            try:
                dw_r = _svc().spreadsheets().values().get(
                    spreadsheetId=SHEET_ID, range="SIGNAL_HISTORY!A:C"
                ).execute()
                div_warned_today = {row[2] for row in dw_r.get("values",[])
                                    if len(row)>=3 and row[0].startswith(today_str) and row[1]=="DIVERGENCE"}
            except Exception:
                div_warned_today = set()

            for r in results:
                sym = r["symbol"]
                if sym in div_warned_today:
                    continue
                price_chg = price_changes.get(sym, 0)
                if not isinstance(price_chg, (int, float)) or price_chg < 5:
                    continue
                call_sells = [e for e in r["calls"] if e.get("buy_sell") == "SELL" and e.get("premium", 0) > 100_000]
                call_buys  = [e for e in r["calls"] if e.get("buy_sell") == "BUY"  and e.get("premium", 0) > 100_000]
                if len(call_sells) >= 2 and len(call_sells) > len(call_buys):
                    total_sell = sum(e["premium"] for e in call_sells)
                    total_buy  = sum(e["premium"] for e in call_buys)
                    if total_sell > total_buy * 1.5:
                        # ── Classify the selling ──────────────────────────────
                        avg_delta = sum(abs(e.get("delta") or 0) for e in call_sells) / len(call_sells)
                        avg_vol_oi = sum(e.get("vol_oi_ratio") or 0 for e in call_sells) / len(call_sells)
                        has_sweep = any(e.get("sweep") for e in call_sells)

                        # Delta classification
                        if avg_delta >= 0.70:
                            delta_label = "🔴 Deep ITM (informed exit — existing longs leaving)"
                            conviction = "HIGH"
                        elif avg_delta >= 0.40:
                            delta_label = "🟡 ATM (profit-taking or new short)"
                            conviction = "MEDIUM"
                        else:
                            delta_label = "🟢 OTM (covered call writing or hedging)"
                            conviction = "LOW"

                        # Vol/OI classification
                        if avg_vol_oi and avg_vol_oi > 5:
                            flow_type = "🐻 Directional (new position, vol >> OI)"
                        elif avg_vol_oi and avg_vol_oi > 1:
                            flow_type = "↔️ Mixed (could be closing or new)"
                        else:
                            flow_type = "🛡️ Hedging (vol ≤ OI, likely closing)"

                        sweep_label = "⚡ Sweep (urgent)" if has_sweep else "📋 Block (patient)"

                        own = "  💼 *YOU OWN THIS*" if sym in PORTFOLIO else ""
                        divergence_alerts.append(
                            f"⚠️ *{sym}* up {price_chg:+.1f}% but calls SOLD{own}\n"
                            f"   ${total_sell//1000}K sell vs ${total_buy//1000}K buy\n"
                            f"   {delta_label}\n"
                            f"   {flow_type} | {sweep_label}\n"
                            f"   Conviction: {conviction}"
                        )
                        try:
                            _append(_svc(), SHEET_ID, "SIGNAL_HISTORY", [[
                                datetime.now().strftime("%Y-%m-%d %H:%M"), "DIVERGENCE", sym,
                                f"up {price_chg:+.1f}% calls sold ${total_sell//1000}K | {conviction} | delta:{avg_delta:.2f}"
                            ]])
                        except Exception:
                            pass
            if divergence_alerts:
                # Sort: portfolio stocks first
                divergence_alerts.sort(key=lambda x: (0 if "YOU OWN THIS" in x else 1))
                msg = "*🔍 Divergence Warning — Possible Informed Exit*\n\n"
                msg += "\n\n".join(divergence_alerts)
                msg += "\n\n_Stock rising but smart money selling calls. Check delta + vol/OI for conviction._"
                send_telegram(msg)
                print(f"  ⚠️ Divergence alerts: {len(divergence_alerts)}")
        except Exception:
            pass

    # ── Silent mode: only alert on NEW Golden Flow or ⭐⭐⭐ confluence ──────────
    gf = filter_new_golden_flow(golden_flow(results))  # deduped — once per contract per day
    high_conf = []
    for r in results:
        for entry in r["calls"] + r["puts"]:
            if entry.get("score", 0) >= 8:
                conf = confluence_score(entry["symbol"], entry["type"], results, gamma_data, news_data)
                if "HIGH" in conf:
                    entry["_sym"] = entry["symbol"]
                    high_conf.append((entry, conf))

    if not gf and not high_conf:
        print("🔇 Silent — no Golden Flow or HIGH confluence. Data saved to sheets.")
        return

    # Build focused alert (not the full report)
    now = datetime.now().strftime("%b %d %H:%M")
    # Market session label using Alpaca clock
    import pytz
    et = datetime.now(pytz.timezone('America/New_York'))
    et_mins = et.hour * 60 + et.minute
    is_weekday = et.weekday() < 5
    if is_weekday and 240 <= et_mins < 570:   # 4am-9:30am ET
        session = " 🌅 _Pre-Market_"
    elif is_weekday and 570 <= et_mins < 960:  # 9:30am-4pm ET
        session = ""  # regular hours — no label
    elif is_weekday and 960 <= et_mins < 1200: # 4pm-8pm ET
        session = " 🌆 _After-Hours_"
    else:
        session = " 🌙 _Overnight_"
    lines = [f"*🚨 High Conviction Alert — {now}*{session}", ""]

    if gf:
        lines.append("*⭐ Golden Flow*")
        # Log to SIGNAL_HISTORY for dedup persistence
        try:
            from sheets import _service, SHEET_ID, _append
            svc = _service()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            log_rows = [[now_str, "GOLDEN_FLOW", f["_sym"],
                         f"{f['_sym']}-{f['type']}-{f['strike']}-{f['expiry']}"]
                        for f in gf[:3]]
            _append(svc, SHEET_ID, "SIGNAL_HISTORY", log_rows)
        except Exception:
            pass
        # Sort: portfolio stocks first
        gf_sorted = sorted(gf[:5], key=lambda x: (0 if x["_sym"] in PORTFOLIO else 1, -x["premium"]))
        for f in gf_sorted[:3]:
            side = "🐂 CALL" if f["type"] == "CALL" else "🐻 PUT"
            conf = confluence_score(f["_sym"], f["type"], results, gamma_data, news_data)
            own = "  💼 *YOU OWN THIS*" if f["_sym"] in PORTFOLIO else ""
            lines.append(f"{side} *{f['_sym']}* ${f['strike']:.0f} {f['expiry']}  ⭐{f['score']}  💰 ${f['premium']//1000}K{own}")
            if conf: lines.append(f"  └ {conf}")

    if high_conf:
        lines.append("\n*⭐⭐⭐ High Confluence*")
        # Sort: portfolio stocks first
        high_conf_sorted = sorted(high_conf[:5], key=lambda x: (0 if x[0]["_sym"] in PORTFOLIO else 1))
        for entry, conf in high_conf_sorted[:3]:
            side = "🐂 CALL" if entry["type"] == "CALL" else "🐻 PUT"
            own = "  💼 *YOU OWN THIS*" if entry["_sym"] in PORTFOLIO else ""
            lines.append(f"{side} *{entry['_sym']}* ${entry['strike']:.0f} {entry['expiry']}  ⭐{entry['score']}  💰 ${entry['premium']//1000}K{own}")
            lines.append(f"  └ {conf}")

    # Add market context
    spy = next((r for r in results if r["symbol"] == "SPY"), None)
    if spy:
        lines.append(f"\nSPY P/C `{spy['pc_ratio']}` | VIX `{vix}`")

    lines.append("\n_Not financial advice_")
    send_telegram("\n".join(lines))
    print(f"✅ Alert sent — {len(gf)} golden flow, {len(high_conf)} high confluence. VIX={vix}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Send even if no new signals")
    parser.add_argument("--premarket", action="store_true", help="Pre-market scan mode")
    parser.add_argument("--afterhours", action="store_true", help="After-hours scan mode")
    args = parser.parse_args()

    if args.premarket:
        # Send market open reminder before scanning
        try:
            r = requests.get(
                "https://paper-api.alpaca.markets/v2/clock",
                headers={"APCA-API-KEY-ID": _key(), "APCA-API-SECRET-KEY": _secret()},
                timeout=5
            )
            clock = r.json()
            next_open = clock.get("next_open", "")[:16].replace("T", " ")
            send_telegram(
                f"🔔 *Market opens in ~30 min* ({next_open} ET)\n\n"
                f"Check the morning brief for today's setup.\n"
                f"_Options flow scanner starting pre-market scan..._"
            )
        except Exception:
            pass

    run_scan(force_send=args.force or args.premarket or args.afterhours)

    # Save refreshed Schwab token back to Google Sheets
    if os.environ.get("SCHWAB_APP_KEY"):
        try:
            from schwab_token_store import save_token
            save_token()
        except Exception:
            pass
