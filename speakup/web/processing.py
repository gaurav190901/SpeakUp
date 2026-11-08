"""
Background processing of uploaded recordings.

Pipeline for one session:
    browser audio (webm/ogg/mp4/wav) → 16 kHz mono WAV → complete session
    → transcribe → save transcript → analyse (scores stored in the DB)

Transcription can take a while on a CPU, so jobs run on a single worker
thread (one at a time, which also keeps the Whisper model loaded once) and
the browser polls :func:`get_status`.
"""

from __future__ import annotations

import logging
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from speakup.config.settings import settings
from speakup.database.connection import get_session
from speakup.database.repository import PracticeSessionRepository

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000

# Stage names shown to the browser
SAVING, TRANSCRIBING, ANALYSING, DONE, ERROR = (
    "saving", "transcribing", "analysing", "done", "error",
)


@dataclass
class JobStatus:
    stage: str
    message: str
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


_jobs: dict[int, JobStatus] = {}
_jobs_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="speakup-process")
_transcriber = None
_transcriber_lock = threading.Lock()


def _set(session_id: int, stage: str, message: str, error: str | None = None) -> None:
    with _jobs_lock:
        _jobs[session_id] = JobStatus(stage, message, error)


def get_status(session_id: int) -> JobStatus | None:
    with _jobs_lock:
        return _jobs.get(session_id)


def recording_dir() -> Path:
    now = datetime.now()
    path = Path(settings.RECORDINGS_DIR) / f"{now:%Y}" / f"{now:%m}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def convert_to_wav(source: Path, target: Path) -> float:
    """Decode any browser audio format to 16 kHz mono 16-bit WAV.

    Returns the audio duration in seconds.
    """
    from speakup.services.audio_decode import decode_audio  # noqa: PLC0415

    samples = decode_audio(source, sampling_rate=SAMPLE_RATE)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(target), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())
    return len(samples) / SAMPLE_RATE


def _get_transcriber():
    global _transcriber
    with _transcriber_lock:
        if _transcriber is None:
            from speakup.services.transcription_service import (  # noqa: PLC0415
                TranscriptionService,
            )
            _transcriber = TranscriptionService()
        return _transcriber


def reset_transcriber() -> None:
    """Forget the cached transcriber (used by tests)."""
    global _transcriber
    with _transcriber_lock:
        _transcriber = None


def process_recording(session_id: int, upload_path: Path, client_duration: float) -> None:
    """Run the full pipeline for one uploaded recording (blocking)."""
    from speakup.services.analysis_service import AnalysisService  # noqa: PLC0415
    from speakup.services.word_service import WordService  # noqa: PLC0415

    try:
        _set(session_id, SAVING, "Saving your recording…")
        wav_path = upload_path.with_suffix(".wav")
        try:
            duration = convert_to_wav(upload_path, wav_path)
        except Exception as exc:
            logger.exception("Could not decode upload for session %s", session_id)
            raise RuntimeError(
                "We couldn't read that recording. Please try recording again."
            ) from exc
        if wav_path != upload_path:
            upload_path.unlink(missing_ok=True)
        if duration <= 0:
            duration = max(0.0, client_duration)

        WordService().complete_practice(
            session_id=session_id,
            duration_seconds=int(round(duration)),
            recording_path=str(wav_path),
        )

        _set(session_id, TRANSCRIBING,
             "Turning your speech into text… The first time can take a few "
             "minutes while the speech model downloads.")
        result = _get_transcriber().transcribe_sync(wav_path)
        transcript = result.to_plain_text().strip() if result else ""
        if not transcript:
            raise RuntimeError(
                "We couldn't hear any speech in this recording. Check your "
                "microphone and try another session."
            )

        with get_session() as db:
            PracticeSessionRepository(db).update_status(
                session_id=session_id, status="completed", transcript=transcript,
            )

        _set(session_id, ANALYSING, "Scoring fluency, vocabulary and confidence…")
        AnalysisService().analyse_transcript_object(session_id, result)

        _set(session_id, DONE, "Your results are ready!")
        logger.info("Web session %s processed (%.0fs audio).", session_id, duration)
    except Exception as exc:
        logger.exception("Processing failed for session %s", session_id)
        message = str(exc) if isinstance(exc, RuntimeError) else (
            "Something went wrong while processing your recording."
        )
        _set(session_id, ERROR, message, error=message)


def submit(session_id: int, upload_path: Path, client_duration: float,
           background: bool = True) -> None:
    """Queue (or, in tests, run immediately) processing for a recording."""
    _set(session_id, SAVING, "Waiting to start…")
    if background:
        _executor.submit(process_recording, session_id, upload_path, client_duration)
    else:
        process_recording(session_id, upload_path, client_duration)
