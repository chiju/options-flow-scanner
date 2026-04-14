"""
Earnings tracker — stores pre-earnings options flow snapshot and fetches
post-earnings results to measure prediction accuracy.

Google Sheets tab: EARNINGS_TRACKER
Columns: symbol | earnings_date | pre_pc_ratio | pre_signal | pre_top_flow_k |
         pre_sweep | actual_eps_surprise | price_before | price_after_1d |
         price_change_pct | flow_correct | recorded_at
"""
import requests
from datetime import datetime, timedelta

YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0"}


def get_earnings_result(symbol: str) -> dict:
    """
    Fetch latest earnings result from Yahoo Finance.
    Returns {eps_surprise_pct, price_before, price_after, change_pct} or {}
    """
    try:
        url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules=earningsHistory,price"
        r = requests.get(url, headers=YAHOO_HEADERS, timeout=5)
        if not r.ok:
            return {}
        data = r.json().get("quoteSummary", {}).get("result", [{}])[0]

        # Latest earnings surprise
        history = data.get("earningsHistory", {}).get("history", [])
        if not history:
            return {}
        latest = history[-1]
        actual  = latest.get("epsActual", {}).get("raw")
        estimate = latest.get("epsEstimate", {}).get("raw")
        surprise_pct = round((actual - estimate) / abs(estimate) * 100, 1) if actual and estimate and estimate != 0 else None

        # Current price
        price_info = data.get("price", {})
        current = price_info.get("regularMarketPrice", {}).get("raw")
        prev    = price_info.get("regularMarketPreviousClose", {}).get("raw")
        change  = round((current - prev) / prev * 100, 2) if current and prev else None

        return {
            "eps_surprise_pct": surprise_pct,
            "price_current":    current,
            "price_prev":       prev,
            "price_change_pct": change,
        }
    except Exception:
        return {}


def snapshot_pre_earnings(symbol: str, result: dict) -> list:
    """
    Build a pre-earnings row from a scan result for one symbol.
    Returns a row ready to append to EARNINGS_TRACKER sheet.
    """
    pc = result.get("pc_ratio", "")
    signal = _signal(pc)
    top_call_k = result["calls"][0]["premium"] // 1000 if result.get("calls") else 0
    top_put_k  = result["puts"][0]["premium"]  // 1000 if result.get("puts")  else 0
    has_sweep  = any(e.get("sweep") for e in result.get("calls", []) + result.get("puts", []))

    return [
        symbol,
        "",                    # earnings_date (filled by earnings.py)
        pc or "",
        signal,
        max(top_call_k, top_put_k),
        "YES" if has_sweep else "",
        "",                    # actual_eps_surprise (filled post-earnings)
        "",                    # price_before
        "",                    # price_after_1d
        "",                    # price_change_pct
        "",                    # flow_correct
        datetime.now().strftime("%Y-%m-%d %H:%M"),
    ]


def update_post_earnings(svc, sid: str, symbol: str):
    """
    After earnings: fetch result and update the EARNINGS_TRACKER row for this symbol.
    """
    result = get_earnings_result(symbol)
    if not result:
        return

    r = svc.spreadsheets().values().get(
        spreadsheetId=sid, range="EARNINGS_TRACKER!A:A"
    ).execute()
    rows = r.get("values", [])

    # Find last row for this symbol
    row_num = None
    for i, row in enumerate(rows):
        if row and row[0] == symbol:
            row_num = i + 1  # 1-indexed

    if not row_num:
        return

    surprise = result.get("eps_surprise_pct", "")
    change   = result.get("price_change_pct", "")

    # flow_correct: pre-signal was bullish and price went up, or bearish and price went down
    r2 = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"EARNINGS_TRACKER!D{row_num}"
    ).execute()
    pre_signal = (r2.get("values") or [[""]])[0][0]
    flow_correct = ""
    if change is not None and pre_signal:
        bullish = "Bullish" in pre_signal or "Very Bullish" in pre_signal
        bearish = "Bearish" in pre_signal
        if (bullish and change > 0) or (bearish and change < 0):
            flow_correct = "✅ Correct"
        elif bullish or bearish:
            flow_correct = "❌ Wrong"
        else:
            flow_correct = "⚪ Neutral"

    svc.spreadsheets().values().update(
        spreadsheetId=sid,
        range=f"EARNINGS_TRACKER!G{row_num}",
        valueInputOption="RAW",
        body={"values": [[surprise, result.get("price_prev",""), result.get("price_current",""), change, flow_correct]]}
    ).execute()


def _signal(pc) -> str:
    if pc is None: return "⚪"
    if pc < 0.3:   return "🔥 Very Bullish"
    if pc < 0.6:   return "🟢 Bullish"
    if pc < 1.0:   return "🟡 Neutral"
    if pc < 1.5:   return "🟠 Cautious"
    return "🔴 Bearish"
