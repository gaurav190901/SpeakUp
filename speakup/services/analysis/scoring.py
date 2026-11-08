"""
speakup/services/analysis/scoring.py
──────────────────────────────────────
Overall weighted score calculator.

Takes per-dimension scores and combines them into a transparent final score.
Every weight and contribution is surfaced so the user can see exactly how
the number was computed.
"""

from __future__ import annotations

import logging

from speakup.config.settings import settings
from speakup.services.analysis.schemas import (
    ConfidenceMetrics,
    FluencyMetrics,
    FillerMetrics,
    GrammarMetrics,
    OverallScore,
    ScoreBreakdown,
    VocabularyMetrics,
)

logger = logging.getLogger(__name__)


def compute_overall_score(
    fluency:    FluencyMetrics,
    vocabulary: VocabularyMetrics,
    filler:     FillerMetrics,
    grammar:    GrammarMetrics,
    confidence: ConfidenceMetrics,
    weights: dict[str, float] | None = None,
) -> OverallScore:
    """
    Combine per-dimension scores into a transparent overall score.

    Args:
        fluency, vocabulary, filler, grammar, confidence: Metric objects.
        weights: Optional dict with keys ``fluency``, ``vocabulary``,
                 ``grammar``, ``confidence``, ``filler`` mapping to floats
                 in [0, 1] that sum to 1.  Defaults to ``settings.ANALYSIS_WEIGHT_*``.

    Returns:
        :class:`OverallScore` with per-dimension breakdown and total.

    Note on pronunciation_score:
        The ``pronunciation_score`` DB column is intentionally left None in
        Pronunciation analysis requires phoneme-level data not
        available from Whisper alone, so it is not scored yet.
    """
    if weights is None:
        weights = {
            "fluency":    settings.ANALYSIS_WEIGHT_FLUENCY,
            "vocabulary": settings.ANALYSIS_WEIGHT_VOCABULARY,
            "grammar":    settings.ANALYSIS_WEIGHT_GRAMMAR,
            "confidence": settings.ANALYSIS_WEIGHT_CONFIDENCE,
            "filler":     settings.ANALYSIS_WEIGHT_FILLER,
        }

    def _bd(dimension: str, raw: float, w: float) -> ScoreBreakdown:
        return ScoreBreakdown(
            dimension=dimension,
            raw_score=round(raw, 2),
            weight=w,
            contribution=round(raw * w, 2),
        )

    fl = _bd("Fluency",     fluency.score,     weights["fluency"])
    vo = _bd("Vocabulary",  vocabulary.score,  weights["vocabulary"])
    gr = _bd("Grammar",     grammar.score,     weights["grammar"])
    co = _bd("Confidence",  confidence.score,  weights["confidence"])
    fi = _bd("Filler Words", filler.score,     weights["filler"])

    total = fl.contribution + vo.contribution + gr.contribution + co.contribution + fi.contribution
    total = max(0.0, min(100.0, round(total, 2)))

    logger.debug(
        "Overall score: fl=%.1f vo=%.1f gr=%.1f co=%.1f fi=%.1f → %.1f",
        fl.raw_score, vo.raw_score, gr.raw_score,
        co.raw_score, fi.raw_score, total,
    )

    return OverallScore(
        fluency=fl,
        vocabulary=vo,
        grammar=gr,
        confidence=co,
        filler=fi,
        total=total,
    )
