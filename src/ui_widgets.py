from __future__ import annotations

from PySide6.QtCore import Property, Qt
from PySide6.QtGui import QColor, QPaintEvent, QPainter, QPen
from PySide6.QtWidgets import QProgressBar, QWidget


class RoundedProgressBar(QProgressBar):
    """繪製不受 native style 影響的圓角 progress"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._track_color = QColor("#1a2532")
        self._chunk_color = QColor("#5b8cff")
        self._border_color = QColor("#2a394b")

    @Property(QColor)
    def trackColor(self) -> QColor:
        return self._track_color

    @trackColor.setter
    def trackColor(self, color: QColor) -> None:
        self._track_color = color

    @Property(QColor)
    def chunkColor(self) -> QColor:
        return self._chunk_color

    @chunkColor.setter
    def chunkColor(self, color: QColor) -> None:
        self._chunk_color = color

    @Property(QColor)
    def borderColor(self) -> QColor:
        return self._border_color

    @borderColor.setter
    def borderColor(self, color: QColor) -> None:
        self._border_color = color

    def paintEvent(self, event: QPaintEvent) -> None:
        """繪製 determinate progress, indeterminate 保留 Qt 原生動畫"""
        if self.minimum() == self.maximum():
            super().paintEvent(event)
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = self.rect().toRectF().adjusted(0.5, 0.5, -0.5, -0.5)
        radius = outer.height() / 2
        painter.setPen(QPen(self._border_color, 1))
        painter.setBrush(self._track_color)
        painter.drawRoundedRect(outer, radius, radius)

        span = self.maximum() - self.minimum()
        ratio = max(0.0, min(1.0, (self.value() - self.minimum()) / span)) if span else 0.0
        if ratio <= 0: return
        inner = outer.adjusted(1, 1, -1, -1)
        inner.setWidth(inner.width() * ratio)
        chunk_radius = min(inner.width(), inner.height()) / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._chunk_color)
        painter.drawRoundedRect(inner, chunk_radius, chunk_radius)
