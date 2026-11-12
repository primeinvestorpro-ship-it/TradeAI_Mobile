"""
5-Parallel-Agent Trade Analysis Orchestrator for NSE Stock Futures.

Implements the three-phase trade-analyze workflow:
  Phase 1  — Discovery (price, news, financials via AI web search)
  Phase 2  — 5 parallel agents (Technical, Fundamental, Sentiment, Risk, Thesis)
  Phase 3  — Synthesis → Composite Trade Score + report

All AI calls use the same Gemini/Claude provider configured in settings.json.
Google Search grounding is used where available. Failures fall back gracefully.

DISCLAIMER: For educational and research purposes only. Not financial advice.
"""

import datetime
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


# ---------------------------------------------------------------------------
# Safe defaults
# ---------------------------------------------------------------------------

_SAFE_SENTIMENT = {"sentiment": "NEUTRAL", "reason": "safe default (no AI response)"}
_JSON_OBJ_RE = re.compile(r"\{.*?\}", re.DOTALL)
VALID_SENTIMENTS = ("POSITIVE", "NEGATIVE", "NEUTRAL")


def _parse_json_field(text, key):
    """Extract a JSON field from free-form AI text."""
    match = _JSON_OBJ_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data.get(key)
    except (json.JSONDecodeError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Low-level AI API callers (same pattern as algo_ai/ai_sentiment.py)
# ---------------------------------------------------------------------------

def _call_gemini(api_key, model, prompt, timeout=120, log=print):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
    }
    resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    if resp.status_code == 429:
        log("[ai_orchestrator] Google Search grounding quota exhausted (429), retrying without search")
        body_no_search = {"contents": [{"parts": [{"text": prompt}]}]}
        resp = requests.post(url, headers=headers, json=body_no_search, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


def _call_claude(api_key, model, prompt, timeout=120):
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
    }
    resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return "".join(
        block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
    )


def _call_ai(settings, prompt, log=print, timeout=120):
    """Unified AI call. Returns raw text or raises."""
    provider = settings.get("ai_provider", "gemini")
    if provider == "gemini":
        return _call_gemini(
            settings["gemini_api_key"], settings["gemini_model"], prompt, timeout=timeout, log=log
        )
    elif provider == "claude":
        return _call_claude(
            settings["anthropic_api_key"], settings["claude_model"], prompt, timeout=timeout
        )
    else:
        raise ValueError(f"unknown ai_provider: {provider!r}")


# ---------------------------------------------------------------------------
# Phase 1 — Discovery
# ---------------------------------------------------------------------------

_DISCOVERY_PROMPT = """\
Today is {today}. You are the discovery agent for a comprehensive stock analysis.

Search the web and compile the following information for the NSE-listed stock: {ticker}

1. CURRENT MARKET DATA
   - Current stock price (₹)
   - Today's price change (₹ and %)
   - Market cap and category (Large/Mid/Small cap)
   - 52-week high and 52-week low
   - Average daily volume (shares)
   - Sector and industry classification
   - NIFTY 50 / NIFTY 500 / relevant index performance for context

2. COMPANY OVERVIEW
   - What the company does (2-3 sentences)
   - Key products or revenue segments
   - CEO and key leadership
   - NSE symbol and BSE code
   - Promoter holding %

3. RECENT NEWS & CATALYSTS (last 30 days)
   - Last 5-10 major headlines
   - Upcoming results date (Q2/Q3 etc.)
   - Most recent quarterly results (EPS beat/miss, revenue beat/miss)
   - Any major announcements (orders, capex, mergers, regulatory)

4. KEY FINANCIAL METRICS
   - P/E ratio (TTM and forward)
   - Revenue (TTM) and YoY growth
   - EPS (TTM) and YoY growth
   - Profit margins (gross, operating, net)
   - Debt-to-equity ratio
   - Return on Equity (ROE)
   - Dividend yield (if any)

5. F&O DATA & OPTION CHAIN (if available)
   - Futures premium/discount to spot
   - Put-Call Ratio (PCR) for the stock
   - Near-month futures OI trend
   - Option Chain ATM strike & strike step
   - Institutional Resistance (max Call OI wall above ATM) and Support (max Put OI wall below ATM)
   - ATM Call & Put premiums and combined straddle breakeven required move

6. INTRADAY BUY SETUP & VOLUME FILTER (Chartink: https://chartink.com/screener/my-intraday-buy-setup-adx)
   Evaluate whether {ticker} currently meets the "My-intraday-buy-setup-ADX" volume and momentum filter:
   - Multiple EMA alignment: Is price > 5-min EMA(8), EMA(34), and EMA(50)?
   - Volume Surge: Is current/recent volume > 10-period WMA of volume (or 20-day avg volume)?
   - ADX & Directional Trend: Is +DI(14) >= 24 confirming strong directional buyer control?
   - Pivot Positioning: Did Today's Open and price hold above Yesterday's Pivot (H+L+C)/3 and R1?
   - Setup Status: Assess if this stock qualifies as "PASSED", "PARTIAL", or "FAILED".

Compile all findings into a structured JSON. Respond ONLY with this JSON:
{{
  "ticker": "{ticker}",
  "company_name": "<full name>",
  "current_price": <number>,
  "price_change": "<+X.XX (Y.YY%)>",
  "market_cap": "<e.g. ₹12.5L Cr>",
  "cap_category": "<Large/Mid/Small>",
  "week52_high": <number>,
  "week52_low": <number>,
  "avg_daily_volume": "<e.g. 2.3M shares>",
  "sector": "<sector>",
  "industry": "<industry>",
  "index_context": "<brief>",
  "company_description": "<2-3 sentences>",
  "key_products": "<brief>",
  "ceo": "<name>",
  "promoter_holding": "<X.X%>",
  "recent_news": ["<headline 1>", "<headline 2>", "..."],
  "next_results_date": "<date or 'unknown'>",
  "last_quarter_eps": "<beat/miss/inline>",
  "last_quarter_revenue": "<beat/miss/inline>",
  "pe_ttm": <number or null>,
  "pe_forward": <number or null>,
  "revenue_ttm": "<e.g. ₹2.4L Cr>",
  "revenue_growth_yoy": "<X.X%>",
  "eps_ttm": <number or null>,
  "eps_growth_yoy": "<X.X%>",
  "gross_margin": "<X.X%>",
  "operating_margin": "<X.X%>",
  "net_margin": "<X.X%>",
  "debt_to_equity": <number or null>,
  "roe": "<X.X%>",
  "dividend_yield": "<X.X% or 'None'>",
  "fno_futures_premium": "<premium/discount or 'N/A'>",
  "fno_pcr": <number or null>,
  "option_chain_summary": {{
    "atm_strike": <number or null>,
    "strike_interval": <number or null>,
    "pcr": <number or null>,
    "resistance_wall": <number or null>,
    "support_wall": <number or null>,
    "atm_ce_premium": <number or null>,
    "atm_pe_premium": <number or null>,
    "straddle_net_premium": <number or null>,
    "oi_bias": "BULLISH|NEUTRAL|BEARISH"
  }},
  "adx_volume_setup": {{
    "status": "PASSED|PARTIAL|FAILED",
    "plus_di": <number or null>,
    "adx_14": <number or null>,
    "volume_surge": true,
    "volume_vs_10wma": "<details e.g. 1.8x 10-WMA>",
    "ema_alignment": "BULLISH|NEUTRAL|BEARISH",
    "pivot_status": "ABOVE_PIVOT_R1|BELOW_PIVOT",
    "notes": "<concise summary of Chartink ADX volume filter>"
  }},
  "ai_sentiment": "POSITIVE|NEGATIVE|NEUTRAL",
  "ai_sentiment_reason": "<one-line reason>",
  "executive_summary": "<2-3 paragraph summary>",
  "entry_zone": "<e.g. ₹2,450 - ₹2,480>",
  "stop_loss": "<e.g. ₹2,380>",
  "target1": "<e.g. ₹2,580>",
  "target2": "<e.g. ₹2,680>",
  "rr_ratio": "<e.g. 2.5:1>",
  "timeframe": "<Swing / Position trade>",
  "bull_points": ["<bull 1>", "<bull 2>", "<bull 3>", "<bull 4>", "<bull 5>"],
  "bear_points": ["<bear 1>", "<bear 2>", "<bear 3>", "<bear 4>", "<bear 5>"],
  "bull_target": "<e.g. ₹2,800>",
  "bear_target": "<e.g. ₹2,100>",
  "catalysts": [
    {{"date": "<date>", "event": "<event>", "impact": "Bullish/Bearish/Neutral"}}
  ]
}}

DISCLAIMER: This is for educational and research purposes only. Not financial advice.
"""

# ---------------------------------------------------------------------------
# Phase 2 — Agent mandate prompts
# ---------------------------------------------------------------------------

_AGENT_PROMPTS = {
    "technical": """\
You are a Technical Analysis specialist for NSE F&O stocks. Analyze {ticker} ({company_name}).

DISCOVERY DATA:
{discovery_brief}

YOUR MANDATE — Deliver comprehensive technical analysis for NSE FUTURES trading:

1. TREND ANALYSIS
   - Primary trend direction (bullish / bearish / sideways)
   - EMA 20/50/200 alignment and slope
   - Price vs key moving averages
   - Higher highs / higher lows pattern

2. SUPPORT & RESISTANCE (Technical + Option Chain Confluence)
   - At least 3 support levels with reasoning; note confluence with Option Chain Put OI support wall
   - At least 3 resistance levels with reasoning; note confluence with Option Chain Call OI resistance ceiling
   - Highlight key option strikes (ATM, Support Wall, Resistance Wall)

3. MOMENTUM INDICATORS
   - RSI (14): current value, overbought/oversold
   - MACD: signal line, histogram direction, divergences
   - Stochastic: %K/%D position

4. VOLUME ANALYSIS
   - Current volume vs 20-day average
   - Accumulation/distribution pattern
   - OBV trend and divergences

5. CHART PATTERNS
   - Active patterns (flags, wedges, H&S, cups, double tops/bottoms)
   - Pattern completion % and implied target
   - Key breakout/breakdown levels

6. ADDITIONAL
   - Bollinger Band position and squeeze
   - Golden/death cross proximity
   - Relative strength vs NIFTY 500 (1M, 3M, 6M)
   - Fibonacci retracement levels

7. CHARTINK VOLUME-BASED INTRADAY BUY SETUP (ADX + MULTIPLE EMA + 10-WMA VOLUME)
   (Reference: https://chartink.com/screener/my-intraday-buy-setup-adx)
   - Check if 5-min price > EMA(8), EMA(34), EMA(50)
   - Check if 5-min volume > 10-period WMA of Volume (Volume expansion)
   - Check if +DI(14) >= 24 with ADX indicating active trend acceleration
   - Check if Today's Open and price >= Daily Pivot (H+L+C)/3 and R1 level
   - Provide an explicit verdict: [QUALIFIED / PARTIAL / NOT QUALIFIED]

SCORING (0-100):
   - Trend Score (0-20)
   - Momentum Score (0-20)
   - Volume Score (0-20)
   - Pattern Quality (0-20)
   - Relative Strength (0-20)

Return in EXACTLY this format:
## Technical Analysis: {ticker}
### Technical Score: [X]/100
[Trend: X/20 | Momentum: X/20 | Volume: X/20 | Pattern: X/20 | Rel Strength: X/20]
### Signal: [Bullish / Neutral / Bearish]
[Full analysis by the sections above, including the Chartink ADX Setup verdict]
### Key Levels
- Entry Zone: ₹X - ₹X
- Stop Loss: ₹X (X% below entry)
- Target 1: ₹X (X% upside)
- Target 2: ₹X (X% upside)

DISCLAIMER: For educational and research purposes only. Not financial advice.
""",

    "fundamental": """\
You are a Fundamental Analysis specialist for NSE F&O stocks. Analyze {ticker} ({company_name}).

DISCOVERY DATA:
{discovery_brief}

YOUR MANDATE — Comprehensive fundamental analysis:

1. VALUATION
   - P/E (trailing & forward) vs sector median and 5-year average
   - P/S, P/B, PEG, EV/EBITDA vs sector
   - Verdict: Undervalued / Fair Value / Overvalued

2. GROWTH
   - Revenue growth (QoQ, YoY, 3Y CAGR)
   - Earnings growth (QoQ, YoY, 3Y CAGR)
   - Forward guidance and analyst estimates
   - TAM and penetration

3. PROFITABILITY
   - Gross, operating, net margin trends
   - ROE, ROCE, ROIC

4. FINANCIAL HEALTH
   - Debt-to-equity and trend
   - Current and quick ratio
   - Free cash flow and FCF yield
   - Cash position

5. COMPETITIVE MOAT
   - Brand, network effects, switching costs
   - Cost advantages, patents/licences
   - Moat rating: Wide / Narrow / None

6. MANAGEMENT
   - Promoter holding and pledge %
   - CEO track record
   - Capital allocation (buybacks, dividends, M&A)

SCORING (0-100):
   - Valuation (0-20)
   - Growth (0-20)
   - Profitability (0-20)
   - Financial Health (0-20)
   - Moat Strength (0-20)

Return in EXACTLY this format:
## Fundamental Analysis: {ticker}
### Fundamental Score: [X]/100
[Valuation: X/20 | Growth: X/20 | Profitability: X/20 | Health: X/20 | Moat: X/20]
### Signal: [Strong / Adequate / Weak]
[Full analysis by the 6 sections]

DISCLAIMER: For educational and research purposes only. Not financial advice.
""",

    "sentiment": """\
You are a Sentiment & Momentum Analysis specialist for NSE F&O stocks. Analyze {ticker} ({company_name}).

DISCOVERY DATA:
{discovery_brief}

YOUR MANDATE:

1. NEWS SENTIMENT — Search recent headlines. Score each positive/negative/neutral. Identify key catalysts.

2. SOCIAL MEDIA BUZZ — Search for {ticker} on Reddit (IndiaInvestments, SecurityAnalysis), Twitter/X, StockTwits. Gauge direction and intensity.

3. ANALYST RATINGS — Consensus rating (buy/hold/sell), average price target vs current price, recent upgrades/downgrades.

4. INSTITUTIONAL ACTIVITY — Recent bulk/block deals, FII/DII buying/selling, shareholding changes.

5. INSIDER TRADING — Recent insider buys and sells by promoters/directors. Flag cluster buying or large sales. SEBI disclosures.

6. F&O & OPTION CHAIN SENTIMENT — Live Option Chain PCR trend, institutional call/put writing concentration at nearest walls, option-chain implied directional odds, and delivery % trend in cash market.

SCORING (0-100 each dimension):
   - News Sentiment (0-20)
   - Social Buzz (0-20)
   - Analyst Consensus (0-20)
   - Institutional Activity (0-20)
   - Insider/F&O Signals (0-20)

Return in EXACTLY this format:
## Sentiment Analysis: {ticker}
### Sentiment Score: [X]/100
[News: X/20 | Social: X/20 | Analysts: X/20 | Institutional: X/20 | Insider/FnO: X/20]
### Signal: [Bullish / Neutral / Bearish]
[Full analysis by the 6 sections]

DISCLAIMER: For educational and research purposes only. Not financial advice.
""",

    "risk": """\
You are a Risk Assessment specialist for NSE F&O stocks. Analyze {ticker} ({company_name}).

DISCOVERY DATA:
{discovery_brief}

YOUR MANDATE — Comprehensive risk assessment for FUTURES trading:

1. VOLATILITY PROFILE & OPTION CHAIN PRICING
   - Historical volatility (30-day, 90-day) and ATR
   - Beta vs NIFTY 50 and NIFTY 500
   - Option Chain ATM straddle net premium (market-implied expected breakeven move)
   - Institutional resistance ceiling and support floor distances
   - Implied volatility from options (if available)

2. DOWNSIDE SCENARIOS
   - Bear case price target with reasoning
   - Maximum drawdown from current price (worst case)
   - Key risk events on the calendar
   - Sector-specific risks

3. CORRELATION & MACRO RISK
   - Correlation with NIFTY and sector index
   - RBI/interest rate sensitivity
   - USD/INR currency exposure
   - Commodity input risks
   - Regulatory and geopolitical risks for this company

4. LIQUIDITY RISK
   - Average daily dollar volume (cash + futures combined)
   - Futures OI and bid-ask spread
   - Institutional concentration risk

5. POSITION SIZING RECOMMENDATION (for futures)
   - Suggested lots (conservative, moderate, aggressive) given typical retail capital
   - Recommended stop-loss level and rationale
   - Risk/reward ratio at current price
   - Margin requirement awareness

6. TOP 5 RISKS — probability × severity matrix

SCORING (0-100, HIGHER = LOWER RISK):
   - Volatility (0-20): 20 = low vol
   - Downside Protection (0-20): 20 = limited downside
   - Macro Resilience (0-20)
   - Liquidity (0-20)
   - Risk/Reward (0-20)

Return in EXACTLY this format:
## Risk Assessment: {ticker}
### Risk Score: [X]/100 (higher = lower risk)
[Volatility: X/20 | Downside: X/20 | Macro: X/20 | Liquidity: X/20 | R/R: X/20]
### Risk Level: [Low / Moderate / High / Extreme]
[Full analysis by the 6 sections]

DISCLAIMER: For educational and research purposes only. Not financial advice.
""",

    "thesis": """\
You are an Investment Thesis specialist for NSE F&O stocks. Build a thesis for {ticker} ({company_name}).

DISCOVERY DATA:
{discovery_brief}

YOUR MANDATE — Complete investment thesis:

1. CORE THESIS (2-3 sentences)
   - Why this stock, why now, what is the edge for a FUTURES trade

2. BULL CASE
   - 3-5 specific catalysts driving upside
   - Bull case price target with timeline
   - What must go right

3. BEAR CASE
   - 3-5 specific risks driving downside
   - Bear case price target with timeline
   - What must go wrong

4. CATALYST CALENDAR
   - Upcoming events that could move the stock (results, AGM, product launches, regulatory)
   - Expected dates and impact direction

5. ENTRY/EXIT STRATEGY (Futures + Option Chain Confluence)
   - Recommended entry zone
   - Stop-loss (MANDATORY for futures — anchor to chart support and Option Chain support wall)
   - Target 1 (conservative) and Target 2 (aggressive — anchor to resistance wall ceiling)
   - Suggested timeframe (intraday / swing / positional)
   - Lot size suggestion (for ₹5L, ₹10L, ₹25L capital)

6. CONVICTION ASSESSMENT
   - What gives you conviction
   - Thesis invalidation triggers
   - Comparison to holding NIFTY ETF instead

SCORING (0-100):
   - Catalyst Clarity (0-20)
   - Timing (0-20)
   - Asymmetry (0-20)
   - Edge (0-20)
   - Conviction (0-20)

Return in EXACTLY this format:
## Investment Thesis: {ticker}
### Thesis Score: [X]/100
[Catalyst: X/20 | Timing: X/20 | Asymmetry: X/20 | Edge: X/20 | Conviction: X/20]
### Thesis: [Strong / Moderate / Weak]
[Full analysis by the 6 sections]

DISCLAIMER: For educational and research purposes only. Not financial advice.
""",
}


# ---------------------------------------------------------------------------
# Orchestrator class
# ---------------------------------------------------------------------------

class TradeAnalysisOrchestrator:
    """3-phase trade analysis orchestrator for NSE Stock Futures."""

    def __init__(self, settings: dict, log=print):
        self.settings = settings
        self.log = log

    # ------------------------------------------------------------------
    # Phase 1: Discovery
    # ------------------------------------------------------------------

    def phase1_discovery(self, ticker: str, stop_event=None) -> dict:
        """
        Calls AI with Google Search to gather all foundational market data.
        Returns the DISCOVERY_BRIEF dict.
        Falls back gracefully if the AI call fails.
        """
        if stop_event and stop_event.is_set():
            return self._minimal_brief(ticker, "Cancelled by user")

        today = datetime.date.today().strftime("%d %B %Y")
        prompt = _DISCOVERY_PROMPT.format(ticker=ticker.upper(), today=today)

        self.log(f"[orchestrator] Phase 1: Discovery for {ticker.upper()}...")
        try:
            raw = _call_ai(self.settings, prompt, log=self.log, timeout=120)
            if stop_event and stop_event.is_set():
                return self._minimal_brief(ticker, "Cancelled by user")
            # Extract JSON from response
            json_match = re.search(r'\{[\s\S]*\}', raw)
            if json_match:
                brief = json.loads(json_match.group(0))
                self.log(
                    f"[orchestrator] Discovery complete: {brief.get('company_name', ticker)} "
                    f"@ ₹{brief.get('current_price', 'N/A')} "
                    f"({brief.get('price_change', '')})"
                )
                return brief
            else:
                self.log("[orchestrator] Discovery: could not parse JSON from AI response, using minimal brief")
                return self._minimal_brief(ticker, raw)
        except Exception as exc:  # noqa: BLE001
            self.log(f"[orchestrator] Discovery failed ({exc!r}), using minimal brief")
            return self._minimal_brief(ticker, "")

    def _minimal_brief(self, ticker: str, raw_text: str) -> dict:
        """Minimal discovery brief when AI fails, so agents can still run."""
        return {
            "ticker": ticker.upper(),
            "company_name": ticker.upper(),
            "current_price": "N/A",
            "price_change": "N/A",
            "sector": "N/A",
            "industry": "N/A",
            "company_description": raw_text[:500] if raw_text else "Data unavailable",
            "recent_news": [],
            "ai_sentiment": "NEUTRAL",
            "ai_sentiment_reason": "Discovery data unavailable",
            "executive_summary": "",
            "catalysts": [],
            "bull_points": [],
            "bear_points": [],
            "adx_volume_setup": {
                "status": "UNKNOWN",
                "notes": "Discovery data unavailable",
                "screener_url": "https://chartink.com/screener/my-intraday-buy-setup-adx",
            },
            "option_chain_summary": {},
        }

    # ------------------------------------------------------------------
    # Phase 2: Parallel agents
    # ------------------------------------------------------------------

    def phase2_launch_agents(self, ticker: str, discovery_brief: dict, stop_event=None) -> dict:
        """
        Launches all 5 agents concurrently.
        Returns {"technical": str, "fundamental": str, "sentiment": str,
                 "risk": str, "thesis": str} — raw agent text responses.
        """
        if stop_event and stop_event.is_set():
            return {}

        company_name = discovery_brief.get("company_name", ticker.upper())
        brief_str = json.dumps(discovery_brief, indent=2, ensure_ascii=False)

        agent_names = ["technical", "fundamental", "sentiment", "risk", "thesis"]
        results = {}
        errors = {}

        self.log(f"[orchestrator] Phase 2: Launching 5 parallel analysis agents for {ticker.upper()}...")

        def run_agent(name):
            if stop_event and stop_event.is_set():
                return name, "", InterruptedError("Cancelled by user")
            prompt = _AGENT_PROMPTS[name].format(
                ticker=ticker.upper(),
                company_name=company_name,
                discovery_brief=brief_str,
            )
            self.log(f"[orchestrator]   → {name.capitalize()} agent starting...")
            try:
                if stop_event and stop_event.is_set():
                    return name, "", InterruptedError("Cancelled by user")
                text = _call_ai(self.settings, prompt, log=self.log, timeout=180)
                if stop_event and stop_event.is_set():
                    return name, "", InterruptedError("Cancelled by user")
                self.log(f"[orchestrator]   [OK] {name.capitalize()} agent complete ({len(text)} chars)")
                return name, text, None
            except Exception as exc:  # noqa: BLE001
                self.log(f"[orchestrator]   [FAIL] {name.capitalize()} agent failed: {exc!r}")
                return name, f"## {name.capitalize()} Analysis: {ticker.upper()}\n\n_Agent failed: {exc!r}_\n", exc

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures_map = {executor.submit(run_agent, name): name for name in agent_names}
            for future in as_completed(futures_map):
                if stop_event and stop_event.is_set():
                    self.log("[orchestrator] Stop signal received: cancelling pending agents")
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                name, text, err = future.result()
                results[name] = text
                if err:
                    errors[name] = str(err)

        if errors:
            self.log(f"[orchestrator] Phase 2 complete with {len(errors)} agent failure(s): {list(errors.keys())}")
        else:
            self.log("[orchestrator] Phase 2 complete — all 5 agents returned successfully")

        return results

    # ------------------------------------------------------------------
    # Phase 3: Synthesis
    # ------------------------------------------------------------------

    def phase3_synthesize(
        self,
        ticker: str,
        discovery_brief: dict,
        agent_results: dict,
        output_dir: str = "reports",
        stop_event=None,
    ) -> tuple:
        """
        Calculates the composite Trade Score and writes the markdown report.
        Returns (trade_score_result, report_path).
        """
        if stop_event and stop_event.is_set():
            return {
                "score": 50.0, "grade": "C", "signal": "Neutral",
                "dimension_scores": {}, "subagent_results": agent_results,
            }, ""

        import trade_score as ts

        self.log("[orchestrator] Phase 3: Synthesizing composite Trade Score...")

        # Extract numeric scores from each agent's text
        dim_names = {
            "technical":   "Technical",
            "fundamental": "Fundamental",
            "sentiment":   "Sentiment",
            "risk":        "Risk",
            "thesis":      "Thesis",
        }
        scores = {}
        for key, label in dim_names.items():
            text = agent_results.get(key, "")
            scores[key] = ts.score_from_agent_text(text, label)
            self.log(f"[orchestrator]   {label} Score: {scores[key]:.0f}/100")

        result = ts.calculate_composite_score(**scores)
        self.log(
            f"[orchestrator] Composite Trade Score: {result['score']}/100 "
            f"(Grade {result['grade']} — {result['signal']})"
        )

        # Write the report
        import report_writer as rw
        company_name = discovery_brief.get("company_name", ticker.upper())
        report_path = rw.write_trade_analysis_report(
            ticker=ticker,
            company_name=company_name,
            agent_results=agent_results,
            trade_score_result=result,
            discovery_brief=discovery_brief,
            output_dir=output_dir,
        )
        self.log(f"[orchestrator] Report written → {report_path}")

        return result, report_path

    # ------------------------------------------------------------------
    # Convenience: run all 3 phases
    # ------------------------------------------------------------------

    def run_full_analysis(self, ticker: str, output_dir: str = "reports") -> dict:
        """
        Runs Phase 1 → 2 → 3 in sequence and returns:
        {
          "ticker": str,
          "discovery_brief": dict,
          "agent_results": dict,
          "trade_score": dict,
          "report_path": str,
          "ai_sentiment": str,
          "ai_reason": str,
        }
        """
        discovery_brief = self.phase1_discovery(ticker)
        agent_results = self.phase2_launch_agents(ticker, discovery_brief)
        trade_score_result, report_path = self.phase3_synthesize(
            ticker, discovery_brief, agent_results, output_dir=output_dir
        )

        return {
            "ticker":          ticker.upper(),
            "discovery_brief": discovery_brief,
            "agent_results":   agent_results,
            "trade_score":     trade_score_result,
            "report_path":     report_path,
            "ai_sentiment":    discovery_brief.get("ai_sentiment", "NEUTRAL"),
            "ai_reason":       discovery_brief.get("ai_sentiment_reason", ""),
        }


def build_prompt_preview(ticker: str = "TICKER") -> str:
    """
    Public read-only preview of the discovery prompt — for the dashboard's
    'Advanced' section. Never used for an actual AI call.
    """
    today = datetime.date.today().strftime("%d %B %Y")
    return _DISCOVERY_PROMPT.format(ticker=ticker.upper(), today=today)
