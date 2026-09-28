"""
speakup/ui/widgets/onboarding.py
──────────────────────────────────
First-run welcome dialog.

Shows a 5-step workflow overview the first time the application launches.
The "seen" flag is stored as a JSON file in the project root so it
survives application updates but not complete data deletion.

Usage (called from app.py after MainWindow.show()):
    from speakup.ui.widgets.onboarding import maybe_show_onboarding
    maybe_show_onboarding(parent=window)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from speakup.config.settings import settings

logger = logging.getLogger(__name__)

_FLAG_FILE = settings.PROJECT_ROOT / ".speakup_onboarded"

_STEPS = [
    ("🎯", "Choose a Topic",
     "SpeakUp gives you a random English topic to speak about — "
     "from Leadership to Artificial Intelligence. "
     "Use the category and difficulty filters to match your level."),
    ("🎤", "Record Yourself",
     "Press Start Practice to begin recording. "
     "Speak for at least 1 minute. "
     "Your camera and microphone capture the session."),
    ("📝", "Get a Transcript",
     "Your speech is transcribed automatically using a local AI model. "
     "No audio leaves your device."),
    ("📊", "See Your Scores",
     "SpeakUp analyses your fluency, vocabulary, grammar, and confidence. "
     "Every metric is calculated from your actual speech."),
    ("🏆", "Track Progress",
     "The Dashboard shows your improvement over time — "
     "charts, streaks, achievements, and today's practice goal. "
     "Practise every day and watch your scores rise."),
]


class OnboardingDialog(QDialog):
    """A simple 5-step first-run tutorial dialog."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Welcome to {settings.APP_NAME}")
        self.setModal(True)
        self.setMinimumWidth(540)
        self.setMaximumWidth(600)
        self._step = 0
        self._build_ui()
        self._apply_styles()
        self._show_step(0)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 32, 36, 28)
        layout.setSpacing(20)

        # Title
        title = QLabel(f"Welcome to {settings.APP_NAME}!")
        title.setObjectName("OnboardTitle")
        layout.addWidget(title)

        subtitle = QLabel("Here's how it works — just 5 steps.")
        subtitle.setObjectName("OnboardSubtitle")
        layout.addWidget(subtitle)

        # Step card
        self._step_card = QFrame()
        self._step_card.setObjectName("StepCard")
        step_layout = QVBoxLayout(self._step_card)
        step_layout.setContentsMargins(24, 20, 24, 20)
        step_layout.setSpacing(12)

        icon_label = QLabel("")
        icon_label.setObjectName("StepIcon")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._step_title = QLabel("")
        self._step_title.setObjectName("StepTitle")
        self._step_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._step_body = QLabel("")
        self._step_body.setObjectName("StepBody")
        self._step_body.setWordWrap(True)
        self._step_body.setAlignment(Qt.AlignmentFlag.AlignCenter)

        step_layout.addWidget(icon_label)
        self._icon_label = icon_label
        step_layout.addWidget(self._step_title)
        step_layout.addWidget(self._step_body)
        layout.addWidget(self._step_card)

        # Step dots
        dots_row = QHBoxLayout()
        dots_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dots_row.setSpacing(8)
        self._dots: list[QLabel] = []
        for _ in _STEPS:
            dot = QLabel("●")
            dot.setObjectName("DotInactive")
            dots_row.addWidget(dot)
            self._dots.append(dot)
        layout.addLayout(dots_row)

        # Navigation buttons
        btn_row = QHBoxLayout()
        self._back_btn = QPushButton("← Back")
        self._back_btn.setObjectName("OnboardSecBtn")
        self._back_btn.setFixedHeight(40)
        self._back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.clicked.connect(self._on_back)
        self._back_btn.setVisible(False)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setObjectName("OnboardPrimBtn")
        self._next_btn.setFixedHeight(40)
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.clicked.connect(self._on_next)

        btn_row.addWidget(self._back_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._next_btn)
        layout.addLayout(btn_row)

    def _show_step(self, index: int) -> None:
        icon, title, body = _STEPS[index]
        self._icon_label.setText(icon)
        self._step_title.setText(title)
        self._step_body.setText(body)
        self._step = index

        for i, dot in enumerate(self._dots):
            dot.setObjectName("DotActive" if i == index else "DotInactive")
            dot.style().unpolish(dot)
            dot.style().polish(dot)

        self._back_btn.setVisible(index > 0)
        is_last = index == len(_STEPS) - 1
        self._next_btn.setText("Get Started!" if is_last else "Next →")

    def _on_next(self) -> None:
        if self._step < len(_STEPS) - 1:
            self._show_step(self._step + 1)
        else:
            self.accept()

    def _on_back(self) -> None:
        if self._step > 0:
            self._show_step(self._step - 1)

    def _apply_styles(self) -> None:
        c = settings
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {c.COLOR_BG_DARK};
            }}
            #OnboardTitle {{
                font-size: 22px; font-weight: 700;
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #OnboardSubtitle {{
                font-size: 14px; color: {c.COLOR_TEXT_SECONDARY};
            }}
            #StepCard {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 14px;
                min-height: 200px;
            }}
            #StepIcon {{ font-size: 40px; }}
            #StepTitle {{
                font-size: 18px; font-weight: 700;
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #StepBody {{
                font-size: 13px; color: {c.COLOR_TEXT_SECONDARY};
                line-height: 1.6;
            }}
            #DotActive   {{ font-size: 14px; color: {c.COLOR_ACCENT}; }}
            #DotInactive {{ font-size: 14px; color: {c.COLOR_BORDER}; }}
            #OnboardPrimBtn {{
                background-color: {c.COLOR_ACCENT};
                color: #FFFFFF; border: none;
                border-radius: 8px; font-size: 14px; font-weight: 600;
                padding: 0px 24px;
            }}
            #OnboardPrimBtn:hover {{ background-color: {c.COLOR_ACCENT_HOVER}; }}
            #OnboardSecBtn {{
                background-color: transparent;
                color: {c.COLOR_TEXT_SECONDARY};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 8px; font-size: 13px;
                padding: 0px 18px;
            }}
            #OnboardSecBtn:hover {{ color: {c.COLOR_TEXT_PRIMARY}; }}
        """)


def maybe_show_onboarding(parent: QWidget | None = None) -> None:
    """Show the onboarding dialog if this is the first launch."""
    if _FLAG_FILE.exists():
        return
    try:
        dialog = OnboardingDialog(parent=parent)
        dialog.exec()
        _FLAG_FILE.write_text(json.dumps({"seen": True}), encoding="utf-8")
        logger.info("First-run onboarding completed.")
    except Exception as exc:
        logger.warning("Could not show onboarding: %s", exc)


def reset_onboarding() -> None:
    """Remove the seen flag so onboarding shows again (for testing/reset)."""
    _FLAG_FILE.unlink(missing_ok=True)
