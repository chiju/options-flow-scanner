"""
Google Sheets storage for options flow scanner.
Auto-creates the spreadsheet and tabs on first run.

Sheets:
  RAW_FLOW       — every contract scanned (append)
  UNUSUAL_ALERTS — only high-conviction signals (append)
  SYMBOL_TRACKER — one row per symbol, updated in place (upsert)
"""
import os, json
from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Sheet tab definitions: (name, headers)
TABS = {
    "UNUSUAL_ALERTS": [
        "timestamp", "symbol", "type", "strike", "expiry", "dte_bucket",
        "volume", "premium_k", "iv", "delta", "sweep", "iv_spike", "signal",
    ],
    "SYMBOL_TRACKER": [
        "last_updated", "symbol", "signal", "pc_ratio",
        "call_vol", "put_vol", "top_call_k", "top_put_k",
    ],
}

UNUSUAL_THRESHOLD_K = 5000  # $5M+ goes to UNUSUAL_ALERTS


def _creds():
    key_file = os.path.expanduser("~/Desktop/down/yahoo-portfolio-data-44dbe4ae4313.json")
    raw = os.environ.get("GOOGLE_CREDENTIALS", "")
    if os.path.exists(key_file):
        return Credentials.from_service_account_file(key_file, scopes=SCOPES)
    elif raw:
        return Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    raise ValueError("No Google credentials found")


def _service():
    return build("sheets", "v4", credentials=_creds(), cache_discovery=False)


def _sheet_id():
    sid = os.environ.get("GOOGLE_SHEET_ID", "")
    if not sid:
        raise ValueError("GOOGLE_SHEET_ID env var not set")
    return sid


def dte_bucket(dte: int) -> str:
    if dte <= 7:   return "0-7d 🔥"
    if dte <= 30:  return "8-30d 🟢"
    if dte <= 90:  return "31-90d 🟡"
    return "90d+ 🟠"


def ensure_tabs(svc, sheet_id: str):
    """Create any missing tabs with header rows."""
    meta = svc.spreadsheets().get(spreadsheetId=sheet_id).execute()
    existing = {s["properties"]["title"] for s in meta["sheets"]}

    requests = []
    for tab in TABS:
        if tab not in existing:
            requests.append({"addSheet": {"properties": {"title": tab}}})

    if requests:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": requests}
        ).execute()

    # Write headers to any newly created tabs
    meta2 = svc.spreadsheets().get(spreadsheetId=sheet_id).execute()
    for sheet in meta2["sheets"]:
        title = sheet["properties"]["title"]
        if title not in TABS:
            continue
        # Check if header row exists
        result = svc.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"{title}!A1:Z1"
        ).execute()
        if not result.get("values"):
            svc.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range=f"{title}!A1",
                valueInputOption="RAW",
                body={"values": [TABS[title]]},
            ).execute()


def append_rows(svc, sheet_id: str, tab: str, rows: list):
    if not rows:
        return
    svc.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"{tab}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()


def upsert_symbol_tracker(svc, sheet_id: str, rows: list):
    """Update existing symbol row or append new one."""
    if not rows:
        return
    result = svc.spreadsheets().values().get(
        spreadsheetId=sheet_id, range="SYMBOL_TRACKER!A:B"
    ).execute()
    existing = {r[1]: i + 1 for i, r in enumerate(result.get("values", [])) if len(r) > 1}

    updates, appends = [], []
    for row in rows:
        sym = row[1]  # symbol is col B (index 1)
        if sym in existing:
            row_num = existing[sym] + 1  # +1 for 1-indexed, +1 to skip header
            updates.append({
                "range": f"SYMBOL_TRACKER!A{row_num}",
                "values": [row],
            })
        else:
            appends.append(row)

    if updates:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()
    if appends:
        append_rows(svc, sheet_id, "SYMBOL_TRACKER", appends)


def store_results(results: list):
    """Main entry point — store scan results to sheets."""
    try:
        svc = _service()
        sid = _sheet_id()
        ensure_tabs(svc, sid)

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        alert_rows, tracker_rows = [], []

        for r in results:
            sym = r["symbol"]
            pc  = r["pc_ratio"] or ""
            sig = _signal(pc)

            top_call_k = r["calls"][0]["premium"] // 1000 if r["calls"] else 0
            top_put_k  = r["puts"][0]["premium"]  // 1000 if r["puts"]  else 0
            tracker_rows.append([
                now, sym, sig, pc or "",
                r["call_vol"], r["put_vol"], top_call_k, top_put_k,
            ])

            for entry in r["calls"] + r["puts"]:
                bucket = dte_bucket(entry["dte"])
                if entry["premium"] >= UNUSUAL_THRESHOLD_K * 1000 or entry.get("sweep"):
                    alert_rows.append([
                        now, sym, entry["type"],
                        entry["strike"], entry["expiry"], bucket,
                        entry["volume"], entry["premium"] // 1000,
                        entry["iv"] or "", entry["delta"] or "",
                        "YES" if entry.get("sweep") else "",
                        "YES" if entry.get("iv_spike") else "",
                        sig,
                    ])

        append_rows(svc, sid, "UNUSUAL_ALERTS", alert_rows)
        upsert_symbol_tracker(svc, sid, tracker_rows)
        print(f"  📊 Sheets: {len(alert_rows)} alerts, {len(tracker_rows)} symbols updated")

    except Exception as e:
        print(f"  ⚠️  Sheets error: {e}")


def _signal(pc) -> str:
    if pc is None: return "⚪ No data"
    if pc < 0.3:   return "🔥 Very Bullish"
    if pc < 0.6:   return "🟢 Bullish"
    if pc < 1.0:   return "🟡 Neutral"
    if pc < 1.5:   return "🟠 Cautious"
    return "🔴 Bearish"
