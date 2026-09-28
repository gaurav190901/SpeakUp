"""
RecordingTimerWidget — elapsed time display with minimum-duration enforcement.

Displays a live "MM:SS" counter and a progress bar towards the 60-second
minimum speaking requirement.  The Stop Recording button is gated here:
the widget emits ``minimum_reached`` when the minimum duration passes, and
``stop_allowed`` to tell the page it can enable the stop button.

The widget is driven externally by connecting ``RecordingEngine.elapsed_changed``
to the ``update_elapsed`` slot — no internal QTimer is needed.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from speakup.config.settings import settings

logger = logging.getLogger(__name__)


class RecordingTimerWidget(QFrame):
    """
    Shows elapsed recording time and signals when the minimum duration
    (``settings.MIN_SPEAKING_DURATION``) has been reached.

    Signals
    -------
    minimum_reached()
        Emitted exactly once when elapsed >= MIN_SPEAKING_DURATION.
    stop_allowed(bool)
        Emitted when the stop-allowed state changes.
    """

    minimum_reached = Signal()
    stop_allowed    = Signal(bool)

    def __init__(
        self,
        min_duration: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TimerWidget")

        self._min_duration: int = min_duration or settings.MIN_SPEAKING_DURATION
        self._elapsed: int = 0
        self._minimum_was_reached: bool = False
        self._is_active: bool = False

        self._build_ui()
        self._apply_style()
        self.reset()

    # ------------------------------------------------------------------ #
    # UI construction                                                       #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(10)

        # ── Top row: red dot + elapsed + / minimum ──────────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._dot_label = QLabel("⏺")
        self._dot_label.setObjectName("RecordingDot")

        self._elapsed_label = QLabel("00:00")
        self._elapsed_label.setObjectName("ElapsedLabel")
        self._elapsed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._min_label = QLabel(
            f"/ {self._format(self._min_duration)} min required"
        )
        self._min_label.setObjectName("MinLabel")

        top_row.addWidget(self._dot_label)
        top_row.addWidget(self._elapsed_label)
        top_row.addWidget(self._min_label)
        outer.addLayout(top_row)

        # ── Progress bar ────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setObjectName("TimerProgress")
        self._progress.setMinimum(0)
        self._progress.setMaximum(self._min_duration)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        outer.addWidget(self._progress)

        # ── Status message ──────────────────────────────────────────────
        self._status_label = QLabel("Press Start Recording to begin")
        self._status_label.setObjectName("TimerStatus")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setWordWrap(True)
        outer.addWidget(self._status_label)

    def _apply_style(self) -> None:
        c = settings
        self.setStyleSheet(f"""
            #TimerWidget {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 12px;
            }}
            #RecordingDot {{
                font-size: 16px;
                color: {c.COLOR_DANGER};
            }}
            #ElapsedLabel {{
                font-size: 36px;
                font-weight: 700;
                color: {c.COLOR_TEXT_PRIMARY};
                font-family: "Courier New", monospace;
                letter-spacing: 2px;
            }}
            #MinLabel {{
                font-size: 13px;
                color: {c.COLOR_TEXT_MUTED};
            }}
            QProgressBar#TimerProgress {{
                background-color: {c.COLOR_BORDER};
                border: none;
                border-radius: 3px;
            }}
            QProgressBar#TimerProgress::chunk {{
                background-color: {c.COLOR_DANGER};
                border-radius: 3px;
            }}
            #TimerStatus {{
                font-size: 13px;
                color: {c.COLOR_TEXT_SECONDARY};
            }}
        """)

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    @Slot(int)
    def update_elapsed(self, seconds: int) -> None:
        """
        Update the timer display.  Connected to ``RecordingEngine.elapsed_changed``.
        """
        if not self._is_active:
            return

        self._elapsed = seconds
        self._elapsed_label.setText(self._format(seconds))
        self._progress.setValue(min(seconds, self._min_duration))

        # Check minimum reached (emit exactly once)
        if seconds >= self._min_duration and not self._minimum_was_reached:
            self._minimum_was_reached = True
            self._on_minimum_reached()

        # Update status text
        if seconds < self._min_duration:
            remaining = self._min_duration - seconds
            self._status_label.setText(
                f"{remaining}s remaining — keep speaking!"
            )
        else:
            over = seconds - self._min_duration
            extra = f"+{self._format(over)}" if over > 0 else ""
            self._status_label.setText(
                f"✅  Minimum reached{(' — ' + extra) if extra else ''} — you may stop now"
            )
            # Progress bar goes green after minimum
            self._progress.setStyleSheet(
                f"""
                QProgressBar#TimerProgress::chunk {{
                    background-color: {settings.COLOR_SUCCESS};
                    border-radius: 3px;
                }}
                """
            )
            self._progress.setValue(self._min_duration)

    def start(self) -> None:
        """Activate the timer (begin accepting elapsed updates)."""
        self._is_active = True
        self._minimum_was_reached = False
        self._elapsed = 0
        self._elapsed_label.setText("00:00")
        self._progress.setValue(0)
        self._dot_label.setVisible(True)
        self._status_label.setText(
            f"🎤  Speak for at least {self._min_duration}s — keep going!"
        )
        # Reset progress bar colour to red
        self._apply_style()

    def stop(self) -> None:
        """Deactivate the timer."""
        self._is_active = False
        self._dot_label.setVisible(False)

    def reset(self) -> None:
        """Reset to initial idle state."""
        self._is_active = False
        self._elapsed = 0
        self._minimum_was_reached = False
        self._elapsed_label.setText("00:00")
        self._progress.setValue(0)
        self._dot_label.setVisible(False)
        self._status_label.setText("Press Start Recording to begin")
        self._apply_style()

    @property
    def elapsed(self) -> int:
        return self._elapsed

    @property
    def minimum_reached_flag(self) -> bool:
        return self._minimum_was_reached

    @property
    def can_stop(self) -> bool:
        return self._minimum_was_reached

    # ------------------------------------------------------------------ #
    # Internal                                                              #
    # ------------------------------------------------------------------ #

    def _on_minimum_reached(self) -> None:
        logger.info("Minimum recording duration (%ds) reached", self._min_duration)
        self.minimum_reached.emit()
        self.stop_allowed.emit(True)

    @staticmethod
    def _format(seconds: int) -> str:
        """Format *seconds* as MM:SS."""
        m, s = divmod(max(0, seconds), 60)
        return f"{m:02d}:{s:02d}"
