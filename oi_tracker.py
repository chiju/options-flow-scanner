"""
OI Tracker — fetches real Open Interest per strike from Yahoo Finance (yfinance).
Runs once daily after market close (4:30pm ET).

Stores in OI_SNAPSHOT sheet:
  date | symbol | expiry | strike | type | oi | oi_change | vol | price | signal

Signal = OI + price change interpretation:
  Long Buildup   = OI↑ + price↑ → 🐂 Bullish
  Short Buildup  = OI↑ + price↓ → 🐻 Bearish
  Short Covering = OI↓ + price↑ → 🟡 Weak Bullish
  Long Unwinding = OI↓ + price↓ → 🟡 Weak Bearish
"""
import os
from datetime import datetime, timedelta
import yfinance as yf
from sheets import _service, SHEET_ID, _ensure_tabs
from notifier import send

OI_HEADERS = ["date", "symbol", "expiry", "strike", "type",
              "oi", "oi_change", "vol", "price", "signal"]

# ATM zone: ±15% of current price
ATM_RANGE = 0.15
# Top N strikes by OI per symbol per expiry
TOP_N = 5


def _signal(oi_change: int, price_change: float, opt_type: str = "CALL") -> str:
    """
    Interpret OI change + price change correctly for calls and puts.
    
    CALLS:
      OI↑ + price↑ = 🐂 Long Buildup (new call buyers = bullish)
      OI↑ + price↓ = 🐻 Short Buildup (new call writers = bearish)
    PUTS:
      OI↑ + price↓ = 🐂 Long Buildup (new put buyers = bearish on stock, but conviction)
      OI↑ + price↑ = 🛡️ Hedging (new put buyers while price rises = protecting longs)
    """
    if opt_type == "CALL":
        if oi_change > 0 and price_change > 0:   return "🐂 Long Buildup"
        if oi_change > 0 and price_change < 0:   return "🐻 Short Buildup"
        if oi_change < 0 and price_change > 0:   return "🟡 Short Covering"
        if oi_change < 0 and price_change < 0:   return "🟡 Long Unwinding"
    else:  # PUT
        if oi_change > 0 and price_change < 0:   return "🐻 Long Buildup (bearish)"
        if oi_change > 0 and price_change > 0:   return "🛡️ Hedging"
        if oi_change < 0 and price_change > 0:   return "🟡 Put Covering"
        if oi_change < 0 and price_change < 0:   return "🟡 Put Unwinding"
    return "⚪ Neutral"


def fetch_oi(symbol: str) -> list:
    """Fetch top OI contracts for a symbol. Returns list of row dicts."""
    try:
        ticker = yf.Ticker(symbol)
        info   = ticker.fast_info
        price  = info.last_price or 0
        if price == 0:
            return []

        exps = ticker.options
        if not exps:
            return []

        # Pick nearest weekly (>today) + nearest monthly
        today = datetime.now().date()
        selected_exps = []
        for exp in exps[:8]:
            exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
            dte = (exp_date - today).days
            if dte <= 0:
                continue  # skip expired or today's expiry
            if dte <= 7 and not any((datetime.strptime(e, "%Y-%m-%d").date() - today).days <= 7 for e in selected_exps):
                selected_exps.append(exp)  # nearest weekly (future only)
            elif 8 <= dte <= 45 and len(selected_exps) < 2:
                selected_exps.append(exp)  # nearest monthly

        rows = []
        lower = price * (1 - ATM_RANGE)
        upper = price * (1 + ATM_RANGE)

        for exp in selected_exps[:2]:
            chain = ticker.option_chain(exp)

            for df, opt_type in [(chain.calls, "CALL"), (chain.puts, "PUT")]:
                # Filter ATM zone
                atm = df[(df["strike"] >= lower) & (df["strike"] <= upper)]
                if atm.empty:
                    continue
                # Top N by OI
                top = atm.nlargest(TOP_N, "openInterest")
                for _, row in top.iterrows():
                    oi_val  = row.get("openInterest")
                    vol_val = row.get("volume")
                    strike  = row.get("strike")
                    if any(v != v for v in [oi_val, vol_val, strike]):  # NaN check
                        continue
                    rows.append({
                        "symbol":  symbol,
                        "expiry":  exp,
                        "strike":  float(strike),
                        "type":    opt_type,
                        "oi":      int(oi_val or 0),
                        "vol":     int(vol_val or 0),
                        "price":   round(price, 2),
                    })
        return rows
    except Exception as e:
        print(f"  [{symbol}] OI error: {e}")
        return []


def get_prev_oi(svc, sid: str) -> dict:
    """Read yesterday's OI from sheet. Returns {(symbol,expiry,strike,type): oi}"""
    r = svc.spreadsheets().values().get(
        spreadsheetId=sid, range="OI_SNAPSHOT!A:F"
    ).execute()
    rows = r.get("values", [])[1:]  # skip header
    if not rows:
        return {}
    # Get the most recent date's data
    dates = sorted(set(row[0] for row in rows if row), reverse=True)
    if not dates:
        return {}
    last_date = dates[0]
    prev = {}
    for row in rows:
        if len(row) >= 6 and row[0] == last_date:
            try:
                key = (row[1], row[2], float(row[3]), row[4])
                prev[key] = int(row[5])
            except (ValueError, IndexError):
                pass
    return prev


def run_oi_tracker(symbols: list):
    """Main entry — fetch OI for all symbols, prepend to OI_SNAPSHOT."""
    print(f"[{datetime.now().strftime('%H:%M')}] Running OI tracker for {len(symbols)} symbols...")
    svc = _service()
    _ensure_tabs(svc, SHEET_ID, ["OI_SNAPSHOT"])

    # Fix header
    r = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="OI_SNAPSHOT!A1:J1").execute()
    if not r.get("values") or r["values"][0] != OI_HEADERS:
        svc.spreadsheets().values().update(
            spreadsheetId=SHEET_ID, range="OI_SNAPSHOT!A1",
            valueInputOption="RAW", body={"values": [OI_HEADERS]}
        ).execute()

    prev_oi = get_prev_oi(svc, SHEET_ID)
    today   = datetime.now().strftime("%Y-%m-%d")

    # Guard: only run once per day
    r_check = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="OI_SNAPSHOT!A2:A2").execute()
    existing = (r_check.get("values") or [[""]])[0][0] if r_check.get("values") else ""
    if existing == today:
        print(f"  ⏭️  OI already collected for {today}, skipping.")
        return

    all_rows = []
    MIN_OI_CHANGE_PCT = 10  # only store if OI changed by >10% or is new

    # Get yesterday's prices per symbol for accurate signal calculation
    prev_prices = {}
    r_prev = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="OI_SNAPSHOT!A:I").execute()
    prev_rows = r_prev.get("values", [])[1:]
    if prev_rows:
        last_date = sorted(set(r[0] for r in prev_rows if r), reverse=True)[0]
        for row in prev_rows:
            if len(row) >= 9 and row[0] == last_date and row[1] not in prev_prices:
                try: prev_prices[row[1]] = float(row[8])
                except: pass

    for sym in symbols:
        print(f"  {sym}...", end=" ", flush=True)
        contracts = fetch_oi(sym)
        for c in contracts:
            key = (c["symbol"], c["expiry"], c["strike"], c["type"])
            prev = prev_oi.get(key, 0)
            oi_change = c["oi"] - prev

            # Skip if OI didn't change significantly
            if prev > 0:
                change_pct = abs(oi_change) / prev * 100
                if change_pct < MIN_OI_CHANGE_PCT:
                    continue  # not significant
            elif c["oi"] == 0:
                continue  # no OI at all, skip

            # Get yesterday's price for this symbol to calculate real price change
            prev_price = prev_prices.get(c["symbol"], 0)
            price_change = (c["price"] - prev_price) if prev_price > 0 else (1 if oi_change > 0 else -1)
            sig = _signal(oi_change, price_change, c["type"]) if prev > 0 else "⚪ New"
            all_rows.append([
                today, c["symbol"], c["expiry"], c["strike"],
                c["type"], c["oi"], oi_change, c["vol"], c["price"], sig
            ])
    print()

    if not all_rows:
        print("No OI data fetched.")
        return

    # Prepend after header (insert rows at position 2)
    meta = svc.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    sheet_id = next(s["properties"]["sheetId"] for s in meta["sheets"]
                    if s["properties"]["title"] == "OI_SNAPSHOT")

    svc.spreadsheets().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"requests": [{"insertDimension": {
            "range": {"sheetId": sheet_id, "dimension": "ROWS",
                      "startIndex": 1, "endIndex": 1 + len(all_rows)},
            "inheritFromBefore": False
        }}]}
    ).execute()

    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range="OI_SNAPSHOT!A2",
        valueInputOption="RAW", body={"values": all_rows}
    ).execute()

    print(f"✅ OI Tracker: {len(all_rows)} contracts stored")

    # Send summary to Telegram
    bullish = [r for r in all_rows if "Buildup" in r[9] and "🐂" in r[9]]
    bearish = [r for r in all_rows if "Buildup" in r[9] and "🐻" in r[9]]
    if bullish or bearish:
        msg = f"📊 *EOD OI Snapshot — {today}*\n\n"
        if bullish:
            msg += "*🐂 Long Buildup (OI↑ + price↑):*\n"
            for r in sorted(bullish, key=lambda x: x[5], reverse=True)[:5]:
                msg += f"  `{r[1]}` {r[4]} ${r[3]:.0f} {r[2]} | OI: {r[5]:,} (+{r[6]:,})\n"
        if bearish:
            msg += "\n*🐻 Short Buildup (OI↑ + price↓):*\n"
            for r in sorted(bearish, key=lambda x: x[5], reverse=True)[:5]:
                msg += f"  `{r[1]}` {r[4]} ${r[3]:.0f} {r[2]} | OI: {r[5]:,} (+{r[6]:,})\n"
        msg += "\n_OI data from Yahoo Finance. Not financial advice._"
        send(msg)


if __name__ == "__main__":
    from options_flow_scanner import ALL_SYMBOLS
    run_oi_tracker(ALL_SYMBOLS)
