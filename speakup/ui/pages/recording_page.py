"""
RecordingPage — the active practice recording screen.

Layout
──────
┌─ Header: topic name + "Recording Session" ──────────────────────────────┐
│                                                                           │
│  ┌─ Left column (60%) ─────────────┐  ┌─ Right column (40%) ───────────┐ │
│  │                                  │  │                                 │ │
│  │   [CameraPreviewWidget]          │  │  Topic card (word + meta)       │ │
│  │                                  │  │                                 │ │
│  │   🎤  Mic: Active  ●●●          │  │  [RecordingTimerWidget]         │ │
│  │                                  │  │                                 │ │
│  └──────────────────────────────────┘  │  [Start Recording]              │ │
│                                        │  [Stop Recording] (gated)       │ │
│                                        │  [Abandon Session]              │ │
│                                        │                                 │ │
│                                        └─────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────┘

Recording states (mirrors RecordingState):
    IDLE         — page just loaded, camera initialising
    INITIALISING — opening camera / mic
    READY        — camera live, waiting for user to click Start Recording
    COUNTDOWN    — 3-2-1 countdown (visual only)
    RECORDING    — capture running, timer ticking
    STOPPING     — finalising file
    COMPLETED    — file saved, session updated
    ERROR        — camera / mic / file error
    ABANDONED    — user clicked Abandon

Signals
───────
session_completed(session_id: int, duration: int, recording_path: str)
    Emitted when the recording is saved and the practice session should be
    completed.  MainWindow (or PracticePage) listens and calls
    word_service.complete_practice().

session_abandoned(session_id: int)
    Emitted when the user abandons so the caller can call
    word_service.abandon_practice().
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from speakup.config.settings import settings
from speakup.services.recording_service import RecordingService, RecordingState
from speakup.ui.widgets.camera_preview import CameraPreviewWidget
from speakup.ui.widgets.recording_timer import RecordingTimerWidget

logger = logging.getLogger(__name__)

_MIN_DURATION = settings.MIN_SPEAKING_DURATION  # default 60 s


# ---------------------------------------------------------------------------
# RecordingPage
# ---------------------------------------------------------------------------

class RecordingPage(QWidget):
    """
    Full-screen recording page launched from PracticePage.

    Parameters
    ----------
    parent : QWidget | None
        Parent widget (typically MainWindow).

    Lifecycle
    ---------
    1.  ``prepare(topic, session_id)`` — called by MainWindow before navigating here.
    2.  Camera initialises in background → READY state.
    3.  User clicks Start Recording → COUNTDOWN → RECORDING.
    4.  Timer runs; Stop Recording is disabled for first 60 s.
    5.  User clicks Stop → STOPPING → COMPLETED.
    6.  ``session_completed`` signal fires with path + duration.
    7.  ``session_abandoned`` fires if user clicks Abandon at any point.
    """

    session_completed = Signal(int, int, str)   # (session_id, duration_s, path)
    session_abandoned = Signal(int)              # (session_id,)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RecordingPage")

        self._session_id:  int | None = None
        self._topic:       dict | None = None
        self._recording_svc: RecordingService = RecordingService()
        self._ui_state: str = RecordingState.IDLE

        # Countdown
        self._countdown_value: int = 3
        self._countdown_timer: QTimer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._on_countdown_tick)

        self._build_ui()
        self._apply_styles()

    # ------------------------------------------------------------------ #
    # Public API called by MainWindow                                       #
    # ------------------------------------------------------------------ #

    def prepare(self, topic: dict, session_id: int) -> None:
        """
        Set the topic and session, reset all state, start camera init.

        Called by MainWindow just before navigating to this page.
        """
        self._topic = topic
        self._session_id = session_id

        logger.info(
            "RecordingPage.prepare  session_id=%s  word=%r",
            session_id, topic.get("word"),
        )

        self._reset_ui()
        self._populate_topic(topic)
        self._set_ui_state(RecordingState.INITIALISING)
        self._initialise_recording()

    def cleanup(self) -> None:
        """
        Release hardware resources.  Called when navigating away.
        """
        self._countdown_timer.stop()
        self._recording_svc.release()
        logger.debug("RecordingPage.cleanup complete")

    # ------------------------------------------------------------------ #
    # UI construction                                                       #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        main = QVBoxLayout(self)
        main.setContentsMargins(28, 24, 28, 24)
        main.setSpacing(16)

        # ── Header ────────────────────────────────────────────────────────
        self._build_header(main)

        # ── Two-column body ───────────────────────────────────────────────
        body = QHBoxLayout()
        body.setSpacing(20)

        # Left: camera + mic status
        left_col = self._build_left_column()
        body.addWidget(left_col, stretch=6)

        # Right: topic card + timer + buttons
        right_col = self._build_right_column()
        body.addWidget(right_col, stretch=4)

        main.addLayout(body)

        # ── Error / status bar ────────────────────────────────────────────
        self._status_bar = QLabel("")
        self._status_bar.setObjectName("StatusBar")
        self._status_bar.setWordWrap(True)
        self._status_bar.setVisible(False)
        main.addWidget(self._status_bar)

    def _build_header(self, parent_layout: QVBoxLayout) -> None:
        header = QWidget()
        header.setObjectName("RecordingHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)

        self._header_title = QLabel("Recording Session")
        self._header_title.setObjectName("RecordingHeaderTitle")

        self._header_word = QLabel("—")
        self._header_word.setObjectName("RecordingHeaderWord")
        self._header_word.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        row.addWidget(self._header_title)
        row.addStretch()
        row.addWidget(self._header_word)
        parent_layout.addWidget(header)

    def _build_left_column(self) -> QWidget:
        col = QWidget()
        col.setObjectName("LeftColumn")
        layout = QVBoxLayout(col)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Camera preview
        self._camera = CameraPreviewWidget()
        self._camera.setMinimumHeight(300)
        layout.addWidget(self._camera, stretch=1)

        # Microphone status bar
        self._mic_bar = self._build_mic_bar()
        layout.addWidget(self._mic_bar)

        return col

    def _build_mic_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("MicBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(10)

        mic_icon = QLabel("🎤")
        mic_icon.setObjectName("MicIcon")

        self._mic_status_label = QLabel("Microphone: Checking…")
        self._mic_status_label.setObjectName("MicStatusLabel")

        # Level dots
        self._mic_dots = []
        dot_row = QHBoxLayout()
        dot_row.setSpacing(4)
        for _ in range(5):
            dot = QLabel("●")
            dot.setObjectName("MicDotOff")
            dot_row.addWidget(dot)
            self._mic_dots.append(dot)

        row.addWidget(mic_icon)
        row.addWidget(self._mic_status_label)
        row.addStretch()
        row.addLayout(dot_row)
        return bar

    def _build_right_column(self) -> QWidget:
        col = QWidget()
        col.setObjectName("RightColumn")
        layout = QVBoxLayout(col)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # Topic card
        self._topic_card = self._build_topic_card()
        layout.addWidget(self._topic_card)

        # Timer
        self._timer_widget = RecordingTimerWidget(min_duration=_MIN_DURATION)
        self._timer_widget.minimum_reached.connect(self._on_minimum_reached)
        self._timer_widget.stop_allowed.connect(self._on_stop_allowed)
        layout.addWidget(self._timer_widget)

        # Countdown overlay (shown over timer during countdown)
        self._countdown_label = QLabel("3")
        self._countdown_label.setObjectName("CountdownLabel")
        self._countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._countdown_label.setVisible(False)
        layout.addWidget(self._countdown_label)

        # Buttons
        btn_col = QVBoxLayout()
        btn_col.setSpacing(10)

        self._start_btn = QPushButton("🔴  Start Recording")
        self._start_btn.setObjectName("StartRecordingBtn")
        self._start_btn.setFixedHeight(48)
        self._start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start_btn.setEnabled(False)   # enabled when READY
        self._start_btn.clicked.connect(self._on_start_recording)

        self._stop_btn = QPushButton("⏹  Stop Recording")
        self._stop_btn.setObjectName("StopRecordingBtn")
        self._stop_btn.setFixedHeight(48)
        self._stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_btn.setEnabled(False)    # enabled after 60 s
        self._stop_btn.setToolTip(
            f"You need to speak for at least {_MIN_DURATION}s"
        )
        self._stop_btn.clicked.connect(self._on_stop_recording)

        self._abandon_btn = QPushButton("✕  Abandon Session")
        self._abandon_btn.setObjectName("AbandonBtn")
        self._abandon_btn.setFixedHeight(40)
        self._abandon_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._abandon_btn.clicked.connect(self._on_abandon)

        btn_col.addWidget(self._start_btn)
        btn_col.addWidget(self._stop_btn)
        btn_col.addWidget(self._abandon_btn)
        layout.addLayout(btn_col)
        layout.addStretch()

        return col

    def _build_topic_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("RecTopicCard")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        your_topic = QLabel("Your Topic:")
        your_topic.setObjectName("YourTopicMini")

        self._topic_word_label = QLabel("—")
        self._topic_word_label.setObjectName("TopicWordMini")
        self._topic_word_label.setWordWrap(True)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(16)

        cat_col = QVBoxLayout()
        cat_col.setSpacing(1)
        cat_key = QLabel("Category:")
        cat_key.setObjectName("MetaKeyMini")
        self._cat_label = QLabel("—")
        self._cat_label.setObjectName("MetaValueMini")
        cat_col.addWidget(cat_key)
        cat_col.addWidget(self._cat_label)

        diff_col = QVBoxLayout()
        diff_col.setSpacing(1)
        diff_key = QLabel("Difficulty:")
        diff_key.setObjectName("MetaKeyMini")
        self._diff_label = QLabel("—")
        self._diff_label.setObjectName("MetaValueMini")
        diff_col.addWidget(diff_key)
        diff_col.addWidget(self._diff_label)

        meta_row.addLayout(cat_col)
        meta_row.addLayout(diff_col)
        meta_row.addStretch()

        layout.addWidget(your_topic)
        layout.addWidget(self._topic_word_label)
        layout.addLayout(meta_row)

        return card

    # ------------------------------------------------------------------ #
    # Recording lifecycle                                                   #
    # ------------------------------------------------------------------ #

    def _initialise_recording(self) -> None:
        """Start the recording engine (camera + mic probe)."""
        # Check hardware probes first
        if not self._recording_svc.probe_camera():
            self._on_error("Camera not found or permission denied.")
            return
        if not self._recording_svc.probe_microphone():
            self._on_error("No microphone found or permission denied.")
            return

        # Update mic status
        self._mic_status_label.setText("Microphone: Active")

        # Connect engine signals
        engine = None
        self._recording_svc.initialise(session_id=self._session_id)
        engine = self._recording_svc.engine

        if engine:
            engine.frame_ready.connect(self._camera.update_frame)
            engine.elapsed_changed.connect(self._timer_widget.update_elapsed)
            engine.state_changed.connect(self._on_engine_state_changed)
            engine.error_occurred.connect(self._on_error)
            engine.recording_finished.connect(self._on_recording_finished)

    @Slot(str)
    def _on_engine_state_changed(self, state: str) -> None:
        """Relay engine state to UI state machine."""
        logger.debug("RecordingPage engine state → %s", state)

        if state == RecordingState.READY:
            self._set_ui_state(RecordingState.READY)
            self._start_btn.setEnabled(True)
            self._camera.show_placeholder("Camera ready — press Start Recording")

        elif state == RecordingState.RECORDING:
            self._set_ui_state(RecordingState.RECORDING)

        elif state == RecordingState.STOPPING:
            self._set_ui_state(RecordingState.STOPPING)
            self._start_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            self._abandon_btn.setEnabled(False)
            self._show_status("Saving recording…", colour=settings.COLOR_WARNING)

        elif state == RecordingState.COMPLETED:
            self._set_ui_state(RecordingState.COMPLETED)

        elif state == RecordingState.ERROR:
            self._set_ui_state(RecordingState.ERROR)

        elif state == RecordingState.ABANDONED:
            self._set_ui_state(RecordingState.ABANDONED)

    def _on_start_recording(self) -> None:
        """User clicked Start Recording — run countdown then begin capture."""
        if self._ui_state not in (RecordingState.READY,):
            return
        self._set_ui_state(RecordingState.COUNTDOWN)
        self._start_btn.setEnabled(False)
        self._abandon_btn.setEnabled(False)
        self._countdown_value = 3
        self._countdown_label.setText(str(self._countdown_value))
        self._countdown_label.setVisible(True)
        self._timer_widget.setVisible(False)
        self._countdown_timer.start()

    @Slot()
    def _on_countdown_tick(self) -> None:
        self._countdown_value -= 1
        if self._countdown_value > 0:
            self._countdown_label.setText(str(self._countdown_value))
        else:
            self._countdown_timer.stop()
            self._countdown_label.setVisible(False)
            self._timer_widget.setVisible(True)
            self._begin_capture()

    def _begin_capture(self) -> None:
        """Actually start the recording engine."""
        self._timer_widget.start()
        self._recording_svc.start_recording()
        self._abandon_btn.setEnabled(True)
        self._show_status(
            f"🔴  Recording — speak for at least {_MIN_DURATION}s",
            colour=settings.COLOR_DANGER,
        )
        logger.info("Recording begun for session_id=%s", self._session_id)

    def _on_stop_recording(self) -> None:
        """User clicked Stop Recording — enforce minimum duration."""
        if self._ui_state != RecordingState.RECORDING:
            return

        if not self._timer_widget.can_stop:
            remaining = _MIN_DURATION - self._timer_widget.elapsed
            self._show_status(
                f"⚠️  You need to speak for at least 1 minute. "
                f"{remaining}s remaining.",
                colour=settings.COLOR_WARNING,
            )
            logger.info(
                "Stop blocked: only %ds recorded (minimum=%ds)",
                self._timer_widget.elapsed, _MIN_DURATION,
            )
            return

        self._stop_btn.setEnabled(False)
        self._timer_widget.stop()
        self._recording_svc.stop_recording()
        self._show_status("Saving recording…", colour=settings.COLOR_WARNING)

    @Slot(str)
    def _on_recording_finished(self, path: str) -> None:
        """Engine emits this when the file is written."""
        duration = self._timer_widget.elapsed
        logger.info(
            "Recording finished: path=%s  duration=%ds  session=%s",
            path, duration, self._session_id,
        )
        self._timer_widget.stop()
        self._camera.show_hidden()
        self._show_status(
            f"✅  Recording saved ({duration}s). Completing session…",
            colour=settings.COLOR_SUCCESS,
        )
        # Emit so MainWindow / PracticePage can call complete_practice
        self.session_completed.emit(self._session_id, duration, path)

    def _on_abandon(self) -> None:
        """User clicked Abandon — clean up and signal caller."""
        logger.info("Session abandoned by user. session_id=%s", self._session_id)
        self._countdown_timer.stop()
        self._timer_widget.stop()
        sid = self._session_id
        self._recording_svc.abandon()
        self._set_ui_state(RecordingState.ABANDONED)
        self.session_abandoned.emit(sid)

    @Slot(str)
    def _on_error(self, message: str) -> None:
        """Engine or probe error."""
        logger.error("RecordingPage error: %s", message)
        self._set_ui_state(RecordingState.ERROR)
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._camera.show_error(message)
        self._show_status(f"⚠️  {message}", colour=settings.COLOR_DANGER)

    @Slot()
    def _on_minimum_reached(self) -> None:
        """Timer widget signals minimum duration has passed."""
        self._show_status(
            "✅  Minimum reached — you can stop now or keep speaking!",
            colour=settings.COLOR_SUCCESS,
        )

    @Slot(bool)
    def _on_stop_allowed(self, allowed: bool) -> None:
        """Enable Stop Recording once minimum is reached."""
        self._stop_btn.setEnabled(allowed)
        if allowed:
            self._stop_btn.setToolTip("")

    # ------------------------------------------------------------------ #
    # UI state helpers                                                      #
    # ------------------------------------------------------------------ #

    def _set_ui_state(self, state: str) -> None:
        self._ui_state = state
        logger.debug("RecordingPage UI state → %s", state)

    def _populate_topic(self, topic: dict) -> None:
        word = topic.get("word", "—")
        self._header_word.setText(word)
        self._topic_word_label.setText(word)
        self._cat_label.setText(topic.get("category") or "—")
        self._diff_label.setText(topic.get("difficulty_display") or
                                  topic.get("difficulty") or "—")

    def _reset_ui(self) -> None:
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._stop_btn.setToolTip(
            f"You need to speak for at least {_MIN_DURATION}s"
        )
        self._abandon_btn.setEnabled(True)
        self._countdown_label.setVisible(False)
        self._timer_widget.setVisible(True)
        self._timer_widget.reset()
        self._camera.show_placeholder("Initialising camera…")
        self._mic_status_label.setText("Microphone: Checking…")
        self._hide_status()

    def _show_status(self, message: str, colour: str = "") -> None:
        self._status_bar.setText(message)
        colour = colour or settings.COLOR_TEXT_SECONDARY
        self._status_bar.setStyleSheet(
            f"QLabel {{ color: {colour}; font-size: 13px; padding: 6px 0; }}"
        )
        self._status_bar.setVisible(True)

    def _hide_status(self) -> None:
        self._status_bar.setVisible(False)

    # ------------------------------------------------------------------ #
    # Styles                                                                #
    # ------------------------------------------------------------------ #

    def _apply_styles(self) -> None:
        c = settings
        self.setStyleSheet(f"""
            #RecordingPage {{
                background-color: {c.COLOR_BG_DARK};
            }}

            /* Header */
            #RecordingHeader {{
                background-color: transparent;
            }}
            #RecordingHeaderTitle {{
                font-size: 20px;
                font-weight: 700;
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #RecordingHeaderWord {{
                font-size: 16px;
                font-weight: 600;
                color: {c.COLOR_ACCENT};
            }}

            /* Mic bar */
            #MicBar {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 8px;
            }}
            #MicIcon {{ font-size: 16px; }}
            #MicStatusLabel {{
                font-size: 13px;
                font-weight: 500;
                color: {c.COLOR_TEXT_SECONDARY};
            }}
            #MicDotOff  {{ font-size: 12px; color: {c.COLOR_BORDER};   }}
            #MicDotOn   {{ font-size: 12px; color: {c.COLOR_SUCCESS};  }}

            /* Topic card */
            #RecTopicCard {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 12px;
            }}
            #YourTopicMini {{
                font-size: 11px;
                font-weight: 500;
                color: {c.COLOR_TEXT_MUTED};
                text-transform: uppercase;
                letter-spacing: 0.8px;
            }}
            #TopicWordMini {{
                font-size: 22px;
                font-weight: 700;
                color: {c.COLOR_TEXT_PRIMARY};
                line-height: 1.3;
            }}
            #MetaKeyMini {{
                font-size: 10px;
                font-weight: 500;
                color: {c.COLOR_TEXT_MUTED};
                text-transform: uppercase;
            }}
            #MetaValueMini {{
                font-size: 13px;
                font-weight: 600;
                color: {c.COLOR_TEXT_PRIMARY};
            }}

            /* Countdown overlay */
            #CountdownLabel {{
                font-size: 72px;
                font-weight: 700;
                color: {c.COLOR_DANGER};
                background-color: transparent;
            }}

            /* Buttons */
            #StartRecordingBtn {{
                background-color: {c.COLOR_DANGER};
                color: #FFFFFF;
                border: none;
                border-radius: 10px;
                font-size: 15px;
                font-weight: 600;
            }}
            #StartRecordingBtn:hover {{
                background-color: #e53935;
            }}
            #StartRecordingBtn:disabled {{
                background-color: {c.COLOR_BORDER};
                color: {c.COLOR_TEXT_MUTED};
            }}

            #StopRecordingBtn {{
                background-color: {c.COLOR_BG_CARD};
                color: {c.COLOR_TEXT_PRIMARY};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 10px;
                font-size: 15px;
                font-weight: 600;
            }}
            #StopRecordingBtn:hover {{
                border-color: {c.COLOR_TEXT_SECONDARY};
            }}
            #StopRecordingBtn:enabled {{
                background-color: {c.COLOR_SUCCESS};
                color: #FFFFFF;
                border: none;
            }}
            #StopRecordingBtn:enabled:hover {{
                background-color: #43a047;
            }}
            #StopRecordingBtn:disabled {{
                background-color: {c.COLOR_BORDER};
                color: {c.COLOR_TEXT_MUTED};
            }}

            #AbandonBtn {{
                background-color: transparent;
                color: {c.COLOR_TEXT_MUTED};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 8px;
                font-size: 13px;
            }}
            #AbandonBtn:hover {{
                color: {c.COLOR_DANGER};
                border-color: {c.COLOR_DANGER};
            }}
            #AbandonBtn:disabled {{
                color: {c.COLOR_TEXT_MUTED};
                border-color: {c.COLOR_BORDER};
            }}
        """)
