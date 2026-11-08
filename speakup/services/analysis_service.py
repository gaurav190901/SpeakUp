"""
AnalysisService — orchestrates all speech analysis modules.

Pipeline
────────
1. FluencyAnalyser       → FluencyMetrics
2. VocabularyAnalyser    → VocabularyMetrics
3. FillerWordAnalyser    → FillerMetrics
4. GrammarAnalyser       → GrammarMetrics
5. ConfidenceAnalyser    → ConfidenceMetrics
6. OverallScorer         → OverallScore
7. FeedbackGenerator     → FeedbackBundle
8. Persist               → AnalysisResult (DB)
9. Return                → SpeechAnalysisResult / dict

The service is designed to work with:
    - A plain transcript string (minimum input)
    - A TranscriptResult (richer input: provides segments, duration, logprob)

All metric modules live under speakup.services.analysis.* and can be
tested independently without hitting the database.
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from speakup.config.settings import settings
from speakup.database.connection import get_session
from speakup.database.repository import AnalysisResultRepository

# Analysis sub-modules
from speakup.services.analysis.fluency      import analyse_fluency
from speakup.services.analysis.vocabulary   import analyse_vocabulary
from speakup.services.analysis.filler_words import analyse_filler_words
from speakup.services.analysis.grammar      import get_grammar_analyzer
from speakup.services.analysis.confidence   import analyse_confidence
from speakup.services.analysis.scoring      import compute_overall_score
from speakup.services.analysis.feedback     import generate_feedback
from speakup.services.analysis.schemas      import SpeechAnalysisResult

if TYPE_CHECKING:
    from speakup.services.transcription_schemas import TranscriptResult

logger = logging.getLogger(__name__)


class AnalysisService:
    """
    Analyses a session transcript and stores the results.

    Entry points
    ────────────
    analyse_session(session_id, transcript, transcript_result=None)
        Full pipeline — run all metrics, persist to DB, return dict.

    analyse_transcript_object(session_id, transcript_result)
        Same pipeline but accepts a TranscriptResult object for richer
        segment-level metrics (pauses, logprob).

    Read-only helpers
    ─────────────────────────────────────────────────────
    get_result_for_session(session_id)
    get_average_scores_for_user(user_id)
    """

    # ------------------------------------------------------------------ #
    # Main analysis pipeline                                               #
    # ------------------------------------------------------------------ #

    def analyse_session(
        self,
        session_id: int,
        transcript: str,
        transcript_result: Optional["TranscriptResult"] = None,
        duration_seconds: int = 0,
        grammar_backend: str | None = None,
    ) -> dict:
        """
        Run the full analysis pipeline and persist results to the database.

        Args:
            session_id:         The practice session to analyse.
            transcript:         Plain-text transcript string (minimum required).
            transcript_result:  Optional :class:`TranscriptResult` from Whisper.
                                When provided, enables segment-level metrics
                                (pause detection, logprob-based scoring).
            duration_seconds:   Fallback audio duration when *transcript_result*
                                is not provided.
            grammar_backend:    Override ``settings.ANALYSIS_GRAMMAR_BACKEND``.

        Returns:
            A dict keyed by AnalysisResult DB column names plus extra
            UI metadata from :meth:`SpeechAnalysisResult.to_ui_dict`.

        Raises:
            RuntimeError: if database persistence fails.
        """
        result = self._run_pipeline(
            session_id=session_id,
            transcript=transcript,
            transcript_result=transcript_result,
            duration_seconds=duration_seconds,
            grammar_backend=grammar_backend,
        )

        self._persist(session_id, result)

        ui_dict = result.to_ui_dict()
        logger.info(
            "Analysis complete  session=%s  overall=%.1f  fluency=%.1f  vocab=%.1f",
            session_id,
            ui_dict.get("overall_score", 0),
            ui_dict.get("fluency_score", 0),
            ui_dict.get("vocabulary_score", 0),
        )
        return ui_dict

    def analyse_transcript_object(
        self,
        session_id: int,
        transcript_result: "TranscriptResult",
        grammar_backend: str | None = None,
    ) -> dict:
        """
        Convenience wrapper that extracts text and duration from
        a :class:`TranscriptResult` then calls :meth:`analyse_session`.
        """
        return self.analyse_session(
            session_id=session_id,
            transcript=transcript_result.to_plain_text(),
            transcript_result=transcript_result,
            duration_seconds=int(transcript_result.duration),
            grammar_backend=grammar_backend,
        )

    # ------------------------------------------------------------------ #
    # Individual calculators (preserve original stub signatures)           #
    # ------------------------------------------------------------------ #

    def calculate_words_per_minute(
        self, transcript: str, duration_seconds: int
    ) -> float:
        """
        Calculate words per minute from transcript text and duration.

        Returns 0.0 if duration_seconds is 0 or transcript is empty.
        """
        if not transcript.strip() or duration_seconds <= 0:
            return 0.0
        word_count = len(transcript.split())
        return round(word_count / (duration_seconds / 60.0), 1)

    def detect_filler_words(self, transcript: str) -> dict:
        """
        Detect filler words in *transcript*.

        Returns:
            Dict mapping each detected filler word to its count.
            Example: ``{"um": 3, "like": 7, "you know": 2}``
        """
        metrics = analyse_filler_words(transcript)
        return dict(metrics.counts)

    def generate_feedback(self, scores: dict) -> str:
        """
        Generate natural-language feedback from a scores dict.

        Accepts the dict returned by :meth:`analyse_session` (or any dict
        with keys ``fluency_score``, ``vocabulary_score``, ``grammar_score``,
        ``confidence_score``, ``overall_score``).

        Returns:
            Multi-paragraph plain text string.
        """
        from speakup.services.analysis.schemas import (
            FluencyMetrics, VocabularyMetrics, FillerMetrics,
            GrammarMetrics, ConfidenceMetrics, OverallScore, ScoreBreakdown,
        )

        def _bd(dim: str, raw: float, w: float) -> ScoreBreakdown:
            return ScoreBreakdown(dim, round(raw, 2), w, round(raw * w, 2))

        # Build minimal metric stubs from the score dict
        fl = FluencyMetrics(
            words_per_minute=scores.get("words_per_minute", 0.0),
            pause_count=scores.get("pause_count", 0) or 0,
            avg_pause_duration_s=0.0,
            total_pause_duration_s=0.0,
            speech_ratio=0.8,
            disfluent_segment_ratio=0.0,
            score=float(scores.get("fluency_score", 50.0) or 50.0),
        )
        vo = VocabularyMetrics(
            total_words=scores.get("total_words", 100) or 100,
            unique_words=50,
            type_token_ratio=0.5,
            hapax_legomena_ratio=0.3,
            avg_word_length=5.0,
            repeated_words={},
            advanced_word_count=5,
            score=float(scores.get("vocabulary_score", 50.0) or 50.0),
        )
        fi = FillerMetrics(
            counts={},
            total_filler_count=scores.get("filler_word_count", 0) or 0,
            filler_rate=0.0,
            score=float(scores.get("filler_score", 70.0) or 70.0),
        )
        gr = GrammarMetrics(
            backend="rule_based",
            issues=[],
            issue_count=0,
            score=float(scores.get("grammar_score", 50.0) or 50.0),
        )
        co = ConfidenceMetrics(
            wpm_consistency_score=50.0,
            low_filler_score=70.0,
            vocabulary_richness_score=50.0,
            speech_continuity_score=60.0,
            logprob_quality_score=60.0,
            score=float(scores.get("confidence_score", 50.0) or 50.0),
        )
        overall_val = float(scores.get("overall_score", 50.0) or 50.0)
        w = settings.ANALYSIS_WEIGHT_FLUENCY
        overall = OverallScore(
            fluency=_bd("Fluency", fl.score, w),
            vocabulary=_bd("Vocabulary", vo.score, settings.ANALYSIS_WEIGHT_VOCABULARY),
            grammar=_bd("Grammar", gr.score, settings.ANALYSIS_WEIGHT_GRAMMAR),
            confidence=_bd("Confidence", co.score, settings.ANALYSIS_WEIGHT_CONFIDENCE),
            filler=_bd("Filler", fi.score, settings.ANALYSIS_WEIGHT_FILLER),
            total=overall_val,
        )
        bundle = generate_feedback(
            fluency=fl, vocabulary=vo, filler=fi,
            grammar=gr, confidence=co, overall=overall,
        )
        return bundle.to_text()

    # ------------------------------------------------------------------ #
    # Read operations                                                     #
    # ------------------------------------------------------------------ #

    def get_result_for_session(self, session_id: int) -> dict | None:
        """Return stored analysis scores for *session_id*, or None."""
        with get_session() as session:
            repo = AnalysisResultRepository(session)
            result = repo.get_by_session(session_id)
            if result is None:
                return None
            return {
                "id": result.id,
                "session_id": result.session_id,
                "pronunciation_score": result.pronunciation_score,
                "fluency_score": result.fluency_score,
                "grammar_score": result.grammar_score,
                "vocabulary_score": result.vocabulary_score,
                "confidence_score": result.confidence_score,
                "filler_word_count": result.filler_word_count,
                "words_per_minute": result.words_per_minute,
                "pause_count": result.pause_count,
                "overall_score": result.overall_score,
                "feedback": result.feedback,
                "created_at": result.created_at,
            }

    def get_average_scores_for_user(self, user_id: int) -> dict:
        """Return average scores across all sessions for *user_id*."""
        with get_session() as session:
            repo = AnalysisResultRepository(session)
            return repo.get_average_scores_for_user(user_id)

    # ------------------------------------------------------------------ #
    # Internal pipeline                                                    #
    # ------------------------------------------------------------------ #

    def _run_pipeline(
        self,
        session_id: int,
        transcript: str,
        transcript_result: Optional["TranscriptResult"],
        duration_seconds: int,
        grammar_backend: str | None,
    ) -> SpeechAnalysisResult:
        """Run all analysers and return a SpeechAnalysisResult."""

        # ── Resolve duration and segments from transcript_result ─────────
        if transcript_result is not None:
            duration = transcript_result.duration
            segments = transcript_result.speech_segments
            avg_logprob = (
                sum(s.avg_logprob for s in segments) / len(segments)
                if segments else -0.3
            )
        else:
            duration = float(duration_seconds)
            segments = None
            avg_logprob = -0.3   # neutral default when no segments available

        # ── Run individual analysers ─────────────────────────────────────
        fluency    = analyse_fluency(transcript, duration, segments)
        vocabulary = analyse_vocabulary(transcript)
        filler     = analyse_filler_words(transcript)
        grammar    = get_grammar_analyzer(grammar_backend).analyse(transcript)
        confidence = analyse_confidence(fluency, vocabulary, filler, avg_logprob)
        overall    = compute_overall_score(fluency, vocabulary, filler, grammar, confidence)
        feedback   = generate_feedback(fluency, vocabulary, filler, grammar, confidence, overall)

        return SpeechAnalysisResult(
            session_id=session_id,
            fluency=fluency,
            vocabulary=vocabulary,
            filler=filler,
            grammar=grammar,
            confidence=confidence,
            overall=overall,
            feedback=feedback,
        )

    def _persist(self, session_id: int, result: SpeechAnalysisResult) -> None:
        """
        Store the analysis result in the database.

        Guards against duplicate entries by checking for an existing row first
        (the DB has a UNIQUE constraint on session_id).
        """
        db_data = result.to_db_dict()
        try:
            with get_session() as session:
                repo = AnalysisResultRepository(session)
                existing = repo.get_by_session(session_id)
                if existing is not None:
                    # Update individual fields via update_status equivalent
                    # There is no update method on the repo — use ORM directly
                    for key, value in db_data.items():
                        if hasattr(existing, key):
                            setattr(existing, key, value)
                    logger.info(
                        "Updated existing AnalysisResult for session_id=%s", session_id
                    )
                else:
                    repo.create(session_id=session_id, **db_data)
                    logger.info(
                        "Created AnalysisResult for session_id=%s", session_id
                    )
        except Exception as exc:
            logger.error(
                "Failed to persist analysis result for session_id=%s: %s",
                session_id, exc,
            )
            raise
