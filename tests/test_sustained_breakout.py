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
