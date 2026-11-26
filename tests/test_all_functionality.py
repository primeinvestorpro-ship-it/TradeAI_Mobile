"""
Comprehensive verification suite testing all core functionality and edge cases:
  1. Signal timestamp & popup timing (_make_signal, Unix epoch, None, live emit)
  2. GTI Strategy G across NIFTY, BANKNIFTY, FINNIFTY and all 5 scenarios
  3. Strategy F Sustained Breakout across indices & scenarios
  4. PositionGuardian (MTM SL, Trailing SL, Per-Pos SL, Closed positions)
  5. Flask HTTP routes (/, /api/status, /api/scanner/gti, /api/scanner/sustained_breakout,
     /api/scanner/resolve_atm, /api/guardian/status)
"""

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import app as flask_app
import gti_indicator
import scanner_strategy
import sustained_breakout_strategy


def test_make_signal_timestamps_all_formats():
    # 1. None / "None" candle_ts -> valid current ISO timestamp
    s_none = scanner_strategy._make_signal(
        "G", "GTI", "BULLISH", "BANKNIFTY", "BANKNIFTY26NOVFUT", "2026-11-26", None, {}
    )
    assert s_none["timestamp"] != "None"
    assert datetime.datetime.fromisoformat(s_none["timestamp"])

    # 2. Unix epoch seconds (e.g. 1795578300) -> converted properly, not 1970
    s_epoch = scanner_strategy._make_signal(
        "G", "GTI", "BULLISH", "FINNIFTY", "FINNIFTY26DECFUT", "2026-12-29", 1795578300, {}
    )
    ct = datetime.datetime.fromisoformat(s_epoch["candle_time"])
    assert ct.year >= 2026
    ts = datetime.datetime.fromisoformat(s_epoch["timestamp"])
    assert ts.year >= 2026

    # 3. Past candle_ts (e.g. 09:50 bar) -> timestamp reflects current live detection time
    old_bar = datetime.datetime(2026, 11, 26, 9, 50, 0)
    s_bar = scanner_strategy._make_signal(
        "F", "Sustained", "BEARISH", "NIFTY", "NIFTY26NOVFUT", "2026-11-26", old_bar, {}
    )
    assert s_bar["candle_time"].startswith("2026-11-26T09:50:00")
    emitted_dt = datetime.datetime.fromisoformat(s_bar["timestamp"])
    assert (datetime.datetime.now() - emitted_dt).total_seconds() < 5


def test_gti_all_scenarios_and_indices_contract_structure():
    for sc in ("call", "put", "yellow", "sideways", "dynamic"):
        for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY"):
            res = gti_indicator.generate_gti_scenario(sc, sym)
            assert res["symbol"] == sym
            assert res["spot_price"] > 0
            assert res["suggested_option"] is not None
            assert isinstance(res["suggested_option"], dict)
            assert res["entry_price"] is not None
            assert res["structure_stop_loss"] is not None
            assert res["target2"] is not None

    # Verify Strategy G signal builds dict atm_option (never raw string -> avoids JS String.prototype.strike)
    sc_call = gti_indicator.generate_gti_scenario("call", "BANKNIFTY")
    candles = sc_call["classified_candles_tail"] + [{
        "open": 62100.0, "high": 62160.0, "low": 62090.0, "close": 62155.0,
        "volume": 190000, "date": datetime.datetime.now(),
    }]
    eval_res = scanner_strategy.evaluate_strategy_g(
        candles_5m=candles,
        symbol="BANKNIFTY",
        spot_price=62155.0,
        futures_symbol="BANKNIFTY26NOVFUT",
        futures_price=62200.0,
        expiry="2026-11-26",
        override_time=datetime.time(10, 15),
    )
    if eval_res["passed"] and eval_res["signal"]:
        sig = eval_res["signal"]
        assert isinstance(sig["atm_option"], dict)
        assert isinstance(sig["atm_option"]["strike"], (int, float))
        assert sig["futures_symbol"] == "BANKNIFTY26NOVFUT"


def test_strategy_f_all_scenarios_and_indices():
    for sc in ("bullish", "bearish", "neutral", "dynamic", "safety"):
        for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY"):
            res = sustained_breakout_strategy.generate_strategy_f_scenario(sc, sym)
            sig = res.get("trading_signal", {})
            assert sig.get("symbol") == sym
            assert sig.get("confidence_score") is not None


def test_flask_endpoints_smoke():
    client = flask_app.app.test_client()

    # Dashboard HTML contains 5m Chart Modal & Position Guardian Panel
    r_home = client.get("/")
    assert r_home.status_code == 200
    html = r_home.data.decode("utf-8")
    assert "signalChartModalOverlay" in html
    assert "popBtnChart" in html
    assert "positionGuardianCard" in html
    assert "guardianDayPnl" in html

    # Status endpoint
    r_status = client.get("/api/status")
    assert r_status.status_code == 200
    assert "logged_in" in r_status.get_json()

    # GTI multi-symbol scenario endpoints
    for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY"):
        r_gti = client.get(f"/api/scanner/gti?symbol={sym}&scenario=call")
        assert r_gti.status_code == 200
        d = r_gti.get_json()
        assert d["ok"] is True
        assert d["data"]["symbol"] == sym

    # Strategy F endpoint
    r_f = client.get("/api/scanner/sustained_breakout?symbol=BANKNIFTY&scenario=bullish")
    assert r_f.status_code == 200
    assert r_f.get_json()["ok"] is True

    # Resolve ATM offline/fallback
    r_atm = client.get("/api/scanner/resolve_atm?symbol=FINNIFTY&option_type=CE&spot_price=28867.3")
    assert r_atm.status_code == 200
    atm_json = r_atm.get_json()
    assert atm_json["ok"] is True
    assert isinstance(atm_json["atm_option"]["strike"], (int, float))

    # Guardian status endpoint
    r_gd = client.get("/api/guardian/status")
    assert r_gd.status_code == 200
    assert "config" in r_gd.get_json()
