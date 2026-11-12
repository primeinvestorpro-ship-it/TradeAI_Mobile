"""
Unit tests for futures_signal.py — pure-Python, no Kite/AI dependency.
Run: pytest test_futures_signal.py -v
"""

import pytest
import futures_signal as fs


# ── VWAP ────────────────────────────────────────────────────────────────────

class TestComputeVwapFromCandles:
    def test_basic(self):
        candles = [
            {"high": 110, "low": 90, "close": 100, "volume": 1000},
            {"high": 115, "low": 95, "close": 110, "volume": 2000},
        ]
        vwap = fs.compute_vwap_from_candles(candles)
        # TP1 = (110+90+100)/3 = 100, TP2 = (115+95+110)/3 = 106.67
        # VWAP = (100*1000 + 106.67*2000) / 3000 ≈ 104.44
        assert abs(vwap - 104.44) < 0.2

    def test_zero_volume_raises(self):
        candles = [{"high": 110, "low": 90, "close": 100, "volume": 0}]
        with pytest.raises(ValueError):
            fs.compute_vwap_from_candles(candles)


class TestComputeVwapBias:
    def test_bullish(self):
        r = fs.compute_vwap_bias(105, 100)
        assert r["vwap_bias"] == "BULLISH"

    def test_bearish(self):
        r = fs.compute_vwap_bias(95, 100)
        assert r["vwap_bias"] == "BEARISH"

    def test_neutral_within_band(self):
        r = fs.compute_vwap_bias(100.02, 100.0, neutral_band_pct=0.001)
        assert r["vwap_bias"] == "NEUTRAL"

    def test_none_vwap(self):
        r = fs.compute_vwap_bias(100, None)
        assert r["vwap_bias"] == "NEUTRAL"


# ── ORB ─────────────────────────────────────────────────────────────────────

class TestComputeOrbBias:
    def test_above_high(self):
        assert fs.compute_orb_bias(110, 105, 95) == "BULLISH"

    def test_below_low(self):
        assert fs.compute_orb_bias(90, 105, 95) == "BEARISH"

    def test_inside_range(self):
        assert fs.compute_orb_bias(100, 105, 95) == "NEUTRAL"

    def test_none_levels(self):
        assert fs.compute_orb_bias(100, None, None) == "NEUTRAL"


# ── Gap ─────────────────────────────────────────────────────────────────────

class TestComputeGapFlag:
    def _ranges(self):
        return [100, 120, 90, 110, 100]  # avg = 104

    def test_small_gap(self):
        r = fs.compute_gap_flag(self._ranges(), today_open=2105, yesterday_close=2100, gap_threshold_factor=0.5)
        assert r["large_gap"] is False
        assert abs(r["gap"] - 5) < 0.01

    def test_large_gap(self):
        # avg_range = 104, threshold_factor=0.5 → threshold=52; gap of 60 should be large
        r = fs.compute_gap_flag(self._ranges(), today_open=2160, yesterday_close=2100, gap_threshold_factor=0.5)
        assert r["large_gap"] is True

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            fs.compute_gap_flag([], today_open=100, yesterday_close=95, gap_threshold_factor=0.5)


# ── Momentum ─────────────────────────────────────────────────────────────────

class TestComputeMomentumBias:
    def test_bullish_in_band(self):
        r = fs.compute_momentum_bias(30, 100, min_fraction=0.25, max_fraction=0.65)
        assert r["momentum_bias"] == "BULLISH"
        assert abs(r["used_fraction"] - 0.3) < 0.001

    def test_bearish_in_band(self):
        r = fs.compute_momentum_bias(-50, 100, min_fraction=0.25, max_fraction=0.65)
        assert r["momentum_bias"] == "BEARISH"

    def test_neutral_below_min(self):
        r = fs.compute_momentum_bias(10, 100)
        assert r["momentum_bias"] == "NEUTRAL"

    def test_neutral_above_max(self):
        # Move > 65% of expected → exhausted, neutral
        r = fs.compute_momentum_bias(70, 100)
        assert r["momentum_bias"] == "NEUTRAL"

    def test_none_expected_move(self):
        r = fs.compute_momentum_bias(50, None)
        assert r["momentum_bias"] == "NEUTRAL"
        assert r["used_fraction"] is None


# ── ATR ──────────────────────────────────────────────────────────────────────

class TestComputeAtrExpectedMove:
    def _candles(self):
        return [
            {"high": 110, "low": 90,  "close": 100, "open": 95},
            {"high": 115, "low": 95,  "close": 112, "open": 101},
            {"high": 112, "low": 100, "close": 105, "open": 113},
        ]

    def test_returns_float(self):
        result = fs.compute_atr_expected_move(self._candles(), lookback=2)
        assert isinstance(result, float)
        assert result > 0

    def test_insufficient_data(self):
        assert fs.compute_atr_expected_move([{"high": 100, "low": 90, "close": 95}]) is None


# ── Bias combiner ─────────────────────────────────────────────────────────────

class TestCombineBias:
    def test_majority_bullish(self):
        assert fs.combine_bias("BULLISH", "BULLISH", "BEARISH") == "BULLISH"

    def test_majority_bearish(self):
        assert fs.combine_bias("BEARISH", "BEARISH", "NEUTRAL") == "BEARISH"

    def test_no_majority(self):
        assert fs.combine_bias("BULLISH", "BEARISH", "NEUTRAL") == "NEUTRAL"

    def test_all_neutral(self):
        assert fs.combine_bias("NEUTRAL", "NEUTRAL", "NEUTRAL") == "NEUTRAL"


# ── EMA / RSI ─────────────────────────────────────────────────────────────────

class TestComputeEma:
    def test_basic(self):
        prices = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
        ema = fs.compute_ema(prices, period=5)
        assert ema is not None and 15 < ema < 20

    def test_insufficient_data(self):
        assert fs.compute_ema([10, 11], period=5) is None


class TestComputeRsi:
    def test_all_gains_returns_100(self):
        prices = [100 + i for i in range(20)]
        rsi = fs.compute_rsi(prices, period=14)
        assert rsi == 100.0

    def test_insufficient_data(self):
        assert fs.compute_rsi([100, 101], period=14) is None

    def test_typical_value(self):
        import random
        random.seed(42)
        prices = [100]
        for _ in range(20):
            prices.append(prices[-1] + random.uniform(-2, 2))
        rsi = fs.compute_rsi(prices)
        assert 0 <= rsi <= 100


# ── WMA & Chartink Setup Tests ───────────────────────────────────────────────

class TestComputeWma:
    def test_basic_wma(self):
        # values: [1, 2, 3] -> (1*1 + 2*2 + 3*3) / (1+2+3) = (1+4+9)/6 = 14/6 ≈ 2.333
        assert abs(fs.compute_wma([1, 2, 3], period=3) - 2.333) < 0.01

    def test_insufficient_data(self):
        assert fs.compute_wma([1, 2], period=5) is None


class TestEvaluateChartinkAdxSetup:
    def test_insufficient_candles(self):
        res = fs.evaluate_chartink_adx_setup([{"close": 100}])
        assert res["status"] == "UNKNOWN"

    def test_strong_uptrend_candles(self):
        # 25 increasing candles with volume
        candles = []
        price = 100.0
        for i in range(30):
            price += 1.0
            candles.append({
                "open": price - 0.5,
                "high": price + 0.8,
                "low": price - 0.6,
                "close": price,
                "volume": 1000 + i * 200,
            })
        res = fs.evaluate_chartink_adx_setup(candles)
        assert res["status"] in ("PASSED", "PARTIAL")
        assert res["values"]["close"] == price
        assert res["values"]["ema8"] is not None

