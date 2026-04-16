"""
Google Sheets storage for Options Flow Tracker.
Spreadsheet: https://docs.google.com/spreadsheets/d/1zhF6uyyoJpfbcjQvTqIQ11hbLQ17fO_4mKv1W5H4q8g

Tabs:
  SYMBOL_TRACKER  — one row per symbol, updated in place
  UNUSUAL_ALERTS  — all high-conviction signals, appended
  SPY / QQQ / IWM / MSFT / NVDA / ...  — per-symbol, all contracts, appended
"""
import os, json
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES    = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_ID  = os.environ.get("GOOGLE_OPTIONS_SHEET_ID", "1zhF6uyyoJpfbcjQvTqIQ11hbLQ17fO_4mKv1W5H4q8g")

ALERT_THRESHOLD_K = 5000   # $5M+ or sweep → UNUSUAL_ALERTS

SUMMARY_HEADERS = ["last_updated", "symbol", "signal", "pc_ratio",
                   "call_vol", "put_vol", "top_call_k", "top_put_k"]

ALERT_HEADERS   = ["timestamp", "symbol", "type", "strike", "expiry", "dte_bucket",
                   "volume", "premium_k", "iv", "delta", "sweep", "iv_spike", "signal",
                   "price_at_alert", "score", "buy_sell", "oi", "vol_oi_ratio"]

SYMBOL_HEADERS  = ["timestamp", "type", "strike", "expiry", "dte_bucket",
                   "volume", "premium_k", "iv", "delta", "sweep", "iv_spike"]

OI_HEADERS      = ["date", "symbol", "call_oi", "put_oi", "pc_oi_ratio"]

SIGNAL_HISTORY_HEADERS = ["timestamp", "event_type", "symbol", "detail", "value", "prev_value"]

EARNINGS_HEADERS = ["symbol", "earnings_date", "pre_pc_ratio", "pre_signal",
                    "pre_top_flow_k", "pre_sweep", "actual_eps_surprise",
                    "price_before", "price_after_1d", "price_change_pct",
                    "flow_correct", "recorded_at"]


def dte_bucket(dte: int) -> str:
    if dte <= 7:   return "0-7d 🔥"
    if dte <= 30:  return "8-30d 🟢"
    if dte <= 90:  return "31-90d 🟡"
    return "90d+ 🟠"


def _creds():
    key_file = "/mnt/data_disk/yahoo-portfolio-data-492bd9f9aea4.json"
    raw = os.environ.get("GOOGLE_CREDENTIALS", "")
    if os.path.exists(key_file):
        return Credentials.from_service_account_file(key_file, scopes=SCOPES)
    elif raw:
        return Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    raise ValueError("No Google credentials found")


def _service():
    return build("sheets", "v4", credentials=_creds(), cache_discovery=False)


def _ensure_tabs(svc, sid: str, needed: list):
    """Create missing tabs and write headers."""
    meta     = svc.spreadsheets().get(spreadsheetId=sid).execute()
    existing = {s["properties"]["title"] for s in meta["sheets"]}

    # Add missing tabs
    reqs = [{"addSheet": {"properties": {"title": t}}}
            for t in needed if t not in existing]
    if reqs:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=sid, body={"requests": reqs}
        ).execute()

    # Write headers to new tabs
    SIGNAL_OUTCOMES_HEADERS = [
        "alert_ts", "symbol", "type", "strike", "expiry", "score", "premium_k",
        "price_at_alert", "price_1d", "price_3d",
        "move_1d_pct", "move_3d_pct", "direction", "correct_1d", "correct_3d"
    ]
    header_map = {
        "SYMBOL_TRACKER":   SUMMARY_HEADERS,
        "UNUSUAL_ALERTS":   ALERT_HEADERS,
        "OI_SNAPSHOT":      OI_HEADERS,
        "EARNINGS_TRACKER": EARNINGS_HEADERS,
        "SIGNAL_HISTORY":   SIGNAL_HISTORY_HEADERS,
        "SIGNAL_OUTCOMES":  [
            "alert_ts", "symbol", "type", "strike", "expiry", "score", "premium_k",
            "price_at_alert", "price_1d", "price_3d",
            "move_1d_pct", "move_3d_pct", "direction", "correct_1d", "correct_3d",
            "oi_next_day", "oi_confirmed"
        ],
    }
    for tab in needed:
        if tab in existing:
            continue
        headers = header_map.get(tab, SYMBOL_HEADERS)
        svc.spreadsheets().values().update(
            spreadsheetId=sid, range=f"{tab}!A1",
            valueInputOption="RAW", body={"values": [headers]}
        ).execute()


def _get_sheet_id(svc, sid, tab_name: str) -> int:
    """Get the numeric sheetId for a tab by name (cached in meta)."""
    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] == tab_name:
            return s["properties"]["sheetId"]
    raise ValueError(f"Tab '{tab_name}' not found")


def _prepend_batch(svc, sid, tab_rows: dict):
    """
    Prepend rows to multiple tabs in 2 API calls total (not 2 per tab).
    tab_rows = {tab_name: [rows]}
    """
    if not tab_rows:
        return

    # Get all sheet IDs in one call
    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    sheet_ids = {s["properties"]["title"]: s["properties"]["sheetId"]
                 for s in meta["sheets"]}

    # Build batch insert requests (1 insertDimension per tab)
    insert_requests = []
    for tab, rows in tab_rows.items():
        if tab not in sheet_ids or not rows:
            continue
        insert_requests.append({"insertDimension": {
            "range": {"sheetId": sheet_ids[tab], "dimension": "ROWS",
                      "startIndex": 1, "endIndex": 1 + len(rows)},
            "inheritFromBefore": False
        }})

    if not insert_requests:
        return

    # Single batchUpdate for all insertions
    svc.spreadsheets().batchUpdate(
        spreadsheetId=sid, body={"requests": insert_requests}
    ).execute()

    # Single values.batchUpdate for all data
    value_data = [{"range": f"{tab}!A2", "values": rows}
                  for tab, rows in tab_rows.items() if rows]
    svc.spreadsheets().values().batchUpdate(
        spreadsheetId=sid,
        body={"valueInputOption": "RAW", "data": value_data}
    ).execute()


def _append(svc, sid, tab, rows):
    """Single-tab append — used for UNUSUAL_ALERTS and summary tabs."""
    if not rows:
        return
    svc.spreadsheets().values().append(
        spreadsheetId=sid, range=f"{tab}!A2",
        valueInputOption="RAW", insertDataOption="INSERT_ROWS",
        body={"values": rows}
    ).execute()


def _upsert_tracker(svc, sid, rows):
    """Update existing symbol row or append. One row per symbol."""
    result = svc.spreadsheets().values().get(
        spreadsheetId=sid, range="SYMBOL_TRACKER!A:B"
    ).execute()
    existing = {}
    for i, r in enumerate(result.get("values", [])):
        if len(r) > 1 and i > 0:  # skip header row (i=0)
            existing[r[1]] = i + 1  # 1-indexed sheet row number

    updates, appends = [], []
    for row in rows:
        sym = row[1]
        if sym in existing:
            updates.append({"range": f"SYMBOL_TRACKER!A{existing[sym]}", "values": [row]})
        else:
            appends.append(row)

    if updates:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "RAW", "data": updates}
        ).execute()
    if appends:
        _append(svc, sid, "SYMBOL_TRACKER", appends)


def _signal(pc) -> str:
    if pc is None: return "⚪"
    if pc < 0.3:   return "🔥 Very Bullish"
    if pc < 0.6:   return "🟢 Bullish"
    if pc < 1.0:   return "🟡 Neutral"
    if pc < 1.5:   return "🟠 Cautious"
    return "🔴 Bearish"


def detect_signal_events(svc, sid: str, results: list) -> list:
    """
    Detect 3 types of meaningful events. Returns rows for SIGNAL_HISTORY.

    Event types:
      SIGNAL_FLIP  — P/C crossed bullish/bearish threshold vs last scan
      SWEEP_ALERT  — new sweep with score >= 8 (high conviction)
      PERSISTENCE  — same $5M+ contract seen in UNUSUAL_ALERTS for 3+ days
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    events = []

    # ── Read previous SYMBOL_TRACKER state ──
    r = svc.spreadsheets().values().get(
        spreadsheetId=sid, range="SYMBOL_TRACKER!A:D"
    ).execute()
    prev_state = {}
    for row in r.get("values", [])[1:]:
        if len(row) >= 4:
            try:
                prev_state[row[1]] = float(row[3]) if row[3] else None
            except ValueError:
                pass

    for res in results:
        sym = res["symbol"]
        pc_now  = res["pc_ratio"]
        pc_prev = prev_state.get(sym)

        # ── 1. SIGNAL FLIP ──
        if pc_now and pc_prev:
            def bucket(pc):
                if pc < 0.6: return "bullish"
                if pc < 1.5: return "neutral"
                return "bearish"
            b_now, b_prev = bucket(pc_now), bucket(pc_prev)
            if b_now != b_prev:
                arrow = "🟢" if b_now == "bullish" else ("🔴" if b_now == "bearish" else "🟡")
                events.append([
                    now, "SIGNAL_FLIP", sym,
                    f"{arrow} {b_prev.upper()} → {b_now.upper()}",
                    str(pc_now), str(pc_prev)
                ])

        # ── 2. SWEEP_ALERT (score >= 8) ──
        for entry in res["calls"] + res["puts"]:
            if entry.get("score", 0) >= 8 and entry.get("sweep"):
                side = "🐂 CALL" if entry["type"] == "CALL" else "🐻 PUT"
                events.append([
                    now, "SWEEP_ALERT", sym,
                    f"{side} ${entry['strike']:.0f} {entry['expiry']} ⭐{entry['score']}",
                    f"${entry['premium']//1000}K", ""
                ])

    # ── 3. PERSISTENCE — contract seen 3+ days in UNUSUAL_ALERTS ──
    r2 = svc.spreadsheets().values().get(
        spreadsheetId=sid, range="UNUSUAL_ALERTS!A:F"
    ).execute()
    alert_rows = r2.get("values", [])[1:]
    today = datetime.now().strftime("%Y-%m-%d")
    three_days_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")

    from collections import Counter
    contract_days = Counter()
    for row in alert_rows:
        if len(row) >= 5 and row[0][:10] >= three_days_ago:
            key = f"{row[1]}-{row[2]}-{row[3]}-{row[4]}"  # sym-type-strike-expiry
            contract_days[key].add(row[0][:10]) if isinstance(contract_days[key], set) else None

    # Rebuild as sets properly
    contract_day_sets = {}
    for row in alert_rows:
        if len(row) >= 5 and row[0][:10] >= three_days_ago:
            key = f"{row[1]}-{row[2]}-{row[3]}-{row[4]}"
            contract_day_sets.setdefault(key, set()).add(row[0][:10])

    for key, days in contract_day_sets.items():
        if len(days) >= 3 and today in days:
            parts = key.split("-", 4)
            if len(parts) >= 5:
                sym, typ, strike, expiry = parts[0], parts[1], parts[2], parts[3]
                side = "🐂" if typ == "CALL" else "🐻"
                events.append([
                    now, "PERSISTENCE", sym,
                    f"{side} {typ} ${strike} {expiry} — {len(days)} days",
                    f"{len(days)} days", ""
                ])

    return events


def store_oi_snapshot(svc, sid: str, results: list):
    """Store daily volume snapshot per symbol (OI not available in free chain data).
    Tracks call_vol vs put_vol daily to detect trend changes."""
    today = datetime.now().strftime("%Y-%m-%d")
    _ensure_tabs(svc, sid, ["OI_SNAPSHOT"])

    # Fix header if wrong
    r = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="OI_SNAPSHOT!A1:E1").execute()
    header = r.get("values", [[]])[0] if r.get("values") else []
    if header != ["date", "symbol", "call_vol", "put_vol", "pc_vol_ratio"]:
        svc.spreadsheets().values().update(spreadsheetId=SHEET_ID, range="OI_SNAPSHOT!A1",
            valueInputOption="RAW", body={"values": [["date", "symbol", "call_vol", "put_vol", "pc_vol_ratio"]]}).execute()

    r = svc.spreadsheets().values().get(spreadsheetId=sid, range="OI_SNAPSHOT!A:B").execute()
    existing = {(row[0], row[1]): i + 1 for i, row in enumerate(r.get("values", [])) if len(row) >= 2}

    updates, appends = [], []
    for res in results:
        sym = res["symbol"]
        call_vol = res["call_vol"]
        put_vol  = res["put_vol"]
        pc_ratio = round(put_vol / call_vol, 2) if call_vol > 0 else ""
        row = [today, sym, call_vol, put_vol, pc_ratio]
        key = (today, sym)
        if key in existing:
            updates.append({"range": f"OI_SNAPSHOT!A{existing[key]+1}", "values": [row]})
        else:
            appends.append(row)

    if updates:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sid, body={"valueInputOption": "RAW", "data": updates}
        ).execute()
    if appends:
        _append(svc, sid, "OI_SNAPSHOT", appends)


def get_oi_changes(svc, sid: str, results: list) -> list:
    """Compare today's volume P/C vs yesterday's. Returns change alert strings."""
    r = svc.spreadsheets().values().get(
        spreadsheetId=sid, range="OI_SNAPSHOT!A:E"
    ).execute()
    rows = r.get("values", [])[1:]
    history = {}
    for row in rows:
        if len(row) < 5 or row[0] == "date": continue
        try:
            history.setdefault(row[1], {})[row[0]] = float(row[4]) if row[4] else None
        except ValueError:
            pass
    today = datetime.now().strftime("%Y-%m-%d")
    all_dates = sorted({d for sym_d in history.values() for d in sym_d})
    if len(all_dates) < 2:
        return []
    yesterday = all_dates[-2] if all_dates[-1] == today else all_dates[-1]
    alerts = []
    for res in results:
        sym = res["symbol"]
        t, y = history.get(sym, {}).get(today), history.get(sym, {}).get(yesterday)
        if t and y:
            diff = round(t - y, 2)
            if diff > 0.5:
                alerts.append(f"📈 `{sym}` vol P/C ↑ {y} → *{t}* — put volume growing")
            elif diff < -0.5:
                alerts.append(f"📉 `{sym}` vol P/C ↓ {y} → *{t}* — call volume growing")
    return alerts


def get_last_scan(svc, sid) -> dict:
    """Read last scan from SYMBOL_TRACKER and UNUSUAL_ALERTS.
    Returns {symbol: {pc_ratio, contracts: set of 'TYPE-STRIKE-EXPIRY'}}
    """
    last = {}

    # Get SYMBOL_TRACKER for P/C ratios
    r = svc.spreadsheets().values().get(
        spreadsheetId=sid, range="SYMBOL_TRACKER!A:D"
    ).execute()
    for row in r.get("values", [])[1:]:  # skip header
        if len(row) >= 4:
            try:
                last[row[1]] = {"pc": float(row[3]) if row[3] else None, "contracts": set()}
            except ValueError:
                pass

    # Get last 100 rows of UNUSUAL_ALERTS for contract tracking
    r = svc.spreadsheets().values().get(
        spreadsheetId=sid, range="UNUSUAL_ALERTS!A:F"
    ).execute()
    rows = r.get("values", [])[1:]  # skip header
    if rows:
        last_ts = rows[-1][0] if rows else None
        for row in reversed(rows):
            if len(row) < 6: continue
            if row[0] != last_ts: break  # only last timestamp
            sym = row[1]
            key = f"{row[2]}-{row[3]}-{row[4]}"  # TYPE-STRIKE-EXPIRY
            if sym in last:
                last[sym]["contracts"].add(key)

    return last


def compare_scans(current_results: list, previous: dict) -> list:
    """Compare current scan to previous. Returns list of momentum alert strings."""
    alerts = []

    for r in current_results:
        sym = r["symbol"]
        pc_now = r["pc_ratio"]
        prev = previous.get(sym, {})
        pc_prev = prev.get("pc")
        prev_contracts = prev.get("contracts", set())

        # P/C ratio momentum
        if pc_now and pc_prev:
            if pc_now >= 1.5 and pc_now > pc_prev * 1.3:
                alerts.append(f"⚠️ `{sym}` hedging ↑ P/C {pc_prev} → *{pc_now}*")
            elif pc_now <= 0.6 and pc_now < pc_prev * 0.7:
                alerts.append(f"🚀 `{sym}` bullish ↑ P/C {pc_prev} → *{pc_now}*")

        # Repeated contracts = position being built
        for entry in r["calls"] + r["puts"]:
            if entry["premium"] < 5_000_000: continue
            key = f"{entry['type']}-{entry['strike']}-{entry['expiry']}"
            if key in prev_contracts:
                side = "🐂" if entry["type"] == "CALL" else "🐻"
                alerts.append(
                    f"🔁 {side} `{sym}` {entry['type']} ${entry['strike']:.0f} {entry['expiry']} "
                    f"— repeated 💰 ${entry['premium']//1000}K"
                )

    return alerts


def store_results(results: list, prices: dict = None, fixed_symbols: set = None) -> list:
    try:
        svc = _service()
        sid = SHEET_ID

        # Get previous scan BEFORE writing new data
        previous = get_last_scan(svc, sid)

        _ensure_tabs(svc, sid, ["SYMBOL_TRACKER", "UNUSUAL_ALERTS", "OI_SNAPSHOT", "EARNINGS_TRACKER"])

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        tracker_rows, alert_rows = [], []

        for r in results:
            sym = r["symbol"]
            pc  = r["pc_ratio"]
            sig = _signal(pc)

            # SYMBOL_TRACKER
            tracker_rows.append([
                now, sym, sig, pc or "",
                r["call_vol"], r["put_vol"],
                r["calls"][0]["premium"] // 1000 if r["calls"] else 0,
                r["puts"][0]["premium"]  // 1000 if r["puts"]  else 0,
            ])

            # UNUSUAL_ALERTS — only high-conviction flows
            for entry in r["calls"] + r["puts"]:
                if entry["premium"] >= ALERT_THRESHOLD_K * 1000 or entry.get("sweep"):
                    bucket = dte_bucket(entry["dte"])
                    alert_rows.append([
                        now, sym, entry["type"], entry["strike"], entry["expiry"], bucket,
                        entry["volume"], entry["premium"] // 1000,
                        entry["iv"] or "", entry["delta"] or "",
                        "YES" if entry.get("sweep") else "",
                        "YES" if entry.get("iv_spike") else "",
                        sig,
                        prices.get(sym, "") if prices else "",
                        entry.get("score", ""),
                        entry.get("buy_sell", ""),
                        entry.get("oi", ""),
                        entry.get("vol_oi_ratio", ""),
                    ])

        _upsert_tracker(svc, sid, tracker_rows)
        _append(svc, sid, "UNUSUAL_ALERTS", alert_rows)
        print(f"  📊 Sheets: {len(alert_rows)} alerts | {len(tracker_rows)} tracker")

        # OI snapshot handled by oi_tracker.py (EOD only)
        oi_alerts = []

        # Detect and store signal events
        _ensure_tabs(svc, sid, ["SIGNAL_HISTORY"])
        signal_events = detect_signal_events(svc, sid, results)
        if signal_events:
            _append(svc, sid, "SIGNAL_HISTORY", signal_events)

        momentum = compare_scans(results, previous)
        return momentum + oi_alerts + [
            f"{'🔄' if e[1]=='SIGNAL_FLIP' else '🚨' if e[1]=='SWEEP_ALERT' else '📌'} "
            f"*{e[2]}* {e[3]}"
            for e in signal_events
        ]

    except Exception as e:
        print(f"  ⚠️  Sheets error: {e}")
        return []
