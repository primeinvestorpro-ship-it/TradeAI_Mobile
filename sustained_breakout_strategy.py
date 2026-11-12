"""
sustained_breakout_strategy.py
------------------------------
Strategy F: NIFTY 50 Sustained Breakout/Breakdown + Futures & Options OI Confirmation Scanner.

Decision Sequence:
  1. Price breaks resistance / support on 5-minute chart.
  2. Sustained movement: Next 5-minute candle holds or successful retest.
  3. Indicator + volume check: EMA 10/21/50 alignment, Supertrend (7,3), Anchored VWAP, and Volume > 1.5x 20-MA.
  4. Futures confirmation: Long buildup / Short buildup with volume and OI expansion.
  5. Options chain confirmation: Put/Call writing, unwinding, and PCR alignment.
  6. Safety check: Invalidate confirmed signal if Volume or OI is missing/zero.
  7. 100-point weighted confluence scoring model.
  8. Hypothetical Entry, Structural SL, Target 1, Target 2, Risk/Reward.

DISCLAIMER: For educational and simulation analysis only. Not financial advice.
"""

from typing import Dict, List, Optional, Tuple
import datetime
import math
import futures_signal


STRATEGY_F_ID   = "F"
STRATEGY_F_NAME = "NIFTY Sustained Breakout + F&O OI"


# ---------------------------------------------------------------------------
# Indicator Calculations
# ---------------------------------------------------------------------------

def compute_avwap(candles: List[Dict], anchor_idx: int = 0) -> List[Optional[float]]:
    """
    Computes Anchored VWAP from anchor_idx onwards.
    AVWAP = Cumulative(Typical_Price * Volume) / Cumulative(Volume).
    Returns list of float (or None if volume <= 0).
    """
    n = len(candles)
    avwap_series: List[Optional[float]] = [None] * n
    if n == 0 or anchor_idx >= n:
        return avwap_series

    cum_vol = 0.0
    cum_tp_vol = 0.0

    for i in range(anchor_idx, n):
        c = candles[i]
        vol = float(c.get("volume", 0) or 0)
        tp = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0
        cum_vol += vol
        cum_tp_vol += tp * vol
        if cum_vol > 0:
            avwap_series[i] = round(cum_tp_vol / cum_vol, 2)
        else:
            avwap_series[i] = None

    return avwap_series


def compute_volume_ma(candles: List[Dict], period: int = 20) -> List[float]:
    """Computes simple moving average of volume."""
    vols = [float(c.get("volume", 0) or 0) for c in candles]
    res = []
    for i in range(len(vols)):
        if i + 1 < period:
            window = vols[: i + 1]
        else:
            window = vols[i + 1 - period : i + 1]
        res.append(sum(window) / len(window) if window else 0.0)
    return res


def detect_support_resistance(candles: List[Dict], lookback: int = 25) -> Tuple[float, float, int, int]:
    """
    Identifies recent consolidation resistance (highest high) and support (lowest low)
    excluding the latest 2 candles to detect active breakouts.
    Returns (resistance_level, support_level, res_idx, sup_idx).
    """
    if len(candles) < 5:
        c0 = candles[-1]
        return c0["high"], c0["low"], len(candles) - 1, len(candles) - 1

    eval_window = candles[-lookback - 2 : -2] if len(candles) >= lookback + 2 else candles[:-2]
    if not eval_window:
        eval_window = candles[:-1]

    res_level = max(c["high"] for c in eval_window)
    sup_level = min(c["low"] for c in eval_window)
    res_idx = max(i for i, c in enumerate(candles) if c["high"] == res_level)
    sup_idx = min(i for i, c in enumerate(candles) if c["low"] == sup_level)

    return res_level, sup_level, res_idx, sup_idx


# ---------------------------------------------------------------------------
# Breakout & Retest Sustainability
# ---------------------------------------------------------------------------

def check_sustained_breakout(candles: List[Dict], resistance: float) -> Tuple[bool, str, Dict]:
    """
    Checks if resistance breakout is sustained on 5m candles:
      1. Breakout candle closes above resistance.
      2. The subsequent candle holds above resistance OR a successful retest holds level.
      3. No immediate reversal close back below resistance.
    """
    if len(candles) < 3:
        return False, "Insufficient candles", {}

    c_curr = candles[-1]
    c_prev = candles[-2]
    c_prev2 = candles[-3] if len(candles) >= 3 else c_prev

    breakout_cand = None
    sustained = False
    retest_success = False
    status = "NO_BREAKOUT"

    # Case A: Previous candle broke out, current candle holds
    if c_prev["close"] > resistance:
        breakout_cand = c_prev
        # Current holds above breakout level or retested and bounced
        if c_curr["close"] >= resistance * 0.999:
            sustained = True
            if c_curr["low"] <= resistance * 1.001 and c_curr["close"] > resistance:
                retest_success = True
            status = "SUSTAINED_HOLD" if not retest_success else "SUCCESSFUL_RETEST"
    # Case B: Candle -2 broke out, -1 held/retested, current continues
    elif c_prev2["close"] > resistance and c_prev["close"] >= resistance * 0.999:
        breakout_cand = c_prev2
        if c_curr["close"] >= resistance:
            sustained = True
            status = "SUSTAINED_CONTINUATION"

    # Check if latest candle closed back into range (failed breakout)
    if sustained and c_curr["close"] < resistance * 0.9985:
        sustained = False
        status = "FAILED_BREAKOUT"

    details = {
        "resistance": resistance,
        "breakout_candle_high": breakout_cand["high"] if breakout_cand else None,
        "breakout_candle_close": breakout_cand["close"] if breakout_cand else None,
        "retest_success": retest_success,
        "status": status,
    }
    return sustained, status, details


def check_sustained_breakdown(candles: List[Dict], support: float) -> Tuple[bool, str, Dict]:
    """
    Checks if support breakdown is sustained on 5m candles:
      1. Breakdown candle closes below support.
      2. Subsequent candle holds below support OR breakdown retest fails.
      3. No immediate reversal close back above support.
    """
    if len(candles) < 3:
        return False, "Insufficient candles", {}

    c_curr = candles[-1]
    c_prev = candles[-2]
    c_prev2 = candles[-3] if len(candles) >= 3 else c_prev

    breakdown_cand = None
    sustained = False
    retest_failed = False
    status = "NO_BREAKDOWN"

    # Case A: Previous candle broke down, current candle holds below
    if c_prev["close"] < support:
        breakdown_cand = c_prev
        if c_curr["close"] <= support * 1.001:
            sustained = True
            if c_curr["high"] >= support * 0.999 and c_curr["close"] < support:
                retest_failed = True
            status = "SUSTAINED_HOLD" if not retest_failed else "RETEST_REJECTED"
    # Case B: Candle -2 broke down, -1 held, current continues
    elif c_prev2["close"] < support and c_prev["close"] <= support * 1.001:
        breakdown_cand = c_prev2
        if c_curr["close"] <= support:
            sustained = True
            status = "SUSTAINED_CONTINUATION"

    # Check if latest candle closed back into range (failed breakdown)
    if sustained and c_curr["close"] > support * 1.0015:
        sustained = False
        status = "FAILED_BREAKDOWN"

    details = {
        "support": support,
        "breakdown_candle_low": breakdown_cand["low"] if breakdown_cand else None,
        "breakdown_candle_close": breakdown_cand["close"] if breakdown_cand else None,
        "retest_failed": retest_failed,
        "status": status,
    }
    return sustained, status, details


# ---------------------------------------------------------------------------
# Options Chain Confirmation Analysis
# ---------------------------------------------------------------------------

def analyze_options_chain_confluence(
    options_chain: Optional[List[Dict]],
    spot_price: float,
    direction: str,
) -> Dict:
    """
    Analyzes Options Chain around ATM (+/- 2 strikes) for Put/Call writing,
    OI unwinding, PCR, and directional positioning.
    """
    if not options_chain:
        return {
            "status": "UNAVAILABLE",
            "pcr": None,
            "call_oi_total": 0,
            "put_oi_total": 0,
            "call_oi_change": 0,
            "put_oi_change": 0,
            "bias": "NEUTRAL",
            "score": 0,
            "desc": "Options chain data not available",
        }

    # Sort strikes and find ATM
    strikes = sorted(options_chain, key=lambda x: abs(float(x.get("strike", 0)) - spot_price))
    near_strikes = strikes[:5]  # ATM +/- 2 strikes

    call_oi_total = sum(float(s.get("ce_oi", 0) or 0) for s in near_strikes)
    put_oi_total = sum(float(s.get("pe_oi", 0) or 0) for s in near_strikes)
    call_change = sum(float(s.get("ce_change_oi", 0) or 0) for s in near_strikes)
    put_change = sum(float(s.get("pe_change_oi", 0) or 0) for s in near_strikes)

    pcr = round(put_oi_total / call_oi_total, 2) if call_oi_total > 0 else 1.0

    # Bullish options setup: Put writing > Call writing (or Call unwinding)
    is_bull_opts = (put_change > call_change and put_change > 0) or (call_change < 0 and put_change >= 0) or (pcr > 1.1)
    # Bearish options setup: Call writing > Put writing (or Put unwinding)
    is_bear_opts = (call_change > put_change and call_change > 0) or (put_change < 0 and call_change >= 0) or (pcr < 0.9)

    score = 0
    bias = "NEUTRAL"
    desc = "Mixed options positioning"

    if direction == "LONG":
        if is_bull_opts:
            score = 10
            bias = "BULLISH_CONFIRMED"
            desc = f"Put writing dominant (PE dOI: {int(put_change)} vs CE dOI: {int(call_change)}), PCR: {pcr}"
        elif not is_bear_opts:
            score = 5
            bias = "NEUTRAL"
            desc = f"Neutral options positioning, PCR: {pcr}"
        else:
            score = 0
            bias = "BEARISH_DIVERGENCE"
            desc = f"Heavy Call writing overhead (CE dOI: {int(call_change)}), opposing LONG"
    elif direction == "SHORT":
        if is_bear_opts:
            score = 10
            bias = "BEARISH_CONFIRMED"
            desc = f"Call writing dominant (CE dOI: {int(call_change)} vs PE dOI: {int(put_change)}), PCR: {pcr}"
        elif not is_bull_opts:
            score = 5
            bias = "NEUTRAL"
            desc = f"Neutral options positioning, PCR: {pcr}"
        else:
            score = 0
            bias = "BULLISH_DIVERGENCE"
            desc = f"Heavy Put writing support (PE dOI: {int(put_change)}), opposing SHORT"

    return {
        "status": "AVAILABLE",
        "pcr": pcr,
        "call_oi_total": call_oi_total,
        "put_oi_total": put_oi_total,
        "call_oi_change": call_change,
        "put_oi_change": put_change,
        "bias": bias,
        "score": score,
        "desc": desc,
    }


# ---------------------------------------------------------------------------
# 100-Point Weighted Scoring Model
# ---------------------------------------------------------------------------

def calculate_confluence_score(
    ema_data: Dict,
    st_data: Dict,
    avwap_data: Dict,
    breakout_data: Dict,
    volume_data: Dict,
    futures_data: Dict,
    options_data: Dict,
    direction: str,
) -> Tuple[int, Dict[str, int]]:
    """
    Weighted 100-Point Scoring Model:
      - EMA trend alignment: 15
      - Supertrend confirmation: 10
      - AVWAP confirmation: 10
      - Breakout/breakdown sustainability: 20
      - Volume confirmation: 15
      - Futures price & OI confirmation: 15
      - Options-chain confirmation: 10
      - Market structure & R:R: 5
    """
    breakdown: Dict[str, int] = {}

    # 1. EMA Trend Alignment (15 pts)
    # EMA 10, EMA 21, EMA 50 stacked and rising/declining
    ema_score = 0
    if direction == "LONG":
        if ema_data["stacked_bull"]:
            ema_score += 10
        if ema_data["rising"]:
            ema_score += 5
    else:
        if ema_data["stacked_bear"]:
            ema_score += 10
        if ema_data["declining"]:
            ema_score += 5
    breakdown["ema_trend"] = ema_score

    # 2. Supertrend Confirmation (10 pts)
    st_score = 0
    if direction == "LONG" and st_data["trend"] == "UP":
        st_score = 10
    elif direction == "SHORT" and st_data["trend"] == "DOWN":
        st_score = 10
    breakdown["supertrend"] = st_score

    # 3. AVWAP Confirmation (10 pts)
    avwap_score = 0
    if direction == "LONG" and avwap_data["above_avwap"]:
        avwap_score = 10
    elif direction == "SHORT" and avwap_data["below_avwap"]:
        avwap_score = 10
    breakdown["avwap"] = avwap_score

    # 4. Breakout / Breakdown Sustainability (20 pts)
    bo_score = 0
    if breakout_data["sustained"]:
        bo_score += 15
        if breakout_data.get("retest_confirmed"):
            bo_score += 5
        elif breakout_data.get("holds_above_or_below"):
            bo_score += 5
    breakdown["sustainability"] = bo_score

    # 5. Volume Confirmation (15 pts)
    vol_score = 0
    ratio = volume_data.get("vol_ratio", 0.0)
    if ratio >= 1.5:
        vol_score = 15
    elif ratio >= 1.2:
        vol_score = 10
    elif ratio >= 1.0:
        vol_score = 5
    breakdown["volume"] = vol_score

    # 6. Futures Price & OI Confirmation (15 pts)
    fut_score = 0
    buildup = futures_data.get("buildup")
    if direction == "LONG":
        if buildup == "LONG_BUILDUP":
            fut_score = 15
        elif buildup == "SHORT_COVERING":
            fut_score = 10
    else:
        if buildup == "SHORT_BUILDUP":
            fut_score = 15
        elif buildup == "LONG_UNWINDING":
            fut_score = 8
    breakdown["futures_oi"] = fut_score

    # 7. Options-Chain Confirmation (10 pts)
    breakdown["options_chain"] = options_data.get("score", 0)

    # 8. Market Structure & Risk/Reward (5 pts)
    rr_score = 5 if breakout_data.get("rr_valid", True) else 0
    breakdown["market_structure"] = rr_score

    total_score = sum(breakdown.values())
    return total_score, breakdown


# ---------------------------------------------------------------------------
# Main Strategy F Evaluator
# ---------------------------------------------------------------------------

def evaluate_sustained_breakout_strategy(
    candles_5m: List[Dict],
    symbol: str = "NIFTY",
    spot_price: Optional[float] = None,
    futures_symbol: Optional[str] = None,
    futures_price: Optional[float] = None,
    prev_futures_price: Optional[float] = None,
    futures_volume: Optional[int] = None,
    current_oi: Optional[int] = None,
    prev_oi: Optional[int] = None,
    options_chain: Optional[List[Dict]] = None,
    futures_candles_5m: Optional[List[Dict]] = None,
    candle_ts=None,
    supertrend_period: int = 7,
    supertrend_multiplier: float = 3.0,
) -> Dict:
    """
    Evaluates Strategy F: Sustained Breakout/Breakdown with Derivatives Confirmation.
    Follows:
      Price Action -> Sustained Confirmation -> Indicators -> Volume -> Futures OI -> Options Chain -> Target/SL.
    """
    min_candles = 55
    if not candles_5m or len(candles_5m) < min_candles:
        return {
            "passed": False,
            "direction": "NO TRADE",
            "score": 0,
            "signal": None,
            "status": "INSUFFICIENT_DATA",
            "setup_status": "INSUFFICIENT_DATA",
            "reasons_for": [],
            "reasons_against": [f"Need >= {min_candles} 5m candles, got {len(candles_5m) if candles_5m else 0}"],
            "values": {},
        }

    # If spot candles have zero volume (e.g. index spot like NIFTY 50), borrow volume from futures candles
    fut_vol_map = {}
    if futures_candles_5m:
        for fc in futures_candles_5m:
            ts = fc.get("date")
            if ts:
                k = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)[:16]
                fut_vol_map[k] = fc.get("volume", 0)

    eval_candles = []
    has_any_spot_vol = any(float(c.get("volume", 0) or 0) > 0 for c in candles_5m[-10:])
    for i, c in enumerate(candles_5m):
        c_copy = dict(c)
        if not has_any_spot_vol and futures_candles_5m:
            ts = c.get("date")
            k = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)[:16]
            if k in fut_vol_map:
                c_copy["volume"] = fut_vol_map[k]
            else:
                offset = len(candles_5m) - 1 - i
                fut_idx = len(futures_candles_5m) - 1 - offset
                if 0 <= fut_idx < len(futures_candles_5m):
                    c_copy["volume"] = futures_candles_5m[fut_idx].get("volume", 0)
        eval_candles.append(c_copy)

    closes = [float(c["close"]) for c in eval_candles]
    last_candle = eval_candles[-1]
    curr_close = float(spot_price) if (spot_price and spot_price > 0) else last_candle["close"]

    # --- SAFETY RULE: Volume & Data Availability Check ---
    last_vol = float(last_candle.get("volume", 0) or 0)
    has_valid_volume = last_vol > 0
    has_valid_oi = current_oi is not None and current_oi > 0

    reasons_against: List[str] = []
    reasons_for: List[str] = []

    if not has_valid_volume:
        reasons_against.append("Volume Not Available on current candle — signals strictly invalidated as per safety rule")
    if not has_valid_oi:
        reasons_against.append("Futures Open Interest data unavailable — signals strictly invalidated as per safety rule")

    # 1. EMAs (10, 21, 50)
    ema10_series = futures_signal.compute_ema_series(closes, 10)
    ema21_series = futures_signal.compute_ema_series(closes, 21)
    ema50_series = futures_signal.compute_ema_series(closes, 50)

    ema10 = ema10_series[-1]
    ema21 = ema21_series[-1]
    ema50 = ema50_series[-1]

    ema10_rising = len(ema10_series) >= 2 and ema10_series[-1] > ema10_series[-2]
    ema21_rising = len(ema21_series) >= 2 and ema21_series[-1] > ema21_series[-2]
    ema10_declining = len(ema10_series) >= 2 and ema10_series[-1] < ema10_series[-2]
    ema21_declining = len(ema21_series) >= 2 and ema21_series[-1] < ema21_series[-2]

    stacked_bull = (curr_close > ema10 > ema21 > ema50)
    stacked_bear = (curr_close < ema10 < ema21 < ema50)

    ema_extension_pct = abs(curr_close - ema10) / ema10 * 100.0 if ema10 else 0.0
    is_overextended = ema_extension_pct > 1.25  # Extended too far from EMA 10

    if is_overextended:
        reasons_against.append(f"Price is overextended from EMA 10 ({ema_extension_pct:.2f}% > 1.25%)")

    ema_data = {
        "ema10": round(ema10, 2),
        "ema21": round(ema21, 2),
        "ema50": round(ema50, 2),
        "stacked_bull": stacked_bull,
        "stacked_bear": stacked_bear,
        "rising": ema10_rising and ema21_rising,
        "declining": ema10_declining and ema21_declining,
        "extension_pct": round(ema_extension_pct, 2),
    }

    # 2. Supertrend (7, 3)
    st_series = futures_signal.compute_supertrend_series(eval_candles, supertrend_period, supertrend_multiplier)
    st_last = st_series[-1] if (st_series and st_series[-1]) else None
    st_trend = st_last["trend"] if st_last else "UNKNOWN"
    st_val = round(st_last["supertrend"], 2) if st_last and st_last.get("supertrend") else None

    st_data = {"trend": st_trend, "val": st_val}

    # 3. Anchored VWAP (AVWAP) anchored from today's open (or index 0)
    avwap_series = compute_avwap(eval_candles, anchor_idx=max(0, len(eval_candles) - 75))
    avwap_val = avwap_series[-1] if (avwap_series and avwap_series[-1]) else None
    above_avwap = avwap_val is not None and curr_close > avwap_val
    below_avwap = avwap_val is not None and curr_close < avwap_val

    avwap_data = {
        "avwap": avwap_val,
        "above_avwap": above_avwap,
        "below_avwap": below_avwap,
    }

    # 4. Support & Resistance + Breakout Detection
    res_level, sup_level, res_idx, sup_idx = detect_support_resistance(eval_candles, lookback=30)
    bull_sustained, bull_bo_status, bull_bo_details = check_sustained_breakout(eval_candles, res_level)
    bear_sustained, bear_bo_status, bear_bo_details = check_sustained_breakdown(eval_candles, sup_level)

    # 5. Volume Confirmation (20-MA)
    vol_ma_series = compute_volume_ma(eval_candles, period=20)
    vol_ma20 = vol_ma_series[-1] if vol_ma_series else 0.0
    vol_ratio = round(last_vol / vol_ma20, 2) if vol_ma20 > 0 else 0.0

    volume_data = {
        "current_vol": int(last_vol),
        "vol_ma20": round(vol_ma20, 1),
        "vol_ratio": vol_ratio,
        "is_volume_confirmed": vol_ratio >= 1.5,
    }

    # 6. Futures OI & Flow
    fut_p = float(futures_price) if (futures_price and futures_price > 0) else curr_close
    prev_fut_p = float(prev_futures_price) if (prev_futures_price and prev_futures_price > 0) else fut_p
    d_fut_p = fut_p - prev_fut_p
    d_oi = (current_oi - prev_oi) if (current_oi is not None and prev_oi is not None) else 0

    if d_fut_p >= 0 and d_oi > 0:
        buildup = "LONG_BUILDUP"
    elif d_fut_p >= 0 and d_oi <= 0:
        buildup = "SHORT_COVERING"
    elif d_fut_p < 0 and d_oi > 0:
        buildup = "SHORT_BUILDUP"
    else:
        buildup = "LONG_UNWINDING"

    futures_data = {
        "futures_symbol": futures_symbol or f"{symbol} FUT",
        "futures_price": fut_p,
        "futures_volume": futures_volume,
        "current_oi": current_oi,
        "prev_oi": prev_oi,
        "change_oi": d_oi,
        "buildup": buildup,
    }

    # 7. Options Chain Confluence
    opt_long = analyze_options_chain_confluence(options_chain, curr_close, "LONG")
    opt_short = analyze_options_chain_confluence(options_chain, curr_close, "SHORT")

    # 8. Risk / Reward & Targets
    atr = curr_close * 0.005  # ~0.5% default for 5m NIFTY
    long_sl = round(min(res_level * 0.998, ema21, curr_close - atr), 2)
    long_risk = max(1.0, curr_close - long_sl)
    long_t1 = round(curr_close + 1.5 * long_risk, 2)
    long_t2 = round(curr_close + 2.5 * long_risk, 2)

    short_sl = round(max(sup_level * 1.002, ema21, curr_close + atr), 2)
    short_risk = max(1.0, short_sl - curr_close)
    short_t1 = round(curr_close - 1.5 * short_risk, 2)
    short_t2 = round(curr_close - 2.5 * short_risk, 2)

    long_breakout_data = {
        "sustained": bull_sustained,
        "status": bull_bo_status,
        "holds_above_or_below": curr_close > res_level,
        "retest_confirmed": bull_bo_details.get("retest_success", False),
        "rr_valid": (long_t1 - curr_close) / long_risk >= 1.4,
    }

    short_breakout_data = {
        "sustained": bear_sustained,
        "status": bear_bo_status,
        "holds_above_or_below": curr_close < sup_level,
        "retest_confirmed": bear_bo_details.get("retest_failed", False),
        "rr_valid": (curr_close - short_t1) / short_risk >= 1.4,
    }

    # Calculate Long and Short Confluence Scores
    long_score, long_breakdown = calculate_confluence_score(
        ema_data, st_data, avwap_data, long_breakout_data, volume_data, futures_data, opt_long, "LONG"
    )
    short_score, short_breakdown = calculate_confluence_score(
        ema_data, st_data, avwap_data, short_breakout_data, volume_data, futures_data, opt_short, "SHORT"
    )

    # Compile reasons
    if stacked_bull and ema10_rising:
        reasons_for.append(f"EMA 10/21/50 bullish alignment ({ema10:.1f} > {ema21:.1f} > {ema50:.1f})")
    elif stacked_bear and ema10_declining:
        reasons_for.append(f"EMA 10/21/50 bearish alignment ({ema10:.1f} < {ema21:.1f} < {ema50:.1f})")

    if st_trend == "UP":
        reasons_for.append(f"Supertrend GREEN (UP @ {st_val})")
    elif st_trend == "DOWN":
        reasons_for.append(f"Supertrend RED (DOWN @ {st_val})")

    if above_avwap:
        reasons_for.append(f"Price above AVWAP ({avwap_val})")
    elif below_avwap:
        reasons_for.append(f"Price below AVWAP ({avwap_val})")

    if vol_ratio >= 1.5:
        reasons_for.append(f"Volume expansion confirmed ({vol_ratio}x 20-MA)")
    else:
        reasons_against.append(f"Volume below 1.5x average ({vol_ratio}x 20-MA)")

    if buildup in ("LONG_BUILDUP", "SHORT_BUILDUP"):
        reasons_for.append(f"Futures confirmation: {buildup}")
    else:
        reasons_against.append(f"Sub-optimal futures buildup: {buildup}")

    # Mandatory decision criteria
    # Long Setup requires:
    # 1. Price action sustained breakout above resistance
    # 2. EMA stacked bullish & rising
    # 3. Supertrend UP
    # 4. Price > AVWAP
    # 5. Volume > 1.2x (preferably 1.5x)
    # 6. Futures not short buildup
    # 7. Data must be valid (volume & OI)
    # 8. Score >= 75 (Strong: >= 85)
    long_confirmed = (
        has_valid_volume
        and has_valid_oi
        and not is_overextended
        and bull_sustained
        and stacked_bull
        and st_trend == "UP"
        and above_avwap
        and buildup != "SHORT_BUILDUP"
        and long_score >= 75
    )

    short_confirmed = (
        has_valid_volume
        and has_valid_oi
        and not is_overextended
        and bear_sustained
        and stacked_bear
        and st_trend == "DOWN"
        and below_avwap
        and buildup != "LONG_BUILDUP"
        and short_score >= 75
    )

    direction = "NO TRADE"
    confidence_score = max(long_score, short_score)
    breakdown = long_breakdown if long_score >= short_score else short_breakdown
    passed = False
    setup_status = "NO TRADE"
    entry_p = curr_close
    sl_p = long_sl if long_score >= short_score else short_sl
    t1_p = long_t1 if long_score >= short_score else short_t1
    t2_p = long_t2 if long_score >= short_score else short_t2
    level_broken = res_level if long_score >= short_score else sup_level

    if long_confirmed:
        direction = "LONG"
        passed = True
        confidence_score = long_score
        breakdown = long_breakdown
        setup_status = "STRONG CONFIRMED LONG" if long_score >= 85 else "VALID LONG SETUP"
        entry_p = curr_close
        sl_p = long_sl
        t1_p = long_t1
        t2_p = long_t2
        level_broken = res_level
    elif short_confirmed:
        direction = "SHORT"
        passed = True
        confidence_score = short_score
        breakdown = short_breakdown
        setup_status = "STRONG CONFIRMED SHORT" if short_score >= 85 else "VALID SHORT SETUP"
        entry_p = curr_close
        sl_p = short_sl
        t1_p = short_t1
        t2_p = short_t2
        level_broken = sup_level
    else:
        # Check Watchlist condition
        if confidence_score >= 60 and (has_valid_volume and has_valid_oi):
            setup_status = "WATCHLIST ONLY"
        else:
            setup_status = "NO TRADE"

    values = {
        "spot_price": curr_close,
        "entry_price": entry_p,
        "breakout_level": level_broken,
        "stop_loss": sl_p,
        "target1": t1_p,
        "target2": t2_p,
        "risk_reward": "1:1.5",
        "confidence_score": confidence_score,
        "setup_status": setup_status,
        "ema_data": ema_data,
        "supertrend_data": st_data,
        "avwap_data": avwap_data,
        "volume_data": volume_data,
        "futures_data": futures_data,
        "options_data": opt_long if direction == "LONG" else opt_short,
        "breakdown": breakdown,
        "resistance": res_level,
        "support": sup_level,
        "data_valid": has_valid_volume and has_valid_oi,
        "reasons_for": reasons_for,
        "reasons_against": reasons_against,
    }

    signal = None
    if passed and direction in ("LONG", "SHORT"):
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
        sig_id = f"F_{direction}_{symbol}_{ts.strftime('%Y%m%d_%H%M%S') if hasattr(ts, 'strftime') else ts}"
        signal = {
            "id": sig_id,
            "strategy_id": STRATEGY_F_ID,
            "strategy_name": STRATEGY_F_NAME,
            "direction": "BULLISH" if direction == "LONG" else "BEARISH",
            "symbol": symbol,
            "futures_symbol": futures_symbol or f"{symbol} FUT",
            "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "spot_price": curr_close,
            "futures_price": fut_p,
            "confidence_score": confidence_score,
            "setup_status": setup_status,
            "entry_price": entry_p,
            "stop_loss": sl_p,
            "target1": t1_p,
            "target2": t2_p,
            "risk_reward": "1:1.5",
            "values": values,
            "status": "CONFIRMED",
        }

    return {
        "passed": passed,
        "direction": direction,
        "score": confidence_score,
        "setup_status": setup_status,
        "signal": signal,
        "values": values,
        "reasons_for": reasons_for,
        "reasons_against": reasons_against,
    }
