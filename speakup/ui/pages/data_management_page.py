"""
DataManagementPage — export, backup, and privacy controls.

Features
────────
    Export Sessions   — CSV or JSON of all completed sessions
    Backup Database   — copy speakup.db to a user-chosen location
    Delete Recordings — remove audio/video files while keeping session records
    Delete All Data   — wipe the database and recordings (with confirmation)

All destructive actions require a confirmation dialog before proceeding.
"""

from __future__ import annotations

import csv
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from speakup.config.settings import settings
from speakup.services.progress_service import ProgressService

logger = logging.getLogger(__name__)


class DataManagementPage(QWidget):
    """Data management and privacy controls page."""

    def __init__(self, user_id: int = 1, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._user_id = user_id
        self._svc = ProgressService()
        self._build_ui()
        self._apply_styles()

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setObjectName("DataScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("DataContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(24)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Header
        title = QLabel("Data & Privacy")
        title.setObjectName("PageTitle")
        subtitle = QLabel("Export your data, manage recordings, or delete your account.")
        subtitle.setObjectName("PageSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        layout.addWidget(self._build_section(
            "📤  Export Session Results",
            "Download all your practice sessions as a CSV or JSON file.",
            [
                ("Export as CSV", self._export_csv),
                ("Export as JSON", self._export_json),
            ],
        ))

        layout.addWidget(self._build_section(
            "💾  Backup Database",
            "Copy the local database file to a safe location.",
            [("Create Backup", self._backup_db)],
        ))

        layout.addWidget(self._build_section(
            "🗑  Delete Recordings",
            "Remove audio and video recording files. "
            "Your session records and scores are kept.",
            [("Delete All Recordings", self._delete_recordings)],
            danger=False,
        ))

        layout.addWidget(self._build_section(
            "☠️  Delete All Data",
            "Permanently delete your database, recordings, and all session history. "
            "This cannot be undone.",
            [("Delete Everything", self._delete_all)],
            danger=True,
        ))

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _build_section(
        self,
        title: str,
        description: str,
        buttons: list[tuple[str, callable]],
        danger: bool = False,
    ) -> QFrame:
        card = QFrame()
        card.setObjectName("DataCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(10)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("CardDangerTitle" if danger else "CardTitle2")

        desc_lbl = QLabel(description)
        desc_lbl.setObjectName("CardDesc")
        desc_lbl.setWordWrap(True)

        layout.addWidget(title_lbl)
        layout.addWidget(desc_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        for btn_text, handler in buttons:
            btn = QPushButton(btn_text)
            btn.setObjectName("DangerBtn" if danger else "ActionBtn")
            btn.setFixedHeight(38)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(handler)
            btn.setAccessibleName(btn_text)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        return card

    # ------------------------------------------------------------------ #
    # Actions                                                               #
    # ------------------------------------------------------------------ #

    def _export_csv(self) -> None:
        rows = self._fetch_rows()
        if not rows:
            QMessageBox.information(self, "No Data", "No completed sessions to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", "speakup_sessions.csv", "CSV Files (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            QMessageBox.information(self, "Exported", f"Sessions exported to:\n{path}")
            logger.info("Sessions exported to CSV: %s", path)
        except Exception as exc:
            logger.error("CSV export failed: %s", exc)
            QMessageBox.warning(self, "Export Failed", str(exc))

    def _export_json(self) -> None:
        rows = self._fetch_rows()
        if not rows:
            QMessageBox.information(self, "No Data", "No completed sessions to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export JSON", "speakup_sessions.json", "JSON Files (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rows, f, indent=2, default=str)
            QMessageBox.information(self, "Exported", f"Sessions exported to:\n{path}")
            logger.info("Sessions exported to JSON: %s", path)
        except Exception as exc:
            logger.error("JSON export failed: %s", exc)
            QMessageBox.warning(self, "Export Failed", str(exc))

    def _backup_db(self) -> None:
        db_path = settings.PROJECT_ROOT / "speakup.db"
        if not db_path.exists():
            QMessageBox.warning(self, "Not Found", "Database file not found.")
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"speakup_backup_{ts}.db"
        dest, _ = QFileDialog.getSaveFileName(
            self, "Backup Database", default_name, "Database Files (*.db)"
        )
        if not dest:
            return
        try:
            shutil.copy2(db_path, dest)
            QMessageBox.information(self, "Backup Created", f"Database backed up to:\n{dest}")
            logger.info("Database backed up to: %s", dest)
        except Exception as exc:
            logger.error("DB backup failed: %s", exc)
            QMessageBox.warning(self, "Backup Failed", str(exc))

    def _delete_recordings(self) -> None:
        rec_dir = settings.RECORDINGS_DIR
        files   = list(rec_dir.rglob("*.mp4")) + list(rec_dir.rglob("*.wav"))
        if not files:
            QMessageBox.information(self, "Nothing to Delete", "No recording files found.")
            return

        reply = QMessageBox.question(
            self, "Delete Recordings",
            f"This will delete {len(files)} recording file(s).\n\n"
            "Your session records and scores will be kept.\n\n"
            "This cannot be undone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        deleted = 0
        for f in files:
            try:
                f.unlink()
                deleted += 1
            except Exception as exc:
                logger.warning("Could not delete %s: %s", f, exc)

        QMessageBox.information(
            self, "Deleted",
            f"Deleted {deleted} of {len(files)} recording file(s)."
        )
        logger.info("Deleted %d recording files.", deleted)

    def _delete_all(self) -> None:
        # Step 1: first confirmation
        reply1 = QMessageBox.question(
            self, "Delete All Data — Are You Sure?",
            "This will permanently delete:\n\n"
            "  • All session records and scores\n"
            "  • Your transcript history\n"
            "  • All recording files\n"
            "  • Your preferences and achievements\n\n"
            "The application will restart with a fresh database.\n\n"
            "This CANNOT be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply1 != QMessageBox.StandardButton.Yes:
            return

        # Step 2: second confirmation
        reply2 = QMessageBox.warning(
            self, "Final Confirmation",
            "Type-confirm: all data will be permanently erased.\n\n"
            "Are you absolutely sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply2 != QMessageBox.StandardButton.Yes:
            return

        try:
            # Delete recordings
            rec_dir = settings.RECORDINGS_DIR
            if rec_dir.exists():
                shutil.rmtree(rec_dir, ignore_errors=True)
                rec_dir.mkdir(parents=True, exist_ok=True)

            # Delete database
            db_path = settings.PROJECT_ROOT / "speakup.db"
            if db_path.exists():
                db_path.unlink()

            logger.warning("All user data deleted by user request.")
            QMessageBox.information(
                self, "Data Deleted",
                "All data has been deleted.\n\n"
                "Please restart the application to begin fresh."
            )
        except Exception as exc:
            logger.error("Delete all failed: %s", exc)
            QMessageBox.warning(self, "Error", f"Delete failed: {exc}")

    def _fetch_rows(self) -> list[dict]:
        """Fetch session history as a list of plain dicts for export."""
        try:
            rows = self._svc.get_rich_session_history(self._user_id)
            # Serialise datetimes and remove large fields
            cleaned = []
            for r in rows:
                cleaned.append({
                    "session_id":       r.get("session_id"),
                    "date":             str(r.get("completed_at", "")),
                    "topic":            r.get("word", ""),
                    "category":         r.get("category", ""),
                    "difficulty":       r.get("difficulty", ""),
                    "duration_seconds": r.get("duration_seconds", 0),
                    "overall_score":    r.get("overall_score"),
                    "fluency_score":    r.get("fluency_score"),
                    "vocabulary_score": r.get("vocabulary_score"),
                    "grammar_score":    r.get("grammar_score"),
                    "confidence_score": r.get("confidence_score"),
                    "words_per_minute": r.get("words_per_minute"),
                    "filler_word_count": r.get("filler_word_count"),
                    "transcript":       (r.get("transcript", "") or "")[:200],
                })
            return cleaned
        except Exception as exc:
            logger.error("Failed to fetch rows for export: %s", exc)
            return []

    # ------------------------------------------------------------------ #
    # Styles                                                                #
    # ------------------------------------------------------------------ #

    def _apply_styles(self) -> None:
        c = settings
        self.setStyleSheet(f"""
            #DataScroll, #DataContent {{ background-color: {c.COLOR_BG_DARK}; }}
            #PageTitle {{ font-size: 24px; font-weight: 700; color: {c.COLOR_TEXT_PRIMARY}; }}
            #PageSubtitle {{ font-size: 14px; color: {c.COLOR_TEXT_SECONDARY}; }}
            #DataCard {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 12px;
            }}
            #CardTitle2 {{ font-size: 14px; font-weight: 600; color: {c.COLOR_TEXT_PRIMARY}; }}
            #CardDangerTitle {{ font-size: 14px; font-weight: 600; color: {c.COLOR_DANGER}; }}
            #CardDesc {{ font-size: 13px; color: {c.COLOR_TEXT_SECONDARY}; }}
            #ActionBtn {{
                background-color: {c.COLOR_BG_DARK};
                color: {c.COLOR_ACCENT};
                border: 1px solid {c.COLOR_ACCENT};
                border-radius: 8px;
                padding: 0px 18px;
                font-size: 13px; font-weight: 600;
            }}
            #ActionBtn:hover {{ background-color: rgba(108,99,255,0.12); }}
            #DangerBtn {{
                background-color: rgba(244,67,54,0.10);
                color: {c.COLOR_DANGER};
                border: 1px solid {c.COLOR_DANGER};
                border-radius: 8px;
                padding: 0px 18px;
                font-size: 13px; font-weight: 600;
            }}
            #DangerBtn:hover {{ background-color: rgba(244,67,54,0.20); }}
        """)
