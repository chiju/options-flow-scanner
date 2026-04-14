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

# ── Watchlist ─────────────────────────────────────────────────────────────────
INDEX_ETFS  = ["SPY", "QQQ", "IWM"]
SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "GLD", "TLT"]
PORTFOLIO   = ["MSFT","NVDA","AMZN","META","TSLA","PLTR","CRWV","IONQ","OKLO",
               "ACHR","DUOL","SOFI","PYPL","PATH","JOBY","UUUU","POET"]
MEGA_CAPS   = ["AAPL","GOOGL","MSFT","NVDA","AMZN","META","TSLA"]
HIGH_VOL    = ["AMD","COIN","MSTR","HOOD","SMCI","ARM","SNOW"]
ALL_SYMBOLS = list(dict.fromkeys(INDEX_ETFS + SECTOR_ETFS + MEGA_CAPS + HIGH_VOL + PORTFOLIO))

# ── Thresholds ────────────────────────────────────────────────────────────────
MIN_PREMIUM       = 25000   # $25k minimum notional
MAX_DTE           = 45      # days to expiry
SWEEP_BLOCK_SIZE  = 500     # contracts = institutional block
IV_SPIKE_THRESH   = 80.0    # IV% on call = urgency
MIN_ALERT_SCORE   = 7       # only send Telegram if top alert scores >= this

# ── Credentials ───────────────────────────────────────────────────────────────
def _key():      return os.environ.get("ALPACA_API_KEY", "")
def _secret():   return os.environ.get("ALPACA_SECRET_KEY", "")
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
    if entry.get("iv_spike"): s += 2

    dte = entry.get("dte", 99)
    if dte <= 7:    s += 2
    elif dte <= 30: s += 1

    delta = entry.get("delta")
    if delta is not None:
        if entry.get("type") == "CALL" and delta < 0.4:   s += 1
        elif entry.get("type") == "PUT" and delta > -0.4: s += 1

    return min(s, 10)


# ── Price Tracking ────────────────────────────────────────────────────────────
def get_current_prices(symbols: list) -> dict:
    """Fetch latest price for each symbol. Returns {sym: price}."""
    try:
        client = StockHistoricalDataClient(api_key=_key(), secret_key=_secret())
        req = StockLatestQuoteRequest(symbol_or_symbols=symbols)
        quotes = client.get_stock_latest_quote(req)
        return {sym: round((q.bid_price + q.ask_price) / 2, 2)
                for sym, q in quotes.items() if q.bid_price and q.ask_price}
    except Exception as e:
        print(f"  Price fetch error: {e}")
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
        iv_spike  = bool(iv_pct and iv_pct > IV_SPIKE_THRESH and cp == "C")
        sweep     = volume >= SWEEP_BLOCK_SIZE and cp == "C"

        entry = {
            "symbol": sym, "contract": contract_sym,
            "type": "CALL" if cp == "C" else "PUT",
            "strike": strike, "expiry": expiry_fmt, "dte": dte,
            "volume": int(volume), "premium": int(premium),
            "delta": round(delta, 2) if delta else None,
            "iv": iv_pct, "mid": round(mid, 2),
            "sweep": sweep, "iv_spike": iv_spike,
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


def interpret_signal(result: dict) -> str:
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
    token, chat = _tg_token(), _tg_chat()
    if not token or not chat:
        print(text); return
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": chunk,
                      "parse_mode": "Markdown", "disable_web_page_preview": True},
                timeout=10,
            )
            if not r.ok: print(f"Telegram error: {r.text}")
        except Exception as e:
            print(f"Telegram error: {e}")


# ── Formatter ─────────────────────────────────────────────────────────────────
def format_report(results: list, earnings: dict = None,
                  vix: float = None, momentum: list = None) -> str:
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
    for sym in ["XLK", "XLF", "XLE", "GLD", "TLT"]:
        r = next((x for x in results if x["symbol"] == sym), None)
        if r and r["pc_ratio"]:
            sig = "🟢" if r["pc_ratio"] < 0.7 else ("🔴" if r["pc_ratio"] > 1.5 else "🟡")
            sector_line.append(f"{sym}{sig}{r['pc_ratio']}")
    if sector_line:
        lines.append("  ".join(sector_line))

    # Sector rotation
    rotation = sector_rotation_signal(results)
    if rotation:
        lines.append(rotation)
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
            lines.append(
                f"{side} *{f['_sym']}* ${f['strike']:.0f} {f['expiry']}"
                f"  Vol {f['volume']:,}{iv_s}{score}"
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

def has_new_signals(results: list) -> bool:
    """Returns True if top flows changed since last scan."""
    global _last_top_flows
    current = set()
    for r in results:
        for entry in r["calls"][:2] + r["puts"][:2]:
            if entry.get("score", 0) >= MIN_ALERT_SCORE:
                current.add(f"{r['symbol']}-{entry['type']}-{entry['strike']}-{entry['expiry']}")
    changed = current != _last_top_flows
    _last_top_flows = current
    return changed


# ── Main ──────────────────────────────────────────────────────────────────────
def run_scan(force_send: bool = False):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Scanning {len(ALL_SYMBOLS)} symbols...")
    client = OptionHistoricalDataClient(api_key=_key(), secret_key=_secret())

    results = []
    for sym in ALL_SYMBOLS:
        print(f"  {sym}...", end=" ", flush=True)
        r = scan_symbol(client, sym)
        if r:
            results.append(r)
    print()

    if not results:
        print("No results.")
        return

    # Fetch VIX + prices + earnings in parallel-ish
    vix      = get_vix()
    prices   = get_current_prices([r["symbol"] for r in results])
    earnings = get_earnings_this_week(ALL_SYMBOLS)

    # Store to sheets + get momentum
    momentum = store_results(results, prices)

    # Duplicate suppression — only send if something new or forced
    if not force_send and not has_new_signals(results):
        print("⏭️  No new high-score signals — skipping Telegram.")
        return

    report = format_report(results, earnings, vix, momentum)
    send_telegram(report)
    print(f"✅ Sent. VIX={vix}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Send even if no new signals")
    parser.add_argument("--premarket", action="store_true", help="Pre-market scan mode")
    parser.add_argument("--afterhours", action="store_true", help="After-hours scan mode")
    args = parser.parse_args()

    run_scan(force_send=args.force or args.premarket or args.afterhours)
