import datetime
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest
from sustained_breakout_strategy import (
    compute_avwap,
    compute_volume_ma,
    detect_support_resistance,
    check_sustained_breakout,
    check_sustained_breakdown,
    analyze_options_chain_confluence,
    calculate_confluence_score,
    evaluate_sustained_breakout_strategy,
    STRATEGY_F_ID,
    STRATEGY_F_NAME,
)
import scanner_strategy


def _build_breakout_candles(mode="BULLISH", n=60):
    """
    Builds 60 candles where candles 0..57 form a consolidation band,
    candle 58 breaks out / down, and candle 59 sustains with high volume.
    """
    candles = []
    start_time = datetime.datetime(2026, 11, 10, 9, 15)
    base = 24500.0

    # Consolidation band: 24490 to 24510 (Resistance ~24510, Support ~24490)
    for i in range(n - 2):
        if mode == "BULLISH":
            # Gentle uptrend to align EMA 10 > 21 > 50
            p = base + (i * 0.5)
        else:
            # Gentle downtrend to align EMA 10 < 21 < 50
            p = base - (i * 0.5)

        candles.append({
            "date": start_time + datetime.timedelta(minutes=5 * i),
            "open": round(p - 1.0, 2),
            "high": round(p + 2.0, 2),
            "low": round(p - 2.0, 2),
            "close": round(p, 2),
            "volume": 50000,
        })

    res, sup, _, _ = detect_support_resistance(candles, lookback=25)

    if mode == "BULLISH":
        # Breakout candle: closes above resistance
        c58_close = round(res + 10.0, 2)
        candles.append({
            "date": start_time + datetime.timedelta(minutes=5 * (n - 2)),
            "open": round(res - 1.0, 2),
            "high": round(c58_close + 2.0, 2),
            "low": round(res - 1.0, 2),
            "close": c58_close,
            "volume": 120000,  # > 2x volume expansion
        })
        # Sustained candle: holds above breakout level
        c59_close = round(c58_close + 4.0, 2)
        candles.append({
            "date": start_time + datetime.timedelta(minutes=5 * (n - 1)),
            "open": c58_close,
            "high": round(c59_close + 2.0, 2),
            "low": round(c58_close - 1.0, 2),
            "close": c59_close,
            "volume": 130000,
        })
    else:
        # Breakdown candle: closes below support
        c58_close = round(sup - 10.0, 2)
        candles.append({
            "date": start_time + datetime.timedelta(minutes=5 * (n - 2)),
            "open": round(sup + 1.0, 2),
            "high": round(sup + 1.0, 2),
            "low": round(c58_close - 2.0, 2),
            "close": c58_close,
            "volume": 120000,
        })
        # Sustained breakdown candle: holds below support
        c59_close = round(c58_close - 4.0, 2)
        candles.append({
            "date": start_time + datetime.timedelta(minutes=5 * (n - 1)),
            "open": c58_close,
            "high": round(c58_close + 1.0, 2),
            "low": round(c59_close - 2.0, 2),
            "close": c59_close,
            "volume": 130000,
        })

    return candles


def test_compute_avwap():
    candles = [
        {"high": 100, "low": 90, "close": 95, "volume": 1000},
        {"high": 105, "low": 95, "close": 100, "volume": 2000},
    ]
    avwap = compute_avwap(candles, anchor_idx=0)
    assert len(avwap) == 2
    assert avwap[0] == 95.0
    assert avwap[1] == 98.33


def test_compute_volume_ma():
    candles = [{"volume": 100} for _ in range(25)]
    vma = compute_volume_ma(candles, period=20)
    assert len(vma) == 25
    assert vma[-1] == 100.0


def test_detect_support_resistance():
    candles = _build_breakout_candles(mode="BULLISH", n=60)
    res, sup, _, _ = detect_support_resistance(candles, lookback=20)
    assert res >= sup
    assert res > 0
    assert sup > 0


def test_safety_guard_rejects_zero_volume_or_oi():
    """Golden Safety Rule: Never confirm a signal if volume or OI is missing or 0."""
    candles = _build_breakout_candles(mode="BULLISH", n=60)
    # 1. Zero volume in current candle
    candles_zero_vol = [dict(c) for c in candles]
    candles_zero_vol[-1]["volume"] = 0

    res = evaluate_sustained_breakout_strategy(
        candles_5m=candles_zero_vol,
        symbol="NIFTY",
        spot_price=candles[-1]["close"],
        futures_price=candles[-1]["close"] + 15.0,
        futures_volume=0,
        current_oi=1000000,
        prev_oi=950000,
    )
    assert res["passed"] is False
    assert res["direction"] == "NO TRADE"
    assert "Volume Not Available" in res["reasons_against"][0]

    # 2. Zero / missing OI
    res_no_oi = evaluate_sustained_breakout_strategy(
        candles_5m=candles,
        symbol="NIFTY",
        spot_price=candles[-1]["close"],
        futures_price=candles[-1]["close"] + 15.0,
        futures_volume=100000,
        current_oi=0,  # Zero OI
        prev_oi=0,
    )
    assert res_no_oi["passed"] is False
    assert res_no_oi["direction"] == "NO TRADE"


def test_options_chain_confluence_bullish():
    options_chain = [
        {"strike": 24500, "ce_oi": 200000, "pe_oi": 500000, "ce_change_oi": -10000, "pe_change_oi": 80000},
    ]
    res_opt = analyze_options_chain_confluence(options_chain, spot_price=24500.0, direction="LONG")
    assert res_opt["pcr"] == 2.5
    assert res_opt["bias"] == "BULLISH_CONFIRMED"


def test_long_setup_confirmed():
    candles = _build_breakout_candles(mode="BULLISH", n=60)
    spot_p = candles[-1]["close"]
    options_chain = [
        {"strike": spot_p, "ce_oi": 200000, "pe_oi": 600000, "ce_change_oi": -10000, "pe_change_oi": 90000},
    ]
    res = evaluate_sustained_breakout_strategy(
        candles_5m=candles,
        symbol="NIFTY",
        spot_price=spot_p,
        futures_symbol="NIFTY26NOVFUT",
        futures_price=spot_p + 15.0,
        prev_futures_price=spot_p + 5.0,
        futures_volume=180000,
        current_oi=1200000,
        prev_oi=1100000,
        options_chain=options_chain,
    )
    assert res["direction"] == "LONG"
    assert res["score"] >= 75
    assert res["passed"] is True
    assert res["signal"] is not None
    assert res["signal"]["strategy_id"] == STRATEGY_F_ID
    assert res["signal"]["strategy_name"] == STRATEGY_F_NAME
    assert res["values"]["target1"] > res["values"]["entry_price"]
    assert res["values"]["stop_loss"] < res["values"]["entry_price"]


def test_short_setup_confirmed():
    candles = _build_breakout_candles(mode="BEARISH", n=60)
    spot_p = candles[-1]["close"]
    options_chain = [
        {"strike": spot_p, "ce_oi": 700000, "pe_oi": 200000, "ce_change_oi": 80000, "pe_change_oi": -10000},
    ]
    res = evaluate_sustained_breakout_strategy(
        candles_5m=candles,
        symbol="NIFTY",
        spot_price=spot_p,
        futures_symbol="NIFTY26NOVFUT",
        futures_price=spot_p - 15.0,
        prev_futures_price=spot_p - 5.0,
        futures_volume=180000,
        current_oi=1200000,
        prev_oi=1100000,
        options_chain=options_chain,
    )
    assert res["direction"] == "SHORT"
    assert res["score"] >= 75
    assert res["passed"] is True
    assert res["signal"] is not None
    assert res["values"]["target1"] < res["values"]["entry_price"]
    assert res["values"]["stop_loss"] > res["values"]["entry_price"]


def test_scanner_strategy_wrapper_f():
    candles = _build_breakout_candles(mode="BULLISH", n=60)
    res = scanner_strategy.evaluate_strategy_f(
        candles_5m=candles,
        symbol="NIFTY",
        spot_price=candles[-1]["close"],
        futures_volume=120000,
        current_oi=1000000,
    )
    assert "score" in res
    assert "values" in res
    assert res["values"]["spot_price"] == candles[-1]["close"]


def test_dynamic_score_varies_with_price_action():
    """Verify that score is truly dynamic and varies continuously with spot price movements."""
    from sustained_breakout_strategy import generate_strategy_f_scenario
    # Test ticks across channel: -15, 0, +15
    d1 = generate_strategy_f_scenario("dynamic", "NIFTY", tick_offset=-15.0)
    d2 = generate_strategy_f_scenario("dynamic", "NIFTY", tick_offset=0.0)
    d3 = generate_strategy_f_scenario("dynamic", "NIFTY", tick_offset=15.0)

    score1 = d1["trading_signal"]["confidence_score"]
    score2 = d2["trading_signal"]["confidence_score"]
    score3 = d3["trading_signal"]["confidence_score"]

    # Scores must NOT be identical/frozen
    assert len({score1, score2, score3}) > 1
    assert score3 > score1, f"Expected score to increase with bullish price action: {score1} vs {score3}"


def test_options_chain_separate_contract_format_pcr():
    """Verify separate CE/PE contract objects calculate accurate PCR and don't default to 1.00."""
    chain = [
        {"strike": 25200, "instrument_type": "CE", "oi": 200000, "change_oi": -10000},
        {"strike": 25200, "instrument_type": "PE", "oi": 400000, "change_oi": 50000},
    ]
    res_long = analyze_options_chain_confluence(chain, spot_price=25200.0, direction="LONG")
    assert res_long["pcr"] == 2.0
    assert res_long["bias"] == "BULLISH_CONFIRMED"
    assert res_long["call_oi_total"] == 200000
    assert res_long["put_oi_total"] == 400000
    assert res_long["score"] == 10

    # Opposite test: Heavy CE writing
    chain_bear = [
        {"strike": 25200, "instrument_type": "CE", "oi": 500000, "change_oi": 60000},
        {"strike": 25200, "instrument_type": "PE", "oi": 250000, "change_oi": -5000},
    ]
    res_short = analyze_options_chain_confluence(chain_bear, spot_price=25200.0, direction="SHORT")
    assert res_short["pcr"] == 0.5
    assert res_short["bias"] == "BEARISH_CONFIRMED"
    assert res_short["score"] == 10


def test_scenario_generator_all_cases():
    """Exhaustively test all Strategy F market scenarios."""
    from sustained_breakout_strategy import generate_strategy_f_scenario

    # Scenario 1: Bullish Breakout
    bull = generate_strategy_f_scenario("bullish", "NIFTY")
    assert bull["trading_signal"]["direction"] == "LONG"
    assert bull["trading_signal"]["confidence_score"] >= 75
    assert bull["trading_signal"]["passed"] is True
    assert bull["derivatives"]["pcr"] >= 1.2

    # Scenario 2: Bearish Breakdown
    bear = generate_strategy_f_scenario("bearish", "NIFTY")
    assert bear["trading_signal"]["direction"] == "SHORT"
    assert bear["trading_signal"]["confidence_score"] >= 75
    assert bear["trading_signal"]["passed"] is True
    assert bear["derivatives"]["pcr"] <= 0.8

    # Scenario 3: Neutral Rangebound
    neutral = generate_strategy_f_scenario("neutral", "NIFTY", tick_offset=0.0)
    assert neutral["trading_signal"]["direction"] == "NO TRADE"
    assert 35 <= neutral["trading_signal"]["confidence_score"] <= 65

    # Scenario 4: Safety Rule Invalidation
    safety = generate_strategy_f_scenario("safety", "NIFTY")
    assert safety["trading_signal"]["passed"] is False
    assert safety["trading_signal"]["direction"] == "NO TRADE"
    assert any("strictly invalidated" in r for r in safety["trading_signal"]["reasons_against"])


def test_api_endpoint_scenario_support():
    """Test app route handles ?scenario= and ?symbol= correctly."""
    import app
    client = app.app.test_client()

    # Test bullish scenario
    res_bull = client.get("/api/scanner/sustained_breakout?scenario=bullish&symbol=BANKNIFTY")
    data_bull = res_bull.get_json()
    assert data_bull["ok"] is True
    assert data_bull["data"]["trading_signal"]["direction"] == "LONG"
    assert data_bull["data"]["market_trend"]["symbol"] == "BANKNIFTY"

    # Test dynamic ticking scenario
    res_dyn = client.get("/api/scanner/sustained_breakout?scenario=dynamic&tick=12.5")
    data_dyn = res_dyn.get_json()
    assert data_dyn["ok"] is True
    assert data_dyn["data"]["trading_signal"]["confidence_score"] > 0
