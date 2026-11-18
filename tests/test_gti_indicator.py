"""
Unit tests for GTI Smart Money Trader Indicator & Signal Engine.
"""

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import gti_indicator


def test_gti_candle_classification():
    # 1. Accumulation Yellow
    acc_candle = {"open": 25000.0, "high": 25008.0, "low": 24994.0, "close": 25002.0, "volume": 70000}
    meta_y = gti_indicator.classify_gti_candle(acc_candle, atr_val=25.0, vol_ma=70000.0)
    assert meta_y["color"] == gti_indicator.CANDLE_COLOR_YELLOW
    assert meta_y["is_yellow"] is True

    # 2. Bullish Blue
    bull_candle = {"open": 25000.0, "high": 25050.0, "low": 24998.0, "close": 25048.0, "volume": 160000}
    meta_b = gti_indicator.classify_gti_candle(bull_candle, atr_val=25.0, vol_ma=70000.0)
    assert meta_b["color"] == gti_indicator.CANDLE_COLOR_BLUE
    assert meta_b["is_blue"] is True

    # 3. Bearish Black
    bear_candle = {"open": 25050.0, "high": 25052.0, "low": 25000.0, "close": 25002.0, "volume": 165000}
    meta_k = gti_indicator.classify_gti_candle(bear_candle, atr_val=25.0, vol_ma=70000.0)
    assert meta_k["color"] == gti_indicator.CANDLE_COLOR_BLACK
    assert meta_k["is_black"] is True


def test_time_window_filter():
    # Morning window
    open_m, msg_m = gti_indicator.check_trading_time_window(datetime.time(10, 0))
    assert open_m is True
    assert "Morning" in msg_m

    # Afternoon window
    open_a, msg_a = gti_indicator.check_trading_time_window(datetime.time(14, 0))
    assert open_a is True
    assert "Afternoon" in msg_a

    # Lunch break
    open_l, msg_l = gti_indicator.check_trading_time_window(datetime.time(12, 30))
    assert open_l is False
    assert "Lunch" in msg_l


def test_atm_option_calculation():
    # NIFTY Call
    opt_call = gti_indicator.calculate_atm_option(25230.0, symbol="NIFTY", signal_type="BUY_CALL")
    assert opt_call["atm_strike"] == 25250
    assert opt_call["option_type"] == "CE"
    assert opt_call["tradingsymbol"] == "NIFTY 25250 CE"
    assert opt_call["option_stop_loss"] < opt_call["estimated_ltp"]
    assert opt_call["option_target"] > opt_call["estimated_ltp"]

    # BANKNIFTY Put
    opt_put = gti_indicator.calculate_atm_option(51480.0, symbol="BANKNIFTY", signal_type="BUY_PUT")
    assert opt_put["atm_strike"] == 51500
    assert opt_put["option_type"] == "PE"
    assert opt_put["tradingsymbol"] == "BANKNIFTY 51500 PE"


def test_gti_scenarios():
    call_sc = gti_indicator.generate_gti_scenario("call", "NIFTY")
    assert call_sc["direction"] == "BUY_CALL"
    assert call_sc["passed"] is True
    assert call_sc["has_recent_yellow"] is True
    assert call_sc["breaks_yellow_high"] is True
    assert call_sc["suggested_option"] is not None

    put_sc = gti_indicator.generate_gti_scenario("put", "NIFTY")
    assert put_sc["direction"] == "BUY_PUT"
    assert put_sc["passed"] is True
    assert put_sc["has_recent_yellow"] is True
    assert put_sc["breaks_yellow_low"] is True
    assert put_sc["suggested_option"] is not None

    yellow_sc = gti_indicator.generate_gti_scenario("yellow", "NIFTY")
    assert yellow_sc["direction"] == "ACCUMULATION"
    assert yellow_sc["current_candle_color"] == gti_indicator.CANDLE_COLOR_YELLOW

    sideways_sc = gti_indicator.generate_gti_scenario("sideways", "NIFTY")
    assert sideways_sc["filter_active"] is True


def test_gti_multi_index_pricing():
    # Test BANKNIFTY scenario base price and ATM option strike
    bn_sc = gti_indicator.generate_gti_scenario("dynamic", "BANKNIFTY")
    assert bn_sc["symbol"] == "BANKNIFTY"
    assert bn_sc["spot_price"] > 55000.0
    assert bn_sc["atr"] > 50.0

    bn_opt = gti_indicator.calculate_atm_option(62040.0, symbol="BANKNIFTY", signal_type="BUY_CALL")
    assert bn_opt["atm_strike"] == 62000

    # Test FINNIFTY scenario base price and ATM option strike
    fn_sc = gti_indicator.generate_gti_scenario("dynamic", "FINNIFTY")
    assert fn_sc["symbol"] == "FINNIFTY"
    assert 25000.0 < fn_sc["spot_price"] < 35000.0

    fn_opt = gti_indicator.calculate_atm_option(28035.0, symbol="FINNIFTY", signal_type="BUY_PUT")
    assert fn_opt["atm_strike"] == 28050
