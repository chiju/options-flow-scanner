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
    """Verifier — uses Groq with smaller Llama model (different from analyst's 70B)."""
    key = _groq_key()
    if not key:
        return call_gemini(prompt)
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.1-8b-instant",  # smaller/faster model = different perspective
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 600, "temperature": 0.2
            }, timeout=30
        )
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return call_gemini(prompt)


VERIFIER_PROMPT = """You are a senior options flow analyst verifying two junior analysts' reports.
STRICT RULES:
1. Do NOT introduce any claim not present in at least one analyst's report
2. Every claim in CONSENSUS must be supported by BOTH analysts
3. Flag any claim that contradicts the raw data as INCORRECT
4. Preserve unique insights from each analyst

ANALYST 1 (Gemini):
{analysis_a}

ANALYST 2 (Groq/Llama):
{analysis_b}

RAW DATA (ground truth):
{data}

Write your consolidated report in exactly this format:

✅ CONSENSUS: [claims both analysts agree on, verified against raw data]

⚠️ UNCERTAIN: [claims they disagree on, or that contradict raw data — mark incorrect ones]

💡 UNIQUE FINDINGS: [valuable insights from only one analyst, if data-supported]

📊 FINAL BRIEF: [consolidated analysis under 120 words, only data-supported claims]"""


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

    # Run both analysts in parallel-ish
    print("  Calling Gemini...")
    analysis_a = call_gemini(analyst_prompt)

    print("  Calling Groq...")
    analysis_b = call_groq(analyst_prompt)

    # Verifier consolidates
    print("  Calling verifier (HuggingFace — 3rd provider)...")
    verifier_prompt = VERIFIER_PROMPT.format(
        analysis_a=analysis_a,
        analysis_b=analysis_b,
        data=data_str[:2000]
    )
    consolidated = call_hf(verifier_prompt)

    # Format Telegram message
    emoji = "🌅" if mode == "morning" else "🌆"
    title = "Morning Brief" if mode == "morning" else "Evening Digest"
    now = datetime.now().strftime("%b %d %H:%M")

    msg = f"🌅 AI {title} — {now}\n\n" if emoji == "🌅" else f"🌆 AI {title} — {now}\n\n"
    # Strip all markdown to avoid Telegram parse errors
    clean = consolidated.replace("**", "").replace("*", "•").replace("_", "")
    msg += clean
    msg += "\n\nBased on options flow data only. Verify with technicals before trading. Not financial advice."

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
