"""
Schwab token persistence via Google Sheets.
Saves/loads the schwab-token.json to a private sheet cell so GitHub Actions
can use Schwab API without a persistent filesystem.

Usage:
  python schwab_token_store.py save   # save local token to sheet
  python schwab_token_store.py load   # load token from sheet to file
"""
import json, os, sys, base64

TOKEN_PATH = os.path.expanduser("~/.alpaca/schwab-token.json")
SHEET_ID   = os.environ.get("GOOGLE_SHEET_ID", "1zhF6uyyoJpfbcjQvTqIQ11hbLQ17fO_4mKv1W5H4q8g")
TAB        = "SCHWAB_TOKEN"
CELL       = "A1"


def _svc():
    from sheets import _service
    return _service()


def save_token():
    """Save local token file to Google Sheets (base64 encoded)."""
    if not os.path.exists(TOKEN_PATH):
        print("No token file found")
        return
    with open(TOKEN_PATH) as f:
        token_json = f.read()
    encoded = base64.b64encode(token_json.encode()).decode()
    svc = _svc()
    # Ensure tab exists
    meta = svc.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    existing = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if TAB not in existing:
        svc.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body={
            "requests": [{"addSheet": {"properties": {"title": TAB}}}]
        }).execute()
    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range=f"{TAB}!{CELL}",
        valueInputOption="RAW", body={"values": [[encoded]]}
    ).execute()
    print(f"✅ Token saved to sheet ({len(encoded)} chars)")


def load_token():
    """Load token from Google Sheets and write to local file."""
    svc = _svc()
    try:
        r = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"{TAB}!{CELL}"
        ).execute()
        encoded = r.get("values", [[]])[0][0]
        token_json = base64.b64decode(encoded.encode()).decode()
        os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
        with open(TOKEN_PATH, "w") as f:
            f.write(token_json)
        print(f"✅ Token loaded from sheet → {TOKEN_PATH}")
        return True
    except Exception as e:
        print(f"⚠️ Token load failed: {e}")
        return False


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "load"
    if cmd == "save":
        save_token()
    else:
        load_token()
