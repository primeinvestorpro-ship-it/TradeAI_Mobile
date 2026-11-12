"""
Trade Score calculation — pure, deterministic, no I/O.

Converts 5 raw agent scores (0–100 each) into a single Composite Trade Score,
grade, and signal using the weighted formula from the trade-analyze prompt.
"""

WEIGHTS = {
    "technical":   0.25,
    "fundamental": 0.25,
    "sentiment":   0.20,
    "risk":        0.15,
    "thesis":      0.15,
}

# (min_score_inclusive, grade, signal)
GRADE_TABLE = [
    (85, "A+", "Strong Buy"),
    (70, "A",  "Buy"),
    (55, "B",  "Hold/Accumulate"),
    (40, "C",  "Neutral"),
    (25, "D",  "Caution"),
    (0,  "F",  "Avoid"),
]


def calculate_composite_score(
    technical: float,
    fundamental: float,
    sentiment: float,
    risk: float,
    thesis: float,
) -> dict:
    """
    Returns:
    {
        "score":     float,   # 0–100 rounded to 1 decimal
        "grade":     str,     # A+/A/B/C/D/F
        "signal":    str,     # e.g. "Strong Buy"
        "breakdown": {
            "technical":   {"score": float, "weight": float, "weighted": float},
            ...
        }
    }
    """
    raw = {
        "technical":   technical,
        "fundamental": fundamental,
        "sentiment":   sentiment,
        "risk":        risk,
        "thesis":      thesis,
    }

    breakdown = {}
    composite = 0.0
    for dim, weight in WEIGHTS.items():
        score = raw[dim]
        weighted = score * weight
        composite += weighted
        breakdown[dim] = {
            "score":    round(score, 1),
            "weight":   weight,
            "weighted": round(weighted, 2),
        }

    composite = round(composite, 1)
    grade, signal = _grade(composite)

    return {
        "score":     composite,
        "grade":     grade,
        "signal":    signal,
        "breakdown": breakdown,
    }


def _grade(score: float):
    for min_score, grade, signal in GRADE_TABLE:
        if score >= min_score:
            return grade, signal
    return "F", "Avoid"


def score_from_agent_text(agent_text: str, dimension: str) -> float:
    """
    Extract a numeric score (0–100) from the agent's free-text response.
    Looks for patterns like "Technical Score: 72/100" or "Thesis Score: 65/100".
    Falls back to 50 (neutral) if not parseable.
    """
    import re
    # Patterns: "Technical Score: 72/100" or "[72/100]" or "Score: 72"
    patterns = [
        rf"{dimension}\s+Score[:\s]+\[?(\d+(?:\.\d+)?)\]?\s*/\s*100",
        r"Score[:\s]+\[?(\d+(?:\.\d+)?)\]?\s*/\s*100",
        r"\[(\d+(?:\.\d+)?)\s*/\s*100\]",
        r"Score:\s*\[?(\d+(?:\.\d+)?)\]?",
    ]
    for pattern in patterns:
        m = re.search(pattern, agent_text, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1))
                return max(0.0, min(100.0, val))
            except ValueError:
                continue
    return 50.0  # neutral fallback
