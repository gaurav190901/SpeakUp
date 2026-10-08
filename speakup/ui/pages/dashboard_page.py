"""
DashboardPage — complete progress dashboard.

Layout
──────
┌─ Header + Date Filter ──────────────────────────────────────────────────┐
│  Progress Dashboard      [7 days] [30 days] [90 days] [All time]        │
└─────────────────────────────────────────────────────────────────────────┘

┌─ Stat Cards (2 × 3 grid) ───────────────────────────────────────────────┐
│  Sessions  │  Practice Time  │  Avg Confidence  (72→79  +7)             │
│  Overall   │  Avg Fluency    │  Avg Vocabulary                           │
└─────────────────────────────────────────────────────────────────────────┘

┌─ Charts (3 × 2 grid) ───────────────────────────────────────────────────┐
│  Confidence over sessions  │  Overall score over sessions               │
│  Fluency over sessions     │  Speaking duration over sessions           │
│  Words per minute          │  Filler words over sessions                │
└─────────────────────────────────────────────────────────────────────────┘

┌─ Recent Sessions ───────────────────────────────────────────────────────┐
│  Date │ Topic │ Duration │ Score │ Confidence │ View                    │
└─────────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
from typing import Callable

import matplotlib
matplotlib.use("QtAgg")   # must be set before other matplotlib imports
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from speakup.config.settings import settings
from speakup.services.progress_service import ProgressService
from speakup.services.gamification_service import GamificationService
from speakup.services.dashboard_mapper import (
    map_stat_cards,
    map_delta,
    map_chart_series,
    map_recent_sessions,
)

logger = logging.getLogger(__name__)

# Matplotlib dark theme colours
_BG     = "#1A1D27"
_FG     = "#EAEAEA"
_GRID   = "#252837"
_ACCENT = "#6C63FF"

_DATE_RANGE_LABELS = [
    ("7d",  "7 days"),
    ("30d", "30 days"),
    ("90d", "90 days"),
    ("all", "All time"),
]


# ===========================================================================
# Reusable sub-widgets
# ===========================================================================

class _StatCard(QFrame):
    """Stat card with title, large value, subtitle, and optional delta."""

    def __init__(
        self,
        title: str,
        accent: str = settings.COLOR_ACCENT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("StatCard")
        self._accent = accent
        self._build(title)
        self._apply_style()

    def _build(self, title: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(4)

        self._title_lbl = QLabel(title)
        self._title_lbl.setObjectName("CardTitle")

        self._value_lbl = QLabel("—")
        self._value_lbl.setObjectName("CardValue")
        self._value_lbl.setStyleSheet(f"color: {self._accent}; font-size: 30px; font-weight: 700;")

        self._sub_lbl = QLabel("")
        self._sub_lbl.setObjectName("CardSubtitle")

        # Improvement delta row (hidden until data available)
        self._delta_row = QHBoxLayout()
        self._delta_row.setSpacing(6)

        self._delta_from = QLabel("")
        self._delta_from.setObjectName("DeltaFrom")

        self._delta_arrow = QLabel("→")
        self._delta_arrow.setObjectName("DeltaArrow")

        self._delta_to = QLabel("")
        self._delta_to.setObjectName("DeltaTo")

        self._delta_badge = QLabel("")
        self._delta_badge.setObjectName("DeltaBadge")

        for w in (self._delta_from, self._delta_arrow,
                  self._delta_to, self._delta_badge):
            self._delta_row.addWidget(w)
            w.setVisible(False)
        self._delta_row.addStretch()

        layout.addWidget(self._title_lbl)
        layout.addWidget(self._value_lbl)
        layout.addWidget(self._sub_lbl)
        layout.addLayout(self._delta_row)
        layout.addStretch()

    def update_data(
        self,
        value: str,
        subtitle: str = "",
        delta: dict | None = None,
    ) -> None:
        self._value_lbl.setText(value)
        self._sub_lbl.setText(subtitle)

        if delta:
            colour = settings.COLOR_SUCCESS if delta["positive"] else settings.COLOR_DANGER
            self._delta_from.setText(delta["first_str"])
            self._delta_arrow.setText("→")
            self._delta_to.setText(delta["last_str"])
            self._delta_badge.setText(delta["delta_str"])
            self._delta_badge.setStyleSheet(
                f"color: {colour}; font-weight: 700; font-size: 12px;"
            )
            for w in (self._delta_from, self._delta_arrow,
                      self._delta_to, self._delta_badge):
                w.setVisible(True)
        else:
            for w in (self._delta_from, self._delta_arrow,
                      self._delta_to, self._delta_badge):
                w.setVisible(False)

    def _apply_style(self) -> None:
        self.setStyleSheet(f"""
            #StatCard {{
                background-color: {settings.COLOR_BG_CARD};
                border: 1px solid {settings.COLOR_BORDER};
                border-radius: 12px;
            }}
            #CardTitle {{
                font-size: 11px;
                font-weight: 600;
                color: {settings.COLOR_TEXT_MUTED};
                text-transform: uppercase;
                letter-spacing: 0.8px;
            }}
            #CardSubtitle {{
                font-size: 12px;
                color: {settings.COLOR_TEXT_MUTED};
            }}
            #DeltaFrom, #DeltaTo {{
                font-size: 12px;
                color: {settings.COLOR_TEXT_SECONDARY};
            }}
            #DeltaArrow {{
                font-size: 11px;
                color: {settings.COLOR_TEXT_MUTED};
            }}
        """)


class _MplChartWidget(QWidget):
    """
    Embeds a single matplotlib Figure in a QWidget.

    Usage:
        chart = _MplChartWidget("Confidence", height_px=200)
        chart.plot(x, y, colour="#6C63FF", ylabel="Score")
    """

    def __init__(
        self,
        title: str,
        height_px: int = 210,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ChartWidget")
        self._title = title

        self._fig = Figure(figsize=(4, 2.1), dpi=90)
        self._ax = self._fig.add_subplot(111)
        self._fig.patch.set_facecolor(_BG)
        self._ax.set_facecolor(_BG)

        self._canvas = FigureCanvasQTAgg(self._fig)
        self._canvas.setMinimumHeight(height_px)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 10)
        layout.setSpacing(6)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("ChartTitle")
        layout.addWidget(title_lbl)
        layout.addWidget(self._canvas)

        self.setStyleSheet(f"""
            #ChartWidget {{
                background-color: {_BG};
                border: 1px solid {settings.COLOR_BORDER};
                border-radius: 12px;
            }}
            #ChartTitle {{
                font-size: 13px;
                font-weight: 600;
                color: {settings.COLOR_TEXT_PRIMARY};
            }}
        """)

    def plot(
        self,
        x: list,
        y: list,
        colour: str = _ACCENT,
        ylabel: str = "",
        y_max: float | None = 100.0,
        marker: str = "o",
        fill: bool = True,
        integer_y: bool = False,
    ) -> None:
        """Render the chart.  Clears previous data before drawing."""
        self._ax.clear()

        if not x or not y or all(v is None for v in y):
            self._draw_empty()
            return

        # Replace None with 0 for rendering
        y_clean = [v if v is not None else 0 for v in y]

        self._ax.plot(
            x, y_clean,
            colour,
            linewidth=2,
            marker=marker,
            markersize=4 if len(x) <= 20 else 0,
        )
        if fill and len(x) > 1:
            self._ax.fill_between(x, y_clean, alpha=0.12, color=colour)

        # Styling
        self._ax.set_facecolor(_BG)
        self._ax.tick_params(colors=_FG, labelsize=8)
        for spine in self._ax.spines.values():
            spine.set_color(_GRID)
        self._ax.grid(axis="y", color=_GRID, linewidth=0.6, alpha=0.7)
        if ylabel:
            self._ax.set_ylabel(ylabel, color=settings.COLOR_TEXT_MUTED, fontsize=8)
        if y_max is not None:
            self._ax.set_ylim(0, y_max)
        if integer_y:
            self._ax.yaxis.set_major_formatter(
                matplotlib.ticker.FuncFormatter(lambda v, _: str(int(v)))
            )
        self._ax.set_xlim(left=0.5)

        self._fig.tight_layout(pad=0.6)
        self._canvas.draw()

    def _draw_empty(self) -> None:
        self._ax.clear()
        self._ax.set_facecolor(_BG)
        self._ax.text(
            0.5, 0.5, "No data yet",
            transform=self._ax.transAxes,
            ha="center", va="center",
            color=settings.COLOR_TEXT_MUTED, fontsize=9,
        )
        for spine in self._ax.spines.values():
            spine.set_color(_GRID)
        self._fig.tight_layout(pad=0.6)
        self._canvas.draw()


# ===========================================================================
# DashboardPage
# ===========================================================================

class DashboardPage(QWidget):
    """
    Full progress dashboard.

    Emits view_session(session_id) when the user clicks "View" in the
    Recent Sessions table — MainWindow handles navigation.
    """

    view_session = Signal(int)   # session_id

    def __init__(self, user_id: int = 1, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._user_id      = user_id
        self._date_range   = "all"
        self._svc          = ProgressService()
        self._gam_svc      = GamificationService()

        self._build_ui()
        self._apply_page_styles()
        QTimer.singleShot(150, self._load_all)

    # ------------------------------------------------------------------ #
    # UI construction                                                       #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setObjectName("DashboardScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("DashboardContent")
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(32, 28, 32, 36)
        self._layout.setSpacing(20)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── Header + filter ───────────────────────────────────────────────
        self._build_header()

        # ── Streak + Goal progress bar ────────────────────────────────────
        self._build_streak_goal_row()

        # ── Stat cards (2 × 3) ───────────────────────────────────────────
        self._build_stat_cards()

        # ── Charts (3 × 2) ───────────────────────────────────────────────
        charts_title = QLabel("Progress Over Sessions")
        charts_title.setObjectName("SectionTitle")
        self._layout.addWidget(charts_title)
        self._build_charts()

        # ── Recent sessions ───────────────────────────────────────────────
        recent_title = QLabel("Recent Sessions")
        recent_title.setObjectName("SectionTitle")
        self._layout.addWidget(recent_title)

        self._sessions_container = QVBoxLayout()
        self._sessions_container.setSpacing(6)
        self._layout.addLayout(self._sessions_container)

        # ── Achievements row ──────────────────────────────────────────────
        ach_title = QLabel("Achievements")
        ach_title.setObjectName("SectionTitle")
        self._layout.addWidget(ach_title)
        self._achievements_container = QHBoxLayout()
        self._achievements_container.setSpacing(10)
        self._layout.addLayout(self._achievements_container)

        self._layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _build_streak_goal_row(self) -> None:
        """Streak chip + daily goal progress bar on one compact row."""
        from PySide6.QtWidgets import QProgressBar  # noqa: PLC0415

        row = QHBoxLayout()
        row.setSpacing(14)

        # ── Streak chip ───────────────────────────────────────────────────
        streak_card = QFrame()
        streak_card.setObjectName("StreakCard")
        streak_layout = QHBoxLayout(streak_card)
        streak_layout.setContentsMargins(16, 12, 16, 12)
        streak_layout.setSpacing(8)

        self._streak_icon = QLabel("🔥")
        self._streak_icon.setObjectName("StreakIcon")

        self._streak_label = QLabel("—")
        self._streak_label.setObjectName("StreakLabel")

        streak_layout.addWidget(self._streak_icon)
        streak_layout.addWidget(self._streak_label)
        streak_layout.addStretch()
        row.addWidget(streak_card, stretch=1)

        # ── Personal best chip ────────────────────────────────────────────
        pb_card = QFrame()
        pb_card.setObjectName("PBCard")
        pb_layout = QHBoxLayout(pb_card)
        pb_layout.setContentsMargins(16, 12, 16, 12)
        pb_layout.setSpacing(8)

        pb_icon = QLabel("⭐")
        pb_icon.setObjectName("PBIcon")

        self._pb_label = QLabel("Personal Best: —")
        self._pb_label.setObjectName("PBLabel")

        pb_layout.addWidget(pb_icon)
        pb_layout.addWidget(self._pb_label)
        pb_layout.addStretch()
        row.addWidget(pb_card, stretch=1)

        self._layout.addLayout(row)

        # ── Today's goal progress ─────────────────────────────────────────
        goal_card = QFrame()
        goal_card.setObjectName("GoalCard")
        goal_layout = QVBoxLayout(goal_card)
        goal_layout.setContentsMargins(20, 14, 20, 14)
        goal_layout.setSpacing(6)

        goal_header = QHBoxLayout()
        goal_title = QLabel("Today's Goal")
        goal_title.setObjectName("GoalTitle")
        self._goal_label = QLabel("—")
        self._goal_label.setObjectName("GoalStatusLabel")
        self._goal_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        goal_header.addWidget(goal_title)
        goal_header.addStretch()
        goal_header.addWidget(self._goal_label)
        goal_layout.addLayout(goal_header)

        self._goal_bar = QProgressBar()
        self._goal_bar.setObjectName("GoalProgressBar")
        self._goal_bar.setRange(0, 100)
        self._goal_bar.setValue(0)
        self._goal_bar.setTextVisible(False)
        self._goal_bar.setFixedHeight(8)
        goal_layout.addWidget(self._goal_bar)

        self._goal_sublabel = QLabel("")
        self._goal_sublabel.setObjectName("GoalSubLabel")
        goal_layout.addWidget(self._goal_sublabel)

        self._layout.addWidget(goal_card)

    def _build_header(self) -> None:
        row = QHBoxLayout()

        title = QLabel("Progress Dashboard")
        title.setObjectName("PageTitle")

        row.addWidget(title)
        row.addStretch()

        # Date filter buttons
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        self._filter_btns: dict[str, QPushButton] = {}

        for key, label in _DATE_RANGE_LABELS:
            btn = QPushButton(label)
            btn.setObjectName("FilterBtn")
            btn.setCheckable(True)
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, k=key: self._on_filter(k))
            self._filter_group.addButton(btn)
            self._filter_btns[key] = btn
            row.addWidget(btn)

        self._filter_btns["all"].setChecked(True)
        self._layout.addLayout(row)

        subtitle = QLabel("Your speaking practice overview")
        subtitle.setObjectName("PageSubtitle")
        self._layout.addWidget(subtitle)

    def _build_stat_cards(self) -> None:
        CARDS = [
            ("sessions",    "Speaking Sessions", settings.COLOR_ACCENT),
            ("time",        "Practice Time",     "#4CAF50"),
            ("confidence",  "Avg Confidence",    "#FF9800"),
            ("overall",     "Avg Overall Score", "#2196F3"),
            ("fluency",     "Avg Fluency",        "#00BCD4"),
            ("vocabulary",  "Avg Vocabulary",     "#9C27B0"),
        ]
        grid = QGridLayout()
        grid.setSpacing(14)
        self._stat_cards: dict[str, _StatCard] = {}
        for i, (key, title, colour) in enumerate(CARDS):
            card = _StatCard(title, accent=colour)
            self._stat_cards[key] = card
            grid.addWidget(card, i // 3, i % 3)
        self._layout.addLayout(grid)

    def _build_charts(self) -> None:
        CHART_DEFS = [
            ("confidence", "Confidence",           _ACCENT,   True,  False),
            ("overall",    "Overall Score",         "#2196F3", True,  False),
            ("fluency",    "Fluency",               "#00BCD4", True,  False),
            ("duration",   "Duration (min)",        "#4CAF50", False, False),
            ("wpm",        "Words per Minute",      "#FF9800", False, False),
            ("fillers",    "Filler Words (count)",  "#F44336", False, True),
        ]
        grid = QGridLayout()
        grid.setSpacing(14)
        self._charts: dict[str, _MplChartWidget] = {}
        for i, (key, title, colour, score_axis, is_int) in enumerate(CHART_DEFS):
            chart = _MplChartWidget(title, height_px=190)
            chart._colour   = colour       # type: ignore[attr-defined]
            chart._score_ax = score_axis   # type: ignore[attr-defined]
            chart._int_y    = is_int       # type: ignore[attr-defined]
            self._charts[key] = chart
            grid.addWidget(chart, i // 2, i % 2)
        self._layout.addLayout(grid)

    # ------------------------------------------------------------------ #
    # Data loading                                                          #
    # ------------------------------------------------------------------ #

    def _load_all(self) -> None:
        self._load_stats()
        self._load_charts()
        self._load_recent_sessions()
        self._load_gamification()

    def _load_stats(self) -> None:
        try:
            stats    = self._svc.get_dashboard_stats(self._user_id, self._date_range)
            deltas   = self._svc.get_improvement_delta(self._user_id, date_range=self._date_range)
            delta_ui = map_delta(deltas)
            cards    = map_stat_cards(stats)

            for card_data in cards:
                key  = card_data["key"]
                card = self._stat_cards.get(key)
                if not card:
                    continue
                card.update_data(
                    value    = card_data["value"],
                    subtitle = card_data["subtitle"],
                    delta    = delta_ui.get(key),
                )
        except Exception as exc:
            logger.error("Failed to load dashboard stats: %s", exc)

    def _load_charts(self) -> None:
        try:
            raw    = self._svc.get_chart_data(self._user_id, self._date_range)
            series = map_chart_series(raw)

            if not series.get("has_data"):
                for chart in self._charts.values():
                    chart._draw_empty()
                return

            x = series["x"]
            mapping = {
                "confidence": ("confidence", None, 100.0),
                "overall":    ("overall",    None, 100.0),
                "fluency":    ("fluency",    None, 100.0),
                "duration":   ("duration_min", "min", None),
                "wpm":        ("wpm",        "WPM", None),
                "fillers":    ("filler_counts", "count", None),
            }
            for key, (data_key, ylabel, y_max) in mapping.items():
                chart = self._charts.get(key)
                if chart:
                    chart.plot(
                        x, series[data_key],
                        colour   = chart._colour,    # type: ignore[attr-defined]
                        ylabel   = ylabel or "",
                        y_max    = y_max,
                        fill     = True,
                        integer_y= chart._int_y,     # type: ignore[attr-defined]
                    )
        except Exception as exc:
            logger.error("Failed to load charts: %s", exc)

    def _load_recent_sessions(self) -> None:
        # Clear old rows
        while self._sessions_container.count():
            item = self._sessions_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            raw_rows = self._svc.get_rich_session_history(
                self._user_id, date_range=self._date_range, limit=20
            )
            rows = map_recent_sessions(raw_rows)

            if not rows:
                empty = QLabel("No sessions yet in this time range.")
                empty.setObjectName("EmptyLabel")
                empty.setStyleSheet(
                    f"color: {settings.COLOR_TEXT_MUTED}; font-size: 13px; padding: 12px 0;"
                )
                self._sessions_container.addWidget(empty)
                return

            # Header row
            self._sessions_container.addWidget(self._make_table_header())

            for row_data in rows:
                row_widget = self._make_session_row(row_data)
                self._sessions_container.addWidget(row_widget)

        except Exception as exc:
            logger.error("Failed to load recent sessions: %s", exc)

    # ------------------------------------------------------------------ #
    # Recent sessions table                                                 #
    # ------------------------------------------------------------------ #

    def _make_table_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("TableHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(0)

        cols = [("Date", 110), ("Topic", 0), ("Duration", 80),
                ("Score", 72), ("Confidence", 90), ("", 60)]
        for label, width in cols:
            lbl = QLabel(label)
            lbl.setObjectName("HeaderLabel")
            if width:
                lbl.setFixedWidth(width)
            else:
                lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            layout.addWidget(lbl)
        return header

    def _make_session_row(self, row: dict) -> QFrame:
        """One row in the Recent Sessions table."""
        frame = QFrame()
        frame.setObjectName("SessionRow")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(0)

        def _cell(text: str, width: int | None = None, bold: bool = False) -> QLabel:
            lbl = QLabel(text)
            lbl.setObjectName("CellLabel")
            if width:
                lbl.setFixedWidth(width)
            else:
                lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if bold:
                lbl.setStyleSheet("font-weight: 600;")
            return lbl

        layout.addWidget(_cell(row["date_str"], 110))
        layout.addWidget(_cell(row["word"], bold=True))
        layout.addWidget(_cell(row["duration_str"], 80))

        # Coloured score
        score_lbl = _cell(row["overall_str"], 72)
        if row.get("overall_score") is not None:
            from speakup.utils.helpers import score_colour
            score_lbl.setStyleSheet(
                f"color: {score_colour(row['overall_score'])}; font-weight: 600;"
            )
        layout.addWidget(score_lbl)

        conf_lbl = _cell(row["confidence_str"], 90)
        if row.get("confidence_score") is not None:
            from speakup.utils.helpers import score_colour
            conf_lbl.setStyleSheet(
                f"color: {score_colour(row['confidence_score'])};"
            )
        layout.addWidget(conf_lbl)

        # View button
        view_btn = QPushButton("View")
        view_btn.setObjectName("ViewBtn")
        view_btn.setFixedSize(60, 28)
        view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sid = row["session_id"]
        view_btn.clicked.connect(lambda checked, r=row: self._on_view_session(r))
        layout.addWidget(view_btn)

        return frame

    def _on_view_session(self, row: dict) -> None:
        """User clicked View — emit signal so MainWindow can navigate."""
        logger.info("View session requested: session_id=%s", row["session_id"])
        self._current_view_row = row
        self.view_session.emit(row["session_id"])

    def get_current_view_row(self) -> dict | None:
        """Return the row last clicked for View (used by MainWindow to prepare results page)."""
        return getattr(self, "_current_view_row", None)

    # ------------------------------------------------------------------ #
    # Filter                                                                #
    # ------------------------------------------------------------------ #

    def _load_gamification(self) -> None:
        """Fetch streak, goal, personal best, and achievements from DB."""
        try:
            snap = self._gam_svc.get_snapshot(self._user_id)

            # ── Streak ────────────────────────────────────────────────────
            self._streak_label.setText(snap.streak.streak_label)

            # ── Personal best ─────────────────────────────────────────────
            pb = snap.personal_best.best_overall_score
            pb_text = f"Personal Best: {int(pb)}/100" if pb is not None else "Personal Best: —"
            self._pb_label.setText(pb_text)

            # ── Today's goal ──────────────────────────────────────────────
            goal = snap.daily_goal
            pct  = int(goal.progress_pct * 100)
            self._goal_bar.setValue(pct)
            colour = settings.COLOR_SUCCESS if goal.completed else settings.COLOR_ACCENT
            self._goal_bar.setStyleSheet(f"""
                QProgressBar#GoalProgressBar {{
                    background-color: {settings.COLOR_BORDER};
                    border: none; border-radius: 4px;
                }}
                QProgressBar#GoalProgressBar::chunk {{
                    background-color: {colour};
                    border-radius: 4px;
                }}
            """)
            self._goal_label.setText(
                "✅  Complete!" if goal.completed else f"{pct}%"
            )
            self._goal_sublabel.setText(goal.display_text)

            # ── Achievements ──────────────────────────────────────────────
            self._load_achievements(snap.achievements)

        except Exception as exc:
            logger.error("Failed to load gamification: %s", exc)

    def _load_achievements(self, achievements: list) -> None:
        """Populate the achievements row with badge chips."""
        while self._achievements_container.count():
            item = self._achievements_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not achievements:
            empty = QLabel("Complete sessions to earn achievements.")
            empty.setObjectName("EmptyLabel")
            self._achievements_container.addWidget(empty)
            self._achievements_container.addStretch()
            return

        # Show up to 8 achievements (unlocked first, then locked)
        unlocked = [a for a in achievements if a.unlocked]
        locked   = [a for a in achievements if not a.unlocked]
        visible  = (unlocked + locked)[:8]

        for ach in visible:
            chip = self._make_achievement_chip(ach)
            self._achievements_container.addWidget(chip)
        self._achievements_container.addStretch()

    def _make_achievement_chip(self, ach) -> QFrame:
        chip = QFrame()
        chip.setObjectName("AchievementChipUnlocked" if ach.unlocked else "AchievementChipLocked")
        chip.setFixedSize(84, 84)
        layout = QVBoxLayout(chip)
        layout.setContentsMargins(6, 8, 6, 8)
        layout.setSpacing(3)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel(ach.icon)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            "font-size: 22px;" if ach.unlocked else "font-size: 22px; opacity: 0.3;"
        )

        title = QLabel(ach.title)
        title.setObjectName("AchievementTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setWordWrap(True)

        layout.addWidget(icon)
        layout.addWidget(title)
        chip.setToolTip(ach.description)
        return chip

    def _on_filter(self, date_range: str) -> None:
        if date_range == self._date_range:
            return
        self._date_range = date_range
        self._filter_btns[date_range].setChecked(True)
        QTimer.singleShot(0, self._load_all)

    def refresh(self) -> None:
        """Reload all dashboard data — called when navigating here."""
        QTimer.singleShot(50, self._load_all)

    # ------------------------------------------------------------------ #
    # Styles                                                                #
    # ------------------------------------------------------------------ #

    def _apply_page_styles(self) -> None:
        c = settings
        self.setStyleSheet(f"""
            #DashboardScroll, #DashboardContent {{
                background-color: {c.COLOR_BG_DARK};
            }}
            #PageTitle {{
                font-size: 22px;
                font-weight: 700;
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #PageSubtitle {{
                font-size: 13px;
                color: {c.COLOR_TEXT_SECONDARY};
                margin-top: -4px;
            }}
            #SectionTitle {{
                font-size: 15px;
                font-weight: 600;
                color: {c.COLOR_TEXT_PRIMARY};
                margin-top: 4px;
            }}

            /* Date filter buttons */
            #FilterBtn {{
                background-color: transparent;
                color: {c.COLOR_TEXT_SECONDARY};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 16px;
                padding: 0px 14px;
                font-size: 12px;
                font-weight: 500;
            }}
            #FilterBtn:hover {{
                border-color: {c.COLOR_ACCENT};
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #FilterBtn:checked {{
                background-color: {c.COLOR_ACCENT};
                color: #FFFFFF;
                border-color: {c.COLOR_ACCENT};
                font-weight: 600;
            }}

            /* Table */
            #TableHeader {{
                background-color: transparent;
            }}
            #HeaderLabel {{
                font-size: 11px;
                font-weight: 600;
                color: {c.COLOR_TEXT_MUTED};
                text-transform: uppercase;
                letter-spacing: 0.6px;
            }}
            #SessionRow {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 8px;
            }}
            #SessionRow:hover {{
                border-color: {c.COLOR_ACCENT};
            }}
            #CellLabel {{
                font-size: 13px;
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #ViewBtn {{
                background-color: transparent;
                color: {c.COLOR_ACCENT};
                border: 1px solid {c.COLOR_ACCENT};
                border-radius: 6px;
                font-size: 12px;
                font-weight: 600;
            }}
            #ViewBtn:hover {{
                background-color: rgba(108,99,255,0.12);
            }}
            #EmptyLabel {{
                color: {c.COLOR_TEXT_MUTED};
            }}

            /* ── Gamification widgets ── */
            #StreakCard, #PBCard {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 12px;
            }}
            #StreakIcon, #PBIcon {{ font-size: 20px; }}
            #StreakLabel {{
                font-size: 15px;
                font-weight: 600;
                color: {c.COLOR_WARNING};
            }}
            #PBLabel {{
                font-size: 14px;
                font-weight: 500;
                color: {c.COLOR_TEXT_SECONDARY};
            }}
            #GoalCard {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 12px;
            }}
            #GoalTitle {{
                font-size: 13px;
                font-weight: 600;
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #GoalStatusLabel {{
                font-size: 13px;
                font-weight: 600;
                color: {c.COLOR_ACCENT};
            }}
            #GoalSubLabel {{
                font-size: 12px;
                color: {c.COLOR_TEXT_MUTED};
            }}
            #AchievementChipUnlocked {{
                background-color: rgba(108,99,255,0.10);
                border: 1px solid rgba(108,99,255,0.30);
                border-radius: 10px;
            }}
            #AchievementChipLocked {{
                background-color: rgba(255,255,255,0.03);
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 10px;
                opacity: 0.5;
            }}
            #AchievementTitle {{
                font-size: 9px;
                font-weight: 500;
                color: {c.COLOR_TEXT_SECONDARY};
                text-align: center;
            }}
        """)
