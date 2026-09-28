"""
TranscriptionResultPage — shows transcription progress and the final transcript.

Layout (two phases)
───────────────────

Phase 1 — PROCESSING:
┌─────────────────────────────────────────────────────────────────┐
│  Recording complete                                              │
│  Topic: Artificial Intelligence                                  │
│                                                                  │
│  ┌─ Progress card ──────────────────────────────────────────┐  │
│  │                                                           │  │
│  │     🎙️                                                   │  │
│  │     Analyzing your speech…                               │  │
│  │     Loading model (base)…                                │  │
│  │     ▓▓▓▓▓▓░░░░░░░░░░░░░░  (indeterminate spinner)       │  │
│  │                                                           │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘

Phase 2 — COMPLETED / FAILED:
┌─────────────────────────────────────────────────────────────────┐
│  Recording complete                                              │
│  Topic: Artificial Intelligence                                  │
│                                                                  │
│  ┌─ Transcript card ────────────────────────────────────────┐  │
│  │  Your Transcript  (234 words • en 99%)                   │  │
│  │  ──────────────────────────────────────────────────────  │  │
│  │  Hello, today I want to talk about artificial…           │  │
│  │  [00:02 → 00:08]  Hello, today I want to talk about…    │  │
│  │  [Show timestamps] toggle                                │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌─ Stats chips ────────────────────────────────────────────┐  │
│  │  📝 Words: 234  │  ⏱ Duration: 01:15  │  🌐 Lang: en   │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  [🔄  Retry Transcription]  [Continue to Practice →]           │
└─────────────────────────────────────────────────────────────────┘

Signals
───────
transcription_saved(session_id: int, transcript: str)
    Emitted when transcription is done and stored.  MainWindow listens
    and navigates back to Practice.

transcription_skipped(session_id: int)
    Emitted when the user clicks Continue without a transcript.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from speakup.config.settings import settings
from speakup.services.transcription_schemas import (
    TranscriptResult,
    TranscriptionStatus,
)
from speakup.services.transcription_service import TranscriptionService
from speakup.services.transcription_providers import MockTranscriptionProvider
from speakup.services.analysis_worker import AnalysisRunner

logger = logging.getLogger(__name__)


class TranscriptionResultPage(QWidget):
    """
    Shows the "Analyzing your speech…" spinner, then the transcript.

    Parameters
    ----------
    parent : QWidget | None

    Usage (called by MainWindow)
    ----------------------------
        page.prepare(session_id, recording_path, topic)
        # navigate to PAGE_TRANSCRIPTION
        # page auto-starts transcription; emits transcription_saved when done
    """

    transcription_saved   = Signal(int, str)   # (session_id, transcript_text)
    transcription_skipped = Signal(int)         # (session_id,)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TranscriptionResultPage")

        self._session_id:     int | None = None
        self._recording_path: str = ""
        self._topic:          dict | None = None
        self._transcript_result: TranscriptResult | None = None
        self._show_timestamps: bool = False

        # TranscriptionService is created fresh for each session so a new
        # provider + worker is spun up each time (avoids stale state).
        self._svc: TranscriptionService | None = None

        # AnalysisRunner — started after transcription completes
        self._analysis_runner: AnalysisRunner = AnalysisRunner()

        self._build_ui()
        self._apply_styles()

    # ------------------------------------------------------------------ #
    # Public API called by MainWindow                                       #
    # ------------------------------------------------------------------ #

    def prepare(
        self,
        session_id: int,
        recording_path: str,
        topic: dict,
        provider=None,
    ) -> None:
        """
        Initialise the page and start async transcription.

        Args:
            session_id:     Practice session being transcribed.
            recording_path: Absolute path to the MP4 or WAV file.
            topic:          Topic dict (keys: word, category, difficulty_display).
            provider:       Optional provider override (used in tests/mock mode).
        """
        self._session_id     = session_id
        self._recording_path = recording_path
        self._topic          = topic
        self._transcript_result = None

        logger.info(
            "TranscriptionResultPage.prepare  session=%s  path=%s",
            session_id, recording_path,
        )

        self._reset_ui()
        self._populate_header(topic)
        self._show_processing_phase()
        self._start_transcription(recording_path, provider)

    def cleanup(self) -> None:
        """Cancel any running transcription and release resources."""
        if self._svc:
            self._svc.cancel()
            self._svc = None
        self._analysis_runner.cancel()

    # ------------------------------------------------------------------ #
    # UI construction                                                       #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setObjectName("TranscriptionScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("TranscriptionContent")
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(32, 32, 32, 32)
        self._layout.setSpacing(20)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── Header ────────────────────────────────────────────────────────
        self._header_title = QLabel("Recording complete")
        self._header_title.setObjectName("PageTitle")

        self._header_topic = QLabel("—")
        self._header_topic.setObjectName("PageSubtitle")

        self._layout.addWidget(self._header_title)
        self._layout.addWidget(self._header_topic)

        # ── Phase 1: Processing card ──────────────────────────────────────
        self._processing_card = self._build_processing_card()
        self._layout.addWidget(self._processing_card)

        # ── Phase 2: Transcript card ──────────────────────────────────────
        self._transcript_card = self._build_transcript_card()
        self._transcript_card.setVisible(False)
        self._layout.addWidget(self._transcript_card)

        # ── Stats chips ───────────────────────────────────────────────────
        self._stats_row = self._build_stats_row()
        self._stats_row.setVisible(False)
        self._layout.addWidget(self._stats_row)

        # ── Action buttons ────────────────────────────────────────────────
        self._buttons_row = self._build_buttons_row()
        self._buttons_row.setVisible(False)
        self._layout.addWidget(self._buttons_row)

        # ── Analysis card (shown after background analysis completes) ─────
        self._analysis_card = self._build_analysis_card()
        self._analysis_card.setVisible(False)
        self._layout.addWidget(self._analysis_card)

        self._layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _build_processing_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("ProcessingCard")
        card.setMinimumHeight(220)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel("🎙️")
        icon.setObjectName("ProcessingIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._processing_title = QLabel("Analyzing your speech…")
        self._processing_title.setObjectName("ProcessingTitle")
        self._processing_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._progress_detail = QLabel("")
        self._progress_detail.setObjectName("ProgressDetail")
        self._progress_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("TranscriptionProgress")
        self._progress_bar.setRange(0, 0)   # indeterminate
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.setTextVisible(False)

        layout.addWidget(icon)
        layout.addWidget(self._processing_title)
        layout.addWidget(self._progress_detail)
        layout.addWidget(self._progress_bar)
        return card

    def _build_transcript_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("TranscriptCard")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        # Title row
        title_row = QHBoxLayout()
        self._transcript_title = QLabel("Your Transcript")
        self._transcript_title.setObjectName("TranscriptTitle")

        self._transcript_meta = QLabel("")
        self._transcript_meta.setObjectName("TranscriptMeta")
        self._transcript_meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        title_row.addWidget(self._transcript_title)
        title_row.addStretch()
        title_row.addWidget(self._transcript_meta)
        layout.addLayout(title_row)

        # Separator line
        sep = QFrame()
        sep.setObjectName("TranscriptSep")
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # Transcript text area (read-only)
        self._transcript_text = QTextEdit()
        self._transcript_text.setObjectName("TranscriptText")
        self._transcript_text.setReadOnly(True)
        self._transcript_text.setMinimumHeight(180)
        self._transcript_text.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self._transcript_text)

        # Toggle timestamps button
        self._toggle_timestamps_btn = QPushButton("🕐  Show Timestamps")
        self._toggle_timestamps_btn.setObjectName("ToggleTimestampsBtn")
        self._toggle_timestamps_btn.setFixedHeight(34)
        self._toggle_timestamps_btn.setCheckable(True)
        self._toggle_timestamps_btn.clicked.connect(self._on_toggle_timestamps)
        layout.addWidget(self._toggle_timestamps_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        # Error banner (shown when transcript failed)
        self._error_banner = QLabel("")
        self._error_banner.setObjectName("ErrorBanner")
        self._error_banner.setWordWrap(True)
        self._error_banner.setVisible(False)
        layout.addWidget(self._error_banner)

        return card

    def _build_stats_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._chip_words    = self._make_stat_chip("📝", "Words",    "—")
        self._chip_duration = self._make_stat_chip("⏱️", "Duration", "—")
        self._chip_lang     = self._make_stat_chip("🌐", "Language", "—")
        self._chip_backend  = self._make_stat_chip("🤖", "Model",    "—")

        layout.addWidget(self._chip_words)
        layout.addWidget(self._chip_duration)
        layout.addWidget(self._chip_lang)
        layout.addWidget(self._chip_backend)
        layout.addStretch()
        return row

    @staticmethod
    def _make_stat_chip(icon: str, label: str, value: str) -> QFrame:
        chip = QFrame()
        chip.setObjectName("StatChip")
        layout = QVBoxLayout(chip)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(2)

        header = QLabel(f"{icon}  {label}")
        header.setObjectName("ChipHeader")

        val_lbl = QLabel(value)
        val_lbl.setObjectName("ChipValue")

        layout.addWidget(header)
        layout.addWidget(val_lbl)
        chip._value_label = val_lbl   # type: ignore[attr-defined]
        return chip

    def _build_buttons_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._retry_btn = QPushButton("🔄  Retry Transcription")
        self._retry_btn.setObjectName("RetryBtn")
        self._retry_btn.setFixedHeight(44)
        self._retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._retry_btn.clicked.connect(self._on_retry)

        self._continue_btn = QPushButton("Continue to Practice  →")
        self._continue_btn.setObjectName("ContinueBtn")
        self._continue_btn.setFixedHeight(44)
        self._continue_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._continue_btn.clicked.connect(self._on_continue)

        layout.addWidget(self._retry_btn)
        layout.addStretch()
        layout.addWidget(self._continue_btn)
        return row

    # ------------------------------------------------------------------ #
    # Transcription lifecycle                                               #
    # ------------------------------------------------------------------ #

    def _start_transcription(self, recording_path: str, provider=None) -> None:
        """Create a TranscriptionService and start the async worker."""
        if self._svc:
            self._svc.cancel()

        self._svc = TranscriptionService(provider=provider)

        # Connect worker signals before starting
        w = self._svc.worker  # None until start() — connect after start
        # We connect after start() using a helper slot

        # Monkey-patch: after start() the worker is created; connect immediately
        self._svc.start(
            session_id=self._session_id,
            audio_path=recording_path,
        )

        # Now worker exists — connect signals
        worker = self._svc.worker
        if worker:
            worker.progress_message.connect(self._on_progress_message)
            worker.transcript_ready.connect(self._on_transcript_ready)
            worker.error_occurred.connect(self._on_error)
            worker.status_changed.connect(self._on_status_changed)

    # ------------------------------------------------------------------ #
    # Slots                                                                 #
    # ------------------------------------------------------------------ #

    @Slot(str)
    def _on_progress_message(self, message: str) -> None:
        self._progress_detail.setText(message)

    @Slot(str)
    def _on_status_changed(self, status: str) -> None:
        logger.debug("Transcription status → %s", status)
        if status == TranscriptionStatus.PROCESSING:
            self._processing_title.setText("Analyzing your speech…")

    @Slot(object)
    def _on_transcript_ready(self, result: TranscriptResult) -> None:
        """Called from GUI thread when transcription completes."""
        logger.info(
            "Transcript ready: %d words, %d segments",
            result.word_count, len(result.segments),
        )
        self._transcript_result = result
        self._show_result_phase(result)

        # Emit to MainWindow so it can store transcript in DB
        transcript_text = result.to_plain_text()
        self.transcription_saved.emit(self._session_id, transcript_text)

        # Start async analysis immediately — don't wait for the user
        if result.has_content and self._session_id is not None:
            self._start_analysis(result)

    @Slot(str)
    def _on_error(self, error: str) -> None:
        """Called when transcription fails."""
        logger.warning("Transcription failed: %s", error)
        self._show_error_phase(error)

    def _on_retry(self) -> None:
        """Re-run transcription with the same audio file."""
        logger.info("Retrying transcription for session_id=%s", self._session_id)
        self._show_processing_phase()
        self._start_transcription(self._recording_path)

    def _on_continue(self) -> None:
        """User clicks Continue — skip/accept and go back to Practice."""
        if self._transcript_result and self._transcript_result.has_content:
            # Already emitted transcription_saved — just navigate
            self.transcription_skipped.emit(self._session_id)
        else:
            # No transcript — skip
            self.transcription_skipped.emit(self._session_id)

    def _on_toggle_timestamps(self, checked: bool) -> None:
        self._show_timestamps = checked
        self._toggle_timestamps_btn.setText(
            "🕐  Hide Timestamps" if checked else "🕐  Show Timestamps"
        )
        if self._transcript_result:
            self._update_transcript_text(self._transcript_result)

    # ------------------------------------------------------------------ #
    # Phase transitions                                                     #
    # ------------------------------------------------------------------ #

    def _show_processing_phase(self) -> None:
        self._processing_card.setVisible(True)
        self._transcript_card.setVisible(False)
        self._stats_row.setVisible(False)
        self._buttons_row.setVisible(False)
        self._progress_bar.setRange(0, 0)   # indeterminate spinner
        self._processing_title.setText("Analyzing your speech…")
        self._progress_detail.setText("")

    def _show_result_phase(self, result: TranscriptResult) -> None:
        self._processing_card.setVisible(False)

        if result.has_content:
            self._transcript_title.setText("Your Transcript")
            meta_parts = [f"{result.word_count} words"]
            if result.language:
                pct = int(result.language_probability * 100)
                meta_parts.append(f"{result.language} {pct}%")
            self._transcript_meta.setText(" • ".join(meta_parts))
            self._error_banner.setVisible(False)
            self._toggle_timestamps_btn.setVisible(True)
        else:
            self._transcript_title.setText("No Speech Detected")
            self._transcript_meta.setText("")
            self._error_banner.setText(
                "⚠️  No speech was detected in the recording. "
                "This may happen if the microphone was muted or the audio is too quiet. "
                "You can retry transcription or continue."
            )
            self._error_banner.setVisible(True)
            self._toggle_timestamps_btn.setVisible(False)

        self._update_transcript_text(result)
        self._update_stats(result)

        self._transcript_card.setVisible(True)
        self._stats_row.setVisible(True)
        self._buttons_row.setVisible(True)

    def _show_error_phase(self, error: str) -> None:
        self._processing_card.setVisible(False)

        self._transcript_title.setText("Transcription Failed")
        self._transcript_meta.setText("")
        self._transcript_text.setPlainText("")
        self._error_banner.setText(f"⚠️  {error}")
        self._error_banner.setVisible(True)
        self._toggle_timestamps_btn.setVisible(False)

        self._transcript_card.setVisible(True)
        self._stats_row.setVisible(False)
        self._buttons_row.setVisible(True)

    def _update_transcript_text(self, result: TranscriptResult) -> None:
        if self._show_timestamps and result.has_content:
            self._transcript_text.setPlainText(result.to_timestamped_text())
        elif result.has_content:
            self._transcript_text.setPlainText(result.to_plain_text())
        else:
            self._transcript_text.setPlainText("")

    def _update_stats(self, result: TranscriptResult) -> None:
        self._chip_words._value_label.setText(    # type: ignore[attr-defined]
            str(result.word_count) if result.has_content else "0"
        )
        mins, secs = divmod(int(result.duration), 60)
        self._chip_duration._value_label.setText(  # type: ignore[attr-defined]
            f"{mins:02d}:{secs:02d}"
        )
        self._chip_lang._value_label.setText(      # type: ignore[attr-defined]
            result.language.upper() if result.language else "—"
        )
        # Use the service's backend name if available
        backend = self._svc.backend_name if self._svc else result.model_size
        self._chip_backend._value_label.setText(   # type: ignore[attr-defined]
            backend or "—"
        )

    # ------------------------------------------------------------------ #
    # Helpers                                                               #
    # ------------------------------------------------------------------ #

    def _reset_ui(self) -> None:
        self._processing_card.setVisible(True)
        self._transcript_card.setVisible(False)
        self._stats_row.setVisible(False)
        self._buttons_row.setVisible(False)
        self._analysis_card.setVisible(False)
        self._transcript_text.setPlainText("")
        self._error_banner.setVisible(False)
        self._toggle_timestamps_btn.setChecked(False)
        self._show_timestamps = False

    # ------------------------------------------------------------------ #
    # Analysis card                                                         #
    # ------------------------------------------------------------------ #

    def _build_analysis_card(self) -> QFrame:
        """
        Build the analysis results card (hidden until analysis completes).

        Layout:
        ┌─ Your Speaking Analysis ─────────────────────────────────────┐
        │  ┌── Scores row ────────────────────────────────────────┐    │
        │  │  Overall 81  │  Fluency 85  │  Vocab 95  │  Gram 75 │    │
        │  └──────────────────────────────────────────────────────┘    │
        │  ┌── Second row ────────────────────────────────────────┐    │
        │  │  Confidence 85  │  Fillers 62  │  WPM 97             │    │
        │  └──────────────────────────────────────────────────────┘    │
        │  Analysis Spinner / "Calculating scores…"                    │
        │  ─────────────────────────────────────────────────────────   │
        │  Strengths: …                                                 │
        │  Areas to Improve: …                                          │
        │  Score breakdown (weight × sub-score): …                     │
        │  ⚠ Confidence caveat                                         │
        └───────────────────────────────────────────────────────────────┘
        """
        from PySide6.QtWidgets import QProgressBar  # already imported at top; re-import for clarity

        card = QFrame()
        card.setObjectName("AnalysisCard")

        outer = QVBoxLayout(card)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(16)

        # ── Title row ────────────────────────────────────────────────
        title_row = QHBoxLayout()
        title_lbl = QLabel("📊  Your Speaking Analysis")
        title_lbl.setObjectName("AnalysisTitle")
        self._analysis_status_lbl = QLabel("Calculating scores…")
        self._analysis_status_lbl.setObjectName("AnalysisStatus")
        self._analysis_status_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        title_row.addWidget(self._analysis_status_lbl)
        outer.addLayout(title_row)

        # ── Mini progress bar (indeterminate while running) ────────
        self._analysis_progress = QProgressBar()
        self._analysis_progress.setObjectName("AnalysisProgress")
        self._analysis_progress.setRange(0, 0)
        self._analysis_progress.setFixedHeight(4)
        self._analysis_progress.setTextVisible(False)
        outer.addWidget(self._analysis_progress)

        # ── Score chips — row 1: Overall, Fluency, Vocabulary, Grammar
        row1 = QHBoxLayout()
        row1.setSpacing(10)
        self._achip_overall  = self._make_analysis_chip("⭐", "Overall",    "—", big=True)
        self._achip_fluency  = self._make_analysis_chip("🗣", "Fluency",    "—")
        self._achip_vocab    = self._make_analysis_chip("📚", "Vocabulary", "—")
        self._achip_grammar  = self._make_analysis_chip("✏️", "Grammar",   "—")
        for w in [self._achip_overall, self._achip_fluency,
                  self._achip_vocab, self._achip_grammar]:
            row1.addWidget(w)
        row1.addStretch()
        outer.addLayout(row1)

        # ── Score chips — row 2: Confidence, Filler Score, WPM
        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self._achip_confidence = self._make_analysis_chip("💪", "Confidence", "—")
        self._achip_filler     = self._make_analysis_chip("🔇", "Filler",     "—")
        self._achip_wpm        = self._make_analysis_chip("⚡", "WPM",        "—")
        for w in [self._achip_confidence, self._achip_filler, self._achip_wpm]:
            row2.addWidget(w)
        row2.addStretch()
        outer.addLayout(row2)

        # ── Separator ────────────────────────────────────────────────
        sep = QFrame()
        sep.setObjectName("AnalysisSep")
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        outer.addWidget(sep)

        # ── Feedback text ─────────────────────────────────────────────
        self._feedback_text = QTextEdit()
        self._feedback_text.setObjectName("FeedbackText")
        self._feedback_text.setReadOnly(True)
        self._feedback_text.setMinimumHeight(160)
        self._feedback_text.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        outer.addWidget(self._feedback_text)

        # ── Score breakdown (collapsible toggle) ─────────────────────
        self._breakdown_btn = QPushButton("📊  Show Score Breakdown")
        self._breakdown_btn.setObjectName("BreakdownBtn")
        self._breakdown_btn.setFixedHeight(32)
        self._breakdown_btn.setCheckable(True)
        self._breakdown_btn.clicked.connect(self._on_toggle_breakdown)
        outer.addWidget(self._breakdown_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        self._breakdown_text = QTextEdit()
        self._breakdown_text.setObjectName("BreakdownText")
        self._breakdown_text.setReadOnly(True)
        self._breakdown_text.setFixedHeight(130)
        self._breakdown_text.setVisible(False)
        outer.addWidget(self._breakdown_text)

        # ── Confidence caveat ─────────────────────────────────────────
        self._confidence_caveat = QLabel("")
        self._confidence_caveat.setObjectName("ConfidenceCaveat")
        self._confidence_caveat.setWordWrap(True)
        self._confidence_caveat.setVisible(False)
        outer.addWidget(self._confidence_caveat)

        return card

    def _make_analysis_chip(
        self, icon: str, label: str, value: str, big: bool = False
    ) -> QFrame:
        chip = QFrame()
        chip.setObjectName("AnalysisChip")
        layout = QVBoxLayout(chip)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)

        hdr = QLabel(f"{icon}  {label}")
        hdr.setObjectName("AChipHeader")

        val = QLabel(value)
        val.setObjectName("AChipValueBig" if big else "AChipValue")

        layout.addWidget(hdr)
        layout.addWidget(val)
        chip._value_label = val        # type: ignore[attr-defined]
        chip._is_big      = big        # type: ignore[attr-defined]
        return chip

    # ------------------------------------------------------------------ #
    # Analysis lifecycle                                                    #
    # ------------------------------------------------------------------ #

    def _start_analysis(self, transcript_result: "TranscriptResult") -> None:
        """Start async analysis after transcription completes."""
        logger.info("Starting analysis for session_id=%s", self._session_id)

        # Show the card in "running" state before the worker fires
        self._analysis_card.setVisible(True)
        self._analysis_status_lbl.setText("Calculating scores…")
        self._analysis_progress.setRange(0, 0)   # indeterminate

        self._analysis_runner.cancel()
        self._analysis_runner.start(
            session_id=self._session_id,
            transcript=transcript_result.to_plain_text(),
            transcript_result=transcript_result,
            duration_seconds=int(transcript_result.duration),
        )

        worker = self._analysis_runner.worker
        if worker:
            worker.analysis_ready.connect(self._on_analysis_ready)
            worker.error_occurred.connect(self._on_analysis_error)
            worker.progress_message.connect(
                lambda msg: self._analysis_status_lbl.setText(msg)
            )

    @Slot(object)
    def _on_analysis_ready(self, result: dict) -> None:
        """Called when analysis finishes — populate the analysis card."""
        logger.info("Analysis ready for session_id=%s", self._session_id)
        self.show_analysis(result)

    @Slot(str)
    def _on_analysis_error(self, error: str) -> None:
        """Called when analysis fails — show a gentle error in the card."""
        logger.warning("Analysis error: %s", error)
        self._analysis_progress.setRange(0, 1)
        self._analysis_progress.setValue(1)
        self._analysis_status_lbl.setText("⚠️  Analysis could not complete")
        self._feedback_text.setPlainText(
            f"Analysis was unable to run:\n{error}\n\n"
            "Your transcript has been saved. You can still continue to Practice."
        )

    def show_analysis(self, result: dict) -> None:
        """
        Populate and reveal the analysis card.

        Args:
            result: Dict from SpeechAnalysisResult.to_ui_dict() — contains
                    all score keys, feedback lists, breakdown text, etc.

        This is a public method so MainWindow (or tests) can call it directly.
        """
        c = settings

        # Stop spinner
        self._analysis_progress.setRange(0, 1)
        self._analysis_progress.setValue(1)

        overall = result.get("overall_score", 0) or 0
        self._analysis_status_lbl.setText(f"Overall score: {overall:.0f} / 100")

        # ── Score chips ──────────────────────────────────────────────
        def _set(chip: QFrame, val, suffix: str = "") -> None:
            if val is None:
                chip._value_label.setText("—")  # type: ignore[attr-defined]
            else:
                chip._value_label.setText(f"{val:.0f}{suffix}")  # type: ignore[attr-defined]
            # Colour the chip by score
            if val is not None and not suffix == " WPM":
                colour = self._score_colour(float(val))
                chip._value_label.setStyleSheet(  # type: ignore[attr-defined]
                    f"color: {colour}; font-weight: 700;"
                )

        _set(self._achip_overall,    result.get("overall_score"))
        _set(self._achip_fluency,    result.get("fluency_score"))
        _set(self._achip_vocab,      result.get("vocabulary_score"))
        _set(self._achip_grammar,    result.get("grammar_score"))
        _set(self._achip_confidence, result.get("confidence_score"))

        filler_score = result.get("filler_score")
        # Filler score not always in db_dict — compute from filler_rate if absent
        if filler_score is None:
            fc = result.get("filler_word_count", 0) or 0
            tw = result.get("total_words", 100) or 100
            rate = fc / tw if tw else 0
            from speakup.services.analysis.filler_words import _score_from_filler_rate
            filler_score = _score_from_filler_rate(rate)
        _set(self._achip_filler, filler_score)

        wpm = result.get("words_per_minute", 0) or 0
        self._achip_wpm._value_label.setText(  # type: ignore[attr-defined]
            f"{wpm:.0f} WPM"
        )

        # ── Feedback ────────────────────────────────────────────────
        feedback_parts: list[str] = []
        strengths    = result.get("strengths", [])
        improvements = result.get("improvements", [])
        suggestions  = result.get("suggestions", [])

        if strengths:
            feedback_parts.append("✅  Strengths:\n" +
                "\n".join(f"  • {s}" for s in strengths))
        if improvements:
            feedback_parts.append("📌  Areas to Improve:\n" +
                "\n".join(f"  • {i}" for i in improvements))
        if suggestions:
            feedback_parts.append("💡  Suggestions:\n" +
                "\n".join(f"  • {s}" for s in suggestions))

        if not feedback_parts:
            # Fallback: use the pre-built feedback text from DB
            feedback_parts.append(result.get("feedback", "") or "")

        self._feedback_text.setPlainText("\n\n".join(feedback_parts))

        # ── Breakdown ────────────────────────────────────────────────
        breakdown = result.get("score_breakdown_text", "")
        if breakdown:
            self._breakdown_text.setPlainText(breakdown)
            self._breakdown_btn.setVisible(True)
        else:
            self._breakdown_btn.setVisible(False)

        # ── Confidence caveat ────────────────────────────────────────
        caveat = result.get("confidence_caveat", "")
        if caveat:
            self._confidence_caveat.setText(f"ℹ️  {caveat}")
            self._confidence_caveat.setVisible(True)

        self._analysis_card.setVisible(True)

    def _on_toggle_breakdown(self, checked: bool) -> None:
        self._breakdown_btn.setText(
            "📊  Hide Score Breakdown" if checked else "📊  Show Score Breakdown"
        )
        self._breakdown_text.setVisible(checked)

    @staticmethod
    def _score_colour(score: float) -> str:
        """Return a hex colour for a 0–100 score (green / amber / red)."""
        if score >= 75:
            return settings.COLOR_SUCCESS
        if score >= 50:
            return settings.COLOR_WARNING
        return settings.COLOR_DANGER

    def _populate_header(self, topic: dict) -> None:
        word = topic.get("word", "—")
        diff = topic.get("difficulty_display") or topic.get("difficulty") or ""
        cat  = topic.get("category") or ""
        parts = [p for p in [cat, diff] if p]
        subtitle = f"Topic: {word}"
        if parts:
            subtitle += f"  ({' · '.join(parts)})"
        self._header_topic.setText(subtitle)

    # ------------------------------------------------------------------ #
    # Styles                                                                #
    # ------------------------------------------------------------------ #

    def _apply_styles(self) -> None:
        c = settings
        self.setStyleSheet(f"""
            #TranscriptionResultPage {{
                background-color: {c.COLOR_BG_DARK};
            }}
            #TranscriptionScroll, #TranscriptionContent {{
                background-color: {c.COLOR_BG_DARK};
            }}
            #PageTitle {{
                font-size: 24px;
                font-weight: 700;
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #PageSubtitle {{
                font-size: 14px;
                color: {c.COLOR_TEXT_SECONDARY};
            }}

            /* Processing card */
            #ProcessingCard {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 14px;
            }}
            #ProcessingIcon {{
                font-size: 40px;
            }}
            #ProcessingTitle {{
                font-size: 20px;
                font-weight: 600;
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #ProgressDetail {{
                font-size: 13px;
                color: {c.COLOR_TEXT_MUTED};
            }}
            QProgressBar#TranscriptionProgress {{
                background-color: {c.COLOR_BORDER};
                border: none;
                border-radius: 3px;
            }}
            QProgressBar#TranscriptionProgress::chunk {{
                background-color: {c.COLOR_ACCENT};
                border-radius: 3px;
            }}

            /* Transcript card */
            #TranscriptCard {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 14px;
            }}
            #TranscriptTitle {{
                font-size: 16px;
                font-weight: 600;
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #TranscriptMeta {{
                font-size: 12px;
                color: {c.COLOR_TEXT_MUTED};
            }}
            #TranscriptSep {{
                background-color: {c.COLOR_BORDER};
                color: {c.COLOR_BORDER};
            }}
            #TranscriptText {{
                background-color: rgba(255,255,255,0.03);
                color: {c.COLOR_TEXT_PRIMARY};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 8px;
                padding: 12px;
                font-size: 14px;
                line-height: 1.7;
                selection-background-color: {c.COLOR_ACCENT};
            }}
            #ToggleTimestampsBtn {{
                background-color: transparent;
                color: {c.COLOR_ACCENT};
                border: 1px solid {c.COLOR_ACCENT};
                border-radius: 6px;
                padding: 4px 14px;
                font-size: 12px;
                font-weight: 500;
            }}
            #ToggleTimestampsBtn:hover {{
                background-color: rgba(108, 99, 255, 0.12);
            }}
            #ErrorBanner {{
                font-size: 13px;
                color: {c.COLOR_WARNING};
                background-color: rgba(255, 152, 0, 0.08);
                border: 1px solid rgba(255, 152, 0, 0.25);
                border-radius: 8px;
                padding: 10px 14px;
            }}

            /* Stats chips */
            #StatChip {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 8px;
            }}
            #ChipHeader {{
                font-size: 11px;
                font-weight: 500;
                color: {c.COLOR_TEXT_MUTED};
                text-transform: uppercase;
                letter-spacing: 0.6px;
            }}
            #ChipValue {{
                font-size: 14px;
                font-weight: 600;
                color: {c.COLOR_TEXT_PRIMARY};
            }}

            /* Buttons */
            #RetryBtn {{
                background-color: transparent;
                color: {c.COLOR_TEXT_SECONDARY};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 10px;
                font-size: 14px;
                font-weight: 500;
                padding: 0px 20px;
            }}
            #RetryBtn:hover {{
                color: {c.COLOR_TEXT_PRIMARY};
                border-color: {c.COLOR_TEXT_SECONDARY};
            }}
            #ContinueBtn {{
                background-color: {c.COLOR_ACCENT};
                color: #FFFFFF;
                border: none;
                border-radius: 10px;
                font-size: 14px;
                font-weight: 600;
                padding: 0px 28px;
            }}
            #ContinueBtn:hover {{
                background-color: {c.COLOR_ACCENT_HOVER};
            }}

            /* Analysis card */
            #AnalysisCard {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 14px;
            }}
            #AnalysisTitle {{
                font-size: 16px;
                font-weight: 600;
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #AnalysisStatus {{
                font-size: 12px;
                color: {c.COLOR_TEXT_MUTED};
            }}
            QProgressBar#AnalysisProgress {{
                background-color: {c.COLOR_BORDER};
                border: none;
                border-radius: 2px;
            }}
            QProgressBar#AnalysisProgress::chunk {{
                background-color: {c.COLOR_SUCCESS};
                border-radius: 2px;
            }}
            #AnalysisChip {{
                background-color: {c.COLOR_BG_DARK};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 8px;
            }}
            #AChipHeader {{
                font-size: 10px;
                font-weight: 500;
                color: {c.COLOR_TEXT_MUTED};
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }}
            #AChipValue {{
                font-size: 18px;
                font-weight: 700;
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #AChipValueBig {{
                font-size: 26px;
                font-weight: 700;
                color: {c.COLOR_SUCCESS};
            }}
            #AnalysisSep {{
                background-color: {c.COLOR_BORDER};
                color: {c.COLOR_BORDER};
            }}
            #FeedbackText {{
                background-color: rgba(255,255,255,0.02);
                color: {c.COLOR_TEXT_SECONDARY};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 8px;
                padding: 10px;
                font-size: 13px;
                line-height: 1.65;
                selection-background-color: {c.COLOR_ACCENT};
            }}
            #BreakdownBtn {{
                background-color: transparent;
                color: {c.COLOR_TEXT_MUTED};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 6px;
                padding: 2px 12px;
                font-size: 11px;
                font-weight: 500;
            }}
            #BreakdownBtn:hover {{
                color: {c.COLOR_TEXT_SECONDARY};
                border-color: {c.COLOR_TEXT_MUTED};
            }}
            #BreakdownText {{
                background-color: rgba(255,255,255,0.02);
                color: {c.COLOR_TEXT_MUTED};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 6px;
                padding: 8px;
                font-size: 12px;
                font-family: "Courier New", monospace;
            }}
            #ConfidenceCaveat {{
                font-size: 11px;
                color: {c.COLOR_TEXT_MUTED};
                background-color: rgba(255,255,255,0.02);
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 6px;
                padding: 8px 12px;
            }}
        """)
