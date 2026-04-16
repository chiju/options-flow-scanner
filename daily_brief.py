"""
Daily Brief — 3-AI pipeline for morning and evening reports.

Flow:
  Google Sheets data → Gemini Flash (analyst 1)
                     → Groq Llama (analyst 2)
                     → Gemini Flash (verifier) → consolidated report
                     → Telegram

Run:
  python daily_brief.py --morning    # 8am ET
  python daily_brief.py --evening    # 4:30pm ET
"""
import os, json, argparse, requests
from datetime import datetime, timedelta
from sheets import _service, SHEET_ID
from notifier import send

# ── Credentials ───────────────────────────────────────────────────────────────
def _gemini_key():    return os.environ.get("GOOGLE_AI_API", os.environ.get("google_ai_api", ""))
def _gemini_key2():   return os.environ.get("GOOGLE_AI_API_2", "")
def _groq_key():      return os.environ.get("GROQ_API_KEY", "")
def _hf_key():        return os.environ.get("HF_TOKEN", "")
def _alpaca_key():    return os.environ.get("ALPACA_API_KEY", "")
def _alpaca_secret(): return os.environ.get("ALPACA_SECRET_KEY", "")


# ── FinBERT Sentiment ─────────────────────────────────────────────────────────
def _finbert_score(text: str) -> tuple[str, float]:
    """
    Score a headline using FinBERT via HuggingFace Inference API.
    Falls back to keyword matching if no HF_TOKEN.
    Returns (label, confidence): label = 'positive' | 'negative' | 'neutral'
    """
    hf = _hf_key()
    if hf:
        try:
            r = requests.post(
                "https://router.huggingface.co/hf-inference/models/ProsusAI/finbert",
                headers={"Authorization": f"Bearer {hf}"},
                json={"inputs": text[:200]},
                timeout=10,
            )
            result = r.json()
            if isinstance(result, list) and result:
                top = max(result[0], key=lambda x: x["score"])
                return top["label"], top["score"]
        except Exception:
            pass
    # Keyword fallback
    t = text.lower()
    pos = sum(1 for w in ["surge","rally","beat","strong","gain","rise","bullish","upgrade","record"] if w in t)
    neg = sum(1 for w in ["drop","fall","miss","weak","loss","decline","bearish","downgrade","crash","cut"] if w in t)
    if pos > neg:   return "positive", 0.6
    if neg > pos:   return "negative", 0.6
    return "neutral", 0.5


# ── Reddit Sentiment (free, no API key) ───────────────────────────────────────
def fetch_reddit_sentiment(symbols: list) -> dict:
    """Scrape WSB/stocks/investing for symbol mentions. No API key needed."""
    from collections import defaultdict
    results = defaultdict(lambda: {"mentions": 0, "bullish": 0, "bearish": 0})
    bull_words = {"buy", "calls", "moon", "bullish", "long", "breakout", "squeeze"}
    bear_words = {"sell", "puts", "short", "bearish", "crash", "dump", "downside"}

    for sub in ["wallstreetbets", "stocks", "investing"]:
        try:
            r = requests.get(
                f"https://www.reddit.com/r/{sub}/hot.json?limit=25",
                headers={"User-Agent": "OptionsBot/1.0"}, timeout=8
            )
            if not r.ok: continue
            for post in r.json()["data"]["children"]:
                d = post["data"]
                title = d.get("title", "").upper()
                text = (d.get("selftext", "") + " " + title).lower()
                for sym in symbols:
                    if f"${sym}" in title or f" {sym} " in f" {title} ":
                        results[sym]["mentions"] += 1
                        words = set(text.split())
                        if words & bull_words: results[sym]["bullish"] += 1
                        elif words & bear_words: results[sym]["bearish"] += 1
        except Exception:
            continue
    return {k: v for k, v in results.items() if v["mentions"] > 0}


# ── News Sentiment (Alpaca News API) ─────────────────────────────────────────
def fetch_news_sentiment(symbols: list, hours_back: int = 18) -> dict:
    """
    Fetch news from Alpaca and score with FinBERT (falls back to keywords if no HF_TOKEN).
    Returns {symbol: {"positive": n, "negative": n, "neutral": n, "headlines": [...]}}
    """
    if not _alpaca_key():
        return {}
    try:
        from datetime import timezone
        start = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = requests.get(
            "https://data.alpaca.markets/v1beta1/news",
            headers={"APCA-API-KEY-ID": _alpaca_key(), "APCA-API-SECRET-KEY": _alpaca_secret()},
            params={"symbols": ",".join(symbols[:20]), "start": start, "limit": 50, "sort": "desc"},
            timeout=10
        )
        if not r.ok:
            return {}
        result = {}
        for article in r.json().get("news", []):
            headline = article.get("headline", "")
            label, _ = _finbert_score(headline)
            for sym in article.get("symbols", []):
                if sym not in result:
                    result[sym] = {"positive": 0, "negative": 0, "neutral": 0, "headlines": []}
                result[sym][label] += 1
                if len(result[sym]["headlines"]) < 3:
                    emoji = "🟢" if label == "positive" else ("🔴" if label == "negative" else "⚪")
                    result[sym]["headlines"].append(f"{emoji} {headline[:80]}")
        return result
    except Exception as e:
        print(f"  News fetch error: {e}")
        return {}


# ── Data Fetcher ──────────────────────────────────────────────────────────────
def fetch_brief_data(hours_back: int = 24) -> dict:
    """Read last N hours of data from Google Sheets."""
    svc = _service()
    cutoff = (datetime.now() - timedelta(hours=hours_back)).strftime("%Y-%m-%d %H:%M")

    # UNUSUAL_ALERTS — last N hours
    r = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="UNUSUAL_ALERTS!A:N"
    ).execute()
    rows = r.get("values", [])[1:]
    alerts = [row for row in rows if len(row) > 1 and row[0] >= cutoff]

    # SIGNAL_HISTORY — last N hours
    r2 = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="SIGNAL_HISTORY!A:F"
    ).execute()
    sig_rows = r2.get("values", [])[1:]
    signals = [row for row in sig_rows if len(row) > 1 and row[0] >= cutoff]

    # SYMBOL_TRACKER — current state
    r3 = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="SYMBOL_TRACKER!A:H"
    ).execute()
    tracker = r3.get("values", [])

    # Top symbols from tracker for news fetch
    # Price trend — is the market actually up or down? (critical context)
    price_trend = {}
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        import datetime as _dt
        stock_client = StockHistoricalDataClient(api_key=_alpaca_key(), secret_key=_alpaca_secret())
        key_syms = ["SPY", "QQQ", "IWM", "GLD", "TLT"]
        bars = stock_client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=key_syms, timeframe=TimeFrame.Day,
            start=_dt.datetime.now() - _dt.timedelta(days=7),
            end=_dt.datetime.now()
        ))
        df = bars.df
        for sym in key_syms:
            try:
                sym_df = df.xs(sym, level="symbol")["close"]
                if len(sym_df) >= 2:
                    chg = round((sym_df.iloc[-1] - sym_df.iloc[-2]) / sym_df.iloc[-2] * 100, 2)
                    chg5 = round((sym_df.iloc[-1] - sym_df.iloc[0]) / sym_df.iloc[0] * 100, 2)
                    trend = "📈" if chg > 0 else "📉"
                    price_trend[sym] = f"{trend} {chg:+.1f}% today, {chg5:+.1f}% 5d (${sym_df.iloc[-1]:.2f})"
            except Exception:
                pass
    except Exception:
        pass
    history_ctx = []
    try:
        # SIGNAL_OUTCOMES — what fired recently and was it right?
        ro = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="SIGNAL_OUTCOMES!A:O").execute()
        outcome_rows = ro.get("values", [])[1:]
        from datetime import date as _date, timedelta as _td
        three_days_ago = (datetime.now() - _td(days=3)).strftime("%Y-%m-%d")
        for row in outcome_rows:
            if len(row) >= 14 and row[0][:10] >= three_days_ago:
                result = row[13] if row[13] else "pending"
                move = f"{row[10]}%" if row[10] else "?"
                history_ctx.append(
                    f"{row[0][:10]} | {row[1]} {row[2]} ${row[3]} {row[4]} | "
                    f"score={row[5]} | 1d move={move} | {result}"
                )

        # SIGNAL_HISTORY — persistent sweeps (same contract 3+ days)
        rh = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="SIGNAL_HISTORY!A:F").execute()
        sig_rows = rh.get("values", [])[1:]
        from collections import Counter
        contract_count = Counter()
        for row in sig_rows:
            if len(row) >= 4 and row[0][:10] >= three_days_ago:
                contract_count[f"{row[2]}|{row[3]}"] += 1
        for key, count in contract_count.most_common(5):
            if count >= 3:
                sym, detail = key.split("|", 1)
                history_ctx.append(f"PERSISTENT ({count}x): {sym} {detail}")
    except Exception:
        pass

    top_syms = [row[1] for row in tracker[1:21] if len(row) > 1]
    news   = fetch_news_sentiment(top_syms, hours_back)
    reddit = fetch_reddit_sentiment(top_syms)

    # Gamma levels — nearest expiry per symbol
    gamma = {}
    try:
        rg = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="GAMMA_LEVELS!A:L").execute()
        for row in rg.get("values", [])[1:]:
            if len(row) >= 12 and row[1] not in gamma:
                gamma[row[1]] = {
                    "spot": row[3], "max_pain": row[4],
                    "call_wall": row[5], "put_wall": row[6],
                    "gex": row[10], "gex_regime": row[11]
                }
    except Exception:
        pass

    return {
        "alerts":  alerts[:50],
        "signals": signals[:30],
        "tracker": tracker[:20],
        "news":    news,
        "reddit":  reddit,
        "gamma":   gamma,
        "history": history_ctx,
        "price_trend": price_trend,
        "period":  f"Last {hours_back} hours",
        "timestamp": datetime.now().strftime("%b %d %H:%M"),
    }


def format_data_for_ai(data: dict, mode: str) -> str:
    """Format sheet data into a compact string for AI prompt."""
    lines = [f"=== OPTIONS FLOW DATA ({data['period']}) ===\n"]

    # Price trend FIRST — critical context for interpreting flow
    price_trend = data.get("price_trend", {})
    if price_trend:
        lines.append("--- PRICE TREND (actual market movement) ---")
        lines.append("IMPORTANT: High put volume during a rally = hedging, NOT bearish signal")
        for sym, trend in price_trend.items():
            lines.append(f"{sym}: {trend}")
        lines.append("")

    lines.append("--- SYMBOL TRACKER (current P/C ratios) ---")
    for row in data["tracker"][1:]:
        if len(row) >= 4:
            lines.append(f"{row[1]}: signal={row[2]} P/C={row[3]} calls={row[4] if len(row)>4 else '?'} puts={row[5] if len(row)>5 else '?'}")

    # Historical context
    history = data.get("history", [])
    if history:
        lines.append("\n--- HISTORICAL CONTEXT (last 3 days — what fired and was it right?) ---")
        for h in history[:15]:
            lines.append(h)

    lines.append("\n--- UNUSUAL ALERTS (top flows, score≥7) ---")
    # ALERT_HEADERS: timestamp(0) symbol(1) type(2) strike(3) expiry(4) dte_bucket(5)
    #   volume(6) premium_k(7) iv(8) delta(9) sweep(10) iv_spike(11) signal(12)
    #   price_at_alert(13) score(14) buy_sell(15) oi(16) vol_oi_ratio(17)
    for row in data["alerts"][:20]:
        if len(row) >= 8:
            score = row[14] if len(row) > 14 else ""
            sweep = "SWEEP" if len(row) > 10 and row[10] == "YES" else ""
            buy_sell = row[15] if len(row) > 15 else ""
            lines.append(
                f"{row[0]} | {row[1]} {row[2]} ${row[3]} {row[4]} | "
                f"vol={row[6]} premium=${row[7]}K | score={score} {sweep} {buy_sell}"
            )

    lines.append("\n--- SIGNAL HISTORY (sweeps, flips, persistence) ---")
    for row in data["signals"]:
        if len(row) >= 4:
            lines.append(f"{row[0]} | {row[1]} | {row[2]} | {row[3]}")

    # Gamma levels
    gamma = data.get("gamma", {})
    if gamma:
        lines.append("\n--- GAMMA LEVELS (max pain / call wall / put wall / GEX) ---")
        for sym, g in list(gamma.items())[:6]:
            lines.append(
                f"{sym}: spot={g['spot']} max_pain={g['max_pain']} "
                f"call_wall={g['call_wall']} put_wall={g['put_wall']} "
                f"GEX={g['gex']}M ({g['gex_regime']})"
            )

    # News sentiment
    news = data.get("news", {})
    if news:
        lines.append("\n--- NEWS SENTIMENT (FinBERT scored) ---")
        for sym, s in list(news.items())[:12]:
            bias = "🟢 bullish" if s["positive"] > s["negative"] else ("🔴 bearish" if s["negative"] > s["positive"] else "⚪ neutral")
            lines.append(f"{sym}: {bias} (+{s['positive']}/-{s['negative']})")
            for h in s["headlines"][:1]:
                lines.append(f"  {h}")

    # Reddit
    reddit = data.get("reddit", {})
    if reddit:
        lines.append("\n--- REDDIT BUZZ ---")
        for sym, s in sorted(reddit.items(), key=lambda x: x[1]["mentions"], reverse=True)[:8]:
            mood = "🟢" if s["bullish"] > s["bearish"] else ("🔴" if s["bearish"] > s["bullish"] else "⚪")
            lines.append(f"{sym}: {s['mentions']} mentions {mood}")

    return "\n".join(lines)


# ── AI Callers ────────────────────────────────────────────────────────────────
def call_with_fallback(prompt: str, chain: list) -> tuple:
    """
    Try each model in chain order. Returns (response, model_used).
    Chain items: 'gemini', 'groq-70b', 'groq-8b', 'openrouter'
    """
    callers = {
        "gemini":     call_gemini,
        "groq-70b":   call_groq,
        "groq-8b":    lambda p: _call_groq_model(p, "llama-3.1-8b-instant"),
        "openrouter": call_hf,
    }
    for model in chain:
        result = callers[model](prompt)
        if result and "error" not in result.lower()[:20] and len(result) > 50:
            return result, model
        print(f"  ⚠️ {model} failed, trying next...")
    return "All models failed — check API keys", "none"


def _call_groq_model(prompt: str, model: str) -> str:
    key = _groq_key()
    if not key:
        return "Groq key not set"
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 400, "temperature": 0.3}, timeout=30
        )
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Groq error: {e}"


# Priority chains — different order per role so no single service gets all 3 calls
ANALYST_1_CHAIN = ["gemini", "groq-70b", "openrouter"]   # Gemini first (best quality)
ANALYST_2_CHAIN = ["groq-70b", "openrouter", "gemini"]   # Groq first (fastest)
VERIFIER_CHAIN  = ["gemini", "groq-70b", "openrouter"]   # Gemini first (most decisive)

ANALYST_PROMPT = """You are an institutional options flow analyst. Analyze ONLY the data provided. Think step by step before concluding.

Symbol names — always write "Full Name (TICKER)": S&P 500 (SPY), Nasdaq (QQQ), Russell 2000 (IWM), Gold (GLD), Bonds (TLT), ARK Innovation (ARKK), Tech ETF (XLK), Finance ETF (XLF), Energy ETF (XLE), Health ETF (XLV), Defence ETF (ITA), Oil (USO), Biotech (XBI), Microsoft (MSFT), Nvidia (NVDA), Tesla (TSLA), Amazon (AMZN), Apple (AAPL), Meta (META), Google (GOOGL), Palantir (PLTR), CoreWeave (CRWV), Coinbase (COIN), MicroStrategy (MSTR).

{mode_instruction}

DATA:
{data}"""

MORNING_INSTRUCTION = """MORNING BRIEF — reason step by step, then write the brief:

STEP 1 — READ PRICE TREND: Is the market up or down today and this week?
STEP 2 — FILTER HEDGES: High put volume when market is UP = hedges (ignore). High call volume when market is DOWN = hedges (ignore). Only keep flow that goes AGAINST the price trend.
STEP 3 — CHECK HISTORY: What signals fired in the last 3 days? Were they right or wrong? What's being built persistently (3+ days same contract)?
STEP 4 — SCORE CONFLUENCE: For each remaining signal, count: (a) flow direction matches price trend? (b) news sentiment agrees? (c) GEX negative = move will be amplified?
STEP 5 — WRITE BRIEF: Based on steps 1-4, write 4 short paragraphs:
  Para 1: Market context (price trend + overall bias)
  Para 2: The real signals (not hedges) with exact $ amounts
  Para 3: What's being built persistently and historical accuracy
  Para 4: Single highest-conviction setup with reasoning

Under 200 words. Cite exact $ amounts. No vague statements."""

EVENING_INSTRUCTION = """EVENING DIGEST — reason step by step, then write the digest:

STEP 1 — WHAT HAPPENED TODAY: Check price trend. Did the market move with or against the morning's signals?
STEP 2 — SCORE OUTCOMES: For each signal from HISTORICAL CONTEXT — was it correct (✅) or wrong (❌)? What does this tell us about that signal type?
STEP 3 — NEW POSITIONS: What large flows appeared today? Are they opening (new positions) or closing (OI decreasing)?
STEP 4 — PERSISTENCE CHECK: What contracts appeared 3+ times today or over multiple days? This = institutional accumulation.
STEP 5 — WRITE DIGEST: 4 short paragraphs:
  Para 1: What happened today vs what was expected
  Para 2: Signal accuracy — what worked, what didn't
  Para 3: Positions being built (persistent signals)
  Para 4: Tomorrow's highest-conviction setup

Under 200 words. Cite exact $ amounts."""

def call_hf(prompt: str) -> str:
    """Verifier - uses OpenRouter free models (3rd provider).
    Falls back to Groq 8B if no OpenRouter key."""
    or_key = os.environ.get("OPENROUTER_API_KEY", "")
    if or_key:
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {or_key}", "Content-Type": "application/json"},
                json={
                    "model": "google/gemma-4-31b-it:free",  # non-thinking model, free on OpenRouter
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 600, "temperature": 0.2
                }, timeout=30
            )
            result = r.json()
            if "choices" in result:
                return result["choices"][0]["message"]["content"]
        except Exception:
            pass
    # Fallback: Groq small model
    key = _groq_key()
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 600, "temperature": 0.2}, timeout=30
        )
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return call_gemini(prompt)


VERIFIER_PROMPT = """You are a senior options flow analyst. Two analysts reviewed the same data. Your job: consolidate into ONE actionable report.

STEP 1: Check which claims are in RAW DATA. Mark anything not in raw data as [UNVERIFIED].
STEP 2: Where analysts agree AND data supports it → CONSENSUS.
STEP 3: Where they disagree → pick the one supported by raw data, or mark UNCERTAIN.
STEP 4: Write FINAL BRIEF — be decisive. If data shows X, say X. Don't hedge with "may" or "could".

ANALYST 1: {analysis_a}

ANALYST 2: {analysis_b}

RAW DATA: {data}

Output exactly (no preamble):

✅ CONSENSUS:
[verified facts both agree on]

⚠️ UNCERTAIN:
[genuine disagreements or unverified claims only — keep short]

💡 UNIQUE FINDINGS:
[one data-supported insight only one analyst caught]

📊 FINAL BRIEF (150 words max):
[decisive summary: market context, real signals (not hedges), persistent positions, one specific setup with reasoning]"""


def call_gemini(prompt: str) -> str:
    # Try primary key first, then Melveetil key as fallback
    for key in [_gemini_key(), _gemini_key2()]:
        if not key:
            continue
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
            r = requests.post(url, json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": 800,
                    "temperature": 0.3,
                    "thinkingConfig": {"thinkingBudget": 0}
                }
            }, timeout=30)
            data = r.json()
            if "candidates" in data:
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            continue
    return "Gemini error: all keys exhausted"


def call_groq(prompt: str) -> str:
    key = _groq_key()
    if not key:
        return "Groq key not set — get free key at console.groq.com"
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 400, "temperature": 0.3
            }, timeout=30
        )
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Groq error: {e}"


# ── Main Pipeline ─────────────────────────────────────────────────────────────
def run_brief(mode: str = "morning"):
    print(f"[{datetime.now().strftime('%H:%M')}] Running {mode} brief...")

    hours = 18 if mode == "morning" else 8
    data = fetch_brief_data(hours_back=hours)
    data_str = format_data_for_ai(data, mode)

    instruction = MORNING_INSTRUCTION if mode == "morning" else EVENING_INSTRUCTION
    analyst_prompt = ANALYST_PROMPT.format(mode_instruction=instruction, data=data_str)

    # Run both analysts using fallback chains
    print("  Calling Analyst 1...")
    analysis_a, model_a = call_with_fallback(analyst_prompt, ANALYST_1_CHAIN)

    print("  Calling Analyst 2...")
    analysis_b, model_b = call_with_fallback(analyst_prompt, ANALYST_2_CHAIN)

    # Verifier consolidates using 3rd-priority chain
    print("  Calling verifier...")
    verifier_prompt = VERIFIER_PROMPT.format(
        analysis_a=analysis_a, analysis_b=analysis_b, data=data_str[:2000]
    )
    consolidated, model_v = call_with_fallback(verifier_prompt, VERIFIER_CHAIN)
    print(f"  Models used: A1={model_a} A2={model_b} V={model_v}")

    # Strip thinking preamble — find first section marker
    for marker in ["✅", "⚠️", "💡", "📊"]:
        if marker in consolidated:
            consolidated = consolidated[consolidated.index(marker):]
            break

    # Format Telegram message
    emoji = "🌅" if mode == "morning" else "🌆"
    title = "Morning Brief" if mode == "morning" else "Evening Digest"
    now = datetime.now().strftime("%b %d %H:%M")

    msg = f"🌅 AI {title} — {now}\n\n" if emoji == "🌅" else f"🌆 AI {title} — {now}\n\n"
    # Strip all markdown to avoid Telegram parse errors
    clean = consolidated.replace("**", "").replace("*", "•").replace("_", "")
    msg += clean
    msg += "\n\nBased on options flow data only. Verify with technicals before trading. Not financial advice."

    # Log brief to Google Sheets AFTER stripping preamble
    try:
        from sheets import _service, SHEET_ID, _ensure_tabs, _append
        svc = _service()
        _ensure_tabs(svc, SHEET_ID, ["BRIEF_LOG"])
        # Write header if empty
        r = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="BRIEF_LOG!A1").execute()
        if not r.get("values"):
            svc.spreadsheets().values().update(spreadsheetId=SHEET_ID, range="BRIEF_LOG!A1",
                valueInputOption="RAW", body={"values": [["timestamp", "type", "analyst1", "analyst2", "verifier", "brief"]]}).execute()
        _append(svc, SHEET_ID, "BRIEF_LOG", [[
            datetime.now().strftime("%Y-%m-%d %H:%M"), mode.upper(),
            model_a, model_b, model_v, consolidated[:400]
        ]])
    except Exception as e:
        print(f"  ⚠️ Brief log error: {e}")

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("⚠️  No Telegram credentials")
        print(msg)
        return

    for chunk in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": chunk, "disable_web_page_preview": True},
            timeout=10,
        )
        if not r.ok:
            print(f"Telegram error: {r.status_code} {r.text[:100]}")
    print(f"✅ {title} sent.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--morning", action="store_true")
    parser.add_argument("--evening", action="store_true")
    args = parser.parse_args()

    if args.morning:
        run_brief("morning")
    elif args.evening:
        run_brief("evening")
    else:
        run_brief("morning")  # default
