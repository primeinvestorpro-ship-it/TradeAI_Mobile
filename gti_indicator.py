"""
GTI Smart Money Trader — Indicator & Signal Engine (Strategy G)
================================================================
Based on the GTI Smart Money Trader User Stories:

Epic 1: GTI Indicator Engine
- User Story 1.1: Candle Color Display (Yellow: Accumulation, Blue: Strong Bullish Momentum + Volume Spike, Black: Strong Bearish Momentum + Volume Spike).
- User Story 1.2: Configurable parameters (ATR period, Volume spike threshold, Accumulation ratio).

Epic 2: Smart Signal Engine
- User Story 2.1: CALL Signal: Yellow candle exists within lookback AND price breaks Yellow HIGH AND current candle = Blue.
- User Story 2.2: PUT Signal: Yellow candle exists within lookback AND price breaks Yellow LOW AND current candle = Black.
- Confidence levels: HIGH, MEDIUM, LOW.

Epic 3: Option Suggestion Engine
- User Story 3.1: Suggest nearest ATM Strike for NIFTY / BANKNIFTY (CE for CALL, PE for PUT) with Delta, IV, estimated premium.
- User Story 3.2: Option chain metrics.

Epic 4: Risk Management Engine
- User Story 4.1: Stop Loss: 20-25% of option premium OR structure-based (swing low for CALL, swing high for PUT).
- User Story 4.2: Target calculation: 1:2 Risk-Reward ratio (2x risk distance).
- User Story 4.3: Trailing Stop Loss (+20% profit threshold).

Epic 5: Fake Signal Filter
- User Story 5.1: ATR-Based Filter (pauses when ATR < threshold).
- User Story 5.2: Volume Filter (pauses when volume < 50% of 20-period average).
- User Story 5.3: Sideways Market Detection (ADX < 20 or narrow consolidation range).

Epic 6: Time Filter
- User Story 6.1: Approved trading windows (9:25 AM - 11:30 AM AND 1:30 PM - 3:15 PM IST).
"""

import datetime
import math
from typing import Dict, List, Optional, Tuple, Any

# ---------------------------------------------------------------------------
# Constants & Defaults
# ---------------------------------------------------------------------------
STRATEGY_G_ID = "G"
STRATEGY_G_NAME = "GTI Smart Money Trader"

CANDLE_COLOR_YELLOW = "YELLOW"  # Smart Money Accumulation
CANDLE_COLOR_BLUE   = "BLUE"    # Strong Bullish Momentum + Volume Spike
CANDLE_COLOR_BLACK  = "BLACK"   # Strong Bearish Momentum + Volume Spike (Rendered Dark Navy/Charcoal)
CANDLE_COLOR_GREEN  = "GREEN"   # Standard Bullish
CANDLE_COLOR_RED    = "RED"     # Standard Bearish

# Trading Windows (IST): 9:25 - 11:30 & 13:30 - 15:15
MORNING_WINDOW_START   = datetime.time(9, 25)
MORNING_WINDOW_END     = datetime.time(11, 30)
AFTERNOON_WINDOW_START = datetime.time(13, 30)
AFTERNOON_WINDOW_END   = datetime.time(15, 15)

# ATM Step Sizes
ATM_STEPS = {
    "NIFTY": 50,
    "NIFTY 50": 50,
    "BANKNIFTY": 100,
    "NIFTY BANK": 100,
    "FINNIFTY": 50,
    "MIDCPNIFTY": 25,
}


# ---------------------------------------------------------------------------
# Technical Indicator Helpers
# ---------------------------------------------------------------------------

def compute_atr(candles: List[Dict], period: int = 14) -> List[Optional[float]]:
    """Compute Average True Range (ATR) over candles."""
    n = len(candles)
    if n == 0:
        return []
    trs: List[float] = []
    for i in range(n):
        high = float(candles[i].get("high", candles[i].get("close", 0)))
        low = float(candles[i].get("low", candles[i].get("close", 0)))
        close_prev = float(candles[i - 1].get("close", high)) if i > 0 else low
        tr = max(high - low, abs(high - close_prev), abs(low - close_prev))
        trs.append(tr)

    atr_series: List[Optional[float]] = [None] * n
    if n < period:
        # Simple average of available if below period
        mean_tr = sum(trs) / float(n) if n > 0 else 0.0
        return [round(mean_tr, 2)] * n

    # Initial SMA of TR
    first_atr = sum(trs[:period]) / float(period)
    atr_series[period - 1] = round(first_atr, 2)
    current_atr = first_atr
    for i in range(period, n):
        current_atr = (current_atr * (period - 1) + trs[i]) / float(period)
        atr_series[i] = round(current_atr, 2)

    # Backfill earliest with first_atr
    for i in range(period - 1):
        atr_series[i] = round(first_atr, 2)

    return atr_series


def compute_volume_ma(candles: List[Dict], period: int = 20) -> List[float]:
    """Compute Simple Moving Average of Volume."""
    n = len(candles)
    if n == 0:
        return []
    vols = [float(c.get("volume", 0) or 0) for c in candles]
    res: List[float] = []
    for i in range(n):
        start_idx = max(0, i - period + 1)
        sub = vols[start_idx : i + 1]
        res.append(round(sum(sub) / float(len(sub)), 2) if sub else 0.0)
    return res


def compute_adx(candles: List[Dict], period: int = 14) -> Tuple[float, str]:
    """Simple ADX estimation for trending vs sideways determination."""
    n = len(candles)
    if n < period + 2:
        return 22.0, "NORMAL"

    plus_dm_list: List[float] = []
    minus_dm_list: List[float] = []
    tr_list: List[float] = []

    for i in range(1, n):
        h = float(candles[i].get("high", 0))
        l = float(candles[i].get("low", 0))
        h_prev = float(candles[i - 1].get("high", 0))
        l_prev = float(candles[i - 1].get("low", 0))
        c_prev = float(candles[i - 1].get("close", 0))

        up_move = h - h_prev
        down_move = l_prev - l

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))

        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
        tr_list.append(tr)

    sub_len = min(period, len(plus_dm_list))
    if sub_len == 0 or sum(tr_list[-sub_len:]) == 0:
        return 20.0, "SIDEWAYS"

    tr_sum = sum(tr_list[-sub_len:])
    pdi = (sum(plus_dm_list[-sub_len:]) / tr_sum) * 100.0 if tr_sum > 0 else 0
    mdi = (sum(minus_dm_list[-sub_len:]) / tr_sum) * 100.0 if tr_sum > 0 else 0

    denom = pdi + mdi
    dx = (abs(pdi - mdi) / denom) * 100.0 if denom > 0 else 20.0
    adx_val = round(dx, 1)

    if adx_val >= 25.0:
        status = "TRENDING"
    elif adx_val < 20.0:
        status = "SIDEWAYS"
    else:
        status = "MODERATE"

    return adx_val, status


# ---------------------------------------------------------------------------
# Epic 1: GTI Indicator Engine (Candle Color Classification)
# ---------------------------------------------------------------------------

def classify_gti_candle(
    candle: Dict,
    atr_val: float,
    vol_ma: float,
    vol_spike_threshold: float = 1.45,
    acc_body_ratio: float = 0.40,
    acc_range_ratio: float = 0.75,
) -> Dict[str, Any]:
    """
    Classifies a single candle according to GTI Indicator rules:
      - YELLOW: Accumulation detected (smart money consolidation, tight range / small body vs ATR)
      - BLUE:   Strong Bullish Momentum + Volume Spike (large body, Close > Open, close near high, Volume > threshold * MA)
      - BLACK:  Strong Bearish Momentum + Volume Spike (large body, Close < Open, close near low, Volume > threshold * MA)
      - GREEN / RED: Standard market candles
    """
    o = float(candle.get("open", 0))
    h = float(candle.get("high", 0))
    l = float(candle.get("low", 0))
    c = float(candle.get("close", 0))
    v = float(candle.get("volume", 0) or 0)

    total_range = max(h - l, 0.05)
    body = abs(c - o)
    is_bull = c >= o

    # Volume ratio
    vol_ratio = (v / vol_ma) if (vol_ma and vol_ma > 0) else 1.0

    # 1. Accumulation Candle (Yellow):
    # Smart money tight range compression, body small relative to ATR & range
    is_tight_body = (body / atr_val) <= acc_body_ratio if atr_val > 0 else (body / total_range <= 0.35)
    is_tight_range = (total_range / atr_val) <= acc_range_ratio if atr_val > 0 else True
    # Volume is steady/absorption without extreme dump
    is_absorption_vol = vol_ratio >= 0.4 and vol_ratio <= 1.8

    if is_tight_body and is_tight_range and is_absorption_vol:
        return {
            "color": CANDLE_COLOR_YELLOW,
            "label": "🟡 Accumulation (Smart Money)",
            "is_yellow": True,
            "is_blue": False,
            "is_black": False,
            "vol_ratio": round(vol_ratio, 2),
            "body_ratio": round(body / total_range, 2),
            "high": h,
            "low": l,
            "close": c,
            "open": o,
        }

    # 2. Strong Bullish Momentum + Volume Spike (Blue):
    # Close > Open, large body (>= 55% of range), close near high (upper wick <= 25% of range), volume spike >= threshold
    upper_wick = h - c
    is_strong_bull_body = is_bull and (body / total_range >= 0.55) and (upper_wick / total_range <= 0.25)
    is_vol_spike = vol_ratio >= vol_spike_threshold

    if is_strong_bull_body and is_vol_spike:
        return {
            "color": CANDLE_COLOR_BLUE,
            "label": "🔵 Strong Bullish Momentum (Volume Spike)",
            "is_yellow": False,
            "is_blue": True,
            "is_black": False,
            "vol_ratio": round(vol_ratio, 2),
            "body_ratio": round(body / total_range, 2),
            "high": h,
            "low": l,
            "close": c,
            "open": o,
        }

    # 3. Strong Bearish Momentum + Volume Spike (Black / Dark Charcoal):
    # Close < Open, large body (>= 55% of range), close near low (lower wick <= 25% of range), volume spike >= threshold
    lower_wick = c - l
    is_strong_bear_body = (not is_bull) and (body / total_range >= 0.55) and (lower_wick / total_range <= 0.25)

    if is_strong_bear_body and is_vol_spike:
        return {
            "color": CANDLE_COLOR_BLACK,
            "label": "⚫ Strong Bearish Momentum (Volume Spike)",
            "is_yellow": False,
            "is_blue": False,
            "is_black": True,
            "vol_ratio": round(vol_ratio, 2),
            "body_ratio": round(body / total_range, 2),
            "high": h,
            "low": l,
            "close": c,
            "open": o,
        }

    # Default normal green / red
    default_color = CANDLE_COLOR_GREEN if is_bull else CANDLE_COLOR_RED
    return {
        "color": default_color,
        "label": "Normal Bullish" if is_bull else "Normal Bearish",
        "is_yellow": False,
        "is_blue": False,
        "is_black": False,
        "vol_ratio": round(vol_ratio, 2),
        "body_ratio": round(body / total_range, 2),
        "high": h,
        "low": l,
        "close": c,
        "open": o,
    }


def classify_gti_candles_series(
    candles: List[Dict],
    atr_period: int = 14,
    vol_period: int = 20,
    vol_spike_threshold: float = 1.45,
) -> List[Dict[str, Any]]:
    """Classifies an entire series of candles with GTI colors and metadata."""
    if not candles:
        return []

    atrs = compute_atr(candles, period=atr_period)
    vol_mas = compute_volume_ma(candles, period=vol_period)

    classified: List[Dict[str, Any]] = []
    for i, c in enumerate(candles):
        atr_val = atrs[i] if (i < len(atrs) and atrs[i] is not None) else 25.0
        v_ma = vol_mas[i] if i < len(vol_mas) else 1000.0
        meta = classify_gti_candle(
            c,
            atr_val=atr_val,
            vol_ma=v_ma,
            vol_spike_threshold=vol_spike_threshold,
        )
        meta["time"] = c.get("time") or c.get("date")
        meta["candle_idx"] = i
        classified.append(meta)

    return classified


# ---------------------------------------------------------------------------
# Epic 6: Time Window Filter (9:25 - 11:30 & 13:30 - 15:15 IST)
# ---------------------------------------------------------------------------

def check_trading_time_window(current_time: Optional[datetime.time] = None) -> Tuple[bool, str]:
    """
    Checks if current time is within GTI approved trading windows:
      - Morning: 09:25 AM to 11:30 AM
      - Afternoon: 01:30 PM to 03:15 PM
    """
    if current_time is None:
        now_dt = datetime.datetime.now()
        current_time = now_dt.time()

    is_morning = MORNING_WINDOW_START <= current_time <= MORNING_WINDOW_END
    is_afternoon = AFTERNOON_WINDOW_START <= current_time <= AFTERNOON_WINDOW_END

    if is_morning:
        return True, "Trading Window Open (Morning Momentum 09:25-11:30)"
    if is_afternoon:
        return True, "Trading Window Open (Afternoon Reversal/Trend 13:30-15:15)"

    # Outside windows
    if datetime.time(11, 30) < current_time < datetime.time(13, 30):
        return False, "Lunch / Midday Consolidation Range (Signals Paused)"
    if current_time < datetime.time(9, 25):
        return False, "Opening Volatility / Pre-Window (Signals Paused until 09:25)"
    return False, "Outside Trading Hours (Market Closed / Settling)"


# ---------------------------------------------------------------------------
# Epic 3: Option Suggestion Engine (ATM Option Display)
# ---------------------------------------------------------------------------

def calculate_atm_option(
    spot_price: float,
    symbol: str = "NIFTY",
    signal_type: str = "BUY_CALL",
    iv_percent: float = 14.5,
) -> Dict[str, Any]:
    """
    Calculates nearest ATM Option for NIFTY / BANKNIFTY:
      - Call: Suggests ATM CE
      - Put: Suggests ATM PE
      - Includes Strike, estimated LTP, Delta, IV, and standard risk levels
    """
    sym_upper = (symbol or "").strip().upper()
    if "BANK" in sym_upper:
        step = 100
    elif "MIDCP" in sym_upper:
        step = 25
    else:
        step = 50
    atm_strike = int(round(spot_price / step) * step)

    opt_type = "CE" if signal_type in ("BUY_CALL", "CALL") else "PE"

    # Approximate Black-Scholes estimate for ATM near-expiry premium
    # ATM option premium roughly ~ 0.40 * spot * (IV/100) * sqrt(days/365)
    # For weekly ATM (~3 days to expiry): ~ 0.8% - 1.1% of index spot
    est_ltp = round(spot_price * 0.0095, 1) if spot_price > 0 else 180.0
    delta = 0.52 if opt_type == "CE" else -0.52

    # Stop Loss & Target calculations (Epic 4)
    # SL: 20-25% of option premium (User Story 4.1)
    sl_pts = round(est_ltp * 0.22, 1)
    target_pts = round(sl_pts * 2.0, 1)  # 1:2 Risk-Reward (User Story 4.2)

    option_sl = round(max(1.0, est_ltp - sl_pts), 1)
    option_target = round(est_ltp + target_pts, 1)
    option_target2 = round(est_ltp + (sl_pts * 2.5), 1)

    return {
        "symbol": sym_upper,
        "atm_strike": atm_strike,
        "option_type": opt_type,
        "tradingsymbol": f"{sym_upper} {atm_strike} {opt_type}",
        "estimated_ltp": est_ltp,
        "delta": delta,
        "iv": iv_percent,
        "option_stop_loss": option_sl,
        "option_target": option_target,
        "option_target2": option_target2,
        "risk_points": sl_pts,
        "reward_points": target_pts,
        "risk_reward_ratio": "1:2",
    }


# ---------------------------------------------------------------------------
# Epic 2, 4, 5: Smart Signal Engine & Risk Management
# ---------------------------------------------------------------------------

def evaluate_gti_strategy(
    candles_5m: List[Dict],
    symbol: str = "NIFTY",
    spot_price: Optional[float] = None,
    atr_period: int = 14,
    vol_period: int = 20,
    lookback_yellow: int = 5,
    vol_spike_threshold: float = 1.45,
    min_atr_threshold: float = 12.0,
    override_time: Optional[datetime.time] = None,
) -> Dict[str, Any]:
    """
    Main GTI Strategy Evaluator:
      1. Classifies candles into Yellow (accumulation), Blue (bull momentum), Black (bear momentum).
      2. Evaluates filters:
         - Time Window Filter (9:25-11:30 & 13:30-15:15)
         - ATR Filter (volatility check)
         - Volume Filter (min 50% of 20-MA)
         - Sideways Market Filter (ADX)
      3. Evaluates Signal Rules:
         - CALL Signal: Yellow candle exists within lookback AND price breaks Yellow HIGH AND current candle = Blue
         - PUT Signal: Yellow candle exists within lookback AND price breaks Yellow LOW AND current candle = Black
      4. Calculates Risk Management:
         - Structure-based Stop Loss (recent swing low/high)
         - 20-25% Option Premium SL
         - Target 1:2 R:R
         - Trailing SL (+20% threshold)
    """
    if not candles_5m or len(candles_5m) < 5:
        return {
            "strategy_id": STRATEGY_G_ID,
            "strategy_name": STRATEGY_G_NAME,
            "symbol": symbol,
            "signal": "NO_DATA",
            "passed": False,
            "reason": "Insufficient 5m candle history",
        }

    curr_spot = float(spot_price if spot_price is not None else candles_5m[-1].get("close", 0))
    # Keep the current live candle's close, high, low in sync with live spot tick
    if spot_price is not None and candles_5m:
        candles_5m[-1]["close"] = curr_spot
        candles_5m[-1]["high"] = max(float(candles_5m[-1].get("high", curr_spot)), curr_spot)
        candles_5m[-1]["low"] = min(float(candles_5m[-1].get("low", curr_spot)), curr_spot)

    # Classify candles
    classified = classify_gti_candles_series(
        candles_5m,
        atr_period=atr_period,
        vol_period=vol_period,
        vol_spike_threshold=vol_spike_threshold,
    )
    last_candle = classified[-1]
    curr_color = last_candle["color"]

    # Compute ATR & ADX
    atrs = compute_atr(candles_5m, period=atr_period)
    curr_atr = atrs[-1] if atrs and atrs[-1] is not None else (curr_spot * 0.0012)
    vol_mas = compute_volume_ma(candles_5m, period=vol_period)
    curr_vol_ma = vol_mas[-1] if vol_mas else 1000.0
    curr_vol = float(candles_5m[-1].get("volume", 0) or 0)
    adx_val, adx_market_status = compute_adx(candles_5m, period=14)

    # If raw spot candle has volume=0 (NSE index spot), derive effective volume ratio from range expansion vs ATR
    if (curr_vol <= 0 or curr_vol_ma <= 0) and curr_atr > 0:
        c_h = float(candles_5m[-1].get("high", curr_spot))
        c_l = float(candles_5m[-1].get("low", curr_spot))
        c_range = max(c_h - c_l, 0.5)
        derived_ratio = round(c_range / (curr_atr * 0.70), 2)
        last_candle["vol_ratio"] = max(0.6, min(derived_ratio, 3.5))

    # ── Epic 6: Time Filter ──
    time_window_open, time_msg = check_trading_time_window(override_time)

    # ── Epic 5: Fake Signal Filters ──
    # Dynamic ATR threshold based on index spot scale (~0.05% of price)
    effective_min_atr = max(10.0, curr_spot * 0.0005) if min_atr_threshold == 12.0 else min_atr_threshold
    atr_filter_passed = curr_atr >= effective_min_atr
    vol_filter_passed = (curr_vol >= (0.50 * curr_vol_ma)) if (curr_vol > 0 and curr_vol_ma > 0) else True
    market_not_sideways = adx_market_status != "SIDEWAYS"

    filter_active = not (time_window_open and atr_filter_passed and vol_filter_passed and market_not_sideways)
    filter_reasons: List[str] = []
    if not time_window_open:
        filter_reasons.append(time_msg)
    if not atr_filter_passed:
        filter_reasons.append(f"Low Volatility Filter (ATR {curr_atr:.1f} < {effective_min_atr:.1f}) — Signals Paused")
    if not vol_filter_passed:
        filter_reasons.append("Low Volume Filter (Volume < 50% of 20-MA) — Signals Paused")
    if not market_not_sideways:
        filter_reasons.append(f"Sideways Market Filter (ADX {adx_val:.1f} < 20) — Signals Paused")

    # ── Epic 2: Lookback for Yellow Accumulation Candle ──
    # Check if a Yellow candle appeared in the last N candles (excluding the current one)
    lookback_window = classified[-min(len(classified), lookback_yellow + 1) : -1]
    yellow_candles = [c for c in lookback_window if c["is_yellow"]]

    has_recent_yellow = len(yellow_candles) > 0
    ref_yellow = yellow_candles[-1] if has_recent_yellow else None

    yellow_high = ref_yellow["high"] if ref_yellow else None
    yellow_low = ref_yellow["low"] if ref_yellow else None

    # Check Breakout / Breakdown
    curr_high = float(candles_5m[-1].get("high", curr_spot))
    curr_low = float(candles_5m[-1].get("low", curr_spot))
    curr_close = float(candles_5m[-1].get("close", curr_spot))

    breaks_yellow_high = (yellow_high is not None) and (curr_close > yellow_high or curr_high > yellow_high)
    breaks_yellow_low = (yellow_low is not None) and (curr_close < yellow_low or curr_low < yellow_low)

    # Evaluate Signal Trigger
    is_call_signal = has_recent_yellow and breaks_yellow_high and (curr_color == CANDLE_COLOR_BLUE)
    is_put_signal = has_recent_yellow and breaks_yellow_low and (curr_color == CANDLE_COLOR_BLACK)

    # Confidence calculation (High / Medium / Low)
    confidence = "LOW"
    vol_ratio = last_candle.get("vol_ratio", 1.0)
    if is_call_signal or is_put_signal:
        score_points = 0
        if vol_ratio >= 2.0:
            score_points += 30
        elif vol_ratio >= 1.5:
            score_points += 20
        else:
            score_points += 10

        if adx_val >= 25.0:
            score_points += 25
        elif adx_val >= 20.0:
            score_points += 15

        if time_window_open:
            score_points += 25
        if curr_atr >= min_atr_threshold * 1.2:
            score_points += 20

        if score_points >= 75:
            confidence = "HIGH"
        elif score_points >= 50:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

    # Structure-based Stop-Loss & Targets (Epic 4)
    # Look back over last 5-10 candles for swing low / swing high
    recent_lows = [float(c.get("low", curr_spot)) for c in candles_5m[-8:]]
    recent_highs = [float(c.get("high", curr_spot)) for c in candles_5m[-8:]]
    swing_low = min(recent_lows) if recent_lows else curr_spot - (curr_atr * 1.5)
    swing_high = max(recent_highs) if recent_highs else curr_spot + (curr_atr * 1.5)

    if is_call_signal:
        direction = "BUY_CALL"
        signal_badge = "🔵 BUY CALL"
        signal_status = "CONFIRMED BUY CALL"
        structure_sl = round(min(swing_low, yellow_low or swing_low), 2)
        risk_dist = max(curr_spot - structure_sl, curr_atr * 0.8)
        target1 = round(curr_spot + (risk_dist * 1.5), 2)
        target2 = round(curr_spot + (risk_dist * 2.0), 2)  # 1:2 Target
        entry_price = round(curr_spot, 2)
        suggested_option = calculate_atm_option(
            spot_price=curr_spot,
            symbol=symbol,
            signal_type="BUY_CALL",
            iv_percent=14.5,
        )
    elif is_put_signal:
        direction = "BUY_PUT"
        signal_badge = "⚫ BUY PUT"
        signal_status = "CONFIRMED BUY PUT"
        structure_sl = round(max(swing_high, yellow_high or swing_high), 2)
        risk_dist = max(structure_sl - curr_spot, curr_atr * 0.8)
        target1 = round(curr_spot - (risk_dist * 1.5), 2)
        target2 = round(curr_spot - (risk_dist * 2.0), 2)  # 1:2 Target
        entry_price = round(curr_spot, 2)
        suggested_option = calculate_atm_option(
            spot_price=curr_spot,
            symbol=symbol,
            signal_type="BUY_PUT",
            iv_percent=14.5,
        )
    elif curr_color == CANDLE_COLOR_YELLOW or has_recent_yellow:
        direction = "ACCUMULATION"
        signal_badge = "🟡 SMART MONEY ACCUMULATION"
        signal_status = "SMART MONEY ACCUMULATION (Wait for Blue/Black Breakout)"
        ref_h = yellow_high or float(candles_5m[-1].get("high", curr_spot))
        ref_l = yellow_low or float(candles_5m[-1].get("low", curr_spot))
        risk_dist = max(ref_h - ref_l, curr_atr * 0.8)
        entry_price = round(ref_h, 2)
        structure_sl = round(ref_l, 2)
        target1 = round(ref_h + (risk_dist * 1.5), 2)
        target2 = round(ref_h + (risk_dist * 2.0), 2)
        calc_opt = calculate_atm_option(
            spot_price=curr_spot,
            symbol=symbol,
            signal_type="BUY_CALL",
            iv_percent=14.5,
        )
        suggested_option = {
            "atm_strike": calc_opt["atm_strike"],
            "option_type": "CE / PE",
            "tradingsymbol": f"{symbol} {calc_opt['atm_strike']} CE / PE (Watch)",
            "estimated_ltp": calc_opt["estimated_ltp"],
            "delta": 0.50,
            "iv": calc_opt["iv"],
            "option_stop_loss": calc_opt["option_stop_loss"],
            "option_target": calc_opt["option_target"],
            "expiry": calc_opt.get("expiry"),
            "note": "Smart Money Base Watch — CE above Yellow High, PE below Yellow Low",
        }
    else:
        direction = "WAIT"
        signal_badge = "WAIT / NO TRADE"
        signal_status = "Awaiting GTI Setup (Yellow Accumulation + Momentum Breakout)"
        risk_dist = max(swing_high - swing_low, curr_atr * 0.8)
        entry_price = round(curr_spot, 2)
        structure_sl = round(swing_low, 2)
        target1 = round(curr_spot + (risk_dist * 1.5), 2)
        target2 = round(curr_spot + (risk_dist * 2.0), 2)
        calc_opt = calculate_atm_option(
            spot_price=curr_spot,
            symbol=symbol,
            signal_type="BUY_CALL",
            iv_percent=14.5,
        )
        suggested_option = {
            "atm_strike": calc_opt["atm_strike"],
            "option_type": "CE / PE",
            "tradingsymbol": f"{symbol} {calc_opt['atm_strike']} CE / PE (Awaiting Base)",
            "estimated_ltp": calc_opt["estimated_ltp"],
            "delta": 0.50,
            "iv": calc_opt["iv"],
            "option_stop_loss": calc_opt["option_stop_loss"],
            "option_target": calc_opt["option_target"],
            "expiry": calc_opt.get("expiry"),
            "note": "Awaiting Yellow Accumulation Candle Setup",
        }

    # Check final trigger passed
    signal_passed = (is_call_signal or is_put_signal) and not filter_active

    return {
        "strategy_id": STRATEGY_G_ID,
        "strategy_name": STRATEGY_G_NAME,
        "symbol": symbol,
        "spot_price": curr_spot,
        "direction": direction,
        "signal_badge": signal_badge,
        "signal_status": signal_status,
        "passed": signal_passed,
        "confidence": confidence,
        "current_candle_color": curr_color,
        "current_candle_label": last_candle.get("label", ""),
        "has_recent_yellow": has_recent_yellow,
        "yellow_candle_high": yellow_high,
        "yellow_candle_low": yellow_low,
        "breaks_yellow_high": breaks_yellow_high,
        "breaks_yellow_low": breaks_yellow_low,
        "vol_ratio": last_candle.get("vol_ratio", 1.0),
        "atr": curr_atr,
        "adx": adx_val,
        "market_status": adx_market_status,  # TRENDING / SIDEWAYS / MODERATE
        "time_window_open": time_window_open,
        "time_window_status": time_msg,
        "filter_active": filter_active,
        "filter_reasons": filter_reasons,
        "atr_filter_passed": atr_filter_passed,
        "vol_filter_passed": vol_filter_passed,
        "market_not_sideways": market_not_sideways,
        "entry_price": entry_price,
        "structure_stop_loss": structure_sl,
        "target1": target1,
        "target2": target2,
        "risk_reward": "1:2",
        "trailing_sl_threshold": "+20% option profit",
        "suggested_option": suggested_option,
        "classified_candles_tail": classified[-15:],  # Last 15 candles with colors for chart rendering
        "timestamp": datetime.datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# Scenario Generator (For Testing, Demo, and Offline Verification)
# ---------------------------------------------------------------------------

def generate_gti_scenario(
    scenario: str = "dynamic",
    symbol: str = "NIFTY",
    tick_offset: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Generates realistic GTI Smart Money scenarios for testing & live preview:
      - 'dynamic': Live realistic setup with time & market filters active
      - 'call': Confirmed BUY CALL (Yellow accumulation candle broken by Blue momentum candle)
      - 'put': Confirmed BUY PUT (Yellow accumulation candle broken by Black momentum candle)
      - 'yellow': Smart Money Accumulation detected (Yellow candle active)
      - 'sideways': Low volatility / choppy range (signals paused by filters)
    """
    sym_up = (symbol or "").strip().upper()
    if "BANK" in sym_up:
        base_price = 62050.0
        scale = 2.4
    elif "FIN" in sym_up:
        base_price = 28000.0
        scale = 1.1
    else:
        base_price = 25250.0
        scale = 1.0

    if tick_offset is not None:
        base_price += tick_offset

    now = datetime.datetime.now()
    candles: List[Dict] = []

    # Generate 30 baseline 5m candles
    for i in range(30, 0, -1):
        c_time = int((now - datetime.timedelta(minutes=i * 5)).timestamp())
        step_drift = math.sin(i * 0.4) * (15.0 * scale)
        c_open = base_price + step_drift
        c_high = c_open + (14.0 * scale)
        c_low = c_open - (12.0 * scale)
        c_close = c_open + ((8.0 if i % 2 == 0 else -6.0) * scale)
        c_vol = int((65000 + (i * 1200)) * (0.8 if "BANK" in sym_up else 1.0))
        candles.append({
            "time": c_time,
            "open": round(c_open, 2),
            "high": round(c_high, 2),
            "low": round(c_low, 2),
            "close": round(c_close, 2),
            "volume": c_vol,
        })

    # Scenario modifications on the last 3-4 candles
    curr_t = int(now.timestamp())
    prev_t1 = curr_t - 300
    prev_t2 = curr_t - 600

    sc = scenario.lower()

    vol_factor = 0.8 if "BANK" in sym_up else 1.0

    if sc in ("call", "bullish"):
        # Candle -2: Yellow accumulation candle (narrow range, body small, normal volume)
        y_open = base_price
        y_high = base_price + (10.0 * scale)
        y_low = base_price - (8.0 * scale)
        y_close = base_price + (2.0 * scale)
        candles[-2] = {"time": prev_t1, "open": round(y_open, 2), "high": round(y_high, 2), "low": round(y_low, 2), "close": round(y_close, 2), "volume": int(72000 * vol_factor)}

        # Current Candle: Blue momentum breakout (breaks y_high, large green body, 2.1x volume)
        b_open = base_price + (3.0 * scale)
        b_close = base_price + (45.0 * scale)
        b_high = base_price + (48.0 * scale)
        b_low = base_price + (1.0 * scale)
        b_vol = int(170000 * vol_factor)
        candles[-1] = {"time": curr_t, "open": round(b_open, 2), "high": round(b_high, 2), "low": round(b_low, 2), "close": round(b_close, 2), "volume": b_vol}
        override_time = datetime.time(10, 15)  # Approved morning window

    elif sc in ("put", "bearish"):
        # Candle -2: Yellow accumulation/distribution candle
        y_open = base_price
        y_high = base_price + (8.0 * scale)
        y_low = base_price - (9.0 * scale)
        y_close = base_price - (2.0 * scale)
        candles[-2] = {"time": prev_t1, "open": round(y_open, 2), "high": round(y_high, 2), "low": round(y_low, 2), "close": round(y_close, 2), "volume": int(70000 * vol_factor)}

        # Current Candle: Black momentum breakdown (breaks y_low, large red body, 2.2x volume)
        b_open = base_price - (3.0 * scale)
        b_close = base_price - (48.0 * scale)
        b_high = base_price - (1.0 * scale)
        b_low = base_price - (50.0 * scale)
        b_vol = int(175000 * vol_factor)
        candles[-1] = {"time": curr_t, "open": round(b_open, 2), "high": round(b_high, 2), "low": round(b_low, 2), "close": round(b_close, 2), "volume": b_vol}
        override_time = datetime.time(14, 0)  # Approved afternoon window

    elif sc in ("yellow", "accumulation"):
        # Current Candle is Yellow (Smart Money Accumulation)
        y_open = base_price
        y_high = base_price + (6.0 * scale)
        y_low = base_price - (6.0 * scale)
        y_close = base_price + (1.0 * scale)
        candles[-1] = {"time": curr_t, "open": round(y_open, 2), "high": round(y_high, 2), "low": round(y_low, 2), "close": round(y_close, 2), "volume": int(68000 * vol_factor)}
        override_time = datetime.time(10, 30)

    elif sc in ("sideways", "filter"):
        # Sideways choppy range with very low volume
        for j in range(-5, 0):
            candles[j]["high"] = round(base_price + (4.0 * scale), 2)
            candles[j]["low"] = round(base_price - (4.0 * scale), 2)
            candles[j]["open"] = round(base_price - (1.0 * scale), 2)
            candles[j]["close"] = round(base_price + (1.0 * scale), 2)
            candles[j]["volume"] = int(26000 * vol_factor)  # < 50% average
        override_time = datetime.time(12, 15)  # Lunch time filter active

    else:
        # Dynamic live
        override_time = None

    return evaluate_gti_strategy(
        candles_5m=candles,
        symbol=symbol,
        spot_price=candles[-1]["close"],
        override_time=override_time,
    )
