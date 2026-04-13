"""
Earnings calendar — fetches upcoming earnings dates for watchlist symbols.
Uses Yahoo Finance (free, no API key needed).
"""
import requests
from datetime import datetime, timedelta

YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0"}


def get_earnings_this_week(symbols: list) -> dict:
    """
    Returns {symbol: earnings_date_str} for symbols reporting within 7 days.
    Uses Yahoo Finance quote endpoint — no API key needed.
    """
    upcoming = {}
    today = datetime.now().date()
    cutoff = today + timedelta(days=7)

    for sym in symbols:
        try:
            url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}?modules=calendarEvents"
            r = requests.get(url, headers=YAHOO_HEADERS, timeout=5)
            if not r.ok:
                continue
            data = r.json()
            events = data.get("quoteSummary", {}).get("result", [{}])[0]
            earnings = events.get("calendarEvents", {}).get("earnings", {})
            dates = earnings.get("earningsDate", [])
            if not dates:
                continue
            ts = dates[0].get("raw")
            if not ts:
                continue
            earn_date = datetime.fromtimestamp(ts).date()
            if today <= earn_date <= cutoff:
                upcoming[sym] = earn_date.strftime("%b %d")
        except Exception:
            continue

    return upcoming
