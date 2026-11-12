"""
Unit tests for report_writer.py — verify TRADE-ANALYSIS-TICKER.md generation.
Run: pytest test_report_writer.py -v
"""

import os
import tempfile
import pytest
import report_writer as rw
import trade_score as ts


class TestWriteTradeAnalysisReport:
    def test_creates_file_with_expected_sections(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ticker = "RELIANCE"
            company_name = "Reliance Industries Ltd"
            agent_results = {
                "technical":   "## Technical Analysis: RELIANCE\n### Technical Score: [75]/100\nTrend is bullish.",
                "fundamental": "## Fundamental Analysis: RELIANCE\n### Fundamental Score: [80]/100\nStrong moat.",
                "sentiment":   "## Sentiment Analysis: RELIANCE\n### Sentiment Score: [70]/100\nPositive news flow.",
                "risk":        "## Risk Assessment: RELIANCE\n### Risk Score: [65]/100\nModerate volatility.",
                "thesis":      "## Investment Thesis: RELIANCE\n### Thesis Score: [78]/100\nCatalyst driven growth.",
            }
            score_res = ts.calculate_composite_score(75, 80, 70, 65, 78)
            discovery_brief = {
                "current_price": 2850.50,
                "price_change": "+25.50 (+0.90%)",
                "sector": "Energy & Retail",
                "entry_zone": "₹2,840 - ₹2,860",
                "stop_loss": "₹2,780",
                "target1": "₹2,980",
                "target2": "₹3,100",
                "rr_ratio": "2.1:1",
                "bull_points": ["Retail expansion", "Jio tariff hike", "Oil-to-chemicals recovery"],
                "bear_points": ["Global refining margin pressure", "High debt"],
                "bull_target": "₹3,200",
                "bear_target": "₹2,500",
                "catalysts": [
                    {"date": "18 Oct 2026", "event": "Q2 Earnings", "impact": "Bullish"}
                ]
            }

            path = rw.write_trade_analysis_report(
                ticker=ticker,
                company_name=company_name,
                agent_results=agent_results,
                trade_score_result=score_res,
                discovery_brief=discovery_brief,
                output_dir=tmpdir,
            )

            assert os.path.exists(path)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            assert "# Trade Analysis: RELIANCE — Reliance Industries Ltd" in content
            assert "## Executive Summary" in content
            assert "## Trade Score Dashboard" in content
            assert "## Technical Analysis" in content
            assert "## Fundamental Analysis" in content
            assert "## Sentiment Analysis" in content
            assert "## Risk Assessment" in content
            assert "## Investment Thesis" in content
            assert "## Entry/Exit Strategy" in content
            assert "## Bull vs Bear" in content
            assert "## Catalyst Calendar" in content
            assert "DISCLAIMER" in content
            assert f"**{score_res['score']}/100**" in content

    def test_creates_file_with_option_chain_section(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ticker = "INFY"
            company_name = "Infosys Ltd"
            agent_results = {"technical": "", "fundamental": "", "sentiment": "", "risk": "", "thesis": ""}
            score_res = ts.calculate_composite_score(70, 70, 70, 70, 70)
            discovery_brief = {
                "current_price": 1820.0,
                "option_chain": {
                    "ticker": "INFY",
                    "nearest_expiry": "2026-10-29",
                    "strike_interval": 20.0,
                    "atm_strike": 1820.0,
                    "pcr": 1.25,
                    "weighted_pcr": 1.30,
                    "oi_bias": "BULLISH",
                    "resistance_strike": 1860.0,
                    "resistance_oi": 500000,
                    "support_strike": 1800.0,
                    "support_oi": 750000,
                    "upside_probability": 0.65,
                    "downside_probability": 0.35,
                    "ce_premium": 28.5,
                    "pe_premium": 22.0,
                    "net_premium": 50.5,
                    "required_move_both": 58.075,
                    "rows": [
                        {
                            "strike": 1800.0,
                            "is_atm": False,
                            "is_resistance": False,
                            "is_support": True,
                            "ce": {"oi": 100000, "volume": 1200, "ltp": 38.0},
                            "pe": {"oi": 750000, "volume": 8500, "ltp": 15.0},
                        },
                        {
                            "strike": 1820.0,
                            "is_atm": True,
                            "is_resistance": False,
                            "is_support": False,
                            "ce": {"oi": 300000, "volume": 5200, "ltp": 28.5},
                            "pe": {"oi": 400000, "volume": 6100, "ltp": 22.0},
                        },
                        {
                            "strike": 1840.0,
                            "is_atm": False,
                            "is_resistance": False,
                            "is_support": False,
                            "ce": {"oi": 450000, "volume": 4200, "ltp": 19.0},
                            "pe": {"oi": 150000, "volume": 2100, "ltp": 32.0},
                        },
                        {
                            "strike": 1860.0,
                            "is_atm": False,
                            "is_resistance": True,
                            "is_support": False,
                            "ce": {"oi": 500000, "volume": 9200, "ltp": 12.0},
                            "pe": {"oi": 50000, "volume": 800, "ltp": 48.0},
                        },
                    ]
                }
            }

            path = rw.write_trade_analysis_report(
                ticker=ticker,
                company_name=company_name,
                agent_results=agent_results,
                trade_score_result=score_res,
                discovery_brief=discovery_brief,
                output_dir=tmpdir,
            )

            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            assert "## Option Chain Analysis (ATM ± 5 Strikes)" in content
            assert "Put-Call Ratio (PCR)" in content
            assert "1.25" in content
            assert "BULLISH" in content
            assert "Resistance Wall (Ceiling)" in content
            assert "1860" in content
            assert "Support Wall (Floor)" in content
            assert "1800" in content
            assert "### 11-Strike Derivative Chain" in content
            assert "[ATM]" in content
            assert "[RES]" in content
            assert "[SUP]" in content
