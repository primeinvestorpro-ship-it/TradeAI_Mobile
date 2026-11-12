"""
Pure-Python technical signal logic for NSE Stock Futures.
No network calls, no Kite/AI dependency — every function takes plain data
in and returns plain data out, so it can be unit-tested with fake input.

futures_engine.py is responsible for fetching raw numbers (live ticks,
Kite historical API) and passing them to these functions.
"""

import math
from statistics import mean


# ---------------------------------------------------------------------------
# VWAP
# ---------------------------------------------------------------------------

def compute_vwap_from_candles(candles):
    """
    Compute VWAP from minute-candle list.
    candles: list of {"high": float, "low": float, "close": float, "volume": int}
    Returns VWAP float or raises ValueError if zero volume.
    """
    total_vol = 0
    total_tp_vol = 0.0
    for c in candles:
        typical_price = (c["high"] + c["low"] + c["close"]) / 3
        vol = c.get("volume", 0) or 0
        total_tp_vol += typical_price * vol
        total_vol += vol
    if total_vol <= 0:
        raise ValueError("cannot compute VWAP: zero cumulative volume")
    return total_tp_vol / total_vol


def compute_vwap_bias(current_price, vwap, neutral_band_pct=0.0005):
    """
    current_price > vwap → BULLISH
    current_price < vwap → BEARISH
    Within +/- neutral_band_pct band → NEUTRAL
    """
    if vwap is None or vwap <= 0:
        return {"vwap": vwap, "vwap_bias": "NEUTRAL"}
    band = vwap * neutral_band_pct
    if current_price > vwap + band:
        bias = "BULLISH"
    elif current_price < vwap - band:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"
    return {"vwap": vwap, "vwap_bias": bias}


# ---------------------------------------------------------------------------
# Opening Range Breakout (ORB)
# ---------------------------------------------------------------------------

def compute_orb_bias(current_price, orb_high, orb_low):
    """
    Breaking above opening-range high → BULLISH.
    Breaking below opening-range low  → BEARISH.
    Still inside the range             → NEUTRAL.
    """
    if orb_high is None or orb_low is None:
        return "NEUTRAL"
    if current_price > orb_high:
        return "BULLISH"
    if current_price < orb_low:
        return "BEARISH"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Gap analysis
# ---------------------------------------------------------------------------

def compute_gap_flag(daily_ranges, today_open, yesterday_close, gap_threshold_factor):
    """
    daily_ranges: list of (high - low) for recent completed trading days.
    Returns {"avg_range": float, "gap": float, "large_gap": bool}.
    """
    if not daily_ranges:
        raise ValueError("daily_ranges must not be empty")
    avg_range = mean(daily_ranges)
    gap = today_open - yesterday_close
    large_gap = abs(gap) > avg_range * gap_threshold_factor
    return {"avg_range": avg_range, "gap": gap, "large_gap": large_gap}


# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------

def compute_momentum_bias(points_moved_from_open, expected_move,
                           min_fraction=0.25, max_fraction=0.65):
    """
    Sizes today's already-realized move against the expected move.
    Returns {"momentum_bias": "BULLISH"|"BEARISH"|"NEUTRAL", "used_fraction": float|None}.
    """
    if expected_move is None or expected_move <= 0 or points_moved_from_open is None:
        return {"momentum_bias": "NEUTRAL", "used_fraction": None}
    used_fraction = abs(points_moved_from_open) / expected_move
    if min_fraction <= used_fraction <= max_fraction:
        bias = "BULLISH" if points_moved_from_open > 0 else "BEARISH"
    else:
        bias = "NEUTRAL"
    return {"momentum_bias": bias, "used_fraction": used_fraction}


# ---------------------------------------------------------------------------
# Expected move (ATR-based for stocks, no India VIX available per-stock)
# ---------------------------------------------------------------------------

def compute_atr_expected_move(daily_candles, lookback=14):
    """
    Compute ATR-based expected daily move for a stock.
    Uses True Range: max(H-L, |H-prev_close|, |L-prev_close|).
    Returns the ATR value (expected move in price points) or None.
    """
    if len(daily_candles) < 2:
        return None
    trs = []
    for i in range(1, min(lookback + 1, len(daily_candles))):
        c = daily_candles[i]
        prev = daily_candles[i - 1]
        tr = max(
            c["high"] - c["low"],
            abs(c["high"] - prev["close"]),
            abs(c["low"] - prev["close"]),
        )
        trs.append(tr)
    if not trs:
        return None
    return sum(trs) / len(trs)


def compute_avg_open_to_close_range(daily_candles):
    """Average |close - open| across recent completed daily candles."""
    if not daily_candles:
        return None
    diffs = [abs(c["close"] - c["open"]) for c in daily_candles]
    return sum(diffs) / len(diffs)


# ---------------------------------------------------------------------------
# CPR (Central Pivot Range)
# ---------------------------------------------------------------------------

def compute_cpr_width(prev_high, prev_low, prev_close):
    """
    CPR width from previous day's H/L/C.
    Narrow CPR → more likely a trend day tomorrow.
    """
    pivot = (prev_high + prev_low + prev_close) / 3
    bc = (prev_high + prev_low) / 2
    tc = 2 * pivot - bc
    return abs(tc - bc)


# ---------------------------------------------------------------------------
# Bias combining
# ---------------------------------------------------------------------------

def combine_bias(*biases):
    """
    Majority vote: any 2+ of N agree → that direction.
    Otherwise NEUTRAL.
    """
    bullish = biases.count("BULLISH")
    bearish = biases.count("BEARISH")
    if bullish >= 2 and bullish > bearish:
        return "BULLISH"
    if bearish >= 2 and bearish > bullish:
        return "BEARISH"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Simple EMA helpers (for dashboard display)
# ---------------------------------------------------------------------------

def compute_ema(prices, period):
    """
    Compute EMA over a list of prices (oldest first).
    Returns the final EMA value or None if insufficient data.
    """
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = price * k + ema * (1 - k)
    return ema


def compute_ema_series(prices, period):
    """
    Compute full EMA series over a list of prices (oldest first).
    Returns list of the same length as prices, where indices before
    period - 1 are None, and subsequent indices contain the EMA value.
    """
    n = len(prices)
    if n < period or period <= 0:
        return [None] * n

    series = [None] * (period - 1)
    k = 2.0 / (period + 1.0)
    ema = sum(prices[:period]) / float(period)
    series.append(ema)

    for price in prices[period:]:
        ema = price * k + ema * (1.0 - k)
        series.append(ema)

    return series


def compute_rsi(prices, period=14):
    """
    Compute RSI from a list of closing prices (oldest first).
    Returns float 0–100 or None if insufficient data.
    """
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    # Initial averages
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_macd(prices, fast=12, slow=26, signal=9):
    """
    Compute MACD line, signal line, and histogram from closing prices (oldest first).
    Returns {"macd": float, "signal": float, "histogram": float} or None.
    """
    if len(prices) < slow + signal:
        return None
    ema_fast = compute_ema(prices, fast)
    ema_slow = compute_ema(prices, slow)
    if ema_fast is None or ema_slow is None:
        return None
    macd_line = ema_fast - ema_slow
    # For a proper signal line we'd need a list of MACD values — simplified here
    return {"macd": macd_line, "signal": None, "histogram": None}


# ---------------------------------------------------------------------------
# WMA (Weighted Moving Average) & ADX / Directional Index
# Setup reference: https://chartink.com/screener/my-intraday-buy-setup-adx
# ---------------------------------------------------------------------------

def compute_wma(values, period=10):
    """
    Weighted Moving Average over the last `period` elements.
    Weights are 1, 2, ..., period (linearly increasing toward most recent).
    """
    if len(values) < period:
        return None
    window = values[-period:]
    weights = list(range(1, period + 1))
    total_weight = sum(weights)
    weighted_sum = sum(w * v for w, v in zip(weights, window))
    return weighted_sum / total_weight


def compute_dmi(candles, period=14):
    """
    Compute Wilder's +DI, -DI, and ADX over a series of candles.
    Each candle has {"high": float, "low": float, "close": float}.
    Returns {"plus_di": float, "minus_di": float, "adx": float} or None.
    """
    if len(candles) < period + 1:
        return None

    plus_dms = []
    minus_dms = []
    trs = []

    for i in range(1, len(candles)):
        c = candles[i]
        prev = candles[i - 1]
        up_move = c["high"] - prev["high"]
        down_move = prev["low"] - c["low"]

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr = max(
            c["high"] - c["low"],
            abs(c["high"] - prev["close"]),
            abs(c["low"] - prev["close"]),
        )
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)
        trs.append(tr)

    if len(trs) < period:
        return None

    # Wilder smoothing
    smooth_tr = sum(trs[:period])
    smooth_plus_dm = sum(plus_dms[:period])
    smooth_minus_dm = sum(minus_dms[:period])

    dx_list = []
    for i in range(period, len(trs)):
        smooth_tr = smooth_tr - (smooth_tr / period) + trs[i]
        smooth_plus_dm = smooth_plus_dm - (smooth_plus_dm / period) + plus_dms[i]
        smooth_minus_dm = smooth_minus_dm - (smooth_minus_dm / period) + minus_dms[i]

        p_di = (100 * smooth_plus_dm / smooth_tr) if smooth_tr > 0 else 0.0
        m_di = (100 * smooth_minus_dm / smooth_tr) if smooth_tr > 0 else 0.0
        di_sum = p_di + m_di
        dx = (100 * abs(p_di - m_di) / di_sum) if di_sum > 0 else 0.0
        dx_list.append((p_di, m_di, dx))

    if not dx_list:
        p_di = (100 * smooth_plus_dm / smooth_tr) if smooth_tr > 0 else 0.0
        m_di = (100 * smooth_minus_dm / smooth_tr) if smooth_tr > 0 else 0.0
        di_sum = p_di + m_di
        dx = (100 * abs(p_di - m_di) / di_sum) if di_sum > 0 else 0.0
        return {"plus_di": round(p_di, 2), "minus_di": round(m_di, 2), "adx": round(dx, 2)}

    last_p_di, last_m_di, _ = dx_list[-1]
    adx = sum(d[2] for d in dx_list[-period:]) / min(len(dx_list), period)
    return {
        "plus_di": round(last_p_di, 2),
        "minus_di": round(last_m_di, 2),
        "adx": round(adx, 2),
    }


def evaluate_chartink_adx_setup(candles_5min, prev_day_hlc=None, current_open=None):
    """
    Evaluates the 'My-intraday-buy-setup-ADX' volume and momentum screener:
    Setup criteria:
      1. close > EMA(8) [5_min]
      2. close > EMA(34) [5_min]
      3. close > EMA(50) [5_min]
      4. volume > WMA(volume, 10) [5_min]
      5. +DI(14) >= 24 [5_min]
      6. open >= yesterday's pivot (optional confirmation)
      7. close >= yesterday's R1 / pivot (optional confirmation)

    Returns:
    {
        "status": "PASSED" | "PARTIAL" | "FAILED",
        "passed_rules": int,
        "total_rules": int,
        "rules": dict,
        "values": dict,
    }
    """
    if not candles_5min or len(candles_5min) < 10:
        return {
            "status": "UNKNOWN",
            "passed_rules": 0,
            "total_rules": 5,
            "rules": {},
            "values": {},
            "summary": "Insufficient 5-minute candle data to evaluate setup",
        }

    closes = [c["close"] for c in candles_5min]
    volumes = [c.get("volume", 0) for c in candles_5min]
    last_close = closes[-1]
    last_vol = volumes[-1]

    ema8 = compute_ema(closes, 8)
    ema34 = compute_ema(closes, 34)
    ema50 = compute_ema(closes, 50)
    wma10_vol = compute_wma(volumes, 10)
    dmi = compute_dmi(candles_5min, 14)

    plus_di = dmi["plus_di"] if dmi else None
    adx_val = dmi["adx"] if dmi else None

    # Check the 5 core rules
    rule_ema8 = (last_close > ema8) if ema8 is not None else False
    rule_ema34 = (last_close > ema34) if ema34 is not None else False
    rule_ema50 = (last_close > ema50) if ema50 is not None else False
    rule_vol_wma = (last_vol > wma10_vol) if wma10_vol is not None else False
    rule_plus_di = (plus_di is not None and plus_di >= 24.0)

    # Optional pivot rules if yesterday's H/L/C provided
    rule_pivot_open = True
    rule_pivot_close = True
    pivot_val = None
    r1_val = None
    if prev_day_hlc:
        h, l, c = prev_day_hlc.get("high"), prev_day_hlc.get("low"), prev_day_hlc.get("close")
        if h and l and c:
            pivot_val = (h + l + c) / 3
            r1_val = 2 * pivot_val - l
            if current_open is not None:
                rule_pivot_open = current_open >= (pivot_val * 0.998)
            rule_pivot_close = last_close >= pivot_val

    rules = {
        "close_gt_ema8": rule_ema8,
        "close_gt_ema34": rule_ema34,
        "close_gt_ema50": rule_ema50,
        "volume_gt_10wma": rule_vol_wma,
        "plus_di_ge_24": rule_plus_di,
    }

    core_passed = sum(1 for r in rules.values() if r)
    total_core = len(rules)

    if core_passed == total_core:
        status = "PASSED"
    elif core_passed >= 3:
        status = "PARTIAL"
    else:
        status = "FAILED"

    vol_mult = (last_vol / wma10_vol) if (wma10_vol and wma10_vol > 0) else None

    return {
        "status": status,
        "passed_rules": core_passed,
        "total_rules": total_core,
        "rules": rules,
        "values": {
            "close": last_close,
            "ema8": round(ema8, 2) if ema8 else None,
            "ema34": round(ema34, 2) if ema34 else None,
            "ema50": round(ema50, 2) if ema50 else None,
            "last_volume": last_vol,
            "wma10_volume": round(wma10_vol, 0) if wma10_vol else None,
            "volume_multiple": round(vol_mult, 2) if vol_mult else None,
            "plus_di": plus_di,
            "adx": adx_val,
            "pivot": round(pivot_val, 2) if pivot_val else None,
            "r1": round(r1_val, 2) if r1_val else None,
        },
        "screener_url": "https://chartink.com/screener/my-intraday-buy-setup-adx",
        "summary": (
            f"Chartink ADX Setup: {status} ({core_passed}/{total_core} rules satisfied). "
            f"+DI={plus_di or 'N/A'}, Vol={vol_mult or 'N/A'}x 10-WMA."
        ),
    }


def compute_supertrend_series(candles, period=7, multiplier=3.0):
    """
    Compute Supertrend indicator series for all candles.
    candles: list of {"high", "low", "close"} dicts (oldest first)
    period: ATR period (default 7)
    multiplier: ATR band multiplier (default 3.0)

    Returns a list of dicts (same length as candles) with keys:
      {"supertrend": float, "trend": "UP"|"DOWN", "upper_band": float, "lower_band": float}
    or None entries for indices < period.
    Returns None if total candles < period + 1.
    """
    if len(candles) < period + 1:
        return None

    # Compute ATR via Wilder smoothing
    trs = []
    for i in range(1, len(candles)):
        c = candles[i]
        p = candles[i - 1]
        tr = max(
            c["high"] - c["low"],
            abs(c["high"] - p["close"]),
            abs(c["low"] - p["close"]),
        )
        trs.append(tr)

    # Wilder smoothed ATR
    atr_list = [None] * len(candles)
    first_atr = sum(trs[:period]) / period
    atr_list[period] = first_atr
    for i in range(period + 1, len(candles)):
        atr_list[i] = (atr_list[i - 1] * (period - 1) + trs[i - 1]) / period

    # Compute Supertrend
    upper_bands = [None] * len(candles)
    lower_bands = [None] * len(candles)
    supertrend  = [None] * len(candles)
    trend_up    = [True] * len(candles)  # True=UP, False=DOWN

    for i in range(period, len(candles)):
        c = candles[i]
        atr = atr_list[i]
        if atr is None:
            continue
        hl2 = (c["high"] + c["low"]) / 2
        basic_upper = hl2 + multiplier * atr
        basic_lower = hl2 - multiplier * atr

        if i == period:
            upper_bands[i] = basic_upper
            lower_bands[i] = basic_lower
            trend_up[i] = c["close"] >= basic_lower
        else:
            # Final Upper Band
            prev_upper = upper_bands[i - 1]
            upper_bands[i] = (
                min(basic_upper, prev_upper)
                if candles[i - 1]["close"] < prev_upper
                else basic_upper
            )
            # Final Lower Band
            prev_lower = lower_bands[i - 1]
            lower_bands[i] = (
                max(basic_lower, prev_lower)
                if candles[i - 1]["close"] > prev_lower
                else basic_lower
            )
            # Trend
            if trend_up[i - 1]:
                trend_up[i] = c["close"] >= lower_bands[i]
            else:
                trend_up[i] = c["close"] > upper_bands[i]

        supertrend[i] = lower_bands[i] if trend_up[i] else upper_bands[i]

    series = []
    for i in range(len(candles)):
        if supertrend[i] is None:
            series.append(None)
        else:
            series.append({
                "supertrend": round(supertrend[i], 4),
                "trend": "UP" if trend_up[i] else "DOWN",
                "upper_band": round(upper_bands[i], 4) if upper_bands[i] is not None else None,
                "lower_band": round(lower_bands[i], 4) if lower_bands[i] is not None else None,
            })
    return series


def compute_supertrend(candles, period=7, multiplier=3.0):
    """
    Compute Supertrend indicator for the last candle.
    Returns dict {"supertrend": float, "trend": "UP"|"DOWN"} or None.
    """
    series = compute_supertrend_series(candles, period=period, multiplier=multiplier)
    if not series or series[-1] is None:
        return None
    return {
        "supertrend": series[-1]["supertrend"],
        "trend": series[-1]["trend"],
    }
