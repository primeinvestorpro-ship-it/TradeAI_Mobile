"""
Pure-Python option signal analytics for NSE Stock and Index Options.
Zero network calls, zero external library dependencies.

Every function takes plain Python types in and returns plain Python types out,
allowing full offline verification via pytest.
"""

import math
from statistics import mean


def get_strike_interval(sorted_strikes: list[float]) -> float:
    """
    Derives the exchange's strike-spacing from a sorted list of unique
    available strikes for the nearest expiry (e.g., 20 for RELIANCE, 10 for INFY, 50 for NIFTY).
    """
    if len(sorted_strikes) < 2:
        raise ValueError("need at least 2 strikes to derive strike interval")
    diffs = [b - a for a, b in zip(sorted_strikes, sorted_strikes[1:]) if b > a]
    if not diffs:
        raise ValueError("could not derive a positive strike interval")
    return min(diffs)


def get_atm_strike(spot_price: float, strike_interval: float) -> float:
    """Rounds spot price to the nearest available strike price."""
    if strike_interval <= 0:
        raise ValueError("strike_interval must be positive")
    return round(spot_price / strike_interval) * strike_interval


def get_strikes_around_atm(atm_strike: float, strike_interval: float, n: int = 5) -> list[float]:
    """
    Returns ATM +/- n strikes (2n+1 strikes total, default 11: ATM +/- 5 strikes).
    Sorted ascending from lowest strike to highest strike.
    """
    return [atm_strike + i * strike_interval for i in range(-n, n + 1)]


def compute_oi_bias(
    strike_oi_data: dict,
    pcr_threshold: float,
    spot_price: float = None,
    strike_interval: float = None,
    window_strikes: int = 5,
    strike_oi_change_ratios: dict = None,
    oi_change_clamp: float = 0.5,
) -> dict:
    """
    strike_oi_data: {strike: {"CE": call_oi, "PE": put_oi}, ...}
    pcr_threshold:  e.g. 0.15
    """
    total_call_oi = sum((v.get("CE", 0) or 0) for v in strike_oi_data.values())
    total_put_oi = sum((v.get("PE", 0) or 0) for v in strike_oi_data.values())

    if total_call_oi <= 0:
        pcr = float("inf") if total_put_oi > 0 else 0.0
    else:
        pcr = total_put_oi / total_call_oi

    def _proximity(strike):
        if spot_price is None or not strike_interval:
            return 1.0
        max_distance = window_strikes * strike_interval
        if max_distance <= 0:
            return 1.0
        return max(0.0, 1.0 - abs(strike - spot_price) / max_distance)

    def _change_multiplier(strike, opt_type):
        if not strike_oi_change_ratios:
            return 1.0
        ratio = strike_oi_change_ratios.get(strike, {}).get(opt_type)
        if ratio is None:
            return 1.0
        return min(1.0 + oi_change_clamp, max(1.0 - oi_change_clamp, ratio))

    weighted_call_oi = sum(
        (v.get("CE", 0) or 0) * _proximity(strike) * _change_multiplier(strike, "CE")
        for strike, v in strike_oi_data.items()
    )
    weighted_put_oi = sum(
        (v.get("PE", 0) or 0) * _proximity(strike) * _change_multiplier(strike, "PE")
        for strike, v in strike_oi_data.items()
    )

    if weighted_call_oi <= 0:
        weighted_pcr = float("inf") if weighted_put_oi > 0 else 0.0
    else:
        weighted_pcr = weighted_put_oi / weighted_call_oi

    if weighted_pcr > 1.0 + pcr_threshold:
        oi_bias = "BULLISH"
    elif weighted_pcr < 1.0 - pcr_threshold:
        oi_bias = "BEARISH"
    else:
        oi_bias = "NEUTRAL"

    return {
        "pcr": pcr,
        "weighted_pcr": weighted_pcr,
        "oi_bias": oi_bias,
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
    }


def compute_oi_walls(strike_oi_data: dict, atm_strike: float) -> dict:
    """
    Identifies the institutional resistance wall and support wall from OTM open interest:
      - Resistance: strike ABOVE ATM with the highest Call OI (call writing ceiling).
      - Support: strike BELOW ATM with the highest Put OI (put writing floor).
    """
    strikes_above = {s: v for s, v in strike_oi_data.items() if s > atm_strike}
    strikes_below = {s: v for s, v in strike_oi_data.items() if s < atm_strike}

    resistance_strike, resistance_oi = None, 0
    for strike, oi in strikes_above.items():
        call_oi = oi.get("CE", 0) or 0
        if call_oi > resistance_oi:
            resistance_strike, resistance_oi = strike, call_oi

    support_strike, support_oi = None, 0
    for strike, oi in strikes_below.items():
        put_oi = oi.get("PE", 0) or 0
        if put_oi > support_oi:
            support_strike, support_oi = strike, put_oi

    return {
        "resistance_strike": resistance_strike,
        "resistance_oi": resistance_oi,
        "support_strike": support_strike,
        "support_oi": support_oi,
    }


def compute_direction_probability(
    support_oi: float,
    resistance_oi: float,
    support_strike: float = None,
    resistance_strike: float = None,
    spot_price: float = None,
    strike_interval: float = None,
    window_strikes: int = 5,
    support_oi_change_ratio: float = None,
    resistance_oi_change_ratio: float = None,
    oi_change_clamp: float = 0.5,
) -> dict:
    """
    Derives directional probability split based on institutional OI wall balance,
    weighted by proximity to current spot price and fresh daily OI change.
    """
    def _weighted(strike, oi, oi_change_ratio):
        oi = oi or 0
        if strike is None or spot_price is None or not strike_interval:
            weighted = oi
        else:
            max_distance = window_strikes * strike_interval
            if max_distance <= 0:
                weighted = oi
            else:
                distance = abs(strike - spot_price)
                proximity = max(0.0, 1.0 - distance / max_distance)
                weighted = oi * proximity
        if oi_change_ratio is not None:
            multiplier = min(1.0 + oi_change_clamp, max(1.0 - oi_change_clamp, oi_change_ratio))
            weighted *= multiplier
        return weighted

    weighted_support = _weighted(support_strike, support_oi, support_oi_change_ratio)
    weighted_resistance = _weighted(resistance_strike, resistance_oi, resistance_oi_change_ratio)

    total = weighted_support + weighted_resistance
    if total <= 0:
        return {"upside_probability": 0.5, "downside_probability": 0.5}
    upside = weighted_support / total
    return {"upside_probability": upside, "downside_probability": 1.0 - upside}


def compute_atm_oi_change_bias(
    atm_ce_oi_change_ratio: float,
    atm_pe_oi_change_ratio: float,
    threshold: float = 0.1,
) -> str:
    """
    Fresh OI build-up comparison specifically at the ATM strike today vs yesterday.
    Faster put accumulation -> BULLISH; faster call accumulation -> BEARISH.
    """
    if atm_ce_oi_change_ratio is None or atm_pe_oi_change_ratio is None:
        return "NEUTRAL"
    diff = atm_pe_oi_change_ratio - atm_ce_oi_change_ratio
    if diff > threshold:
        return "BULLISH"
    if diff < -threshold:
        return "BEARISH"
    return "NEUTRAL"


def compute_premiums(
    ce_ltp: float,
    pe_ltp: float,
    profit_margin_factor: float = 1.15,
) -> dict:
    """
    Calculates ATM Call, Put, and Straddle (Net) premiums and the required
    underlying moves to clear breakeven including profit margin.
    """
    req_call = ce_ltp * profit_margin_factor if ce_ltp is not None else None
    req_put = pe_ltp * profit_margin_factor if pe_ltp is not None else None
    net_premium = None
    req_both = None
    if ce_ltp is not None and pe_ltp is not None:
        net_premium = ce_ltp + pe_ltp
        req_both = net_premium * profit_margin_factor

    return {
        "ce_premium": ce_ltp,
        "pe_premium": pe_ltp,
        "net_premium": net_premium,
        "required_move_call": req_call,
        "required_move_put": req_put,
        "required_move_both": req_both,
    }


def compute_vix_expected_move(
    spot_price: float,
    india_vix: float,
    trading_days_per_year: int = 252,
) -> float:
    """
    Standard option implied 1-day expected move:
      expected_move = spot_price * (india_vix / 100) * sqrt(1 / 252)
    """
    if spot_price <= 0 or india_vix < 0:
        raise ValueError("spot_price must be positive and india_vix must be non-negative")
    return spot_price * (india_vix / 100.0) * math.sqrt(1.0 / trading_days_per_year)
