"""
PracticePage — topic assignment and session management UI.

Layout
──────────────
┌─ Page header ───────────────────────────────────────────────────────┐
│  "Today's Practice"                                                  │
│  "Build your English speaking confidence"                            │
└──────────────────────────────────────────────────────────────────────┘

┌─ Filter bar ─────────────────────────────────────────────────────────┐
│  [All Categories ▾]   [All Levels ▾]   [🔀 New Topic]               │
└──────────────────────────────────────────────────────────────────────┘

┌─ Topic card ─────────────────────────────────────────────────────────┐
│  "Your Topic:"                                                        │
│                                                                       │
│        ╔═══════════════════════════════════════╗                      │
│        ║   Artificial Intelligence             ║   ← big word        │
│        ╚═══════════════════════════════════════╝                      │
│                                                                       │
│  Category:  Technology      Difficulty:  Intermediate                │
│                                                                       │
│  [description text…]                                                  │
│                                                                       │
│  ℹ  Speak about this topic in English for at least 1 minute.        │
│                                                                       │
│           [ 🎤  Start Practice ]                                     │
└──────────────────────────────────────────────────────────────────────┘

┌─ Info chips ─────────────────────────────────────────────────────────┐
│  📚 Topics Remaining  │  ⏱ Min Duration  │  🎯 Format               │
└──────────────────────────────────────────────────────────────────────┘

┌─ Tips card ──────────────────────────────────────────────────────────┐
│  • tips …                                                             │
└──────────────────────────────────────────────────────────────────────┘

State machine
─────────────
    IDLE          → no topic loaded yet / loading
    TOPIC_LOADED  → PENDING session exists, topic displayed
    EXHAUSTED     → no unused words remain for current filters
"""

from __future__ import annotations

import logging
import random

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from speakup.config.settings import settings
from speakup.data.words.loader import (
    DIFFICULTY_HINTS,
    DIFFICULTY_ICONS,
    category_with_icon,
    format_hints_html,
    get_topic_hints,
)
from speakup.services.word_service import WordService, POOL_EXHAUSTED_MESSAGE

logger = logging.getLogger(__name__)


def _difficulty_with_icon(label: str | None) -> str:
    if not label:
        return "—"
    icon = DIFFICULTY_ICONS.get(label)
    return f"{icon}  {label}" if icon else label

# ── UI page states ──────────────────────────────────────────────────────────
_STATE_IDLE         = "idle"
_STATE_TOPIC_LOADED = "topic_loaded"
_STATE_EXHAUSTED    = "exhausted"

# ---------------------------------------------------------------------------
# Small reusable sub-widgets
# ---------------------------------------------------------------------------

class _FilterBar(QWidget):
    """Category + difficulty combo boxes and New Topic button."""

    new_topic_requested = Signal()

    def __init__(self, word_service: WordService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ws = word_service
        self._build()

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Category combo
        self._cat_combo = QComboBox()
        self._cat_combo.setObjectName("FilterCombo")
        self._cat_combo.setFixedHeight(38)
        self._cat_combo.setMinimumWidth(180)
        self._cat_combo.addItem("🗂️  All Categories", None)
        for cat in self._ws.get_categories():
            self._cat_combo.addItem(category_with_icon(cat), cat)

        # Difficulty combo
        self._diff_combo = QComboBox()
        self._diff_combo.setObjectName("FilterCombo")
        self._diff_combo.setFixedHeight(38)
        self._diff_combo.setMinimumWidth(150)
        self._diff_combo.addItem("All Levels", None)
        for label in self._ws.get_difficulty_labels():
            self._diff_combo.addItem(_difficulty_with_icon(label), label)
            self._diff_combo.setItemData(
                self._diff_combo.count() - 1,
                DIFFICULTY_HINTS.get(label, ""),
                Qt.ItemDataRole.ToolTipRole,
            )

        # New Topic button
        self._new_btn = QPushButton("🔀  New Topic")
        self._new_btn.setObjectName("NewTopicButton")
        self._new_btn.setFixedHeight(38)
        self._new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_btn.clicked.connect(self.new_topic_requested.emit)

        layout.addWidget(self._cat_combo)
        layout.addWidget(self._diff_combo)
        layout.addWidget(self._new_btn)
        layout.addStretch()

    # ── public accessors ───────────────────────────────────────────────────

    @property
    def selected_category(self) -> str | None:
        return self._cat_combo.currentData()

    @property
    def selected_difficulty(self) -> str | None:
        return self._diff_combo.currentData()

    def set_enabled(self, enabled: bool) -> None:
        self._cat_combo.setEnabled(enabled)
        self._diff_combo.setEnabled(enabled)
        self._new_btn.setEnabled(enabled)


class _TopicCard(QFrame):
    """The large card showing the assigned topic, metadata, and action button."""

    start_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopicCard")
        self._shuffle_timer = QTimer(self)
        self._shuffle_timer.setInterval(45)
        self._shuffle_timer.timeout.connect(self._advance_topic_shuffle)
        self._shuffle_target = ""
        self._shuffle_steps = 0
        self._build()
        self._apply_style()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 36, 40, 36)
        outer.setSpacing(20)

        # ── "Your Topic:" label ────────────────────────────────────────────
        your_topic_lbl = QLabel("Your Topic:")
        your_topic_lbl.setObjectName("YourTopicLabel")
        outer.addWidget(your_topic_lbl)

        # ── Topic word (big, centered) ─────────────────────────────────────
        word_frame = QFrame()
        word_frame.setObjectName("WordFrame")
        word_layout = QVBoxLayout(word_frame)
        word_layout.setContentsMargins(24, 20, 24, 20)
        word_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._word_label = QLabel("—")
        self._word_label.setObjectName("TopicWord")
        self._word_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._word_label.setWordWrap(True)
        # Room for two lines so long titles (e.g. debate questions) aren't clipped
        self._word_label.setMinimumHeight(100)
        word_layout.addWidget(self._word_label)

        outer.addWidget(word_frame)

        # ── Category / Difficulty row ──────────────────────────────────────
        meta_row = QHBoxLayout()
        meta_row.setSpacing(32)
        meta_row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        cat_col = QVBoxLayout()
        cat_col.setSpacing(2)
        cat_lbl = QLabel("Category:")
        cat_lbl.setObjectName("MetaKey")
        self._cat_value = QLabel("—")
        self._cat_value.setObjectName("MetaValue")
        cat_col.addWidget(cat_lbl)
        cat_col.addWidget(self._cat_value)
        cat_col.addStretch()
        cat_col.setAlignment(Qt.AlignmentFlag.AlignTop)

        diff_col = QVBoxLayout()
        diff_col.setSpacing(2)
        diff_lbl = QLabel("Difficulty:")
        diff_lbl.setObjectName("MetaKey")
        self._diff_value = QLabel("—")
        self._diff_value.setObjectName("MetaValue")
        diff_col.addWidget(diff_lbl)
        diff_col.addWidget(self._diff_value)

        self._diff_hint = QLabel("")
        self._diff_hint.setObjectName("DifficultyHint")
        self._diff_hint.setWordWrap(False)
        diff_col.addWidget(self._diff_hint)
        diff_col.setAlignment(Qt.AlignmentFlag.AlignTop)

        meta_row.addLayout(cat_col)
        meta_row.addLayout(diff_col)
        meta_row.addStretch()
        outer.addLayout(meta_row)

        # ── Description ────────────────────────────────────────────────────
        self._desc_label = QLabel("")
        self._desc_label.setObjectName("TopicDescription")
        self._desc_label.setWordWrap(True)
        self._desc_label.setVisible(False)
        outer.addWidget(self._desc_label)

        # ── Ideas to talk about ────────────────────────────────────────────
        self._hints_label = QLabel("")
        self._hints_label.setObjectName("TopicHints")
        self._hints_label.setWordWrap(True)
        self._hints_label.setTextFormat(Qt.TextFormat.RichText)
        self._hints_label.setVisible(False)
        outer.addWidget(self._hints_label)

        # ── Instruction banner ─────────────────────────────────────────────
        minutes = max(1, round(settings.MIN_SPEAKING_DURATION / 60))
        self._instruction = QLabel(
            f"ℹ️   Talk for at least {minutes} minute{'s' if minutes != 1 else ''}. "
            "There are no wrong answers — just keep going!"
        )
        self._instruction.setObjectName("InstructionLabel")
        self._instruction.setWordWrap(True)
        self._instruction.setVisible(False)
        outer.addWidget(self._instruction)

        # ── Start Practice button ──────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._start_btn = QPushButton("🎤   Start Practice")
        self._start_btn.setObjectName("StartButton")
        self._start_btn.setFixedHeight(52)
        self._start_btn.setMinimumWidth(220)
        self._start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self.start_clicked.emit)

        btn_row.addWidget(self._start_btn)
        outer.addLayout(btn_row)

    # ── public update API ──────────────────────────────────────────────────

    def show_loading(self) -> None:
        self._cancel_topic_shuffle()
        self._word_label.setText("Loading…")
        self._cat_value.setText("—")
        self._diff_value.setText("—")
        self._diff_hint.setText("")
        self._desc_label.setVisible(False)
        self._hints_label.setVisible(False)
        self._instruction.setVisible(False)
        self._start_btn.setEnabled(False)
        self._start_btn.setCursor(Qt.CursorShape.ForbiddenCursor)

    def show_topic(self, topic: dict, animate: bool = False) -> None:
        word = topic.get("word", "—")
        if animate:
            self._start_topic_shuffle(word)
        else:
            self._cancel_topic_shuffle()
            self._word_label.setText(word)
        self._cat_value.setText(category_with_icon(topic.get("category")))
        level = topic.get("difficulty_display")
        self._diff_value.setText(_difficulty_with_icon(level))
        self._diff_hint.setText(DIFFICULTY_HINTS.get(level or "", ""))

        desc = topic.get("description") or ""
        self._desc_label.setText(desc)
        self._desc_label.setVisible(bool(desc))

        hints = topic.get("hints") or get_topic_hints(topic.get("word"))
        self._hints_label.setText(format_hints_html(hints))
        self._hints_label.setVisible(bool(hints))
        self._instruction.setVisible(True)

        self._start_btn.setEnabled(True)
        self._start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start_btn.setToolTip("")

    def _start_topic_shuffle(self, word: str) -> None:
        self._cancel_topic_shuffle()
        self._shuffle_target = word
        self._shuffle_steps = 7
        self._word_label.setText(self._scramble_word(word))
        self._shuffle_timer.start()

    def _advance_topic_shuffle(self) -> None:
        self._shuffle_steps -= 1
        if self._shuffle_steps <= 0:
            self._word_label.setText(self._shuffle_target)
            self._shuffle_timer.stop()
            return
        self._word_label.setText(self._scramble_word(self._shuffle_target))

    @staticmethod
    def _scramble_word(word: str) -> str:
        positions = [index for index, char in enumerate(word) if not char.isspace()]
        if len(positions) < 2:
            return word

        original = [word[index] for index in positions]
        shuffled = original.copy()
        random.shuffle(shuffled)
        if shuffled == original:
            shuffled = shuffled[1:] + shuffled[:1]

        characters = list(word)
        for index, char in zip(positions, shuffled):
            characters[index] = char
        return "".join(characters)

    def _cancel_topic_shuffle(self) -> None:
        self._shuffle_timer.stop()
        self._shuffle_steps = 0

    def show_exhausted(self, message: str | None = None) -> None:
        self._cancel_topic_shuffle()
        self._word_label.setText("🏆")
        self._cat_value.setText("—")
        self._diff_value.setText("—")
        self._diff_hint.setText("")
        self._hints_label.setVisible(False)
        self._desc_label.setText(message or POOL_EXHAUSTED_MESSAGE)
        self._desc_label.setVisible(True)
        self._instruction.setVisible(False)
        self._start_btn.setEnabled(False)
        self._start_btn.setCursor(Qt.CursorShape.ForbiddenCursor)
        self._start_btn.setToolTip("No topics remaining")

    def _apply_style(self) -> None:
        c = settings
        self.setStyleSheet(f"""
            #TopicCard {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 16px;
            }}
            #YourTopicLabel {{
                font-size: 13px;
                font-weight: 500;
                color: {c.COLOR_TEXT_MUTED};
                text-transform: uppercase;
                letter-spacing: 1px;
            }}
            #WordFrame {{
                background-color: rgba(108, 99, 255, 0.08);
                border: 1px solid rgba(108, 99, 255, 0.25);
                border-radius: 12px;
            }}
            #TopicWord {{
                font-size: 34px;
                font-weight: 700;
                color: {c.COLOR_TEXT_PRIMARY};
                padding: 4px 0px;
            }}
            #MetaKey {{
                font-size: 11px;
                font-weight: 500;
                color: {c.COLOR_TEXT_MUTED};
                text-transform: uppercase;
                letter-spacing: 0.8px;
            }}
            #MetaValue {{
                font-size: 15px;
                font-weight: 600;
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #TopicDescription {{
                font-size: 16px;
                font-weight: 500;
                color: {c.COLOR_TEXT_PRIMARY};
                line-height: 1.6;
            }}
            #DifficultyHint {{
                font-size: 12px;
                color: {c.COLOR_TEXT_MUTED};
            }}
            #TopicHints {{
                font-size: 14px;
                color: {c.COLOR_TEXT_SECONDARY};
                background-color: rgba(255, 193, 7, 0.08);
                border: 1px solid rgba(255, 193, 7, 0.25);
                border-radius: 8px;
                padding: 10px 14px;
            }}
            #InstructionLabel {{
                font-size: 13px;
                color: {c.COLOR_ACCENT};
                background-color: rgba(108, 99, 255, 0.08);
                border: 1px solid rgba(108, 99, 255, 0.20);
                border-radius: 8px;
                padding: 10px 14px;
            }}
            #StartButton {{
                background-color: {c.COLOR_ACCENT};
                color: #FFFFFF;
                border: none;
                border-radius: 10px;
                font-size: 16px;
                font-weight: 600;
                padding: 0px 36px;
            }}
            #StartButton:hover {{
                background-color: {c.COLOR_ACCENT_HOVER};
            }}
            #StartButton:pressed {{
                background-color: #5a52e0;
            }}
            #StartButton:disabled {{
                background-color: {c.COLOR_BORDER};
                color: {c.COLOR_TEXT_MUTED};
            }}
        """)


class _InfoChip(QFrame):
    """Small stat chip below the topic card."""

    def __init__(
        self, icon: str, label: str, value: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("InfoChip")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(4)

        lbl = QLabel(f"{icon}  {label}")
        lbl.setObjectName("ChipLabel")

        self._value_lbl = QLabel(value)
        self._value_lbl.setObjectName("ChipValue")

        layout.addWidget(lbl)
        layout.addWidget(self._value_lbl)

        self.setStyleSheet(f"""
            #InfoChip {{
                background-color: {settings.COLOR_BG_CARD};
                border: 1px solid {settings.COLOR_BORDER};
                border-radius: 10px;
            }}
            #ChipLabel  {{ font-size: 12px; font-weight: 500; color: {settings.COLOR_TEXT_SECONDARY}; }}
            #ChipValue  {{ font-size: 13px; font-weight: 600; color: {settings.COLOR_TEXT_PRIMARY};   }}
        """)

    def set_value(self, value: str) -> None:
        self._value_lbl.setText(value)


# ---------------------------------------------------------------------------
# PracticePage
# ---------------------------------------------------------------------------

class PracticePage(QWidget):
    """
    Main practice page — topic display, filters, session lifecycle.

    The page owns the current session state:
        _current_session_id  — id of the active PENDING session (or None)
        _current_topic       — the last loaded topic dict (or None)

    Signals
    -------
    navigate_to_recording(topic: dict, session_id: int)
        Emitted when the user clicks Start Practice and the session has been
        moved to IN_PROGRESS.  MainWindow listens and navigates to RecordingPage.
    """

    navigate_to_recording = Signal(dict, int)

    def __init__(self, user_id: int = 1, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._user_id = user_id
        self._word_service = WordService()
        self._current_session_id: int | None = None
        self._current_topic: dict | None = None
        self._page_state: str = _STATE_IDLE

        self._build_ui()
        # Load first topic shortly after the widget appears
        QTimer.singleShot(200, self._load_initial_topic)
        # Apply saved difficulty preference (after UI is built)
        QTimer.singleShot(250, self._apply_saved_difficulty)

    # ------------------------------------------------------------------ #
    # UI construction                                                       #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setObjectName("PracticeScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("PracticeContent")
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(32, 32, 32, 32)
        self._content_layout.setSpacing(20)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── Page header ────────────────────────────────────────────────────
        page_title = QLabel("Today's Practice")
        page_title.setObjectName("PageTitle")
        page_subtitle = QLabel("Build your English speaking confidence")
        page_subtitle.setObjectName("PageSubtitle")
        self._content_layout.addWidget(page_title)
        self._content_layout.addWidget(page_subtitle)

        # ── Filter bar ─────────────────────────────────────────────────────
        self._filter_bar = _FilterBar(self._word_service)
        self._filter_bar.new_topic_requested.connect(self._on_new_topic)
        self._content_layout.addWidget(self._filter_bar)

        # ── Topic card ─────────────────────────────────────────────────────
        self._topic_card = _TopicCard()
        self._topic_card.start_clicked.connect(self._on_start_practice)
        self._topic_card.show_loading()
        self._content_layout.addWidget(self._topic_card)

        # ── Info chips row ─────────────────────────────────────────────────
        chips_row = QHBoxLayout()
        chips_row.setSpacing(14)

        self._chip_remaining = _InfoChip("📚", "Topics Remaining", "—")
        self._chip_duration  = _InfoChip("⏱️", "Min Duration",     "1 minute")
        self._chip_format    = _InfoChip("🎯", "Format",           "Free speaking")

        chips_row.addWidget(self._chip_remaining)
        chips_row.addWidget(self._chip_duration)
        chips_row.addWidget(self._chip_format)
        self._content_layout.addLayout(chips_row)

        # ── Tips card ──────────────────────────────────────────────────────
        self._content_layout.addWidget(self._build_tips_card())
        self._content_layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)
        self._apply_page_styles()

    def _build_tips_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("TipsCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        title = QLabel("💡  Tips for a great session")
        title.setObjectName("TipsTitle")
        layout.addWidget(title)

        tips = [
            "Speak clearly and at a natural pace — aim for 120–140 words per minute.",
            "Stay on topic, but feel free to share personal examples.",
            "Avoid filler words like 'um', 'uh', and 'like' — pause instead.",
            "Structure your answer: introduce → elaborate → conclude.",
            "Practise regularly — even one session per day makes a difference.",
        ]
        for tip in tips:
            row = QHBoxLayout()
            row.setSpacing(8)
            dot = QLabel("•")
            dot.setObjectName("TipDot")
            dot.setFixedWidth(12)
            txt = QLabel(tip)
            txt.setObjectName("TipText")
            txt.setWordWrap(True)
            row.addWidget(dot, alignment=Qt.AlignmentFlag.AlignTop)
            row.addWidget(txt)
            layout.addLayout(row)

        return card

    # ------------------------------------------------------------------ #
    # Data loading                                                          #
    # ------------------------------------------------------------------ #

    def _load_initial_topic(self) -> None:
        """Called once on first show via QTimer."""
        self._load_topic()

    def _load_topic(self, animate: bool = False) -> None:
        """Assign a new topic using the current filter selections."""
        category   = self._filter_bar.selected_category
        difficulty = self._filter_bar.selected_difficulty

        self._filter_bar.set_enabled(False)
        self._topic_card.show_loading()

        try:
            result = self._word_service.assign_topic(
                user_id=self._user_id,
                category=category,
                difficulty_label=difficulty,
            )

            if result["exhausted"]:
                self._page_state = _STATE_EXHAUSTED
                self._current_session_id = None
                self._current_topic = None
                self._topic_card.show_exhausted(result.get("message"))
                self._chip_remaining.set_value("0 topics")
                logger.info("Topic pool exhausted for user_id=%s", self._user_id)
            else:
                self._page_state = _STATE_TOPIC_LOADED
                self._current_session_id = result["session_id"]
                self._current_topic = result
                self._topic_card.show_topic(result, animate=animate)
                self._update_remaining_chip(category, difficulty)
                logger.debug(
                    "Topic assigned: %r  session_id=%s",
                    result["word"], result["session_id"],
                )

        except Exception as exc:
            logger.error("Failed to load topic: %s", exc)
            self._topic_card.show_exhausted(
                "Failed to load a topic. Please restart the application."
            )
        finally:
            self._filter_bar.set_enabled(True)

    def _update_remaining_chip(
        self, category: str | None, difficulty: str | None
    ) -> None:
        try:
            count = self._word_service.get_unused_count_for_user(
                self._user_id,
                category=category,
                difficulty_label=difficulty,
            )
            label = "topic" if count == 1 else "topics"
            self._chip_remaining.set_value(f"{count} {label}")
        except Exception as exc:
            logger.warning("Could not update remaining chip: %s", exc)

    # ------------------------------------------------------------------ #
    # Slot handlers                                                         #
    # ------------------------------------------------------------------ #

    def _on_new_topic(self) -> None:
        """User clicked 🔀 New Topic — abandon current session and load fresh."""
        logger.debug(
            "New topic requested. Abandoning session_id=%s", self._current_session_id
        )
        if self._current_session_id is not None:
            try:
                self._word_service.abandon_practice(self._current_session_id)
            except Exception as exc:
                logger.warning("Could not abandon session: %s", exc)
            self._current_session_id = None

        self._load_topic(animate=True)

    def _on_start_practice(self) -> None:
        """
        User clicked Start Practice.

        Transitions the session to IN_PROGRESS and emits ``navigate_to_recording``
        so MainWindow can prepare RecordingPage and switch to it.
        """
        if self._current_session_id is None:
            logger.warning("Start Practice clicked but no session active")
            return

        try:
            ok = self._word_service.start_practice(self._current_session_id)
            if not ok:
                logger.error(
                    "start_practice returned False for session_id=%s",
                    self._current_session_id,
                )
                return
            logger.info(
                "Session id=%s moved to IN_PROGRESS — navigating to RecordingPage",
                self._current_session_id,
            )
        except Exception as exc:
            logger.error("Could not start session: %s", exc)
            return

        # Disable filters/button while in recording flow
        self._filter_bar.set_enabled(False)
        btn = self._topic_card._start_btn
        btn.setEnabled(False)
        btn.setText("🔴  Recording…")

        # Hand off to MainWindow → RecordingPage
        self.navigate_to_recording.emit(
            dict(self._current_topic),
            self._current_session_id,
        )

    def on_recording_completed(self) -> None:
        """
        Called by MainWindow after RecordingPage has saved the file and
        complete_practice() has been called successfully.

        Resets UI and loads the next topic automatically.
        """
        logger.info(
            "PracticePage: recording completed for session_id=%s",
            self._current_session_id,
        )
        self._current_session_id = None
        self._current_topic = None
        self._filter_bar.set_enabled(True)
        self._topic_card._start_btn.setText("🎤   Start Practice")
        self._topic_card._start_btn.setEnabled(False)
        self._topic_card.show_loading()
        # Small delay so the user sees the page before we trigger a DB call
        QTimer.singleShot(300, self._load_topic)

    def on_recording_abandoned(self) -> None:
        """
        Called by MainWindow after RecordingPage signals session_abandoned.

        Re-enables the UI and loads a new topic (the abandoned word stays available).
        """
        logger.info(
            "PracticePage: session abandoned (session_id=%s)",
            self._current_session_id,
        )
        self._current_session_id = None
        self._current_topic = None
        self._filter_bar.set_enabled(True)
        self._topic_card._start_btn.setText("🎤   Start Practice")
        self._topic_card._start_btn.setEnabled(False)
        self._topic_card.show_loading()
        QTimer.singleShot(300, self._load_topic)

    # ------------------------------------------------------------------ #
    # Public refresh API (called by MainWindow when navigating here)       #
    # ------------------------------------------------------------------ #

    def _apply_saved_difficulty(self) -> None:
        """Load the user's saved difficulty preference and apply it to the filter bar."""
        try:
            from speakup.services.gamification_service import GamificationService
            prefs = GamificationService().get_preferences(self._user_id)
            diff_pref = prefs.get("difficulty_preference", "all")
            if diff_pref == "all":
                self._filter_bar._diff_combo.setCurrentIndex(0)  # "All Levels"
            else:
                # Map DB value → display label
                from speakup.data.words.loader import DIFFICULTY_DISPLAY
                label = DIFFICULTY_DISPLAY.get(diff_pref, diff_pref.capitalize())
                idx = self._filter_bar._diff_combo.findData(label)
                if idx >= 0:
                    self._filter_bar._diff_combo.setCurrentIndex(idx)
        except Exception as exc:
            logger.debug("Could not apply saved difficulty: %s", exc)

    def refresh(self) -> None:
        """Reload the topic when the user navigates back to this page."""
        if self._page_state == _STATE_IDLE or self._current_session_id is None:
            self._load_topic()
        else:
            self._update_remaining_chip(
                self._filter_bar.selected_category,
                self._filter_bar.selected_difficulty,
            )

    def reset_after_profile_data_deleted(self) -> None:
        """Drop stale session state after this profile's history is cleared."""
        self._current_session_id = None
        self._current_topic = None
        self._page_state = _STATE_IDLE
        self._topic_card.show_loading()
        QTimer.singleShot(0, self._load_topic)

    # ------------------------------------------------------------------ #
    # Styles                                                                #
    # ------------------------------------------------------------------ #

    def _apply_page_styles(self) -> None:
        c = settings
        self.setStyleSheet(f"""
            #PracticeScroll, #PracticeContent {{
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

            /* Filter bar */
            #FilterCombo {{
                background-color: {c.COLOR_BG_CARD};
                color: {c.COLOR_TEXT_PRIMARY};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 8px;
                padding: 0px 12px;
                font-size: 13px;
                font-weight: 500;
            }}
            #FilterCombo::drop-down {{
                border: none;
                padding-right: 10px;
            }}
            #FilterCombo QAbstractItemView {{
                background-color: {c.COLOR_BG_CARD};
                color: {c.COLOR_TEXT_PRIMARY};
                border: 1px solid {c.COLOR_BORDER};
                selection-background-color: {c.COLOR_ACCENT};
                padding: 4px;
            }}
            #FilterCombo:disabled {{
                color: {c.COLOR_TEXT_MUTED};
                background-color: {c.COLOR_BG_DARK};
            }}
            #NewTopicButton {{
                background-color: transparent;
                color: {c.COLOR_ACCENT};
                border: 1px solid {c.COLOR_ACCENT};
                border-radius: 8px;
                padding: 0px 18px;
                font-size: 13px;
                font-weight: 600;
            }}
            #NewTopicButton:hover {{
                background-color: rgba(108, 99, 255, 0.12);
            }}
            #NewTopicButton:pressed {{
                background-color: rgba(108, 99, 255, 0.22);
            }}
            #NewTopicButton:disabled {{
                color: {c.COLOR_TEXT_MUTED};
                border-color: {c.COLOR_BORDER};
                background-color: transparent;
            }}

            /* Tips card */
            #TipsCard {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 12px;
            }}
            #TipsTitle {{
                font-size: 14px;
                font-weight: 600;
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #TipDot {{
                font-size: 16px;
                color: {c.COLOR_ACCENT};
                font-weight: 700;
            }}
            #TipText {{
                font-size: 13px;
                color: {c.COLOR_TEXT_SECONDARY};
            }}
        """)
