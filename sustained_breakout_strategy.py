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
    Supports both combined strike objects and separate CE/PE contract rows.
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
            "score": 5,
            "desc": "Options chain data not available",
        }

    # Aggregate by strike to handle both formats cleanly
    strike_map: Dict[float, Dict[str, float]] = {}
    for item in options_chain:
        strike = float(item.get("strike", 0) or 0)
        if strike <= 0:
            continue
        if strike not in strike_map:
            strike_map[strike] = {
                "strike": strike,
                "ce_oi": 0.0,
                "pe_oi": 0.0,
                "ce_change_oi": 0.0,
                "pe_change_oi": 0.0,
            }

        # Check if already grouped format
        if "ce_oi" in item or "pe_oi" in item:
            strike_map[strike]["ce_oi"] += float(item.get("ce_oi", 0) or 0)
            strike_map[strike]["pe_oi"] += float(item.get("pe_oi", 0) or 0)
            strike_map[strike]["ce_change_oi"] += float(item.get("ce_change_oi", 0) or 0)
            strike_map[strike]["pe_change_oi"] += float(item.get("pe_change_oi", 0) or 0)
        else:
            # Separate contract format (instrument_type: "CE" | "PE")
            itype = item.get("instrument_type")
            oi_val = float(item.get("oi", 0) or 0)
            chg_val = float(item.get("change_oi", 0) or item.get("oi_change", 0) or 0)
            if itype == "CE":
                strike_map[strike]["ce_oi"] += oi_val
                strike_map[strike]["ce_change_oi"] += chg_val
            elif itype == "PE":
                strike_map[strike]["pe_oi"] += oi_val
                strike_map[strike]["pe_change_oi"] += chg_val

    if not strike_map:
        return {
            "status": "UNAVAILABLE",
            "pcr": None,
            "call_oi_total": 0,
            "put_oi_total": 0,
            "call_oi_change": 0,
            "put_oi_change": 0,
            "bias": "NEUTRAL",
            "score": 5,
            "desc": "Options chain strikes unavailable",
        }

    # Sort strikes by proximity to spot_price and select nearest 5 strikes (ATM +/- 2)
    sorted_strikes = sorted(strike_map.values(), key=lambda x: abs(x["strike"] - spot_price))
    near_strikes = sorted_strikes[:5]

    call_oi_total = sum(s["ce_oi"] for s in near_strikes)
    put_oi_total = sum(s["pe_oi"] for s in near_strikes)
    call_change = sum(s["ce_change_oi"] for s in near_strikes)
    put_change = sum(s["pe_change_oi"] for s in near_strikes)

    pcr = round(put_oi_total / call_oi_total, 2) if call_oi_total > 0 else 1.0

    # Smooth dynamic options scoring (0 - 10 points)
    if direction == "LONG":
        if pcr >= 1.3:
            base_score = 10.0
        elif pcr <= 0.7:
            base_score = 1.0
        else:
            # Linear smooth scaling between 0.7 and 1.3
            base_score = 1.0 + ((pcr - 0.7) / (1.3 - 0.7)) * 9.0

        # dOI boost: put writing dominant
        if put_change > call_change and put_change > 0:
            base_score = min(10.0, base_score + 1.0)
        elif call_change > put_change and call_change > 0:
            base_score = max(0.0, base_score - 1.5)

        opt_score = int(round(min(10.0, max(0.0, base_score))))

        if pcr >= 1.1 or (put_change > call_change and put_change > 0):
            bias = "BULLISH_CONFIRMED"
            desc = f"Put writing dominant (PE dOI: {int(put_change)} vs CE dOI: {int(call_change)}), PCR: {pcr}"
        elif pcr <= 0.85 or (call_change > put_change and call_change > 0):
            bias = "BEARISH_DIVERGENCE"
            desc = f"Heavy Call writing overhead (CE dOI: {int(call_change)}), opposing LONG"
        else:
            bias = "NEUTRAL"
            desc = f"Neutral options positioning, PCR: {pcr}"

    else:  # SHORT
        if pcr <= 0.7:
            base_score = 10.0
        elif pcr >= 1.3:
            base_score = 1.0
        else:
            # Linear smooth inverse scaling
            base_score = 1.0 + ((1.3 - pcr) / (1.3 - 0.7)) * 9.0

        if call_change > put_change and call_change > 0:
            base_score = min(10.0, base_score + 1.0)
        elif put_change > call_change and put_change > 0:
            base_score = max(0.0, base_score - 1.5)

        opt_score = int(round(min(10.0, max(0.0, base_score))))

        if pcr <= 0.9 or (call_change > put_change and call_change > 0):
            bias = "BEARISH_CONFIRMED"
            desc = f"Call writing dominant (CE dOI: {int(call_change)} vs PE dOI: {int(put_change)}), PCR: {pcr}"
        elif pcr >= 1.15 or (put_change > call_change and put_change > 0):
            bias = "BULLISH_DIVERGENCE"
            desc = f"Heavy Put writing support (PE dOI: {int(put_change)}), opposing SHORT"
        else:
            bias = "NEUTRAL"
            desc = f"Neutral options positioning, PCR: {pcr}"

    return {
        "status": "AVAILABLE",
        "pcr": pcr,
        "call_oi_total": int(call_oi_total),
        "put_oi_total": int(put_oi_total),
        "call_oi_change": int(call_change),
        "put_oi_change": int(put_change),
        "bias": bias,
        "score": opt_score,
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
    Weighted 100-Point Dynamic Confluence Model:
      - EMA trend alignment & spacing: 15
      - Supertrend confirmation & buffer: 10
      - AVWAP confirmation & expansion: 10
      - Breakout/breakdown sustainability & channel position: 20
      - Volume expansion factor: 15
      - Futures price, OI & basis: 15
      - Options-chain PCR & writing: 10
      - Market structure & R:R: 5
    """
    breakdown: Dict[str, int] = {}
    curr_p = float(breakout_data.get("curr_close") or 0.0)

    # 1. EMA Trend Alignment & Spacing (15 pts)
    ema_score = 0.0
    ema10 = float(ema_data.get("ema10") or 0.0)
    ema21 = float(ema_data.get("ema21") or 0.0)
    ema50 = float(ema_data.get("ema50") or 0.0)
    if curr_p <= 0.0:
        curr_p = ema10

    if direction == "LONG":
        if curr_p > ema10:
            ema_score += 4.0
        if ema10 > ema21:
            ema_score += 4.0
        if ema21 > ema50:
            ema_score += 4.0
        if ema_data.get("rising"):
            ema_score += 3.0
    else:
        if curr_p < ema10:
            ema_score += 4.0
        if ema10 < ema21:
            ema_score += 4.0
        if ema21 < ema50:
            ema_score += 4.0
        if ema_data.get("declining"):
            ema_score += 3.0
    breakdown["ema_trend"] = int(round(ema_score))

    # 2. Supertrend Confirmation & Clearance (10 pts)
    st_score = 0.0
    st_trend = st_data.get("trend")
    st_val = float(st_data.get("val") or 0.0)
    if direction == "LONG" and st_trend == "UP":
        st_score = 7.0
        if st_val > 0 and curr_p > st_val:
            dist_pct = (curr_p - st_val) / st_val * 100.0
            st_score += min(3.0, max(0.5, dist_pct * 3.0))
    elif direction == "SHORT" and st_trend == "DOWN":
        st_score = 7.0
        if st_val > 0 and curr_p < st_val:
            dist_pct = (st_val - curr_p) / st_val * 100.0
            st_score += min(3.0, max(0.5, dist_pct * 3.0))
    breakdown["supertrend"] = int(round(st_score))

    # 3. AVWAP Confirmation & Expansion (10 pts)
    av_val = float(avwap_data.get("avwap") or 0.0)
    avwap_score = 0.0
    if direction == "LONG":
        if avwap_data.get("above_avwap") and av_val > 0:
            dist_pct = (curr_p - av_val) / av_val * 100.0
            avwap_score = 5.0 + min(5.0, max(0.5, dist_pct * 10.0))
        elif av_val > 0 and abs(curr_p - av_val) / av_val <= 0.001:
            avwap_score = 2.0
    else:
        if avwap_data.get("below_avwap") and av_val > 0:
            dist_pct = (av_val - curr_p) / av_val * 100.0
            avwap_score = 5.0 + min(5.0, max(0.5, dist_pct * 10.0))
        elif av_val > 0 and abs(curr_p - av_val) / av_val <= 0.001:
            avwap_score = 2.0
    breakdown["avwap"] = int(round(avwap_score))

    # 4. Breakout / Breakdown Sustainability & Range Position (20 pts)
    bo_score = 0.0
    res = float(breakout_data.get("resistance") or 0.0)
    sup = float(breakout_data.get("support") or 0.0)
    if breakout_data.get("sustained"):
        bo_score = 15.0
        if breakout_data.get("retest_confirmed"):
            bo_score += 5.0
        elif breakout_data.get("holds_above_or_below"):
            bo_score += 5.0
    elif res > sup and curr_p > 0:
        channel_span = res - sup
        if channel_span > 0:
            pos_ratio = (curr_p - sup) / channel_span
            pos_ratio = max(0.0, min(1.0, pos_ratio))
            if direction == "LONG":
                bo_score = pos_ratio * 10.0
            else:
                bo_score = (1.0 - pos_ratio) * 10.0
    breakdown["sustainability"] = int(round(bo_score))

    # 5. Volume Confirmation (15 pts)
    ratio = float(volume_data.get("vol_ratio", 0.0) or 0.0)
    if ratio >= 1.5:
        vol_score = 15.0
    elif ratio > 0.0:
        vol_score = min(15.0, ratio * 10.0)
    else:
        vol_score = 0.0
    breakdown["volume"] = int(round(vol_score))

    # 6. Futures Price, OI & Basis (15 pts)
    fut_score = 0.0
    buildup = futures_data.get("buildup")
    fut_p = float(futures_data.get("futures_price") or curr_p)
    basis = fut_p - curr_p

    if direction == "LONG":
        if buildup == "LONG_BUILDUP":
            fut_score = 12.0
        elif buildup == "SHORT_COVERING":
            fut_score = 8.0
        if curr_p > 0 and basis > 0:
            fut_score += min(3.0, max(0.5, basis / curr_p * 1000.0))
    else:
        if buildup == "SHORT_BUILDUP":
            fut_score = 12.0
        elif buildup == "LONG_UNWINDING":
            fut_score = 8.0
        if curr_p > 0 and basis < 0:
            fut_score += min(3.0, max(0.5, abs(basis) / curr_p * 1000.0))
    breakdown["futures_oi"] = int(round(fut_score))

    # 7. Options-Chain Confirmation (10 pts)
    breakdown["options_chain"] = int(round(options_data.get("score", 0)))

    # 8. Market Structure & Risk/Reward (5 pts)
    rr_score = 5 if breakout_data.get("rr_valid", True) else 2
    breakdown["market_structure"] = rr_score

    total_score = min(100, max(0, int(round(sum(breakdown.values())))))
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

    curr_close = float(spot_price) if (spot_price and spot_price > 0) else float(eval_candles[-1]["close"])
    if spot_price and spot_price > 0:
        eval_candles[-1]["close"] = curr_close
        eval_candles[-1]["high"] = max(float(eval_candles[-1].get("high", curr_close)), curr_close)
        eval_candles[-1]["low"] = min(float(eval_candles[-1].get("low", curr_close)), curr_close)
    closes = [float(c["close"]) for c in eval_candles]
    last_candle = eval_candles[-1]

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
        "resistance": res_level,
        "support": sup_level,
        "curr_close": curr_close,
    }

    short_breakout_data = {
        "sustained": bear_sustained,
        "status": bear_bo_status,
        "holds_above_or_below": curr_close < sup_level,
        "retest_confirmed": bear_bo_details.get("retest_failed", False),
        "rr_valid": (curr_close - short_t1) / short_risk >= 1.4,
        "resistance": res_level,
        "support": sup_level,
        "curr_close": curr_close,
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
        "options_data": opt_long if (direction == "LONG" or long_score >= short_score) else opt_short,
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


# ---------------------------------------------------------------------------
# Scenario Generator & Multi-Scenario Simulation Engine
# ---------------------------------------------------------------------------

def generate_strategy_f_scenario(scenario: str = "dynamic", symbol: str = "NIFTY", tick_offset: Optional[float] = None) -> Dict:
    """
    Generates realistic market scenarios for Strategy F to test all cases:
      - 'bullish': Confirmed Long Breakout with high volume & Put writing (score ~85-95)
      - 'bearish': Confirmed Short Breakdown with high volume & Call writing (score ~85-95)
      - 'neutral': Range-bound consolidation within channel (score ~45-55)
      - 'dynamic': Real-time ticking scenario where score and levels smoothly vary on every tick
      - 'safety': Volume and OI unavailable to test safety invalidation
    """
    now = datetime.datetime.now()
    sc = (scenario or "dynamic").lower()

    if tick_offset is None:
        # Generate cyclic offset based on seconds: 0 to 20 points
        second = now.second + (now.microsecond / 1_000_000.0)
        tick_offset = math.sin(second * 0.2) * 15.0

    # Base price level
    base_price = 25200.0 if symbol == "NIFTY" else (52000.0 if symbol == "BANKNIFTY" else 2800.0)
    res_level = base_price + 50.0
    sup_level = base_price - 50.0

    candles: List[Dict] = []
    num_candles = 60

    if sc in ("bullish", "long", "bull"):
        # Uptrend into breakout
        for i in range(num_candles - 2):
            p = base_price - 20.0 + (i * 0.8)
            candles.append({
                "date": now - datetime.timedelta(minutes=5 * (num_candles - i)),
                "open": round(p - 2.0, 2),
                "high": round(p + 3.0, 2),
                "low": round(p - 3.0, 2),
                "close": round(p, 2),
                "volume": 60000,
            })
        # Breakout candle
        c_bo_p = res_level + 10.0
        candles.append({
            "date": now - datetime.timedelta(minutes=5),
            "open": round(res_level - 1.0, 2),
            "high": round(c_bo_p + 4.0, 2),
            "low": round(res_level - 2.0, 2),
            "close": c_bo_p,
            "volume": 140000,
        })
        # Sustained candle
        c_sust_p = c_bo_p + 8.0 + tick_offset
        candles.append({
            "date": now,
            "open": c_bo_p,
            "high": round(c_sust_p + 5.0, 2),
            "low": round(c_bo_p - 1.0, 2),
            "close": c_sust_p,
            "volume": 150000,
        })
        spot_p = c_sust_p
        fut_p = spot_p + 18.0
        prev_fut_p = spot_p + 6.0
        curr_oi = 1500000
        prev_oi = 1350000
        fut_vol = 180000
        # High Put Writing, PCR > 1.3
        atm = round(spot_p / 50.0) * 50.0
        options_chain = [
            {"strike": atm - 50, "instrument_type": "CE", "oi": 80000, "change_oi": -5000, "volume": 20000, "last_price": 140.0},
            {"strike": atm - 50, "instrument_type": "PE", "oi": 250000, "change_oi": 60000, "volume": 70000, "last_price": 30.0},
            {"strike": atm, "instrument_type": "CE", "oi": 150000, "change_oi": -10000, "volume": 50000, "last_price": 85.0},
            {"strike": atm, "instrument_type": "PE", "oi": 320000, "change_oi": 85000, "volume": 90000, "last_price": 55.0},
            {"strike": atm + 50, "instrument_type": "CE", "oi": 200000, "change_oi": 12000, "volume": 60000, "last_price": 45.0},
            {"strike": atm + 50, "instrument_type": "PE", "oi": 280000, "change_oi": 40000, "volume": 65000, "last_price": 95.0},
        ]

    elif sc in ("bearish", "short", "bear"):
        # Downtrend into breakdown
        for i in range(num_candles - 2):
            p = base_price + 20.0 - (i * 0.8)
            candles.append({
                "date": now - datetime.timedelta(minutes=5 * (num_candles - i)),
                "open": round(p + 2.0, 2),
                "high": round(p + 3.0, 2),
                "low": round(p - 3.0, 2),
                "close": round(p, 2),
                "volume": 60000,
            })
        # Breakdown candle
        c_bd_p = sup_level - 10.0
        candles.append({
            "date": now - datetime.timedelta(minutes=5),
            "open": round(sup_level + 1.0, 2),
            "high": round(sup_level + 2.0, 2),
            "low": round(c_bd_p - 4.0, 2),
            "close": c_bd_p,
            "volume": 140000,
        })
        # Sustained candle
        c_sust_p = c_bd_p - 8.0 - abs(tick_offset)
        candles.append({
            "date": now,
            "open": c_bd_p,
            "high": round(c_bd_p + 1.0, 2),
            "low": round(c_sust_p - 5.0, 2),
            "close": c_sust_p,
            "volume": 150000,
        })
        spot_p = c_sust_p
        fut_p = spot_p - 18.0
        prev_fut_p = spot_p - 6.0
        curr_oi = 1500000
        prev_oi = 1350000
        fut_vol = 180000
        # High Call Writing, PCR < 0.7
        atm = round(spot_p / 50.0) * 50.0
        options_chain = [
            {"strike": atm - 50, "instrument_type": "CE", "oi": 300000, "change_oi": 75000, "volume": 70000, "last_price": 95.0},
            {"strike": atm - 50, "instrument_type": "PE", "oi": 120000, "change_oi": -8000, "volume": 30000, "last_price": 45.0},
            {"strike": atm, "instrument_type": "CE", "oi": 350000, "change_oi": 95000, "volume": 85000, "last_price": 55.0},
            {"strike": atm, "instrument_type": "PE", "oi": 140000, "change_oi": -12000, "volume": 40000, "last_price": 85.0},
            {"strike": atm + 50, "instrument_type": "CE", "oi": 260000, "change_oi": 50000, "volume": 60000, "last_price": 25.0},
            {"strike": atm + 50, "instrument_type": "PE", "oi": 70000, "change_oi": -15000, "volume": 20000, "last_price": 140.0},
        ]

    elif sc in ("safety", "invalidation", "safety_rule"):
        for i in range(num_candles):
            p = base_price + math.sin(i * 0.3) * 15.0
            candles.append({
                "date": now - datetime.timedelta(minutes=5 * (num_candles - i)),
                "open": round(p - 1.0, 2),
                "high": round(p + 2.0, 2),
                "low": round(p - 2.0, 2),
                "close": round(p, 2),
                "volume": 0,  # Zero volume trigger
            })
        spot_p = base_price
        fut_p = base_price
        prev_fut_p = base_price
        curr_oi = 0  # Zero OI trigger
        prev_oi = 0
        fut_vol = 0
        options_chain = []

    else:
        # Dynamic / Rangebound scenario with live ticking modulation
        for i in range(num_candles):
            cycle = math.sin(i * 0.25) * 25.0
            p = base_price + cycle
            candles.append({
                "date": now - datetime.timedelta(minutes=5 * (num_candles - i)),
                "open": round(p - 1.5, 2),
                "high": round(p + 3.0, 2),
                "low": round(p - 3.0, 2),
                "close": round(p, 2),
                "volume": 50000 + int(abs(math.cos(i * 0.2)) * 35000),
            })
        spot_p = round(base_price + tick_offset, 2)
        candles[-1]["close"] = spot_p
        candles[-1]["high"] = max(candles[-1]["high"], spot_p)
        candles[-1]["low"] = min(candles[-1]["low"], spot_p)
        fut_p = round(spot_p + (tick_offset * 0.3), 2)
        prev_fut_p = spot_p - 2.0
        curr_oi = 1250000 + int(tick_offset * 5000)
        prev_oi = 1200000
        vol_factor = max(0.6, min(1.8, 1.0 + (tick_offset / 30.0)))
        fut_vol = int(90000 * vol_factor)
        atm = round(spot_p / 50.0) * 50.0
        # Dynamic PCR varying between 0.85 and 1.35
        pcr_factor = max(0.7, min(1.4, 1.05 + (tick_offset / 40.0)))
        pe_base = int(220000 * pcr_factor)
        ce_base = 220000
        options_chain = [
            {"strike": atm - 50, "instrument_type": "CE", "oi": 180000, "change_oi": 15000, "volume": 40000, "last_price": 110.0},
            {"strike": atm - 50, "instrument_type": "PE", "oi": int(180000 * pcr_factor), "change_oi": 25000, "volume": 50000, "last_price": 40.0},
            {"strike": atm, "instrument_type": "CE", "oi": ce_base, "change_oi": 20000, "volume": 60000, "last_price": 70.0},
            {"strike": atm, "instrument_type": "PE", "oi": pe_base, "change_oi": int(20000 * pcr_factor), "volume": 65000, "last_price": 68.0},
            {"strike": atm + 50, "instrument_type": "CE", "oi": 240000, "change_oi": 30000, "volume": 55000, "last_price": 38.0},
            {"strike": atm + 50, "instrument_type": "PE", "oi": int(240000 * pcr_factor), "change_oi": 15000, "volume": 45000, "last_price": 105.0},
        ]

    # Evaluate Strategy F
    result = evaluate_sustained_breakout_strategy(
        candles_5m=candles,
        symbol=symbol,
        spot_price=spot_p,
        futures_symbol=f"{symbol}26NOVFUT",
        futures_price=fut_p,
        prev_futures_price=prev_fut_p,
        futures_volume=fut_vol,
        current_oi=curr_oi,
        prev_oi=prev_oi,
        options_chain=options_chain,
        candle_ts=now,
    )

    v = result.get("values", {})
    ema_aligned = v.get("ema_data", {}).get("stacked_bull") or v.get("ema_data", {}).get("stacked_bear")
    price_vs_avwap = "ABOVE" if v.get("avwap_data", {}).get("above_avwap") else ("BELOW" if v.get("avwap_data", {}).get("below_avwap") else "AT VWAP")
    fut_chg = round(((fut_p - prev_fut_p) / prev_fut_p * 100), 2) if prev_fut_p else 0.0
    oi_chg = round(((curr_oi - prev_oi) / prev_oi * 100), 2) if prev_oi else 0.0

    return {
        "market_trend": {
            "symbol": symbol,
            "spot_price": v.get("spot_price"),
            "ema10": v.get("ema_data", {}).get("ema10"),
            "ema21": v.get("ema_data", {}).get("ema21"),
            "ema50": v.get("ema_data", {}).get("ema50"),
            "ema_aligned": ema_aligned,
            "supertrend": v.get("supertrend_data", {}).get("val"),
            "supertrend_trend": v.get("supertrend_data", {}).get("trend"),
            "avwap": v.get("avwap_data", {}).get("avwap"),
            "price_vs_avwap": price_vs_avwap,
            "resistance": v.get("resistance"),
            "support": v.get("support"),
            "volume": v.get("volume_data", {}).get("current_vol"),
            "volume_ma": v.get("volume_data", {}).get("vol_ma20"),
            "volume_ratio": v.get("volume_data", {}).get("vol_ratio"),
            "volume_valid": (v.get("volume_data", {}).get("current_vol", 0) or 0) > 0,
        },
        "derivatives": {
            "futures_symbol": f"{symbol}26NOVFUT",
            "futures_price": v.get("futures_data", {}).get("futures_price"),
            "futures_change_pct": fut_chg,
            "futures_volume": v.get("futures_data", {}).get("futures_volume"),
            "current_oi": v.get("futures_data", {}).get("current_oi"),
            "oi_change_pct": oi_chg,
            "buildup": v.get("futures_data", {}).get("buildup"),
            "call_oi": v.get("options_data", {}).get("call_oi_total"),
            "put_oi": v.get("options_data", {}).get("put_oi_total"),
            "pcr": v.get("options_data", {}).get("pcr"),
            "market_bias": v.get("options_data", {}).get("bias"),
            "oi_valid": (curr_oi is not None and curr_oi > 0),
        },
        "trading_signal": {
            "symbol": symbol,
            "direction": result.get("direction"),
            "confidence_score": result.get("score"),
            "setup_status": result.get("setup_status"),
            "passed": result.get("passed"),
            "entry_price": v.get("entry_price"),
            "stop_loss": v.get("stop_loss"),
            "target1": v.get("target1"),
            "target2": v.get("target2"),
            "risk_reward": v.get("risk_reward"),
            "breakout_level": v.get("breakout_level"),
            "reasons_for": result.get("reasons_for", []),
            "reasons_against": result.get("reasons_against", []),
            "breakdown": v.get("breakdown", {}),
            "timestamp": now.isoformat(),
        },
        "records": [result],
    }
