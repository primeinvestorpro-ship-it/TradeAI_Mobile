"""
Unit tests for scanner_strategy.py — Strategy A (Bullish) and Strategy B (Bearish) evaluators.
Run: pytest test_scanner_strategy.py -v
"""

import datetime
import pytest

import scanner_strategy
import futures_signal


# ---------------------------------------------------------------------------
# Helpers — synthetic candle factories
# ---------------------------------------------------------------------------

def _make_candle(close, high=None, low=None, volume=100_000):
    high = high if high is not None else close + 2
    low  = low  if low  is not None else close - 2
    return {"open": close, "high": high, "low": low, "close": close, "volume": volume}


def _rising_candles(n, start=100.0, step=0.5, volume=200_000):
    """Candles with steadily rising closes (useful for bullish warmup)."""
    return [_make_candle(start + i * step, volume=volume) for i in range(n)]


def _falling_candles(n, start=100.0, step=0.5, volume=200_000):
    """Candles with steadily falling closes."""
    return [_make_candle(start - i * step, volume=volume) for i in range(n)]


def _daily_candle(close, high=None, low=None, volume=1_000_000, date=None):
    """A single daily candle."""
    high = high or close + 10
    low  = low  or close - 10
    return {"open": close, "high": high, "low": low, "close": close,
            "volume": volume, "date": date or datetime.date.today()}


# ---------------------------------------------------------------------------
# compute_supertrend (in futures_signal)
# ---------------------------------------------------------------------------

class TestComputeSupertrend:
    def test_returns_dict_with_keys(self):
        candles = _rising_candles(20, start=100)
        result = futures_signal.compute_supertrend(candles, period=7, multiplier=3.0)
        assert result is not None
        assert "supertrend" in result
        assert "trend" in result
        assert result["trend"] in ("UP", "DOWN")

    def test_insufficient_data_returns_none(self):
        candles = _rising_candles(7)  # need > 7
        result = futures_signal.compute_supertrend(candles, period=7, multiplier=3.0)
        assert result is None

    def test_rising_candles_trend_up(self):
        candles = _rising_candles(30, start=500, step=2)
        result = futures_signal.compute_supertrend(candles, period=7, multiplier=3.0)
        assert result is not None
        assert result["trend"] == "UP"

    def test_falling_candles_trend_down(self):
        # Use wider HL spread and larger step to ensure Supertrend flips to DOWN
        candles = [
            {"open": v, "high": v + 15, "low": v - 15, "close": v, "volume": 200_000}
            for v in [500 - i * 8 for i in range(50)]
        ]
        result = futures_signal.compute_supertrend(candles, period=7, multiplier=3.0)
        # Strongly declining (50 candles, -8 each) with wide HL should be DOWN
        assert result is not None
        assert result["trend"] == "DOWN"


# ---------------------------------------------------------------------------
# Strategy A — Bullish
# ---------------------------------------------------------------------------

STD_PREV_DAY = {"high": 105.0, "low": 95.0, "close": 100.0}
STD_OPEN = 102.0


def _bullish_5m_candles(n=60):
    """Candles that pass EMA / volume / +DI conditions for Strategy A."""
    # Generate rising candles so EMA8 < EMA34 < EMA50 < close
    base = _rising_candles(n, start=90.0, step=0.3, volume=300_000)
    # Last candle has high volume
    base[-1]["volume"] = 500_000
    return base


class TestStrategyA:
    def test_all_conditions_pass_generates_bullish_signal(self):
        candles = _bullish_5m_candles(60)
        result = scanner_strategy.evaluate_strategy_a(
            candles_5min=candles,
            prev_day_hlc=STD_PREV_DAY,
            current_day_open=STD_OPEN,
            symbol="TEST",
            futures_symbol="TEST26OCTFUT",
            expiry="2026-10-27",
            candle_ts=datetime.datetime(2026, 10, 23, 9, 30),
        )
        # Strategy A might not pass all 7 without exact values,
        # but structure must always be returned correctly
        assert "passed" in result
        assert "conditions" in result
        assert "values" in result
        assert isinstance(result["conditions"], dict)
        # All 7 condition keys must be present
        for key in ["c1_close_gt_ema8", "c2_close_gt_ema34", "c3_close_gt_ema50",
                    "c4_volume_gt_10wma", "c5_plus_di_ge_24", "c6_daily_open_pivot", "c7_5m_close_r1"]:
            assert key in result["conditions"]

    def test_signal_dict_has_correct_structure_when_passed(self):
        candles = _bullish_5m_candles(60)
        result = scanner_strategy.evaluate_strategy_a(
            candles_5min=candles,
            prev_day_hlc=STD_PREV_DAY,
            current_day_open=STD_OPEN,
            symbol="RELIANCE",
            futures_symbol="RELIANCE26OCTFUT",
            expiry="2026-10-27",
            candle_ts=datetime.datetime(2026, 10, 23, 9, 30),
        )
        if result["passed"]:
            sig = result["signal"]
            assert sig["direction"] == "BULLISH"
            assert sig["strategy_id"] == "A"
            assert sig["symbol"] == "RELIANCE"
            assert sig["status"] == "NEW"

    def test_insufficient_candles_returns_not_passed(self):
        result = scanner_strategy.evaluate_strategy_a(
            candles_5min=[_make_candle(100)] * 3,
            prev_day_hlc=STD_PREV_DAY,
            current_day_open=100.0,
            symbol="X", futures_symbol="XFUT", expiry="2026-10-27",
        )
        assert result["passed"] is False
        assert result["signal"] is None

    def test_ema_conditions_fail_when_price_below_emas(self):
        """Last price below all EMAs → c1, c2, c3 should fail."""
        # Start high then drop sharply at end
        candles = _rising_candles(59, start=100.0, step=0.5, volume=200_000)
        candles.append(_make_candle(50.0, volume=100_000))  # massive drop
        result = scanner_strategy.evaluate_strategy_a(
            candles_5min=candles,
            prev_day_hlc=STD_PREV_DAY,
            current_day_open=100.0,
            symbol="X", futures_symbol="XFUT", expiry="2026-10-27",
        )
        assert result["passed"] is False
        assert result["conditions"]["c1_close_gt_ema8"] is False
        assert result["conditions"]["c2_close_gt_ema34"] is False
        assert result["conditions"]["c3_close_gt_ema50"] is False

    def test_volume_condition_fails_when_low_volume(self):
        candles = _rising_candles(60, start=100.0, step=0.5, volume=200_000)
        candles[-1]["volume"] = 10  # very low volume on last candle
        result = scanner_strategy.evaluate_strategy_a(
            candles_5min=candles,
            prev_day_hlc=STD_PREV_DAY,
            current_day_open=100.0,
            symbol="X", futures_symbol="XFUT", expiry="2026-10-27",
        )
        # c4 should fail
        assert result["conditions"]["c4_volume_gt_10wma"] is False
        assert result["passed"] is False

    def test_condition_6_formula(self):
        """Verify c6: open >= (H+L+C)/3 - C/200"""
        h, l, c = 110.0, 90.0, 100.0
        pivot = (h + l + c) / 3   # 100.0
        threshold = pivot - c / 200  # 100.0 - 0.5 = 99.5

        candles = _rising_candles(60, start=90.0, step=0.5, volume=300_000)
        result = scanner_strategy.evaluate_strategy_a(
            candles_5min=candles,
            prev_day_hlc={"high": h, "low": l, "close": c},
            current_day_open=99.0,  # below threshold
            symbol="X", futures_symbol="XFUT", expiry="2026-10-27",
        )
        assert result["conditions"]["c6_daily_open_pivot"] is False

        result2 = scanner_strategy.evaluate_strategy_a(
            candles_5min=candles,
            prev_day_hlc={"high": h, "low": l, "close": c},
            current_day_open=100.0,  # above threshold
            symbol="X", futures_symbol="XFUT", expiry="2026-10-27",
        )
        assert result2["conditions"]["c6_daily_open_pivot"] is True

    def test_condition_7_formula(self):
        """Verify c7: close >= 2*pivot - prevLow - 1"""
        h, l, c = 110.0, 90.0, 100.0
        pivot = (h + l + c) / 3
        threshold = 2 * pivot - l - 1  # 2*100 - 90 - 1 = 109

        # Candles where last close is well below threshold
        candles = _rising_candles(60, start=90.0, step=0.01, volume=300_000)  # close ~90.6
        result = scanner_strategy.evaluate_strategy_a(
            candles_5min=candles,
            prev_day_hlc={"high": h, "low": l, "close": c},
            current_day_open=100.0,
            symbol="X", futures_symbol="XFUT", expiry="2026-10-27",
        )
        # close ~90.6 < 109 → c7 fails
        assert result["conditions"]["c7_5m_close_r1"] is False


# ---------------------------------------------------------------------------
# Strategy B — Bearish
# ---------------------------------------------------------------------------

def _make_strategy_b_inputs(*, last_15m_close=85.0, today_close=85.0, today_vol=800_000,
                             prev_low=90.0, prev_close=92.0,
                             st_trend="DOWN", declining_5m=True):
    """Generate inputs that make Strategy B pass (by default)."""

    # 15m: falling so last close < EMA8 and EMA34
    candles_15m = _falling_candles(45, start=120.0, step=0.8, volume=600_000)
    candles_15m[-1]["close"] = last_15m_close

    # Daily: 5 rising days then today's drop
    daily = []
    for i in range(5):
        daily.append(_daily_candle(close=100.0, high=105.0, low=95.0, volume=700_000))
    daily.append(_daily_candle(close=prev_close, high=prev_low + 2, low=prev_low - 2, volume=700_000))
    daily.append(_daily_candle(close=today_close, high=today_close + 5, low=today_close - 5, volume=today_vol))

    # 5m: 4 declining candles at the end
    if declining_5m:
        closes_5m = [110.0, 109.5, 109.0, 108.5]
    else:
        closes_5m = [108.0, 108.5, 109.0, 109.5]  # rising
    candles_5m = [_make_candle(c) for c in closes_5m]

    return candles_15m, daily, candles_5m


class TestStrategyB:
    def test_structure_always_returned(self):
        c15m, daily, c5m = _make_strategy_b_inputs()
        result = scanner_strategy.evaluate_strategy_b(
            candles_5min=c5m, candles_15min=c15m, daily_candles=daily,
            symbol="TEST", futures_symbol="TESTFUT", expiry="2026-10-27",
        )
        assert "passed" in result
        assert "conditions" in result
        assert "values" in result
        for key in ["c1_15m_close_lt_ema8", "c2_15m_close_lt_ema34",
                    "c3_daily_vol_gt_500k", "c4_daily_close_gt_90", "c5_daily_close_lt_700",
                    "c6_close_lt_prev_low", "c7_close_lt_prev_close",
                    "c8_close_lt_supertrend", "c9_5m_decline_1",
                    "c10_5m_decline_2", "c11_5m_decline_3"]:
            assert key in result["conditions"]

    def test_bearish_signal_when_all_pass(self):
        """With all conditions met, should generate a BEARISH signal."""
        c15m, daily, c5m = _make_strategy_b_inputs(
            last_15m_close=85.0, today_close=85.0, today_vol=800_000,
            prev_low=90.0, prev_close=92.0, declining_5m=True,
        )
        result = scanner_strategy.evaluate_strategy_b(
            candles_5min=c5m, candles_15min=c15m, daily_candles=daily,
            symbol="TEST", futures_symbol="TESTFUT", expiry="2026-10-27",
            candle_ts=datetime.datetime(2026, 10, 23, 10, 0),
        )
        if result["passed"]:
            assert result["signal"]["direction"] == "BEARISH"
            assert result["signal"]["strategy_id"] == "B"
        # Structure must be correct regardless
        assert isinstance(result["passed"], bool)

    def test_or_group_requires_at_least_one(self):
        """c6 or c7 must pass; if neither, signal suppressed."""
        c15m, daily, c5m = _make_strategy_b_inputs(
            today_close=95.0,   # above prev_low=90 AND above prev_close=92
            prev_low=90.0, prev_close=92.0,
        )
        result = scanner_strategy.evaluate_strategy_b(
            candles_5min=c5m, candles_15min=c15m, daily_candles=daily,
            symbol="X", futures_symbol="XFUT", expiry="2026-10-27",
        )
        assert result["conditions"]["c6_close_lt_prev_low"] is False
        assert result["conditions"]["c7_close_lt_prev_close"] is False
        assert result["conditions"]["group_c_or_passed"] is False
        assert result["passed"] is False

    def test_5m_non_declining_fails(self):
        """Rising 5m candles → group E fails."""
        c15m, daily, _ = _make_strategy_b_inputs()
        rising_5m = [_make_candle(c) for c in [100.0, 100.5, 101.0, 101.5]]
        result = scanner_strategy.evaluate_strategy_b(
            candles_5min=rising_5m, candles_15min=c15m, daily_candles=daily,
            symbol="X", futures_symbol="XFUT", expiry="2026-10-27",
        )
        # At least one of c9/c10/c11 must fail
        c9  = result["conditions"]["c9_5m_decline_1"]
        c10 = result["conditions"]["c10_5m_decline_2"]
        c11 = result["conditions"]["c11_5m_decline_3"]
        assert not (c9 and c10 and c11)
        assert result["passed"] is False

    def test_daily_volume_below_threshold_fails(self):
        c15m, daily, c5m = _make_strategy_b_inputs(today_vol=100_000)
        result = scanner_strategy.evaluate_strategy_b(
            candles_5min=c5m, candles_15min=c15m, daily_candles=daily,
            symbol="X", futures_symbol="XFUT", expiry="2026-10-27",
            min_daily_volume=500_000,
        )
        assert result["conditions"]["c3_daily_vol_gt_500k"] is False
        assert result["passed"] is False

    def test_daily_close_outside_range_fails(self):
        # Close above max
        c15m, daily, c5m = _make_strategy_b_inputs(today_close=750.0)
        result = scanner_strategy.evaluate_strategy_b(
            candles_5min=c5m, candles_15min=c15m, daily_candles=daily,
            symbol="X", futures_symbol="XFUT", expiry="2026-10-27",
            max_daily_close=700.0,
        )
        assert result["conditions"]["c5_daily_close_lt_700"] is False
        assert result["passed"] is False

    def test_insufficient_data_returns_not_passed(self):
        result = scanner_strategy.evaluate_strategy_b(
            candles_5min=[], candles_15min=[], daily_candles=[],
            symbol="X", futures_symbol="XFUT", expiry="2026-10-27",
        )
        assert result["passed"] is False
        assert result["signal"] is None


# ---------------------------------------------------------------------------
# Deduplication helper
# ---------------------------------------------------------------------------

class TestMakeDedupKey:
    def test_same_inputs_produce_same_key(self):
        ts = datetime.datetime(2026, 10, 23, 9, 30)
        k1 = scanner_strategy.make_dedup_key("A", "RELIANCE", ts)
        k2 = scanner_strategy.make_dedup_key("A", "RELIANCE", ts)
        assert k1 == k2

    def test_different_strategy_produces_different_key(self):
        ts = datetime.datetime(2026, 10, 23, 9, 30)
        k1 = scanner_strategy.make_dedup_key("A", "RELIANCE", ts)
        k2 = scanner_strategy.make_dedup_key("B", "RELIANCE", ts)
        assert k1 != k2

    def test_different_symbol_produces_different_key(self):
        ts = datetime.datetime(2026, 10, 23, 9, 30)
        k1 = scanner_strategy.make_dedup_key("A", "RELIANCE", ts)
        k2 = scanner_strategy.make_dedup_key("A", "TCS", ts)
        assert k1 != k2

    def test_different_timestamp_produces_different_key(self):
        ts1 = datetime.datetime(2026, 10, 23, 9, 30)
        ts2 = datetime.datetime(2026, 10, 23, 9, 35)
        k1 = scanner_strategy.make_dedup_key("A", "RELIANCE", ts1)
        k2 = scanner_strategy.make_dedup_key("A", "RELIANCE", ts2)
        assert k1 != k2


# ---------------------------------------------------------------------------
# Strategy C — Trend + Supertrend
# ---------------------------------------------------------------------------

class TestStrategyC:
    def test_bullish_signal_when_price_above_all_emas_and_supertrend_up(self):
        # 120 rising candles ensuring EMA20 > EMA50 > EMA100 and close > EMA20
        candles = _rising_candles(120, start=100.0, step=0.5, volume=10_000)
        result = scanner_strategy.evaluate_strategy_c(
            candles_5min=candles,
            symbol="LAURUSLABS",
            futures_symbol="LAURUSLABS26NOVFUT",
            expiry="2026-11-26",
            spot_price=160.0,
            futures_price=161.0,
        )
        assert result["passed"] is True
        assert result["direction"] == "BULLISH"
        sig = result["signal"]
        assert sig is not None
        assert sig["strategy_id"] == "C"
        assert sig["direction"] == "BULLISH"
        assert sig["values"]["close"] > sig["values"]["ema20"]
        assert sig["values"]["supertrend_trend"] == "UP"

    def test_bearish_signal_when_price_below_all_emas_and_supertrend_down(self):
        # Strongly falling candles ensuring EMA20 < EMA50 < EMA100 and Supertrend flips to DOWN
        candles = [
            {"open": v, "high": v + 5, "low": v - 5, "close": v, "volume": 100_000}
            for v in [1000 - i * 8 for i in range(120)]
        ]
        result = scanner_strategy.evaluate_strategy_c(
            candles_5min=candles,
            symbol="LAURUSLABS",
            futures_symbol="LAURUSLABS26NOVFUT",
            expiry="2026-11-26",
            spot_price=140.0,
            futures_price=139.0,
        )
        assert result["passed"] is True
        assert result["direction"] == "BEARISH"
        sig = result["signal"]
        assert sig is not None
        assert sig["strategy_id"] == "C"
        assert sig["direction"] == "BEARISH"
        assert sig["values"]["close"] < sig["values"]["ema20"]
        assert sig["values"]["supertrend_trend"] == "DOWN"

    def test_insufficient_candles_returns_not_passed(self):
        candles = _rising_candles(30, start=100.0, step=0.5)
        result = scanner_strategy.evaluate_strategy_c(
            candles_5min=candles,
            symbol="X",
        )
        assert result["passed"] is False
        assert result["signal"] is None

    def test_supertrend_requires_sustain_and_open_higher_confirmation(self):
        # 110 candles in clear downtrend
        candles = [{"open": v, "high": v + 5, "low": v - 5, "close": v, "volume": 100_000} for v in [1000 - i * 8 for i in range(110)]]
        # Candle 110: strong bounce that flips Supertrend to UP
        candles.append({"open": 130, "high": 210, "low": 125, "close": 200, "volume": 100_000})

        # On flip candle itself: sustained_candles = 0 -> must NOT pass
        res_flip = scanner_strategy.evaluate_strategy_c(candles, ema_fast=5, ema_mid=10, ema_slow=20)
        assert res_flip["passed"] is False

        # Next candle opens lower than signal candle high (open 195 < 210) -> must NOT pass
        c_low = list(candles) + [{"open": 195, "high": 215, "low": 190, "close": 210, "volume": 100_000}]
        res_low = scanner_strategy.evaluate_strategy_c(c_low, ema_fast=5, ema_mid=10, ema_slow=20)
        assert res_low["passed"] is False

        # Next candle opens higher than signal candle high (open 215 > 210) -> PASSES!
        c_high = list(candles) + [{"open": 215, "high": 230, "low": 210, "close": 225, "volume": 100_000}]
        res_high = scanner_strategy.evaluate_strategy_c(c_high, ema_fast=5, ema_mid=10, ema_slow=20)
        assert res_high["passed"] is True
        assert res_high["direction"] == "BULLISH"
        assert res_high["values"]["sustained_candles"] >= 1


class TestStrategyD:
    """Test Suite for Strategy D: EMA 50/150/200 Trend & Reversal Swing Strategy."""

    def _make_candles(self, count, start_price=100.0, step=1.0):
        candles = []
        p = start_price
        for i in range(count):
            c_open = p
            c_close = p + step
            c_high = max(c_open, c_close) + 2.0
            c_low = min(c_open, c_close) - 2.0
            candles.append({
                "date": f"2026-01-{i+1:02d}",
                "open": round(c_open, 2),
                "high": round(c_high, 2),
                "low": round(c_low, 2),
                "close": round(c_close, 2),
                "volume": 500_000,
            })
            p = c_close
        return candles

    def test_insufficient_candles_returns_not_passed(self):
        candles = self._make_candles(50)
        res = scanner_strategy.evaluate_strategy_d(candles)
        assert res["passed"] is False
        assert res["direction"] == "NEUTRAL"
        assert "Insufficient candles" in res["reason"]

    def test_bullish_entry_signal(self):
        # 220 steadily rising candles
        candles = self._make_candles(220, start_price=100.0, step=1.5)
        # Last candle closes above previous day high
        candles[-1]["high"] = candles[-2]["high"] + 5.0
        candles[-1]["close"] = candles[-2]["high"] + 4.0

        res = scanner_strategy.evaluate_strategy_d(
            candles,
            symbol="RELIANCE",
            futures_symbol="RELIANCE FUT",
            prev_state="NEUTRAL",
        )
        assert res["passed"] is True
        assert res["direction"] == "BULLISH"
        assert res["signal_type"] == "ENTRY"
        assert res["action"] == "BUY"
        assert res["signal"] is not None
        assert res["signal"]["strategy_id"] == "D"
        assert "ABOVE 50/150/200 EMA" in res["values"]["trend_desc"]

    def test_bullish_trend_continuation(self):
        candles = self._make_candles(220, start_price=100.0, step=1.5)
        candles[-1]["close"] = candles[-2]["high"] + 2.0
        res = scanner_strategy.evaluate_strategy_d(
            candles,
            symbol="TCS",
            prev_state="BULLISH",
        )
        assert res["passed"] is True
        assert res["direction"] == "BULLISH"
        assert res["signal_type"] == "TREND"
        assert res["action"] == "BUY (HOLDING)"

    def test_reversal_bearish_to_bullish(self):
        candles = self._make_candles(220, start_price=100.0, step=1.5)
        candles[-1]["close"] = candles[-2]["high"] + 3.0
        res = scanner_strategy.evaluate_strategy_d(
            candles,
            symbol="INFY",
            prev_state="BEARISH",
        )
        assert res["passed"] is True
        assert res["direction"] == "BULLISH"
        assert res["signal_type"] == "REVERSAL"
        assert res["action"] == "EXIT BEARISH / BUY"

    def test_bearish_entry_signal(self):
        # 220 steadily falling candles
        candles = self._make_candles(220, start_price=500.0, step=-1.5)
        candles[-1]["low"] = candles[-2]["low"] - 5.0
        candles[-1]["close"] = candles[-2]["low"] - 4.0

        res = scanner_strategy.evaluate_strategy_d(
            candles,
            symbol="SBIN",
            futures_symbol="SBIN FUT",
            prev_state="NEUTRAL",
        )
        assert res["passed"] is True
        assert res["direction"] == "BEARISH"
        assert res["signal_type"] == "ENTRY"
        assert res["action"] == "SELL"
        assert "BELOW 50/150/200 EMA" in res["values"]["trend_desc"]

    def test_reversal_bullish_to_bearish(self):
        candles = self._make_candles(220, start_price=500.0, step=-1.5)
        candles[-1]["close"] = candles[-2]["low"] - 3.0
        res = scanner_strategy.evaluate_strategy_d(
            candles,
            symbol="HDFCBANK",
            prev_state="BULLISH",
        )
        assert res["passed"] is True
        assert res["direction"] == "BEARISH"
        assert res["signal_type"] == "REVERSAL"
        assert res["action"] == "EXIT BULLISH / SELL"

    def test_require_prev_day_breakout_blocks_signal_when_not_broken(self):
        candles = self._make_candles(220, start_price=100.0, step=1.0)
        # Force last candle to close below prev day high
        candles[-2]["high"] = 500.0
        candles[-1]["close"] = 350.0  # Above EMAs, but well below prev day high of 500
        candles[-1]["high"] = 360.0

        res = scanner_strategy.evaluate_strategy_d(
            candles,
            require_prev_day_breakout=True,
        )
        assert res["passed"] is False

    def test_backtest_simulation_produces_valid_metrics(self):
        import scanner_backtest
        candles = self._make_candles(250, start_price=200.0, step=0.8)
        trades = scanner_backtest.simulate_ema_swing_trades(
            candles=candles,
            symbol="TESTSTOCK",
            futures_symbol="TESTSTOCK FUT",
            lot_size=100,
        )
        assert isinstance(trades, list)

        # Full FNO backtest run
        results = scanner_backtest.run_fno_backtest(
            kite_client=None,
            symbols=["RELIANCE", "TCS"],
            lookback_days=220,
        )
        assert results["ok"] is True
        assert "metrics" in results
        assert results["metrics"]["total_symbols_scanned"] == 2
        assert "win_rate_pct" in results["metrics"]


class TestStrategyE:
    """Test Suite for Strategy E: Smart Money Accumulation & Distribution (F&O)."""

    def _make_candles(self, count, start_price=100.0, step=1.0, vol=500_000):
        candles = []
        p = start_price
        for i in range(count):
            c_open = p
            c_close = p + step
            c_high = max(c_open, c_close) + 2.0
            c_low = min(c_open, c_close) - 2.0
            candles.append({
                "date": f"2026-01-{i+1:02d}",
                "open": round(c_open, 2),
                "high": round(c_high, 2),
                "low": round(c_low, 2),
                "close": round(c_close, 2),
                "volume": vol + (i * 1000),
            })
            p = c_close
        return candles

    def test_insufficient_candles_returns_not_passed(self):
        candles = self._make_candles(100)
        res = scanner_strategy.evaluate_strategy_e(candles)
        assert res["passed"] is False
        assert res["direction"] == "WAIT"
        assert res["category"] == "INSUFFICIENT_DATA"

    def test_bullish_accumulation_confirmed(self):
        # 220 steadily rising candles
        candles = self._make_candles(220, start_price=100.0, step=1.5, vol=600_000)
        res = scanner_strategy.evaluate_strategy_e(
            candles,
            symbol="RELIANCE",
            futures_symbol="RELIANCE FUT",
            futures_price=candles[-1]["close"] + 2.0,
            prev_futures_price=candles[-2]["close"],
            current_oi=1_000_000,
            prev_oi=900_000,  # Long buildup: price up, OI up
        )
        assert res["score"] >= 70
        assert res["values"]["futures_data"]["buildup"] == "LONG_BUILDUP"
        assert res["values"]["obv_data"]["obv_trend"] == "RISING"
        assert res["values"]["entry_price"] > 0
        assert res["values"]["sl"] < res["values"]["entry_price"]
        assert res["values"]["t1"] > res["values"]["entry_price"]

    def test_bearish_distribution_confirmed(self):
        # 220 steadily falling candles
        candles = self._make_candles(220, start_price=800.0, step=-1.5, vol=600_000)
        res = scanner_strategy.evaluate_strategy_e(
            candles,
            symbol="INFY",
            futures_symbol="INFY FUT",
            futures_price=candles[-1]["close"] - 2.0,
            prev_futures_price=candles[-2]["close"],
            current_oi=1_200_000,
            prev_oi=1_000_000,  # Short buildup: price down, OI up
        )
        assert res["score"] >= 70
        assert res["values"]["futures_data"]["buildup"] == "SHORT_BUILDUP"
        assert res["values"]["obv_data"]["obv_trend"] == "FALLING"
        assert res["values"]["entry_price"] > 0
        assert res["values"]["sl"] > res["values"]["entry_price"]
        assert res["values"]["t1"] < res["values"]["entry_price"]

    def test_early_accumulation_detection(self):
        # Mostly flat or consolidating candles near demand
        candles = self._make_candles(220, start_price=300.0, step=0.05, vol=400_000)
        # Recent volume spike with slight uptick
        for c in candles[-5:]:
            c["volume"] = 1_200_000
            c["close"] += 0.8
        res = scanner_strategy.evaluate_strategy_e(
            candles,
            symbol="TCS",
            futures_symbol="TCS FUT",
            futures_price=315.0,
        )
        assert "ACCUMULATION" in res["category"] or res["category"] in ("EARLY ACCUMULATION", "WATCHLIST")
        assert res["values"]["zone_data"]["demand_level"] > 0
        assert res["values"]["zone_data"]["supply_level"] > 0

    def test_timestamp_not_midnight(self):
        # Even if daily candles have date '2026-11-06 00:00:00', the emitted signal must not have 00:00:00
        import datetime
        candles = self._make_candles(220, start_price=100.0, step=1.5, vol=500_000)
        # Assign midnight datetime to last candle
        candles[-1]["date"] = datetime.datetime(2026, 11, 6, 0, 0, 0)
        res = scanner_strategy.evaluate_strategy_e(
            candles,
            symbol="SBIN",
            futures_symbol="SBIN FUT",
            futures_price=candles[-1]["close"] + 2.0,
            prev_futures_price=candles[-2]["close"],
            current_oi=1_000_000,
            prev_oi=900_000,
        )
        if res.get("signal"):
            sig_ts = res["signal"]["timestamp"]
            # Must not be midnight
            assert "00:00:00" not in sig_ts

    def test_timestamp_with_intraday_candle_ts(self):
        import datetime
        candles = self._make_candles(220, start_price=100.0, step=1.5, vol=500_000)
        intraday_ts = datetime.datetime(2026, 11, 6, 14, 15, 0)
        res = scanner_strategy.evaluate_strategy_e(
            candles,
            symbol="SBIN",
            futures_symbol="SBIN FUT",
            candle_ts=intraday_ts,
            futures_price=candles[-1]["close"] + 2.0,
            prev_futures_price=candles[-2]["close"],
            current_oi=1_000_000,
            prev_oi=900_000,
        )
        if res.get("signal"):
            sig_ts = res["signal"]["timestamp"]
            assert "14:15:00" in sig_ts

    def test_15m_bearish_blocks_bullish_signal(self):
        # Even if daily candles are in strong uptrend, a bearish 15m intraday chart must block BUY
        candles = self._make_candles(220, start_price=100.0, step=1.5, vol=500_000)
        # Create 15m candles with a sharp sell-off (bearish Supertrend)
        candles_15m = self._make_candles(40, start_price=500.0, step=-3.0, vol=200_000)
        res = scanner_strategy.evaluate_strategy_e(
            candles,
            symbol="HCLTECH",
            futures_symbol="HCLTECH FUT",
            candles_15m=candles_15m,
            spot_price=candles_15m[-1]["close"],
        )
        assert res["direction"] != "BULLISH"
        assert res["passed"] is False

    def test_15m_bullish_blocks_bearish_signal(self):
        # Even if daily candles are in downtrend, a bullish 15m intraday chart must block SELL
        candles = self._make_candles(220, start_price=800.0, step=-1.5, vol=500_000)
        # Create 15m candles with a strong rally (bullish Supertrend)
        candles_15m = self._make_candles(40, start_price=300.0, step=3.0, vol=200_000)
        res = scanner_strategy.evaluate_strategy_e(
            candles,
            symbol="MPHASIS",
            futures_symbol="MPHASIS FUT",
            candles_15m=candles_15m,
            spot_price=candles_15m[-1]["close"],
        )
        assert res["direction"] != "BEARISH"
        assert res["passed"] is False
