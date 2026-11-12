"""
Background scanner engine that:
  1. Periodically fetches 5m, 15m, daily candles from Kite for a list of symbols.
  2. Evaluates Strategy A (Bullish) and Strategy B (Bearish) for each symbol.
  3. Resolves the ATM option contract for signals.
  4. Emits detected signals via a callback.
  5. Deduplicates signals: one alert per (strategy, symbol, candle timestamp).

DISCLAIMER: For educational and research purposes only. Not financial advice.
"""

import datetime
import threading
import time

import scanner_strategy
import option_signal
import futures_engine


# ---------------------------------------------------------------------------
# ATM Option resolver
# ---------------------------------------------------------------------------

def resolve_atm_option(kite_client, symbol, option_type, spot_price=None, log=print):
    """
    Given a symbol (stock or index) and option_type ('CE' or 'PE'),
    finds the nearest expiry ATM option contract from Kite NFO.

    Returns dict {symbol, strike, option_type, expiry, ltp, lot_size, spot_price}
    or None on failure.
    """
    try:
        deriv_name = futures_engine.resolve_derivative_name(symbol)
        spot_sym   = futures_engine.resolve_spot_tradingsymbol(symbol)

        # Spot price: use passed value or fetch quote
        if spot_price is None or spot_price <= 0:
            q = kite_client.get_quote([f"NSE:{spot_sym}"])
            spot_price = q[f"NSE:{spot_sym}"]["last_price"]

        # Option chain for nearest expiry
        nearest_expiry, chain = kite_client.get_nfo_option_chain(deriv_name)
        filtered = [i for i in chain if i.get("instrument_type") == option_type]
        if not filtered:
            return None

        # Find listed strike closest to spot_price
        atm_instr = min(filtered, key=lambda i: abs(float(i.get("strike", 0)) - spot_price))
        atm_strike = float(atm_instr.get("strike", 0))

        # Get LTP
        ts = atm_instr["tradingsymbol"]
        try:
            q2 = kite_client.get_quote([f"NFO:{ts}"])
            ltp = q2[f"NFO:{ts}"]["last_price"]
        except Exception:
            ltp = None

        # Resolve futures tradingsymbol as well
        fut_symbol = None
        if deriv_name in _FUTURES_META_CACHE:
            fut_entry = _FUTURES_META_CACHE[deriv_name]
            fut_symbol = fut_entry.get("tradingsymbol") if isinstance(fut_entry, dict) else getattr(fut_entry, "get", lambda k: None)("tradingsymbol")
        if not fut_symbol:
            try:
                fut_instr = kite_client.get_nfo_futures_instrument(deriv_name)
                if fut_instr:
                    fut_symbol = fut_instr.get("tradingsymbol")
                    _FUTURES_META_CACHE[deriv_name] = {"tradingsymbol": fut_symbol, "expiry": str(fut_instr.get("expiry"))}
            except Exception:
                pass

        return {
            "symbol":         ts,
            "strike":         atm_strike,
            "option_type":    option_type,
            "expiry":         str(nearest_expiry),
            "ltp":            ltp,
            "lot_size":       atm_instr.get("lot_size"),
            "spot_price":     spot_price,
            "futures_symbol": fut_symbol,
        }
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        log(f"[scanner] ATM option resolution failed for {symbol} {option_type}: {exc!r}")
        return None


# ---------------------------------------------------------------------------
# Data fetcher per symbol (with warm-up and in-memory caches)
# ---------------------------------------------------------------------------

_DAILY_CACHE = {}        # (spot_token, date_str) -> daily_candles
_15M_CACHE = {}          # (spot_token, slot_str) -> candles_15m
_FUTURES_META_CACHE = {} # deriv_name -> fut_instr
_SPOT_INSTR_CACHE = {}   # symbol -> spot_instr
_INVALID_SYMBOLS = set() # symbols without valid spot instrument (e.g. NIFTYFPI)


def fetch_symbol_data(
    kite_client,
    symbol,
    spot_quote=None,
    futures_quote=None,
    fno_meta=None,
    need_5m=True,
    need_15m=True,
    need_daily=True,
    log=print,
):
    """
    Fetches required candles and quote data for one symbol.
    Only fetches candle intervals requested by active strategies.
    Uses batch quotes and in-memory caching to strictly respect Kite rate limits.
    Returns dict or None on failure.
    """
    try:
        if symbol in _INVALID_SYMBOLS:
            return None

        if symbol not in _SPOT_INSTR_CACHE:
            try:
                _SPOT_INSTR_CACHE[symbol] = futures_engine.get_spot_instrument(kite_client, symbol)
            except Exception as e:
                _INVALID_SYMBOLS.add(symbol)
                log(f"[scanner] Skipping {symbol}: {e}")
                return None

        spot_instr = _SPOT_INSTR_CACHE[symbol]
        spot_token = spot_instr["instrument_token"]
        spot_sym   = spot_instr["tradingsymbol"]

        # Spot quote
        if spot_quote:
            quote = spot_quote
        else:
            q = kite_client.get_quote([f"NSE:{spot_sym}"])
            quote = q.get(f"NSE:{spot_sym}", {})
        spot_price = quote.get("last_price")
        today_open = quote.get("ohlc", {}).get("open")

        today = datetime.date.today()
        now = datetime.datetime.now()

        # 5m candles with historical warm-up (only if needed by active strategies)
        candles_5m = []
        if need_5m:
            time.sleep(0.15)
            start_5m = now - datetime.timedelta(days=5)
            candles_5m = kite_client.get_intraday_history(spot_token, start_5m, now, interval="5minute") or []
            if not candles_5m:
                return None

        # 15m candles with historical warm-up (only if needed by active strategies)
        candles_15m = []
        if need_15m:
            slot_str = now.strftime("%Y%m%d_%H") + f"_{now.minute // 15}"
            cache_key_15m = (spot_token, slot_str)
            if cache_key_15m in _15M_CACHE:
                candles_15m = _15M_CACHE[cache_key_15m]
            else:
                time.sleep(0.25)
                start_15m = now - datetime.timedelta(days=5)
                try:
                    candles_15m = kite_client.get_intraday_history(spot_token, start_15m, now, interval="15minute") or []
                except Exception as exc:
                    if "Too many requests" in str(exc):
                        time.sleep(2.0)
                        candles_15m = kite_client.get_intraday_history(spot_token, start_15m, now, interval="15minute") or []
                    else:
                        raise exc
                if candles_15m:
                    _15M_CACHE[cache_key_15m] = candles_15m

        # Daily candles (cached per day for Supertrend and Strategy D/E EMA/SMA warmup)
        daily_candles = []
        if need_daily:
            cache_key = (spot_token, str(today))
            if cache_key in _DAILY_CACHE:
                daily_candles = _DAILY_CACHE[cache_key]
            else:
                # Rate limit safety: throttle calls when fetching fresh history (<= 3 req/sec)
                time.sleep(0.35)
                from_date = today - datetime.timedelta(days=365)
                try:
                    daily_candles = kite_client.get_daily_history(spot_token, from_date, today) or []
                except Exception as exc:
                    err_str = str(exc)
                    if "Too many requests" in err_str:
                        log(f"[scanner] Rate limit hit on {symbol}, retrying after 2s...")
                        time.sleep(2.0)
                        daily_candles = kite_client.get_daily_history(spot_token, from_date, today) or []
                    else:
                        raise exc
                if daily_candles:
                    _DAILY_CACHE[cache_key] = daily_candles
            if not daily_candles:
                return None

        # Futures info (from fno_meta if available, or cached per deriv_name)
        deriv_name = futures_engine.resolve_derivative_name(symbol)
        futures_symbol = None
        expiry = None
        if fno_meta and fno_meta.get("tradingsymbol"):
            futures_symbol = fno_meta["tradingsymbol"]
            expiry = fno_meta.get("expiry")
            _FUTURES_META_CACHE[deriv_name] = {"tradingsymbol": futures_symbol, "expiry": expiry}
        elif deriv_name in _FUTURES_META_CACHE:
            fut_entry = _FUTURES_META_CACHE[deriv_name]
            futures_symbol = fut_entry.get("tradingsymbol") if isinstance(fut_entry, dict) else getattr(fut_entry, "get", lambda k: None)("tradingsymbol")
            expiry = fut_entry.get("expiry") if isinstance(fut_entry, dict) else getattr(fut_entry, "get", lambda k: None)("expiry")
        else:
            try:
                fut_instr = kite_client.get_nfo_futures_instrument(deriv_name)
                if fut_instr:
                    futures_symbol = fut_instr["tradingsymbol"]
                    expiry = fut_instr["expiry"]
                    _FUTURES_META_CACHE[deriv_name] = {"tradingsymbol": futures_symbol, "expiry": expiry}
            except Exception:
                futures_symbol = None
                expiry = None

        # Futures LTP (prefer batch quote, avoiding 180+ separate HTTP calls)
        futures_price = None
        current_oi = None
        if futures_quote:
            futures_price = futures_quote.get("last_price")
            current_oi = futures_quote.get("oi")
        elif futures_symbol:
            try:
                fut_q = kite_client.get_quote([f"NFO:{futures_symbol}"])
                q_item = fut_q.get(f"NFO:{futures_symbol}", {})
                futures_price = q_item.get("last_price")
                current_oi = q_item.get("oi")
            except Exception:
                futures_price = None

        # Extract last 5m and 15m candle timestamps (guarding against midnight 00:00:00)
        now = datetime.datetime.now()
        last_5m_ts = candles_5m[-1].get("date") if candles_5m else now
        if hasattr(last_5m_ts, "hour") and last_5m_ts.hour == 0 and last_5m_ts.minute == 0 and last_5m_ts.second == 0:
            last_5m_ts = now
        last_15m_ts = candles_15m[-1].get("date") if candles_15m else now
        if hasattr(last_15m_ts, "hour") and last_15m_ts.hour == 0 and last_15m_ts.minute == 0 and last_15m_ts.second == 0:
            last_15m_ts = now

        return {
            "symbol":          symbol,
            "spot_price":      spot_price,
            "today_open":      today_open,
            "futures_symbol":  futures_symbol,
            "futures_price":   futures_price,
            "current_oi":      current_oi,
            "expiry":          expiry,
            "candles_5m":      candles_5m,
            "candles_15m":     candles_15m,
            "daily_candles":   daily_candles,
            "last_5m_ts":      last_5m_ts,
            "last_15m_ts":     last_15m_ts,
        }
    except Exception as exc:  # noqa: BLE001
        log(f"[scanner] fetch_symbol_data failed for {symbol}: {exc!r}")
        return None


# ---------------------------------------------------------------------------
# Scanner loop
# ---------------------------------------------------------------------------

class ScannerEngine:
    """
    Background scanner that evaluates Strategy A and B for a list of symbols.
    """

    def __init__(self, kite_client, settings, on_signal, on_signal_reversed=None, log=print):
        self.kite_client = kite_client
        self.settings    = settings
        self.on_signal   = on_signal   # callback(signal_dict)
        self.on_signal_reversed = on_signal_reversed  # callback(dict)
        self.log         = log
        self._stop_event = threading.Event()
        self._thread     = None
        self._seen_keys  = set()       # deduplication
        self._signals    = []          # full history this session
        self._symbol_ema_states = {}   # symbol -> "BULLISH"|"BEARISH"|"NEUTRAL" for Strategy D state transitions
        self._smart_money_data = {
            "bottom_accumulation": [],
            "top_distribution": [],
            "confirmed": [],
            "watchlist": [],
        }
        self._sustained_breakout_data = {
            "market_trend": {},
            "derivatives": {},
            "trading_signal": {},
            "records": [],
        }
        self._lock       = threading.Lock()
        self._current_symbol_count = 0  # updated each scan cycle

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.log("[scanner] Scanner engine started")

    def stop(self):
        self._stop_event.set()
        self.log("[scanner] Scanner engine stop requested")

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def get_signals(self):
        with self._lock:
            return list(self._signals)

    def clear_signals(self):
        with self._lock:
            self._signals.clear()
            self._seen_keys.clear()

    def get_smart_money_dashboard(self):
        with self._lock:
            return {
                "bottom_accumulation": list(self._smart_money_data["bottom_accumulation"]),
                "top_distribution": list(self._smart_money_data["top_distribution"]),
                "confirmed": list(self._smart_money_data["confirmed"]),
                "watchlist": list(self._smart_money_data["watchlist"]),
            }

    def get_sustained_breakout_dashboard(self):
        with self._lock:
            return {
                "market_trend": dict(self._sustained_breakout_data.get("market_trend", {})),
                "derivatives": dict(self._sustained_breakout_data.get("derivatives", {})),
                "trading_signal": dict(self._sustained_breakout_data.get("trading_signal", {})),
                "records": list(self._sustained_breakout_data.get("records", [])),
            }

    def get_or_eval_sustained_breakout(self, max_age_seconds=4):
        now = datetime.datetime.now()
        is_stale = True
        with self._lock:
            ts_str = self._sustained_breakout_data.get("trading_signal", {}).get("timestamp")
            if ts_str:
                try:
                    dt = datetime.datetime.fromisoformat(ts_str)
                    if (now - dt).total_seconds() < max_age_seconds:
                        is_stale = False
                except Exception:
                    pass
        if is_stale:
            try:
                self._eval_strategy_f()
            except Exception as exc:
                self.log(f"[scanner] eval_strategy_f on-demand note: {exc!r}")
        return self.get_sustained_breakout_dashboard()

    def get_symbol_count(self):
        return self._current_symbol_count

    def _loop(self):
        while not self._stop_event.is_set():
            self._scan_all()
            interval = int(self.settings.get("scanner_interval_sec", 60))
            for _ in range(interval):
                if self._stop_event.is_set():
                    break
                time.sleep(1)
        self.log("[scanner] Scanner engine stopped")

    def _scan_all(self):
        raw_symbols = self.settings.get("scanner_symbols", [])
        enabled_a = self.settings.get("scanner_enabled_a", False)
        enabled_b = self.settings.get("scanner_enabled_b", False)
        enabled_c = self.settings.get("scanner_enabled_c", False)
        enabled_d = self.settings.get("scanner_enabled_d", False)
        enabled_e = self.settings.get("scanner_enabled_e", True)
        enabled_f = self.settings.get("scanner_enabled_f", True)
        max_lot_size = self.settings.get("scanner_max_lot_size", 2500)

        # Evaluate Strategy F immediately at the very beginning so NIFTY is never delayed by 186 symbols
        if enabled_f:
            try:
                self._eval_strategy_f()
            except Exception as f_exc:
                self.log(f"[scanner] Immediate Strategy F evaluation note: {f_exc!r}")

        # Collections for Smart Money Dashboard (Section 10)
        sm_acc = []
        sm_dist = []
        sm_conf = []
        sm_watch = []

        # --- Resolve full FNO universe when symbols list is empty or contains "ALL_FNO" ---
        fno_meta_map = {}
        try:
            fno_list = futures_engine.get_active_futures_symbols(self.kite_client, max_lot_size=max_lot_size)
            fno_meta_map = {s["symbol"]: s for s in fno_list}
        except Exception as exc:
            self.log(f"[scanner] Failed to load FNO universe: {exc!r}")

        if not raw_symbols or raw_symbols == ["ALL_FNO"]:
            if not fno_meta_map:
                self.log("[scanner] Warning: FNO universe returned empty list — retrying next cycle")
                return
            symbols = [s["symbol"] for s in fno_list]
            self.log(f"[scanner] Full FNO universe loaded (lot_size < {max_lot_size}): {len(symbols)} symbols")
        else:
            # Strictly filter: if the stock is not listed in futures then remove from the list
            symbols = [s for s in raw_symbols if not fno_meta_map or s in fno_meta_map]
            if len(symbols) < len(raw_symbols):
                excluded = [s for s in raw_symbols if s not in symbols]
                self.log(f"[scanner] Excluded non-futures stocks from scan: {', '.join(excluded)}")

        # Exclude any known invalid symbols (e.g. indices with no spot stock)
        symbols = [s for s in symbols if s not in _INVALID_SYMBOLS]
        self._current_symbol_count = len(symbols)

        # Batch-fetch spot AND futures quotes in chunks of 200 to strictly minimize HTTP calls
        quotes_map = {}
        sym_key_map = {}
        fut_key_map = {}
        batch_keys = []
        valid_symbols = []

        for sym in symbols:
            try:
                if sym not in _SPOT_INSTR_CACHE:
                    _SPOT_INSTR_CACHE[sym] = futures_engine.get_spot_instrument(self.kite_client, sym)
                ts = _SPOT_INSTR_CACHE[sym]["tradingsymbol"]
                k_spot = f"NSE:{ts}"
                batch_keys.append(k_spot)
                sym_key_map[sym] = k_spot
                valid_symbols.append(sym)

                fno_meta = fno_meta_map.get(sym)
                if fno_meta and fno_meta.get("tradingsymbol"):
                    k_fut = f"NFO:{fno_meta['tradingsymbol']}"
                    batch_keys.append(k_fut)
                    fut_key_map[sym] = k_fut
            except Exception:
                _INVALID_SYMBOLS.add(sym)

        symbols = valid_symbols
        self.log(f"[scanner] Scanning {len(symbols)} symbol(s)...")

        try:
            for i in range(0, len(batch_keys), 200):
                chunk = batch_keys[i:i+200]
                q = self.kite_client.get_quote(chunk)
                quotes_map.update(q)
                time.sleep(0.2)
        except Exception as exc:
            self.log(f"[scanner] Batch quote note: {exc!r}")

        # Active strategy candle requirements (15m execution chart confirms Strategy C & E; 5m for A, B & F)
        need_5m = enabled_a or enabled_b or enabled_f
        need_15m = enabled_b or enabled_c or enabled_e
        need_daily = enabled_c or enabled_d or enabled_e

        consecutive_auth_errors = 0
        for sym in symbols:
            if self._stop_event.is_set():
                break
            try:
                k_spot = sym_key_map.get(sym)
                spot_quote = quotes_map.get(k_spot) if k_spot else None
                k_fut = fut_key_map.get(sym)
                futures_quote = quotes_map.get(k_fut) if k_fut else None
                fno_meta = fno_meta_map.get(sym)

                # Enforce lot size filter if meta is present
                if fno_meta and max_lot_size and fno_meta.get("lot_size", 0) >= max_lot_size:
                    continue

                data = fetch_symbol_data(
                    self.kite_client,
                    sym,
                    spot_quote=spot_quote,
                    futures_quote=futures_quote,
                    fno_meta=fno_meta,
                    need_5m=need_5m,
                    need_15m=need_15m,
                    need_daily=need_daily,
                    log=self.log,
                )
                if data is None:
                    continue

                consecutive_auth_errors = 0

                # Build prev_day_hlc from daily candles
                prev_day_hlc = None
                if len(data["daily_candles"]) >= 2:
                    pd = data["daily_candles"][-2]
                    prev_day_hlc = {
                        "high":  pd.get("high"),
                        "low":   pd.get("low"),
                        "close": pd.get("close"),
                    }

                if enabled_a:
                    self._eval_strategy_a(data, prev_day_hlc)
                if enabled_b:
                    self._eval_strategy_b(data, prev_day_hlc)
                if enabled_c:
                    self._eval_strategy_c(data)
                if enabled_d:
                    self._eval_strategy_d(data)
                if enabled_e:
                    self._eval_strategy_e(data, sm_acc, sm_dist, sm_conf, sm_watch)

                # Delay between symbols
                time.sleep(0.08)

            except Exception as exc:  # noqa: BLE001
                exc_str = str(exc)
                exc_cls = type(exc).__name__
                if "TokenException" in exc_cls or "Incorrect `api_key`" in exc_str:
                    consecutive_auth_errors += 1
                    if consecutive_auth_errors >= 5:
                        # Verify whether token is truly expired
                        try:
                            self.kite_client.kite.profile()
                            self.log("[scanner] Rate-limiting throttle detected from Kite gateway — pausing 5s...")
                            time.sleep(5.0)
                            consecutive_auth_errors = 0
                        except Exception:
                            self.log("[scanner] ✗ Kite session expired. Please re-login to Kite from the dashboard.")
                            self.stop()
                            break
                elif "Too many requests" in exc_str or "NetworkException" in exc_cls:
                    self.log(f"[scanner] Rate limit hit on {sym}, pausing 2s...")
                    time.sleep(2.0)
                else:
                    self.log(f"[scanner] Error scanning {sym}: {exc!r}")

        # Update smart money dashboard state
        if enabled_e:
            with self._lock:
                self._smart_money_data = {
                    "bottom_accumulation": sm_acc,
                    "top_distribution": sm_dist,
                    "confirmed": sm_conf,
                    "watchlist": sm_watch,
                }

        # Evaluate Strategy F (NIFTY Sustained Breakout + F&O OI Confirmation)
        if enabled_f:
            self._eval_strategy_f()

    def _eval_strategy_a(self, data, prev_day_hlc):
        sym = data["symbol"]
        result = scanner_strategy.evaluate_strategy_a(
            candles_5min=data["candles_5m"],
            prev_day_hlc=prev_day_hlc,
            current_day_open=data["today_open"],
            symbol=sym,
            futures_symbol=data["futures_symbol"],
            expiry=data["expiry"],
            candle_ts=data["last_5m_ts"],
            spot_price=data["spot_price"],
            futures_price=data["futures_price"],
        )
        if result["passed"] and result["signal"]:
            sig = result["signal"]
            key = scanner_strategy.make_dedup_key("A", sym, data["last_5m_ts"])
            if key not in self._seen_keys:
                if not sig.get("futures_symbol") and data.get("futures_symbol"):
                    sig["futures_symbol"] = data["futures_symbol"]
                if not sig.get("expiry") and data.get("expiry"):
                    sig["expiry"] = str(data["expiry"])
                if sig.get("futures_price") is None and data.get("futures_price"):
                    sig["futures_price"] = data["futures_price"]

                # Resolve ATM CE
                atm = resolve_atm_option(self.kite_client, sym, "CE", spot_price=data.get("spot_price"), log=self.log)
                if atm:
                    sig["atm_option"] = atm
                    if not sig.get("expiry") and atm.get("expiry"):
                        sig["expiry"] = atm["expiry"]
                    if not sig.get("futures_symbol") and atm.get("futures_symbol"):
                        sig["futures_symbol"] = atm["futures_symbol"]
                self._emit_signal(sig, key)

    def _eval_strategy_b(self, data, prev_day_hlc):
        sym = data["symbol"]
        result = scanner_strategy.evaluate_strategy_b(
            candles_5min=data["candles_5m"],
            candles_15min=data["candles_15m"],
            daily_candles=data["daily_candles"],
            symbol=sym,
            futures_symbol=data["futures_symbol"],
            expiry=data["expiry"],
            candle_ts=data["last_5m_ts"],
            spot_price=data["spot_price"],
            futures_price=data["futures_price"],
            min_daily_volume=self.settings.get("scanner_min_volume_daily", 500000),
            min_daily_close=self.settings.get("scanner_min_close_daily", 90.0),
            max_daily_close=self.settings.get("scanner_max_close_daily", 700.0),
            supertrend_period=self.settings.get("scanner_supertrend_period", 7),
            supertrend_multiplier=self.settings.get("scanner_supertrend_multiplier", 3.0),
        )
        if result["passed"] and result["signal"]:
            sig = result["signal"]
            key = scanner_strategy.make_dedup_key("B", sym, data["last_5m_ts"])
            if key not in self._seen_keys:
                if not sig.get("futures_symbol") and data.get("futures_symbol"):
                    sig["futures_symbol"] = data["futures_symbol"]
                if not sig.get("expiry") and data.get("expiry"):
                    sig["expiry"] = str(data["expiry"])
                if sig.get("futures_price") is None and data.get("futures_price"):
                    sig["futures_price"] = data["futures_price"]

                # Resolve ATM PE
                atm = resolve_atm_option(self.kite_client, sym, "PE", spot_price=data.get("spot_price"), log=self.log)
                if atm:
                    sig["atm_option"] = atm
                    if not sig.get("expiry") and atm.get("expiry"):
                        sig["expiry"] = atm["expiry"]
                    if not sig.get("futures_symbol") and atm.get("futures_symbol"):
                        sig["futures_symbol"] = atm["futures_symbol"]
                self._emit_signal(sig, key)

    def _eval_strategy_c(self, data):
        sym = data["symbol"]
        candles_c = data.get("candles_15m") or data.get("candles_5m")
        result = scanner_strategy.evaluate_strategy_c(
            candles_5min=candles_c,
            symbol=sym,
            futures_symbol=data["futures_symbol"],
            expiry=data["expiry"],
            candle_ts=data["last_5m_ts"],
            spot_price=data["spot_price"],
            futures_price=data["futures_price"],
            ema_fast=self.settings.get("scanner_c_ema_fast", 20),
            ema_mid=self.settings.get("scanner_c_ema_mid", 50),
            ema_slow=self.settings.get("scanner_c_ema_slow", 100),
            supertrend_period=self.settings.get("scanner_supertrend_period", 7),
            supertrend_multiplier=self.settings.get("scanner_supertrend_multiplier", 3.0),
            min_sustain_candles=self.settings.get("scanner_c_min_sustain", 1),
            max_lookback=self.settings.get("scanner_c_max_lookback", 25),
            confirm_level=self.settings.get("scanner_c_confirm_level", "high"),
        )
        if result["passed"] and result["signal"]:
            sig = result["signal"]
            direction = result["direction"]
            key = f"C_{direction}_{sym}_{data['last_5m_ts']}"
            if key not in self._seen_keys:
                if not sig.get("futures_symbol") and data.get("futures_symbol"):
                    sig["futures_symbol"] = data["futures_symbol"]
                if not sig.get("expiry") and data.get("expiry"):
                    sig["expiry"] = str(data["expiry"])
                if sig.get("futures_price") is None and data.get("futures_price"):
                    sig["futures_price"] = data["futures_price"]

                opt_type = "CE" if direction == "BULLISH" else "PE"
                atm = resolve_atm_option(self.kite_client, sym, opt_type, spot_price=data.get("spot_price"), log=self.log)
                if atm:
                    sig["atm_option"] = atm
                    if not sig.get("expiry") and atm.get("expiry"):
                        sig["expiry"] = atm["expiry"]
                    if not sig.get("futures_symbol") and atm.get("futures_symbol"):
                        sig["futures_symbol"] = atm["futures_symbol"]
                self._emit_signal(sig, key)

    def _eval_strategy_d(self, data):
        sym = data["symbol"]
        daily_candles = data.get("daily_candles") or []
        prev_state = self._symbol_ema_states.get(sym, "NEUTRAL")

        result = scanner_strategy.evaluate_strategy_d(
            candles=daily_candles,
            symbol=sym,
            futures_symbol=data.get("futures_symbol"),
            expiry=data.get("expiry"),
            candle_ts=data.get("last_5m_ts"),
            spot_price=data.get("spot_price"),
            futures_price=data.get("futures_price"),
            ema_fast=self.settings.get("scanner_d_ema_fast", 50),
            ema_mid=self.settings.get("scanner_d_ema_mid", 150),
            ema_slow=self.settings.get("scanner_d_ema_slow", 200),
            require_prev_day_breakout=self.settings.get("scanner_d_require_breakout", True),
            prev_state=prev_state,
        )

        curr_state = result.get("direction", "NEUTRAL")
        self._symbol_ema_states[sym] = curr_state

        if result["passed"] and result["signal"]:
            sig = result["signal"]
            direction = result["direction"]
            sig_type = result.get("signal_type", "ENTRY")

            # Deduplication key: one alert per strategy D + direction + sig_type + symbol + candle date
            ts_key = data.get("last_5m_ts")
            if hasattr(ts_key, "strftime"):
                ts_str = ts_key.strftime("%Y%m%d")
            else:
                ts_str = str(ts_key)[:10]
            key = f"D_{direction}_{sig_type}_{sym}_{ts_str}"

            if key not in self._seen_keys:
                if not sig.get("futures_symbol") and data.get("futures_symbol"):
                    sig["futures_symbol"] = data["futures_symbol"]
                if not sig.get("expiry") and data.get("expiry"):
                    sig["expiry"] = str(data["expiry"])
                if sig.get("futures_price") is None and data.get("futures_price"):
                    sig["futures_price"] = data["futures_price"]

                opt_type = "CE" if direction == "BULLISH" else "PE"
                atm = resolve_atm_option(self.kite_client, sym, opt_type, spot_price=data.get("spot_price"), log=self.log)
                if atm:
                    sig["atm_option"] = atm
                    if not sig.get("expiry") and atm.get("expiry"):
                        sig["expiry"] = atm["expiry"]
                    if not sig.get("futures_symbol") and atm.get("futures_symbol"):
                        sig["futures_symbol"] = atm["futures_symbol"]
                self._emit_signal(sig, key)

    def _eval_strategy_e(self, data, sm_acc=None, sm_dist=None, sm_conf=None, sm_watch=None):
        sym = data["symbol"]
        daily_candles = data.get("daily_candles") or []
        candles_15m = data.get("candles_15m") or []
        candle_ts = data.get("last_15m_ts") or data.get("last_5m_ts")
        if not candle_ts or (hasattr(candle_ts, "hour") and candle_ts.hour == 0 and candle_ts.minute == 0 and candle_ts.second == 0):
            candle_ts = datetime.datetime.now()

        result = scanner_strategy.evaluate_strategy_e(
            candles=daily_candles,
            symbol=sym,
            futures_symbol=data.get("futures_symbol"),
            expiry=data.get("expiry"),
            spot_price=data.get("spot_price"),
            futures_price=data.get("futures_price"),
            current_oi=data.get("current_oi"),
            candles_15m=candles_15m,
            candle_ts=candle_ts,
            supertrend_period=self.settings.get("scanner_supertrend_period", 10),
            supertrend_multiplier=self.settings.get("scanner_supertrend_multiplier", 3.0),
        )

        cat = result.get("category", "WATCHLIST")
        score = result.get("score", 0)
        direction = result.get("direction", "WAIT")
        v = result.get("values", {})

        entry_record = {
            "symbol": sym,
            "futures_symbol": data.get("futures_symbol") or f"{sym} FUT",
            "spot_price": data.get("spot_price"),
            "score": score,
            "category": cat,
            "direction": direction,
            "signal_name": result.get("signal_name", "WAIT"),
            "dist_demand_pct": v.get("zone_data", {}).get("dist_to_demand_pct"),
            "dist_supply_pct": v.get("zone_data", {}).get("dist_to_supply_pct"),
            "obv_trend": v.get("obv_data", {}).get("obv_trend"),
            "vol_expansion": v.get("vol_data", {}).get("expansion_ratio"),
            "buildup": v.get("futures_data", {}).get("buildup"),
            "supertrend": v.get("supertrend", {}).get("trend"),
            "order_flow": v.get("futures_data", {}).get("order_flow_status"),
            "entry_price": v.get("entry_price"),
            "sl": v.get("sl"),
            "t1": v.get("t1"),
            "t2": v.get("t2"),
            "risk_reward": v.get("risk_reward"),
        }

        # Route to dashboard buckets
        if sm_conf is not None and result.get("passed") and direction in ("BULLISH", "BEARISH"):
            sm_conf.append(entry_record)
        if sm_acc is not None and cat in ("EARLY ACCUMULATION", "CONFIRMED ACCUMULATION", "BREAKOUT READY"):
            sm_acc.append(entry_record)
        if sm_dist is not None and cat in ("EARLY DISTRIBUTION", "CONFIRMED DISTRIBUTION", "BREAKDOWN READY"):
            sm_dist.append(entry_record)
        if sm_watch is not None and cat == "WATCHLIST":
            sm_watch.append(entry_record)

        # Reversal detection: if this symbol now shows opposite institutional flow, clear previous opposite signals
        if direction in ("BULLISH", "BEARISH"):
            opp = "BEARISH" if direction == "BULLISH" else "BULLISH"
            self._clear_reversed_signal(sym, old_direction=opp, new_direction=direction, strategy_id="E")

        # Standard Alert Emission for Confirmed Signals
        if result["passed"] and result["signal"]:
            sig = result["signal"]
            ts_key = candle_ts
            ts_str = ts_key.strftime("%Y%m%d") if hasattr(ts_key, "strftime") else str(ts_key)[:10]
            key = f"E_{direction}_{sym}_{ts_str}"

            if key not in self._seen_keys:
                if not sig.get("futures_symbol") and data.get("futures_symbol"):
                    sig["futures_symbol"] = data["futures_symbol"]
                if not sig.get("expiry") and data.get("expiry"):
                    sig["expiry"] = str(data["expiry"])
                if sig.get("futures_price") is None and data.get("futures_price"):
                    sig["futures_price"] = data["futures_price"]

                opt_type = "CE" if direction == "BULLISH" else "PE"
                atm = resolve_atm_option(self.kite_client, sym, opt_type, spot_price=data.get("spot_price"), log=self.log)
                if atm:
                    sig["atm_option"] = atm
                    if not sig.get("expiry") and atm.get("expiry"):
                        sig["expiry"] = atm["expiry"]
                    if not sig.get("futures_symbol") and atm.get("futures_symbol"):
                        sig["futures_symbol"] = atm["futures_symbol"]
                self._emit_signal(sig, key)

    def _fetch_options_chain_atm(self, deriv_name, spot_price, num_strikes=2):
        """Fetches ATM +- num_strikes Call and Put options quotes for derivatives confluence."""
        chain = []
        if not spot_price or spot_price <= 0:
            return chain
        try:
            step = 50 if deriv_name == "NIFTY" else (100 if deriv_name == "BANKNIFTY" else 50)
            atm_strike = round(spot_price / step) * step
            strikes = [atm_strike + i * step for i in range(-num_strikes, num_strikes + 1)]
            strike_floats = [float(s) for s in strikes]

            today_date = datetime.date.today()
            all_instr = self.kite_client.get_instruments("NFO")
            opt_instrs = [
                ins for ins in all_instr
                if ins.get("name") == deriv_name and ins.get("instrument_type") in ("CE", "PE")
                   and ins.get("expiry") and ins.get("expiry") >= today_date
            ]
            if not opt_instrs:
                return chain
            expiries = sorted(set(ins["expiry"] for ins in opt_instrs))
            nearest_exp = expiries[0]
            target_instrs = [
                ins for ins in opt_instrs
                if ins.get("expiry") == nearest_exp and float(ins.get("strike", 0)) in strike_floats
            ]
            if not target_instrs:
                return chain

            keys = [f"NFO:{ins['tradingsymbol']}" for ins in target_instrs]
            q = self.kite_client.get_quote(keys)
            for ins in target_instrs:
                sym_k = f"NFO:{ins['tradingsymbol']}"
                item_q = q.get(sym_k, {})
                chain.append({
                    "strike": float(ins["strike"]),
                    "instrument_type": ins["instrument_type"],
                    "tradingsymbol": ins["tradingsymbol"],
                    "oi": int(item_q.get("oi") or 0),
                    "volume": int(item_q.get("volume") or 0),
                    "last_price": float(item_q.get("last_price") or 0.0),
                })
        except Exception as exc:
            self.log(f"[scanner] fetch_options_chain note: {exc!r}")
        return chain

    def _eval_strategy_f(self, spot_symbol="NIFTY"):
        """Evaluates Strategy F (NIFTY 50 Sustained Breakout + F&O OI Confirmation)."""
        try:
            # 1. Spot instrument for NIFTY
            spot_instr = futures_engine.get_spot_instrument(self.kite_client, spot_symbol)
            spot_token = spot_instr["instrument_token"]
            spot_key = f"NSE:{spot_instr['tradingsymbol']}"

            # 2. Futures instrument for NIFTY
            deriv_name = futures_engine.resolve_derivative_name(spot_symbol)
            fut_instr = self.kite_client.get_nfo_futures_instrument(deriv_name)
            if not fut_instr:
                return
            fut_token = fut_instr["instrument_token"]
            fut_symbol = fut_instr["tradingsymbol"]
            fut_key = f"NFO:{fut_symbol}"

            # 3. Quotes
            q = self.kite_client.get_quote([spot_key, fut_key])
            spot_q = q.get(spot_key, {})
            fut_q = q.get(fut_key, {})
            spot_price = spot_q.get("last_price")
            fut_price = fut_q.get("last_price")
            curr_oi = fut_q.get("oi")
            fut_vol = fut_q.get("volume")

            # 4. 5m candles for spot and futures
            now = datetime.datetime.now()
            start_5m = now - datetime.timedelta(days=5)
            c5_spot = self.kite_client.get_intraday_history(spot_token, start_5m, now, interval="5minute") or []
            c5_fut = self.kite_client.get_intraday_history(fut_token, start_5m, now, interval="5minute") or []
            if not c5_spot:
                return

            prev_fut_price = c5_fut[-2]["close"] if len(c5_fut) >= 2 else None
            prev_oi = c5_fut[-2].get("oi") if len(c5_fut) >= 2 else None

            # 5. Fetch options chain ATM +- 2 strikes
            options_chain = self._fetch_options_chain_atm(deriv_name, spot_price or c5_spot[-1]["close"])

            # 6. Evaluate Strategy F
            candle_ts = c5_spot[-1].get("date") if c5_spot else now
            result = scanner_strategy.evaluate_strategy_f(
                candles_5m=c5_spot,
                symbol=spot_symbol,
                spot_price=spot_price,
                futures_symbol=fut_symbol,
                futures_price=fut_price,
                prev_futures_price=prev_fut_price,
                futures_volume=fut_vol,
                current_oi=curr_oi,
                prev_oi=prev_oi,
                options_chain=options_chain,
                futures_candles_5m=c5_fut,
                candle_ts=candle_ts,
            )

            # 7. Update self._sustained_breakout_data (3 panels)
            v = result.get("values", {})
            ema_aligned = v.get("ema_data", {}).get("stacked_bull") or v.get("ema_data", {}).get("stacked_bear")
            price_vs_avwap = "ABOVE" if v.get("avwap_data", {}).get("above_avwap") else ("BELOW" if v.get("avwap_data", {}).get("below_avwap") else "AT VWAP")
            fut_chg = round(((fut_price - prev_fut_price) / prev_fut_price * 100), 2) if (fut_price and prev_fut_price) else 0.0
            oi_chg = round((v.get("futures_data", {}).get("change_oi", 0) / prev_oi * 100), 2) if (prev_oi and prev_oi > 0) else 0.0

            with self._lock:
                self._sustained_breakout_data = {
                    "market_trend": {
                        "symbol": spot_symbol,
                        "spot_price": v.get("spot_price"),
                        "ema10": v.get("ema_data", {}).get("ema10"),
                        "ema21": v.get("ema_data", {}).get("ema21"),
                        "ema50": v.get("ema_data", {}).get("ema50"),
                        "ema_aligned": ema_aligned,
                        "supertrend": v.get("supertrend_data", {}).get("val"),
                        "supertrend_trend": v.get("supertrend_data", {}).get("trend"),
                        "avwap": v.get("avwap_data", {}).get("avwap"),
                        "price_vs_avwap": price_vs_avwap,
                        "resistance": v.get("resistance"),
                        "support": v.get("support"),
                        "volume": v.get("volume_data", {}).get("current_vol"),
                        "volume_ma": v.get("volume_data", {}).get("vol_ma20"),
                        "volume_ratio": v.get("volume_data", {}).get("vol_ratio"),
                        "volume_valid": v.get("volume_data", {}).get("current_vol", 0) > 0,
                    },
                    "derivatives": {
                        "futures_symbol": fut_symbol,
                        "futures_price": v.get("futures_data", {}).get("futures_price"),
                        "futures_change_pct": fut_chg,
                        "futures_volume": v.get("futures_data", {}).get("futures_volume"),
                        "current_oi": v.get("futures_data", {}).get("current_oi"),
                        "oi_change_pct": oi_chg,
                        "buildup": v.get("futures_data", {}).get("buildup"),
                        "call_oi": v.get("options_data", {}).get("call_oi_total"),
                        "put_oi": v.get("options_data", {}).get("put_oi_total"),
                        "pcr": v.get("options_data", {}).get("pcr"),
                        "market_bias": v.get("options_data", {}).get("bias"),
                        "oi_valid": (curr_oi is not None and curr_oi > 0),
                    },
                    "trading_signal": {
                        "symbol": spot_symbol,
                        "direction": result.get("direction"),
                        "confidence_score": result.get("score"),
                        "setup_status": result.get("setup_status"),
                        "passed": result.get("passed"),
                        "entry_price": v.get("entry_price"),
                        "stop_loss": v.get("stop_loss"),
                        "target1": v.get("target1"),
                        "target2": v.get("target2"),
                        "risk_reward": v.get("risk_reward"),
                        "breakout_level": v.get("breakout_level"),
                        "reasons_for": result.get("reasons_for", []),
                        "reasons_against": result.get("reasons_against", []),
                        "timestamp": datetime.datetime.now().isoformat(),
                    },
                    "records": [result],
                }

            # Reversal check for Strategy F
            if result.get("direction") == "LONG":
                self._clear_reversed_signal(spot_symbol, old_direction="BEARISH", new_direction="BULLISH", strategy_id="F")
            elif result.get("direction") == "SHORT":
                self._clear_reversed_signal(spot_symbol, old_direction="BULLISH", new_direction="BEARISH", strategy_id="F")

            # 8. Standard Alert Emission for Confirmed Signals
            if result.get("passed") and result.get("signal"):
                sig = result["signal"]
                ts_str = candle_ts.strftime("%Y%m%d_%H%M") if hasattr(candle_ts, "strftime") else str(candle_ts)[:16]
                key = f"F_{result['direction']}_{spot_symbol}_{ts_str}"
                if key not in self._seen_keys:
                    opt_type = "CE" if result["direction"] == "LONG" else "PE"
                    atm = resolve_atm_option(self.kite_client, spot_symbol, opt_type, spot_price=spot_price, log=self.log)
                    if atm:
                        sig["atm_option"] = atm
                    self._emit_signal(sig, key)
        except Exception as exc:
            self.log(f"[scanner] Strategy F evaluation error: {exc!r}")

    def _clear_reversed_signal(self, symbol, old_direction, new_direction, strategy_id=None):
        """Removes opposite-direction signals for a symbol when a setup reverses."""
        removed = []
        with self._lock:
            remaining = []
            for s in self._signals:
                matches_sym = s.get("symbol") == symbol
                matches_dir = (old_direction == "ALL" or s.get("direction") == old_direction)
                matches_strat = (strategy_id is None or s.get("strategy_id") == strategy_id)
                if matches_sym and matches_dir and matches_strat:
                    removed.append(s)
                else:
                    remaining.append(s)
            self._signals = remaining

            # Clear deduplication keys for this symbol and old direction so re-triggers fire cleanly
            self._seen_keys = {
                k for k in self._seen_keys
                if not (symbol in k and (old_direction in k if old_direction != "ALL" else True))
            }

        if removed:
            self.log(f"[scanner] Reversal detected for {symbol}: cleared {len(removed)} old {old_direction} signal(s)")
            if self.on_signal_reversed:
                try:
                    self.on_signal_reversed({
                        "symbol": symbol,
                        "old_direction": old_direction,
                        "new_direction": new_direction,
                        "strategy_id": strategy_id,
                        "removed_ids": [s.get("id") for s in removed],
                    })
                except Exception as exc:  # noqa: BLE001
                    self.log(f"[scanner] on_signal_reversed callback raised: {exc!r}")

    def _emit_signal(self, signal, key):
        sym = signal.get("symbol")
        new_dir = signal.get("direction")
        strat_id = signal.get("strategy_id")

        # When emitting a signal, clear any conflicting opposite signals for this symbol
        opposite_dir = "BEARISH" if new_dir == "BULLISH" else "BULLISH"
        self._clear_reversed_signal(sym, old_direction=opposite_dir, new_direction=new_dir, strategy_id=strat_id)

        with self._lock:
            self._seen_keys.add(key)
            self._signals.append(signal)
        try:
            self.on_signal(signal)
        except Exception as exc:  # noqa: BLE001
            self.log(f"[scanner] on_signal callback raised {exc!r} (ignored)")
        self.log(f"[scanner] [OK] Signal emitted: {signal['strategy_name']} {signal['direction']} {signal['symbol']}")


def evaluate_strategy_f_standalone(kite_client, spot_symbol="NIFTY"):
    """Standalone evaluator for Strategy F when scanner engine is idle or stopped."""
    try:
        spot_instr = futures_engine.get_spot_instrument(kite_client, spot_symbol)
        spot_token = spot_instr["instrument_token"]
        spot_key = f"NSE:{spot_instr['tradingsymbol']}"

        deriv_name = futures_engine.resolve_derivative_name(spot_symbol)
        fut_instr = kite_client.get_nfo_futures_instrument(deriv_name)
        if not fut_instr:
            return {"market_trend": {}, "derivatives": {}, "trading_signal": {}, "records": []}
        fut_token = fut_instr["instrument_token"]
        fut_symbol = fut_instr["tradingsymbol"]
        fut_key = f"NFO:{fut_symbol}"

        q = kite_client.get_quote([spot_key, fut_key])
        spot_q = q.get(spot_key, {})
        fut_q = q.get(fut_key, {})
        spot_price = spot_q.get("last_price")
        fut_price = fut_q.get("last_price")
        curr_oi = fut_q.get("oi")
        fut_vol = fut_q.get("volume")

        now = datetime.datetime.now()
        start_5m = now - datetime.timedelta(days=5)
        c5_spot = kite_client.get_intraday_history(spot_token, start_5m, now, interval="5minute") or []
        c5_fut = kite_client.get_intraday_history(fut_token, start_5m, now, interval="5minute") or []
        if not c5_spot:
            return {"market_trend": {}, "derivatives": {}, "trading_signal": {}, "records": []}

        prev_fut_price = c5_fut[-2]["close"] if len(c5_fut) >= 2 else None
        prev_oi = c5_fut[-2].get("oi") if len(c5_fut) >= 2 else None

        step = 50 if deriv_name == "NIFTY" else (100 if deriv_name == "BANKNIFTY" else 50)
        sp_ref = spot_price or c5_spot[-1]["close"]
        atm_strike = round(sp_ref / step) * step
        strikes = [atm_strike + i * step for i in range(-2, 3)]
        strike_floats = [float(s) for s in strikes]
        all_instr = kite_client.get_instruments("NFO")
        today_date = datetime.date.today()
        opt_instrs = [
            ins for ins in all_instr
            if ins.get("name") == deriv_name and ins.get("instrument_type") in ("CE", "PE")
               and ins.get("expiry") and ins.get("expiry") >= today_date
        ]
        chain = []
        if opt_instrs:
            expiries = sorted(set(ins["expiry"] for ins in opt_instrs))
            nearest_exp = expiries[0]
            target_instrs = [
                ins for ins in opt_instrs
                if ins.get("expiry") == nearest_exp and float(ins.get("strike", 0)) in strike_floats
            ]
            if target_instrs:
                keys = [f"NFO:{ins['tradingsymbol']}" for ins in target_instrs]
                q_opt = kite_client.get_quote(keys)
                for ins in target_instrs:
                    sym_k = f"NFO:{ins['tradingsymbol']}"
                    item_q = q_opt.get(sym_k, {})
                    chain.append({
                        "strike": float(ins["strike"]),
                        "instrument_type": ins["instrument_type"],
                        "tradingsymbol": ins["tradingsymbol"],
                        "oi": int(item_q.get("oi") or 0),
                        "volume": int(item_q.get("volume") or 0),
                        "last_price": float(item_q.get("last_price") or 0.0),
                    })

        candle_ts = c5_spot[-1].get("date") if c5_spot else now
        result = scanner_strategy.evaluate_strategy_f(
            candles_5m=c5_spot,
            symbol=spot_symbol,
            spot_price=spot_price,
            futures_symbol=fut_symbol,
            futures_price=fut_price,
            prev_futures_price=prev_fut_price,
            futures_volume=fut_vol,
            current_oi=curr_oi,
            prev_oi=prev_oi,
            options_chain=chain,
            futures_candles_5m=c5_fut,
            candle_ts=candle_ts,
        )

        v = result.get("values", {})
        ema_aligned = v.get("ema_data", {}).get("stacked_bull") or v.get("ema_data", {}).get("stacked_bear")
        price_vs_avwap = "ABOVE" if v.get("avwap_data", {}).get("above_avwap") else ("BELOW" if v.get("avwap_data", {}).get("below_avwap") else "AT VWAP")
        fut_chg = round(((fut_price - prev_fut_price) / prev_fut_price * 100), 2) if (fut_price and prev_fut_price) else 0.0
        oi_chg = round((v.get("futures_data", {}).get("change_oi", 0) / prev_oi * 100), 2) if (prev_oi and prev_oi > 0) else 0.0

        return {
            "market_trend": {
                "symbol": spot_symbol,
                "spot_price": v.get("spot_price"),
                "ema10": v.get("ema_data", {}).get("ema10"),
                "ema21": v.get("ema_data", {}).get("ema21"),
                "ema50": v.get("ema_data", {}).get("ema50"),
                "ema_aligned": ema_aligned,
                "supertrend": v.get("supertrend_data", {}).get("val"),
                "supertrend_trend": v.get("supertrend_data", {}).get("trend"),
                "avwap": v.get("avwap_data", {}).get("avwap"),
                "price_vs_avwap": price_vs_avwap,
                "resistance": v.get("resistance"),
                "support": v.get("support"),
                "volume": v.get("volume_data", {}).get("current_vol"),
                "volume_ma": v.get("volume_data", {}).get("vol_ma20"),
                "volume_ratio": v.get("volume_data", {}).get("vol_ratio"),
                "volume_valid": v.get("volume_data", {}).get("current_vol", 0) > 0,
            },
            "derivatives": {
                "futures_symbol": fut_symbol,
                "futures_price": v.get("futures_data", {}).get("futures_price"),
                "futures_change_pct": fut_chg,
                "futures_volume": v.get("futures_data", {}).get("futures_volume"),
                "current_oi": v.get("futures_data", {}).get("current_oi"),
                "oi_change_pct": oi_chg,
                "buildup": v.get("futures_data", {}).get("buildup"),
                "call_oi": v.get("options_data", {}).get("call_oi_total"),
                "put_oi": v.get("options_data", {}).get("put_oi_total"),
                "pcr": v.get("options_data", {}).get("pcr"),
                "market_bias": v.get("options_data", {}).get("bias"),
                "oi_valid": (curr_oi is not None and curr_oi > 0),
            },
            "trading_signal": {
                "symbol": spot_symbol,
                "direction": result.get("direction"),
                "confidence_score": result.get("score"),
                "setup_status": result.get("setup_status"),
                "passed": result.get("passed"),
                "entry_price": v.get("entry_price"),
                "stop_loss": v.get("stop_loss"),
                "target1": v.get("target1"),
                "target2": v.get("target2"),
                "risk_reward": v.get("risk_reward"),
                "breakout_level": v.get("breakout_level"),
                "reasons_for": result.get("reasons_for", []),
                "reasons_against": result.get("reasons_against", []),
                "timestamp": datetime.datetime.now().isoformat(),
            },
            "records": [result],
        }
    except Exception as exc:
        print(f"[scanner] evaluate_strategy_f_standalone error: {exc!r}")
        return {"market_trend": {}, "derivatives": {}, "trading_signal": {}, "records": []}
