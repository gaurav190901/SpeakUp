"""
speakup/services/transcription_schemas.py
──────────────────────────────────────────
Pure-Python dataclasses and constants for the transcription system.

No Qt, no database, no model dependencies — safe to import anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Transcription status
# ---------------------------------------------------------------------------

class TranscriptionStatus:
    """Named constants for the transcription lifecycle."""

    PENDING    = "pending"      # audio exists, transcription not started
    PROCESSING = "processing"   # model is running
    COMPLETED  = "completed"    # transcript available
    FAILED     = "failed"       # error occurred; may be retried

    ALL: tuple[str, ...] = (PENDING, PROCESSING, COMPLETED, FAILED)

    @classmethod
    def is_terminal(cls, status: str) -> bool:
        return status in (cls.COMPLETED, cls.FAILED)


# ---------------------------------------------------------------------------
# Transcript data structures
# ---------------------------------------------------------------------------

@dataclass
class TranscriptWord:
    """A single word with optional timing from word-level timestamps."""

    word: str
    start: float  # seconds from audio start
    end: float    # seconds from audio start
    probability: float = 1.0  # confidence score 0–1


@dataclass
class TranscriptSegment:
    """
    One spoken segment (typically a sentence or clause).

    Corresponds to a Whisper segment — has start/end times and text.
    """

    id: int
    start: float         # seconds
    end: float           # seconds
    text: str            # segment text (may have leading space)
    avg_logprob: float = 0.0   # log-probability (quality indicator)
    no_speech_prob: float = 0.0  # probability of silence/noise
    words: list[TranscriptWord] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def is_likely_speech(self) -> bool:
        """Return True when the segment is likely actual speech (not noise)."""
        return self.no_speech_prob < 0.6

    @property
    def text_clean(self) -> str:
        """Strip leading/trailing whitespace."""
        return self.text.strip()


@dataclass
class TranscriptResult:
    """
    Complete transcription result for one audio file.

    Produced by a provider and consumed by TranscriptionService / UI.
    """

    text: str                               # full concatenated transcript
    segments: list[TranscriptSegment]       # time-aligned segments
    language: str = "en"                    # detected or forced language
    language_probability: float = 1.0       # confidence in detected language
    duration: float = 0.0                   # total audio duration (seconds)
    model_size: str = ""                    # which model was used
    audio_path: str = ""                    # absolute path of the source file

    # Error information (populated when status == FAILED)
    error: Optional[str] = None

    @classmethod
    def empty(cls, audio_path: str = "", error: str = "") -> "TranscriptResult":
        """Return a TranscriptResult representing an empty/failed transcription."""
        return cls(
            text="",
            segments=[],
            language="",
            language_probability=0.0,
            duration=0.0,
            error=error or None,
            audio_path=audio_path,
        )

    @property
    def word_count(self) -> int:
        return len(self.text.split()) if self.text.strip() else 0

    @property
    def has_content(self) -> bool:
        return bool(self.text.strip())

    @property
    def speech_segments(self) -> list[TranscriptSegment]:
        """Return only segments that are likely actual speech."""
        return [s for s in self.segments if s.is_likely_speech]

    @property
    def status(self) -> str:
        if self.error:
            return TranscriptionStatus.FAILED
        if self.has_content:
            return TranscriptionStatus.COMPLETED
        return TranscriptionStatus.FAILED

    def to_plain_text(self) -> str:
        """Return the full transcript as clean plain text."""
        return self.text.strip()

    def to_timestamped_text(self) -> str:
        """
        Return a human-readable timestamped representation.

        Example:
            [00:02 → 00:08]  Hello, my name is Alice.
            [00:09 → 00:15]  Today I want to talk about leadership.
        """
        lines: list[str] = []
        for seg in self.speech_segments:
            start_m, start_s = divmod(int(seg.start), 60)
            end_m,   end_s   = divmod(int(seg.end),   60)
            lines.append(
                f"[{start_m:02d}:{start_s:02d} → {end_m:02d}:{end_s:02d}]"
                f"  {seg.text_clean}"
            )
        return "\n".join(lines)
