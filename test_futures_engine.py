"""
Unit test for futures_engine import and basic data helpers.
Run: pytest test_futures_engine.py -v
"""

from unittest.mock import MagicMock
import futures_engine


def test_import_and_parse_hhmm():
    t = futures_engine._parse_hhmm("15:15")
    assert t.hour == 15
    assert t.minute == 15


def test_enter_futures_position_syntax_and_flow():
    mock_kite = MagicMock()
    mock_kite.kite.TRANSACTION_TYPE_BUY = "BUY"
    mock_kite.get_tick.return_value = None
    mock_kite.get_quote.return_value = {
        "NFO:RELIANCE26OCTFUT": {"last_price": 2850.0}
    }

    universe = {
        "futures_token": 12345,
        "futures_instrument": {"tradingsymbol": "RELIANCE26OCTFUT"},
        "lot_size": 250,
    }
    settings = {"lots": 1}

    positions = futures_engine.enter_futures_position(
        mock_kite, universe, "LONG", settings, log=lambda _: None
    )

    assert len(positions) == 1
    assert positions[0]["tradingsymbol"] == "RELIANCE26OCTFUT"
    assert positions[0]["entry_price"] == 2850.0
    assert positions[0]["qty"] == 250
    assert positions[0]["type"] == "LONG"


def test_get_active_futures_symbols_default_and_filter():
    # Fallback list when offline
    symbols = futures_engine.get_active_futures_symbols(kite_client=None)
    assert len(symbols) >= 50
    reliance = next((s for s in symbols if s["symbol"] == "RELIANCE"), None)
    assert reliance is not None
    assert reliance["lot_size"] == 250

    # Query filtering
    filtered = futures_engine.get_active_futures_symbols(kite_client=None, query="tata")
    assert len(filtered) > 0
    assert all("TATA" in s["symbol"].upper() for s in filtered)

    # Max lot size filtering (< 2500)
    filtered_lots = futures_engine.get_active_futures_symbols(kite_client=None, max_lot_size=2500)
    assert len(filtered_lots) > 0
    assert all(s["lot_size"] < 2500 for s in filtered_lots)


def test_api_futures_symbols_endpoint():
    import app as flask_app
    client = flask_app.app.test_client()
    resp = client.get("/api/futures_symbols?q=INFY")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert any(s["symbol"] == "INFY" for s in data["symbols"])


def test_api_analyze_and_spot_routes_respect_ticker():
    import app as flask_app
    client = flask_app.app.test_client()
    # When not logged in, spot_ltp returns 400 not logged in, but parses ticker cleanly
    resp = client.get("/api/spot_ltp?ticker=HAL")
    assert resp.status_code == 400
    assert "not logged in" in resp.get_json()["error"]


def test_build_option_chain_data():
    mock_kite = MagicMock()
    chain = []
    for s in [2300, 2320, 2340, 2360, 2380, 2400, 2420, 2440, 2460, 2480, 2500]:
        chain.append({
            "strike": s,
            "instrument_type": "CE",
            "instrument_token": 1000 + s,
            "tradingsymbol": f"RELIANCE26OCT{s}CE",
        })
        chain.append({
            "strike": s,
            "instrument_type": "PE",
            "instrument_token": 2000 + s,
            "tradingsymbol": f"RELIANCE26OCT{s}PE",
        })
    mock_kite.get_nfo_option_chain.return_value = ("2026-10-29", chain)
    mock_kite.get_all_ticks.return_value = {
        3400: {"oi": 500000, "volume_traded": 12000, "last_price": 35.0},
        4400: {"oi": 400000, "volume_traded": 11000, "last_price": 28.0},
        3440: {"oi": 800000, "volume_traded": 15000, "last_price": 18.0},
        4380: {"oi": 900000, "volume_traded": 14000, "last_price": 16.0},
    }
    mock_kite.get_quote.return_value = {}
    mock_kite.get_daily_history.return_value = []

    res = futures_engine.build_option_chain_data(
        mock_kite, "RELIANCE", spot_price=2405.0, log=lambda _: None
    )

    assert res is not None
    assert res["ticker"] == "RELIANCE"
    assert res["atm_strike"] == 2400.0
    assert res["strike_interval"] == 20.0
    assert len(res["rows"]) == 11
    assert res["resistance_strike"] == 2440.0
    assert res["support_strike"] == 2380.0
    assert res["ce_premium"] == 35.0
    assert res["pe_premium"] == 28.0
    assert res["net_premium"] == 63.0


def test_api_option_chain_endpoint():
    import app as flask_app
    client = flask_app.app.test_client()
    resp = client.get("/api/option_chain?ticker=RELIANCE")
    assert resp.status_code == 400
    assert "not logged in" in resp.get_json()["error"]


def test_resolve_symbols_and_get_spot_instrument():
    assert futures_engine.resolve_spot_tradingsymbol("NIFTY") == "NIFTY 50"
    assert futures_engine.resolve_spot_tradingsymbol("NIFTY 50") == "NIFTY 50"
    assert futures_engine.resolve_spot_tradingsymbol("BANKNIFTY") == "NIFTY BANK"
    assert futures_engine.resolve_spot_tradingsymbol("RELIANCE") == "RELIANCE"

    assert futures_engine.resolve_derivative_name("NIFTY 50") == "NIFTY"
    assert futures_engine.resolve_derivative_name("NIFTY") == "NIFTY"
    assert futures_engine.resolve_derivative_name("BANKNIFTY") == "BANKNIFTY"
    assert futures_engine.resolve_derivative_name("RELIANCE") == "RELIANCE"

    mock_kite = MagicMock()
    mock_kite.kite.instruments.return_value = [
        {"instrument_token": 738561, "tradingsymbol": "RELIANCE", "segment": "NSE", "instrument_type": "EQ"},
        {"instrument_token": 256265, "tradingsymbol": "NIFTY 50", "segment": "INDICES", "instrument_type": "INDEX"},
        {"instrument_token": 260105, "tradingsymbol": "NIFTY BANK", "segment": "INDICES", "instrument_type": "INDEX"},
    ]

    spot_rel = futures_engine.get_spot_instrument(mock_kite, "RELIANCE")
    assert spot_rel["instrument_token"] == 738561
    assert spot_rel["tradingsymbol"] == "RELIANCE"

    spot_nifty = futures_engine.get_spot_instrument(mock_kite, "NIFTY")
    assert spot_nifty["instrument_token"] == 256265
    assert spot_nifty["tradingsymbol"] == "NIFTY 50"

    spot_nifty50 = futures_engine.get_spot_instrument(mock_kite, "NIFTY 50")
    assert spot_nifty50["instrument_token"] == 256265


def test_build_futures_universe_for_index_and_stock():
    mock_kite = MagicMock()
    mock_kite.kite.instruments.return_value = [
        {"instrument_token": 256265, "tradingsymbol": "NIFTY 50", "segment": "INDICES"},
        {"instrument_token": 738561, "tradingsymbol": "RELIANCE", "segment": "NSE"},
    ]
    mock_kite.get_nfo_futures_instrument.side_effect = lambda name: {
        "NIFTY": {
            "instrument_token": 99901,
            "tradingsymbol": "NIFTY26OCTFUT",
            "expiry": "2026-10-27",
            "lot_size": 65,
        },
        "RELIANCE": {
            "instrument_token": 99902,
            "tradingsymbol": "RELIANCE26OCTFUT",
            "expiry": "2026-10-27",
            "lot_size": 250,
        },
    }[name]
    mock_kite.get_quote.side_effect = lambda keys: {
        "NSE:NIFTY 50": {"last_price": 25150.0, "ohlc": {"open": 25100.0}},
        "NSE:RELIANCE": {"last_price": 2850.0, "ohlc": {"open": 2840.0}},
    }

    # Test NIFTY (Index)
    u_nifty = futures_engine.build_futures_universe(mock_kite, "NIFTY", "NSE", log=lambda _: None)
    assert u_nifty["ticker"] == "NIFTY"
    assert u_nifty["derivative_name"] == "NIFTY"
    assert u_nifty["spot_tradingsymbol"] == "NIFTY 50"
    assert u_nifty["spot_token"] == 256265
    assert u_nifty["futures_tradingsymbol"] == "NIFTY26OCTFUT"
    assert u_nifty["lot_size"] == 65
    assert u_nifty["initial_quote"]["last_price"] == 25150.0

    # Test RELIANCE (Stock)
    u_rel = futures_engine.build_futures_universe(mock_kite, "RELIANCE", "NSE", log=lambda _: None)
    assert u_rel["ticker"] == "RELIANCE"
    assert u_rel["derivative_name"] == "RELIANCE"
    assert u_rel["spot_tradingsymbol"] == "RELIANCE"
    assert u_rel["spot_token"] == 738561
    assert u_rel["futures_tradingsymbol"] == "RELIANCE26OCTFUT"
    assert u_rel["lot_size"] == 250


def test_analyze_only_respects_stop_event():
    import threading
    stop_event = threading.Event()
    stop_event.set()  # pre-set stop signal
    events = []
    res = futures_engine.analyze_only(
        kite_client=MagicMock(),
        stop_event=stop_event,
        on_event=lambda e: events.append(e),
        log=lambda _: None,
    )
    assert res.get("status") == "STOPPED"
    assert any(e.get("type") == "stopped" for e in events)


def test_api_stop_route():
    import app as flask_app
    import threading
    client = flask_app.app.test_client()

    # When engine is not running, /api/stop returns 400
    with flask_app.STATE_LOCK:
        flask_app.STATE["running"] = False
    resp = client.post("/api/stop")
    assert resp.status_code == 400

    # When engine is running with a stop_event, /api/stop sets the event and returns 200
    stop_evt = threading.Event()
    with flask_app.STATE_LOCK:
        flask_app.STATE["running"] = True
        flask_app.STATE["stop_event"] = stop_evt
    resp = client.post("/api/stop")
    assert resp.status_code == 200
    assert stop_evt.is_set() is True


def test_expiry_rollover_logic():
    import datetime
    import kite_client
    settings = {"kite_api_key": "dummy", "kite_api_secret": "dummy", "expiry_rollover_days": 3, "dry_run": True}
    kc = kite_client.KiteClient(settings)

    # Synthetic instruments: oct 27 (tomorrow) and nov 26
    t_today = datetime.date(2026, 10, 26)
    t_oct = datetime.date(2026, 10, 27)
    t_nov = datetime.date(2026, 11, 26)
    kc.get_instruments = MagicMock(return_value=[
        {"name": "AMBUJACEM", "instrument_type": "FUT", "expiry": t_oct, "tradingsymbol": "AMBUJACEM26OCTFUT", "lot_size": 1200},
        {"name": "AMBUJACEM", "instrument_type": "FUT", "expiry": t_nov, "tradingsymbol": "AMBUJACEM26NOVFUT", "lot_size": 1200},
        {"name": "AMBUJACEM", "instrument_type": "CE", "expiry": t_oct, "strike": 370.0, "tradingsymbol": "AMBUJACEM26OCT370CE"},
        {"name": "AMBUJACEM", "instrument_type": "CE", "expiry": t_nov, "strike": 370.0, "tradingsymbol": "AMBUJACEM26NOV370CE"},
    ])

    # Futures rollover
    fut = kc.get_nfo_futures_instrument("AMBUJACEM", today=t_today)
    assert fut["expiry"] == t_nov
    assert fut["tradingsymbol"] == "AMBUJACEM26NOVFUT"

    # Options rollover
    opt_exp, opt_chain = kc.get_nfo_option_chain("AMBUJACEM", today=t_today)
    assert opt_exp == t_nov
    assert opt_chain[0]["tradingsymbol"] == "AMBUJACEM26NOV370CE"


def test_api_scanner_order_endpoint():
    import app as flask_app
    client = flask_app.app.test_client()

    # When not logged in -> 400
    with flask_app.STATE_LOCK:
        flask_app.STATE["logged_in"] = False
        flask_app.STATE["kite_client"] = None
    resp = client.post("/api/scanner/order", json={"tradingsymbol": "AMBUJACEM26NOV370CE", "quantity": 1200})
    assert resp.status_code == 400

    # When logged in
    mock_kc = MagicMock()
    mock_kc.kite.PRODUCT_MIS = "MIS"
    mock_kc.settings = {"dry_run": True}
    mock_kc.place_market_order.return_value = {
        "dry_run": True,
        "tradingsymbol": "AMBUJACEM26NOV370CE",
        "quantity": 1200,
        "transaction_type": "BUY",
        "product": "MIS",
    }
    with flask_app.STATE_LOCK:
        flask_app.STATE["logged_in"] = True
        flask_app.STATE["kite_client"] = mock_kc

    resp2 = client.post("/api/scanner/order", json={
        "tradingsymbol": "AMBUJACEM26NOV370CE",
        "exchange": "NFO",
        "transaction_type": "BUY",
        "quantity": 1200,
        "product": "NRML",
    })
    assert resp2.status_code == 200
    d = resp2.get_json()
    assert d["ok"] is True
    assert d["result"]["dry_run"] is True
    assert d["result"]["tradingsymbol"] == "AMBUJACEM26NOV370CE"
    mock_kc.place_market_order.assert_called_with(
        tradingsymbol="AMBUJACEM26NOV370CE",
        exchange="NFO",
        transaction_type="BUY",
        quantity=1200,
        product="NRML",
    )






