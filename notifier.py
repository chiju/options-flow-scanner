"""Send messages to Telegram — supports multiple recipients."""
import os, requests

def _token(): return os.environ.get("TELEGRAM_BOT_TOKEN", "")

def _chats() -> list:
    """Return list of chat IDs to send to."""
    chats = []
    # Primary chat (your personal)
    primary = os.environ.get("TELEGRAM_CHAT_ID", "")
    if primary: chats.append(primary)
    # Additional chats (comma-separated)
    extra = os.environ.get("TELEGRAM_EXTRA_CHAT_IDS", "")
    if extra:
        chats.extend([c.strip() for c in extra.split(",") if c.strip()])
    return list(dict.fromkeys(chats))  # dedup


def send(text: str):
    for chat_id in _chats():
        for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{_token()}/sendMessage",
                    json={"chat_id": chat_id, "text": chunk,
                          "parse_mode": "Markdown", "disable_web_page_preview": True},
                    timeout=10,
                )
            except Exception as e:
                print(f"Telegram error ({chat_id}): {e}")
