"""
speakup/services/analysis_worker.py
──────────────────────────────────────
Async wrapper that runs AnalysisService inside a QThread so the GUI
never freezes during analysis (which can take a few seconds on large
transcripts with grammar checking).

Signals (connect from the UI)
──────────────────────────────
    AnalysisWorker.status_changed(str)      — "running" | "completed" | "failed"
    AnalysisWorker.progress_message(str)    — human-readable progress text
    AnalysisWorker.analysis_ready(object)   — dict from SpeechAnalysisResult.to_ui_dict()
    AnalysisWorker.error_occurred(str)      — error message on failure

Usage
─────
    worker = AnalysisWorker(
        session_id=42,
        transcript="Hello world…",
        transcript_result=result_obj,   # optional TranscriptResult
        duration_seconds=75,
    )
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.analysis_ready.connect(my_slot)
    worker.error_occurred.connect(my_error_slot)
    thread.start()
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, Signal, Slot

from speakup.services.analysis_service import AnalysisService

if TYPE_CHECKING:
    from speakup.services.transcription_schemas import TranscriptResult

logger = logging.getLogger(__name__)

_STATUS_RUNNING   = "running"
_STATUS_COMPLETED = "completed"
_STATUS_FAILED    = "failed"


class AnalysisWorker(QObject):
    """
    Background worker that runs the full analysis pipeline.

    Move to a QThread before starting (see module docstring for pattern).
    """

    status_changed   = Signal(str)      # _STATUS_* values
    progress_message = Signal(str)      # UI-displayable progress text
    analysis_ready   = Signal(object)   # dict (ui_dict from SpeechAnalysisResult)
    error_occurred   = Signal(str)      # human-readable error message

    def __init__(
        self,
        session_id: int,
        transcript: str,
        transcript_result: Optional["TranscriptResult"] = None,
        duration_seconds: int = 0,
        grammar_backend: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_id       = session_id
        self._transcript       = transcript
        self._transcript_result = transcript_result
        self._duration_seconds = duration_seconds
        self._grammar_backend  = grammar_backend

    @Slot()
    def run(self) -> None:
        """Entry point — called when the thread starts."""
        logger.info(
            "AnalysisWorker.run  session_id=%s  words=%d",
            self._session_id, len(self._transcript.split()),
        )
        self.status_changed.emit(_STATUS_RUNNING)
        self.progress_message.emit("Calculating your speaking scores…")

        try:
            svc = AnalysisService()

            self.progress_message.emit("Analysing fluency and vocabulary…")
            result_dict = svc.analyse_session(
                session_id=self._session_id,
                transcript=self._transcript,
                transcript_result=self._transcript_result,
                duration_seconds=self._duration_seconds,
                grammar_backend=self._grammar_backend,
            )

            self.status_changed.emit(_STATUS_COMPLETED)
            self.progress_message.emit(
                f"Analysis complete — overall score: "
                f"{result_dict.get('overall_score', 0):.0f}/100"
            )
            self.analysis_ready.emit(result_dict)

        except Exception as exc:
            logger.exception("AnalysisWorker error: %s", exc)
            self.status_changed.emit(_STATUS_FAILED)
            self.error_occurred.emit(f"Analysis failed: {exc}")


# ---------------------------------------------------------------------------
# AnalysisService facade (manages thread lifecycle)
# ---------------------------------------------------------------------------

class AnalysisRunner:
    """
    Convenience class that owns the QThread + AnalysisWorker lifecycle.

    Usage
    ─────
        runner = AnalysisRunner()
        runner.worker.analysis_ready.connect(page.show_analysis)
        runner.worker.error_occurred.connect(page.on_analysis_error)
        runner.start(session_id, transcript, transcript_result, duration)
    """

    def __init__(self) -> None:
        self._worker: Optional[AnalysisWorker] = None
        self._thread: Optional[QThread] = None

    @property
    def worker(self) -> Optional[AnalysisWorker]:
        return self._worker

    def start(
        self,
        session_id: int,
        transcript: str,
        transcript_result: Optional["TranscriptResult"] = None,
        duration_seconds: int = 0,
        grammar_backend: str | None = None,
    ) -> None:
        """Start async analysis.  Replaces any previously running worker."""
        self._cleanup()

        self._worker = AnalysisWorker(
            session_id=session_id,
            transcript=transcript,
            transcript_result=transcript_result,
            duration_seconds=duration_seconds,
            grammar_backend=grammar_backend,
        )
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        # Quit the thread when the worker finishes (either way)
        self._worker.analysis_ready.connect(
            lambda _: self._thread.quit() if self._thread else None
        )
        self._worker.error_occurred.connect(
            lambda _: self._thread.quit() if self._thread else None
        )

        self._thread.started.connect(self._worker.run)
        self._thread.start()

        logger.debug("AnalysisRunner started for session_id=%s", session_id)

    def cancel(self) -> None:
        """Abort the running worker (best-effort)."""
        self._cleanup()

    def _cleanup(self) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        self._worker = None
        self._thread = None
