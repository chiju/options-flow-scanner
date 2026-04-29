"""
Schwab options chain fetcher — replaces Alpaca for real Greeks, OI, volume.
Returns same structure as scan_symbol() so sheets.py works unchanged.
"""
import os, warnings
from datetime import datetime, timedelta, date
warnings.filterwarnings("ignore")

from schwab import auth, client as schwab_client

APP_KEY    = os.environ.get("SCHWAB_APP_KEY", "")
APP_SECRET = os.environ.get("SCHWAB_APP_SECRET", "")
TOKEN_PATH = os.path.expanduser("~/.alpaca/schwab-token.json")

MIN_PREMIUM      = 25_000   # $25K
SWEEP_BLOCK_SIZE = 500
IV_SPIKE_THRESH  = 80.0
MAX_DTE          = 90


def get_schwab_client():
    return auth.client_from_token_file(TOKEN_PATH, APP_KEY, APP_SECRET)


def scan_symbol_schwab(c, sym: str, alerts_30d: list = None) -> dict | None:
    """Fetch options chain from Schwab with real Greeks + OI + volume."""
    from options_flow_scanner import get_volume_baseline, score_alert

    today  = date.today()
    cutoff = today + timedelta(days=MAX_DTE)

    try:
        r = c.get_option_chain(
            sym,
            contract_type=schwab_client.Client.Options.ContractType.ALL,
            strike_count=40,
            include_underlying_quote=True,
            from_date=today,
            to_date=cutoff,
        )
        data = r.json()
    except Exception as e:
        print(f"  [{sym}] Schwab error: {e}")
        return None

    underlying = data.get("underlying", {})
    spot = underlying.get("last") or underlying.get("mark") or 0

    calls, puts = [], []
    total_call_vol = total_put_vol = 0

    for cp_key, exp_key in [("callExpDateMap", "C"), ("putExpDateMap", "P")]:
        for exp_str, strikes in data.get(cp_key, {}).items():
            # exp_str like "2026-05-15:17"
            exp_date_str = exp_str.split(":")[0]
            try:
                expiry_date = datetime.strptime(exp_date_str, "%Y-%m-%d").date()
            except Exception:
                continue
            dte = (expiry_date - today).days
            if dte < 0 or dte > MAX_DTE:
                continue
            expiry_fmt = expiry_date.strftime("%b %d")

            for strike_str, contracts in strikes.items():
                d = contracts[0]
                strike = float(strike_str)
                bid    = d.get("bid", 0) or 0
                ask    = d.get("ask", 0) or 0
                mid    = (bid + ask) / 2 if ask else bid
                volume = d.get("totalVolume", 0) or 0
                oi     = d.get("openInterest", 0) or 0
                last   = d.get("last", 0) or 0
                delta  = d.get("delta")
                gamma  = d.get("gamma")
                theta  = d.get("theta")
                vega   = d.get("vega")
                iv_pct = round(d.get("volatility", 0), 1) or None

                if exp_key == "C": total_call_vol += volume
                else:              total_put_vol  += volume

                # Use OI as proxy when market closed (volume=0)
                effective_vol = volume if volume > 0 else 0
                if effective_vol == 0 or mid == 0: continue
                premium = mid * effective_vol * 100
                if premium < MIN_PREMIUM: continue

                vol_oi_ratio = round(volume / oi, 1) if oi > 0 else None
                iv_spike = bool(iv_pct and iv_pct > IV_SPIKE_THRESH and exp_key == "C")
                sweep    = volume >= SWEEP_BLOCK_SIZE and exp_key == "C"
                buy_sell = "BUY" if last >= mid else ("SELL" if last > 0 else "")

                opt_type = "CALL" if exp_key == "C" else "PUT"
                baseline = get_volume_baseline(sym, opt_type, alerts_30d or [])
                vol_vs_baseline = round(volume / baseline, 1) if baseline and baseline > 0 else None
                from options_flow_scanner import get_ascending_volume
                ascending_vol = get_ascending_volume(
                    f"{sym}{expiry_date.strftime('%y%m%d')}{exp_key}{int(strike*1000):08d}",
                    volume, alerts_30d or [])

                entry = {
                    "symbol": sym,
                    "contract": f"{sym}{expiry_date.strftime('%y%m%d')}{exp_key}{int(strike*1000):08d}",
                    "type": "CALL" if exp_key == "C" else "PUT",
                    "strike": strike, "expiry": expiry_fmt, "dte": dte,
                    "volume": int(volume), "premium": int(premium),
                    "delta": round(delta, 3) if delta is not None else None,
                    "gamma": round(gamma, 4) if gamma is not None else None,
                    "theta": round(theta, 4) if theta is not None else None,
                    "vega":  round(vega, 4)  if vega  is not None else None,
                    "iv": iv_pct, "mid": round(mid, 2),
                    "oi": oi, "vol_oi_ratio": vol_oi_ratio,
                    "sweep": sweep, "iv_spike": iv_spike, "buy_sell": buy_sell,
                    "vol_vs_baseline": vol_vs_baseline,
                    "ascending_vol": ascending_vol,
                }
                entry["score"] = score_alert(entry)

                if exp_key == "C": calls.append(entry)
                else:              puts.append(entry)

    if not calls and not puts:
        return None

    pc_ratio = round(total_put_vol / total_call_vol, 2) if total_call_vol > 0 else None
    return {
        "symbol": sym, "spot": spot,
        "calls": calls, "puts": puts,
        "call_vol": total_call_vol, "put_vol": total_put_vol,
        "pc_ratio": pc_ratio,
    }


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    c = get_schwab_client()
    result = scan_symbol_schwab(c, sym)
    if result:
        print(f"{sym} | spot:${result['spot']:.2f} | P/C:{result['pc_ratio']}")
        print(f"Calls: {len(result['calls'])} | Puts: {len(result['puts'])}")
        for e in sorted(result['calls']+result['puts'], key=lambda x: x['premium'], reverse=True)[:5]:
            print(f"  {e['type']} ${e['strike']} {e['expiry']} | vol:{e['volume']:,} "
                  f"OI:{e['oi']:,} delta:{e['delta']} gamma:{e['gamma']} "
                  f"IV:{e['iv']}% | ${e['premium']//1000}K | {e['buy_sell']}")
