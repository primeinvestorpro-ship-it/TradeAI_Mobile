"""
Thin, proxy-aware wrapper around KiteConnect + KiteTicker.

Responsibilities:
  - login (Flask /kite/callback redirect catcher on the same port as the
    dashboard), token.json caching per-day
  - instrument lookup (tradingsymbol + LIVE lot_size, nearest expiry) - never hardcoded
  - live tick store (thread-safe) fed by KiteTicker, so the rest of the
    system reads prices/OI from memory instead of polling REST in a loop
  - order placement, hard-gated by settings["dry_run"]

Kite developer console Redirect URL must be set to:
  http://127.0.0.1:5050/kite/callback
(same port as the dashboard — no separate listener needed)
"""

import datetime
import json
import os
import re
import threading
import time
import webbrowser
from urllib.parse import parse_qs, unquote, urlparse

from kiteconnect import KiteConnect, KiteTicker

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")  # absolute - never dependent on the process's cwd

# ---------------------------------------------------------------------------
# Flask-based redirect catcher
# The Flask app registers GET /kite/callback which calls set_callback_result()
# below.  The login thread blocks on _callback_event instead of running its
# own HTTPServer on a second port.
# ---------------------------------------------------------------------------
REDIRECT_URL = "http://127.0.0.1:5050/kite/callback"  # must match Kite console exactly
LOGIN_TIMEOUT_SECONDS = 240

# Module-level state shared between Flask's callback route and login().
_callback_event = threading.Event()
_callback_token: str | None = None
_callback_error: str | None = None
_callback_lock = threading.Lock()


def set_callback_result(token: str | None, error: str | None = None) -> None:
    """Called by the Flask /kite/callback route to deliver the token (or error)
    to the waiting login() thread.  Thread-safe."""
    global _callback_token, _callback_error  # noqa: PLW0603
    with _callback_lock:
        _callback_token = token
        _callback_error = error
    _callback_event.set()


def _wait_for_callback_token(timeout_seconds: int = LOGIN_TIMEOUT_SECONDS) -> str:
    """Block until set_callback_result() is called (by the Flask route) or
    the timeout expires.  Returns the request_token on success."""
    global _callback_token, _callback_error  # noqa: PLW0603
    # Check if a fresh token was already received before wait was entered
    with _callback_lock:
        if _callback_token:
            tok = _callback_token
            _callback_token = None
            _callback_event.clear()
            return tok
        _callback_error = None
        _callback_event.clear()

    fired = _callback_event.wait(timeout=timeout_seconds)

    with _callback_lock:
        tok = _callback_token
        err = _callback_error
        _callback_token = None
        _callback_error = None
        _callback_event.clear()

    if not fired:
        raise TimeoutError(
            f"Login timed out after {timeout_seconds}s waiting for Kite's redirect on {REDIRECT_URL} "
            f"— double-check your Kite app's Redirect URL is set to EXACTLY {REDIRECT_URL} "
            f"in the Kite developer console."
        )
    if err and not tok:
        raise RuntimeError(f"Kite login failed: {err}")
    if not tok:
        raise RuntimeError("Kite redirect received but no request_token found.")
    return tok


def load_cached_access_token():
    """
    Return today's cached Kite access_token from token.json, or None if it's
    missing or from a previous day. This is what lets a login stay valid for
    the whole trading day even if the app itself is restarted in between -
    Kite's own tokens are valid until ~6 AM the next day regardless of
    whether this process keeps running.
    """
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("date") != _today_str():
        return None
    return data.get("access_token")


def _today_str():
    return datetime.date.today().isoformat()


def _build_proxies(proxy_settings):
    if not proxy_settings or not proxy_settings.get("enabled"):
        return {}
    host = proxy_settings.get("host", "")
    port = proxy_settings.get("port", "")
    if not host or not port:
        return {}
    user = proxy_settings.get("user", "")
    pwd = proxy_settings.get("pass", "")
    auth = f"{user}:{pwd}@" if user else ""
    proxy_url = f"http://{auth}{host}:{port}"
    return {"http": proxy_url, "https": proxy_url}


_TOKEN_RE = re.compile(r"request_token=([^\&\s]+)")
_TOKEN_RE = re.compile(r"request_token=([^&\s]+)")


def extract_request_token(raw_input):
    """
    Pull a request_token out of whatever the user pastes: the full (even if
    'can't be reached') redirect URL Kite sent them to, or just the bare
    token itself. This is the manual fallback for when the automatic
    Flask /kite/callback redirect can't be reached - usually because the
    Kite app's Redirect URL isn't registered as EXACTLY:
      http://127.0.0.1:5050/kite/callback
    """
    raw_input = (raw_input or "").strip()
    if not raw_input:
        raise ValueError("Paste the redirect URL or request_token first.")

    match = _TOKEN_RE.search(raw_input)
    if match:
        return unquote(match.group(1))

    if raw_input.startswith("http") or " " in raw_input or "?" in raw_input:
        raise ValueError("Could not find a request_token in what you pasted - "
                          "paste the full URL from your browser's address bar after login, "
                          "or just the request_token value itself.")
    return raw_input


class KiteClient:
    def __init__(self, settings, log=print):
        self.settings = settings
        self.log = log
        self.proxies = _build_proxies(settings.get("proxy"))

        try:
            self.kite = KiteConnect(api_key=settings["kite_api_key"], proxies=self.proxies)
        except TypeError:
            # Installed kiteconnect version doesn't accept `proxies=` in the
            # constructor - fall back to setting it on the underlying session.
            self.kite = KiteConnect(api_key=settings["kite_api_key"])
            if self.proxies:
                self.kite.reqsession.proxies.update(self.proxies)

        self.ticker = None
        self._tick_lock = threading.Lock()
        self._tick_store = {}  # instrument_token -> latest tick dict
        self._token_meta = {}  # instrument_token -> {"tradingsymbol", "strike", "type", "lot_size"}
        self._ticker_connected = threading.Event()
        self._last_tick_time = None  # wall-clock time.time() of the most recent tick received (any instrument)
        self._instruments_cache = {}  # exchange -> (timestamp, list_of_dicts)

    # ---------------------------------------------------------------- auth

    def login(self, redirect_display_url=None, open_browser=True):
        """Reuse today's cached access_token if present, else open the Kite
        login URL in the browser and wait for the Flask /kite/callback route
        to deliver the request_token via set_callback_result().

        redirect_display_url is kept for API compatibility but is no longer
        used (the Flask route handles the browser redirect itself)."""
        cached = self._load_cached_token()
        if cached:
            self.kite.set_access_token(cached)
            self.log(f"[kite_client] reused cached access_token from {TOKEN_FILE}")
            return

        login_url = self.kite.login_url()
        if open_browser:
            self.log(f"[kite_client] opening browser for Kite login: {login_url}")
            webbrowser.open(login_url)

        self.log(
            f"[kite_client] waiting up to {LOGIN_TIMEOUT_SECONDS}s for Kite's redirect on {REDIRECT_URL} "
            f"— IMPORTANT: your Kite app's Redirect URL (in the Kite developer console) must be set to "
            f"EXACTLY {REDIRECT_URL} in the Kite developer console."
        )
        try:
            request_token = _wait_for_callback_token(LOGIN_TIMEOUT_SECONDS)
        except Exception:
            # If callback route already completed login in the meantime, check cached token
            cached_now = self._load_cached_token()
            if cached_now:
                self.kite.set_access_token(cached_now)
                self.log(f"[kite_client] login completed via callback route")
                return
            raise

        # Check if callback route already exchanged this token
        cached_now = self._load_cached_token()
        if cached_now:
            self.kite.set_access_token(cached_now)
            self.log(f"[kite_client] login completed via callback route")
            return

        self.complete_login(request_token)

    def complete_login(self, request_token):
        """
        Exchange a request_token for an access_token and cache it. This is
        the shared final step for both the automatic flow (login() above,
        which catches the token itself via the local redirect listener) and
        the manual fallback (the web UI's "paste redirect URL/token" box,
        for when the automatic listener can't be reached - e.g. the Kite
        app's Redirect URL isn't registered as exactly http://127.0.0.1:5000/).
        """
        data = self.kite.generate_session(request_token, api_secret=self.settings["kite_api_secret"])
        access_token = data["access_token"]
        self.kite.set_access_token(access_token)
        self._save_token(access_token)
        self.log("[kite_client] login successful, access_token cached")

    def _load_cached_token(self):
        return load_cached_access_token()

    def _save_token(self, access_token):
        with open(TOKEN_FILE, "w") as f:
            json.dump({"access_token": access_token, "date": _today_str()}, f)

    # --------------------------------------------------------- instruments

    def get_instruments(self, exchange="NFO", ttl_seconds=3600):
        """Fetch instruments dump cached in-memory for `ttl_seconds` to prevent
        expensive and rate-limited multi-megabyte repeated downloads."""
        now = time.time()
        cached = self._instruments_cache.get(exchange)
        if cached and (now - cached[0] < ttl_seconds):
            return cached[1]
        data = self.kite.instruments(exchange)
        self._instruments_cache[exchange] = (now, data)
        return data

    def get_nfo_option_chain(self, name, today=None):
        """
        Returns (nearest_expiry, list_of_instrument_dicts) for the given
        index/stock `name` (e.g. "NIFTY", "CONCOR"), CE/PE only, nearest expiry >= today.
        Always fetched from cached Kite NFO dump - tradingsymbol and lot_size are never
        hardcoded, since NSE revises lot sizes periodically.
        """
        today = today or datetime.date.today()
        instruments = self.get_instruments("NFO")
        options = [
            i
            for i in instruments
            if i["name"] == name and i["instrument_type"] in ("CE", "PE") and i["expiry"] >= today
        ]
        if not options:
            raise ValueError(f"no NFO options found for {name!r}")

        expiries = sorted({i["expiry"] for i in options})
        selected_expiry = expiries[0]
        # If nearest contract is expiring within rollover_days (e.g. within 3 days, month-end week),
        # roll over to next month's contract so premiums have adequate time value and avoid delivery margin risk
        rollover_days = self.settings.get("expiry_rollover_days", 3)
        if len(expiries) > 1 and (selected_expiry - today).days <= rollover_days:
            selected_expiry = expiries[1]

        chain = [i for i in options if i["expiry"] == selected_expiry]
        return selected_expiry, chain

    def get_index_instrument(self, tradingsymbol, exchange="NSE"):
        instruments = self.get_instruments(exchange)
        for i in instruments:
            if i["tradingsymbol"] == tradingsymbol:
                return i
        raise ValueError(f"instrument {tradingsymbol!r} not found on {exchange}")

    def get_nfo_futures_instrument(self, name, today=None):
        """
        NFO FUTURES instrument for the given name (e.g. "NIFTY", "CONCOR", "AMBUJACEM").
        If the nearest contract is within `expiry_rollover_days` (default 3 days),
        automatically selects the NEXT month contract.
        """
        today = today or datetime.date.today()
        instruments = self.get_instruments("NFO")
        futures = [
            i for i in instruments
            if i["name"] == name and i["instrument_type"] == "FUT" and i["expiry"] >= today
        ]
        if not futures:
            raise ValueError(f"no NFO futures found for {name!r}")

        expiries = sorted({i["expiry"] for i in futures})
        selected_expiry = expiries[0]
        rollover_days = self.settings.get("expiry_rollover_days", 3)
        if len(expiries) > 1 and (selected_expiry - today).days <= rollover_days:
            selected_expiry = expiries[1]

        return next(i for i in futures if i["expiry"] == selected_expiry)

    def get_quote(self, exchange_tradingsymbols):
        """One-off REST quote (list of "EXCH:TRADINGSYMBOL" strings). Used
        only for the initial snapshot before the ticker is streaming, or as
        a fallback if a specific token's tick hasn't arrived yet."""
        return self.kite.quote(exchange_tradingsymbols)

    def get_daily_history(self, instrument_token, from_date, to_date, oi=False):
        """
        oi=True adds an "oi" field (closing open interest) to each returned
        candle - confirmed via the installed kiteconnect SDK's own
        historical_data()/_format_historical() (only F&O instruments carry
        a meaningful value; the API silently returns none for equities/
        indices, same as it always has). Used to compare an option's
        current OI against yesterday's close (fresh build-up vs unwinding).
        """
        return self.kite.historical_data(instrument_token, from_date, to_date, interval="day", oi=oi)

    def get_intraday_history(self, instrument_token, from_dt, to_dt, interval="5minute"):
        """Same idea as get_daily_history but for sub-day candles (e.g. today's
        5-minute candles from market open till now) - used to backfill the
        dashboard's live chart with real history instead of starting blank."""
        return self.kite.historical_data(instrument_token, from_dt, to_dt, interval=interval)

    # -------------------------------------------------------------- ticker

    def start_ticker(self, instrument_tokens, token_meta=None, on_tick=None):
        """Connect KiteTicker in the background and subscribe in FULL mode
        (LTP + depth + OI). All subsequent price/OI reads should come from
        get_tick()/get_all_ticks(), not repeated REST polling.

        `on_tick`, if given, is called once per raw tick dict (e.g. to push
        it to a UI over WebSocket) IN ADDITION TO storing it - it is best-
        effort and sandboxed: any exception it raises is caught and logged,
        never allowed to affect the tick store or the trading logic."""
        self._token_meta = token_meta or {}

        # KiteTicker auto-reconnects by default (reconnect=True), with an
        # exponential backoff up to reconnect_max_delay and up to
        # reconnect_max_tries attempts, and auto-resubscribes on reconnect -
        # this is the underlying library's own behavior, not something we
        # implement here. What we DO need is visibility into it: without
        # logging on_reconnect/on_noreconnect, a mid-session disconnect would
        # be invisible until someone noticed prices had stopped moving.
        self.ticker = KiteTicker(
            self.settings["kite_api_key"], self.kite.access_token,
            reconnect=True, reconnect_max_delay=15, reconnect_max_tries=100,
        )

        def on_ticks(ws, ticks):
            with self._tick_lock:
                for t in ticks:
                    self._tick_store[t["instrument_token"]] = t
                self._last_tick_time = time.time()
            if on_tick is not None:
                for t in ticks:
                    try:
                        on_tick(t)
                    except Exception as exc:  # noqa: BLE001 - UI hook must never break the ticker
                        self.log(f"[kite_client] on_tick callback raised {exc!r} (ignored)")

        def on_connect(ws, response):
            ws.subscribe(instrument_tokens)
            ws.set_mode(ws.MODE_FULL, instrument_tokens)
            self._ticker_connected.set()
            self.log(f"[kite_client] ticker connected, subscribed {len(instrument_tokens)} instruments")

        def on_close(ws, code, reason):
            self.log(f"[kite_client] ticker closed: {code} {reason}")

        def on_error(ws, code, reason):
            self.log(f"[kite_client] ticker error: {code} {reason}")

        def on_reconnect(ws, attempts_count):
            self.log(f"[kite_client] ticker RECONNECTING (attempt {attempts_count}) - "
                      f"live prices may be briefly stale until this succeeds")

        def on_noreconnect(ws):
            self.log("[kite_client] ticker gave up reconnecting after repeated failures - "
                     "live prices will NOT update until Execute is run again. "
                     "Check your internet connection.")

        self.ticker.on_ticks = on_ticks
        self.ticker.on_connect = on_connect
        self.ticker.on_close = on_close
        self.ticker.on_error = on_error
        self.ticker.on_reconnect = on_reconnect
        self.ticker.on_noreconnect = on_noreconnect

        self.ticker.connect(threaded=True)

    def wait_for_ticker(self, timeout=10):
        return self._ticker_connected.wait(timeout=timeout)

    def seconds_since_last_tick(self):
        """None if no tick has ever been received yet; otherwise how long
        ago (in seconds) the most recent tick (any instrument) arrived.
        Used to detect a stale/stalled live feed while positions are open."""
        if self._last_tick_time is None:
            return None
        return time.time() - self._last_tick_time

    def stop_ticker(self):
        if self.ticker is not None:
            try:
                self.ticker.close()
            except Exception:  # noqa: BLE001 - best-effort shutdown
                pass

    def get_tick(self, instrument_token):
        with self._tick_lock:
            return self._tick_store.get(instrument_token)

    def get_all_ticks(self):
        with self._tick_lock:
            return dict(self._tick_store)

    # -------------------------------------------------------------- orders

    def place_market_order(self, tradingsymbol, exchange, transaction_type, quantity, product=None):
        """
        variety=REGULAR, product=MIS/NRML, order_type=MARKET.
        transaction_type: kite.TRANSACTION_TYPE_BUY or _SELL.
        Hard-gated by dry_run - if dry_run is true, NOTHING is sent to Kite,
        only printed and simulated.
        """
        product = product or self.kite.PRODUCT_MIS
        if self.settings.get("dry_run", True):
            self.log(
                f"[DRY_RUN] would place order: {transaction_type} {quantity} x {tradingsymbol} "
                f"({exchange}) MARKET/{product}/REGULAR (market_protection=auto)"
            )
            return {"dry_run": True, "tradingsymbol": tradingsymbol, "transaction_type": transaction_type, "quantity": quantity, "product": product}

        order_id = self.kite.place_order(
            variety=self.kite.VARIETY_REGULAR,
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type,
            quantity=quantity,
            product=product,
            order_type=self.kite.ORDER_TYPE_MARKET,
            market_protection=self.kite.MARKET_PROTECTION_AUTO,
        )
        self.log(f"[kite_client] LIVE order placed: {transaction_type} {quantity} x {tradingsymbol} -> order_id={order_id}")
        return {"dry_run": False, "order_id": order_id, "tradingsymbol": tradingsymbol, "transaction_type": transaction_type, "quantity": quantity, "product": product}

    def get_actual_position_qty(self, tradingsymbol, exchange="NFO", product="MIS", expected_qty=None):
        """
        Real safety check used before every square-off SELL (see
        engine._square_off_one): asks Zerodha's own positions() API what net
        quantity is ACTUALLY still held for this tradingsymbol right now,
        instead of blindly trusting this project's in-memory record - which
        could be stale if the position was already exited manually (e.g. via
        the Zerodha app/website) outside this project. Placing a SELL for a
        position that's already flat would open a brand-new, unintended
        SHORT position instead of a harmless no-op, which this check exists
        specifically to prevent.

        Returns the real net quantity (positive = still long, 0 = flat,
        negative = net short - confirmed field meaning per Kite Connect's
        official /portfolio/positions docs). In dry_run, there's no real
        Zerodha position to check against, so this trusts the given
        expected_qty as-is (dry_run is pure simulation, never real state).
        """
        if self.settings.get("dry_run", True):
            return expected_qty

        positions = self.kite.positions()
        for p in positions.get("net", []):
            if (p.get("tradingsymbol") == tradingsymbol and p.get("exchange") == exchange
                    and p.get("product") == product):
                return p.get("quantity", 0)
        return 0
