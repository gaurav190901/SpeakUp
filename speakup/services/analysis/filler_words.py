"""
speakup/services/analysis/filler_words.py
──────────────────────────────────────────
Filler-word detector.

Detects both single-word fillers ("um") and multi-word phrases
("you know", "i mean") using a single-pass scan.

The filler list comes from settings.get_filler_words() and can be
configured via the ANALYSIS_FILLER_WORDS environment variable.

Pure-Python stdlib only.
"""

from __future__ import annotations

import re
import logging
from collections import Counter

from speakup.config.settings import settings
from speakup.services.analysis.schemas import FillerMetrics

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_from_filler_rate(rate: float) -> float:
    """
    Filler rate (fillers / total_words, 0–1) → score 0–100.

    Lower rate is better.

    Benchmarks:
        0–0.01   → 90–100  (excellent — ≤1 filler per 100 words)
        0.01–0.03 → 70–90  (good)
        0.03–0.06 → 50–70  (noticeable)
        0.06–0.10 → 30–50  (distracting)
        > 0.10    → 0–30   (very frequent)
    """
    if rate <= 0:
        return 100.0
    if rate <= 0.01:
        return 90.0 + (0.01 - rate) / 0.01 * 10.0
    if rate <= 0.03:
        return 70.0 + (0.03 - rate) / 0.02 * 20.0
    if rate <= 0.06:
        return 50.0 + (0.06 - rate) / 0.03 * 20.0
    if rate <= 0.10:
        return 30.0 + (0.10 - rate) / 0.04 * 20.0
    return max(0.0, 30.0 - (rate - 0.10) * 200)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyse_filler_words(
    text: str,
    filler_words: list[str] | None = None,
) -> FillerMetrics:
    """
    Count filler words/phrases in *text*.

    Args:
        text:         Full transcript text.
        filler_words: Override the filler list from settings.

    Returns:
        :class:`FillerMetrics` with counts per filler and overall rate.
    """
    fillers = filler_words if filler_words is not None else settings.get_filler_words()

    # Sort longest phrases first so multi-word matches are tried before
    # their constituent single words
    fillers_sorted = sorted(fillers, key=len, reverse=True)

    text_lower = text.lower()
    total_words = len(text.split()) if text.strip() else 0

    counts: Counter[str] = Counter()

    for filler in fillers_sorted:
        # Match whole-word / whole-phrase boundaries
        pattern = rf"\b{re.escape(filler)}\b"
        matches = re.findall(pattern, text_lower)
        if matches:
            counts[filler] += len(matches)

    total_fillers = sum(counts.values())
    rate = total_fillers / total_words if total_words > 0 else 0.0
    score = _score_from_filler_rate(rate)

    logger.debug(
        "Fillers: total=%d  rate=%.3f  score=%.1f  top=%s",
        total_fillers, rate, score,
        dict(counts.most_common(3)),
    )

    return FillerMetrics(
        counts=dict(counts),
        total_filler_count=total_fillers,
        filler_rate=round(rate, 4),
        score=round(score, 2),
    )
