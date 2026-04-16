"""
Signal Outcomes — fetches price 1d and 3d after each UNUSUAL_ALERT and records
whether the options flow direction was correct.

OI confirmation: if OI at that strike INCREASED the next day = new position (real signal).
If OI decreased = closing/hedge, mark as IGNORE in analysis.

Sheet: SIGNAL_OUTCOMES
Columns: alert_ts | symbol | type | strike | expiry | score | premium_k |
         price_at_alert | price_1d | price_3d | move_1d_pct | move_3d_pct |
         direction | correct_1d | correct_3d | oi_next_day | oi_confirmed
"""
import os
from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from sheets import _service, SHEET_ID, _append, _ensure_tabs

OUTCOMES_HEADERS = [
    "alert_ts", "symbol", "type", "strike", "expiry", "score", "premium_k",
    "price_at_alert", "price_1d", "price_3d",
    "move_1d_pct", "move_3d_pct", "direction", "correct_1d", "correct_3d",
    "oi_next_day", "oi_confirmed"  # NEW: was this a real new position?
]

MIN_SCORE = 7


def _alpaca_client():
    return StockHistoricalDataClient(
        api_key=os.environ.get("ALPACA_API_KEY", ""),
        secret_key=os.environ.get("ALPACA_SECRET_KEY", "")
    )


def get_price_on_date(symbol: str, date) -> float | None:
    try:
        client = _alpaca_client()
        start = datetime.combine(date - timedelta(days=3), datetime.min.time())
        end   = datetime.combine(date + timedelta(days=1), datetime.min.time())
        bars = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbol, timeframe=TimeFrame.Day, start=start, end=end,
        ))
        df = bars.df
        if df.empty:
            return None
        df.index = df.index.get_level_values("timestamp").normalize().date
        future = df[df.index >= date]
        if future.empty:
            future = df
        return round(float(future["close"].iloc[0]), 2)
    except Exception:
        return None


def get_oi_confirmation(svc, symbol: str, opt_type: str, strike: str, alert_date) -> tuple:
    """
    Check OI_SNAPSHOT for the strike the day after the alert.
    Returns (oi_next_day, confirmed) where confirmed = '✅ New Position' or '⚠️ Closing/Hedge'
    OI_SNAPSHOT columns: date | symbol | expiry | strike | type | oi | oi_change | ...
    """
    try:
        next_day = (alert_date + timedelta(days=1)).strftime("%Y-%m-%d")
        r = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range="OI_SNAPSHOT!A:J"
        ).execute()
        rows = r.get("values", [])[1:]
        for row in rows:
            if (len(row) >= 7 and row[0] == next_day and row[1] == symbol
                    and row[4] == opt_type and str(row[3]) == str(strike)):
                oi_change = int(row[6]) if row[6] else 0
                confirmed = "✅ New Position" if oi_change > 0 else "⚠️ Closing/Hedge"
                return int(row[5]), confirmed
    except Exception:
        pass
    return "", ""


def run_outcomes():
    svc = _service()
    _ensure_tabs(svc, SHEET_ID, ["SIGNAL_OUTCOMES", "UNUSUAL_ALERTS"])

    # Write/update header
    r = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="SIGNAL_OUTCOMES!A1:Q1").execute()
    if not r.get("values") or r["values"][0] != OUTCOMES_HEADERS:
        svc.spreadsheets().values().update(
            spreadsheetId=SHEET_ID, range="SIGNAL_OUTCOMES!A1",
            valueInputOption="RAW", body={"values": [OUTCOMES_HEADERS]}
        ).execute()

    existing = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="SIGNAL_OUTCOMES!A:A"
    ).execute()
    tracked = {r[0] for r in existing.get("values", [])[1:] if r}

    r2 = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="UNUSUAL_ALERTS!A:P"
    ).execute()
    alerts = r2.get("values", [])[1:]

    today = datetime.now().date()
    new_rows = []

    for row in alerts:
        if len(row) < 14:
            continue
        ts, symbol, opt_type, strike, expiry = row[0], row[1], row[2], row[3], row[4]
        premium_k = row[7]
        price_at  = row[13]
        score     = int(row[14]) if len(row) > 14 and row[14] else 0

        if score < MIN_SCORE:
            continue

        key = f"{ts}|{symbol}|{opt_type}|{strike}"
        if key in tracked:
            continue

        try:
            alert_date = datetime.strptime(ts[:10], "%Y-%m-%d").date()
        except ValueError:
            continue

        days_ago = (today - alert_date).days
        if days_ago < 1:
            continue

        price_at_f = float(price_at) if price_at else None
        if not price_at_f:
            continue

        date_1d = alert_date + timedelta(days=1)
        date_3d = alert_date + timedelta(days=3)

        price_1d = get_price_on_date(symbol, date_1d) if days_ago >= 1 else None
        price_3d = get_price_on_date(symbol, date_3d) if days_ago >= 3 else None

        move_1d = round((price_1d - price_at_f) / price_at_f * 100, 2) if price_1d else ""
        move_3d = round((price_3d - price_at_f) / price_at_f * 100, 2) if price_3d else ""

        direction = "UP" if opt_type == "CALL" else "DOWN"

        def outcome(move, direction):
            if move == "": return ""
            return "✅" if (direction == "UP" and move > 0) or (direction == "DOWN" and move < 0) else "❌"

        # OI confirmation — was this a new position or closing?
        oi_next, oi_confirmed = get_oi_confirmation(svc, symbol, opt_type, strike, alert_date)

        new_rows.append([
            ts, symbol, opt_type, strike, expiry, score, premium_k,
            price_at_f, price_1d or "", price_3d or "",
            move_1d, move_3d, direction,
            outcome(move_1d, direction), outcome(move_3d, direction),
            oi_next, oi_confirmed
        ])
        tracked.add(key)

    if new_rows:
        _append(svc, SHEET_ID, "SIGNAL_OUTCOMES", new_rows)
        print(f"✅ Signal outcomes: {len(new_rows)} rows added")
    else:
        print("No new outcomes to record.")


if __name__ == "__main__":
    run_outcomes()
