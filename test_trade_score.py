"""
Unit tests for trade_score.py — composite calculation and score text parsing.
Run: pytest test_trade_score.py -v
"""

import pytest
import trade_score as ts


class TestCalculateCompositeScore:
    def test_all_100_strong_buy(self):
        res = ts.calculate_composite_score(100, 100, 100, 100, 100)
        assert res["score"] == 100.0
        assert res["grade"] == "A+"
        assert res["signal"] == "Strong Buy"
        assert res["breakdown"]["technical"]["weighted"] == 25.0
        assert res["breakdown"]["sentiment"]["weighted"] == 20.0
        assert res["breakdown"]["risk"]["weighted"] == 15.0

    def test_all_zero_avoid(self):
        res = ts.calculate_composite_score(0, 0, 0, 0, 0)
        assert res["score"] == 0.0
        assert res["grade"] == "F"
        assert res["signal"] == "Avoid"

    def test_weighted_calculation(self):
        # 80*0.25 + 70*0.25 + 60*0.20 + 50*0.15 + 90*0.15
        # = 20 + 17.5 + 12 + 7.5 + 13.5 = 70.5
        res = ts.calculate_composite_score(80, 70, 60, 50, 90)
        assert res["score"] == 70.5
        assert res["grade"] == "A"
        assert res["signal"] == "Buy"

    def test_grade_boundaries(self):
        # Exactly 85 -> A+
        assert ts._grade(85.0) == ("A+", "Strong Buy")
        # 84.9 -> A
        assert ts._grade(84.9) == ("A", "Buy")
        # Exactly 70 -> A
        assert ts._grade(70.0) == ("A", "Buy")
        # Exactly 55 -> B
        assert ts._grade(55.0) == ("B", "Hold/Accumulate")
        # Exactly 40 -> C
        assert ts._grade(40.0) == ("C", "Neutral")
        # Exactly 25 -> D
        assert ts._grade(25.0) == ("D", "Caution")
        # Below 25 -> F
        assert ts._grade(24.9) == ("F", "Avoid")


class TestScoreFromAgentText:
    def test_parse_pattern_1(self):
        text = "## Technical Analysis: RELIANCE\n### Technical Score: [78]/100\nDetails..."
        score = ts.score_from_agent_text(text, "Technical")
        assert score == 78.0

    def test_parse_pattern_2(self):
        text = "Fundamental Score: 65.5/100"
        score = ts.score_from_agent_text(text, "Fundamental")
        assert score == 65.5

    def test_parse_pattern_bracket_only(self):
        text = "Overall rating [82/100] for this asset"
        score = ts.score_from_agent_text(text, "Sentiment")
        assert score == 82.0

    def test_fallback_on_unparseable(self):
        text = "No score mentioned here."
        score = ts.score_from_agent_text(text, "Risk")
        assert score == 50.0  # default fallback
