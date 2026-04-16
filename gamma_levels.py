"""
Gamma Levels — daily snapshot of Max Pain, Call Wall, Put Wall per symbol.

Max Pain  = strike where most options expire worthless (MM pinning target)
Call Wall = strike with highest call OI (resistance)
Put Wall  = strike with highest put OI (support)

Runs once daily (EOD). Stores time series in GAMMA_LEVELS sheet.

Sheet columns:
  date | symbol | expiry | spot | max_pain | call_wall | put_wall |
  call_wall_oi | put_wall_oi | days_to_expiry
"""
import os
from datetime import datetime, timedelta
import yfinance as yf
from alpaca.data.historical import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest
from sheets import _service, SHEET_ID, _append, _ensure_tabs

HEADERS = [
    "date", "symbol", "expiry", "spot",
    "max_pain", "call_wall", "put_wall",
    "call_wall_oi", "put_wall_oi", "days_to_expiry",
    "gex", "gex_regime"  # NEW: gamma exposure
]

# Only track these for gamma levels — indexes + key symbols
GAMMA_SYMBOLS = ["SPY", "QQQ", "IWM", "AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "META"]


def get_spot(symbol: str) -> float | None:
    try:
        t = yf.Ticker(symbol)
        return round(t.fast_info.last_price, 2)
    except Exception:
        return None


def calc_gamma_levels(chain: dict, symbol: str, spot: float) -> list:
    """
    Calculate max pain, call wall, put wall from options chain.
    Returns list of rows (one per expiry).
    """
    from collections import defaultdict
    today = datetime.now().date()

    # Group by expiry
    expiry_data = defaultdict(lambda: defaultdict(lambda: {"call_oi": 0, "put_oi": 0, "call_gex": 0.0, "put_gex": 0.0}))

    for contract_sym, snap in chain.items():
        try:
            offset = len(symbol)
            cp     = contract_sym[offset + 6]
            strike = int(contract_sym[offset + 7:]) / 1000
            expiry_str  = contract_sym[offset:offset + 6]
            expiry_date = datetime.strptime(expiry_str, "%y%m%d").date()

            # Use volume as OI proxy (Alpaca chain doesn't expose OI directly)
            oi = int(snap.latest_trade.size) if snap.latest_trade and snap.latest_trade.size else 0
            gamma = snap.greeks.gamma if snap.greeks and snap.greeks.gamma else None
            if gamma is None:
                continue  # skip 0DTE — greeks not available, gamma unreliable anyway

            # GEX = gamma × OI × 100 × spot² (dollar gamma per 1% move)
            gex = gamma * oi * 100 * (spot ** 2) / 100

            if cp == "C":
                expiry_data[expiry_date][strike]["call_oi"] += oi
                expiry_data[expiry_date][strike]["call_gex"] += gex
            else:
                expiry_data[expiry_date][strike]["put_oi"] += oi
                expiry_data[expiry_date][strike]["put_gex"] += gex  # puts have negative GEX
        except Exception:
            continue

    rows = []
    date_str = today.strftime("%Y-%m-%d")

    for expiry_date, strikes in expiry_data.items():
        dte = (expiry_date - today).days
        if dte < 0 or dte > 45:
            continue
        if not strikes:
            continue

        strike_list = sorted(strikes.keys())

        # Max Pain: strike with lowest total dollar pain to option buyers
        min_pain = float("inf")
        max_pain_strike = None
        for test_strike in strike_list:
            total_pain = 0
            for s, d in strikes.items():
                # Call buyers lose if spot < strike at expiry
                call_pain = max(0, test_strike - s) * d["call_oi"]
                # Put buyers lose if spot > strike at expiry
                put_pain  = max(0, s - test_strike) * d["put_oi"]
                total_pain += call_pain + put_pain
            if total_pain < min_pain:
                min_pain = total_pain
                max_pain_strike = test_strike

        # Call Wall: strike with highest call OI
        call_wall = max(strike_list, key=lambda s: strikes[s]["call_oi"])
        call_wall_oi = strikes[call_wall]["call_oi"]

        # Put Wall: strike with highest put OI
        put_wall = max(strike_list, key=lambda s: strikes[s]["put_oi"])
        put_wall_oi = strikes[put_wall]["put_oi"]

        if not max_pain_strike:
            continue

        # GEX: net gamma exposure across all strikes for this expiry
        # Positive = MMs are long gamma (stabilizing), Negative = short gamma (amplifying)
        total_gex = sum(d["call_gex"] - d["put_gex"] for d in strikes.values())
        total_gex_m = round(total_gex / 1_000_000, 2)  # in $M
        gex_regime = "🟢 Positive (pinned)" if total_gex > 0 else "🔴 Negative (trending)"

        rows.append([
            date_str, symbol, expiry_date.strftime("%Y-%m-%d"), spot,
            max_pain_strike, call_wall, put_wall,
            call_wall_oi, put_wall_oi, dte,
            total_gex_m, gex_regime
        ])

    return rows


def run_gamma_levels():
    print(f"[{datetime.now().strftime('%H:%M')}] Running gamma levels...")
    svc = _service()
    _ensure_tabs(svc, SHEET_ID, ["GAMMA_LEVELS"])

    # Write header if empty
    r = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="GAMMA_LEVELS!A1:J1").execute()
    if not r.get("values"):
        svc.spreadsheets().values().update(
            spreadsheetId=SHEET_ID, range="GAMMA_LEVELS!A1",
            valueInputOption="RAW", body={"values": [HEADERS]}
        ).execute()

    # Guard: only run once per day
    today = datetime.now().strftime("%Y-%m-%d")
    r2 = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="GAMMA_LEVELS!A2:A2").execute()
    if (r2.get("values") or [[""]])[0][0] == today:
        print("  ⏭️  Gamma levels already collected today.")
        return

    client = OptionHistoricalDataClient(
        api_key=os.environ.get("ALPACA_API_KEY", ""),
        secret_key=os.environ.get("ALPACA_SECRET_KEY", "")
    )

    all_rows = []
    cutoff = datetime.now().date() + timedelta(days=45)

    for symbol in GAMMA_SYMBOLS:
        print(f"  {symbol}...", end=" ", flush=True)
        spot = get_spot(symbol)
        if not spot:
            print("no spot")
            continue
        try:
            chain = client.get_option_chain(OptionChainRequest(
                underlying_symbol=symbol,
                expiration_date_gte=datetime.now().date(),
                expiration_date_lte=cutoff,
            ))
            rows = calc_gamma_levels(chain, symbol, spot)
            all_rows.extend(rows)
            print(f"{len(rows)} expiries")
        except Exception as e:
            print(f"error: {e}")

    if all_rows:
        # Prepend (newest at top)
        meta = svc.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
        sheet_id = next(s["properties"]["sheetId"] for s in meta["sheets"]
                        if s["properties"]["title"] == "GAMMA_LEVELS")
        svc.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"insertDimension": {
                "range": {"sheetId": sheet_id, "dimension": "ROWS",
                          "startIndex": 1, "endIndex": 1 + len(all_rows)},
                "inheritFromBefore": False
            }}]}
        ).execute()
        svc.spreadsheets().values().update(
            spreadsheetId=SHEET_ID, range="GAMMA_LEVELS!A2",
            valueInputOption="RAW", body={"values": all_rows}
        ).execute()
        print(f"\n✅ Gamma levels: {len(all_rows)} rows stored")
    else:
        print("No data.")


if __name__ == "__main__":
    run_gamma_levels()
