"""
Pure-Python scanner strategy evaluators.

Strategy A — Bullish Futures Setup (7 conditions from Chartink ADX screener).
Strategy B — Fully Bearish Futures Setup (10 conditions, one OR group).

No network calls. All functions take plain dicts/lists and return plain dicts.
This makes them fully unit-testable without Kite or any broker dependency.

DISCLAIMER: For educational and research purposes only. Not financial advice.
"""

import datetime
from typing import Optional

import futures_signal


# ---------------------------------------------------------------------------
# Signal schema helpers
# ---------------------------------------------------------------------------

def _make_signal(strategy_id, strategy_name, direction, symbol,
                 futures_symbol, expiry, candle_ts, conditions,
                 spot_price=None, futures_price=None, atm_option=None, values=None):
    """Build a standardised signal dict."""
    return {
        "id": f"{strategy_id}_{symbol}_{candle_ts.strftime('%Y%m%d_%H%M') if hasattr(candle_ts, 'strftime') else candle_ts}",
        "strategy_id":    strategy_id,
        "strategy_name":  strategy_name,
        "direction":      direction,
        "symbol":         symbol,
        "futures_symbol": futures_symbol,
        "expiry":         str(expiry) if expiry else None,
        "timestamp":      candle_ts.isoformat() if hasattr(candle_ts, 'isoformat') else str(candle_ts),
        "timeframe":      "5minute",
        "spot_price":     spot_price,
        "futures_price":  futures_price,
        "conditions":     conditions,
        "values":         values or {},
        "atm_option":     atm_option,
        "status":         "NEW",
    }


# ---------------------------------------------------------------------------
# Strategy A — Bullish Futures Setup (7 conditions)
# ---------------------------------------------------------------------------

STRATEGY_A_ID   = "A"
STRATEGY_A_NAME = "Bullish Futures Setup"


def evaluate_strategy_a(candles_5min, prev_day_hlc, current_day_open,
                        symbol, futures_symbol, expiry,
                        candle_ts=None, spot_price=None, futures_price=None,
                        atm_option=None):
    """
    Evaluate Strategy A — Bullish Futures Setup.

    Required inputs:
      candles_5min: list of {"high", "low", "close", "volume"} — oldest first.
                    Must have >= 55 candles for EMA(50) warmup.
      prev_day_hlc: {"high": float, "low": float, "close": float}
      current_day_open: float — today's opening price of the stock.
      symbol: str — e.g. "RELIANCE"
      futures_symbol: str — e.g. "RELIANCE26OCTFUT"
      expiry: date or str
      candle_ts: datetime — timestamp of the last evaluated candle

    The 7 conditions:
      1. 5m close > EMA(close, 8)
      2. 5m close > EMA(close, 34)
      3. 5m close > EMA(close, 50)
      4. 5m volume > WMA(volume, 10)
      5. 5m +DI(14) >= 24
      6. Daily open >= (prevH + prevL + prevC)/3 - prevC/200
      7. 5m close >= 2*((prevH + prevL + prevC)/3) - prevL - 1

    Returns:
      {"passed": bool, "signal": dict|None, "conditions": dict, "values": dict}
    """
    if not candles_5min or len(candles_5min) < 10:
        return {
            "passed": False, "signal": None,
            "conditions": {}, "values": {},
            "reason": "Insufficient 5m candles",
        }

    closes  = [c["close"]  for c in candles_5min]
    volumes = [c.get("volume", 0) for c in candles_5min]
    last_close = closes[-1]
    last_vol   = volumes[-1]

    ema8  = futures_signal.compute_ema(closes, 8)
    ema34 = futures_signal.compute_ema(closes, 34)
    ema50 = futures_signal.compute_ema(closes, 50)
    wma10 = futures_signal.compute_wma(volumes, 10)
    dmi   = futures_signal.compute_dmi(candles_5min, 14)
    plus_di = dmi["plus_di"] if dmi else None

    # Conditions 1-5
    c1 = (last_close > ema8)  if ema8  is not None else False
    c2 = (last_close > ema34) if ema34 is not None else False
    c3 = (last_close > ema50) if ema50 is not None else False
    c4 = (last_vol > wma10)   if wma10 is not None else False
    c5 = (plus_di is not None and plus_di >= 24.0)

    # Conditions 6-7 (daily pivot formulas)
    c6 = c7 = False
    pivot_val = cond6_threshold = cond7_threshold = None
    h = prev_day_hlc.get("high")  if prev_day_hlc else None
    l = prev_day_hlc.get("low")   if prev_day_hlc else None
    c_prev = prev_day_hlc.get("close") if prev_day_hlc else None
    if h is not None and l is not None and c_prev is not None and current_day_open is not None:
        pivot_val = (h + l + c_prev) / 3
        # Condition 6: Daily open >= pivot - prevClose/200
        cond6_threshold = pivot_val - c_prev / 200
        c6 = current_day_open >= cond6_threshold
        # Condition 7: 5m close >= 2*pivot - prevLow - 1
        cond7_threshold = 2 * pivot_val - l - 1
        c7 = last_close >= cond7_threshold

    conditions = {
        "c1_close_gt_ema8":    c1,
        "c2_close_gt_ema34":   c2,
        "c3_close_gt_ema50":   c3,
        "c4_volume_gt_10wma":  c4,
        "c5_plus_di_ge_24":    c5,
        "c6_daily_open_pivot": c6,
        "c7_5m_close_r1":      c7,
    }
    values = {
        "close":           last_close,
        "ema8":            round(ema8,  2) if ema8  else None,
        "ema34":           round(ema34, 2) if ema34 else None,
        "ema50":           round(ema50, 2) if ema50 else None,
        "last_volume":     last_vol,
        "wma10_volume":    round(wma10, 0) if wma10 else None,
        "plus_di":         plus_di,
        "daily_open":      current_day_open,
        "pivot":           round(pivot_val, 2) if pivot_val else None,
        "c6_threshold":    round(cond6_threshold, 2) if cond6_threshold else None,
        "c7_threshold":    round(cond7_threshold, 2) if cond7_threshold else None,
        "prev_day_high":   h,
        "prev_day_low":    l,
        "prev_day_close":  c_prev,
    }

    all_passed = all(conditions.values())
    signal = None
    if all_passed:
        ts = candle_ts or datetime.datetime.now()
        signal = _make_signal(
            STRATEGY_A_ID, STRATEGY_A_NAME, "BULLISH",
            symbol, futures_symbol, expiry, ts,
            conditions, spot_price, futures_price, atm_option, values=values,
        )

    return {
        "passed":     all_passed,
        "signal":     signal,
        "conditions": conditions,
        "values":     values,
    }


# ---------------------------------------------------------------------------
# Strategy B — Fully Bearish Futures Setup (10 conditions)
# ---------------------------------------------------------------------------

STRATEGY_B_ID   = "B"
STRATEGY_B_NAME = "Fully Bearish Setup"


def evaluate_strategy_b(
    candles_5min,
    candles_15min,
    daily_candles,
    symbol, futures_symbol, expiry,
    candle_ts=None, spot_price=None, futures_price=None,
    atm_option=None,
    min_daily_volume=500000,
    min_daily_close=90.0,
    max_daily_close=700.0,
    supertrend_period=7,
    supertrend_multiplier=3.0,
):
    """
    Evaluate Strategy B — Fully Bearish Futures Setup.

    Groups:
      A. 15m trend (both):
         1. 15m close < EMA(close, 8)
         2. 15m close < EMA(close, 34)
      B. Daily filters (all):
         3. daily volume > min_daily_volume (default 500000)
         4. daily close > min_daily_close (default 90)
         5. daily close < max_daily_close (default 700)
      C. Prev-day breakdown OR group (at least one):
         6. daily close < prev_day_low
         7. daily close < prev_day_close
      D. Daily Supertrend (must pass):
         8. daily close < Supertrend(supertrend_period, supertrend_multiplier)
      E. 5m consecutive declining closes (all three):
         9.  5m close[-1] < 5m close[-2]
         10. 5m close[-2] < 5m close[-3]
         11. 5m close[-3] < 5m close[-4]  (indexes: 0=oldest, -1=newest)

    Returns:
      {"passed": bool, "signal": dict|None, "conditions": dict, "values": dict}
    """
    missing = []
    if not candles_5min  or len(candles_5min)  < 4:  missing.append("5m candles (need >= 4)")
    if not candles_15min or len(candles_15min) < 40: missing.append("15m candles (need >= 40)")
    if not daily_candles or len(daily_candles) < 3:  missing.append("daily candles (need >= 3)")
    if missing:
        return {
            "passed": False, "signal": None,
            "conditions": {}, "values": {},
            "reason": "Insufficient data: " + ", ".join(missing),
        }

    # ── Group A: 15m trend ───────────────────────
    closes_15m = [c["close"] for c in candles_15min]
    last_15m   = closes_15m[-1]
    ema8_15m   = futures_signal.compute_ema(closes_15m, 8)
    ema34_15m  = futures_signal.compute_ema(closes_15m, 34)

    c1 = (last_15m < ema8_15m)  if ema8_15m  is not None else False
    c2 = (last_15m < ema34_15m) if ema34_15m is not None else False

    # ── Group B: Daily filters ───────────────────
    today_daily = daily_candles[-1]
    today_vol   = today_daily.get("volume", 0) or 0
    today_close = today_daily.get("close", 0)

    c3 = today_vol   > min_daily_volume
    c4 = today_close > min_daily_close
    c5 = today_close < max_daily_close

    # ── Group C: Prev-day OR breakdown ──────────
    prev_daily      = daily_candles[-2]
    prev_day_low    = prev_daily.get("low")
    prev_day_close  = prev_daily.get("close")

    c6 = (today_close < prev_day_low)   if prev_day_low   is not None else False
    c7 = (today_close < prev_day_close) if prev_day_close is not None else False
    group_c_passed  = c6 or c7

    # ── Group D: Daily Supertrend ────────────────
    st_result  = futures_signal.compute_supertrend(daily_candles, supertrend_period, supertrend_multiplier)
    st_val     = st_result["supertrend"] if st_result else None
    c8         = (today_close < st_val) if st_val is not None else False

    # ── Group E: 5m consecutive declining closes ─
    closes_5m  = [c["close"] for c in candles_5min]
    # Need last 4 closes: [-1], [-2], [-3], [-4]
    c9  = closes_5m[-1] < closes_5m[-2]
    c10 = closes_5m[-2] < closes_5m[-3]
    c11 = closes_5m[-3] < closes_5m[-4] if len(closes_5m) >= 4 else False

    conditions = {
        "c1_15m_close_lt_ema8":     c1,
        "c2_15m_close_lt_ema34":    c2,
        "c3_daily_vol_gt_500k":      c3,
        "c4_daily_close_gt_90":      c4,
        "c5_daily_close_lt_700":     c5,
        "c6_close_lt_prev_low":      c6,
        "c7_close_lt_prev_close":    c7,
        "c8_close_lt_supertrend":    c8,
        "c9_5m_decline_1":           c9,
        "c10_5m_decline_2":          c10,
        "c11_5m_decline_3":          c11,
        "group_c_or_passed":         group_c_passed,
    }
    values = {
        "last_15m_close":    last_15m,
        "ema8_15m":          round(ema8_15m,  2) if ema8_15m  else None,
        "ema34_15m":         round(ema34_15m, 2) if ema34_15m else None,
        "daily_volume":      today_vol,
        "daily_close":       today_close,
        "min_daily_volume":  min_daily_volume,
        "min_daily_close":   min_daily_close,
        "max_daily_close":   max_daily_close,
        "prev_day_low":      prev_day_low,
        "prev_day_close":    prev_day_close,
        "supertrend":        st_val,
        "supertrend_period": supertrend_period,
        "supertrend_mult":   supertrend_multiplier,
        "5m_closes": [
            round(closes_5m[-4], 2) if len(closes_5m) >= 4 else None,
            round(closes_5m[-3], 2) if len(closes_5m) >= 3 else None,
            round(closes_5m[-2], 2) if len(closes_5m) >= 2 else None,
            round(closes_5m[-1], 2),
        ],
    }

    # Final pass condition
    group_a = c1 and c2
    group_b = c3 and c4 and c5
    group_d = c8
    group_e = c9 and c10 and c11
    all_passed = group_a and group_b and group_c_passed and group_d and group_e

    signal = None
    if all_passed:
        ts = candle_ts or datetime.datetime.now()
        signal = _make_signal(
            STRATEGY_B_ID, STRATEGY_B_NAME, "BEARISH",
            symbol, futures_symbol, expiry, ts,
            conditions, spot_price, futures_price, atm_option, values=values,
        )

    return {
        "passed":     all_passed,
        "signal":     signal,
        "conditions": conditions,
        "values":     values,
    }


# ---------------------------------------------------------------------------
# Strategy C — Trend + Supertrend Setup (EMA 20/50/100 + Supertrend 7,3)
# ---------------------------------------------------------------------------

STRATEGY_C_ID   = "C"
STRATEGY_C_NAME = "EMA 20/50/100 + Supertrend"


def evaluate_strategy_c(
    candles_5min,
    symbol="NIFTY",
    futures_symbol=None,
    expiry=None,
    candle_ts=None,
    spot_price=None,
    futures_price=None,
    atm_option=None,
    ema_fast=20,
    ema_mid=50,
    ema_slow=100,
    supertrend_period=7,
    supertrend_multiplier=3.0,
    min_sustain_candles=1,
    max_lookback=25,
    confirm_level="high",
):
    """
    Evaluate Strategy C — F&O Trend + Supertrend Setup.
    Intraday 5-minute candles:
      BULLISH (BUY / ATM CE):
        1. Close > EMA(Close, 20)
        2. Close > EMA(Close, 50)
        3. Close > EMA(Close, 100)
        4. Supertrend(7, 3) currently in Uptrend (trend == 'UP')
        5. Supertrend Buy signal (flip from DOWN to UP) generated and sustained
           for at least `min_sustain_candles` (default 1) without flipping back.
        6. Further candle(s) open higher than that signal candle (Open > High/Close).
      BEARISH (SELL / Short / ATM PE):
        1. Close < EMA(Close, 20)
        2. Close < EMA(Close, 50)
        3. Close < EMA(Close, 100)
        4. Supertrend(7, 3) currently in Downtrend (trend == 'DOWN')
        5. Supertrend Sell signal (flip from UP to DOWN) generated and sustained
           for at least `min_sustain_candles` (default 1) without flipping back.
        6. Further candle(s) open lower than that signal candle (Open < Low/Close).

    Returns:
      {"passed": bool, "direction": "BULLISH"|"BEARISH"|None,
       "signal": dict|None, "conditions": dict, "values": dict}
    """
    min_candles = max(ema_slow + 5, supertrend_period + 5)
    if not candles_5min or len(candles_5min) < min_candles:
        return {
            "passed": False,
            "direction": None,
            "signal": None,
            "conditions": {},
            "values": {},
            "reason": f"Insufficient 5m candles (need >= {min_candles})",
        }

    closes = [c["close"] for c in candles_5min]
    last_idx = len(candles_5min) - 1
    last_candle = candles_5min[last_idx]
    last_close = last_candle["close"]
    last_open = last_candle.get("open", last_close)

    ema_f = futures_signal.compute_ema(closes, ema_fast)
    ema_m = futures_signal.compute_ema(closes, ema_mid)
    ema_s = futures_signal.compute_ema(closes, ema_slow)

    st_series = futures_signal.compute_supertrend_series(candles_5min, supertrend_period, supertrend_multiplier)
    if not st_series or st_series[last_idx] is None:
        return {
            "passed": False,
            "direction": None,
            "signal": None,
            "conditions": {},
            "values": {},
            "reason": "Could not compute Supertrend series",
        }

    st_last = st_series[last_idx]
    st_val = st_last["supertrend"]
    st_trend = st_last["trend"]

    # ------------------------------------------------------------------
    # Detect most recent Supertrend signal flip & sustain
    # ------------------------------------------------------------------
    # Search backwards from last_idx down to max_lookback
    bull_sig_idx = None
    bear_sig_idx = None
    search_start = max(supertrend_period + 1, last_idx - max_lookback)

    for i in range(last_idx, search_start - 1, -1):
        curr_st = st_series[i]
        prev_st = st_series[i - 1]
        if curr_st is None or prev_st is None:
            continue

        # Flip to UP
        if bull_sig_idx is None and curr_st["trend"] == "UP" and prev_st["trend"] == "DOWN":
            # Verify Supertrend has continuously stayed UP from i to last_idx (no intermediate flips)
            if all(st_series[k] and st_series[k]["trend"] == "UP" for k in range(i, last_idx + 1)):
                bull_sig_idx = i

        # Flip to DOWN
        if bear_sig_idx is None and curr_st["trend"] == "DOWN" and prev_st["trend"] == "UP":
            # Verify Supertrend has continuously stayed DOWN from i to last_idx
            if all(st_series[k] and st_series[k]["trend"] == "DOWN" for k in range(i, last_idx + 1)):
                bear_sig_idx = i

        if bull_sig_idx is not None and bear_sig_idx is not None:
            break

    # If the entire lookback window has been continuously UP or DOWN with no flip observed within lookback,
    # find if the continuous run began at or before search_start
    if bull_sig_idx is None and st_trend == "UP":
        if all(st_series[k] and st_series[k]["trend"] == "UP" for k in range(search_start, last_idx + 1)):
            bull_sig_idx = search_start

    if bear_sig_idx is None and st_trend == "DOWN":
        if all(st_series[k] and st_series[k]["trend"] == "DOWN" for k in range(search_start, last_idx + 1)):
            bear_sig_idx = search_start

    # Sustained candle count after the signal candle
    bull_sustained = (last_idx - bull_sig_idx) if bull_sig_idx is not None else 0
    bear_sustained = (last_idx - bear_sig_idx) if bear_sig_idx is not None else 0

    # Signal candles & reference thresholds
    bull_sig_candle = candles_5min[bull_sig_idx] if bull_sig_idx is not None else None
    bear_sig_candle = candles_5min[bear_sig_idx] if bear_sig_idx is not None else None

    # Reference levels for opening higher/lower
    if confirm_level == "close":
        bull_ref = bull_sig_candle["close"] if bull_sig_candle else None
        bear_ref = bear_sig_candle["close"] if bear_sig_candle else None
    else:  # default "high" (uses high for bull, low for bear)
        bull_ref = bull_sig_candle["high"] if bull_sig_candle else None
        bear_ref = bear_sig_candle["low"] if bear_sig_candle else None

    # Open higher confirmation: at least one candle strictly after the signal candle opened higher than ref
    bull_open_confirmed = False
    if bull_sig_candle and bull_ref is not None and bull_sustained >= min_sustain_candles:
        bull_open_confirmed = any(
            candles_5min[k].get("open", candles_5min[k]["close"]) > bull_ref
            for k in range(bull_sig_idx + 1, last_idx + 1)
        )

    # Open lower confirmation: at least one candle strictly after the signal candle opened lower than ref
    bear_open_confirmed = False
    if bear_sig_candle and bear_ref is not None and bear_sustained >= min_sustain_candles:
        bear_open_confirmed = any(
            candles_5min[k].get("open", candles_5min[k]["close"]) < bear_ref
            for k in range(bear_sig_idx + 1, last_idx + 1)
        )

    # Bullish checks (BUY)
    bull_c1 = (last_close > ema_f) if ema_f is not None else False
    bull_c2 = (last_close > ema_m) if ema_m is not None else False
    bull_c3 = (last_close > ema_s) if ema_s is not None else False
    bull_c4 = (st_trend == "UP" and (st_val is None or last_close > st_val))
    bull_c5 = bull_sustained >= min_sustain_candles
    bull_c6 = bull_open_confirmed
    bullish_passed = bull_c1 and bull_c2 and bull_c3 and bull_c4 and bull_c5 and bull_c6

    # Bearish checks (SELL / Short)
    bear_c1 = (last_close < ema_f) if ema_f is not None else False
    bear_c2 = (last_close < ema_m) if ema_m is not None else False
    bear_c3 = (last_close < ema_s) if ema_s is not None else False
    bear_c4 = (st_trend == "DOWN" and (st_val is None or last_close < st_val))
    bear_c5 = bear_sustained >= min_sustain_candles
    bear_c6 = bear_open_confirmed
    bearish_passed = bear_c1 and bear_c2 and bear_c3 and bear_c4 and bear_c5 and bear_c6

    direction = None
    passed = False
    sig_candle_used = None
    ref_price_used = None
    sustained_used = 0

    if bullish_passed:
        direction = "BULLISH"
        passed = True
        sig_candle_used = bull_sig_candle
        ref_price_used = bull_ref
        sustained_used = bull_sustained
        conditions = {
            f"c1_close_gt_ema{ema_fast}": bull_c1,
            f"c2_close_gt_ema{ema_mid}":  bull_c2,
            f"c3_close_gt_ema{ema_slow}": bull_c3,
            "c4_supertrend_up":          bull_c4,
            "c5_supertrend_sustained":   bull_c5,
            "c6_open_gt_signal_candle":  bull_c6,
        }
    elif bearish_passed:
        direction = "BEARISH"
        passed = True
        sig_candle_used = bear_sig_candle
        ref_price_used = bear_ref
        sustained_used = bear_sustained
        conditions = {
            f"c1_close_lt_ema{ema_fast}": bear_c1,
            f"c2_close_lt_ema{ema_mid}":  bear_c2,
            f"c3_close_lt_ema{ema_slow}": bear_c3,
            "c4_supertrend_down":        bear_c4,
            "c5_supertrend_sustained":   bear_c5,
            "c6_open_lt_signal_candle":  bear_c6,
        }
    else:
        sig_candle_used = bull_sig_candle if st_trend == "UP" else bear_sig_candle
        ref_price_used = bull_ref if st_trend == "UP" else bear_ref
        sustained_used = bull_sustained if st_trend == "UP" else bear_sustained
        conditions = {
            f"close_gt_ema{ema_fast}": bull_c1,
            f"close_gt_ema{ema_mid}":  bull_c2,
            f"close_gt_ema{ema_slow}": bull_c3,
            "supertrend_trend":        st_trend == "UP",
            "supertrend_sustained":    (bull_sustained >= min_sustain_candles) if st_trend == "UP" else (bear_sustained >= min_sustain_candles),
            "open_confirmed":          bull_open_confirmed if st_trend == "UP" else bear_open_confirmed,
        }

    values = {
        "close":               last_close,
        "open":                last_open,
        f"ema{ema_fast}":      round(ema_f, 2) if ema_f is not None else None,
        f"ema{ema_mid}":       round(ema_m, 2) if ema_m is not None else None,
        f"ema{ema_slow}":      round(ema_s, 2) if ema_s is not None else None,
        "supertrend":          round(st_val, 2) if st_val is not None else None,
        "supertrend_trend":    st_trend,
        "supertrend_period":   supertrend_period,
        "supertrend_mult":     supertrend_multiplier,
        "direction":           direction,
        "sustained_candles":   sustained_used,
        "min_sustain_candles": min_sustain_candles,
        "ref_price":           round(ref_price_used, 2) if ref_price_used is not None else None,
        "confirm_level":       confirm_level,
        "signal_candle_high":  round(sig_candle_used["high"], 2) if sig_candle_used else None,
        "signal_candle_low":   round(sig_candle_used["low"], 2) if sig_candle_used else None,
        "signal_candle_close": round(sig_candle_used["close"], 2) if sig_candle_used else None,
    }

    signal = None
    if passed and direction:
        ts = candle_ts or datetime.datetime.now()
        signal = _make_signal(
            STRATEGY_C_ID, STRATEGY_C_NAME, direction,
            symbol, futures_symbol, expiry, ts,
            conditions, spot_price, futures_price, atm_option, values=values,
        )

    return {
        "passed":     passed,
        "direction":  direction,
        "signal":     signal,
        "conditions": conditions,
        "values":     values,
    }


# ---------------------------------------------------------------------------
# Strategy D — EMA 50/150/200 Trend & Reversal (Swing Setup)
# ---------------------------------------------------------------------------

STRATEGY_D_ID   = "D"
STRATEGY_D_NAME = "EMA 50/150/200 Trend & Reversal"


def evaluate_strategy_d(
    candles,
    symbol="NIFTY",
    futures_symbol=None,
    expiry=None,
    candle_ts=None,
    spot_price=None,
    futures_price=None,
    atm_option=None,
    ema_fast=50,
    ema_mid=150,
    ema_slow=200,
    require_prev_day_breakout=True,
    prev_state=None,
    timeframe="daily",
):
    """
    Evaluate Strategy D — EMA 50/150/200 Trend & Reversal Swing Strategy.

    Conditions:
      BULLISH (BUY / Long Futures / ATM CE):
        1. Close > EMA(Close, 50)
        2. Close > EMA(Close, 150)
        3. Close > EMA(Close, 200)
        4. Next-day / breakout sustain: Close > Prev Day High (or High > Prev Day High & Close > Prev Close)
      BEARISH (SELL / Short Futures / ATM PE):
        1. Close < EMA(Close, 50)
        2. Close < EMA(Close, 150)
        3. Close < EMA(Close, 200)
        4. Next-day / breakout sustain: Close < Prev Day Low (or Low < Prev Day Low & Close < Prev Close)
      NEUTRAL:
        Price oscillating between EMAs -> No new signal.

    Signal Types & Reversal Rules:
      - NEUTRAL -> BULLISH : ENTRY (action="BUY")
      - BEARISH -> BULLISH : REVERSAL (action="EXIT BEARISH / BUY")
      - BULLISH -> BULLISH : TREND continuation (action="BUY (HOLDING)")
      - NEUTRAL -> BEARISH : ENTRY (action="SELL")
      - BULLISH -> BEARISH : REVERSAL (action="EXIT BULLISH / SELL")
      - BEARISH -> BEARISH : TREND continuation (action="SELL (HOLDING)")

    Returns:
      {"passed": bool, "direction": "BULLISH"|"BEARISH"|"NEUTRAL",
       "signal": dict|None, "conditions": dict, "values": dict,
       "signal_type": "ENTRY"|"REVERSAL"|"TREND"|"NEUTRAL", "action": str}
    """
    min_candles = ema_slow + 2
    if not candles or len(candles) < min_candles:
        return {
            "passed": False,
            "direction": "NEUTRAL",
            "signal": None,
            "conditions": {},
            "values": {},
            "signal_type": "NEUTRAL",
            "action": "NONE",
            "reason": f"Insufficient candles (need >= {min_candles}, got {len(candles) if candles else 0})",
        }

    closes = [c["close"] for c in candles]
    last_idx = len(candles) - 1
    last_candle = candles[last_idx]
    prev_candle = candles[last_idx - 1]

    last_close = float(last_candle["close"])
    last_open  = float(last_candle.get("open", last_close))
    last_high  = float(last_candle.get("high", last_close))
    last_low   = float(last_candle.get("low", last_close))

    prev_close = float(prev_candle["close"])
    prev_high  = float(prev_candle.get("high", prev_close))
    prev_low   = float(prev_candle.get("low", prev_close))

    ema_f_series = futures_signal.compute_ema_series(closes, ema_fast)
    ema_m_series = futures_signal.compute_ema_series(closes, ema_mid)
    ema_s_series = futures_signal.compute_ema_series(closes, ema_slow)

    ema_f = ema_f_series[last_idx]
    ema_m = ema_m_series[last_idx]
    ema_s = ema_s_series[last_idx]

    prev_ema_f = ema_f_series[last_idx - 1]
    prev_ema_m = ema_m_series[last_idx - 1]
    prev_ema_s = ema_s_series[last_idx - 1]

    if ema_f is None or ema_m is None or ema_s is None:
        return {
            "passed": False,
            "direction": "NEUTRAL",
            "signal": None,
            "conditions": {},
            "values": {},
            "signal_type": "NEUTRAL",
            "action": "NONE",
            "reason": "Could not compute 50/150/200 EMAs",
        }

    # Trend checks
    bull_c1 = last_close > ema_f
    bull_c2 = last_close > ema_m
    bull_c3 = last_close > ema_s
    curr_bullish = bull_c1 and bull_c2 and bull_c3

    bear_c1 = last_close < ema_f
    bear_c2 = last_close < ema_m
    bear_c3 = last_close < ema_s
    curr_bearish = bear_c1 and bear_c2 and bear_c3

    # Sustain / breakout check
    if require_prev_day_breakout:
        bull_c4 = (last_close > prev_high) or (last_high > prev_high and last_close >= prev_close)
        bear_c4 = (last_close < prev_low) or (last_low < prev_low and last_close <= prev_close)
    else:
        bull_c4 = True
        bear_c4 = True

    # Previous candle trend
    prev_was_bull = False
    prev_was_bear = False
    if prev_ema_f is not None and prev_ema_m is not None and prev_ema_s is not None:
        prev_was_bull = (prev_close > prev_ema_f and prev_close > prev_ema_m and prev_close > prev_ema_s)
        prev_was_bear = (prev_close < prev_ema_f and prev_close < prev_ema_m and prev_close < prev_ema_s)

    # Effective previous state (uses explicit prev_state if provided, else previous candle)
    effective_prev_state = prev_state
    if not effective_prev_state:
        if prev_was_bull:
            effective_prev_state = "BULLISH"
        elif prev_was_bear:
            effective_prev_state = "BEARISH"
        else:
            effective_prev_state = "NEUTRAL"

    direction = "NEUTRAL"
    signal_type = "NEUTRAL"
    action = "NONE"
    passed = False
    trend_desc = "BETWEEN EMAs"

    if curr_bullish and bull_c4:
        direction = "BULLISH"
        trend_desc = "ABOVE 50/150/200 EMA"
        passed = True
        if effective_prev_state == "BEARISH":
            signal_type = "REVERSAL"
            action = "EXIT BEARISH / BUY"
        elif effective_prev_state == "NEUTRAL" or not prev_was_bull:
            signal_type = "ENTRY"
            action = "BUY"
        else:
            signal_type = "TREND"
            action = "BUY (HOLDING)"

    elif curr_bearish and bear_c4:
        direction = "BEARISH"
        trend_desc = "BELOW 50/150/200 EMA"
        passed = True
        if effective_prev_state == "BULLISH":
            signal_type = "REVERSAL"
            action = "EXIT BULLISH / SELL"
        elif effective_prev_state == "NEUTRAL" or not prev_was_bear:
            signal_type = "ENTRY"
            action = "SELL"
        else:
            signal_type = "TREND"
            action = "SELL (HOLDING)"
    else:
        direction = "NEUTRAL"
        if curr_bullish:
            trend_desc = "ABOVE EMAs (Awaiting Breakout)"
        elif curr_bearish:
            trend_desc = "BELOW EMAs (Awaiting Breakout)"
        else:
            trend_desc = "BETWEEN 50/150/200 EMA"

    conditions = {
        f"close_gt_ema{ema_fast}": bull_c1,
        f"close_gt_ema{ema_mid}":  bull_c2,
        f"close_gt_ema{ema_slow}": bull_c3,
        f"close_lt_ema{ema_fast}": bear_c1,
        f"close_lt_ema{ema_mid}":  bear_c2,
        f"close_lt_ema{ema_slow}": bear_c3,
        "sustain_prev_day_breakout": bull_c4 if direction == "BULLISH" else (bear_c4 if direction == "BEARISH" else False),
    }

    values = {
        "close":                     round(last_close, 2),
        "open":                      round(last_open, 2),
        "high":                      round(last_high, 2),
        "low":                       round(last_low, 2),
        "prev_close":                round(prev_close, 2),
        "prev_high":                 round(prev_high, 2),
        "prev_low":                  round(prev_low, 2),
        f"ema{ema_fast}":            round(ema_f, 2),
        f"ema{ema_mid}":             round(ema_m, 2),
        f"ema{ema_slow}":            round(ema_s, 2),
        "direction":                 direction,
        "signal_type":               signal_type,
        "action":                    action,
        "trend_desc":                trend_desc,
        "prev_state":                effective_prev_state,
        "curr_state":                direction,
        "require_prev_day_breakout": require_prev_day_breakout,
        "timeframe":                 timeframe,
    }

    signal = None
    if passed and direction in ("BULLISH", "BEARISH"):
        ts = candle_ts or last_candle.get("date") or datetime.datetime.now()
        signal = _make_signal(
            STRATEGY_D_ID, STRATEGY_D_NAME, direction,
            symbol, futures_symbol, expiry, ts,
            conditions, spot_price, futures_price, atm_option, values=values,
        )
        signal["signal_type"] = signal_type
        signal["action"] = action
        signal["trend_desc"] = trend_desc

    return {
        "passed":      passed,
        "direction":   direction,
        "signal_type": signal_type,
        "action":      action,
        "trend_desc":  trend_desc,
        "signal":      signal,
        "conditions":  conditions,
        "values":      values,
    }


# ---------------------------------------------------------------------------
# Strategy E — Smart Money Accumulation & Distribution (F&O)
# ---------------------------------------------------------------------------

STRATEGY_E_ID   = "E"
STRATEGY_E_NAME = "Smart Money Accumulation & Distribution"


def evaluate_strategy_e(
    candles,
    symbol="NIFTY",
    futures_symbol=None,
    expiry=None,
    spot_price=None,
    futures_price=None,
    prev_futures_price=None,
    current_oi=None,
    prev_oi=None,
    futures_candles=None,
    candles_15m=None,
    candle_ts=None,
    supertrend_period=10,
    supertrend_multiplier=3.0,
    lot_size=1,
):
    """
    Evaluate Strategy E — Smart Money Accumulation & Distribution Setup.
    Combines 50/100/200 SMA, OBV, Volume Expansion, Demand/Supply Zones,
    Futures OI Buildup, VWAP, Order Flow, and Supertrend with a 100-pt scoring model.
    """
    import smart_money_strategy
    return smart_money_strategy.evaluate_smart_money_setup(
        candles=candles,
        symbol=symbol,
        futures_symbol=futures_symbol,
        expiry=expiry,
        spot_price=spot_price,
        futures_price=futures_price,
        prev_futures_price=prev_futures_price,
        current_oi=current_oi,
        prev_oi=prev_oi,
        futures_candles=futures_candles,
        candles_15m=candles_15m,
        candle_ts=candle_ts,
        supertrend_period=supertrend_period,
        supertrend_multiplier=supertrend_multiplier,
        lot_size=lot_size,
    )


# ---------------------------------------------------------------------------
# Strategy F — NIFTY Sustained Breakout/Breakdown + F&O OI
# ---------------------------------------------------------------------------

STRATEGY_F_ID   = "F"
STRATEGY_F_NAME = "NIFTY Sustained Breakout + F&O OI"


def evaluate_strategy_f(
    candles_5m,
    symbol="NIFTY",
    spot_price=None,
    futures_symbol=None,
    futures_price=None,
    prev_futures_price=None,
    futures_volume=None,
    current_oi=None,
    prev_oi=None,
    options_chain=None,
    futures_candles_5m=None,
    candle_ts=None,
    supertrend_period=7,
    supertrend_multiplier=3.0,
):
    """
    Evaluate Strategy F — NIFTY 50 Sustained Breakout/Breakdown + F&O OI Scanner.
    Combines 5m EMA 10/21/50, Supertrend (7,3), Anchored VWAP, Volume confirmation,
    Futures OI Buildup, Options Chain PCR & Writing with a 100-pt scoring model.
    """
    import sustained_breakout_strategy
    return sustained_breakout_strategy.evaluate_sustained_breakout_strategy(
        candles_5m=candles_5m,
        symbol=symbol,
        spot_price=spot_price,
        futures_symbol=futures_symbol,
        futures_price=futures_price,
        prev_futures_price=prev_futures_price,
        futures_volume=futures_volume,
        current_oi=current_oi,
        prev_oi=prev_oi,
        options_chain=options_chain,
        futures_candles_5m=futures_candles_5m,
        candle_ts=candle_ts,
        supertrend_period=supertrend_period,
        supertrend_multiplier=supertrend_multiplier,
    )


# ---------------------------------------------------------------------------
# Deduplication helper
# ---------------------------------------------------------------------------

def make_dedup_key(strategy_id, symbol, candle_ts):
    """Deduplication key — one signal per strategy+symbol+candle."""
    ts_str = candle_ts.strftime('%Y%m%d_%H%M') if hasattr(candle_ts, 'strftime') else str(candle_ts)
    return f"{strategy_id}_{symbol}_{ts_str}"
