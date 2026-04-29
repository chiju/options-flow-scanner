"""
Schwab CLI — check account, quotes, options chain from terminal
Usage:
  python schwab_cli.py auth              # first-time login
  python schwab_cli.py account           # portfolio summary
  python schwab_cli.py quote AAPL NVDA   # stock quotes
  python schwab_cli.py options UUUU      # options chain
  python schwab_cli.py orders            # recent orders
"""
import sys, os, json, warnings
warnings.filterwarnings("ignore", category=DeprecationWarning, module="authlib")
import schwab
from schwab import auth, client

APP_KEY    = os.environ.get("SCHWAB_APP_KEY", "")
APP_SECRET = os.environ.get("SCHWAB_APP_SECRET", "")
CALLBACK   = "https://127.0.0.1"
TOKEN_PATH = os.path.expanduser("~/.alpaca/schwab-token.json")


def get_client():
    if os.path.exists(TOKEN_PATH):
        return auth.client_from_token_file(TOKEN_PATH, APP_KEY, APP_SECRET)
    return auth.client_from_manual_flow(APP_KEY, APP_SECRET, CALLBACK, TOKEN_PATH)


def cmd_auth():
    print("Opening browser for Schwab login...")
    c = auth.client_from_manual_flow(APP_KEY, APP_SECRET, CALLBACK, TOKEN_PATH)
    print("✅ Authenticated! Token saved.")


def cmd_account():
    c = get_client()
    r = c.get_accounts(fields=[client.Client.Account.Fields.POSITIONS])
    data = r.json()
    for acct in data:
        a = acct['securitiesAccount']
        bal = a.get('currentBalances', {})
        print(f"\nAccount: {a['accountNumber']} ({a['type']})")
        print(f"  Liquidation value: ${bal.get('liquidationValue', 0):,.2f}")
        print(f"  Cash: ${bal.get('cashBalance', 0):,.2f}")
        print(f"  Buying power: ${bal.get('buyingPower', 0):,.2f}")
        
        positions = a.get('positions', [])
        if positions:
            print(f"\n  Positions ({len(positions)}):")
            for p in sorted(positions, key=lambda x: abs(x.get('marketValue',0)), reverse=True):
                sym = p['instrument'].get('symbol','?')
                qty = p.get('longQuantity', 0) or p.get('shortQuantity', 0)
                val = p.get('marketValue', 0)
                pl  = p.get('unrealizedGainLoss', 0)
                plp = p.get('unrealizedGainLossPercentage', 0)
                print(f"    {sym:<8} {qty:>8.2f}sh  ${val:>10,.2f}  P&L: ${pl:>+10,.2f} ({plp:+.1f}%)")


def cmd_quote(symbols):
    c = get_client()
    r = c.get_quotes(symbols)
    data = r.json()
    for sym, q in data.items():
        quote = q.get('quote', {})
        ref   = q.get('reference', {})
        price = quote.get('lastPrice', 0)
        chg   = quote.get('netChange', 0)
        chgp  = quote.get('netPercentChange', 0)
        bid   = quote.get('bidPrice', 0)
        ask   = quote.get('askPrice', 0)
        vol   = quote.get('totalVolume', 0)
        print(f"{sym}: ${price:.2f} ({chgp:+.2f}%) | bid:${bid:.2f} ask:${ask:.2f} | vol:{vol:,}")


def cmd_options(symbol):
    c = get_client()
    r = c.get_option_chain(
        symbol,
        contract_type=client.Client.Options.ContractType.PUT,
        strike_count=10,
        include_underlying_quote=True,
    )
    data = r.json()
    underlying = data.get('underlyingPrice', 0)
    print(f"\n{symbol} options | Underlying: ${underlying:.2f}")
    print(f"{'Expiry':<12} {'Strike':>7} {'Bid':>7} {'Ask':>7} {'IV%':>7} {'Delta':>7} {'OI':>8}")
    print("-"*65)
    
    for exp_date, strikes in data.get('putExpDateMap', {}).items():
        exp = exp_date.split(':')[0]
        for strike, contracts in strikes.items():
            for c_data in contracts:
                bid = c_data.get('bid', 0)
                ask = c_data.get('ask', 0)
                iv  = c_data.get('volatility', 0)
                delta = c_data.get('delta', 0)
                oi  = c_data.get('openInterest', 0)
                if bid > 0.05:
                    print(f"{exp:<12} ${float(strike):>6.0f} ${bid:>6.2f} ${ask:>6.2f} {iv:>6.1f}% {delta:>7.2f} {oi:>8,}")


def cmd_orders():
    c = get_client()
    r = c.get_orders_for_all_linked_accounts()
    orders = r.json()
    print(f"\nRecent orders ({len(orders)}):")
    for o in orders[:10]:
        sym = o.get('orderLegCollection', [{}])[0].get('instrument', {}).get('symbol', '?')
        side = o.get('orderLegCollection', [{}])[0].get('instruction', '?')
        qty  = o.get('quantity', 0)
        status = o.get('status', '?')
        price = o.get('price', o.get('filledPrice', 0))
        print(f"  {sym:<8} {side:<5} {qty:>8.2f} @ ${price:.2f} [{status}]")


if __name__ == "__main__":
    if not APP_KEY:
        print("Set SCHWAB_APP_KEY and SCHWAB_APP_SECRET in env first")
        print("Add to ~/.alpaca/options-paper.env:")
        print("  export SCHWAB_APP_KEY=your_key")
        print("  export SCHWAB_APP_SECRET=your_secret")
        sys.exit(1)
    
    cmd = sys.argv[1] if len(sys.argv) > 1 else "account"
    
    if cmd == "auth":
        cmd_auth()
    elif cmd == "account":
        cmd_account()
    elif cmd == "quote":
        cmd_quote(sys.argv[2:])
    elif cmd == "options":
        cmd_options(sys.argv[2])
    elif cmd == "orders":
        cmd_orders()
    else:
        print(__doc__)
