"""
TranscriptionService — async speech-to-text for SpeakUp practice sessions.

Architecture
────────────
TranscriptionWorker (QThread)
    Calls provider.transcribe(wav_path) on a background thread.
    Emits progress / result / error signals back to the GUI thread.

TranscriptionService
    Thin facade:
    - Holds and manages the worker thread.
    - Exposes the worker's signals for UI connection.
    - Handles WAV path resolution from an MP4 recording path.
    - Stores the completed transcript to the database via WordService.
    - Supports retry on failure.

Signals (connect from the UI)
──────────────────────────────
    worker.status_changed(str)      — TranscriptionStatus value
    worker.progress_message(str)    — human-readable progress text
    worker.transcript_ready(TranscriptResult)
    worker.error_occurred(str)      — error message

Usage
─────
    svc = TranscriptionService()
    svc.worker.transcript_ready.connect(page.on_transcript_ready)
    svc.worker.error_occurred.connect(page.on_error)
    svc.start(session_id=42, audio_path="/recordings/2026/09/session_42.wav")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot

from speakup.config.settings import settings
from speakup.config.settings import settings
from speakup.services.transcription_providers import (
    BaseTranscriptionProvider,
    TranscriptionError,
    get_provider,
)
from speakup.services.transcription_schemas import (
    TranscriptResult,
    TranscriptionStatus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TranscriptionWorker — runs in a QThread
# ---------------------------------------------------------------------------

class TranscriptionWorker(QObject):
    """
    Background worker that runs the transcription provider.

    Must be moved to a QThread before starting:

        worker = TranscriptionWorker(provider, audio_path)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        thread.start()
    """

    status_changed    = Signal(str)           # TranscriptionStatus value
    progress_message  = Signal(str)           # UI status text
    transcript_ready  = Signal(object)        # TranscriptResult (use object for compat)
    error_occurred    = Signal(str)           # human-readable error

    def __init__(
        self,
        provider: BaseTranscriptionProvider,
        audio_path: str | Path,
        session_id: int,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._provider   = provider
        self._audio_path = Path(audio_path)
        self._session_id = session_id

    @Slot()
    def run(self) -> None:
        """Entry point — called when the thread starts."""
        logger.info(
            "TranscriptionWorker.run  session_id=%s  audio=%s",
            self._session_id, self._audio_path.name,
        )

        self.status_changed.emit(TranscriptionStatus.PROCESSING)
        self.progress_message.emit("Analyzing your speech…")

        try:
            # Validate audio file
            if not self._audio_path.exists():
                raise TranscriptionError(
                    f"Audio file not found: {self._audio_path}"
                )

            self.progress_message.emit(
                f"Loading model ({settings.WHISPER_MODEL_SIZE})…"
            )

            result = self._provider.transcribe(self._audio_path)

            if result.error:
                # Provider signalled a soft failure (e.g. empty audio)
                logger.warning("Transcription soft failure: %s", result.error)
                self.status_changed.emit(TranscriptionStatus.FAILED)
                self.error_occurred.emit(result.error)
                return

            self.status_changed.emit(TranscriptionStatus.COMPLETED)
            self.progress_message.emit(
                f"Transcription complete — {result.word_count} words detected."
            )
            self.transcript_ready.emit(result)

        except TranscriptionError as exc:
            logger.error("TranscriptionWorker error: %s", exc)
            self.status_changed.emit(TranscriptionStatus.FAILED)
            self.error_occurred.emit(str(exc))

        except Exception as exc:
            logger.exception("TranscriptionWorker unexpected error: %s", exc)
            self.status_changed.emit(TranscriptionStatus.FAILED)
            self.error_occurred.emit(f"Unexpected error: {exc}")


# ---------------------------------------------------------------------------
# TranscriptionService — facade / manager
# ---------------------------------------------------------------------------

class TranscriptionService:
    """
    Manages one transcription session lifecycle.

    The service holds the QThread + worker, exposes their signals for UI
    connection, and provides ``start()`` and ``cancel()`` control.
    """

    def __init__(
        self,
        provider: BaseTranscriptionProvider | None = None,
        backend: str | None = None,
    ) -> None:
        # In TEST_MODE, always use the mock provider regardless of config
        if provider is None and settings.TEST_MODE:
            from speakup.services.transcription_providers import MockTranscriptionProvider
            provider = MockTranscriptionProvider(
                transcript="This is a test-mode transcript. "
                           "Real transcription requires a microphone and Whisper model.",
                language="en",
            )
        self._provider: BaseTranscriptionProvider = (
            provider or get_provider(backend=backend)
        )
        self._status: str = TranscriptionStatus.PENDING

        self._worker: Optional[TranscriptionWorker] = None
        self._thread: Optional[QThread] = None

    # ------------------------------------------------------------------ #
    # Properties                                                            #
    # ------------------------------------------------------------------ #

    @property
    def status(self) -> str:
        return self._status

    @property
    def worker(self) -> Optional[TranscriptionWorker]:
        """The underlying worker — connect signals from here."""
        return self._worker

    @property
    def backend_name(self) -> str:
        return self._provider.backend_name

    # ------------------------------------------------------------------ #
    # Control                                                               #
    # ------------------------------------------------------------------ #

    def start(self, session_id: int, audio_path: str | Path) -> None:
        """
        Kick off async transcription.

        Args:
            session_id: The practice session being transcribed.
            audio_path: Path to the WAV file (preferred) or MP4.
        """
        # Resolve WAV path from MP4 if needed
        resolved = self._resolve_audio_path(audio_path)

        # Tear down any previous run
        self._cleanup_thread()

        self._status = TranscriptionStatus.PROCESSING

        self._worker = TranscriptionWorker(
            provider=self._provider,
            audio_path=resolved,
            session_id=session_id,
        )
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        # Forward worker status to service-level tracking
        self._worker.status_changed.connect(self._on_status)

        # Quit the thread when the worker finishes
        self._worker.transcript_ready.connect(
            lambda _: self._thread.quit() if self._thread else None
        )
        self._worker.error_occurred.connect(
            lambda _: self._thread.quit() if self._thread else None
        )

        self._thread.started.connect(self._worker.run)
        self._thread.start()

        logger.info(
            "TranscriptionService.start  session_id=%s  audio=%s  backend=%s",
            session_id, resolved, self._provider.backend_name,
        )

    def cancel(self) -> None:
        """Abort transcription (best-effort — worker may already be done)."""
        self._cleanup_thread()
        self._status = TranscriptionStatus.FAILED

    def release(self) -> None:
        """Release all resources."""
        self._cleanup_thread()

    # ------------------------------------------------------------------ #
    # WAV path resolution
    # ------------------------------------------------------------------ #

    @staticmethod
    def resolve_wav_path(recording_path: str | Path) -> Path:
        """
        Given an MP4 or WAV path, return the sibling WAV file path.

        Recording format (from Chunk 3):
            recordings/YYYY/MM/session_<id>.mp4   ← video
            recordings/YYYY/MM/session_<id>.wav   ← audio (sibling)
        """
        p = Path(recording_path)
        wav = p.with_suffix(".wav")
        if wav.exists():
            return wav
        # Fallback: try the path itself if it's already a WAV
        if p.suffix.lower() == ".wav" and p.exists():
            return p
        # Return the expected WAV path even if it doesn't exist yet
        # (the worker will raise TranscriptionError with a clear message)
        return wav

    def _resolve_audio_path(self, audio_path: str | Path) -> Path:
        p = Path(audio_path)
        if p.suffix.lower() == ".mp4":
            return self.resolve_wav_path(p)
        return p

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _on_status(self, status: str) -> None:
        self._status = status

    def _cleanup_thread(self) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(5000)
        self._worker = None
        self._thread = None

    # ------------------------------------------------------------------ #
    # Synchronous convenience (for tests and non-Qt contexts)             #
    # ------------------------------------------------------------------ #

    def transcribe_sync(
        self, audio_path: str | Path
    ) -> TranscriptResult:
        """
        Blocking transcription — runs in the calling thread.

        Useful in tests and CLI contexts.
        """
        resolved = self._resolve_audio_path(audio_path)
        result = self._provider.transcribe(resolved)
        self._status = (
            TranscriptionStatus.COMPLETED
            if result.has_content
            else TranscriptionStatus.FAILED
        )
        return result
