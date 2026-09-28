"""
speakup/services/feedback_service.py
──────────────────────────────────────
FeedbackService — orchestrates the AI feedback engine.

Responsibilities
────────────────
- Build a FeedbackRequest from session data
- Select the appropriate provider (local or LLM) with automatic fallback
- Run the analysis asynchronously in a QThread (FeedbackWorker)
- Expose provider signals for UI connection

Fallback chain
──────────────
    LLMFeedbackProvider  →  (on any error)  →  LocalFeedbackProvider

Usage
─────
    svc = FeedbackService()
    svc.worker.feedback_ready.connect(page.show_ai_feedback)
    svc.worker.error_occurred.connect(page.on_feedback_error)
    svc.start(session_id=42, request=req)
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, Signal, Slot

from speakup.config.settings import settings
from speakup.services.feedback_providers import (
    BaseFeedbackProvider,
    FeedbackProviderError,
    LocalFeedbackProvider,
    get_feedback_provider,
)
from speakup.services.feedback_schemas import AIFeedbackResult, FeedbackRequest

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ============================================================================
# FeedbackWorker — runs in a QThread
# ============================================================================

class FeedbackWorker(QObject):
    """
    Background worker that runs the feedback provider.

    Signals
    -------
    status_changed(str)      — "running" | "completed" | "failed"
    progress_message(str)    — human-readable progress text
    feedback_ready(object)   — AIFeedbackResult (use object type for cross-thread)
    error_occurred(str)      — human-readable error message
    """

    status_changed   = Signal(str)
    progress_message = Signal(str)
    feedback_ready   = Signal(object)    # AIFeedbackResult
    error_occurred   = Signal(str)

    def __init__(
        self,
        provider:   BaseFeedbackProvider,
        request:    FeedbackRequest,
        parent:     QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._provider = provider
        self._request  = request

    @Slot()
    def run(self) -> None:
        logger.info(
            "FeedbackWorker.run  backend=%s  words=%d",
            self._provider.backend_name,
            len(self._request.transcript.split()),
        )
        self.status_changed.emit("running")
        self.progress_message.emit("Generating AI feedback…")

        try:
            result = self._provider.analyze(self._request)

            # If the primary provider failed with an error, try local fallback
            if result.error and not isinstance(self._provider, LocalFeedbackProvider):
                logger.warning(
                    "Primary provider failed (%s) — falling back to local",
                    result.error,
                )
                self.progress_message.emit("Falling back to local analysis…")
                local_result = LocalFeedbackProvider().analyze(self._request)
                local_result.warning = (
                    f"LLM feedback unavailable ({result.error}). "
                    "Showing local analysis instead."
                )
                result = local_result

            self.status_changed.emit("completed")
            self.progress_message.emit("Feedback ready.")
            self.feedback_ready.emit(result)

        except Exception as exc:
            logger.exception("FeedbackWorker unexpected error: %s", exc)
            self.status_changed.emit("failed")
            self.error_occurred.emit(f"Feedback generation failed: {exc}")


# ============================================================================
# FeedbackService — facade
# ============================================================================

class FeedbackService:
    """
    Manages one feedback generation lifecycle per session.

    Thread lifecycle mirrors TranscriptionService / AnalysisWorker.
    """

    def __init__(
        self,
        provider:  BaseFeedbackProvider | None = None,
        backend:   str | None = None,
    ) -> None:
        self._provider: BaseFeedbackProvider = provider or get_feedback_provider(backend)
        self._worker:   Optional[FeedbackWorker] = None
        self._thread:   Optional[QThread] = None

    # ------------------------------------------------------------------ #
    # Properties                                                            #
    # ------------------------------------------------------------------ #

    @property
    def worker(self) -> Optional[FeedbackWorker]:
        return self._worker

    @property
    def backend_name(self) -> str:
        return self._provider.backend_name

    # ------------------------------------------------------------------ #
    # Control                                                               #
    # ------------------------------------------------------------------ #

    def start(self, request: FeedbackRequest) -> None:
        """Start async feedback generation."""
        self._cleanup()

        self._worker = FeedbackWorker(provider=self._provider, request=request)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        # Quit thread when worker finishes
        self._worker.feedback_ready.connect(
            lambda _: self._thread.quit() if self._thread else None
        )
        self._worker.error_occurred.connect(
            lambda _: self._thread.quit() if self._thread else None
        )

        self._thread.started.connect(self._worker.run)
        self._thread.start()

        logger.debug("FeedbackService started  backend=%s", self._provider.backend_name)

    def cancel(self) -> None:
        self._cleanup()

    def release(self) -> None:
        self._cleanup()

    # ------------------------------------------------------------------ #
    # Sync helper (tests / non-Qt contexts)                               #
    # ------------------------------------------------------------------ #

    def analyze_sync(self, request: FeedbackRequest) -> AIFeedbackResult:
        """Blocking analysis — runs in the calling thread."""
        result = self._provider.analyze(request)
        if result.error and not isinstance(self._provider, LocalFeedbackProvider):
            logger.warning("Sync fallback: %s", result.error)
            local = LocalFeedbackProvider().analyze(request)
            local.warning = f"LLM unavailable ({result.error}). Local analysis shown."
            return local
        return result

    # ------------------------------------------------------------------ #
    # Factory helper                                                        #
    # ------------------------------------------------------------------ #

    @staticmethod
    def build_request(
        analysis_result:  dict,
        transcript:       str = "",
        topic:            str = "",
        category:         str = "",
        duration_seconds: int = 0,
        prior_avg_overall:    float | None = None,
        prior_avg_confidence: float | None = None,
    ) -> FeedbackRequest:
        """
        Build a FeedbackRequest from the analysis_result dict and session info.

        Args:
            analysis_result:  Output of ``SpeechAnalysisResult.to_ui_dict()``
                              or ``AnalysisService.get_result_for_session()``.
            transcript:       Plain-text transcript.
            topic:            Topic word (e.g. "Leadership").
            category:         Topic category (e.g. "Business").
            duration_seconds: Recorded duration.
            prior_avg_overall:     User's historical average overall score.
            prior_avg_confidence:  User's historical average confidence score.

        Returns:
            :class:`FeedbackRequest` ready for provider.analyze().
        """
        return FeedbackRequest(
            transcript=transcript,
            topic=topic,
            category=category,
            duration_seconds=duration_seconds,
            words_per_minute=analysis_result.get("words_per_minute") or 0.0,
            filler_word_count=analysis_result.get("filler_word_count") or 0,
            filler_counts=analysis_result.get("filler_counts") or {},
            fluency_score=analysis_result.get("fluency_score") or 0.0,
            vocabulary_score=analysis_result.get("vocabulary_score") or 0.0,
            grammar_score=analysis_result.get("grammar_score") or 0.0,
            confidence_score=analysis_result.get("confidence_score") or 0.0,
            overall_score=analysis_result.get("overall_score") or 0.0,
            repeated_words=analysis_result.get("repeated_words") or {},
            prior_avg_overall=prior_avg_overall,
            prior_avg_confidence=prior_avg_confidence,
        )

    # ------------------------------------------------------------------ #
    # Internal                                                              #
    # ------------------------------------------------------------------ #

    def _cleanup(self) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        self._worker = None
        self._thread = None
