"""
NSE Stock Futures Analysis & Execution Engine.

Wires kite_client + futures_signal + ai_orchestrator + decision together.

Safety rules:
  - AI/analysis is called exactly once, at the start, before any monitoring loop.
  - force_exit_time is checked on every loop iteration, unconditionally.
  - stop_event is checked on every loop iteration; when set, open positions
    are squared off immediately.
  - Quantity is always settings["lots"] × the LIVE lot_size from Kite's
    instrument dump — never hardcoded.
  - SL is mandatory for futures positions (set sl_enabled=true always).

DISCLAIMER: For educational and research purposes only. Not financial advice.
"""

import datetime
import json
import threading
import time

import futures_signal
import option_signal
import ai_orchestrator
import decision as decision_mod
from kite_client import KiteClient

MARKET_OPEN_TIME = datetime.time(9, 15)


def load_settings(path="settings.json"):
    settings = {}
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                settings = json.load(f)
        except Exception:
            settings = {}
    elif os.path.exists("settings.example.json"):
        try:
            with open("settings.example.json", "r") as f:
                settings = json.load(f)
        except Exception:
            settings = {}

    env_keys = {
        "KITE_API_KEY": "kite_api_key",
        "KITE_API_SECRET": "kite_api_secret",
        "GEMINI_API_KEY": "gemini_api_key",
        "GEMINI_MODEL": "gemini_model",
        "ANTHROPIC_API_KEY": "anthropic_api_key",
        "CLAUDE_MODEL": "claude_model",
        "AI_PROVIDER": "ai_provider",
    }
    for env_var, key in env_keys.items():
        v = os.environ.get(env_var)
        if v:
            settings[key] = v.strip()

    if os.environ.get("DRY_RUN") is not None:
        settings["dry_run"] = os.environ.get("DRY_RUN", "").lower() in ("true", "1", "yes")

    return settings


def _emit(on_event, payload):
    """Fire-and-forget structured event for the UI. Never raises into trading logic."""
    if on_event is None:
        return
    try:
        on_event(payload)
    except Exception as exc:  # noqa: BLE001
        print(f"[engine] on_event callback raised {exc!r} (ignored)")


def _parse_hhmm(hhmm_str):
    hour, minute = hhmm_str.split(":")
    return datetime.time(int(hour), int(minute))


# ---------------------------------------------------------------------------
# Index spot and derivative symbol mappings (NSE / NFO)
# ---------------------------------------------------------------------------

INDEX_DERIVATIVE_TO_SPOT = {
    "NIFTY": "NIFTY 50",
    "NIFTY50": "NIFTY 50",
    "NIFTY 50": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
    "NIFTYBANK": "NIFTY BANK",
    "NIFTY BANK": "NIFTY BANK",
    "FINNIFTY": "NIFTY FIN SERVICE",
    "NIFTYFINSERVICE": "NIFTY FIN SERVICE",
    "NIFTY FIN SERVICE": "NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NIFTY MID SELECT",
    "NIFTYMIDSELECT": "NIFTY MID SELECT",
    "NIFTY MID SELECT": "NIFTY MID SELECT",
    "NIFTYNXT50": "NIFTY NEXT 50",
    "NIFTY NEXT 50": "NIFTY NEXT 50",
}

INDEX_SPOT_TO_DERIVATIVE = {
    "NIFTY 50": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
    "NIFTY FIN SERVICE": "FINNIFTY",
    "NIFTY MID SELECT": "MIDCPNIFTY",
    "NIFTY NEXT 50": "NIFTYNXT50",
}


def resolve_spot_tradingsymbol(ticker: str) -> str:
    """Returns the NSE cash/spot tradingsymbol (e.g. 'NIFTY 50' for 'NIFTY', 'RELIANCE' for 'RELIANCE')."""
    cleaned = (ticker or "").strip().upper()
    return INDEX_DERIVATIVE_TO_SPOT.get(cleaned, cleaned)


def resolve_derivative_name(ticker: str) -> str:
    """Returns the NFO derivative name (e.g. 'NIFTY' for 'NIFTY 50', 'RELIANCE' for 'RELIANCE')."""
    cleaned = (ticker or "").strip().upper()
    if cleaned in INDEX_SPOT_TO_DERIVATIVE:
        return INDEX_SPOT_TO_DERIVATIVE[cleaned]
    if cleaned in INDEX_DERIVATIVE_TO_SPOT:
        spot = INDEX_DERIVATIVE_TO_SPOT[cleaned]
        return INDEX_SPOT_TO_DERIVATIVE.get(spot, cleaned)
    return cleaned


def get_spot_instrument(kite_client, ticker: str) -> dict:
    """
    Finds the NSE spot instrument for a stock (e.g. RELIANCE) or index (e.g. NIFTY 50).
    Handles index segment differences where segment may be 'INDICES'.
    """
    spot_symbol = resolve_spot_tradingsymbol(ticker)
    norm_ticker = (ticker or "").strip().upper()
    spot_instruments = (
        kite_client.get_instruments("NSE")
        if isinstance(getattr(kite_client, "_instruments_cache", None), dict)
        else kite_client.kite.instruments("NSE")
    )

    # Priority 1: Exact match on resolved spot tradingsymbol
    for i in spot_instruments:
        if i.get("tradingsymbol") == spot_symbol:
            return i

    # Priority 2: Exact match on raw ticker
    for i in spot_instruments:
        if i.get("tradingsymbol") == norm_ticker:
            return i

    # Priority 3: Fuzzy search for indices in INDICES segment or INDEX type
    for i in spot_instruments:
        ts = (i.get("tradingsymbol") or "").upper()
        name = (i.get("name") or "").upper()
        seg = i.get("segment", "")
        itype = i.get("instrument_type", "")
        if (seg == "INDICES" or itype == "INDEX") and (norm_ticker in ts or norm_ticker in name):
            return i

    raise ValueError(f"Could not find NSE spot instrument for {ticker!r}")


# ---------------------------------------------------------------------------
# Universe builder — stock and index futures
# ---------------------------------------------------------------------------

def build_futures_universe(kite_client, ticker: str, exchange: str, log=print) -> dict:
    """
    Fetches the nearest-expiry NSE futures contract for the given stock or index ticker.
    Returns a universe dict analogous to algo_ai's build_option_universe().
    """
    log(f"[engine] Building futures universe for {ticker} ({exchange})...")

    # Derivative name in NFO (e.g. "NIFTY" for "NIFTY 50", "RELIANCE" for "RELIANCE")
    derivative_name = resolve_derivative_name(ticker)

    # Nearest-expiry futures instrument from NSE F&O
    futures_instrument = kite_client.get_nfo_futures_instrument(derivative_name)
    futures_token = futures_instrument["instrument_token"]
    futures_tradingsymbol = futures_instrument["tradingsymbol"]
    lot_size = futures_instrument["lot_size"]
    nearest_expiry = futures_instrument["expiry"]

    log(f"[engine] Futures: {futures_tradingsymbol} expiry={nearest_expiry} lot_size={lot_size}")

    # Spot instrument for the underlying (stock or index) on NSE
    spot_instrument = get_spot_instrument(kite_client, ticker)
    spot_token = spot_instrument["instrument_token"]
    spot_tradingsymbol = spot_instrument["tradingsymbol"]

    # Initial quote for spot price
    quote_key = f"NSE:{spot_tradingsymbol}"
    quote_res = kite_client.get_quote([quote_key])
    initial_quote = quote_res.get(quote_key)
    if not initial_quote:
        initial_quote = next(iter(quote_res.values()), None)
    if not initial_quote:
        raise ValueError(f"Could not fetch quote for spot symbol {quote_key!r}")
    spot_price = initial_quote["last_price"]
    log(f"[engine] {spot_tradingsymbol} spot price = ₹{spot_price}")

    return {
        "ticker":                 ticker.upper(),
        "derivative_name":        derivative_name,
        "exchange":               exchange.upper(),
        "spot_token":             spot_token,
        "spot_instrument":        spot_instrument,
        "spot_tradingsymbol":     spot_tradingsymbol,
        "initial_quote":          initial_quote,
        "futures_instrument":     futures_instrument,
        "futures_token":          futures_token,
        "futures_tradingsymbol":  futures_tradingsymbol,
        "nearest_expiry":         nearest_expiry,
        "lot_size":               lot_size,
    }


# ---------------------------------------------------------------------------
# Selectable Futures Symbols (NSE F&O Universe)
# ---------------------------------------------------------------------------

_FUTURES_SYMBOLS_CACHE = {"timestamp": 0.0, "data": []}

DEFAULT_NSE_FNO_SYMBOLS = [
    {"symbol": "NIFTY", "name": "NIFTY 50", "lot_size": 65},
    {"symbol": "BANKNIFTY", "name": "NIFTY BANK", "lot_size": 30},
    {"symbol": "FINNIFTY", "name": "NIFTY FIN SERVICE", "lot_size": 65},
    {"symbol": "MIDCPNIFTY", "name": "NIFTY MID SELECT", "lot_size": 120},
    {"symbol": "RELIANCE", "name": "RELIANCE", "lot_size": 250},
    {"symbol": "TCS", "name": "TCS", "lot_size": 175},
    {"symbol": "INFY", "name": "INFY", "lot_size": 400},
    {"symbol": "HDFCBANK", "name": "HDFCBANK", "lot_size": 550},
    {"symbol": "ICICIBANK", "name": "ICICIBANK", "lot_size": 700},
    {"symbol": "SBIN", "name": "SBIN", "lot_size": 1500},
    {"symbol": "BHARTIARTL", "name": "BHARTIARTL", "lot_size": 475},
    {"symbol": "LT", "name": "LT", "lot_size": 150},
    {"symbol": "AXISBANK", "name": "AXISBANK", "lot_size": 625},
    {"symbol": "KOTAKBANK", "name": "KOTAKBANK", "lot_size": 400},
    {"symbol": "TATAMOTORS", "name": "TATAMOTORS", "lot_size": 1425},
    {"symbol": "TATASTEEL", "name": "TATASTEEL", "lot_size": 5500},
    {"symbol": "ITC", "name": "ITC", "lot_size": 1600},
    {"symbol": "HINDUNILVR", "name": "HINDUNILVR", "lot_size": 300},
    {"symbol": "BAJFINANCE", "name": "BAJFINANCE", "lot_size": 125},
    {"symbol": "MARUTI", "name": "MARUTI", "lot_size": 50},
    {"symbol": "SUNPHARMA", "name": "SUNPHARMA", "lot_size": 350},
    {"symbol": "TITAN", "name": "TITAN", "lot_size": 175},
    {"symbol": "ADANIENT", "name": "ADANIENT", "lot_size": 300},
    {"symbol": "ADANIPORTS", "name": "ADANIPORTS", "lot_size": 400},
    {"symbol": "NTPC", "name": "NTPC", "lot_size": 1500},
    {"symbol": "POWERGRID", "name": "POWERGRID", "lot_size": 1800},
    {"symbol": "ONGC", "name": "ONGC", "lot_size": 3850},
    {"symbol": "COALINDIA", "name": "COALINDIA", "lot_size": 2100},
    {"symbol": "TECHM", "name": "TECHM", "lot_size": 600},
    {"symbol": "WIPRO", "name": "WIPRO", "lot_size": 1500},
    {"symbol": "HCLTECH", "name": "HCLTECH", "lot_size": 350},
    {"symbol": "JSWSTEEL", "name": "JSWSTEEL", "lot_size": 675},
    {"symbol": "HINDALCO", "name": "HINDALCO", "lot_size": 1400},
    {"symbol": "VEDL", "name": "VEDL", "lot_size": 2000},
    {"symbol": "TRENT", "name": "TRENT", "lot_size": 100},
    {"symbol": "BEL", "name": "BEL", "lot_size": 2700},
    {"symbol": "HAL", "name": "HAL", "lot_size": 150},
    {"symbol": "CANBK", "name": "CANBK", "lot_size": 6750},
    {"symbol": "BANKBARODA", "name": "BANKBARODA", "lot_size": 2925},
    {"symbol": "INDUSINDBK", "name": "INDUSINDBK", "lot_size": 500},
    {"symbol": "FEDERALBNK", "name": "FEDERALBNK", "lot_size": 5000},
    {"symbol": "IDFCFIRSTB", "name": "IDFCFIRSTB", "lot_size": 7500},
    {"symbol": "PNB", "name": "PNB", "lot_size": 8000},
    {"symbol": "PFC", "name": "PFC", "lot_size": 1300},
    {"symbol": "RECLTD", "name": "RECLTD", "lot_size": 1400},
    {"symbol": "BPCL", "name": "BPCL", "lot_size": 1800},
    {"symbol": "IOC", "name": "IOC", "lot_size": 4875},
    {"symbol": "GAIL", "name": "GAIL", "lot_size": 3250},
    {"symbol": "TATAPOWER", "name": "TATAPOWER", "lot_size": 1700},
    {"symbol": "BHEL", "name": "BHEL", "lot_size": 2625},
    {"symbol": "SAIL", "name": "SAIL", "lot_size": 4000},
    {"symbol": "NMDC", "name": "NMDC", "lot_size": 3375},
    {"symbol": "NATIONALUM", "name": "NATIONALUM", "lot_size": 5000},
    {"symbol": "JINDALSTEL", "name": "JINDALSTEL", "lot_size": 625},
    {"symbol": "DLF", "name": "DLF", "lot_size": 825},
    {"symbol": "GODREJPROP", "name": "GODREJPROP", "lot_size": 200},
    {"symbol": "M&M", "name": "M&M", "lot_size": 350},
    {"symbol": "EICHERMOT", "name": "EICHERMOT", "lot_size": 150},
    {"symbol": "HEROMOTOCO", "name": "HEROMOTOCO", "lot_size": 150},
    {"symbol": "TVSMOTOR", "name": "TVSMOTOR", "lot_size": 175},
    {"symbol": "BAJAJ-AUTO", "name": "BAJAJ-AUTO", "lot_size": 75},
    {"symbol": "DRREDDY", "name": "DRREDDY", "lot_size": 125},
    {"symbol": "CIPLA", "name": "CIPLA", "lot_size": 375},
    {"symbol": "DIVISLAB", "name": "DIVISLAB", "lot_size": 100},
    {"symbol": "LUPIN", "name": "LUPIN", "lot_size": 425},
    {"symbol": "AUROPHARMA", "name": "AUROPHARMA", "lot_size": 550},
    {"symbol": "APOLLOHOSP", "name": "APOLLOHOSP", "lot_size": 125},
    {"symbol": "BRITANNIA", "name": "BRITANNIA", "lot_size": 200},
    {"symbol": "NESTLEIND", "name": "NESTLEIND", "lot_size": 25},
    {"symbol": "TATACONSUM", "name": "TATACONSUM", "lot_size": 550},
    {"symbol": "DABUR", "name": "DABUR", "lot_size": 1250},
    {"symbol": "MARICO", "name": "MARICO", "lot_size": 1200},
    {"symbol": "COLPAL", "name": "COLPAL", "lot_size": 200},
    {"symbol": "SHREECEM", "name": "SHREECEM", "lot_size": 25},
    {"symbol": "ULTRACEMCO", "name": "ULTRACEMCO", "lot_size": 100},
    {"symbol": "AMBUJACEM", "name": "AMBUJACEM", "lot_size": 900},
    {"symbol": "ACC", "name": "ACC", "lot_size": 300},
    {"symbol": "GRASIM", "name": "GRASIM", "lot_size": 250},
    {"symbol": "SIEMENS", "name": "SIEMENS", "lot_size": 125},
    {"symbol": "ABB", "name": "ABB", "lot_size": 125},
    {"symbol": "HAVELLS", "name": "HAVELLS", "lot_size": 500},
    {"symbol": "POLYCAB", "name": "POLYCAB", "lot_size": 125},
    {"symbol": "VOLTAS", "name": "VOLTAS", "lot_size": 300},
    {"symbol": "CUMMINSIND", "name": "CUMMINSIND", "lot_size": 200},
    {"symbol": "NAUKRI", "name": "NAUKRI", "lot_size": 125},
    {"symbol": "IRCTC", "name": "IRCTC", "lot_size": 875},
    {"symbol": "INDIGO", "name": "INDIGO", "lot_size": 150},
    {"symbol": "CHOLAFIN", "name": "CHOLAFIN", "lot_size": 500},
    {"symbol": "BAJAJFINSV", "name": "BAJAJFINSV", "lot_size": 250},
    {"symbol": "MUTHOOTFIN", "name": "MUTHOOTFIN", "lot_size": 300},
    {"symbol": "SRF", "name": "SRF", "lot_size": 200},
    {"symbol": "PIDILITIND", "name": "PIDILITIND", "lot_size": 250},
    {"symbol": "ASIANPAINT", "name": "ASIANPAINT", "lot_size": 200},
]


def get_active_futures_symbols(kite_client=None, query=None, max_lot_size=None):
    """
    Returns list of available NSE F&O stock futures.
    If kite_client is logged in, fetches live NFO dump (cached for 1 hour).
    Otherwise returns curated list of top NSE F&O symbols.
    Filters out symbols where lot_size >= max_lot_size if specified.
    """
    now = time.time()
    symbols = []

    if kite_client and getattr(kite_client, "kite", None):
        if _FUTURES_SYMBOLS_CACHE["data"] and (now - _FUTURES_SYMBOLS_CACHE["timestamp"] < 3600):
            symbols = _FUTURES_SYMBOLS_CACHE["data"]
        else:
            try:
                today = datetime.date.today()
                instruments = (
                    kite_client.get_instruments("NFO")
                    if isinstance(getattr(kite_client, "_instruments_cache", None), dict)
                    else kite_client.kite.instruments("NFO")
                )
                stock_futs = [
                    i for i in instruments
                    if i["instrument_type"] == "FUT" and i["expiry"] >= today
                ]
                by_name = {}
                for f in stock_futs:
                    name = f["name"]
                    by_name.setdefault(name, []).append(f)

                rollover_days = 3
                if kite_client and getattr(kite_client, "settings", None):
                    rollover_days = kite_client.settings.get("expiry_rollover_days", 3)

                symbols = []
                for name, flist in sorted(by_name.items()):
                    flist.sort(key=lambda x: x["expiry"])
                    chosen = flist[0]
                    if len(flist) > 1 and (chosen["expiry"] - today).days <= rollover_days:
                        chosen = flist[1]
                    symbols.append({
                        "symbol": name,
                        "name": name,
                        "tradingsymbol": chosen["tradingsymbol"],
                        "expiry": str(chosen["expiry"]),
                        "lot_size": chosen["lot_size"],
                    })
                _FUTURES_SYMBOLS_CACHE["data"] = symbols
                _FUTURES_SYMBOLS_CACHE["timestamp"] = now
            except Exception:
                symbols = DEFAULT_NSE_FNO_SYMBOLS
    else:
        symbols = DEFAULT_NSE_FNO_SYMBOLS

    if max_lot_size is not None and max_lot_size > 0:
        symbols = [s for s in symbols if s.get("lot_size", 0) < max_lot_size]

    if query:
        q = query.strip().upper()
        symbols = [s for s in symbols if q in s["symbol"].upper() or q in s.get("tradingsymbol", "").upper()]

    return symbols


def _setup_universe_and_ticker(kite_client, settings, log, on_event, on_tick) -> dict:
    ticker = settings["ticker"]
    exchange = settings.get("exchange", "NSE")
    universe = build_futures_universe(kite_client, ticker, exchange, log)

    _emit(on_event, {
        "type":                  "universe_ready",
        "ticker":                universe["ticker"],
        "futures_tradingsymbol": universe["futures_tradingsymbol"],
        "nearest_expiry":        str(universe["nearest_expiry"]),
        "lot_size":              universe["lot_size"],
        "spot_price":            universe["initial_quote"]["last_price"],
    })

    all_tokens = [universe["spot_token"], universe["futures_token"]]
    kite_client.start_ticker(all_tokens, on_tick=on_tick)
    _emit(on_event, {"type": "progress", "stage": "ticker", "message": "Connecting live market data feed..."})
    if not kite_client.wait_for_ticker(timeout=10):
        log("[engine] WARNING: ticker did not confirm connection within 10s, continuing with REST fallbacks")

    return universe


# ---------------------------------------------------------------------------
# Option Chain Data Gathering (ATM +/- 5 strikes)
# ---------------------------------------------------------------------------

def build_option_chain_data(kite_client, ticker: str, spot_price: float = None, settings: dict = None, log=print) -> dict:
    """
    Fetches the nearest-expiry NFO option chain for ticker (stock or index),
    determines strike interval, ATM strike, and 11 strikes (ATM +/- 5 strikes).
    Collects live OI, volume, and LTP for CE & PE, and calculates:
      - PCR and Proximity-weighted PCR
      - Resistance Wall (highest CE OI above ATM)
      - Support Wall (highest PE OI below ATM)
      - Directional move probabilities
      - ATM Call, Put, and Straddle (Net) premiums with required moves
      - Structured 11-row table for web dashboard and markdown reports.
    """
    settings = settings or {}
    pcr_threshold = settings.get("pcr_threshold", 0.15)
    profit_margin_factor = settings.get("profit_margin_factor", 1.15)

    spot_symbol = resolve_spot_tradingsymbol(ticker)
    deriv_name = resolve_derivative_name(ticker)

    if not spot_price or spot_price <= 0:
        try:
            q = kite_client.get_quote([f"NSE:{spot_symbol}"])
            spot_price = q[f"NSE:{spot_symbol}"]["last_price"]
        except Exception:
            spot_price = 0.0

    try:
        nearest_expiry, chain = kite_client.get_nfo_option_chain(deriv_name)
    except Exception as exc:  # noqa: BLE001
        log(f"[engine] get_nfo_option_chain failed for {deriv_name}: {exc!r}")
        return None

    if not chain or not spot_price or spot_price <= 0:
        return None

    sorted_strikes = sorted({float(i["strike"]) for i in chain if i.get("strike") is not None})
    if len(sorted_strikes) < 2:
        return None

    try:
        strike_interval = option_signal.get_strike_interval(sorted_strikes)
        atm_strike = option_signal.get_atm_strike(spot_price, strike_interval)
        target_strikes = option_signal.get_strikes_around_atm(atm_strike, strike_interval, n=5)
    except Exception as exc:  # noqa: BLE001
        log(f"[engine] Strike interval/ATM calculation failed for {ticker}: {exc!r}")
        return None

    instruments_by_strike = {s: {} for s in target_strikes}
    for i in chain:
        s = float(i["strike"])
        if s in instruments_by_strike:
            instruments_by_strike[s][i["instrument_type"]] = i

    tokens = []
    token_meta = {}
    for s in target_strikes:
        for opt_type in ("CE", "PE"):
            instr = instruments_by_strike[s].get(opt_type)
            if instr:
                t = instr["instrument_token"]
                tokens.append(t)
                token_meta[t] = (s, opt_type, instr["tradingsymbol"])

    ticks = kite_client.get_all_ticks() if hasattr(kite_client, "get_all_ticks") else {}
    missing_symbols = [
        f"NFO:{token_meta[t][2]}"
        for t in tokens
        if t not in ticks or ticks[t].get("oi") is None
    ]

    fallback_quotes = {}
    if missing_symbols:
        try:
            fallback_quotes = kite_client.get_quote(missing_symbols)
        except Exception as exc:  # noqa: BLE001
            log(f"[engine] Fallback quote for option chain failed ({exc!r})")

    strike_oi_data = {s: {"CE": 0, "PE": 0} for s in target_strikes}
    strike_vol_data = {s: {"CE": 0, "PE": 0} for s in target_strikes}
    strike_ltp_data = {s: {"CE": 0.0, "PE": 0.0} for s in target_strikes}

    for t in tokens:
        s, opt_type, tradingsymbol = token_meta[t]
        tick = ticks.get(t)
        if tick and tick.get("oi") is not None:
            strike_oi_data[s][opt_type] = tick.get("oi") or 0
            strike_vol_data[s][opt_type] = tick.get("volume_traded") or 0
            strike_ltp_data[s][opt_type] = tick.get("last_price") or 0.0
        else:
            q = fallback_quotes.get(f"NFO:{tradingsymbol}") or {}
            strike_oi_data[s][opt_type] = q.get("oi") or 0
            strike_vol_data[s][opt_type] = q.get("volume") or 0
            strike_ltp_data[s][opt_type] = q.get("last_price") or 0.0

    oi_walls = option_signal.compute_oi_walls(strike_oi_data, atm_strike)

    # Optional OI change ratios for walls and ATM
    strike_oi_change_ratios = {}
    try:
        today = datetime.date.today()
        from_date = today - datetime.timedelta(days=5)
        to_date = today - datetime.timedelta(days=1)
        check_strikes = [atm_strike]
        if oi_walls.get("resistance_strike"):
            check_strikes.append(oi_walls["resistance_strike"])
        if oi_walls.get("support_strike"):
            check_strikes.append(oi_walls["support_strike"])

        for cs in set(check_strikes):
            strike_oi_change_ratios[cs] = {}
            for opt_type in ("CE", "PE"):
                instr = instruments_by_strike.get(cs, {}).get(opt_type)
                if instr:
                    hist = kite_client.get_daily_history(instr["instrument_token"], from_date, to_date, oi=True)
                    if hist and hist[-1].get("oi") and hist[-1]["oi"] > 0:
                        cur_oi = strike_oi_data[cs].get(opt_type, 0)
                        strike_oi_change_ratios[cs][opt_type] = cur_oi / hist[-1]["oi"]
    except Exception as exc:  # noqa: BLE001
        log(f"[engine] Option chain historical OI change fetch skipped ({exc!r})")

    oi_result = option_signal.compute_oi_bias(
        strike_oi_data,
        pcr_threshold=pcr_threshold,
        spot_price=spot_price,
        strike_interval=strike_interval,
        strike_oi_change_ratios=strike_oi_change_ratios,
    )

    res_strike = oi_walls.get("resistance_strike")
    sup_strike = oi_walls.get("support_strike")
    res_chg = strike_oi_change_ratios.get(res_strike, {}).get("CE") if res_strike else None
    sup_chg = strike_oi_change_ratios.get(sup_strike, {}).get("PE") if sup_strike else None

    prob_result = option_signal.compute_direction_probability(
        support_oi=oi_walls.get("support_oi", 0),
        resistance_oi=oi_walls.get("resistance_oi", 0),
        support_strike=sup_strike,
        resistance_strike=res_strike,
        spot_price=spot_price,
        strike_interval=strike_interval,
        support_oi_change_ratio=sup_chg,
        resistance_oi_change_ratio=res_chg,
    )

    ce_ltp = strike_ltp_data.get(atm_strike, {}).get("CE")
    pe_ltp = strike_ltp_data.get(atm_strike, {}).get("PE")
    premium_result = option_signal.compute_premiums(ce_ltp, pe_ltp, profit_margin_factor=profit_margin_factor)

    rows = []
    for s in target_strikes:
        ce_oi = strike_oi_data.get(s, {}).get("CE", 0)
        ce_vol = strike_vol_data.get(s, {}).get("CE", 0)
        ce_price = strike_ltp_data.get(s, {}).get("CE", 0.0)
        pe_oi = strike_oi_data.get(s, {}).get("PE", 0)
        pe_vol = strike_vol_data.get(s, {}).get("PE", 0)
        pe_price = strike_ltp_data.get(s, {}).get("PE", 0.0)
        rows.append({
            "strike": s,
            "is_atm": (s == atm_strike),
            "is_resistance": (s == res_strike),
            "is_support": (s == sup_strike),
            "ce": {"oi": ce_oi, "volume": ce_vol, "ltp": ce_price},
            "pe": {"oi": pe_oi, "volume": pe_vol, "ltp": pe_price},
        })

    return {
        "ticker": ticker.upper(),
        "spot_symbol": spot_symbol,
        "derivative_name": deriv_name,
        "nearest_expiry": str(nearest_expiry),
        "strike_interval": strike_interval,
        "atm_strike": atm_strike,
        "spot_price": spot_price,
        "pcr": oi_result["pcr"],
        "weighted_pcr": oi_result["weighted_pcr"],
        "oi_bias": oi_result["oi_bias"],
        "total_call_oi": oi_result["total_call_oi"],
        "total_put_oi": oi_result["total_put_oi"],
        "resistance_strike": res_strike,
        "resistance_oi": oi_walls.get("resistance_oi", 0),
        "support_strike": sup_strike,
        "support_oi": oi_walls.get("support_oi", 0),
        "upside_probability": prob_result["upside_probability"],
        "downside_probability": prob_result["downside_probability"],
        "ce_premium": premium_result["ce_premium"],
        "pe_premium": premium_result["pe_premium"],
        "net_premium": premium_result["net_premium"],
        "required_move_call": premium_result["required_move_call"],
        "required_move_put": premium_result["required_move_put"],
        "required_move_both": premium_result["required_move_both"],
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Technical signal gathering (fast, from live ticks + history)
# ---------------------------------------------------------------------------

def gather_technical_context(kite_client, universe, settings, log) -> dict:
    """
    Gathers the lightweight real-time technical picture that is injected into
    the AI orchestrator's market context — VWAP, ORB, momentum, gap.
    """
    spot_price = universe["initial_quote"]["last_price"]
    day_open = universe["initial_quote"]["ohlc"]["open"]
    points_moved = spot_price - day_open

    # VWAP from live futures tick (same pattern as algo_ai)
    vwap = None
    vwap_bias = "NEUTRAL"
    try:
        tick = kite_client.get_tick(universe["futures_token"])
        if tick and tick.get("average_price"):
            vwap = tick["average_price"]
        else:
            q = kite_client.get_quote([f"NFO:{universe['futures_tradingsymbol']}"])
            q_data = q.get(f"NFO:{universe['futures_tradingsymbol']}")
            if q_data and q_data.get("average_price"):
                vwap = q_data["average_price"]
        if vwap:
            vb = futures_signal.compute_vwap_bias(spot_price, vwap)
            vwap_bias = vb["vwap_bias"]
            log(f"[engine] VWAP: {vwap:.2f} → {vwap_bias}")
    except Exception as exc:  # noqa: BLE001
        log(f"[engine] VWAP fetch failed ({exc!r}), falling back to NEUTRAL")

    # ORB
    orb_result = {"orb_bias": "NEUTRAL", "orb_high": None, "orb_low": None}
    try:
        orb_minutes = settings.get("orb_minutes", 15)
        today = datetime.date.today()
        market_open = datetime.datetime.combine(today, MARKET_OPEN_TIME)
        orb_end = market_open + datetime.timedelta(minutes=orb_minutes)
        if datetime.datetime.now() >= orb_end:
            candles = kite_client.get_intraday_history(universe["spot_token"], market_open, orb_end, interval="minute")
            if candles:
                orb_high = max(c["high"] for c in candles)
                orb_low = min(c["low"] for c in candles)
                orb_bias = futures_signal.compute_orb_bias(spot_price, orb_high, orb_low)
                orb_result = {"orb_bias": orb_bias, "orb_high": orb_high, "orb_low": orb_low}
                log(f"[engine] ORB: {orb_low:.2f}–{orb_high:.2f} → {orb_bias}")
    except Exception as exc:  # noqa: BLE001
        log(f"[engine] ORB fetch failed ({exc!r}), falling back to NEUTRAL")

    # Daily history for gap/ATR
    avg_range = None
    gap_result = {"large_gap": False, "gap": None, "avg_range": None}
    expected_move = None
    try:
        lookback = settings.get("range_lookback_days", 5)
        today = datetime.date.today()
        from_date = today - datetime.timedelta(days=lookback * 3 + 5)
        to_date = today - datetime.timedelta(days=1)
        daily_candles = kite_client.get_daily_history(universe["spot_token"], from_date, to_date)
        if daily_candles:
            recent = daily_candles[-lookback:]
            daily_ranges = [c["high"] - c["low"] for c in recent]
            if daily_ranges:
                gap_result = futures_signal.compute_gap_flag(
                    daily_ranges, day_open, daily_candles[-1]["close"],
                    settings.get("gap_threshold_factor", 0.5),
                )
                avg_range = gap_result["avg_range"]
                log(f"[engine] Gap: {gap_result['gap']:+.2f} avg_range={avg_range:.2f} large_gap={gap_result['large_gap']}")
            # ATR-based expected move
            atr = futures_signal.compute_atr_expected_move(daily_candles)
            oc_range = futures_signal.compute_avg_open_to_close_range(daily_candles[-15:])
            if atr is not None and oc_range is not None:
                expected_move = (atr + oc_range) / 2
            elif atr is not None:
                expected_move = atr
            if expected_move:
                log(f"[engine] Expected move (ATR blend): ±{expected_move:.1f} pts")
    except Exception as exc:  # noqa: BLE001
        log(f"[engine] Daily history/gap/ATR fetch failed ({exc!r})")

    # Momentum bias
    momentum_result = futures_signal.compute_momentum_bias(
        points_moved, expected_move,
        min_fraction=settings.get("momentum_min_fraction", 0.25),
        max_fraction=settings.get("momentum_max_fraction", 0.65),
    )
    log(f"[engine] Momentum: moved {points_moved:+.1f} pts → {momentum_result['momentum_bias']}")

    # Chartink Intraday Buy Setup (ADX + Volume WMA + Multiple EMA)
    # Ref: https://chartink.com/screener/my-intraday-buy-setup-adx
    adx_setup_result = {"status": "UNKNOWN", "summary": "Intraday 5m data unavailable"}
    try:
        today = datetime.date.today()
        market_open = datetime.datetime.combine(today, MARKET_OPEN_TIME)
        now = datetime.datetime.now()
        if now > market_open:
            candles_5min = kite_client.get_intraday_history(universe["spot_token"], market_open, now, interval="5minute")
            prev_hlc = daily_candles[-1] if daily_candles else None
            adx_setup_result = futures_signal.evaluate_chartink_adx_setup(
                candles_5min, prev_day_hlc=prev_hlc, current_open=day_open
            )
            log(f"[engine] Chartink ADX Setup: {adx_setup_result['status']} ({adx_setup_result.get('summary', '')})")
    except Exception as exc:  # noqa: BLE001
        log(f"[engine] Chartink ADX setup check failed ({exc!r})")

    # Option chain context (ATM +/- 5 strikes)
    option_chain_result = None
    try:
        option_chain_result = build_option_chain_data(
            kite_client, universe["ticker"], spot_price, settings=settings, log=log
        )
        if option_chain_result:
            log(
                f"[engine] Option Chain: ATM={option_chain_result['atm_strike']} "
                f"PCR={option_chain_result['pcr']:.2f} ({option_chain_result['oi_bias']}) "
                f"Ceiling={option_chain_result.get('resistance_strike')} "
                f"Floor={option_chain_result.get('support_strike')}"
            )
    except Exception as exc:  # noqa: BLE001
        log(f"[engine] Option chain build failed ({exc!r})")

    return {
        "spot_price":        spot_price,
        "day_open":          day_open,
        "points_moved":      points_moved,
        "vwap":              vwap,
        "vwap_bias":         vwap_bias,
        "orb_bias":          orb_result["orb_bias"],
        "orb_high":          orb_result["orb_high"],
        "orb_low":           orb_result["orb_low"],
        "expected_move":     expected_move,
        "avg_range":         avg_range,
        "large_gap":         gap_result["large_gap"],
        "gap_points":        gap_result["gap"],
        "momentum_bias":     momentum_result["momentum_bias"],
        "momentum_fraction": momentum_result["used_fraction"],
        "adx_volume_setup":  adx_setup_result,
        "option_chain":      option_chain_result,
    }


# ---------------------------------------------------------------------------
# Core analysis pipeline
# ---------------------------------------------------------------------------

def _run_analysis(kite_client, universe, settings, log, on_event, stop_event=None) -> dict:
    """
    Runs the 3-phase AI orchestration + technical context gathering.
    Returns the full analysis result dict.
    """
    ticker = universe["ticker"]
    reports_dir = settings.get("reports_dir", "reports")

    if stop_event and stop_event.is_set():
        log("[engine] Analysis stopped by user.")
        _emit(on_event, {"type": "stopped", "message": "Analysis stopped by user."})
        return {"direction": "NO_TRADE", "status": "STOPPED", "ticker": ticker}

    # Gather lightweight technical context first (fast)
    _emit(on_event, {"type": "progress", "stage": "technical_ctx", "message": "Gathering live technical context..."})
    tech_ctx = gather_technical_context(kite_client, universe, settings, log)
    _emit(on_event, {"type": "technical_context", **tech_ctx})
    if tech_ctx.get("option_chain"):
        _emit(on_event, {"type": "option_chain", "option_chain": tech_ctx["option_chain"]})

    if stop_event and stop_event.is_set():
        log("[engine] Analysis stopped by user.")
        _emit(on_event, {"type": "stopped", "message": "Analysis stopped by user."})
        return {"direction": "NO_TRADE", "status": "STOPPED", "ticker": ticker}

    # Run the 3-phase AI orchestration
    _emit(on_event, {"type": "progress", "stage": "discovery",
                     "message": "Phase 1: AI discovery (price, news, financials)..."})
    orchestrator = ai_orchestrator.TradeAnalysisOrchestrator(settings, log=log)
    discovery_brief = orchestrator.phase1_discovery(ticker, stop_event=stop_event)

    if stop_event and stop_event.is_set():
        log("[engine] Analysis stopped by user after Phase 1.")
        _emit(on_event, {"type": "stopped", "message": "Analysis stopped by user."})
        return {"direction": "NO_TRADE", "status": "STOPPED", "ticker": ticker}

    # Inject live technical context into discovery brief for richer agent prompts
    discovery_brief["live_vwap_bias"] = tech_ctx["vwap_bias"]
    discovery_brief["live_orb_bias"] = tech_ctx["orb_bias"]
    discovery_brief["live_momentum_bias"] = tech_ctx["momentum_bias"]
    discovery_brief["live_expected_move"] = tech_ctx["expected_move"]
    discovery_brief["live_spot_price"] = tech_ctx["spot_price"]
    discovery_brief["live_chartink_adx_setup"] = tech_ctx.get("adx_volume_setup", {})
    discovery_brief["option_chain"] = tech_ctx.get("option_chain")

    # If AI discovery didn't populate adx_volume_setup, fallback to live calculation
    if not discovery_brief.get("adx_volume_setup") or discovery_brief.get("adx_volume_setup", {}).get("status") == "UNKNOWN":
        discovery_brief["adx_volume_setup"] = tech_ctx.get("adx_volume_setup", {})

    _emit(on_event, {"type": "discovery_complete", "discovery_brief": discovery_brief})

    if stop_event and stop_event.is_set():
        log("[engine] Analysis stopped by user.")
        _emit(on_event, {"type": "stopped", "message": "Analysis stopped by user."})
        return {"direction": "NO_TRADE", "status": "STOPPED", "ticker": ticker}

    _emit(on_event, {"type": "progress", "stage": "agents",
                     "message": "Phase 2: Launching 5 parallel analysis agents..."})
    agent_results = orchestrator.phase2_launch_agents(ticker, discovery_brief, stop_event=stop_event)
    _emit(on_event, {"type": "agents_complete", "agent_results": agent_results})

    if stop_event and stop_event.is_set():
        log("[engine] Analysis stopped by user after Phase 2.")
        _emit(on_event, {"type": "stopped", "message": "Analysis stopped by user."})
        return {"direction": "NO_TRADE", "status": "STOPPED", "ticker": ticker}

    _emit(on_event, {"type": "progress", "stage": "synthesis", "message": "Phase 3: Synthesising Trade Score..."})
    trade_score_result, report_path = orchestrator.phase3_synthesize(
        ticker, discovery_brief, agent_results, output_dir=reports_dir, stop_event=stop_event
    )

    ai_sentiment = discovery_brief.get("ai_sentiment", "NEUTRAL")
    ai_reason = discovery_brief.get("ai_sentiment_reason", "")

    # Profitability / range check
    min_move = 10.0  # minimum ATR-implied move for futures to be tradeable
    range_too_tight = (
        tech_ctx["expected_move"] is not None and tech_ctx["expected_move"] < min_move
    )

    # Final direction decision
    buy_threshold = settings.get("buy_score_threshold", 70.0)
    sell_threshold = settings.get("sell_score_threshold", 30.0)
    direction = decision_mod.decide_direction(
        trade_score=trade_score_result["score"],
        ai_sentiment=ai_sentiment,
        range_too_tight=range_too_tight,
        buy_threshold=buy_threshold,
        sell_threshold=sell_threshold,
    )
    log(f"[engine] FINAL DECISION: {direction} "
        f"(score={trade_score_result['score']} grade={trade_score_result['grade']} "
        f"signal={trade_score_result['signal']} ai_sentiment={ai_sentiment})")

    result = {
        "ticker":           ticker,
        "direction":        direction,
        "trade_score":      trade_score_result,
        "ai_sentiment":     ai_sentiment,
        "ai_reason":        ai_reason,
        "report_path":      report_path,
        "discovery_brief":  discovery_brief,
        "agent_results":    agent_results,
        "technical_ctx":    tech_ctx,
        "option_chain":     tech_ctx.get("option_chain"),
        "range_too_tight":  range_too_tight,
        "buy_threshold":    buy_threshold,
        "sell_threshold":   sell_threshold,
    }

    _emit(on_event, {
        "type":            "decision",
        "ticker":          ticker,
        "direction":       direction,
        "trade_score":     trade_score_result,
        "ai_sentiment":    ai_sentiment,
        "ai_reason":       ai_reason,
        "report_path":     report_path,
        "technical_ctx":   tech_ctx,
        "option_chain":    tech_ctx.get("option_chain"),
        "range_too_tight": range_too_tight,
    })

    return result


# ---------------------------------------------------------------------------
# Position entry / exit
# ---------------------------------------------------------------------------

def enter_futures_position(kite_client, universe, direction, settings, log) -> list:
    """
    Places a BUY (LONG) or SELL (SHORT) market order for futures.
    Returns the positions list.
    """
    if direction not in ("LONG", "SHORT"):
        raise ValueError(f"invalid direction: {direction!r}")

    instrument = universe["futures_instrument"]
    lot_size = universe["lot_size"]
    qty = settings["lots"] * lot_size

    tx_type = (
        kite_client.kite.TRANSACTION_TYPE_BUY if direction == "LONG"
        else kite_client.kite.TRANSACTION_TYPE_SELL
    )
    kite_client.place_market_order(instrument["tradingsymbol"], "NFO", tx_type, qty)

    tick = kite_client.get_tick(universe["futures_token"])
    entry_price = tick["last_price"] if tick and tick.get("last_price") is not None else None
    if entry_price is None:
        q = kite_client.get_quote([f"NFO:{instrument['tradingsymbol']}"])
        entry_price = q[f"NFO:{instrument['tradingsymbol']}"]["last_price"]

    position = {
        "token":          universe["futures_token"],
        "tradingsymbol":  instrument["tradingsymbol"],
        "type":           direction,
        "qty":            qty,
        "entry_price":    entry_price,
        "ltp":            entry_price,
        "pnl":            0.0,
    }
    log(f"[engine] entered {direction} {instrument['tradingsymbol']} qty={qty} entry_price={entry_price}")
    return [position]


def compute_positions_pnl(kite_client, positions):
    for p in positions:
        tick = kite_client.get_tick(p["token"])
        ltp = tick["last_price"] if tick and tick.get("last_price") is not None else p["entry_price"]
        p["ltp"] = ltp
        # LONG: profit when price rises; SHORT: profit when price falls
        if p["type"] == "LONG":
            p["pnl"] = (ltp - p["entry_price"]) * p["qty"]
        else:
            p["pnl"] = (p["entry_price"] - ltp) * p["qty"]
    return positions


def _square_off_futures(kite_client, p, log, reason=""):
    """Square off a single futures position (SELL for LONG, BUY for SHORT)."""
    actual_qty = kite_client.get_actual_position_qty(
        p["tradingsymbol"], exchange="NFO", product="MIS", expected_qty=p["qty"]
    )
    if not actual_qty or actual_qty <= 0:
        log(f"[engine] {p['tradingsymbol']}: no remaining qty — skipping square-off [reason={reason}]")
        return False

    if p["type"] == "LONG":
        tx_type = kite_client.kite.TRANSACTION_TYPE_SELL
    else:
        tx_type = kite_client.kite.TRANSACTION_TYPE_BUY

    kite_client.place_market_order(p["tradingsymbol"], "NFO", tx_type, actual_qty)
    log(f"[engine] squared off {p['tradingsymbol']} qty={actual_qty} "
        f"entry={p['entry_price']} ltp={p.get('ltp')} pnl={p.get('pnl'):.2f} reason={reason}")
    return True


def square_off_all(kite_client, positions, log, reason=""):
    for p in positions:
        _square_off_futures(kite_client, p, log, reason=reason)
    positions.clear()


def _positions_snapshot(positions):
    return [
        {"tradingsymbol": p["tradingsymbol"], "type": p["type"], "qty": p["qty"],
         "entry_price": p["entry_price"], "ltp": p.get("ltp"), "pnl": p.get("pnl", 0.0)}
        for p in positions
    ]


# ---------------------------------------------------------------------------
# Safety monitoring loop
# ---------------------------------------------------------------------------

_EXIT_SETTING_KEYS = ("sl_enabled", "max_loss", "target_enabled", "target_profit",
                      "time_exit_enabled", "time_exit", "pnl_mode", "force_exit_time")

STALE_TICK_WARNING_SECONDS = 20
STALE_TICK_RE_WARN_SECONDS = 30


def _read_live_exit_settings(settings, settings_path, log):
    try:
        with open(settings_path, "r") as f:
            fresh = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log(f"[engine] could not re-read {settings_path} ({exc!r}), using last-known values")
        return {k: settings[k] for k in _EXIT_SETTING_KEYS if k in settings}
    return {k: fresh[k] for k in _EXIT_SETTING_KEYS if k in fresh}


def monitor_and_exit(kite_client, positions, settings, stop_event, log,
                     on_event=None, settings_path="settings.json"):
    live = dict(settings)
    last_stale_warning_at = 0.0
    last_logged_exit_settings = None

    while positions:
        if stop_event.is_set():
            log("[engine] STOP signal received — squaring off immediately")
            snapshot = _positions_snapshot(positions)
            square_off_all(kite_client, positions, log, reason="MANUAL_STOP")
            _emit(on_event, {"type": "exit", "reason": "MANUAL_STOP", "positions": snapshot})
            break

        live.update(_read_live_exit_settings(settings, settings_path, log))
        exit_settings_now = {k: live.get(k) for k in _EXIT_SETTING_KEYS}
        if last_logged_exit_settings is not None and exit_settings_now != last_logged_exit_settings:
            log(f"[engine] SL/Target settings changed mid-trade: "
                f"sl_enabled={live.get('sl_enabled')} max_loss={live.get('max_loss')} "
                f"target_enabled={live.get('target_enabled')} target_profit={live.get('target_profit')}")
        last_logged_exit_settings = exit_settings_now

        force_exit_t = _parse_hhmm(live["force_exit_time"])
        time_exit_t = _parse_hhmm(live["time_exit"]) if live.get("time_exit_enabled") else None
        pnl_mode = live.get("pnl_mode", "COMBINED")

        # Stale tick watchdog
        stale_seconds = kite_client.seconds_since_last_tick()
        if stale_seconds is not None and stale_seconds > STALE_TICK_WARNING_SECONDS:
            now_wall = time.time()
            if now_wall - last_stale_warning_at > STALE_TICK_RE_WARN_SECONDS:
                log(f"[engine] WARNING: no live tick for {stale_seconds:.0f}s — prices may be stale")
                _emit(on_event, {"type": "tick_stale", "seconds_since_last_tick": stale_seconds})
                last_stale_warning_at = now_wall

        compute_positions_pnl(kite_client, positions)
        _emit(on_event, {"type": "pnl_update", "positions": _positions_snapshot(positions),
                         "total_pnl": sum(p["pnl"] for p in positions)})
        now_t = datetime.datetime.now().time()

        # Rule: day-end safety exit — ALWAYS active
        if now_t >= force_exit_t:
            log(f"[engine] force_exit_time {live['force_exit_time']} reached — squaring off (safety net)")
            snapshot = _positions_snapshot(positions)
            square_off_all(kite_client, positions, log, reason="FORCE_EXIT_TIME")
            _emit(on_event, {"type": "exit", "reason": "FORCE_EXIT_TIME", "positions": snapshot})
            break

        if time_exit_t is not None and now_t >= time_exit_t:
            log(f"[engine] time_exit {live['time_exit']} reached — squaring off")
            snapshot = _positions_snapshot(positions)
            square_off_all(kite_client, positions, log, reason="TIME_EXIT")
            _emit(on_event, {"type": "exit", "reason": "TIME_EXIT", "positions": snapshot})
            break

        # SL / Target (combined or per-leg)
        if pnl_mode == "COMBINED":
            total_pnl = sum(p["pnl"] for p in positions)
            if live.get("sl_enabled") and total_pnl <= -abs(live["max_loss"]):
                log(f"[engine] combined SL hit: pnl={total_pnl:.2f}")
                snapshot = _positions_snapshot(positions)
                square_off_all(kite_client, positions, log, reason="STOP_LOSS")
                _emit(on_event, {"type": "exit", "reason": "STOP_LOSS", "positions": snapshot})
                break
            if live.get("target_enabled") and total_pnl >= abs(live["target_profit"]):
                log(f"[engine] combined target hit: pnl={total_pnl:.2f}")
                snapshot = _positions_snapshot(positions)
                square_off_all(kite_client, positions, log, reason="TARGET")
                _emit(on_event, {"type": "exit", "reason": "TARGET", "positions": snapshot})
                break
        else:
            for p in list(positions):
                if live.get("sl_enabled") and p["pnl"] <= -abs(live["max_loss"]):
                    log(f"[engine] leg SL hit: {p['tradingsymbol']} pnl={p['pnl']:.2f}")
                    _square_off_futures(kite_client, p, log, reason="STOP_LOSS")
                    _emit(on_event, {"type": "leg_exit", "reason": "STOP_LOSS",
                                     "tradingsymbol": p["tradingsymbol"], "pnl": p["pnl"]})
                    positions.remove(p)
                elif live.get("target_enabled") and p["pnl"] >= abs(live["target_profit"]):
                    log(f"[engine] leg target hit: {p['tradingsymbol']} pnl={p['pnl']:.2f}")
                    _square_off_futures(kite_client, p, log, reason="TARGET")
                    _emit(on_event, {"type": "leg_exit", "reason": "TARGET",
                                     "tradingsymbol": p["tradingsymbol"], "pnl": p["pnl"]})
                    positions.remove(p)

        stop_event.wait(timeout=1)


def _safety_net_exit(kite_client, positions, log, on_event):
    if positions:
        log("[engine] unexpected exit path — squaring off remaining positions as safety net")
        snapshot = _positions_snapshot(positions)
        square_off_all(kite_client, positions, log, reason="SAFETY_NET_EXIT")
        _emit(on_event, {"type": "exit", "reason": "SAFETY_NET_EXIT", "positions": snapshot})
    kite_client.stop_ticker()


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def analyze_only(settings_path="settings.json", log=print, kite_client=None,
                 on_event=None, on_tick=None, ticker_override=None, settings_dict=None,
                 stop_event=None) -> dict:
    """
    Runs the full 3-phase analysis (Discovery → 5 Agents → Trade Score)
    and writes the report, but NEVER places any order.
    'Analyze first, you decide.'
    """
    if stop_event and stop_event.is_set():
        log("[engine] Analysis stopped before start.")
        _emit(on_event, {"type": "stopped", "message": "Analysis stopped by user."})
        return {"direction": "NO_TRADE", "status": "STOPPED", "ticker": ticker_override or "RELIANCE"}

    if settings_dict:
        settings = dict(settings_dict)
    else:
        settings = load_settings(settings_path)
    if ticker_override:
        settings["ticker"] = ticker_override.upper()

    if kite_client is None:
        kite_client = KiteClient(settings, log=log)
        kite_client.login()
    else:
        kite_client.settings = settings

    try:
        if stop_event and stop_event.is_set():
            log("[engine] analysis aborted before start")
            _emit(on_event, {"type": "stopped", "message": "Analysis cancelled by user."})
            return {"direction": "NO_TRADE", "status": "STOPPED"}
        universe = _setup_universe_and_ticker(kite_client, settings, log, on_event, on_tick)
        if stop_event and stop_event.is_set():
            log("[engine] analysis stopped after universe setup")
            _emit(on_event, {"type": "stopped", "message": "Analysis cancelled by user."})
            return {"direction": "NO_TRADE", "status": "STOPPED", "ticker": universe.get("ticker")}
        result = _run_analysis(kite_client, universe, settings, log, on_event, stop_event=stop_event)
        if stop_event and stop_event.is_set():
            log("[engine] analysis stopped by user")
            _emit(on_event, {"type": "stopped", "message": "Analysis stopped by user."})
            return {"direction": "NO_TRADE", "status": "STOPPED", "ticker": universe.get("ticker")}
        log(f"[engine] ANALYZE ONLY: direction={result.get('direction')} — no order placed")
        _emit(on_event, {"type": "analysis_complete", "result": result})
        return result
    finally:
        kite_client.stop_ticker()


def run(stop_event=None, settings_path="settings.json", log=print,
        kite_client=None, on_event=None, on_tick=None, ticker_override=None, settings_dict=None) -> dict:
    """
    Full execute: analyze → enter futures position (if not NO_TRADE) → monitor.
    kite_client: pass an already-logged-in KiteClient from the web UI login.
    """
    if settings_dict:
        settings = dict(settings_dict)
    else:
        settings = load_settings(settings_path)
    if ticker_override:
        settings["ticker"] = ticker_override.upper()
    stop_event = stop_event or threading.Event()

    if kite_client is None:
        kite_client = KiteClient(settings, log=log)
        kite_client.login()
    else:
        kite_client.settings = settings

    positions = []
    try:
        universe = _setup_universe_and_ticker(kite_client, settings, log, on_event, on_tick)
        if stop_event and stop_event.is_set():
            log("[engine] execution stopped by user before analysis")
            _emit(on_event, {"type": "stopped", "message": "Execution cancelled by user."})
            return {"direction": "NO_TRADE", "status": "STOPPED", "ticker": universe.get("ticker")}
        result = _run_analysis(kite_client, universe, settings, log, on_event, stop_event=stop_event)
        if stop_event and stop_event.is_set():
            log("[engine] execution stopped by user after analysis")
            _emit(on_event, {"type": "stopped", "message": "Execution cancelled by user."})
            return {"direction": "NO_TRADE", "status": "STOPPED", "ticker": universe.get("ticker")}
        direction = result["direction"]

        if direction == "NO_TRADE":
            log("[engine] NO_TRADE — no order placed, run ending.")
            _emit(on_event, {"type": "no_trade", "result": result})
            return result

        positions = enter_futures_position(kite_client, universe, direction, settings, log)
        _emit(on_event, {"type": "positions_entered", "positions": _positions_snapshot(positions)})

        monitor_and_exit(kite_client, positions, settings, stop_event, log,
                         on_event=on_event, settings_path=settings_path)

        result["positions"] = positions
        return result

    finally:
        _safety_net_exit(kite_client, positions, log, on_event)


VALID_MANUAL_DIRECTIONS = ("LONG", "SHORT")


def manual_run(direction, stop_event=None, settings_path="settings.json", log=print,
               kite_client=None, on_event=None, on_tick=None) -> dict:
    """
    Manual override: enter LONG or SHORT directly without running the AI analysis.
    The full SL/target/time-exit monitoring loop still applies.
    """
    if direction not in VALID_MANUAL_DIRECTIONS:
        raise ValueError(f"invalid direction: {direction!r}")

    settings = load_settings(settings_path)
    stop_event = stop_event or threading.Event()

    if kite_client is None:
        kite_client = KiteClient(settings, log=log)
        kite_client.login()
    else:
        kite_client.settings = settings

    positions = []
    try:
        universe = _setup_universe_and_ticker(kite_client, settings, log, on_event, on_tick)
        log(f"[engine] MANUAL OVERRIDE: entering {direction} directly — no analysis performed")
        _emit(on_event, {"type": "decision", "direction": direction, "manual": True})

        positions = enter_futures_position(kite_client, universe, direction, settings, log)
        _emit(on_event, {"type": "positions_entered", "positions": _positions_snapshot(positions)})

        monitor_and_exit(kite_client, positions, settings, stop_event, log,
                         on_event=on_event, settings_path=settings_path)

        return {"direction": direction, "positions": positions}

    finally:
        _safety_net_exit(kite_client, positions, log, on_event)
