"""
Telegram Bot — command handler for on-demand triggers.

Commands:
  /scan    → trigger a forced scan (sends full report)
  /brief   → trigger morning brief
  /status  → show current market snapshot from SYMBOL_TRACKER
  /help    → list commands

Run: python telegram_trigger.py
Deploy: add as a GitHub Actions job triggered by webhook, or run locally.

Uses long-polling (no webhook server needed).
"""
import os, requests, time
from datetime import datetime

def _token(): return os.environ.get("TELEGRAM_BOT_TOKEN", "")
def _chat():  return os.environ.get("TELEGRAM_CHAT_ID", "")


def send(text: str):
    requests.post(
        f"https://api.telegram.org/bot{_token()}/sendMessage",
        json={"chat_id": _chat(), "text": text,
              "parse_mode": "Markdown", "disable_web_page_preview": True},
        timeout=10
    )


def get_updates(offset: int = 0) -> list:
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{_token()}/getUpdates",
            params={"offset": offset, "timeout": 30, "allowed_updates": ["message"]},
            timeout=35
        )
        return r.json().get("result", [])
    except Exception:
        return []


def handle_status() -> str:
    """Quick snapshot from SYMBOL_TRACKER."""
    try:
        from sheets import _service, SHEET_ID
        svc = _service()
        r = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range="SYMBOL_TRACKER!A:H"
        ).execute()
        rows = r.get("values", [])[1:]

        # Key symbols only
        key = ["SPY", "QQQ", "IWM", "GLD", "TLT", "NVDA", "TSLA", "MSFT", "AMZN"]
        lines = [f"*📊 Market Snapshot — {datetime.now().strftime('%b %d %H:%M')}*\n"]
        for row in rows:
            if len(row) >= 4 and row[1] in key:
                lines.append(f"`{row[1]}` {row[2]}  P/C `{row[3]}`")

        # Latest SIGNAL_HISTORY
        r2 = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range="SIGNAL_HISTORY!A:D"
        ).execute()
        sig_rows = r2.get("values", [])[1:]
        if sig_rows:
            lines.append("\n*Recent signals:*")
            for row in sig_rows[-3:]:
                if len(row) >= 4:
                    lines.append(f"• {row[0][:10]} {row[2]} {row[3]}")

        return "\n".join(lines)
    except Exception as e:
        return f"Status error: {e}"


def trigger_github(job: str) -> bool:
    """Trigger a GitHub Actions workflow dispatch."""
    token = os.environ.get("GITHUB_PAT", "")
    if not token:
        return False
    r = requests.post(
        "https://api.github.com/repos/chiju/options-flow-scanner/actions/workflows/scanner.yml/dispatches",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"},
        json={"ref": "main", "inputs": {"job": job}},
        timeout=10
    )
    return r.status_code == 204


def handle_command(text: str, user: str):
    cmd = text.strip().lower().split()[0]

    if cmd == "/help":
        send("""*Options Flow Bot Commands*

/scan — trigger a forced scan (sends full report)
/brief — trigger morning AI brief
/status — current market snapshot
/help — this message""")

    elif cmd == "/status":
        send(handle_status())

    elif cmd == "/scan":
        send("⏳ Triggering scan...")
        if trigger_github("scan"):
            send("✅ Scan triggered — report incoming in ~2 min")
        else:
            # Run locally if no PAT
            try:
                from options_flow_scanner import run_scan
                run_scan(force_send=True)
            except Exception as e:
                send(f"❌ Error: {e}")

    elif cmd == "/brief":
        send("⏳ Triggering brief...")
        if trigger_github("brief"):
            send("✅ Brief triggered — incoming in ~2 min")
        else:
            try:
                from daily_brief import run_brief
                run_brief("morning")
            except Exception as e:
                send(f"❌ Error: {e}")

    else:
        send(f"Unknown command. Try /help")


def run_bot():
    print(f"[{datetime.now().strftime('%H:%M')}] Bot started. Listening...")
    offset = 0
    while True:
        updates = get_updates(offset)
        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            text = msg.get("text", "")
            user = msg.get("from", {}).get("username", "unknown")
            chat_id = str(msg.get("chat", {}).get("id", ""))

            # Only respond to your own chat
            if chat_id != _chat():
                continue
            if text.startswith("/"):
                print(f"  Command: {text} from {user}")
                handle_command(text, user)
        time.sleep(1)


if __name__ == "__main__":
    run_bot()
