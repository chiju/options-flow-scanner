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

VERIFIER_PROMPT = """You are a senior options flow analyst verifying two junior analysts' reports.

ANALYST 1 (Gemini):
{analysis_a}

ANALYST 2 (Groq/Llama):
{analysis_b}

RAW DATA:
{data}

Your job:
1. Identify where both analysts AGREE (high confidence)
2. Identify where they DISAGREE (flag as uncertain)
3. Correct any claims not supported by the raw data
4. Write ONE consolidated report under 200 words

Format:
✅ CONSENSUS: [points both agree on]
⚠️ UNCERTAIN: [points they disagree on]
📊 FINAL BRIEF: [your consolidated analysis]"""


def call_gemini(prompt: str) -> str:
    key = _gemini_key()
    if not key:
        return "Gemini key not set"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
        r = requests.post(url, json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 400, "temperature": 0.3}
        }, timeout=30)
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
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
                "model": "llama-3.1-70b-versatile",
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
    print("  Calling verifier...")
    verifier_prompt = VERIFIER_PROMPT.format(
        analysis_a=analysis_a,
        analysis_b=analysis_b,
        data=data_str[:2000]  # truncate for verifier
    )
    consolidated = call_gemini(verifier_prompt)

    # Format Telegram message
    emoji = "🌅" if mode == "morning" else "🌆"
    title = "Morning Brief" if mode == "morning" else "Evening Digest"
    now = datetime.now().strftime("%b %d %H:%M")

    msg = f"*{emoji} AI {title} — {now}*\n\n"
    msg += consolidated
    msg += "\n\n_Based on options flow data only. Verify with technicals before trading. Not financial advice._"

    send(msg)
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
