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
def _gemini_key(): return os.environ.get("GOOGLE_AI_API", os.environ.get("google_ai_api", ""))
def _groq_key():   return os.environ.get("GROQ_API_KEY", "")


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

    return {
        "alerts":  alerts[:50],   # cap to avoid token limits
        "signals": signals[:30],
        "tracker": tracker[:20],  # top 20 symbols
        "period":  f"Last {hours_back} hours",
        "timestamp": datetime.now().strftime("%b %d %H:%M"),
    }


def format_data_for_ai(data: dict, mode: str) -> str:
    """Format sheet data into a compact string for AI prompt."""
    lines = [f"=== OPTIONS FLOW DATA ({data['period']}) ===\n"]

    lines.append("--- SYMBOL TRACKER (current P/C ratios) ---")
    for row in data["tracker"][1:]:  # skip header
        if len(row) >= 4:
            lines.append(f"{row[1]}: signal={row[2]} P/C={row[3]} calls={row[4] if len(row)>4 else '?'} puts={row[5] if len(row)>5 else '?'}")

    lines.append("\n--- UNUSUAL ALERTS (top flows) ---")
    for row in data["alerts"][:20]:
        if len(row) >= 8:
            lines.append(f"{row[0]} | {row[1]} {row[2]} ${row[3]} {row[4]} | vol={row[6]} premium=${row[7]}K | sweep={row[10] if len(row)>10 else ''} score={row[13] if len(row)>13 else ''}")

    lines.append("\n--- SIGNAL HISTORY (key events) ---")
    for row in data["signals"]:
        if len(row) >= 4:
            lines.append(f"{row[0]} | {row[1]} | {row[2]} | {row[3]}")

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
ANALYST_1_CHAIN = ["gemini", "groq-70b", "openrouter"]   # Gemini first (best quality)
ANALYST_2_CHAIN = ["groq-70b", "openrouter", "gemini"]   # Groq first (fastest)
VERIFIER_CHAIN  = ["openrouter", "groq-8b", "gemini"]    # OpenRouter first (3rd provider)

ANALYST_PROMPT = """You are an institutional options flow analyst. Analyze ONLY the data provided.
Do NOT add information not in the data. If data is insufficient, say so explicitly.
Cite specific $ amounts and symbols from the data.

{mode_instruction}

DATA:
{data}

Write your analysis in under 180 words. Be specific. Cite data. No vague statements."""

MORNING_INSTRUCTION = """Write a MORNING BRIEF covering:
1. Top 3 smart money signals from the data (cite exact $ amounts)
2. What expires soon (potential volatility catalysts)
3. Overall market bias with evidence
4. One specific setup to watch today"""

EVENING_INSTRUCTION = """Write an EVENING DIGEST covering:
1. What the smart money did today (top signals)
2. Any signal flips or momentum changes
3. What to watch tomorrow
4. Overall assessment: was today bullish or bearish for smart money?"""

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


VERIFIER_PROMPT = """You are a senior options flow analyst. Two junior analysts have reviewed the same data.
Your job: produce ONE clean consolidated report. Do not show your reasoning process.

RULES (follow strictly):
- Only include claims supported by the RAW DATA below
- Mark any claim not in raw data as [UNVERIFIED]
- Output ONLY the 4 sections below, nothing else

ANALYST 1: {analysis_a}

ANALYST 2: {analysis_b}

RAW DATA: {data}

Output exactly this format (no preamble, no explanation):

✅ CONSENSUS:
[bullet points both analysts agree on, verified in raw data]

⚠️ UNCERTAIN:
[claims they disagree on, or marked [UNVERIFIED]]

💡 UNIQUE FINDINGS:
[data-supported insight from only one analyst]

📊 FINAL BRIEF (120 words max):
[clean actionable summary — what happened, what to watch, market bias]"""


def call_gemini(prompt: str) -> str:
    key = _gemini_key()
    if not key:
        return "Gemini key not set"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
        r = requests.post(url, json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 800,
                "temperature": 0.3,
                "thinkingConfig": {"thinkingBudget": 0}  # disable thinking to save tokens
            }
        }, timeout=30)
        data = r.json()
        if "candidates" not in data:
            return f"Gemini error: {data.get('error', {}).get('message', str(data))[:100]}"
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        return f"Gemini error: {e}"


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
