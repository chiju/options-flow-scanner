"""
Weekly summary report — runs every Friday at 4pm ET.
Reads the week's UNUSUAL_ALERTS from Google Sheets and sends a digest to Telegram.
"""
import os, requests
from datetime import datetime, timedelta
from collections import Counter
from sheets import _service, SHEET_ID, _append, _ensure_tabs
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
    # Call vs put counts per symbol
    sym_calls = Counter(r[1] for r in week_rows if len(r)>2 and r[2]=="CALL")
    sym_puts  = Counter(r[1] for r in week_rows if len(r)>2 and r[2]=="PUT")

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
        "*🏆 Most Active — Your Portfolio*",
    ]
    portfolio_syms = set(PORTFOLIO) if 'PORTFOLIO' in dir() else set()
    try:
        from options_flow_scanner import PORTFOLIO as _PORT
        portfolio_syms = set(_PORT)
    except Exception:
        pass
    # Portfolio symbols first
    portfolio_active = [(sym, count) for sym, count in sym_counts.most_common(30) if sym in portfolio_syms]
    for sym, count in portfolio_active[:6]:
        c = sym_calls.get(sym, 0); p = sym_puts.get(sym, 0)
        bias = "🟢" if c > p*2 else ("🔴" if p > c*2 else "🟡")
        lines.append(f"  💼 `{sym}` — {count} alerts {bias} ({c}🐂 {p}🐻)")

    lines.append("\n*🏆 Most Active — Watchlist*")
    watchlist_active = [(sym, count) for sym, count in sym_counts.most_common(30) if sym not in portfolio_syms]
    for sym, count in watchlist_active[:6]:
        c = sym_calls.get(sym, 0); p = sym_puts.get(sym, 0)
        bias = "🟢" if c > p*2 else ("🔴" if p > c*2 else "🟡")
        lines.append(f"  `{sym}` — {count} alerts {bias} ({c}🐂 {p}🐻)")

    # Top flows — split portfolio vs watchlist
    portfolio_flows = [(pk,s,t,st,ex) for pk,s,t,st,ex in flows if s in portfolio_syms]
    watchlist_flows  = [(pk,s,t,st,ex) for pk,s,t,st,ex in flows if s not in portfolio_syms]

    if portfolio_flows:
        lines.append("\n*💰 Top Flows — Your Portfolio*")
        for premium_k, sym, typ, strike, expiry in portfolio_flows[:3]:
            side = "🐂 CALL" if typ == "CALL" else "🐻 PUT"
            lines.append(f"  {side} 💼`{sym}` ${strike} {expiry} — ${premium_k:,}K")

    lines.append("\n*💰 Top Flows — Watchlist*")
    for premium_k, sym, typ, strike, expiry in watchlist_flows[:5]:
        side = "🐂 CALL" if typ == "CALL" else "🐻 PUT"
        lines.append(f"  {side} `{sym}` ${strike} {expiry} — ${premium_k:,}K")

    if sweeps:
        call_sweeps = [r for r in sweeps if len(r)>2 and r[2]=="CALL"]
        put_sweeps  = [r for r in sweeps if len(r)>2 and r[2]=="PUT"]
        lines.append(f"\n*🚨 Sweeps This Week: {len(sweeps)}* (all bullish call sweeps)")
        sweep_syms = Counter(r[1] for r in sweeps)
        sweep_calls = Counter(r[1] for r in call_sweeps)
        sweep_puts  = Counter(r[1] for r in put_sweeps)
        # Portfolio sweeps first
        port_sweeps = [(sym,cnt) for sym,cnt in sweep_syms.most_common(20) if sym in portfolio_syms]
        other_sweeps = [(sym,cnt) for sym,cnt in sweep_syms.most_common(20) if sym not in portfolio_syms]
        for sym, count in (port_sweeps[:3] + other_sweeps[:3]):
            c = sweep_calls.get(sym, 0); p = sweep_puts.get(sym, 0)
            bias = "🟢" if c > p*2 else ("🔴" if p > c*2 else "🟡")
            tag = "💼" if sym in portfolio_syms else ""
            lines.append(f"  {tag}`{sym}` — {count} sweeps {bias} ({c}🐂 {p}🐻)")

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
                    # Get price via yfinance (reliable on weekends)
                    try:
                        ticker = yf.Ticker(sym)
                        price = ticker.fast_info.last_price or ticker.fast_info.previous_close or 0
                    except Exception:
                        price = 0
                    name = SYMBOL_NAMES.get(sym) or yf.Ticker(sym).info.get("shortName", sym)
                    bias = get_bias(sym)
                    upcoming.append((earn_date, sym, name, price, (earn_date-today).days, bias))
            except Exception:
                pass
        if upcoming:
            # Sort: Bullish first, then Neutral, then No data, then Bearish — within each by date
            sentiment_order = {"🟢 Bullish": 0, "🟡 Neutral": 1, "⚪ No flow data": 2, "🔴 Bearish": 3}
            upcoming.sort(key=lambda x: (sentiment_order.get(x[5] if x[5] != "⬜" else "⚪ No flow data", 2), x[0]))

            portfolio_earnings = [(d,s,n,p,dt,b) for d,s,n,p,dt,b in upcoming if s in PORTFOLIO]
            watchlist_earnings  = [(d,s,n,p,dt,b) for d,s,n,p,dt,b in upcoming if s not in PORTFOLIO]

            if portfolio_earnings:
                lines.append("\n📅 *Earnings This Week — Your Portfolio*")
                for earn_date, sym, name, price, days_to, bias in portfolio_earnings:
                    price_str = f"${price:.2f}" if price else "N/A"
                    bias_str = bias if bias != "⬜" else "⚪ No flow data"
                    lines.append(f"  {earn_date.strftime('%b %d')} ({days_to}d) — *{sym}* ({name}) {price_str} 💼 | {bias_str}")

            if watchlist_earnings:
                lines.append("\n📊 *Earnings This Week — Watchlist*")
                for earn_date, sym, name, price, days_to, bias in watchlist_earnings:
                    price_str = f"${price:.2f}" if price else "N/A"
                    bias_str = bias if bias != "⬜" else "⚪ No flow data"
                    lines.append(f"  {earn_date.strftime('%b %d')} ({days_to}d) — *{sym}* ({name}) {price_str} | {bias_str}")
    except Exception as e:
        lines.append(f"\n📅 Earnings section error: {e}")

    send("\n".join(lines))
    print("✅ Weekly summary sent.")

    # Store weekly report in WEEKLY_REPORTS sheet for historical tracking
    try:
        _ensure_tabs(svc, SHEET_ID, ["WEEKLY_REPORTS"])
        # Add header if first time
        existing = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range="WEEKLY_REPORTS!A1"
        ).execute()
        if not existing.get("values"):
            _append(svc, SHEET_ID, "WEEKLY_REPORTS", [["week_start", "date_saved", "total_alerts", "call_premium_k", "put_premium_k", "top_symbol", "summary"]])
        _append(svc, SHEET_ID, "WEEKLY_REPORTS", [[
            monday.strftime("%Y-%m-%d"),
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            len(week_rows),
            total_call_k,
            total_put_k,
            sym_counts.most_common(1)[0][0] if sym_counts else "",
            "\n".join(lines)[:5000]  # truncate to 5K chars for sheets
        ]])
        print("✅ Weekly report saved to WEEKLY_REPORTS sheet.")
    except Exception as e:
        print(f"  ⚠️ Could not save to sheets: {e}")

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
