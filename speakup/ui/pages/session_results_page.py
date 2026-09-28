"""
SessionResultsPage — polished post-practice results screen.

Layout
──────
┌─ SPEAKING SESSION RESULTS ──────────────────────────────────────────────┐
│  Topic: Artificial Intelligence  ·  Technology · Intermediate           │
│  Duration: 1m 15s                                                        │
│                                                                          │
│  ┌─ Overall Score ──────────────────────────────────────────────────┐   │
│  │              78                                                   │   │
│  │           ── / 100 ──                                             │   │
│  │              Good                                                 │   │
│  │  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░  78%                                   │   │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌─ Score Breakdown ────────────────────────────────────────────────┐   │
│  │  Fluency    74  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░  Fair                         │   │
│  │  Vocabulary 82  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░  Good                        │   │
│  │  Grammar    76  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░  Good                        │   │
│  │  Confidence 79  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░  Good                        │   │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌─ Speaking Metrics ───────────────────────────────────────────────┐   │
│  │  ⚡ 115 WPM  │  🔇 3 fillers  │  ⏸ 2 pauses  │  📚 62% TTR     │   │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌─ Feedback ───────────────────────────────────────────────────────┐   │
│  │  ✅ What you did well            ─────────────────────────────── │   │
│  │  • Good speaking pace                                            │   │
│  │  • Rich vocabulary                                               │   │
│  │                                                                  │   │
│  │  📌 Improve next time            ─────────────────────────────── │   │
│  │  • Reduce filler words                                           │   │
│  │                                                                  │   │
│  │  💡 Try this                     ─────────────────────────────── │   │
│  │  • Pause instead of saying "um"                                  │   │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌─ Your Transcript ▾  (click to expand) ──────────────────────────┐   │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  [🎤  Practice Again]            [📊  Back to Dashboard]                │
└──────────────────────────────────────────────────────────────────────────┘

Signals
───────
practice_again()
    Emitted when the user clicks "Practice Again". MainWindow navigates
    to PracticePage and loads a fresh topic.

go_to_dashboard()
    Emitted when the user clicks "Back to Dashboard". MainWindow navigates
    to DashboardPage.
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, Signal, Slot
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
from speakup.utils.helpers import (
    format_duration,
    format_score,
    score_to_label,
    score_colour,
)

if TYPE_CHECKING:
    from speakup.services.feedback_schemas import AIFeedbackResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: compute filler score when absent from result dict
# ---------------------------------------------------------------------------

def _compute_filler_score(result: dict) -> float:
    """
    ``filler_score`` is not a key in ``to_ui_dict()``.  Derive it from
    ``filler_word_count`` and ``total_words`` using the same formula as
    ``TranscriptionResultPage.show_analysis()``.
    """
    fc = result.get("filler_word_count") or 0
    tw = result.get("total_words") or 100
    rate = fc / tw if tw else 0.0
    from speakup.services.analysis.filler_words import _score_from_filler_rate
    return _score_from_filler_rate(rate)


# ===========================================================================
# Sub-widgets
# ===========================================================================

class _ScoreCard(QFrame):
    """
    A compact card showing one score dimension:  label, numeric score,
    a progress bar, and a descriptive label (e.g. "Good").
    """

    def __init__(
        self,
        label: str,
        score: float | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ScoreCard")
        self._build(label, score)

    def _build(self, label: str, score: float | None) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(14)

        # Label column
        name_lbl = QLabel(label)
        name_lbl.setObjectName("ScoreCardName")
        name_lbl.setFixedWidth(92)
        layout.addWidget(name_lbl)

        # Numeric score
        val_str  = f"{score:.0f}" if score is not None else "—"
        val_lbl  = QLabel(val_str)
        val_lbl.setObjectName("ScoreCardValue")
        val_lbl.setFixedWidth(34)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if score is not None:
            val_lbl.setStyleSheet(f"color: {score_colour(score)}; font-weight: 700;")
        layout.addWidget(val_lbl)

        # Progress bar
        bar = QProgressBar()
        bar.setObjectName("ScoreCardBar")
        bar.setRange(0, 100)
        bar.setValue(int(score) if score is not None else 0)
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        _colour = score_colour(score) if score is not None else settings.COLOR_BORDER
        bar.setStyleSheet(f"""
            QProgressBar#ScoreCardBar {{
                background-color: {settings.COLOR_BORDER};
                border: none;
                border-radius: 4px;
            }}
            QProgressBar#ScoreCardBar::chunk {{
                background-color: {_colour};
                border-radius: 4px;
            }}
        """)
        layout.addWidget(bar)

        # Descriptive label
        desc_lbl = QLabel(score_to_label(score))
        desc_lbl.setObjectName("ScoreCardDesc")
        desc_lbl.setFixedWidth(80)
        desc_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(desc_lbl)

    def _apply_style(self) -> None:
        self.setStyleSheet(f"""
            #ScoreCard {{
                background-color: transparent;
                border-bottom: 1px solid {settings.COLOR_BORDER};
            }}
        """)


class _MetricChip(QFrame):
    """A small labelled chip for a single secondary metric."""

    def __init__(
        self,
        icon: str,
        label: str,
        value: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MetricChip")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)

        hdr = QLabel(f"{icon}  {label}")
        hdr.setObjectName("MetricChipHeader")

        val = QLabel(value)
        val.setObjectName("MetricChipValue")

        layout.addWidget(hdr)
        layout.addWidget(val)
        self._value_label = val

    def set_value(self, value: str) -> None:
        self._value_label.setText(value)


class _FeedbackSection(QFrame):
    """
    Three-part feedback block: strengths, improvements, suggestions.
    All data comes directly from ``to_ui_dict()`` — no invented content.
    """

    def __init__(
        self,
        strengths:    list[str],
        improvements: list[str],
        suggestions:  list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FeedbackSection")
        self._build(strengths, improvements, suggestions)

    def _build(
        self,
        strengths:    list[str],
        improvements: list[str],
        suggestions:  list[str],
    ) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(18)

        sections = [
            ("✅  What you did well",  strengths,    "FBStrengthsTitle"),
            ("📌  Improve next time",  improvements, "FBImprovesTitle"),
            ("💡  Try this",           suggestions,  "FBSuggestsTitle"),
        ]
        for title, items, obj_name in sections:
            if not items:
                continue
            self._add_group(layout, title, items, obj_name)

    @staticmethod
    def _add_group(
        parent_layout: QVBoxLayout,
        title: str,
        items: list[str],
        obj_name: str,
    ) -> None:
        title_lbl = QLabel(title)
        title_lbl.setObjectName(obj_name)
        parent_layout.addWidget(title_lbl)

        for text in items:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.setContentsMargins(4, 0, 0, 0)

            dot = QLabel("•")
            dot.setObjectName("FBBullet")
            dot.setFixedWidth(14)

            item_lbl = QLabel(text)
            item_lbl.setObjectName("FBItem")
            item_lbl.setWordWrap(True)

            row.addWidget(dot, alignment=Qt.AlignmentFlag.AlignTop)
            row.addWidget(item_lbl)
            parent_layout.addLayout(row)


class _TranscriptAccordion(QFrame):
    """
    Collapsible transcript section — click the header to expand/collapse.
    """

    def __init__(
        self,
        transcript: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TranscriptAccordion")
        self._expanded = False
        self._transcript = transcript or ""
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toggle button acting as header
        self._toggle_btn = QPushButton("📄  Your Transcript  ▶")
        self._toggle_btn.setObjectName("TranscriptToggleBtn")
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setFixedHeight(44)
        self._toggle_btn.clicked.connect(self._on_toggle)
        layout.addWidget(self._toggle_btn)

        # Transcript text — hidden by default
        self._text_area = QTextEdit()
        self._text_area.setObjectName("TranscriptBody")
        self._text_area.setReadOnly(True)
        self._text_area.setPlainText(self._transcript)
        self._text_area.setMinimumHeight(160)
        self._text_area.setVisible(False)
        layout.addWidget(self._text_area)

    def _on_toggle(self, checked: bool) -> None:
        self._expanded = checked
        self._text_area.setVisible(checked)
        icon = "▼" if checked else "▶"
        self._toggle_btn.setText(f"📄  Your Transcript  {icon}")


# ===========================================================================
# SessionResultsPage
# ===========================================================================

class SessionResultsPage(QWidget):
    """
    Full-screen results page shown after a completed practice session.

    Accepts data via ``prepare()``.  Every displayed metric comes directly
    from the ``analysis_result`` dict (output of ``SpeechAnalysisResult.to_ui_dict()``)
    or the ``practice_session`` dict — no values are invented.

    Signals
    -------
    practice_again()
        User wants to practice again immediately.
    go_to_dashboard()
        User wants to go back to the dashboard.
    """

    practice_again   = Signal()
    go_to_dashboard  = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SessionResultsPage")
        self._session_id:      int | None = None
        self._topic:           dict = {}
        self._analysis_result: dict = {}
        self._duration_seconds: int = 0
        self._transcript:      str = ""

        self._build_ui()
        self._apply_styles()

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    def prepare(
        self,
        session_id:      int,
        topic:           dict,
        analysis_result: dict,
        duration_seconds: int = 0,
        transcript:      str = "",
    ) -> None:
        """
        Populate the results page with session data.

        Args:
            session_id:       Practice session id.
            topic:            Topic dict (word, category, difficulty_display).
            analysis_result:  Dict from ``SpeechAnalysisResult.to_ui_dict()``
                              (or a compatible subset).
            duration_seconds: Recorded speaking duration.
            transcript:       Plain-text transcript string (may be empty).
        """
        self._session_id      = session_id
        self._topic           = topic or {}
        self._analysis_result = analysis_result or {}
        self._duration_seconds = duration_seconds
        self._transcript      = transcript or ""

        logger.info(
            "SessionResultsPage.prepare  session=%s  overall=%.1f  duration=%ds",
            session_id,
            self._analysis_result.get("overall_score", 0) or 0,
            duration_seconds,
        )
        self._populate()

    # ------------------------------------------------------------------ #
    # UI construction                                                       #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setObjectName("ResultsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._content = QWidget()
        self._content.setObjectName("ResultsContent")
        self._main_layout = QVBoxLayout(self._content)
        self._main_layout.setContentsMargins(36, 32, 36, 40)
        self._main_layout.setSpacing(20)
        self._main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── Header ────────────────────────────────────────────────────────
        self._build_header_section()

        # ── Overall score hero ────────────────────────────────────────────
        self._overall_card = self._build_overall_card()
        self._main_layout.addWidget(self._overall_card)

        # ── Score breakdown (4 dimensions) ───────────────────────────────
        self._breakdown_card = self._build_breakdown_card()
        self._main_layout.addWidget(self._breakdown_card)

        # ── Secondary metrics row ─────────────────────────────────────────
        self._metrics_card = self._build_metrics_card()
        self._main_layout.addWidget(self._metrics_card)

        # ── Feedback ─────────────────────────────────────────────────────
        self._feedback_frame_container = QFrame()
        self._feedback_frame_container.setObjectName("FeedbackContainer")
        self._feedback_inner_layout = QVBoxLayout(self._feedback_frame_container)
        self._feedback_inner_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.addWidget(self._feedback_frame_container)

        # ── Transcript accordion ──────────────────────────────────────────
        self._transcript_frame_container = QFrame()
        self._transcript_frame_container.setObjectName("TranscriptContainer")
        self._transcript_inner_layout = QVBoxLayout(self._transcript_frame_container)
        self._transcript_inner_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.addWidget(self._transcript_frame_container)

        # ── AI Feedback card (hidden until analysis completes) ─────────────
        self._ai_feedback_card = self._build_ai_feedback_card()
        self._ai_feedback_card.setVisible(False)
        self._main_layout.addWidget(self._ai_feedback_card)

        # ── Navigation buttons ────────────────────────────────────────────
        self._main_layout.addWidget(self._build_nav_buttons())
        self._main_layout.addStretch()

        scroll.setWidget(self._content)
        outer.addWidget(scroll)

    def _build_header_section(self) -> None:
        """Page title + topic + duration row."""
        # Title
        title = QLabel("Speaking Session Results")
        title.setObjectName("ResultsPageTitle")
        self._main_layout.addWidget(title)

        # Meta row: topic pill + duration pill
        meta = QHBoxLayout()
        meta.setSpacing(12)
        meta.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self._topic_label = QLabel("—")
        self._topic_label.setObjectName("ResultsTopicLabel")

        self._duration_label = QLabel("—")
        self._duration_label.setObjectName("ResultsDurationLabel")

        meta.addWidget(self._topic_label)
        meta.addWidget(self._duration_label)
        meta.addStretch()
        self._main_layout.addLayout(meta)

    def _build_overall_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("OverallCard")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Big score number
        self._overall_score_lbl = QLabel("—")
        self._overall_score_lbl.setObjectName("OverallScoreNumber")
        self._overall_score_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # "/100" sub-label
        denom_lbl = QLabel("/ 100")
        denom_lbl.setObjectName("OverallDenom")
        denom_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Descriptive label
        self._overall_label_lbl = QLabel("—")
        self._overall_label_lbl.setObjectName("OverallLabel")
        self._overall_label_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Progress bar
        self._overall_bar = QProgressBar()
        self._overall_bar.setObjectName("OverallBar")
        self._overall_bar.setRange(0, 100)
        self._overall_bar.setValue(0)
        self._overall_bar.setFixedHeight(10)
        self._overall_bar.setTextVisible(False)

        layout.addWidget(self._overall_score_lbl)
        layout.addWidget(denom_lbl)
        layout.addWidget(self._overall_label_lbl)
        layout.addSpacing(4)
        layout.addWidget(self._overall_bar)
        return card

    def _build_breakdown_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("BreakdownCard")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(4)

        title = QLabel("Score Breakdown")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        sep = QFrame()
        sep.setObjectName("CardSep")
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        layout.addWidget(sep)
        layout.addSpacing(4)

        # Placeholder rows — replaced in _populate()
        self._score_cards_layout = QVBoxLayout()
        self._score_cards_layout.setSpacing(2)
        layout.addLayout(self._score_cards_layout)

        return card

    def _build_metrics_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("MetricsCard")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(12)

        title = QLabel("Speaking Metrics")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        # Chip row — built once, updated in _populate()
        row = QHBoxLayout()
        row.setSpacing(10)

        self._chip_wpm    = _MetricChip("⚡", "Words / min",   "—")
        self._chip_filler = _MetricChip("🔇", "Filler words",  "—")
        self._chip_pauses = _MetricChip("⏸", "Pauses",        "—")
        self._chip_ttr    = _MetricChip("📚", "Vocab diversity","—")

        for chip in (self._chip_wpm, self._chip_filler,
                     self._chip_pauses, self._chip_ttr):
            row.addWidget(chip)
        row.addStretch()
        layout.addLayout(row)
        return card

    def _build_nav_buttons(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)

        self._practice_btn = QPushButton("🎤   Practice Again")
        self._practice_btn.setObjectName("PracticeAgainBtn")
        self._practice_btn.setFixedHeight(50)
        self._practice_btn.setMinimumWidth(200)
        self._practice_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._practice_btn.clicked.connect(self.practice_again.emit)

        self._dashboard_btn = QPushButton("📊   Back to Dashboard")
        self._dashboard_btn.setObjectName("DashboardBtn")
        self._dashboard_btn.setFixedHeight(50)
        self._dashboard_btn.setMinimumWidth(200)
        self._dashboard_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dashboard_btn.clicked.connect(self.go_to_dashboard.emit)

        layout.addWidget(self._practice_btn)
        layout.addWidget(self._dashboard_btn)
        layout.addStretch()
        return row

    # ------------------------------------------------------------------ #
    # Population                                                            #
    # ------------------------------------------------------------------ #

    def _populate(self) -> None:
        """Fill every widget from self._analysis_result and self._topic."""
        r = self._analysis_result
        t = self._topic

        # ── Header ────────────────────────────────────────────────────────
        word = t.get("word", "—")
        cat  = t.get("category") or ""
        diff = t.get("difficulty_display") or t.get("difficulty") or ""
        meta_parts = [p for p in [cat, diff] if p]
        topic_text = word + (f"  ·  {' · '.join(meta_parts)}" if meta_parts else "")
        self._topic_label.setText(f"🎯  {topic_text}")
        self._duration_label.setText(
            f"⏱  {format_duration(self._duration_seconds)}"
        )

        # ── Overall score ─────────────────────────────────────────────────
        overall = r.get("overall_score")
        overall_int = int(overall) if overall is not None else 0
        self._overall_score_lbl.setText(str(overall_int) if overall is not None else "—")
        self._overall_label_lbl.setText(score_to_label(overall))

        colour = score_colour(overall) if overall is not None else settings.COLOR_BORDER
        self._overall_bar.setValue(overall_int)
        self._overall_bar.setStyleSheet(f"""
            QProgressBar#OverallBar {{
                background-color: {settings.COLOR_BORDER};
                border: none;
                border-radius: 5px;
            }}
            QProgressBar#OverallBar::chunk {{
                background-color: {colour};
                border-radius: 5px;
            }}
        """)
        # Colour the big number
        self._overall_score_lbl.setStyleSheet(
            f"color: {colour}; font-size: 72px; font-weight: 700;"
        )

        # ── Score breakdown cards ─────────────────────────────────────────
        # Clear existing widgets
        while self._score_cards_layout.count():
            item = self._score_cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        dimensions = [
            ("Fluency",     r.get("fluency_score")),
            ("Vocabulary",  r.get("vocabulary_score")),
            ("Grammar",     r.get("grammar_score")),
            ("Confidence",  r.get("confidence_score")),
        ]
        for name, score in dimensions:
            card = _ScoreCard(name, score)
            self._score_cards_layout.addWidget(card)

        # ── Secondary metrics chips ───────────────────────────────────────
        wpm = r.get("words_per_minute")
        self._chip_wpm.set_value(f"{wpm:.0f}" if wpm is not None else "—")

        filler_count = r.get("filler_word_count")
        self._chip_filler.set_value(
            str(filler_count) if filler_count is not None else "—"
        )

        pause_count = r.get("pause_count")
        self._chip_pauses.set_value(
            str(pause_count) if pause_count is not None else "—"
        )

        ttr = r.get("type_token_ratio")
        self._chip_ttr.set_value(
            f"{ttr:.0%}" if ttr is not None else "—"
        )

        # ── Feedback ─────────────────────────────────────────────────────
        # Clear old feedback widget (if any)
        while self._feedback_inner_layout.count():
            item = self._feedback_inner_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        strengths    = r.get("strengths",    []) or []
        improvements = r.get("improvements", []) or []
        suggestions  = r.get("suggestions",  []) or []

        if any([strengths, improvements, suggestions]):
            fb = _FeedbackSection(strengths, improvements, suggestions)
            fb.setObjectName("FeedbackCard")
            self._feedback_inner_layout.addWidget(fb)

        # ── Transcript accordion ──────────────────────────────────────────
        while self._transcript_inner_layout.count():
            item = self._transcript_inner_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if self._transcript.strip():
            acc = _TranscriptAccordion(self._transcript)
            acc.setObjectName("TranscriptAccordionCard")
            self._transcript_inner_layout.addWidget(acc)

    # ------------------------------------------------------------------ #
    # AI Feedback card                                                      #
    # ------------------------------------------------------------------ #

    def _build_ai_feedback_card(self) -> QFrame:
        """
        Build the AI Feedback card.

        UI design principles for this card:
        1. The header badge clearly identifies the source (Local or LLM).
        2. Each section is labelled to distinguish objective vs AI-generated.
        3. The card is hidden until show_ai_feedback() is called.
        """
        card = QFrame()
        card.setObjectName("AIFeedbackCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # ── Header row ───────────────────────────────────────────────────
        header = QHBoxLayout()

        title = QLabel("Detailed Feedback")
        title.setObjectName("AIFeedbackTitle")

        self._ai_source_badge = QLabel("LOCAL ANALYSIS")
        self._ai_source_badge.setObjectName("AISourceBadgeLocal")

        header.addWidget(title)
        header.addStretch()
        header.addWidget(self._ai_source_badge)
        layout.addLayout(header)

        # Progress bar shown while analysis runs
        self._ai_progress = QProgressBar()
        self._ai_progress.setObjectName("AIProgress")
        self._ai_progress.setRange(0, 0)   # indeterminate
        self._ai_progress.setFixedHeight(4)
        self._ai_progress.setTextVisible(False)
        self._ai_progress.setVisible(False)
        layout.addWidget(self._ai_progress)

        # Separator
        sep = QFrame()
        sep.setObjectName("CardSep")
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # Content area — populated by show_ai_feedback()
        self._ai_content_layout = QVBoxLayout()
        self._ai_content_layout.setSpacing(12)
        layout.addLayout(self._ai_content_layout)

        return card

    def show_ai_feedback_loading(self) -> None:
        """Show the card in a loading state while feedback is being generated."""
        self._ai_feedback_card.setVisible(True)
        self._ai_progress.setVisible(True)
        self._ai_source_badge.setText("Generating…")
        self._ai_source_badge.setObjectName("AISourceBadgeLoading")
        # Clear any previous content
        while self._ai_content_layout.count():
            item = self._ai_content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def show_ai_feedback(self, result: "AIFeedbackResult") -> None:
        """
        Populate the AI Feedback card with a completed AIFeedbackResult.

        This is a PUBLIC method — callable from MainWindow, SessionResultsPage,
        and tests.

        The card clearly distinguishes:
        - Objective sections (grammar, vocabulary, filler words):
          derived from transcript text without AI inference
        - AI-generated sections (sentence alternatives, topic feedback,
          personalised advice): require LLM inference, only present when source='llm'

        Args:
            result: :class:`AIFeedbackResult` from FeedbackService.
        """
        c = settings

        # Stop loading spinner
        self._ai_progress.setVisible(False)

        # Clear previous content
        while self._ai_content_layout.count():
            item = self._ai_content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Update source badge
        if result.is_llm:
            self._ai_source_badge.setText(result.source_label)
            self._ai_source_badge.setObjectName("AISourceBadgeLLM")
        else:
            self._ai_source_badge.setText(result.source_label)
            self._ai_source_badge.setObjectName("AISourceBadgeLocal")
        self._ai_source_badge.style().unpolish(self._ai_source_badge)
        self._ai_source_badge.style().polish(self._ai_source_badge)

        # Warning / error banner
        if result.warning:
            self._ai_content_layout.addWidget(
                self._make_banner(f"ℹ️  {result.warning}", "warning")
            )
        if result.error and not result.has_content:
            self._ai_content_layout.addWidget(
                self._make_banner(f"⚠️  {result.error}", "error")
            )
            self._ai_feedback_card.setVisible(True)
            return

        if not result.has_content:
            self._ai_content_layout.addWidget(
                self._make_banner(
                    "No specific issues found — keep up the great work!", "info"
                )
            )
            self._ai_feedback_card.setVisible(True)
            return

        # ── Grammar corrections ──────────────────────────────────────────
        if result.grammar_corrections:
            self._ai_content_layout.addWidget(
                self._make_section_title(
                    "Grammar Notes",
                    badge="Objective",
                    badge_style="objective",
                )
            )
            for correction in result.grammar_corrections:
                frame = QFrame()
                frame.setObjectName("GrammarItem")
                fl = QVBoxLayout(frame)
                fl.setContentsMargins(12, 8, 12, 8)
                fl.setSpacing(4)

                orig = QLabel(f"Found: \"{correction.original}\"")
                orig.setObjectName("GrammarOriginal")
                orig.setWordWrap(True)

                expl = QLabel(correction.explanation)
                expl.setObjectName("GrammarExplanation")
                expl.setWordWrap(True)

                fl.addWidget(orig)
                fl.addWidget(expl)
                self._ai_content_layout.addWidget(frame)

        # ── Vocabulary suggestions ───────────────────────────────────────
        if result.vocabulary_suggestions:
            self._ai_content_layout.addWidget(
                self._make_section_title(
                    "Vocabulary Variety",
                    badge="Objective",
                    badge_style="objective",
                )
            )
            for vs in result.vocabulary_suggestions:
                alts = ", ".join(f"\"{a}\"" for a in vs.alternatives)
                tip = QLabel(
                    f"• \"{vs.word}\" (×{vs.count}) — try: {alts}"
                )
                tip.setObjectName("VocabTip")
                tip.setWordWrap(True)
                self._ai_content_layout.addWidget(tip)

        # ── Filler reduction ─────────────────────────────────────────────
        if result.filler_reduction_tips:
            self._ai_content_layout.addWidget(
                self._make_section_title(
                    "Filler Word Reduction",
                    badge="Objective",
                    badge_style="objective",
                )
            )
            for tip in result.filler_reduction_tips:
                lbl = QLabel(f"• {tip}")
                lbl.setObjectName("FillerTip")
                lbl.setWordWrap(True)
                self._ai_content_layout.addWidget(lbl)

        # ── Repeated word tips ───────────────────────────────────────────
        if result.repeated_word_tips:
            self._ai_content_layout.addWidget(
                self._make_section_title(
                    "Repeated Words",
                    badge="Objective",
                    badge_style="objective",
                )
            )
            for tip in result.repeated_word_tips:
                lbl = QLabel(f"• {tip}")
                lbl.setObjectName("RepeatedTip")
                lbl.setWordWrap(True)
                self._ai_content_layout.addWidget(lbl)

        # ── AI-generated sections (LLM only) ─────────────────────────────
        if result.is_llm:

            if result.better_sentence_alternatives:
                self._ai_content_layout.addWidget(
                    self._make_section_title(
                        "Better Phrasing",
                        badge="AI-Generated",
                        badge_style="ai",
                    )
                )
                for alt in result.better_sentence_alternatives:
                    lbl = QLabel(f"• {alt}")
                    lbl.setObjectName("PhrasingAlt")
                    lbl.setWordWrap(True)
                    self._ai_content_layout.addWidget(lbl)

            if result.topic_specific_feedback:
                self._ai_content_layout.addWidget(
                    self._make_section_title(
                        "Topic-Specific Feedback",
                        badge="AI-Generated",
                        badge_style="ai",
                    )
                )
                for fb in result.topic_specific_feedback:
                    lbl = QLabel(f"• {fb}")
                    lbl.setObjectName("TopicFeedback")
                    lbl.setWordWrap(True)
                    self._ai_content_layout.addWidget(lbl)

            if result.personalized_practice_advice:
                self._ai_content_layout.addWidget(
                    self._make_section_title(
                        "Personalised Practice Advice",
                        badge="AI-Generated",
                        badge_style="ai",
                    )
                )
                for advice in result.personalized_practice_advice:
                    lbl = QLabel(f"• {advice}")
                    lbl.setObjectName("PracticeAdvice")
                    lbl.setWordWrap(True)
                    self._ai_content_layout.addWidget(lbl)

        self._ai_feedback_card.setVisible(True)

    # ── AI card helpers ───────────────────────────────────────────────────

    def _make_section_title(
        self, title: str, badge: str = "", badge_style: str = "objective"
    ) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)

        lbl = QLabel(title)
        lbl.setObjectName("AISubTitle")
        layout.addWidget(lbl)

        if badge:
            bdg = QLabel(badge)
            obj_name = {
                "objective": "BadgeObjective",
                "ai":        "BadgeAI",
            }.get(badge_style, "BadgeObjective")
            bdg.setObjectName(obj_name)
            layout.addWidget(bdg)

        layout.addStretch()
        return row

    def _make_banner(self, message: str, style: str) -> QLabel:
        lbl = QLabel(message)
        lbl.setWordWrap(True)
        obj = {
            "warning": "BannerWarning",
            "error":   "BannerError",
            "info":    "BannerInfo",
        }.get(style, "BannerInfo")
        lbl.setObjectName(obj)
        return lbl

    # ------------------------------------------------------------------ #
    # Styles                                                                #
    # ------------------------------------------------------------------ #

    def _apply_styles(self) -> None:
        c = settings
        self.setStyleSheet(f"""
            #SessionResultsPage {{
                background-color: {c.COLOR_BG_DARK};
            }}
            #ResultsScroll, #ResultsContent {{
                background-color: {c.COLOR_BG_DARK};
            }}

            /* ── Header ── */
            #ResultsPageTitle {{
                font-size: 22px;
                font-weight: 700;
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #ResultsTopicLabel {{
                font-size: 13px;
                font-weight: 500;
                color: {c.COLOR_TEXT_SECONDARY};
                background-color: rgba(108,99,255,0.10);
                border: 1px solid rgba(108,99,255,0.25);
                border-radius: 20px;
                padding: 4px 14px;
            }}
            #ResultsDurationLabel {{
                font-size: 13px;
                font-weight: 500;
                color: {c.COLOR_TEXT_SECONDARY};
                background-color: rgba(255,255,255,0.05);
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 20px;
                padding: 4px 14px;
            }}

            /* ── Overall card ── */
            #OverallCard {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 16px;
            }}
            #OverallScoreNumber {{
                font-size: 72px;
                font-weight: 700;
                color: {c.COLOR_SUCCESS};
            }}
            #OverallDenom {{
                font-size: 18px;
                color: {c.COLOR_TEXT_MUTED};
                margin-top: -8px;
            }}
            #OverallLabel {{
                font-size: 18px;
                font-weight: 600;
                color: {c.COLOR_TEXT_SECONDARY};
                margin-top: 2px;
            }}

            /* ── Score breakdown card ── */
            #BreakdownCard {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 14px;
            }}
            #ScoreCard {{
                background-color: transparent;
                border-bottom: 1px solid {c.COLOR_BORDER};
                border-radius: 0px;
            }}
            #ScoreCardName {{
                font-size: 14px;
                font-weight: 500;
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #ScoreCardValue {{
                font-size: 18px;
                font-weight: 700;
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #ScoreCardDesc {{
                font-size: 12px;
                color: {c.COLOR_TEXT_MUTED};
            }}

            /* ── Section titles ── */
            #SectionTitle {{
                font-size: 14px;
                font-weight: 600;
                color: {c.COLOR_TEXT_PRIMARY};
                margin-bottom: 4px;
            }}
            #CardSep {{
                background-color: {c.COLOR_BORDER};
                color: {c.COLOR_BORDER};
            }}

            /* ── Metrics card ── */
            #MetricsCard {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 14px;
            }}
            #MetricChip {{
                background-color: {c.COLOR_BG_DARK};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 10px;
            }}
            #MetricChipHeader {{
                font-size: 11px;
                font-weight: 500;
                color: {c.COLOR_TEXT_MUTED};
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }}
            #MetricChipValue {{
                font-size: 18px;
                font-weight: 700;
                color: {c.COLOR_TEXT_PRIMARY};
            }}

            /* ── Feedback ── */
            #FeedbackCard {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 14px;
            }}
            #FeedbackContainer {{
                background-color: transparent;
                border: none;
            }}
            #FBStrengthsTitle {{
                font-size: 14px;
                font-weight: 600;
                color: {c.COLOR_SUCCESS};
            }}
            #FBImprovesTitle {{
                font-size: 14px;
                font-weight: 600;
                color: {c.COLOR_WARNING};
            }}
            #FBSuggestsTitle {{
                font-size: 14px;
                font-weight: 600;
                color: {c.COLOR_ACCENT};
            }}
            #FBBullet {{
                font-size: 16px;
                color: {c.COLOR_TEXT_MUTED};
                font-weight: 700;
            }}
            #FBItem {{
                font-size: 13px;
                color: {c.COLOR_TEXT_SECONDARY};
                line-height: 1.5;
            }}

            /* ── Transcript accordion ── */
            #TranscriptAccordion, #TranscriptAccordionCard {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 12px;
            }}
            #TranscriptContainer {{
                background-color: transparent;
                border: none;
            }}
            #TranscriptToggleBtn {{
                background-color: transparent;
                color: {c.COLOR_TEXT_SECONDARY};
                border: none;
                border-radius: 10px;
                text-align: left;
                font-size: 14px;
                font-weight: 500;
                padding: 0px 16px;
            }}
            #TranscriptToggleBtn:hover {{
                background-color: rgba(255,255,255,0.04);
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #TranscriptToggleBtn:checked {{
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #TranscriptBody {{
                background-color: rgba(255,255,255,0.02);
                color: {c.COLOR_TEXT_SECONDARY};
                border: none;
                border-top: 1px solid {c.COLOR_BORDER};
                border-radius: 0px;
                padding: 16px;
                font-size: 13px;
                line-height: 1.7;
                selection-background-color: {c.COLOR_ACCENT};
            }}

            /* ── Navigation buttons ── */
            #PracticeAgainBtn {{
                background-color: {c.COLOR_ACCENT};
                color: #FFFFFF;
                border: none;
                border-radius: 10px;
                font-size: 15px;
                font-weight: 600;
            }}
            #PracticeAgainBtn:hover {{
                background-color: {c.COLOR_ACCENT_HOVER};
            }}
            #DashboardBtn {{
                background-color: {c.COLOR_BG_CARD};
                color: {c.COLOR_TEXT_PRIMARY};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 10px;
                font-size: 15px;
                font-weight: 600;
            }}
            #DashboardBtn:hover {{
                border-color: {c.COLOR_TEXT_SECONDARY};
                background-color: rgba(255,255,255,0.05);
            }}

            /* ── AI Feedback card ── */
            #AIFeedbackCard {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid rgba(108, 99, 255, 0.30);
                border-radius: 14px;
            }}
            #AIFeedbackTitle {{
                font-size: 15px;
                font-weight: 600;
                color: {c.COLOR_TEXT_PRIMARY};
            }}

            /* Source badges — MUST be visually distinct */
            #AISourceBadgeLocal {{
                font-size: 10px;
                font-weight: 700;
                color: {c.COLOR_TEXT_SECONDARY};
                background-color: rgba(139,143,168,0.15);
                border: 1px solid rgba(139,143,168,0.35);
                border-radius: 10px;
                padding: 2px 8px;
                letter-spacing: 0.5px;
            }}
            #AISourceBadgeLLM {{
                font-size: 10px;
                font-weight: 700;
                color: #FFFFFF;
                background-color: rgba(108,99,255,0.65);
                border: 1px solid {c.COLOR_ACCENT};
                border-radius: 10px;
                padding: 2px 8px;
                letter-spacing: 0.5px;
            }}
            #AISourceBadgeLoading {{
                font-size: 10px;
                font-weight: 700;
                color: {c.COLOR_TEXT_MUTED};
                background-color: rgba(255,255,255,0.04);
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 10px;
                padding: 2px 8px;
            }}
            QProgressBar#AIProgress {{
                background-color: {c.COLOR_BORDER};
                border: none;
                border-radius: 2px;
            }}
            QProgressBar#AIProgress::chunk {{
                background-color: {c.COLOR_ACCENT};
                border-radius: 2px;
            }}

            #AISubTitle {{
                font-size: 13px;
                font-weight: 600;
                color: {c.COLOR_TEXT_PRIMARY};
            }}

            /* Section badges */
            #BadgeObjective {{
                font-size: 9px;
                font-weight: 700;
                color: {c.COLOR_SUCCESS};
                background-color: rgba(76,175,80,0.12);
                border: 1px solid rgba(76,175,80,0.30);
                border-radius: 8px;
                padding: 1px 7px;
                letter-spacing: 0.4px;
            }}
            #BadgeAI {{
                font-size: 9px;
                font-weight: 700;
                color: {c.COLOR_ACCENT};
                background-color: rgba(108,99,255,0.12);
                border: 1px solid rgba(108,99,255,0.30);
                border-radius: 8px;
                padding: 1px 7px;
                letter-spacing: 0.4px;
            }}

            /* Content items */
            #GrammarItem {{
                background-color: rgba(244,67,54,0.06);
                border: 1px solid rgba(244,67,54,0.18);
                border-radius: 8px;
            }}
            #GrammarOriginal {{
                font-size: 12px;
                font-weight: 600;
                color: {c.COLOR_DANGER};
            }}
            #GrammarExplanation {{
                font-size: 12px;
                color: {c.COLOR_TEXT_SECONDARY};
            }}
            #VocabTip, #FillerTip, #RepeatedTip,
            #PhrasingAlt, #TopicFeedback, #PracticeAdvice {{
                font-size: 13px;
                color: {c.COLOR_TEXT_SECONDARY};
                padding: 2px 0;
            }}

            /* Banners */
            #BannerInfo {{
                font-size: 13px;
                color: {c.COLOR_SUCCESS};
                background-color: rgba(76,175,80,0.08);
                border: 1px solid rgba(76,175,80,0.20);
                border-radius: 8px;
                padding: 8px 12px;
            }}
            #BannerWarning {{
                font-size: 13px;
                color: {c.COLOR_WARNING};
                background-color: rgba(255,152,0,0.08);
                border: 1px solid rgba(255,152,0,0.25);
                border-radius: 8px;
                padding: 8px 12px;
            }}
            #BannerError {{
                font-size: 13px;
                color: {c.COLOR_DANGER};
                background-color: rgba(244,67,54,0.08);
                border: 1px solid rgba(244,67,54,0.25);
                border-radius: 8px;
                padding: 8px 12px;
            }}
        """)
