"""
speakup/services/analysis/schemas.py
──────────────────────────────────────
Pure-Python dataclasses for the analysis pipeline.

No Qt, database, or UI dependencies — safe to import anywhere.
These are the *internal* transport objects between the analysis modules.
The final DB persistence uses speakup.models.schemas.AnalysisResultSchema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Per-dimension raw metrics
# ---------------------------------------------------------------------------

@dataclass
class FluencyMetrics:
    """Observable speech-rate and continuity metrics."""

    words_per_minute: float             # word_count / (duration / 60)
    pause_count: int                    # gaps between segments >= threshold
    avg_pause_duration_s: float         # mean gap length (seconds)
    total_pause_duration_s: float       # cumulative pause time
    speech_ratio: float                 # speaking time / total duration (0-1)
    disfluent_segment_ratio: float      # fraction of segments with low logprob
    score: float                        # 0-100


@dataclass
class VocabularyMetrics:
    """Lexical richness metrics."""

    total_words: int
    unique_words: int
    type_token_ratio: float         # unique / total  (0-1)
    hapax_legomena_ratio: float     # words appearing exactly once / total
    avg_word_length: float          # mean chars per token
    repeated_words: dict[str, int]  # word → count for words repeated > 2×
    advanced_word_count: int        # words longer than threshold
    score: float                    # 0-100


@dataclass
class FillerMetrics:
    """Filler-word detection results."""

    counts: dict[str, int]          # filler → count
    total_filler_count: int         # sum of all filler occurrences
    filler_rate: float              # filler_count / total_words (0-1)
    score: float                    # 0-100  (higher = fewer fillers)


@dataclass
class GrammarMetrics:
    """Grammar analysis results."""

    backend: str                    # "rule_based" | "llm"
    issues: list[str]               # human-readable issue descriptions
    issue_count: int
    score: float                    # 0-100
    notes: str = ""                 # backend-specific notes or explanation


@dataclass
class ConfidenceMetrics:
    """
    Speaking-confidence estimate based on *observable* speech characteristics.

    ⚠️  This score is an application-generated estimate of speaking confidence
    derived from measurable speech patterns.  It does NOT measure psychological
    confidence, emotional state, or any attribute that cannot be observed
    from speech alone.
    """

    wpm_consistency_score: float        # how close WPM is to a natural target
    low_filler_score: float             # penalises high filler rate
    vocabulary_richness_score: float    # rewards diverse vocabulary
    speech_continuity_score: float      # rewards high speech/pause ratio
    logprob_quality_score: float        # rewards high segment log-probabilities

    score: float                        # 0-100 composite

    # Explanation shown to the user so they understand what this score means
    label:  str = "Speaking Confidence Estimate"
    caveat: str = (
        "This score is based on measurable speaking characteristics "
        "(pace, fluency, vocabulary diversity, and speech continuity). "
        "It does not measure psychological confidence or emotional state."
    )


# ---------------------------------------------------------------------------
# Overall scored result
# ---------------------------------------------------------------------------

@dataclass
class ScoreBreakdown:
    """Transparent per-dimension weight and contribution."""

    dimension:   str
    raw_score:   float   # 0-100
    weight:      float   # 0-1
    contribution: float  # raw_score * weight


@dataclass
class OverallScore:
    """
    Weighted average of all dimensions with a full breakdown.

    Users can see exactly how the score was calculated.
    """

    fluency:     ScoreBreakdown
    vocabulary:  ScoreBreakdown
    grammar:     ScoreBreakdown
    confidence:  ScoreBreakdown
    filler:      ScoreBreakdown

    total: float   # sum of all contributions (0-100)

    @property
    def breakdown_text(self) -> str:
        """Human-readable calculation explanation."""
        lines = ["Score breakdown (weight × sub-score):"]
        for bd in [self.fluency, self.vocabulary, self.grammar,
                   self.confidence, self.filler]:
            lines.append(
                f"  {bd.dimension:12s}  "
                f"{bd.raw_score:5.1f} × {bd.weight:.0%} = {bd.contribution:.1f}"
            )
        lines.append(f"  {'Overall':12s}  {'─'*28} {self.total:.1f} / 100")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Feedback bundle
# ---------------------------------------------------------------------------

@dataclass
class FeedbackBundle:
    """Structured feedback returned to the user after analysis."""

    strengths:   list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        """Flatten to a plain-text multi-paragraph string for DB storage."""
        parts: list[str] = []
        if self.strengths:
            parts.append("Strengths:\n" + "\n".join(f"• {s}" for s in self.strengths))
        if self.improvements:
            parts.append("Areas to improve:\n" + "\n".join(f"• {i}" for i in self.improvements))
        if self.suggestions:
            parts.append("Suggestions:\n" + "\n".join(f"• {s}" for s in self.suggestions))
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Full analysis result
# ---------------------------------------------------------------------------

@dataclass
class SpeechAnalysisResult:
    """
    Complete output of the analysis pipeline for one session.

    All raw metrics, scores, overall score with breakdown, and feedback
    are held here.  The AnalysisService converts this into a DB-storable
    dict that maps to the AnalysisResult ORM columns.
    """

    session_id:  int

    fluency:     FluencyMetrics
    vocabulary:  VocabularyMetrics
    filler:      FillerMetrics
    grammar:     GrammarMetrics
    confidence:  ConfidenceMetrics
    overall:     OverallScore
    feedback:    FeedbackBundle

    # Convenience projections ────────────────────────────────────────────

    def to_db_dict(self) -> dict:
        """
        Return a dict keyed by AnalysisResult column names, ready for
        ``AnalysisResultRepository.create(session_id, **scores)``.
        """
        return {
            "fluency_score":     round(self.fluency.score,    2),
            "vocabulary_score":  round(self.vocabulary.score, 2),
            "grammar_score":     round(self.grammar.score,    2),
            "confidence_score":  round(self.confidence.score, 2),
            "filler_word_count": self.filler.total_filler_count,
            "words_per_minute":  round(self.fluency.words_per_minute, 1),
            "pause_count":       self.fluency.pause_count,
            "overall_score":     round(self.overall.total,    2),
            "feedback":          self.feedback.to_text(),
            # pronunciation_score is deliberately None here (Chunk 6+)
        }

    def to_ui_dict(self) -> dict:
        """
        Extended dict for the UI layer — includes all sub-metrics and breakdown.
        """
        d = self.to_db_dict()
        d.update({
            "type_token_ratio":        round(self.vocabulary.type_token_ratio,      3),
            "unique_words":            self.vocabulary.unique_words,
            "total_words":             self.vocabulary.total_words,
            "filler_rate":             round(self.filler.filler_rate,              3),
            "filler_counts":           dict(self.filler.counts),
            "speech_ratio":            round(self.fluency.speech_ratio,            3),
            "avg_pause_duration_s":    round(self.fluency.avg_pause_duration_s,    2),
            "score_breakdown_text":    self.overall.breakdown_text,
            "confidence_caveat":       self.confidence.caveat,
            "strengths":               list(self.feedback.strengths),
            "improvements":            list(self.feedback.improvements),
            "suggestions":             list(self.feedback.suggestions),
        })
        return d
