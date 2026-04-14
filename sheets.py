"""
Google Sheets storage for Options Flow Tracker.
Spreadsheet: https://docs.google.com/spreadsheets/d/1zhF6uyyoJpfbcjQvTqIQ11hbLQ17fO_4mKv1W5H4q8g

Tabs:
  SYMBOL_TRACKER  — one row per symbol, updated in place
  UNUSUAL_ALERTS  — all high-conviction signals, appended
  SPY / QQQ / IWM / MSFT / NVDA / ...  — per-symbol, all contracts, appended
"""
import os, json
from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES    = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_ID  = os.environ.get("GOOGLE_OPTIONS_SHEET_ID", "1zhF6uyyoJpfbcjQvTqIQ11hbLQ17fO_4mKv1W5H4q8g")

ALERT_THRESHOLD_K = 5000   # $5M+ or sweep → UNUSUAL_ALERTS

SUMMARY_HEADERS = ["last_updated", "symbol", "signal", "pc_ratio",
                   "call_vol", "put_vol", "top_call_k", "top_put_k"]

ALERT_HEADERS   = ["timestamp", "symbol", "type", "strike", "expiry", "dte_bucket",
                   "volume", "premium_k", "iv", "delta", "sweep", "iv_spike", "signal",
                   "price_at_alert", "score"]

SYMBOL_HEADERS  = ["timestamp", "type", "strike", "expiry", "dte_bucket",
                   "volume", "premium_k", "iv", "delta", "sweep", "iv_spike"]

OI_HEADERS      = ["date", "symbol", "call_oi", "put_oi", "pc_oi_ratio"]

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
    key_file = os.path.expanduser("~/Desktop/down/yahoo-portfolio-data-44dbe4ae4313.json")
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
    header_map = {
        "SYMBOL_TRACKER": SUMMARY_HEADERS,
        "UNUSUAL_ALERTS": ALERT_HEADERS,
        "OI_SNAPSHOT":    OI_HEADERS,
        "EARNINGS_TRACKER": EARNINGS_HEADERS,
    }
    for tab in needed:
        if tab in existing:
            continue
        headers = header_map.get(tab, SYMBOL_HEADERS)
        svc.spreadsheets().values().update(
            spreadsheetId=sid, range=f"{tab}!A1",
            valueInputOption="RAW", body={"values": [headers]}
        ).execute()


def _append(svc, sid, tab, rows):
    if not rows:
        return
    svc.spreadsheets().values().append(
        spreadsheetId=sid, range=f"{tab}!A2",
        valueInputOption="RAW", insertDataOption="INSERT_ROWS",
        body={"values": rows}
    ).execute()


def _upsert_tracker(svc, sid, rows):
    """Update existing symbol row or append."""
    existing = {}
    result = svc.spreadsheets().values().get(
        spreadsheetId=sid, range="SYMBOL_TRACKER!A:B"
    ).execute()
    for i, r in enumerate(result.get("values", [])):
        if len(r) > 1:
            existing[r[1]] = i + 1  # 1-indexed

    updates, appends = [], []
    for row in rows:
        sym = row[1]
        if sym in existing:
            row_num = existing[sym] + 1
            updates.append({"range": f"SYMBOL_TRACKER!A{row_num}", "values": [row]})
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


def store_oi_snapshot(svc, sid: str, results: list):
    """Store daily OI snapshot — one row per symbol per day (upsert by date+symbol)."""
    today = datetime.now().strftime("%Y-%m-%d")
    _ensure_tabs(svc, sid, ["OI_SNAPSHOT"])
    r = svc.spreadsheets().values().get(
        spreadsheetId=sid, range="OI_SNAPSHOT!A:B"
    ).execute()
    existing = {(row[0], row[1]): i + 1 for i, row in enumerate(r.get("values", [])) if len(row) >= 2}
    updates, appends = [], []
    for res in results:
        sym = res["symbol"]
        call_oi = sum(e.get("oi", 0) or 0 for e in res["calls"])
        put_oi  = sum(e.get("oi", 0) or 0 for e in res["puts"])
        pc_oi   = round(put_oi / call_oi, 2) if call_oi > 0 else ""
        row = [today, sym, call_oi, put_oi, pc_oi]
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
    """Compare today's OI P/C to yesterday's. Returns change alert strings."""
    r = svc.spreadsheets().values().get(
        spreadsheetId=sid, range="OI_SNAPSHOT!A:E"
    ).execute()
    rows = r.get("values", [])[1:]
    history = {}
    for row in rows:
        if len(row) < 5: continue
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
                alerts.append(f"📈 `{sym}` OI P/C ↑ {y} → *{t}* — put OI growing")
            elif diff < -0.5:
                alerts.append(f"📉 `{sym}` OI P/C ↓ {y} → *{t}* — call OI growing")
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

        # Ensure tabs — only for fixed symbols, not dynamic screener stocks
        fixed_tabs = ["SYMBOL_TRACKER", "UNUSUAL_ALERTS", "OI_SNAPSHOT", "EARNINGS_TRACKER"]
        fixed_syms = [r["symbol"] for r in results if fixed_symbols is None or r["symbol"] in fixed_symbols]
        all_tabs = fixed_tabs + fixed_syms
        _ensure_tabs(svc, sid, all_tabs)

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        tracker_rows, alert_rows = [], []
        symbol_rows = {}   # tab_name → [rows]

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

            # Per-symbol + UNUSUAL_ALERTS
            sym_rows = []
            for entry in r["calls"] + r["puts"]:
                bucket = dte_bucket(entry["dte"])
                base = [
                    now, entry["type"], entry["strike"], entry["expiry"], bucket,
                    entry["volume"], entry["premium"] // 1000,
                    entry["iv"] or "", entry["delta"] or "",
                    "YES" if entry.get("sweep") else "",
                    "YES" if entry.get("iv_spike") else "",
                ]
                sym_rows.append(base)

                if entry["premium"] >= ALERT_THRESHOLD_K * 1000 or entry.get("sweep"):
                    alert_rows.append([now, sym] + base[1:] + [
                        sig,
                        prices.get(sym, "") if prices else "",
                        entry.get("score", ""),
                    ])

            # Only store per-symbol tab for fixed watchlist (not dynamic screener stocks)
            if sym in symbol_rows or True:  # always add to dict, filter at write time
                symbol_rows[sym] = sym_rows

        # Write everything
        _upsert_tracker(svc, sid, tracker_rows)
        _append(svc, sid, "UNUSUAL_ALERTS", alert_rows)
        # Only write per-symbol tabs for fixed watchlist
        for sym, rows in symbol_rows.items():
            if fixed_symbols is None or sym in fixed_symbols:
                _append(svc, sid, sym, rows)

        total_sym_rows = sum(len(v) for v in symbol_rows.values())
        print(f"  📊 Sheets: {len(alert_rows)} alerts | {total_sym_rows} symbol rows | {len(tracker_rows)} tracker")

        # OI snapshot (once per day is enough but harmless to run each scan)
        store_oi_snapshot(svc, sid, results)
        oi_alerts = get_oi_changes(svc, sid, results)

        momentum = compare_scans(results, previous)
        return momentum + oi_alerts

    except Exception as e:
        print(f"  ⚠️  Sheets error: {e}")
        return []
