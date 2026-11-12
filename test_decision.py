"""
Unit tests for decision.py — pure, no Kite/AI/network dependency.
Run: pytest test_decision.py -v
"""

import pytest
import decision


class TestDecideDirection:

    # ── LONG cases ──────────────────────────────────────────────────────────

    def test_high_score_positive_sentiment_long(self):
        assert decision.decide_direction(80.0, "POSITIVE") == "LONG"

    def test_high_score_neutral_sentiment_long(self):
        assert decision.decide_direction(75.0, "NEUTRAL") == "LONG"

    def test_high_score_exactly_at_threshold_long(self):
        assert decision.decide_direction(70.0, "NEUTRAL") == "LONG"

    # ── NOT LONG because of negative sentiment ───────────────────────────────

    def test_high_score_negative_sentiment_no_trade(self):
        assert decision.decide_direction(80.0, "NEGATIVE") == "NO_TRADE"

    def test_borderline_high_negative_sentiment(self):
        assert decision.decide_direction(70.0, "NEGATIVE") == "NO_TRADE"

    # ── SHORT cases ──────────────────────────────────────────────────────────

    def test_low_score_negative_sentiment_short(self):
        assert decision.decide_direction(20.0, "NEGATIVE") == "SHORT"

    def test_low_score_neutral_sentiment_short(self):
        assert decision.decide_direction(25.0, "NEUTRAL") == "SHORT"

    def test_low_score_exactly_at_threshold_short(self):
        assert decision.decide_direction(30.0, "NEUTRAL") == "SHORT"

    # ── NOT SHORT because of positive sentiment ──────────────────────────────

    def test_low_score_positive_sentiment_no_trade(self):
        assert decision.decide_direction(20.0, "POSITIVE") == "NO_TRADE"

    # ── Middle band → NO_TRADE ───────────────────────────────────────────────

    def test_middle_score_no_trade(self):
        assert decision.decide_direction(50.0, "NEUTRAL") == "NO_TRADE"

    def test_just_above_sell_threshold_no_trade(self):
        assert decision.decide_direction(31.0, "NEGATIVE") == "NO_TRADE"

    def test_just_below_buy_threshold_no_trade(self):
        assert decision.decide_direction(69.0, "POSITIVE") == "NO_TRADE"

    # ── range_too_tight always wins ─────────────────────────────────────────

    def test_range_too_tight_overrides_high_score(self):
        assert decision.decide_direction(90.0, "POSITIVE", range_too_tight=True) == "NO_TRADE"

    def test_range_too_tight_overrides_low_score(self):
        assert decision.decide_direction(10.0, "NEGATIVE", range_too_tight=True) == "NO_TRADE"

    def test_range_too_tight_overrides_neutral_middle(self):
        assert decision.decide_direction(50.0, "NEUTRAL", range_too_tight=True) == "NO_TRADE"

    # ── Custom thresholds ────────────────────────────────────────────────────

    def test_custom_buy_threshold(self):
        assert decision.decide_direction(80.0, "POSITIVE", buy_threshold=85.0) == "NO_TRADE"

    def test_custom_sell_threshold(self):
        assert decision.decide_direction(40.0, "NEGATIVE", sell_threshold=45.0) == "SHORT"

    # ── Input validation ─────────────────────────────────────────────────────

    def test_invalid_sentiment_raises(self):
        with pytest.raises(ValueError):
            decision.decide_direction(70.0, "VERY_BULLISH")

    def test_score_out_of_range_raises(self):
        with pytest.raises(ValueError):
            decision.decide_direction(150.0, "NEUTRAL")

    def test_negative_score_raises(self):
        with pytest.raises(ValueError):
            decision.decide_direction(-5.0, "NEUTRAL")

    def test_invalid_thresholds_raises(self):
        with pytest.raises(ValueError):
            decision.decide_direction(50.0, "NEUTRAL", buy_threshold=30.0, sell_threshold=70.0)

    # ── Boundary values ──────────────────────────────────────────────────────

    def test_score_zero(self):
        assert decision.decide_direction(0.0, "NEGATIVE") == "SHORT"

    def test_score_100(self):
        assert decision.decide_direction(100.0, "POSITIVE") == "LONG"

    def test_score_exactly_50_neutral(self):
        assert decision.decide_direction(50.0, "POSITIVE") == "NO_TRADE"
