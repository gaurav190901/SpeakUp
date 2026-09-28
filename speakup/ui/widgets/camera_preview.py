"""
CameraPreviewWidget — live camera preview for the recording page.

Receives QImage frames via the ``update_frame`` slot (connected to
RecordingEngine.frame_ready signal) and paints them scaled to fit.

Gracefully shows a placeholder card when no camera is available.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QSize, Slot
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor, QFont, QPen
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget, QVBoxLayout, QFrame

from speakup.config.settings import settings

logger = logging.getLogger(__name__)

# Nominal preview dimensions (aspect-ratio preserved via scaled())
_PREVIEW_W = 640
_PREVIEW_H = 480


class CameraPreviewWidget(QFrame):
    """
    Displays a live camera feed from QImage frames.

    Usage
    -----
        preview = CameraPreviewWidget()
        recording_engine.frame_ready.connect(preview.update_frame)

    States
    ------
        WAITING  — placeholder shown before the camera opens
        LIVE     — actively displaying frames
        ERROR    — camera unavailable message
        HIDDEN   — preview deliberately hidden (e.g. COMPLETED state)
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CameraPreviewWidget")

        self._current_pixmap: QPixmap | None = None
        self._mode: str = "waiting"          # waiting | live | error | hidden
        self._placeholder_text: str = "Camera preview will appear here"
        self._error_text:       str = "Camera unavailable"

        self._build_ui()
        self._apply_style()
        self._show_placeholder()

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        self.setMinimumSize(_PREVIEW_W // 2, _PREVIEW_H // 2)  # 320 × 240 min
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # The QLabel that holds the pixmap (or placeholder text)
        self._label = QLabel()
        self._label.setObjectName("PreviewLabel")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setScaledContents(False)   # we do manual scaling
        self._label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self._label)

    def _apply_style(self) -> None:
        self.setStyleSheet(f"""
            #CameraPreviewWidget {{
                background-color: #000000;
                border: 1px solid {settings.COLOR_BORDER};
                border-radius: 10px;
            }}
            #PreviewLabel {{
                background-color: transparent;
                color: {settings.COLOR_TEXT_MUTED};
                font-size: 14px;
            }}
        """)

    # ------------------------------------------------------------------ #
    # Public slots / methods                                                #
    # ------------------------------------------------------------------ #

    @Slot(QImage)
    def update_frame(self, image: QImage) -> None:
        """
        Receive a new camera frame and display it.

        Connected to ``RecordingEngine.frame_ready``.  Called on the GUI
        thread (signal is auto-queued across thread boundary).
        """
        if image.isNull():
            return

        if self._mode != "live":
            self._mode = "live"
            self._label.setText("")          # clear placeholder text

        pixmap = QPixmap.fromImage(image)
        self._current_pixmap = pixmap
        self._paint_scaled(pixmap)

    def show_placeholder(self, text: str = "") -> None:
        """Show the waiting/placeholder state."""
        self._mode = "waiting"
        self._placeholder_text = text or "Camera preview will appear here"
        self._show_placeholder()

    def show_error(self, message: str = "") -> None:
        """Show the camera-unavailable error state."""
        self._mode = "error"
        self._error_text = message or "Camera unavailable"
        self._label.setPixmap(QPixmap())
        self._label.setText(f"⚠️  {self._error_text}")

    def show_hidden(self) -> None:
        """Hide the preview (e.g. after recording completes)."""
        self._mode = "hidden"
        self._label.setPixmap(QPixmap())
        self._label.setText("")

    # ------------------------------------------------------------------ #
    # Resize handler                                                        #
    # ------------------------------------------------------------------ #

    def resizeEvent(self, event) -> None:
        """Re-scale the current pixmap when the widget is resized."""
        super().resizeEvent(event)
        if self._current_pixmap and self._mode == "live":
            self._paint_scaled(self._current_pixmap)
        elif self._mode == "waiting":
            self._show_placeholder()

    # ------------------------------------------------------------------ #
    # Internal helpers                                                      #
    # ------------------------------------------------------------------ #

    def _paint_scaled(self, pixmap: QPixmap) -> None:
        """Scale *pixmap* to fit the label while preserving aspect ratio."""
        available = self._label.size()
        if available.width() <= 0 or available.height() <= 0:
            return
        scaled = pixmap.scaled(
            available,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(scaled)

    def _show_placeholder(self) -> None:
        """Render a placeholder with centred text."""
        w = max(self._label.width(),  320)
        h = max(self._label.height(), 240)

        placeholder = QPixmap(w, h)
        placeholder.fill(QColor("#0a0c12"))

        painter = QPainter(placeholder)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Camera icon
        painter.setPen(QColor(settings.COLOR_TEXT_MUTED))
        font_icon = painter.font()
        font_icon.setPointSize(36)
        painter.setFont(font_icon)
        painter.drawText(
            placeholder.rect(),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            "📷",
        )

        # Subtitle text below icon
        font_text = painter.font()
        font_text.setPointSize(11)
        painter.setFont(font_text)
        painter.setPen(QColor(settings.COLOR_TEXT_MUTED))
        text_rect = placeholder.rect().adjusted(0, h // 2 + 10, 0, 0)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            self._placeholder_text,
        )
        painter.end()

        self._label.setPixmap(placeholder)
        self._label.setText("")
