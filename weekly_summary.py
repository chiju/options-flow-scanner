"""
Weekly summary report — runs every Friday at 4pm ET.
Reads the week's UNUSUAL_ALERTS from Google Sheets and sends a digest to Telegram.
"""
import os, requests
from datetime import datetime, timedelta
from collections import Counter
from sheets import _service, SHEET_ID
from notifier import send


def run_weekly_summary():
    svc = _service()

    # Read all UNUSUAL_ALERTS
    r = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="UNUSUAL_ALERTS!A:M"
    ).execute()
    rows = r.get("values", [])[1:]  # skip header

    # Filter to this week (Mon–Fri)
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())
    week_rows = []
    for row in rows:
        if len(row) < 8: continue
        try:
            ts = datetime.strptime(row[0], "%Y-%m-%d %H:%M").date()
            if ts >= monday:
                week_rows.append(row)
        except ValueError:
            continue

    if not week_rows:
        send("📊 *Weekly Summary* — No data this week yet.")
        return

    # Most active symbols
    sym_counts = Counter(r[1] for r in week_rows)

    # Biggest flows — deduplicated by contract (sym+type+strike+expiry), keep max premium
    seen_contracts = {}
    for row in week_rows:
        try:
            key = f"{row[1]}-{row[2]}-{row[3]}-{row[4]}"  # sym-type-strike-expiry
            premium_k = int(row[7])
            if key not in seen_contracts or premium_k > seen_contracts[key][0]:
                seen_contracts[key] = (premium_k, row[1], row[2], row[3], row[4])
        except (ValueError, IndexError):
            continue
    flows = sorted(seen_contracts.values(), reverse=True)

    # Put vs Call split
    calls = [r for r in week_rows if r[2] == "CALL"]
    puts  = [r for r in week_rows if r[2] == "PUT"]
    total_call_k = sum(int(r[7]) for r in calls if len(r) > 7 and r[7].isdigit())
    total_put_k  = sum(int(r[7]) for r in puts  if len(r) > 7 and r[7].isdigit())

    # Sweeps this week
    sweeps = [r for r in week_rows if len(r) > 10 and r[10] == "YES"]

    now = datetime.now().strftime("%b %d")
    lines = [
        f"*📊 Weekly Options Flow Summary — {monday.strftime('%b %d')} to {now}*\n",
        f"Total alerts: {len(week_rows)} | Calls: {len(calls)} | Puts: {len(puts)} | Sweeps: {len(sweeps)}",
        f"Total call premium: ${total_call_k:,}K | Put premium: ${total_put_k:,}K",
        f"Overall bias: {'🐂 Bullish' if total_call_k > total_put_k else '🐻 Bearish'}\n",
        "*🏆 Most Active Symbols*",
    ]
    for sym, count in sym_counts.most_common(8):
        lines.append(f"  `{sym}` — {count} alerts")

    lines.append("\n*💰 Top 5 Flows This Week*")
    for premium_k, sym, typ, strike, expiry in flows[:5]:
        side = "🐂 CALL" if typ == "CALL" else "🐻 PUT"
        lines.append(f"  {side} `{sym}` ${strike} {expiry} — ${premium_k:,}K")

    if sweeps:
        lines.append(f"\n*🚨 Sweeps This Week: {len(sweeps)}*")
        sweep_syms = Counter(r[1] for r in sweeps)
        for sym, count in sweep_syms.most_common(5):
            lines.append(f"  `{sym}` — {count} sweeps")

    lines.append("\n_Not financial advice._")

    # Check Schwab token expiry — warn if within 7 days
    try:
        import json, time, os
        token_path = os.path.expanduser("~/.alpaca/schwab-token.json")
        if os.path.exists(token_path):
            with open(token_path) as f:
                tok = json.load(f)
            exp = tok.get("token", {}).get("expires_at", 0)
            days_left = (exp - time.time()) / 86400
            if days_left < 7:
                lines.append(f"\n⚠️ *Schwab token expires in {days_left:.0f} days!*")
                lines.append("Run locally: `python schwab_token_store.py save`")
    except Exception as e:
        lines.append(f"\n📅 Earnings error: {e}")
        pass

    # ── Upcoming earnings (next 30 days) for all watchlist symbols ──────────────
    try:
        import yfinance as yf, logging
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        from datetime import date as _date
        from options_flow_scanner import INDEX_ETFS, SECTOR_ETFS, DEFENCE, CYBER, PORTFOLIO, MEGA_CAPS, HIGH_VOL, SYMBOL_NAMES
        all_syms = sorted(set(INDEX_ETFS+SECTOR_ETFS+DEFENCE+CYBER+PORTFOLIO+MEGA_CAPS+HIGH_VOL))
        today = _date.today()
        cutoff = today + timedelta(days=7)

        # Get last 7 days of options flow to determine bias per symbol
        week_ago = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        flow_r = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range="UNUSUAL_ALERTS!A:H"
        ).execute()
        flow_rows = [r for r in flow_r.get("values",[]) if len(r)>=8 and r[0]>=week_ago]

        def get_bias(sym):
            sym_rows = [r for r in flow_rows if r[1]==sym]
            if not sym_rows: return "⬜"
            calls = sum(int(r[7]) for r in sym_rows if r[2]=="CALL" and r[7].isdigit())
            puts  = sum(int(r[7]) for r in sym_rows if r[2]=="PUT"  and r[7].isdigit())
            if calls == puts == 0: return "⬜"
            ratio = calls / (calls + puts)
            if ratio >= 0.70:   return "🟢 Bullish"
            elif ratio <= 0.30: return "🔴 Bearish"
            else:               return "🟡 Neutral"

        upcoming = []
        for sym in all_syms:
            try:
                cal = yf.Ticker(sym).calendar
                earn_date = cal.get("Earnings Date", [None])[0] if cal else None
                if earn_date and today <= earn_date <= cutoff:
                    snap = requests.get(
                        f"https://data.alpaca.markets/v2/stocks/{sym}/quotes/latest",
                        headers={"APCA-API-KEY-ID": os.environ.get("ALPACA_LIVE_API_KEY",""),
                                 "APCA-API-SECRET-KEY": os.environ.get("ALPACA_LIVE_SECRET_KEY","")},
                        params={"feed":"sip"}).json()  # sip = consolidated, better on weekends
                    q = snap.get("quote",{})
                    price = (q.get("ap",0)+q.get("bp",0))/2 or q.get("ap",0)
                    # fallback to prev close if quote stale
                    if not price:
                        bar = requests.get(
                            f"https://data.alpaca.markets/v2/stocks/{sym}/bars/latest",
                            headers={"APCA-API-KEY-ID": os.environ.get("ALPACA_LIVE_API_KEY",""),
                                     "APCA-API-SECRET-KEY": os.environ.get("ALPACA_LIVE_SECRET_KEY","")},
                            params={"feed":"sip","timeframe":"1Day"}).json()
                        price = bar.get("bar",{}).get("c",0)
                    name = SYMBOL_NAMES.get(sym, sym)
                    bias = get_bias(sym)
                    upcoming.append((earn_date, sym, name, price, (earn_date-today).days, bias))
            except Exception:
                pass
        if upcoming:
            upcoming.sort()
            lines.append("\n📅 *Earnings This Week*")
            for earn_date, sym, name, price, days_to, bias in upcoming:
                price_str = f"${price:.2f}" if price else "N/A"
                in_portfolio = "💼" if sym in PORTFOLIO else ""
                bias_str = bias if bias != "⬜" else "⚪ No flow data"
                lines.append(f"  {earn_date.strftime('%b %d')} ({days_to}d) — *{sym}* ({name}) {price_str} {in_portfolio} | {bias_str}")
    except Exception as e:
        lines.append(f"\n📅 Earnings section error: {e}")

    send("\n".join(lines))
    print("✅ Weekly summary sent.")

    # Cleanup: delete UNUSUAL_ALERTS rows older than 90 days (keep data fresh)
    try:
        cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        r_all = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range="UNUSUAL_ALERTS!A:A").execute()
        all_rows = r_all.get("values", [])
        # Find rows to keep (header + last 90 days)
        keep = [all_rows[0]] + [row for row in all_rows[1:] if row and row[0][:10] >= cutoff]
        deleted = len(all_rows) - len(keep)
        if deleted > 500:  # only clean if significant
            # Get full data for kept rows
            r_full = svc.spreadsheets().values().get(
                spreadsheetId=SHEET_ID, range="UNUSUAL_ALERTS!A:S").execute()
            full_rows = r_full.get("values", [])
            keep_full = [full_rows[0]] + [row for row in full_rows[1:] if row and row[0][:10] >= cutoff]
            svc.spreadsheets().values().clear(spreadsheetId=SHEET_ID, range="UNUSUAL_ALERTS!A:S").execute()
            svc.spreadsheets().values().update(
                spreadsheetId=SHEET_ID, range="UNUSUAL_ALERTS!A1",
                valueInputOption="RAW", body={"values": keep_full}).execute()
            print(f"  🧹 Cleaned {deleted} old rows from UNUSUAL_ALERTS (kept {len(keep_full)-1})")
    except Exception as e:
        print(f"  ⚠️ Cleanup error: {e}")


if __name__ == "__main__":
    run_weekly_summary()
