"""Send messages to Telegram."""
import os, requests

def _token(): return os.environ.get("TELEGRAM_BOT_TOKEN", "")
def _chat():  return os.environ.get("TELEGRAM_CHAT_ID", "")


def send(text: str):
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        try:
            requests.post(
                f"https://api.telegram.org/bot{_token()}/sendMessage",
                json={"chat_id": _chat(), "text": chunk,
                      "parse_mode": "Markdown", "disable_web_page_preview": True},
                timeout=10,
            )
        except Exception as e:
            print(f"Telegram error: {e}")
