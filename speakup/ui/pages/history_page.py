"""
HistoryPage — list of past speaking sessions with clickable rows.

Each row shows Date | Topic | Duration | Score with a View button
that navigates to the full SessionResultsPage.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
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
from speakup.services.progress_service import ProgressService
from speakup.utils.helpers import format_duration, format_datetime, format_score, score_colour

logger = logging.getLogger(__name__)


class HistoryPage(QWidget):
    """Session history view with View buttons wired to SessionResultsPage."""

    # Emitted when the user clicks View — MainWindow handles navigation
    view_session = Signal(int)   # session_id

    def __init__(self, user_id: int = 1, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._user_id = user_id
        self._svc = ProgressService()
        self._build_ui()
        QTimer.singleShot(150, self._load_history)

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setObjectName("HistoryScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("HistoryContent")
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(32, 32, 32, 32)
        self._layout.setSpacing(16)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        page_title = QLabel("History")
        page_title.setObjectName("PageTitle")
        page_subtitle = QLabel("Review your past speaking sessions")
        page_subtitle.setObjectName("PageSubtitle")
        self._layout.addWidget(page_title)
        self._layout.addWidget(page_subtitle)

        self._list_container = QVBoxLayout()
        self._list_container.setSpacing(8)
        self._layout.addLayout(self._list_container)
        self._layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)
        self._apply_styles()

    # ------------------------------------------------------------------ #
    # Data loading                                                          #
    # ------------------------------------------------------------------ #

    def _load_history(self) -> None:
        self._clear_list()
        try:
            rows = self._svc.get_rich_session_history(self._user_id, limit=50)
            if not rows:
                self._show_empty_state()
                return
            self._list_container.addWidget(self._make_header())
            for row in rows:
                self._list_container.addWidget(self._make_row(row))
            logger.debug("Loaded %d history rows", len(rows))
        except Exception as exc:
            logger.error("Failed to load history: %s", exc)
            self._show_error_state(str(exc))

    def _clear_list(self) -> None:
        while self._list_container.count():
            item = self._list_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _make_header(self) -> QWidget:
        h = QWidget()
        h.setObjectName("TableHeader")
        layout = QHBoxLayout(h)
        layout.setContentsMargins(20, 6, 20, 6)
        layout.setSpacing(0)
        for text, width in [("Topic", 0), ("Date", 120), ("Duration", 80),
                             ("Score", 70), ("", 64)]:
            lbl = QLabel(text)
            lbl.setObjectName("HeaderLabel")
            if width:
                lbl.setFixedWidth(width)
            else:
                lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            layout.addWidget(lbl)
        return h

    def _make_row(self, row: dict) -> QFrame:
        frame = QFrame()
        frame.setObjectName("SessionRow")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(20, 10, 16, 10)
        layout.setSpacing(0)

        # Topic
        word_col = QVBoxLayout()
        word_lbl = QLabel(row.get("word", "—"))
        word_lbl.setObjectName("RowWord")
        cat_lbl = QLabel(row.get("category") or "—")
        cat_lbl.setObjectName("RowCategory")
        word_col.addWidget(word_lbl)
        word_col.addWidget(cat_lbl)
        layout.addLayout(word_col, stretch=1)

        # Date
        date_lbl = QLabel(format_datetime(row.get("completed_at"), "%d %b %Y"))
        date_lbl.setObjectName("RowMeta")
        date_lbl.setFixedWidth(120)
        date_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(date_lbl)

        # Duration
        dur_lbl = QLabel(format_duration(row.get("duration_seconds") or 0))
        dur_lbl.setObjectName("RowMeta")
        dur_lbl.setFixedWidth(80)
        dur_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(dur_lbl)

        # Score
        score = row.get("overall_score")
        score_lbl = QLabel(format_score(score, ""))
        score_lbl.setObjectName("RowScore")
        score_lbl.setFixedWidth(70)
        score_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if score is not None:
            score_lbl.setStyleSheet(
                f"color: {score_colour(score)}; font-weight: 600;"
            )
        layout.addWidget(score_lbl)

        # View button
        view_btn = QPushButton("View")
        view_btn.setObjectName("ViewBtn")
        view_btn.setFixedSize(56, 28)
        view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        view_btn.setAccessibleName(f"View session for {row.get('word', 'topic')}")
        sid = row["session_id"]
        view_btn.clicked.connect(lambda _, r=row: self._on_view(r))
        layout.addWidget(view_btn)

        return frame

    def _on_view(self, row: dict) -> None:
        logger.info("View session from history: session_id=%s", row["session_id"])
        self._current_row = row
        self.view_session.emit(row["session_id"])

    def get_current_row(self) -> dict | None:
        return getattr(self, "_current_row", None)

    def _show_empty_state(self) -> None:
        card = QFrame()
        card.setObjectName("EmptyState")
        card.setMinimumHeight(240)
        lay = QVBoxLayout(card)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(10)
        lay.addWidget(_centered("📋", "font-size: 40px;"))
        msg = QLabel("No speaking sessions yet.")
        msg.setObjectName("EmptyStateMsg")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("Head over to Practice to record your first session.")
        hint.setObjectName("EmptyStateHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        lay.addWidget(msg)
        lay.addWidget(hint)
        self._list_container.addWidget(card)

    def _show_error_state(self, message: str) -> None:
        lbl = QLabel(f"⚠️  Could not load history: {message}")
        lbl.setObjectName("ErrorMsg")
        lbl.setWordWrap(True)
        self._list_container.addWidget(lbl)

    def refresh(self) -> None:
        self._load_history()

    # ------------------------------------------------------------------ #
    # Styles                                                                #
    # ------------------------------------------------------------------ #

    def _apply_styles(self) -> None:
        c = settings
        self.setStyleSheet(f"""
            #HistoryScroll, #HistoryContent {{ background-color: {c.COLOR_BG_DARK}; }}
            #PageTitle {{ font-size: 24px; font-weight: 700; color: {c.COLOR_TEXT_PRIMARY}; }}
            #PageSubtitle {{ font-size: 14px; color: {c.COLOR_TEXT_SECONDARY}; }}
            #TableHeader {{ background-color: transparent; }}
            #HeaderLabel {{
                font-size: 11px; font-weight: 600; color: {c.COLOR_TEXT_MUTED};
                text-transform: uppercase; letter-spacing: 0.6px;
            }}
            #SessionRow {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER}; border-radius: 10px;
            }}
            #SessionRow:hover {{
                border: 1px solid {c.COLOR_ACCENT};
                background-color: rgba(108, 99, 255, 0.05);
            }}
            #RowWord {{ font-size: 14px; font-weight: 600; color: {c.COLOR_TEXT_PRIMARY}; }}
            #RowCategory {{ font-size: 12px; color: {c.COLOR_TEXT_MUTED}; }}
            #RowMeta {{ font-size: 13px; color: {c.COLOR_TEXT_SECONDARY}; }}
            #RowScore {{ font-size: 14px; color: {c.COLOR_TEXT_PRIMARY}; }}
            #ViewBtn {{
                background-color: transparent;
                color: {c.COLOR_ACCENT};
                border: 1px solid {c.COLOR_ACCENT};
                border-radius: 6px; font-size: 12px; font-weight: 600;
            }}
            #ViewBtn:hover {{ background-color: rgba(108,99,255,0.12); }}
            #EmptyState {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER}; border-radius: 12px;
            }}
            #EmptyStateMsg {{ font-size: 18px; font-weight: 600; color: {c.COLOR_TEXT_PRIMARY}; }}
            #EmptyStateHint {{ font-size: 13px; color: {c.COLOR_TEXT_MUTED}; }}
            #ErrorMsg {{ font-size: 13px; color: {c.COLOR_DANGER}; padding: 16px; }}
        """)


def _centered(text: str, style: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if style:
        lbl.setStyleSheet(style)
    return lbl

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session row widget
# ---------------------------------------------------------------------------

