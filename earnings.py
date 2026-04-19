"""
Earnings calendar — fetches upcoming earnings dates for watchlist symbols.
Uses yfinance (more reliable than Yahoo Finance API).
"""
import yfinance as yf
from datetime import datetime, timedelta, timezone


def get_earnings_this_week(symbols: list, days_ahead: int = 30) -> dict:
    """
    Returns {symbol: earnings_date_str} for symbols reporting within days_ahead days.
    Uses yfinance earningsTimestamp from ticker info.
    """
    upcoming = {}
    today = datetime.now().date()
    cutoff = today + timedelta(days=days_ahead)

    for sym in symbols:
        try:
            t = yf.Ticker(sym)
            info = t.info
            earn_ts = info.get("earningsTimestamp") or info.get("earningsDate")
            if not earn_ts:
                continue
            earn_date = datetime.fromtimestamp(earn_ts, tz=timezone.utc).date()
            if today <= earn_date <= cutoff:
                upcoming[sym] = earn_date.strftime("%b %d")
        except Exception:
            continue

    return upcoming
