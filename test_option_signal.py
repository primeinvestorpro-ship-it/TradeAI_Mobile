import pytest
import option_signal


def test_get_strike_interval():
    assert option_signal.get_strike_interval([2400, 2420, 2440, 2460]) == 20
    assert option_signal.get_strike_interval([22000, 22050, 22100]) == 50
    assert option_signal.get_strike_interval([1500, 1510, 1520]) == 10

    with pytest.raises(ValueError):
        option_signal.get_strike_interval([2400])

    with pytest.raises(ValueError):
        option_signal.get_strike_interval([2400, 2400])


def test_get_atm_strike():
    assert option_signal.get_atm_strike(2458.0, 20.0) == 2460.0
    assert option_signal.get_atm_strike(2449.0, 20.0) == 2440.0
    assert option_signal.get_atm_strike(22324.0, 50.0) == 22300.0
    assert option_signal.get_atm_strike(22326.0, 50.0) == 22350.0

    with pytest.raises(ValueError):
        option_signal.get_atm_strike(2450.0, 0)


def test_get_strikes_around_atm():
    strikes = option_signal.get_strikes_around_atm(2400.0, 20.0, n=5)
    assert len(strikes) == 11
    assert strikes[0] == 2300.0
    assert strikes[5] == 2400.0  # ATM
    assert strikes[10] == 2500.0


def test_compute_oi_bias():
    data = {
        2400: {"CE": 100000, "PE": 500000},  # Heavy put writing
        2420: {"CE": 100000, "PE": 400000},
        2440: {"CE": 100000, "PE": 300000},
    }
    res = option_signal.compute_oi_bias(data, pcr_threshold=0.15)
    assert res["pcr"] == 4.0
    assert res["oi_bias"] == "BULLISH"

    # Bearish case (heavy call writing)
    data_bear = {
        2400: {"CE": 600000, "PE": 100000},
        2420: {"CE": 500000, "PE": 100000},
    }
    res_bear = option_signal.compute_oi_bias(data_bear, pcr_threshold=0.15)
    assert res_bear["oi_bias"] == "BEARISH"

    # Neutral case
    data_neut = {
        2400: {"CE": 100000, "PE": 105000},
    }
    res_neut = option_signal.compute_oi_bias(data_neut, pcr_threshold=0.15)
    assert res_neut["oi_bias"] == "NEUTRAL"


def test_compute_oi_walls():
    atm = 2400.0
    data = {
        2360: {"CE": 10000, "PE": 50000},
        2380: {"CE": 15000, "PE": 120000},  # Highest put below ATM -> Support
        2400: {"CE": 80000, "PE": 80000},   # ATM
        2420: {"CE": 60000, "PE": 20000},
        2440: {"CE": 140000, "PE": 10000},  # Highest call above ATM -> Resistance
        2460: {"CE": 90000, "PE": 5000},
    }
    walls = option_signal.compute_oi_walls(data, atm)
    assert walls["resistance_strike"] == 2440.0
    assert walls["resistance_oi"] == 140000
    assert walls["support_strike"] == 2380.0
    assert walls["support_oi"] == 120000


def test_compute_direction_probability():
    # Equal walls with no proximity skew
    prob = option_signal.compute_direction_probability(100000, 100000)
    assert prob["upside_probability"] == 0.5
    assert prob["downside_probability"] == 0.5

    # Heavier support -> higher upside probability
    prob2 = option_signal.compute_direction_probability(
        support_oi=200000,
        resistance_oi=100000,
        support_strike=2380.0,
        resistance_strike=2420.0,
        spot_price=2400.0,
        strike_interval=20.0,
    )
    assert prob2["upside_probability"] > 0.60
    assert prob2["downside_probability"] < 0.40


def test_compute_atm_oi_change_bias():
    # Faster PE build up -> Bullish
    assert option_signal.compute_atm_oi_change_bias(1.2, 1.6, threshold=0.1) == "BULLISH"
    # Faster CE build up -> Bearish
    assert option_signal.compute_atm_oi_change_bias(1.8, 1.2, threshold=0.1) == "BEARISH"
    # Close build up -> Neutral
    assert option_signal.compute_atm_oi_change_bias(1.25, 1.28, threshold=0.1) == "NEUTRAL"
    # Missing data -> Neutral
    assert option_signal.compute_atm_oi_change_bias(None, 1.5) == "NEUTRAL"


def test_compute_premiums():
    res = option_signal.compute_premiums(ce_ltp=40.0, pe_ltp=30.0, profit_margin_factor=1.15)
    assert res["ce_premium"] == 40.0
    assert res["pe_premium"] == 30.0
    assert res["net_premium"] == 70.0
    assert res["required_move_call"] == pytest.approx(46.0)
    assert res["required_move_put"] == pytest.approx(34.5)
    assert res["required_move_both"] == pytest.approx(80.5)


def test_compute_vix_expected_move():
    move = option_signal.compute_vix_expected_move(22000.0, 15.0)
    assert move > 0
    with pytest.raises(ValueError):
        option_signal.compute_vix_expected_move(-100, 15.0)
