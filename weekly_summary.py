"""
Weekly summary report — runs every Friday at 4pm ET.
Reads the week's UNUSUAL_ALERTS from Google Sheets and sends a digest to Telegram.
"""
import os
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

    # Biggest flows
    flows = []
    for row in week_rows:
        try:
            flows.append((int(row[7]), row[1], row[2], row[3], row[4]))  # premium_k, sym, type, strike, expiry
        except (ValueError, IndexError):
            continue
    flows.sort(reverse=True)

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
    send("\n".join(lines))
    print("✅ Weekly summary sent.")


if __name__ == "__main__":
    run_weekly_summary()
