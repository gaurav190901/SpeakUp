"""
speakup/services/analysis/vocabulary.py
─────────────────────────────────────────
Vocabulary analyser — lexical richness, diversity, and complexity.

Pure-Python stdlib only.
"""

from __future__ import annotations

import re
import logging
from collections import Counter

from speakup.config.settings import settings
from speakup.services.analysis.schemas import VocabularyMetrics

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Minimum word length to consider "content" words (skip short function words)
_MIN_CONTENT_WORD_LEN = 4

# "Advanced" words: longer words are loosely correlated with vocabulary range
_ADVANCED_WORD_MIN_LEN = 8

# English function words / stop-words to exclude from TTR
_STOP_WORDS: frozenset[str] = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "of", "in", "on",
    "at", "to", "for", "with", "by", "from", "up", "about", "into",
    "through", "after", "over", "under", "between", "out", "off", "so",
    "but", "and", "or", "nor", "not", "no", "nor", "just", "also",
    "that", "this", "these", "those", "it", "its", "it's", "they",
    "them", "their", "there", "here", "when", "where", "who", "which",
    "what", "how", "if", "as", "then", "than", "because", "i", "you",
    "he", "she", "we", "my", "your", "his", "her", "our", "all", "any",
    "some", "each", "every", "both", "few", "more", "most", "other",
    "such", "very", "too", "quite", "rather", "much", "many",
})


# ---------------------------------------------------------------------------
# Tokeniser
# ---------------------------------------------------------------------------

def _tokenise(text: str) -> list[str]:
    """
    Return a list of lowercased alphabetic tokens.

    Strips punctuation, numbers, and whitespace noise.
    """
    return re.findall(r"[a-z]+", text.lower())


def _content_tokens(tokens: list[str]) -> list[str]:
    """Filter out stop words and very short tokens."""
    return [
        t for t in tokens
        if t not in _STOP_WORDS and len(t) >= _MIN_CONTENT_WORD_LEN
    ]


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _score_ttr(ttr: float) -> float:
    """
    Type-Token Ratio → 0–100 score.

    TTR = unique / total.  Longer texts naturally have lower TTR, so we
    apply a soft curve rather than a hard threshold.

    Benchmarks (approximate, spontaneous speech):
        0.20–0.35 → low (short, repetitive)
        0.35–0.55 → moderate (typical conversational)
        0.55–0.75 → high (varied vocabulary)
        > 0.75    → very high (exceptional range)
    """
    if ttr <= 0:
        return 10.0
    if ttr >= 0.75:
        return 100.0
    # Piecewise linear
    breakpoints = [(0.0, 10), (0.20, 25), (0.35, 45), (0.55, 75), (0.75, 100)]
    for i in range(1, len(breakpoints)):
        x0, y0 = breakpoints[i - 1]
        x1, y1 = breakpoints[i]
        if ttr <= x1:
            t = (ttr - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return 100.0


def _score_advanced_ratio(ratio: float) -> float:
    """Advanced-word ratio (0–1) → 0–100 bonus modifier."""
    return min(100.0, ratio * 300)   # 33% advanced = 100


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyse_vocabulary(text: str) -> VocabularyMetrics:
    """
    Compute lexical-richness metrics from a transcript string.

    Args:
        text: Full transcript text.

    Returns:
        :class:`VocabularyMetrics` with all fields populated.
    """
    all_tokens = _tokenise(text)
    total_words = len(all_tokens)

    if total_words == 0:
        return VocabularyMetrics(
            total_words=0,
            unique_words=0,
            type_token_ratio=0.0,
            hapax_legomena_ratio=0.0,
            avg_word_length=0.0,
            repeated_words={},
            advanced_word_count=0,
            score=20.0,  # minimal baseline for empty transcript
        )

    counts = Counter(all_tokens)
    unique_words = len(counts)

    # Type-Token Ratio — on content words for fairer comparison
    content = _content_tokens(all_tokens)
    content_counts = Counter(content)
    ttr = len(content_counts) / len(content) if content else 0.0

    # Hapax legomena — words appearing exactly once (vocabulary breadth signal)
    hapax = sum(1 for c in counts.values() if c == 1)
    hapax_ratio = hapax / unique_words if unique_words > 0 else 0.0

    # Average word length (all tokens)
    avg_len = sum(len(t) for t in all_tokens) / total_words

    # Repeated content words (appearing > 2×, non-stop)
    repeated = {
        w: c for w, c in content_counts.items()
        if c > 2
    }

    # Advanced-vocabulary count
    advanced = sum(1 for t in set(all_tokens) if len(t) >= _ADVANCED_WORD_MIN_LEN)
    advanced_ratio = advanced / unique_words if unique_words > 0 else 0.0

    # ── Composite score ─────────────────────────────────────────────────
    ttr_score      = _score_ttr(ttr)
    hapax_score    = min(100.0, hapax_ratio * 150)   # 0.67 hapax ratio = 100
    advanced_bonus = _score_advanced_ratio(advanced_ratio)

    raw = (
        ttr_score      * 0.55
        + hapax_score  * 0.30
        + advanced_bonus * 0.15
    )
    score = max(0.0, min(100.0, raw))

    logger.debug(
        "Vocabulary: tokens=%d  unique=%d  ttr=%.3f  advanced=%d  score=%.1f",
        total_words, unique_words, ttr, advanced, score,
    )

    return VocabularyMetrics(
        total_words=total_words,
        unique_words=unique_words,
        type_token_ratio=round(ttr, 3),
        hapax_legomena_ratio=round(hapax_ratio, 3),
        avg_word_length=round(avg_len, 2),
        repeated_words=dict(sorted(repeated.items(), key=lambda x: -x[1])[:10]),
        advanced_word_count=advanced,
        score=round(score, 2),
    )
