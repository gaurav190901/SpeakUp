"""
speakup/services/transcription_providers.py
────────────────────────────────────────────
Provider abstraction layer for speech-to-text backends.

Design
------
BaseTranscriptionProvider
    └── OpenAIWhisperProvider   — hosted OpenAI Whisper API
    └── FasterWhisperProvider   — local inference via faster-whisper
    └── FallbackTranscriptionProvider — primary provider, local on failure
    └── MockTranscriptionProvider — deterministic stub for tests / dev

Any future provider (e.g., AssemblyAI, Azure) can be added here by subclassing BaseTranscriptionProvider without touching the
UI or TranscriptionService.

Usage
-----
    from speakup.services.transcription_providers import get_provider
    provider = get_provider()           # reads settings
    result   = provider.transcribe("/path/to/audio.wav")
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Optional, Protocol

from speakup.config.settings import settings
from speakup.services.transcription_schemas import (
    TranscriptResult,
    TranscriptSegment,
    TranscriptWord,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class BaseTranscriptionProvider(ABC):
    """
    Contract that every transcription backend must fulfil.

    A provider is stateless at the call level — it is created once
    (lazy model loading) and its ``transcribe()`` method can be called
    multiple times from any thread.
    """

    @abstractmethod
    def transcribe(self, audio_path: str | Path) -> TranscriptResult:
        """
        Transcribe *audio_path* and return a :class:`TranscriptResult`.

        Must be thread-safe — called from a QThread worker.
        Must raise ``TranscriptionError`` on fatal failure rather than
        returning an error-flagged result, so callers can distinguish
        "no speech" (valid empty result) from "model crashed" (exception).
        """

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Human-readable identifier, e.g. ``'faster-whisper (base)'``."""

    def warmup(self) -> None:
        """
        Optional: pre-load the model so the first ``transcribe()`` call
        is not delayed.  Default implementation is a no-op.
        """


class TranscriptionError(RuntimeError):
    """Raised by a provider when transcription cannot proceed."""


# ---------------------------------------------------------------------------
# OpenAIWhisperProvider
# ---------------------------------------------------------------------------

class _TranscriptionEndpoint(Protocol):
    def create(
        self,
        *,
        model: str,
        file: BinaryIO,
        response_format: str,
        timestamp_granularities: list[str],
        language: str | None = None,
    ) -> object: ...


class _AudioEndpoint(Protocol):
    transcriptions: _TranscriptionEndpoint


class _OpenAIClient(Protocol):
    audio: _AudioEndpoint


class OpenAIWhisperProvider(BaseTranscriptionProvider):
    """Transcription using OpenAI's hosted Whisper API."""

    _MAX_AUDIO_BYTES = 25 * 1024 * 1024

    def __init__(
        self,
        model: str | None = None,
        language: str | None = None,
        client: _OpenAIClient | None = None,
    ) -> None:
        self._model = model or settings.OPENAI_WHISPER_MODEL
        self._language = language if language is not None else (
            settings.WHISPER_LANGUAGE or None
        )
        self._client = client

    @property
    def backend_name(self) -> str:
        return f"OpenAI Whisper ({self._model})"

    def transcribe(self, audio_path: str | Path) -> TranscriptResult:
        path = Path(audio_path)
        if not path.exists():
            raise TranscriptionError(f"Audio file not found: {path}")
        if path.stat().st_size == 0:
            return TranscriptResult.empty(
                audio_path=str(path),
                error="Audio file is empty — no speech to transcribe.",
            )
        if path.stat().st_size > self._MAX_AUDIO_BYTES:
            raise TranscriptionError(
                "Audio file exceeds the 25 MB limit for OpenAI Whisper. "
                "Record a shorter session and try again."
            )

        client = self._get_client()
        try:
            with path.open("rb") as audio_file:
                response = client.audio.transcriptions.create(
                    model=self._model,
                    file=audio_file,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                    language=self._language,
                )
        except Exception as exc:
            logger.exception("OpenAI Whisper transcription failed for %s", path.name)
            raise TranscriptionError(f"OpenAI Whisper transcription failed: {exc}") from exc

        text = str(getattr(response, "text", "") or "").strip()
        raw_segments = getattr(response, "segments", None) or []
        segments = [
            TranscriptSegment(
                id=int(getattr(segment, "id", index)),
                start=float(getattr(segment, "start", 0.0) or 0.0),
                end=float(getattr(segment, "end", 0.0) or 0.0),
                text=str(getattr(segment, "text", "") or ""),
                avg_logprob=float(getattr(segment, "avg_logprob", 0.0) or 0.0),
                no_speech_prob=float(getattr(segment, "no_speech_prob", 0.0) or 0.0),
            )
            for index, segment in enumerate(raw_segments)
        ]
        return TranscriptResult(
            text=text,
            segments=segments,
            language=str(
                getattr(response, "language", None) or self._language or ""
            ),
            duration=float(getattr(response, "duration", 0.0) or 0.0),
            model_size=self._model,
            audio_path=str(path),
        )

    def _get_client(self) -> _OpenAIClient:
        if self._client is not None:
            return self._client
        if not settings.has_openai_key():
            raise TranscriptionError(
                "OpenAI Whisper requires an API key. Set OPENAI_API_KEY in your "
                ".env file, or set TRANSCRIPTION_BACKEND=faster_whisper to use "
                "local transcription."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise TranscriptionError(
                "The OpenAI package is not installed. Install the project "
                "requirements to use OpenAI Whisper."
            ) from exc

        try:
            self._client = OpenAI(api_key=settings.OPENAI_API_KEY)
        except Exception as exc:
            raise TranscriptionError(f"Could not initialize OpenAI client: {exc}") from exc
        return self._client


# ---------------------------------------------------------------------------
# FasterWhisperProvider
# ---------------------------------------------------------------------------

class FasterWhisperProvider(BaseTranscriptionProvider):
    """
    Local CPU/GPU transcription using `faster-whisper`.

    Model is loaded lazily on the first ``transcribe()`` call and cached
    for subsequent calls in the same process.
    """

    def __init__(
        self,
        model_size: str | None = None,
        model_path: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        language: str | None = None,
        cpu_threads: int | None = None,
        beam_size: int | None = None,
    ) -> None:
        self._model_size   = model_size   or settings.WHISPER_MODEL_SIZE
        self._model_path   = model_path   or settings.WHISPER_MODEL_PATH or None
        self._device       = device       or settings.WHISPER_DEVICE
        self._compute_type = compute_type or settings.WHISPER_COMPUTE_TYPE
        self._language     = language     or settings.WHISPER_LANGUAGE or None
        self._cpu_threads  = cpu_threads  or settings.WHISPER_CPU_THREADS
        self._beam_size    = beam_size    or settings.WHISPER_BEAM_SIZE

        self._model = None   # loaded lazily

    @property
    def backend_name(self) -> str:
        return f"faster-whisper ({self._model_size})"

    def warmup(self) -> None:
        """Load the model into memory without transcribing."""
        self._load_model()

    def _load_model(self):
        """Load (or return cached) WhisperModel."""
        if self._model is not None:
            return self._model

        from faster_whisper import WhisperModel  # lazy import — heavy dependency

        model_source = self._model_path or self._model_size
        logger.info(
            "Loading faster-whisper model: %s  device=%s  compute_type=%s",
            model_source, self._device, self._compute_type,
        )
        try:
            self._model = WhisperModel(
                model_source,
                device=self._device,
                compute_type=self._compute_type,
                cpu_threads=self._cpu_threads,
            )
            logger.info("faster-whisper model loaded: %s", model_source)
        except Exception as exc:
            logger.error("Failed to load faster-whisper model: %s", exc)
            raise TranscriptionError(f"Model load failed: {exc}") from exc

        return self._model

    def transcribe(self, audio_path: str | Path) -> TranscriptResult:
        """
        Transcribe *audio_path* and return a :class:`TranscriptResult`.

        Args:
            audio_path: Path to a WAV or MP3 audio file.

        Returns:
            :class:`TranscriptResult` with full text, segments, and metadata.

        Raises:
            TranscriptionError: on model or audio errors.
        """
        path = Path(audio_path)
        if not path.exists():
            raise TranscriptionError(f"Audio file not found: {path}")
        if path.stat().st_size == 0:
            logger.warning("Audio file is empty: %s", path)
            return TranscriptResult.empty(
                audio_path=str(path),
                error="Audio file is empty — no speech to transcribe.",
            )

        model = self._load_model()

        logger.info(
            "Transcribing %s  (language=%s beam_size=%d)",
            path.name, self._language or "auto", self._beam_size,
        )

        try:
            segments_iter, info = model.transcribe(
                str(path),
                language=self._language or None,
                beam_size=self._beam_size,
                word_timestamps=True,
                vad_filter=True,          # skip silent regions
                vad_parameters={
                    "min_silence_duration_ms": 500,
                },
            )

            segments: list[TranscriptSegment] = []
            full_text_parts: list[str] = []

            for raw_seg in segments_iter:
                words: list[TranscriptWord] = []
                if raw_seg.words:
                    for w in raw_seg.words:
                        words.append(
                            TranscriptWord(
                                word=w.word,
                                start=w.start,
                                end=w.end,
                                probability=w.probability,
                            )
                        )

                seg = TranscriptSegment(
                    id=raw_seg.id,
                    start=raw_seg.start,
                    end=raw_seg.end,
                    text=raw_seg.text,
                    avg_logprob=raw_seg.avg_logprob,
                    no_speech_prob=raw_seg.no_speech_prob,
                    words=words,
                )
                segments.append(seg)
                full_text_parts.append(raw_seg.text)

            full_text = " ".join(p.strip() for p in full_text_parts if p.strip())

            result = TranscriptResult(
                text=full_text,
                segments=segments,
                language=info.language,
                language_probability=info.language_probability,
                duration=info.duration,
                model_size=self._model_size,
                audio_path=str(path),
            )

            logger.info(
                "Transcription complete: %d words, %d segments, lang=%s (%.0f%%)",
                result.word_count,
                len(segments),
                info.language,
                info.language_probability * 100,
            )
            return result

        except TranscriptionError:
            raise
        except Exception as exc:
            logger.exception("Transcription failed for %s: %s", path.name, exc)
            raise TranscriptionError(f"Transcription error: {exc}") from exc


# ---------------------------------------------------------------------------
# MockTranscriptionProvider — for tests / development
# ---------------------------------------------------------------------------

class MockTranscriptionProvider(BaseTranscriptionProvider):
    """
    Deterministic mock that returns a fixed transcript without loading any model.

    Configurable via constructor for targeted test scenarios.
    """

    def __init__(
        self,
        transcript: str = "This is a mock transcript for testing purposes.",
        language: str = "en",
        raise_error: bool = False,
        error_message: str = "Mock transcription error",
        empty_result: bool = False,
    ) -> None:
        self._transcript    = transcript
        self._language      = language
        self._raise_error   = raise_error
        self._error_message = error_message
        self._empty_result  = empty_result

    @property
    def backend_name(self) -> str:
        return "mock"

    def transcribe(self, audio_path: str | Path) -> TranscriptResult:
        if self._raise_error:
            raise TranscriptionError(self._error_message)

        if self._empty_result:
            return TranscriptResult.empty(
                audio_path=str(audio_path),
                error="No speech detected.",
            )

        # Build a minimal but realistic-looking result
        seg = TranscriptSegment(
            id=0,
            start=0.0,
            end=5.0,
            text=f" {self._transcript}",
            avg_logprob=-0.2,
            no_speech_prob=0.05,
        )
        return TranscriptResult(
            text=self._transcript,
            segments=[seg],
            language=self._language,
            language_probability=0.99,
            duration=5.0,
            model_size="mock",
            audio_path=str(audio_path),
        )


# ---------------------------------------------------------------------------
# FallbackTranscriptionProvider
# ---------------------------------------------------------------------------

class FallbackTranscriptionProvider(BaseTranscriptionProvider):
    """
    Try *primary*; on :class:`TranscriptionError` retry with *fallback*.

    Used so an OpenAI outage, network loss, or oversized file degrades to
    local transcription instead of losing the user's session.
    """

    def __init__(
        self,
        primary: BaseTranscriptionProvider,
        fallback: BaseTranscriptionProvider,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    @property
    def backend_name(self) -> str:
        return f"{self._primary.backend_name}, falls back to {self._fallback.backend_name}"

    def transcribe(self, audio_path: str | Path) -> TranscriptResult:
        try:
            return self._primary.transcribe(audio_path)
        except TranscriptionError as primary_error:
            if not Path(audio_path).exists():
                raise
            logger.warning(
                "%s failed (%s) — retrying with %s",
                self._primary.backend_name, primary_error,
                self._fallback.backend_name,
            )
            try:
                return self._fallback.transcribe(audio_path)
            except TranscriptionError as fallback_error:
                raise TranscriptionError(
                    f"{primary_error}\n\nLocal transcription also failed: "
                    f"{fallback_error}"
                ) from fallback_error


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_provider(
    backend: str | None = None,
    **kwargs,
) -> BaseTranscriptionProvider:
    """
    Return the configured transcription provider.

    Args:
        backend: Override ``settings.TRANSCRIPTION_BACKEND``.
                 ``'openai_whisper'`` | ``'faster_whisper'`` | ``'mock'``.
        **kwargs: Passed through to the provider constructor (the primary
                  provider when a local fallback is wrapped around it).

    With ``openai_whisper`` and ``settings.TRANSCRIPTION_LOCAL_FALLBACK``
    enabled (the default), a missing API key selects local transcription
    directly, and API failures are retried locally.

    Returns:
        A ready-to-use :class:`BaseTranscriptionProvider`.
    """
    b = backend or settings.TRANSCRIPTION_BACKEND
    if b == "mock":
        return MockTranscriptionProvider(**kwargs)
    if b == "openai_whisper":
        if not settings.TRANSCRIPTION_LOCAL_FALLBACK:
            return OpenAIWhisperProvider(**kwargs)
        if not settings.has_openai_key() and "client" not in kwargs:
            logger.info(
                "No OPENAI_API_KEY configured — using local transcription."
            )
            return FasterWhisperProvider()
        return FallbackTranscriptionProvider(
            OpenAIWhisperProvider(**kwargs), FasterWhisperProvider()
        )
    if b == "faster_whisper":
        return FasterWhisperProvider(**kwargs)
    raise ValueError(
        f"Unknown transcription backend {b!r}. "
        "Valid options: 'openai_whisper', 'faster_whisper', 'mock'."
    )
