"""
speakup/services/analysis/confidence.py
─────────────────────────────────────────
Speaking-confidence estimator.

⚠️  IMPORTANT DISCLAIMER ⚠️
──────────────────────────
This module produces a "Speaking Confidence Estimate" — a composite score
derived exclusively from *observable, measurable* characteristics of
recorded speech:

    • Speech pacing (words per minute)
    • Filler-word frequency
    • Vocabulary richness (type-token ratio)
    • Speech continuity (speech/pause ratio)
    • Segment transcription quality (Whisper avg_logprob)

This score does NOT measure psychological confidence, emotional state,
personality traits, or any attribute that cannot be directly observed
from the speech signal.  It is an application-generated heuristic metric
intended to give practitioners a single actionable number to track
speaking improvement over time.

Pure-Python stdlib only.
"""

from __future__ import annotations

import logging

from speakup.config.settings import settings
from speakup.services.analysis.schemas import (
    ConfidenceMetrics,
    FluencyMetrics,
    VocabularyMetrics,
    FillerMetrics,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Individual component scorers
# ---------------------------------------------------------------------------

def _wpm_consistency(wpm: float) -> float:
    """
    Reward WPM close to the natural target (130 WPM default).

    Distance from target → penalty.
    """
    target = float(settings.ANALYSIS_TARGET_WPM)
    if wpm <= 0:
        return 20.0
    distance = abs(wpm - target)
    # Within 20 WPM of target = full score
    if distance <= 20:
        return 100.0
    # 20–60 WPM away = linear decline
    if distance <= 60:
        return max(50.0, 100.0 - (distance - 20) * 1.25)
    return max(20.0, 50.0 - (distance - 60) * 0.5)


def _low_filler(filler_rate: float) -> float:
    """
    Lower filler rate → higher confidence component.

    Frequent fillers signal hesitation and reduced verbal fluency.
    """
    if filler_rate <= 0.01:
        return 100.0
    if filler_rate <= 0.04:
        return 100.0 - (filler_rate - 0.01) / 0.03 * 40.0
    if filler_rate <= 0.08:
        return 60.0 - (filler_rate - 0.04) / 0.04 * 30.0
    return max(10.0, 30.0 - filler_rate * 100)


def _vocabulary_richness(ttr: float) -> float:
    """
    Higher type-token ratio → richer vocabulary → higher confidence component.

    Speaking with varied vocabulary signals preparation and comfort with the topic.
    """
    # Map [0, 1] TTR to [0, 100] with a soft curve
    if ttr <= 0:
        return 10.0
    if ttr >= 0.70:
        return 100.0
    return min(100.0, ttr / 0.70 * 100.0)


def _speech_continuity(speech_ratio: float) -> float:
    """
    Higher speech/pause ratio → more continuous delivery → higher confidence component.

    A speaker who pauses rarely and recovers quickly tends to sound more assured.
    """
    if speech_ratio >= 0.90:
        return 100.0
    if speech_ratio <= 0.40:
        return 15.0
    return max(15.0, (speech_ratio - 0.40) / 0.50 * 85.0 + 15.0)


def _logprob_quality(
    segments_logprob: float,
) -> float:
    """
    Average Whisper segment logprob → transcription quality proxy.

    Higher logprob (closer to 0) means the model is more certain about the words,
    which loosely correlates with clearer, more confident articulation.

    Note: this is a transcription-quality signal, not a direct acoustic measure.
    """
    # Logprob range for Whisper: typically -3.0 (very poor) to 0.0 (perfect)
    # Map [-2.5, -0.1] → [20, 100]
    clamped = max(-2.5, min(-0.05, segments_logprob))
    return 20.0 + (clamped - (-2.5)) / ((-0.05) - (-2.5)) * 80.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyse_confidence(
    fluency:    FluencyMetrics,
    vocabulary: VocabularyMetrics,
    filler:     FillerMetrics,
    avg_logprob: float = -0.3,
) -> ConfidenceMetrics:
    """
    Compute the speaking-confidence estimate.

    Args:
        fluency:      FluencyMetrics from the fluency analyser.
        vocabulary:   VocabularyMetrics from the vocabulary analyser.
        filler:       FillerMetrics from the filler-word analyser.
        avg_logprob:  Mean Whisper segment logprob (default -0.3 if unavailable).

    Returns:
        :class:`ConfidenceMetrics` — see module docstring for caveats.
    """
    wpm_score        = _wpm_consistency(fluency.words_per_minute)
    filler_score     = _low_filler(filler.filler_rate)
    vocab_score      = _vocabulary_richness(vocabulary.type_token_ratio)
    continuity_score = _speech_continuity(fluency.speech_ratio)
    logprob_score    = _logprob_quality(avg_logprob)

    # Weighted composite
    composite = (
        wpm_score        * 0.25
        + filler_score   * 0.25
        + vocab_score    * 0.20
        + continuity_score * 0.20
        + logprob_score  * 0.10
    )
    score = max(0.0, min(100.0, composite))

    logger.debug(
        "Confidence: wpm=%.0f  filler=%.0f  vocab=%.0f  cont=%.0f  logp=%.0f  composite=%.1f",
        wpm_score, filler_score, vocab_score, continuity_score, logprob_score, score,
    )

    return ConfidenceMetrics(
        wpm_consistency_score=round(wpm_score,        2),
        low_filler_score=round(filler_score,          2),
        vocabulary_richness_score=round(vocab_score,  2),
        speech_continuity_score=round(continuity_score, 2),
        logprob_quality_score=round(logprob_score,    2),
        score=round(score, 2),
    )
