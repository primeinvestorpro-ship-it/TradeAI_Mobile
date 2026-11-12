"""
Smart Money Accumulation, Distribution & Futures Strategy Engine (Strategy E).

Identifies institutional accumulation near market bottoms and distribution near tops:
  1. 50, 100, 200 SMA trend structure and slope.
  2. On Balance Volume (OBV) and volume-price divergences.
  3. Volume expansion (5-session vs prev 5-session, 20-session baseline).
  4. Automatic Demand & Supply Zone identification with proximity and bounce metrics.
  5. NSE Stock Futures validation (OI buildup, basis, VWAP alignment).
  6. Supertrend confirmation (equity & futures).
  7. Order flow, volume delta, and cumulative delta analysis.
  8. 100-Point Confluence Scoring Model:
     - 80-100: High-confluence confirmed signal
     - 65-79: Developing setup (Early Accumulation / Distribution)
     - < 65: Watchlist / Wait
  9. Invalidation levels, Stop-Loss, Target 1, Target 2, Risk-Reward ratio.

DISCLAIMER: For educational, backtesting and paper trading only. Not financial advice.
"""

import datetime
import math
from typing import Dict, List, Optional, Tuple

import futures_signal


# ---------------------------------------------------------------------------
# Basic technical indicators (SMA, Slope, OBV)
# ---------------------------------------------------------------------------

def compute_sma(prices: List[float], period: int) -> Optional[float]:
    """Compute Simple Moving Average of prices."""
    if len(prices) < period or period <= 0:
        return None
    return sum(prices[-period:]) / float(period)


def compute_sma_series(prices: List[float], period: int) -> List[Optional[float]]:
    """Compute full SMA series over prices."""
    n = len(prices)
    if n < period or period <= 0:
        return [None] * n

    series: List[Optional[float]] = [None] * (period - 1)
    window_sum = sum(prices[:period])
    series.append(window_sum / float(period))

    for i in range(period, n):
        window_sum += prices[i] - prices[i - period]
        series.append(window_sum / float(period))

    return series


def compute_sma_slope(sma_series: List[Optional[float]], lookback: int = 5) -> float:
    """
    Compute percentage slope of SMA series over `lookback` periods.
    Returns percentage change (> 0 is positive/upward slope, < 0 is downward).
    """
    valid = [x for x in sma_series if x is not None]
    if len(valid) < lookback + 1 or valid[-lookback - 1] == 0:
        return 0.0
    p_curr = valid[-1]
    p_prev = valid[-lookback - 1]
    return ((p_curr - p_prev) / p_prev) * 100.0


def compute_obv_series(candles: List[Dict]) -> List[float]:
    """
    Compute On Balance Volume (OBV) series across candles.
    """
    if not candles:
        return []
    obv_series = [0.0]
    for i in range(1, len(candles)):
        prev_close = candles[i - 1]["close"]
        curr_close = candles[i]["close"]
        curr_vol = float(candles[i].get("volume", 0))

        if curr_close > prev_close:
            obv_series.append(obv_series[-1] + curr_vol)
        elif curr_close < prev_close:
            obv_series.append(obv_series[-1] - curr_vol)
        else:
            obv_series.append(obv_series[-1])
    return obv_series


def compute_obv_divergence(candles: List[Dict], obv_series: List[float], lookback: int = 15) -> Dict:
    """
    Detect bullish or bearish divergence between price and OBV over `lookback` candles.
    Bullish divergence: Price makes lower low or flat low, but OBV makes higher low.
    Bearish divergence: Price makes higher high or flat high, but OBV makes lower high.
    """
    if len(candles) < lookback or len(obv_series) < lookback:
        return {"divergence": "NONE", "obv_trend": "FLAT", "bullish_div": False, "bearish_div": False}

    window_candles = candles[-lookback:]
    window_obv = obv_series[-lookback:]

    p_start = window_candles[0]["close"]
    p_end = window_candles[-1]["close"]
    obv_start = window_obv[0]
    obv_end = window_obv[-1]

    p_change = (p_end - p_start) / p_start if p_start else 0
    obv_change = obv_end - obv_start

    # Trend of OBV
    obv_trend = "RISING" if obv_change > 0 else ("FALLING" if obv_change < 0 else "FLAT")

    # Bullish divergence: price declined or moved sideways while OBV rose significantly
    bullish_div = (p_change <= 0.01) and (obv_change > 0)

    # Bearish divergence: price rose or moved sideways while OBV fell significantly
    bearish_div = (p_change >= -0.01) and (obv_change < 0)

    div_type = "BULLISH" if bullish_div else ("BEARISH" if bearish_div else "NONE")
    return {
        "divergence": div_type,
        "obv_trend": obv_trend,
        "bullish_div": bullish_div,
        "bearish_div": bearish_div,
        "obv_latest": window_obv[-1],
    }


# ---------------------------------------------------------------------------
# Volume Expansion Analysis
# ---------------------------------------------------------------------------

def compute_volume_expansion(candles: List[Dict]) -> Dict:
    """
    Compare:
      - Current 5-session average volume
      - Previous 5-session average volume
      - 20-session average volume
      - Latest completed candle volume
    """
    if len(candles) < 20:
        return {
            "vol_5_curr": None,
            "vol_5_prev": None,
            "vol_20_avg": None,
            "vol_latest": None,
            "expansion_ratio": 1.0,
            "vol_5_expanding": False,
            "vol_above_20_avg": False,
        }

    volumes = [float(c.get("volume", 0)) for c in candles]
    vol_latest = volumes[-1]
    vol_5_curr = sum(volumes[-5:]) / 5.0
    vol_5_prev = sum(volumes[-10:-5]) / 5.0 if len(volumes) >= 10 else vol_5_curr
    vol_20_avg = sum(volumes[-20:]) / 20.0

    expansion_ratio = round(vol_5_curr / vol_5_prev, 2) if vol_5_prev > 0 else 1.0
    vol_5_expanding = vol_5_curr > vol_5_prev
    vol_above_20_avg = vol_latest > vol_20_avg

    return {
        "vol_5_curr": round(vol_5_curr),
        "vol_5_prev": round(vol_5_prev),
        "vol_20_avg": round(vol_20_avg),
        "vol_latest": round(vol_latest),
        "expansion_ratio": expansion_ratio,
        "vol_5_expanding": vol_5_expanding,
        "vol_above_20_avg": vol_above_20_avg,
    }


# ---------------------------------------------------------------------------
# Demand and Supply Zone Detection
# ---------------------------------------------------------------------------

def detect_supply_demand_zones(candles: List[Dict], lookback: int = 50) -> Dict:
    """
    Detect key Demand (support) and Supply (resistance) zones from price swings.
    Computes distance percentage from current price to nearest demand and supply zone.
    """
    if not candles or len(candles) < 10:
        return {
            "demand_zone": (0.0, 0.0),
            "supply_zone": (0.0, 0.0),
            "dist_to_demand_pct": 0.0,
            "dist_to_supply_pct": 0.0,
            "demand_status": "NONE",
            "supply_status": "NONE",
        }

    window = candles[-lookback:]
    curr_close = window[-1]["close"]

    # Identify swing lows and highs
    swing_lows = []
    swing_highs = []
    for i in range(2, len(window) - 2):
        l = window[i]["low"]
        h = window[i]["high"]
        if l < window[i - 1]["low"] and l < window[i - 2]["low"] and l < window[i + 1]["low"] and l < window[i + 2]["low"]:
            swing_lows.append(l)
        if h > window[i - 1]["high"] and h > window[i - 2]["high"] and h > window[i + 1]["high"] and h > window[i + 2]["high"]:
            swing_highs.append(h)

    # Nearest demand zone below or near current price
    lows_below = [sl for sl in swing_lows if sl <= curr_close * 1.03]
    if lows_below:
        demand_level = max(lows_below)
    else:
        demand_level = min(c["low"] for c in window)

    demand_zone = (round(demand_level * 0.99, 2), round(demand_level * 1.01, 2))
    dist_demand = round(((curr_close - demand_level) / curr_close) * 100.0, 2)

    # Nearest supply zone above or near current price
    highs_above = [sh for sh in swing_highs if sh >= curr_close * 0.97]
    if highs_above:
        supply_level = min(highs_above)
    else:
        supply_level = max(c["high"] for c in window)

    supply_zone = (round(supply_level * 0.99, 2), round(supply_level * 1.01, 2))
    dist_supply = round(((supply_level - curr_close) / curr_close) * 100.0, 2)

    # Classification
    if abs(dist_demand) <= 1.5:
        demand_status = "BOUNCING" if curr_close >= demand_level else "APPROACHING"
    elif curr_close < demand_zone[0]:
        demand_status = "BROKEN"
    else:
        demand_status = "ABOVE_DEMAND"

    if abs(dist_supply) <= 1.5:
        supply_status = "REJECTING" if curr_close <= supply_level else "APPROACHING"
    elif curr_close > supply_zone[1]:
        supply_status = "BROKEN"
    else:
        supply_status = "BELOW_SUPPLY"

    return {
        "demand_zone": demand_zone,
        "supply_zone": supply_zone,
        "demand_level": round(demand_level, 2),
        "supply_level": round(supply_level, 2),
        "dist_to_demand_pct": dist_demand,
        "dist_to_supply_pct": dist_supply,
        "demand_status": demand_status,
        "supply_status": supply_status,
    }


# ---------------------------------------------------------------------------
# Futures OI Flow & Order Flow Validation
# ---------------------------------------------------------------------------

def analyze_futures_oi_flow(
    spot_price: float,
    futures_price: Optional[float] = None,
    prev_futures_price: Optional[float] = None,
    current_oi: Optional[int] = None,
    prev_oi: Optional[int] = None,
    vwap: Optional[float] = None,
    volume_delta: Optional[float] = None,
) -> Dict:
    """
    Analyze futures direction, basis, VWAP position, and Open Interest buildup:
      - Long Buildup: Price UP + OI UP
      - Short Buildup: Price DOWN + OI UP
      - Short Covering: Price UP + OI DOWN
      - Long Unwinding: Price DOWN + OI DOWN
    """
    fut_p = futures_price if (futures_price and futures_price > 0) else spot_price
    basis = round(fut_p - spot_price, 2)
    basis_pct = round((basis / spot_price) * 100.0, 2) if spot_price else 0.0

    # Direction & OI Change
    p_diff = 0.0
    if prev_futures_price and prev_futures_price > 0:
        p_diff = fut_p - prev_futures_price

    oi_diff = 0
    if current_oi is not None and prev_oi is not None:
        oi_diff = current_oi - prev_oi

    # Classification of Buildup
    if p_diff >= 0 and oi_diff > 0:
        buildup = "LONG_BUILDUP"
        buildup_bias = "BULLISH"
    elif p_diff < 0 and oi_diff > 0:
        buildup = "SHORT_BUILDUP"
        buildup_bias = "BEARISH"
    elif p_diff >= 0 and oi_diff <= 0:
        buildup = "SHORT_COVERING"
        buildup_bias = "MILD_BULLISH"
    else:
        buildup = "LONG_UNWINDING"
        buildup_bias = "MILD_BEARISH"

    # VWAP alignment
    vwap_alignment = "NEUTRAL"
    if vwap and vwap > 0:
        if fut_p > vwap:
            vwap_alignment = "ABOVE_VWAP"
        elif fut_p < vwap:
            vwap_alignment = "BELOW_VWAP"

    # Order flow / Delta status
    order_flow_status = "POSITIVE" if (volume_delta and volume_delta > 0) else ("NEGATIVE" if (volume_delta and volume_delta < 0) else "UNAVAILABLE")

    return {
        "futures_price": fut_p,
        "spot_price": spot_price,
        "basis": basis,
        "basis_pct": basis_pct,
        "oi": current_oi,
        "oi_change": oi_diff,
        "buildup": buildup,
        "buildup_bias": buildup_bias,
        "vwap": vwap,
        "vwap_alignment": vwap_alignment,
        "order_flow_status": order_flow_status,
        "volume_delta": volume_delta,
    }


# ---------------------------------------------------------------------------
# 100-Point Confluence Scoring Engine
# ---------------------------------------------------------------------------

def calculate_smart_money_score(
    sma_data: Dict,
    obv_data: Dict,
    vol_data: Dict,
    zone_data: Dict,
    futures_data: Dict,
    supertrend_trend: str,
    direction: str,  # "BULLISH" or "BEARISH"
) -> Tuple[int, Dict[str, int]]:
    """
    100-Point Confluence Scoring:
      1. SMA trend & structure: 15 pts
      2. OBV accumulation/distribution: 15 pts
      3. Relative volume expansion: 10 pts
      4. Demand/supply zone proximity & response: 15 pts
      5. Equity-futures confirmation: 15 pts
      6. Supertrend alignment: 10 pts
      7. Order flow and OI buildup: 20 pts
      TOTAL: 100 pts.
    """
    breakdown = {}

    # 1. SMA Trend & Structure (15 pts)
    sma_score = 0
    p = sma_data["close"]
    s50 = sma_data.get("sma50")
    s100 = sma_data.get("sma100")
    s200 = sma_data.get("sma200")
    slope50 = sma_data.get("slope50", 0.0)
    slope100 = sma_data.get("slope100", 0.0)

    if direction == "BULLISH":
        if s50 and p > s50:
            sma_score += 4
        if s100 and p > s100:
            sma_score += 4
        if s200 and p > s200:
            sma_score += 3
        if s50 and s100 and s50 > s100:
            sma_score += 2
        if slope50 > 0 and slope100 > 0:
            sma_score += 2
    else:  # BEARISH
        if s50 and p < s50:
            sma_score += 4
        if s100 and p < s100:
            sma_score += 4
        if s200 and p < s200:
            sma_score += 3
        if s50 and s100 and s50 < s100:
            sma_score += 2
        if slope50 < 0 and slope100 < 0:
            sma_score += 2
    breakdown["sma_structure"] = sma_score

    # 2. OBV (15 pts)
    obv_score = 0
    if direction == "BULLISH":
        if obv_data.get("obv_trend") == "RISING":
            obv_score += 7
        if obv_data.get("bullish_div"):
            obv_score += 8
        elif obv_data.get("divergence") != "BEARISH":
            obv_score += 4
    else:
        if obv_data.get("obv_trend") == "FALLING":
            obv_score += 7
        if obv_data.get("bearish_div"):
            obv_score += 8
        elif obv_data.get("divergence") != "BULLISH":
            obv_score += 4
    breakdown["obv_accumulation"] = min(15, obv_score)

    # 3. Volume Expansion (10 pts)
    vol_score = 0
    if vol_data.get("vol_5_expanding"):
        vol_score += 5
    if vol_data.get("vol_above_20_avg"):
        vol_score += 5
    breakdown["volume_expansion"] = vol_score

    # 4. Demand / Supply Zone (15 pts)
    zone_score = 0
    if direction == "BULLISH":
        dist_d = abs(zone_data.get("dist_to_demand_pct", 99.0))
        d_status = zone_data.get("demand_status")
        if dist_d <= 2.5 or d_status in ("BOUNCING", "APPROACHING"):
            zone_score += 10
        elif dist_d <= 5.0:
            zone_score += 5
        if zone_data.get("supply_status") == "BROKEN":
            zone_score += 5
    else:
        dist_s = abs(zone_data.get("dist_to_supply_pct", 99.0))
        s_status = zone_data.get("supply_status")
        if dist_s <= 2.5 or s_status in ("REJECTING", "APPROACHING"):
            zone_score += 10
        elif dist_s <= 5.0:
            zone_score += 5
        if zone_data.get("demand_status") == "BROKEN":
            zone_score += 5
    breakdown["zone_confluence"] = min(15, zone_score)

    # 5. Equity-Futures Confirmation (15 pts)
    fut_score = 0
    vwap_align = futures_data.get("vwap_alignment")
    basis = futures_data.get("basis", 0.0)
    if direction == "BULLISH":
        if vwap_align == "ABOVE_VWAP":
            fut_score += 8
        elif vwap_align == "NEUTRAL":
            fut_score += 4
        if basis >= 0:  # Premium
            fut_score += 7
        else:
            fut_score += 3
    else:
        if vwap_align == "BELOW_VWAP":
            fut_score += 8
        elif vwap_align == "NEUTRAL":
            fut_score += 4
        if basis <= 0:  # Discount
            fut_score += 7
        else:
            fut_score += 3
    breakdown["equity_futures"] = min(15, fut_score)

    # 6. Supertrend (10 pts)
    st_score = 0
    if direction == "BULLISH" and supertrend_trend == "UP":
        st_score = 10
    elif direction == "BEARISH" and supertrend_trend == "DOWN":
        st_score = 10
    breakdown["supertrend"] = st_score

    # 7. Order Flow & OI Buildup (20 pts)
    of_score = 0
    buildup = futures_data.get("buildup")
    of_status = futures_data.get("order_flow_status")

    if direction == "BULLISH":
        if buildup == "LONG_BUILDUP":
            of_score += 12
        elif buildup == "SHORT_COVERING":
            of_score += 8
        if of_status == "POSITIVE":
            of_score += 8
        elif of_status == "UNAVAILABLE":
            of_score += 4  # neutral when data unavailable
    else:
        if buildup == "SHORT_BUILDUP":
            of_score += 12
        elif buildup == "LONG_UNWINDING":
            of_score += 8
        if of_status == "NEGATIVE":
            of_score += 8
        elif of_status == "UNAVAILABLE":
            of_score += 4
    breakdown["order_flow_oi"] = min(20, of_score)

    total_score = sum(breakdown.values())
    return total_score, breakdown


# ---------------------------------------------------------------------------
# Strategy E Evaluator
# ---------------------------------------------------------------------------

STRATEGY_E_ID   = "E"
STRATEGY_E_NAME = "Smart Money Accumulation & Distribution"


def evaluate_smart_money_setup(
    candles: List[Dict],
    symbol: str = "NIFTY",
    futures_symbol: Optional[str] = None,
    expiry: Optional[str] = None,
    spot_price: Optional[float] = None,
    futures_price: Optional[float] = None,
    prev_futures_price: Optional[float] = None,
    current_oi: Optional[int] = None,
    prev_oi: Optional[int] = None,
    futures_candles: Optional[List[Dict]] = None,
    candles_15m: Optional[List[Dict]] = None,
    candle_ts=None,
    supertrend_period: int = 10,
    supertrend_multiplier: float = 3.0,
    lot_size: int = 1,
) -> Dict:
    """
    Full evaluation of Strategy E (Smart Money Accumulation/Distribution).
    Strictly aligns daily macro structure with 15-minute intraday execution chart.
    """
    min_candles = 205
    if not candles or len(candles) < min_candles:
        return {
            "passed": False,
            "direction": "WAIT",
            "signal": None,
            "category": "INSUFFICIENT_DATA",
            "score": 0,
            "breakdown": {},
            "reason": f"Insufficient completed daily candles (need >= {min_candles}, got {len(candles) if candles else 0})",
        }

    closes = [c["close"] for c in candles]
    last_candle = candles[-1]
    curr_close = spot_price if (spot_price and spot_price > 0) else last_candle["close"]

    # 1. Moving Average Structure (Macro Swing Trend)
    sma50_series = compute_sma_series(closes, 50)
    sma100_series = compute_sma_series(closes, 100)
    sma200_series = compute_sma_series(closes, 200)

    sma50 = sma50_series[-1]
    sma100 = sma100_series[-1]
    sma200 = sma200_series[-1]
    slope50 = compute_sma_slope(sma50_series, 5)
    slope100 = compute_sma_slope(sma100_series, 5)
    slope200 = compute_sma_slope(sma200_series, 5)

    sma_data = {
        "close": curr_close,
        "sma50": round(sma50, 2) if sma50 else None,
        "sma100": round(sma100, 2) if sma100 else None,
        "sma200": round(sma200, 2) if sma200 else None,
        "slope50": round(slope50, 2),
        "slope100": round(slope100, 2),
        "slope200": round(slope200, 2),
    }

    # 2. OBV & Divergence
    obv_series = compute_obv_series(candles)
    obv_data = compute_obv_divergence(candles, obv_series, lookback=15)

    # 3. Volume Expansion
    vol_data = compute_volume_expansion(candles)

    # 4. Supply & Demand Zones
    zone_data = detect_supply_demand_zones(candles, lookback=50)

    # 5. Intraday 15m Execution Chart & Supertrend
    # When 15m candles are provided, execution-level Supertrend and VWAP MUST govern intraday direction
    fut_vwap = None
    if candles_15m and len(candles_15m) >= 10:
        st_series = futures_signal.compute_supertrend_series(candles_15m, supertrend_period, supertrend_multiplier)
        try:
            fut_vwap = futures_signal.compute_vwap_from_candles(candles_15m)
        except Exception:
            fut_vwap = None
    else:
        st_series = futures_signal.compute_supertrend_series(candles, supertrend_period, supertrend_multiplier)
        try:
            fut_vwap = futures_signal.compute_vwap_from_candles(futures_candles or candles)
        except Exception:
            fut_vwap = None

    st_last = st_series[-1] if (st_series and st_series[-1]) else None
    st_trend = st_last["trend"] if st_last else "UNKNOWN"
    st_val = round(st_last["supertrend"], 2) if st_last and st_last.get("supertrend") else None

    # 6. Futures VWAP & OI
    fut_data = analyze_futures_oi_flow(
        spot_price=curr_close,
        futures_price=futures_price,
        prev_futures_price=prev_futures_price,
        current_oi=current_oi,
        prev_oi=prev_oi,
        vwap=fut_vwap,
    )

    # Determine primary accumulation vs distribution inclination
    is_bull_inclined = (curr_close >= (sma50 or 0) or obv_data["bullish_div"] or zone_data["demand_status"] == "BOUNCING")
    is_bear_inclined = (curr_close <= (sma50 or 999999) or obv_data["bearish_div"] or zone_data["supply_status"] == "REJECTING")

    # Calculate both scores
    bull_score, bull_breakdown = calculate_smart_money_score(
        sma_data, obv_data, vol_data, zone_data, fut_data, st_trend, "BULLISH"
    )
    bear_score, bear_breakdown = calculate_smart_money_score(
        sma_data, obv_data, vol_data, zone_data, fut_data, st_trend, "BEARISH"
    )

    direction = "WAIT"
    score = 0
    breakdown = {}
    category = "WATCHLIST"
    signal_name = "WAIT"
    passed = False

    # Mandatory confirmations for BUY:
    # 1. Bull score >= 80
    # 2. 15m Supertrend MUST be UP! (If 15m Supertrend is DOWN, BUY is strictly blocked)
    # 3. Price must be at or above intraday VWAP
    # 4. OBV not falling
    # 5. Futures buildup not strictly bearish
    buy_mandatory = (
        bull_score >= 80
        and st_trend == "UP"
        and (fut_vwap is None or curr_close >= fut_vwap * 0.998)
        and obv_data["obv_trend"] != "FALLING"
        and fut_data["buildup"] != "SHORT_BUILDUP"
    )

    # Mandatory confirmations for SELL:
    # 1. Bear score >= 80
    # 2. 15m Supertrend MUST be DOWN! (If 15m Supertrend is UP, SELL is strictly blocked)
    # 3. Price must be at or below intraday VWAP
    # 4. OBV not rising
    # 5. Futures buildup not strictly bullish
    sell_mandatory = (
        bear_score >= 80
        and st_trend == "DOWN"
        and (fut_vwap is None or curr_close <= fut_vwap * 1.002)
        and obv_data["obv_trend"] != "RISING"
        and fut_data["buildup"] != "LONG_BUILDUP"
    )

    if buy_mandatory:
        direction = "BULLISH"
        score = bull_score
        breakdown = bull_breakdown
        signal_name = "BUY FUTURES – CONFIRMED"
        category = "CONFIRMED ACCUMULATION" if curr_close > (sma200 or 0) else "BREAKOUT READY"
        passed = True
    elif sell_mandatory:
        direction = "BEARISH"
        score = bear_score
        breakdown = bear_breakdown
        signal_name = "SELL FUTURES – CONFIRMED"
        category = "CONFIRMED DISTRIBUTION" if curr_close < (sma200 or 999999) else "BREAKDOWN READY"
        passed = True
    else:
        # Categorize into Early Setup / Watchlist / Wait
        # Must be strictly aligned with 15m chart direction
        if bull_score > bear_score and bull_score >= 65 and st_trend != "DOWN" and (fut_vwap is None or curr_close >= fut_vwap * 0.995):
            direction = "WAIT"
            score = bull_score
            breakdown = bull_breakdown
            category = "EARLY ACCUMULATION"
            signal_name = "EARLY ACCUMULATION – AWAIT CONFIRMATION"
        elif bear_score > bull_score and bear_score >= 65 and st_trend != "UP" and (fut_vwap is None or curr_close <= fut_vwap * 1.005):
            direction = "WAIT"
            score = bear_score
            breakdown = bear_breakdown
            category = "EARLY DISTRIBUTION"
            signal_name = "EARLY DISTRIBUTION – AWAIT CONFIRMATION"
        else:
            direction = "WAIT"
            score = max(bull_score, bear_score)
            breakdown = bull_breakdown if bull_score >= bear_score else bear_breakdown
            category = "WATCHLIST"
            signal_name = "WAIT"

    # Targets & Risk Parameters
    atr = (curr_close * 0.015)
    if direction == "BULLISH" or category in ("EARLY ACCUMULATION", "CONFIRMED ACCUMULATION", "BREAKOUT READY"):
        sl = round(min(zone_data["demand_level"], curr_close - 2 * atr), 2)
        risk = max(1.0, curr_close - sl)
        t1 = round(curr_close + 1.5 * risk, 2)
        t2 = round(curr_close + 2.5 * risk, 2)
        invalidation = round(sl * 0.995, 2)
    else:
        sl = round(max(zone_data["supply_level"], curr_close + 2 * atr), 2)
        risk = max(1.0, sl - curr_close)
        t1 = round(curr_close - 1.5 * risk, 2)
        t2 = round(curr_close - 2.5 * risk, 2)
        invalidation = round(sl * 1.005, 2)

    rr_ratio = round((abs(t1 - curr_close) / risk), 2) if risk > 0 else 1.5

    values = {
        "score": score,
        "category": category,
        "signal_name": signal_name,
        "direction": direction,
        "sma_data": sma_data,
        "obv_data": obv_data,
        "vol_data": vol_data,
        "zone_data": zone_data,
        "futures_data": fut_data,
        "supertrend": {"trend": st_trend, "val": st_val},
        "breakdown": breakdown,
        "entry_price": round(curr_close, 2),
        "sl": sl,
        "t1": t1,
        "t2": t2,
        "invalidation": invalidation,
        "risk_reward": f"1:{rr_ratio}",
    }

    signal = None
    if passed and direction in ("BULLISH", "BEARISH"):
        def _normalize_ts(val):
            if not val:
                return datetime.datetime.now()
            if isinstance(val, str):
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                    try:
                        val = datetime.datetime.strptime(val.split("+")[0].split(".")[0], fmt)
                        break
                    except Exception:
                        pass
            if isinstance(val, datetime.date) and not isinstance(val, datetime.datetime):
                return datetime.datetime.now()
            if isinstance(val, datetime.datetime):
                if val.hour == 0 and val.minute == 0 and val.second == 0:
                    return datetime.datetime.now()
                return val
            return datetime.datetime.now()

        ts = _normalize_ts(candle_ts)

        signal = {
            "id": f"E_{direction}_{symbol}_{ts.strftime('%Y%m%d_%H%M') if hasattr(ts, 'strftime') else ts}",
            "strategy_id": STRATEGY_E_ID,
            "strategy_name": STRATEGY_E_NAME,
            "direction": direction,
            "symbol": symbol,
            "futures_symbol": futures_symbol or f"{symbol} FUT",
            "expiry": str(expiry) if expiry else None,
            "timestamp": ts.isoformat() if hasattr(ts, 'isoformat') else str(ts),
            "spot_price": curr_close,
            "futures_price": fut_data["futures_price"],
            "score": score,
            "category": category,
            "signal_name": signal_name,
            "values": values,
            "status": "CONFIRMED",
        }

    return {
        "passed": passed,
        "direction": direction,
        "score": score,
        "category": category,
        "signal_name": signal_name,
        "values": values,
        "signal": signal,
    }
