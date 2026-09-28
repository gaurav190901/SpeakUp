"""
speakup/services/analysis/fluency.py
──────────────────────────────────────
Fluency analyser — speech rate, pauses, and continuity.

Inputs:
    text      — full transcript string
    duration  — audio duration in seconds
    segments  — list of TranscriptSegment (optional; enables pause detection)

All computation is pure-Python; no external dependencies.
"""

from __future__ import annotations

import re
import logging
from typing import TYPE_CHECKING

from speakup.config.settings import settings
from speakup.services.analysis.schemas import FluencyMetrics

if TYPE_CHECKING:
    from speakup.services.transcription_schemas import TranscriptSegment

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Ideal WPM range for natural English speech  (80–160 is broad normal range)
_WPM_IDEAL_LOW  = 100
_WPM_IDEAL_HIGH = 160

# Penalty curve constants
_WPM_VERY_SLOW  = 60
_WPM_VERY_FAST  = 200


def _score_from_wpm(wpm: float) -> float:
    """
    Map words-per-minute to a 0–100 fluency component score.

    Scoring:
        < 60       → 20–40  (very slow, struggling)
        60–100     → 40–70  (slow but acceptable)
        100–160    → 70–100 (natural target range)
        160–200    → 70–90  (fast but intelligible)
        > 200      → 40–70  (very fast, likely rushed)
    """
    if wpm <= 0:
        return 0.0
    if _WPM_IDEAL_LOW <= wpm <= _WPM_IDEAL_HIGH:
        # Linear interpolation to 100 at midpoint (130 WPM)
        midpoint = (_WPM_IDEAL_LOW + _WPM_IDEAL_HIGH) / 2
        dist = abs(wpm - midpoint) / ((_WPM_IDEAL_HIGH - _WPM_IDEAL_LOW) / 2)
        return max(70.0, 100.0 - dist * 30.0)
    if wpm < _WPM_IDEAL_LOW:
        # Below ideal
        t = max(0.0, (wpm - _WPM_VERY_SLOW) / (_WPM_IDEAL_LOW - _WPM_VERY_SLOW))
        return 20.0 + t * 50.0
    # Above ideal
    t = max(0.0, 1.0 - (wpm - _WPM_IDEAL_HIGH) / (_WPM_VERY_FAST - _WPM_IDEAL_HIGH))
    return 40.0 + t * 50.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyse_fluency(
    text: str,
    duration_s: float,
    segments: list[TranscriptSegment] | None = None,
) -> FluencyMetrics:
    """
    Compute fluency metrics from a transcript.

    Args:
        text:      Full transcript text.
        duration_s: Audio duration in seconds (from TranscriptResult.duration).
        segments:  Optional list of TranscriptSegment for pause analysis.

    Returns:
        :class:`FluencyMetrics` with all fields populated.
    """
    # ── Word count & WPM ────────────────────────────────────────────────
    words = text.split() if text.strip() else []
    word_count = len(words)
    wpm = (word_count / (duration_s / 60.0)) if duration_s > 0 else 0.0

    # ── Pause analysis ──────────────────────────────────────────────────
    pause_count, avg_pause_s, total_pause_s = _analyse_pauses(segments, duration_s)

    # ── Speech ratio ────────────────────────────────────────────────────
    speech_ratio = max(0.0, 1.0 - (total_pause_s / duration_s)) if duration_s > 0 else 1.0

    # ── Disfluent-segment ratio ─────────────────────────────────────────
    disfluent_ratio = _disfluent_ratio(segments)

    # ── Sub-scores & composite ──────────────────────────────────────────
    wpm_score           = _score_from_wpm(wpm)
    speech_ratio_score  = _map(speech_ratio,  0.4, 0.95, 20.0, 100.0)
    pause_penalty       = _pause_penalty(pause_count, word_count)
    disfluent_penalty   = _map(disfluent_ratio, 0.0, 0.5, 0.0, 30.0)

    raw = (
        wpm_score          * 0.45
        + speech_ratio_score * 0.30
        + pause_penalty      * 0.25
        - disfluent_penalty  * 0.10
    )
    score = max(0.0, min(100.0, raw))

    logger.debug(
        "Fluency: wpm=%.1f  pauses=%d  speech_ratio=%.2f  score=%.1f",
        wpm, pause_count, speech_ratio, score,
    )

    return FluencyMetrics(
        words_per_minute=round(wpm, 1),
        pause_count=pause_count,
        avg_pause_duration_s=round(avg_pause_s, 2),
        total_pause_duration_s=round(total_pause_s, 2),
        speech_ratio=round(speech_ratio, 3),
        disfluent_segment_ratio=round(disfluent_ratio, 3),
        score=round(score, 2),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _analyse_pauses(
    segments: list[TranscriptSegment] | None,
    duration_s: float,
) -> tuple[int, float, float]:
    """
    Return (pause_count, avg_pause_s, total_pause_s).

    A pause is a gap between consecutive speech segments >= ANALYSIS_PAUSE_THRESHOLD_S.
    """
    threshold = settings.ANALYSIS_PAUSE_THRESHOLD_S

    if not segments or len(segments) < 2:
        return 0, 0.0, 0.0

    speech_segs = [s for s in segments if s.is_likely_speech]
    if len(speech_segs) < 2:
        return 0, 0.0, 0.0

    speech_segs = sorted(speech_segs, key=lambda s: s.start)
    pauses: list[float] = []
    for i in range(1, len(speech_segs)):
        gap = speech_segs[i].start - speech_segs[i - 1].end
        if gap >= threshold:
            pauses.append(gap)

    if not pauses:
        return 0, 0.0, 0.0

    return (
        len(pauses),
        sum(pauses) / len(pauses),
        sum(pauses),
    )


def _pause_penalty(pause_count: int, word_count: int) -> float:
    """
    Map pause frequency to a 0–100 score (higher = fewer pauses relative to words).
    Pauses per 100 words:
        0–2     → 95–100
        2–5     → 70–95
        5–10    → 40–70
        > 10    → 10–40
    """
    if word_count == 0:
        return 50.0
    rate = (pause_count / word_count) * 100
    if rate <= 2:
        return _map(rate, 0, 2, 100.0, 95.0)
    if rate <= 5:
        return _map(rate, 2, 5, 95.0, 70.0)
    if rate <= 10:
        return _map(rate, 5, 10, 70.0, 40.0)
    return max(10.0, _map(rate, 10, 20, 40.0, 10.0))


def _disfluent_ratio(segments: list[TranscriptSegment] | None) -> float:
    """Fraction of speech segments with avg_logprob below threshold."""
    if not segments:
        return 0.0
    threshold = settings.ANALYSIS_LOGPROB_FLUENCY_THRESHOLD
    speech = [s for s in segments if s.is_likely_speech]
    if not speech:
        return 0.0
    below = sum(1 for s in speech if s.avg_logprob < threshold)
    return below / len(speech)


def _map(value: float, in_min: float, in_max: float,
         out_min: float, out_max: float) -> float:
    """Linear map value from [in_min, in_max] → [out_min, out_max], clamped."""
    if in_max == in_min:
        return out_min
    t = (value - in_min) / (in_max - in_min)
    t = max(0.0, min(1.0, t))
    return out_min + t * (out_max - out_min)
