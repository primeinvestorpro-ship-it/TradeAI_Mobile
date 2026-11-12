"""
Final direction decision for NSE Stock Futures.

Pure, standalone, deterministic. No I/O, no Kite, no AI calls here — only
the truth table below. Kept in its own file so it can be tuned/tested
independently of everything else.
"""

VALID_DECISIONS = ("LONG", "SHORT", "NO_TRADE")


def decide_direction(
    trade_score: float,
    ai_sentiment: str,
    range_too_tight: bool = False,
    buy_threshold: float = 70.0,
    sell_threshold: float = 30.0,
) -> str:
    """
    trade_score:     Composite Trade Score (0–100) from trade_score.py.
    ai_sentiment:    "POSITIVE" | "NEGATIVE" | "NEUTRAL" — the blended
                     directional read from the AI orchestrator's discovery
                     phase. Used as a conflict-guard: a strong AI view
                     opposing the score-implied direction blocks the trade.
    range_too_tight: True when ATR-implied expected move is below the
                     configured minimum — not worth paying for a futures
                     move on a stagnant day.
    buy_threshold:   Minimum Trade Score to enter LONG (default 70).
    sell_threshold:  Maximum Trade Score to consider SHORT (default 30).
                     SHORT requires an even lower score because individual-
                     stock shorting carries higher risk.

    Returns one of: "LONG", "SHORT", "NO_TRADE"

    Decision rules (evaluated in order):
    1. range_too_tight → NO_TRADE  (always, overrides everything)
    2. score >= buy_threshold AND sentiment != "NEGATIVE" → LONG
    3. score <= sell_threshold AND sentiment != "POSITIVE" → SHORT
    4. otherwise → NO_TRADE
    """
    if ai_sentiment not in ("POSITIVE", "NEGATIVE", "NEUTRAL"):
        raise ValueError(f"invalid ai_sentiment: {ai_sentiment!r}")
    if not (0.0 <= trade_score <= 100.0):
        raise ValueError(f"trade_score must be 0–100, got {trade_score!r}")
    if not (0.0 <= sell_threshold < buy_threshold <= 100.0):
        raise ValueError(
            f"invalid thresholds: sell_threshold={sell_threshold!r} must be < "
            f"buy_threshold={buy_threshold!r}, both in [0, 100]"
        )

    if range_too_tight:
        return "NO_TRADE"

    if trade_score >= buy_threshold:
        # Strong opposing AI sentiment → step aside
        if ai_sentiment == "NEGATIVE":
            return "NO_TRADE"
        return "LONG"

    if trade_score <= sell_threshold:
        if ai_sentiment == "POSITIVE":
            return "NO_TRADE"
        return "SHORT"

    return "NO_TRADE"
