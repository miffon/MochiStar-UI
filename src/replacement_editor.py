from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QBuffer, QEvent, QIODevice, QPoint, QRectF, QSignalBlocker, QSize, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QIcon, QMouseEvent, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractScrollArea, QFrame, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QSplitter, QStackedLayout, QVBoxLayout, QWidget,
)

from i18n import tr
from models import ReplacementClipTiming, ReplacementTimeline
from theme import theme_color


def _duration(probe: dict[str, Any], media_type: str) -> float | None:
    """優先取得指定 stream 的時間長度"""
    stream = next((item for item in probe.get("streams") or [] if item.get("codec_type") == media_type), {})
    for value in (stream.get("duration"), probe.get("duration"), (probe.get("format") or {}).get("duration")):
        try:
            if value is not None: return max(0.0, float(value))
        except (TypeError, ValueError):
            continue
    return None


def _format_time(seconds: float) -> str:
    value = max(0.0, float(seconds))
    minutes, value = divmod(value, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{value:06.3f}"


def _format_duration(seconds: float) -> str:
    value = max(0, int(float(seconds)))
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _editor_icon(name: str) -> QIcon:
    """依目前 theme 選擇 editor control icon"""
    variant = "on-dark" if QColor(theme_color("text_primary")).lightnessF() > 0.5 else "on-light"
    return QIcon(str(Path(__file__).resolve().parent / "assets" / f"{name}-{variant}.svg"))


@dataclass
class _PreviewSegment:
    data: bytes
    start: float
    end: float


class TimelineWidget(QAbstractScrollArea):
    """繪製兩軌 clip、工作區、播放頭與拖曳控制"""

    source_dropped = Signal(str, str)
    source_browse_requested = Signal(str)
    source_clear_requested = Signal(str)
    timeline_changed = Signal(object)
    output_range_changed = Signal(object)
    playhead_changed = Signal(float)
    scrub_started = Signal(float)
    scrub_moved = Signal(float)
    scrub_finished = Signal(float)
    marker_selection_changed = Signal(bool)
    marker_placement_finished = Signal()

    LABEL_WIDTH = 96
    PLAYHEAD_GUTTER = 6
    RIGHT_GUTTER = 6
    RULER_HEIGHT = 34
    MINIMUM_TRACK_HEIGHT = 52
    TRACK_GAP = 8
    TRACK_MARGIN = 5
    MINIMUM_SCALE = 0.05

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumHeight(205)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.paths = {"visual": "", "audio": ""}
        self.probes: dict[str, dict[str, Any]] = {}
        self.assets: dict[str, QPixmap] = {}
        self.cached_ranges: list[tuple[float, float]] = []
        self.cached_segments: list[tuple[float, float]] = []
        self.timings = {
            "visual": ReplacementClipTiming(), "audio": ReplacementClipTiming(),
        }
        self.track_buttons: dict[str, dict[str, QPushButton]] = {}
        for track in ("visual", "audio"):
            browse_button = QPushButton(tr("Browse Files"), self.viewport())
            loop_button = QPushButton(self.viewport())
            clear_button = QPushButton(self.viewport())
            for button in (browse_button, loop_button, clear_button):
                button.setProperty("role", "ghost")
                button.setProperty("compact", True)
            loop_button.setProperty("iconOnly", True)
            clear_button.setProperty("iconOnly", True)
            loop_button.setCheckable(True)
            loop_button.setAccessibleName(tr("Loop"))
            loop_button.setToolTip(tr("Loop"))
            clear_button.setAccessibleName(tr("Remove"))
            browse_button.clicked.connect(lambda _checked=False, name=track: self.source_browse_requested.emit(name))
            loop_button.toggled.connect(lambda checked, name=track: self._set_track_loop(name, checked))
            clear_button.clicked.connect(lambda _checked=False, name=track: self.source_clear_requested.emit(name))
            clear_button.setToolTip(tr("Remove"))
            self.track_buttons[track] = {"browse": browse_button, "loop": loop_button, "clear": clear_button}
        self._refresh_track_button_icons()
        self.output_in, self.output_out = 0.0, 5.0
        self.playhead = 0.0
        self.track_markers: dict[str, float | None] = {"visual": None, "audio": None}
        self.selected_marker: str | None = None
        self.marker_placement = False
        self.pixels_per_second = 36.0
        self.snapping = True
        self._output_user_set = False
        self._drag: tuple[str, str, float, ReplacementClipTiming | None] | None = None
        self._pan_start: tuple[float, int] | None = None
        self.horizontalScrollBar().valueChanged.connect(lambda _value: self.viewport().update())
        self._update_scrollbar()

    def sizeHint(self) -> QSize:
        return QSize(700, 205)

    def timeline(self) -> ReplacementTimeline:
        """取得與 UI 狀態分離的 timeline snapshot"""
        return ReplacementTimeline(
            visual=ReplacementClipTiming.from_dict(self.timings["visual"].to_dict()),
            audio=ReplacementClipTiming.from_dict(self.timings["audio"].to_dict()),
            output_in=self.output_in, output_out=self.output_out,
        )

    def set_timeline(self, timeline: ReplacementTimeline) -> None:
        """套用列隊或測試提供的 timeline 狀態"""
        self.timings["visual"] = ReplacementClipTiming.from_dict(timeline.visual.to_dict())
        self.timings["audio"] = ReplacementClipTiming.from_dict(timeline.audio.to_dict())
        self.output_in, self.output_out = timeline.output_in, timeline.output_out
        self.playhead = min(max(self.playhead, self.output_in), self.output_out)
        self._output_user_set = True
        self._update_scrollbar()
        self._layout_track_buttons()
        self.viewport().update()

    def set_source(self, track: str, path: str) -> None:
        """更新軌道來源, probe 完成前先顯示待分析 block"""
        self.paths[track] = path
        if not path:
            self.probes.pop(track, None)
            self.assets.pop(track, None)
            self.track_markers[track] = None
            if self.selected_marker == track: self._select_marker(None)
            self.timings[track] = ReplacementClipTiming()
            if not any(self.paths.values()):
                self._output_user_set = False
                self.output_in, self.output_out = 0.0, 5.0
                self.playhead = 0.0
        self._update_scrollbar()
        self._layout_track_buttons()
        self.viewport().update()

    def set_source_probe(self, track: str, probe: dict[str, Any]) -> None:
        """依來源自然長度初始化主 block"""
        self.probes[track] = probe
        media_type = "video" if track == "visual" else "audio"
        source_duration = _duration(probe, media_type)
        still = track == "visual" and source_duration is None
        other = "audio" if track == "visual" else "visual"
        paired_end = self.timings[other].timeline_start + self.timings[other].timeline_duration
        duration = paired_end if still and self.paths[other] and paired_end > 0 else 5.0 if still else source_duration or 5.0
        self.timings[track] = ReplacementClipTiming(
            source_in=0.0, source_out=None if still else duration,
            timeline_start=0.0, timeline_duration=duration, loop=False,
        )
        if track == "audio" and self._is_still("visual") and not self._output_user_set:
            self.timings["visual"].timeline_duration = duration
        if not self._output_user_set:
            self.output_in = 0.0
            self.output_out = max(clip.timeline_start + clip.timeline_duration for clip in self.timings.values())
        self.playhead = min(max(self.playhead, self.output_in), self.output_out)
        self._update_scrollbar()
        self._layout_track_buttons()
        self.viewport().update()
        self.timeline_changed.emit(self.timeline())

    def set_asset(self, track: str, pixmap: QPixmap) -> None:
        """套用影片縮圖列或音訊 overview 波形"""
        if track in self.paths and not pixmap.isNull():
            self.assets[track] = pixmap
            self.viewport().update()

    def set_cached_ranges(self, ranges: list[tuple[float, float]]) -> None:
        """更新 ruler 下方的 RAM cache 範圍"""
        self.cached_ranges = [(float(start), float(end)) for start, end in ranges]
        self.viewport().update()

    def set_cached_segments(self, segments: list[tuple[float, float]]) -> None:
        """更新 ruler 下方的實際 cache 區段邊界"""
        self.cached_segments = sorted((float(start), float(end)) for start, end in segments)
        self.viewport().update()

    def set_playhead(self, seconds: float, emit: bool = False) -> None:
        self.playhead = min(max(float(seconds), self.output_in), self.output_out)
        self.viewport().update()
        if emit: self.playhead_changed.emit(self.playhead)

    def set_snapping(self, enabled: bool) -> None:
        self.snapping = bool(enabled)
        self.viewport().update()

    def set_marker_placement(self, enabled: bool) -> None:
        """切換點選軌道放置 sync marker 的模式"""
        self.marker_placement = bool(enabled)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)

    def set_track_marker(self, track: str, seconds: float) -> None:
        """設定軌道唯一的 sync marker 並綁定來源時間"""
        if track not in self.track_markers or not self.paths[track]: return
        timing = self.timings[track]
        timeline_time = min(max(float(seconds), timing.timeline_start), timing.timeline_start + timing.timeline_duration)
        self.track_markers[track] = round(timing.source_in + timeline_time - timing.timeline_start, 3)
        self._select_marker(track)
        self.marker_placement_finished.emit()

    def remove_selected_marker(self) -> None:
        """刪除目前選取的 marker"""
        if self.selected_marker is None: return
        self.track_markers[self.selected_marker] = None
        self._select_marker(None)

    def zoom_by(self, factor: float, _anchor_x: float | None = None) -> None:
        """以 playhead 為可視區中心調整時間軸比例"""
        viewport_width = max(1, self.viewport().width() - self._content_left() - self.RIGHT_GUTTER)
        anchor_x = self._content_left() + viewport_width / 2
        self.pixels_per_second = min(400.0, max(self.MINIMUM_SCALE, self.pixels_per_second * factor))
        self._update_scrollbar()
        target = self.playhead * self.pixels_per_second - max(0.0, anchor_x - self._content_left())
        self.horizontalScrollBar().setValue(round(target))
        self.viewport().update()

    def fit_timeline(self) -> None:
        horizon = max(1.0, self._horizon())
        width = max(1, self.viewport().width() - self._content_left() - self.RIGHT_GUTTER)
        self.pixels_per_second = min(400.0, max(self.MINIMUM_SCALE, width / horizon))
        self._update_scrollbar()
        self.horizontalScrollBar().setValue(0)
        self.viewport().update()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        paths = self._local_paths(event.mimeData())
        event.acceptProposedAction() if len(paths) == 1 else event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = self._local_paths(event.mimeData())
        track = self._track_at(event.position().toPoint())
        if len(paths) != 1 or track is None:
            event.ignore()
            return
        self.source_dropped.emit(track, paths[0])
        event.acceptProposedAction()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.viewport().rect(), QColor(theme_color("panel_background")))
        self._paint_ruler(painter)
        for track in ("visual", "audio"): self._paint_track(painter, track)
        visual_rect, audio_rect = self._track_rect("visual"), self._track_rect("audio")
        painter.setPen(QPen(QColor(theme_color("border")), 1))
        painter.drawLine(0, self.RULER_HEIGHT, self.viewport().width(), self.RULER_HEIGHT)
        divider_y = round((visual_rect.bottom() + audio_rect.top()) / 2)
        painter.drawLine(0, divider_y, self.viewport().width(), divider_y)
        painter.save()
        painter.setClipRect(self._content_left(), self.RULER_HEIGHT, self.viewport().width() - self._content_left(), 5)
        segments = self.cached_segments or self.cached_ranges
        painter.setPen(QPen(QColor(theme_color("accent")), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        for start, end in segments:
            left, right = round(self._x_at(start)), round(self._x_at(end))
            if right - left > 2: left, right = left + 1, right - 1 # 留出實際 cache 區段的接縫
            painter.drawLine(left, self.RULER_HEIGHT + 2, right, self.RULER_HEIGHT + 2)
        painter.restore()
        self._paint_markers(painter)

        # 播放頭只畫在時間軸內容區, gutter 讓 0 點的三角形保持完整
        x = self._x_at(self.playhead)
        painter.save()
        painter.setClipRect(self.LABEL_WIDTH, 0, self.viewport().width() - self.LABEL_WIDTH, self.viewport().height())
        playhead_color = QColor(theme_color("error"))
        painter.setPen(QPen(playhead_color, 2))
        painter.drawLine(round(x), 8, round(x), self.viewport().height() - 4)
        painter.setBrush(playhead_color)
        painter.drawPolygon([QPoint(round(x) - 5, 6), QPoint(round(x) + 5, 6), QPoint(round(x), 13)])
        painter.restore()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_start = (event.position().x(), self.horizontalScrollBar().value())
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton: return
        point, value = event.position().toPoint(), self._time_at(event.position().x())
        marker = self._marker_at(point)
        if marker is not None:
            self._select_marker(marker)
            self._drag = ("marker", marker, value, None)
            return
        track = self._track_at(point)
        if self.marker_placement and track is not None and self._clip_rect(track).contains(event.position()):
            self.set_track_marker(track, value)
            return
        self._select_marker(None)
        output_handle = self._output_handle_at(point)
        if output_handle is not None:
            self._drag = ("output", output_handle, value, None)
            return
        if track is None:
            self.set_playhead(self._snap(value))
            self._drag = ("playhead", "", value, None)
            self.scrub_started.emit(self.playhead)
            return
        if point.x() < self.LABEL_WIDTH: return
        rect = self._clip_rect(track)
        if not rect.contains(event.position()):
            self.set_playhead(self._snap(value))
            self._drag = ("playhead", "", value, None)
            self.scrub_started.emit(self.playhead)
            return
        timing = ReplacementClipTiming.from_dict(self.timings[track].to_dict())
        mode = "left" if abs(event.position().x() - rect.left()) <= 7 else "right" if abs(event.position().x() - rect.right()) <= 7 else "move"
        self._drag = (track, mode, value, timing)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._pan_start is not None:
            self._pan_to(event.position().x())
            event.accept()
            return
        if self._drag is None: return
        track, mode, start_value, original = self._drag
        raw_value = self._time_at(event.position().x())
        bypass_snapping = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        if track == "marker":
            timing = self.timings[mode]
            value = self._snap(raw_value, excluded_marker=mode, bypass=bypass_snapping)
            value = min(max(value, timing.timeline_start), timing.timeline_start + timing.timeline_duration)
            self.track_markers[mode] = round(timing.source_in + value - timing.timeline_start, 3)
            self.viewport().update()
            return
        excluded_track = track if track in {"visual", "audio"} else None
        value = max(0.0, self._snap(
            raw_value, excluded_track, track == "output", bypass=bypass_snapping,
        ))
        if track == "playhead":
            self.set_playhead(value)
            self.scrub_moved.emit(self.playhead)
            return
        if track == "output":
            if mode == "in": self.output_in = min(value, self.output_out - 0.01)
            else: self.output_out = max(value, self.output_in + 0.01)
            self._output_user_set = True
            self.set_playhead(self.playhead)
        elif original is not None:
            timing, raw_delta = self.timings[track], raw_value - start_value
            delta = value - start_value
            minimum = self._minimum_duration(track)
            if mode == "move":
                marker_delta = self._marker_alignment_delta(track, raw_delta, original, bypass_snapping)
                timing.timeline_start = max(0.0, original.timeline_start + (delta if marker_delta is None else marker_delta))
            elif mode == "left":
                if self._is_still(track):
                    left = min(original.timeline_start + original.timeline_duration - minimum, max(0.0, original.timeline_start + delta))
                    timing.timeline_duration = original.timeline_start + original.timeline_duration - left
                    timing.timeline_start = left
                else:
                    delta = max(-original.source_in, -original.timeline_start, min(delta, original.timeline_duration - minimum))
                    timing.source_in = original.source_in + delta
                    timing.timeline_start = original.timeline_start + delta
                    timing.timeline_duration = original.timeline_duration - delta
            else:
                duration = max(minimum, original.timeline_duration + delta)
                source_duration = self._source_duration(track)
                if not self._is_still(track) and source_duration is not None:
                    duration = min(duration, source_duration - original.source_in)
                    timing.source_out = original.source_in + duration
                timing.timeline_duration = duration
            if not self._is_still(track): timing.source_out = timing.source_in + timing.timeline_duration
        self._update_scrollbar()
        self.viewport().update()
        changed = self.timeline()
        if track == "output": self.output_range_changed.emit(changed)
        else: self.timeline_changed.emit(changed)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._pan_start is not None:
            self._pan_start = None
            self.viewport().unsetCursor()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._drag and self._drag[0] == "playhead":
            self.scrub_finished.emit(self.playhead)
            self.playhead_changed.emit(self.playhead)
        self._drag = None

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            angle_delta = event.angleDelta()
            angle_value = angle_delta.y() if angle_delta.y() else angle_delta.x()
            if angle_value: self.zoom_by(1.2 if angle_value > 0 else 1 / 1.2)
            event.accept()
            return
        pixel_delta = event.pixelDelta()
        pixel_value = pixel_delta.x() if pixel_delta.x() else pixel_delta.y()
        if pixel_value:
            self._scroll_timeline(pixel_value)
        else:
            angle_delta = event.angleDelta()
            angle_value = angle_delta.x() if angle_delta.x() else angle_delta.y()
            step = max(40, min(160, self.horizontalScrollBar().pageStep() // 8))
            self._scroll_timeline(angle_value / 120 * step)
        event.accept()

    def _scroll_timeline(self, delta: float) -> None:
        """依滾輪距離水平移動時間軸"""
        scrollbar = self.horizontalScrollBar()
        scrollbar.setValue(scrollbar.value() - round(delta))

    def _pan_to(self, x: float) -> None:
        """依中鍵起點拖曳時間軸"""
        if self._pan_start is None: return
        start_x, start_scroll = self._pan_start
        self.horizontalScrollBar().setValue(start_scroll + round(start_x - x))

    def _paint_ruler(self, painter: QPainter) -> None:
        interval = self._tick_interval()
        start = max(0.0, self._time_at(self._content_left()))
        end = self._time_at(self.viewport().width())
        first_tick = math.floor(start / interval) * interval
        painter.save()
        painter.setClipRect(self.LABEL_WIDTH, 0, self.viewport().width() - self.LABEL_WIDTH, self.RULER_HEIGHT)
        minor_color = QColor(theme_color("text_muted"))
        minor_color.setAlpha(180)
        painter.setPen(minor_color)

        # 每個主刻度切成五格, 先畫較短的微刻度
        minor_interval = interval / 5
        tick = first_tick
        painter.setPen(QColor(theme_color("text_primary")))
        while tick <= end + interval:
            for index in range(1, 5):
                x = self._x_at(tick + minor_interval * index)
                painter.drawLine(round(x), self.RULER_HEIGHT - 7, round(x), self.RULER_HEIGHT)
            tick += interval

        tick = first_tick
        while tick <= end + interval:
            x = self._x_at(tick)
            painter.drawLine(round(x), 17, round(x), self.RULER_HEIGHT)
            painter.drawText(QRectF(x + 3, 1, 90, 16), _format_time(tick))
            tick += interval
        self._paint_output_handle(painter, "in", self.output_in)
        self._paint_output_handle(painter, "out", self.output_out)
        painter.restore()
        painter.fillRect(0, 0, self.LABEL_WIDTH, self.RULER_HEIGHT, QColor(theme_color("panel_background")))

    def _paint_markers(self, painter: QPainter) -> None:
        """在各軌繪製綁定來源時間的 sync markers"""
        painter.save()
        painter.setClipRect(self._content_left(), 0, self.viewport().width() - self._content_left(), self.viewport().height())
        for track in ("visual", "audio"):
            marker = self._marker_timeline_position(track)
            if marker is None: continue
            selected = self.selected_marker == track
            color = QColor(theme_color("accent"))
            line_color = QColor(color)
            line_color.setAlpha(150 if selected else 80)
            x = round(self._x_at(marker))
            rect = self._track_rect(track)
            painter.setPen(QPen(line_color, 1))
            painter.drawLine(x, round(rect.top() + 2), x, round(rect.bottom() - 2))
            color.setAlpha(255 if selected else 210)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawPolygon([
                QPoint(x - 5, round(rect.top() + 2)), QPoint(x + 5, round(rect.top() + 2)),
                QPoint(x + 5, round(rect.top() + 8)), QPoint(x, round(rect.top() + 13)),
                QPoint(x - 5, round(rect.top() + 8)),
            ])
        painter.restore()

    def _paint_track(self, painter: QPainter, track: str) -> None:
        rect = self._track_rect(track)
        painter.fillRect(rect, QColor(theme_color("input_background")))
        painter.setPen(self.palette().text().color())
        if not self.paths[track]:
            painter.drawText(
                QRectF(self._content_left() + 12, rect.top() + rect.height() / 2 - 11, 175, 22),
                Qt.AlignmentFlag.AlignVCenter, tr("Drop one file here"),
            )
        else:
            # Clip 與 loop block 只允許畫在左側標題欄以外
            painter.save()
            painter.setClipRect(QRectF(self.LABEL_WIDTH, rect.top(), self.viewport().width() - self.LABEL_WIDTH, rect.height()))
            timing = self.timings[track]
            if timing.loop:
                duration = max(0.01, timing.timeline_duration)
                first = timing.timeline_start - math.ceil(timing.timeline_start / duration) * duration
                value = first
                while value < self._horizon():
                    if abs(value - timing.timeline_start) > 0.001:
                        self._paint_clip(painter, track, value, duration, 75)
                    value += duration
            self._paint_clip(painter, track, timing.timeline_start, timing.timeline_duration, 220)
            painter.restore()

        # 固定左側標題欄最後繪製, 捲動內容不會蓋住文字
        painter.fillRect(QRectF(0, rect.top(), self.LABEL_WIDTH, rect.height()), QColor(theme_color("input_background")))
        painter.setPen(self.palette().text().color())
        painter.drawText(
            QRectF(8, rect.top(), self.LABEL_WIDTH - 58, rect.height()), Qt.AlignmentFlag.AlignVCenter,
            tr("Visual") if track == "visual" else tr("Audio"),
        )
    def _paint_clip(self, painter: QPainter, track: str, start: float, duration: float, alpha: int) -> None:
        track_rect = self._track_rect(track)
        rect = QRectF(self._x_at(start), track_rect.top() + 5, max(2.0, duration * self.pixels_per_second), track_rect.height() - 10)
        color = QColor(theme_color("accent"))
        color = color.lighter(112 if track == "visual" else 132)
        color.setAlpha(alpha)
        painter.setBrush(color)
        border = QColor(theme_color("text_primary"))
        border.setAlpha(120)
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(rect, 4, 4)
        asset = self.assets.get(track)
        if asset is not None and not asset.isNull() and rect.width() > 8:
            self._paint_asset(painter, track, rect, asset, alpha)
        if alpha > 100:
            painter.setPen(QColor(theme_color("text_primary")))
            painter.drawText(
                rect.adjusted(7, 5, -7, -5),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, Path(self.paths[track]).name,
            )

    def _paint_asset(self, painter: QPainter, track: str, rect: QRectF, asset: QPixmap, alpha: int) -> None:
        """依來源時間裁切 overview, trimming 時不縮放內部圖案"""
        if track == "visual":
            self._paint_video_thumbnails(painter, rect, asset, alpha)
            return
        visible, target = self._asset_rects(track, rect)
        painter.save()
        painter.setClipRect(visible)
        painter.setOpacity(0.9 if alpha > 100 else 0.25)
        painter.drawPixmap(target, asset, QRectF(asset.rect()))
        painter.restore()

    def _paint_video_thumbnails(self, painter: QPainter, rect: QRectF, asset: QPixmap, alpha: int) -> None:
        """以固定長寬比排列縮圖, 時間軸縮放只改變取樣數量"""
        visible = rect.adjusted(2, 2, -2, -3)
        source_tile_width = asset.height() * 16 / 9
        frame_count = max(1, round(asset.width() / source_tile_width))
        source_tile_width = asset.width() / frame_count
        target_width = max(1.0, visible.height() * source_tile_width / asset.height())
        source_duration = self._source_duration("visual")
        source_in = self.timings["visual"].source_in
        painter.save()
        painter.setClipRect(visible)
        painter.setOpacity(0.9 if alpha > 100 else 0.25)
        x = visible.left()
        while x < visible.right():
            source_time = source_in + max(0.0, x + target_width / 2 - rect.left()) / self.pixels_per_second
            frame = 0 if not source_duration else min(frame_count - 1, int(source_time / source_duration * frame_count))
            painter.drawPixmap(
                QRectF(x, visible.top(), target_width, visible.height()), asset,
                QRectF(frame * source_tile_width, 0, source_tile_width, asset.height()),
            )
            x += target_width
        painter.restore()

    def _asset_rects(self, track: str, rect: QRectF) -> tuple[QRectF, QRectF]:
        """取得 overview 的可見裁切區與固定時間比例繪製區"""
        visible = rect.adjusted(2, 2, -2, -3)
        source_duration = self._source_duration(track)
        if source_duration is None: return visible, visible
        target = QRectF(
            rect.left() - self.timings[track].source_in * self.pixels_per_second, visible.top(),
            max(1.0, source_duration * self.pixels_per_second), visible.height(),
        )
        return visible, target

    def _paint_output_handle(self, painter: QPainter, mode: str, seconds: float) -> None:
        """使用方向與 theme 色彩區分 output in/out handle"""
        x = round(self._x_at(seconds))
        bottom, top = self.RULER_HEIGHT, self.RULER_HEIGHT - 10
        color = QColor(theme_color("accent"))
        painter.setPen(QPen(color, 2))
        painter.setBrush(color)
        painter.drawLine(x, 14, x, bottom)
        side = 8 if mode == "in" else -8
        painter.drawPolygon([QPoint(x, bottom), QPoint(x + side, bottom), QPoint(x, top)])

    def _output_handle_at(self, point: QPoint) -> str | None:
        """在 ruler 底部選取 handle, 重疊時由左右旗標分開操作"""
        if point.y() < self.RULER_HEIGHT - 12 or point.y() > self.RULER_HEIGHT: return None
        in_x, out_x = self._x_at(self.output_in), self._x_at(self.output_out)
        in_hit, out_hit = abs(point.x() - in_x) <= 9, abs(point.x() - out_x) <= 9
        if in_hit and out_hit:
            if abs(in_x - out_x) <= 2: return "out" if point.x() < (in_x + out_x) / 2 else "in"
            return "in" if abs(point.x() - in_x) <= abs(point.x() - out_x) else "out"
        if in_hit: return "in"
        if out_hit: return "out"
        return None

    def _marker_at(self, point: QPoint) -> str | None:
        """取得游標附近的軌道 marker"""
        track = self._track_at(point)
        marker = self._marker_timeline_position(track) if track is not None else None
        return track if marker is not None and abs(point.x() - self._x_at(marker)) <= 7 else None

    def _select_marker(self, marker: str | None) -> None:
        changed = marker != self.selected_marker
        self.selected_marker = marker
        self.viewport().update()
        if changed: self.marker_selection_changed.emit(marker is not None)

    def _marker_timeline_position(
        self, track: str | None, timing: ReplacementClipTiming | None = None,
    ) -> float | None:
        """將來源 marker 換算為目前 timeline 位置"""
        if track not in self.track_markers or self.track_markers[track] is None: return None
        timing = self.timings[track] if timing is None else timing
        source_time = self.track_markers[track]
        if source_time < timing.source_in or source_time > timing.source_in + timing.timeline_duration: return None
        return timing.timeline_start + source_time - timing.source_in

    def _marker_alignment_delta(
        self, track: str, raw_delta: float, original: ReplacementClipTiming, bypass: bool = False,
    ) -> float | None:
        """移動 clip 時讓自身 marker 吸附另一軌 marker"""
        if not self.snapping or bypass: return None
        own_marker = self._marker_timeline_position(track, original)
        other_track = "audio" if track == "visual" else "visual"
        other_marker = self._marker_timeline_position(other_track)
        if own_marker is None or other_marker is None: return None
        difference = other_marker - (own_marker + raw_delta)
        return raw_delta + difference if abs(difference) * self.pixels_per_second <= 8 else None

    def _snap(
        self, value: float, excluded_track: str | None = None,
        exclude_output: bool = False, excluded_marker: float | None = None, bypass: bool = False,
    ) -> float:
        if not self.snapping or bypass: return round(max(0.0, value), 3)
        interval = self._tick_interval()
        candidates = [round(value / interval) * interval, self.playhead]
        if not exclude_output: candidates += [self.output_in, self.output_out]
        candidates += [
            marker for track in ("visual", "audio")
            if track != excluded_marker and (marker := self._marker_timeline_position(track)) is not None
        ]
        for track, timing in self.timings.items():
            if track == excluded_track: continue
            candidates += [timing.timeline_start, timing.timeline_start + timing.timeline_duration]
        closest = min(candidates, key=lambda item: abs(item - value))
        return round(closest if abs(closest - value) * self.pixels_per_second <= 8 else value, 3)

    def _set_track_loop(self, track: str, checked: bool) -> None:
        """由軌道按鈕更新 loop 狀態"""
        if not self.paths[track] or self._is_still(track): return
        self.timings[track].loop = checked
        self.timeline_changed.emit(self.timeline())
        self.viewport().update()

    def _layout_track_buttons(self) -> None:
        """將軌道操作固定在標題列與空軌提示旁"""
        for track in ("visual", "audio"):
            rect, buttons = self._track_rect(track), self.track_buttons[track]
            has_source = bool(self.paths[track])
            button_top = round(rect.top() + rect.height() / 2 - 10)
            buttons["clear"].setVisible(has_source)
            buttons["clear"].setGeometry(self.LABEL_WIDTH - 23, button_top, 20, 20)
            buttons["loop"].setVisible(has_source and not self._is_still(track))
            if buttons["loop"].isChecked() != self.timings[track].loop:
                buttons["loop"].blockSignals(True)
                buttons["loop"].setChecked(self.timings[track].loop)
                buttons["loop"].blockSignals(False)
            buttons["loop"].setGeometry(self.LABEL_WIDTH - 45, button_top, 20, 20)
            buttons["browse"].setVisible(not has_source)
            buttons["browse"].setGeometry(
                self._content_left() + 192, round(rect.top() + rect.height() / 2 - 14), 104, 28,
            )

    def _refresh_track_button_icons(self) -> None:
        """依目前 theme 更新軌道操作圖示"""
        for buttons in self.track_buttons.values():
            buttons["loop"].setIcon(_editor_icon("timeline-loop"))
            buttons["loop"].setIconSize(QSize(16, 16))
            buttons["clear"].setIcon(_editor_icon("window-close"))
            buttons["clear"].setIconSize(QSize(12, 12))

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._layout_track_buttons()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in {QEvent.Type.PaletteChange, QEvent.Type.StyleChange} and hasattr(self, "track_buttons"):
            self._refresh_track_button_icons()

    def _tick_interval(self) -> float:
        for interval in (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 300, 600):
            if interval * self.pixels_per_second >= 112: return interval
        return 1800.0

    def _track_rect(self, track: str) -> QRectF:
        index = 0 if track == "visual" else 1
        available = self.viewport().height() - self.RULER_HEIGHT - self.TRACK_GAP - self.TRACK_MARGIN * 2
        height = max(self.MINIMUM_TRACK_HEIGHT, available / 2)
        top = self.RULER_HEIGHT + self.TRACK_MARGIN + index * (height + self.TRACK_GAP)
        return QRectF(0, top, self.viewport().width(), height)

    def _clip_rect(self, track: str) -> QRectF:
        timing, rect = self.timings[track], self._track_rect(track)
        return QRectF(
            self._x_at(timing.timeline_start), rect.top() + 5,
            max(2.0, timing.timeline_duration * self.pixels_per_second), rect.height() - 10,
        )

    def _track_at(self, point: QPoint) -> str | None:
        for track in ("visual", "audio"):
            if self._track_rect(track).contains(point): return track
        return None

    def _source_duration(self, track: str) -> float | None:
        return _duration(self.probes.get(track, {}), "video" if track == "visual" else "audio")

    def _is_still(self, track: str) -> bool:
        return track == "visual" and bool(self.paths[track]) and self._source_duration(track) is None

    def _minimum_duration(self, track: str) -> float:
        """影片至少保留一個 frame, 其他來源至少保留 10 ms"""
        if track != "visual" or self._is_still(track): return 0.01
        stream = next((item for item in self.probes.get(track, {}).get("streams") or [] if item.get("codec_type") == "video"), {})
        value = str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "30")
        try:
            numerator, denominator = value.split("/", 1)
            rate = float(numerator) / max(float(denominator), 0.001)
        except (TypeError, ValueError):
            try: rate = float(value)
            except (TypeError, ValueError): rate = 30.0
        return 1 / max(1.0, rate)

    def _horizon(self) -> float:
        ends = [self.output_out, 5.0]
        ends += [timing.timeline_start + timing.timeline_duration for timing in self.timings.values()]
        return max(ends) * 1.05

    def _update_scrollbar(self) -> None:
        content = round(self._horizon() * self.pixels_per_second + self.RIGHT_GUTTER)
        visible = max(1, self.viewport().width() - self._content_left())
        self.horizontalScrollBar().setRange(0, max(0, content - visible))
        self.horizontalScrollBar().setPageStep(visible)

    def _x_at(self, seconds: float) -> float:
        return self._content_left() + seconds * self.pixels_per_second - self.horizontalScrollBar().value()

    def _time_at(self, x: float) -> float:
        value = x - self._content_left() + self.horizontalScrollBar().value()
        return max(0.0, value / self.pixels_per_second)

    def _content_left(self) -> int:
        return self.LABEL_WIDTH + self.PLAYHEAD_GUTTER

    @staticmethod
    def _local_paths(mime_data: Any) -> list[str]:
        return [url.toLocalFile() for url in mime_data.urls() if url.isLocalFile() and Path(url.toLocalFile()).is_file()]


class PreviewPane(QFrame):
    """顯示代表圖、RAM preview player 與 transport controls"""

    position_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("role", "card")
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.video_widget = QVideoWidget(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.timeline_in, self.timeline_out = 0.0, 0.0
        self.window_start, self.window_end = 0.0, 0.0
        self._buffer: QBuffer | None = None
        self._segments: list[_PreviewSegment] = []
        self._current_segment: _PreviewSegment | None = None
        self._queued_load: tuple[_PreviewSegment, float, bool] | None = None
        self._pending_handoff: tuple[_PreviewSegment, float, bool] | None = None
        self._handoff_scheduled = False
        self._load_in_progress = False
        self._pending_seek: int | None = None
        self._resume_after_load = False
        self._playback_origin = 0.0
        self._playback_session = False
        self._cache_miss_waiting = False
        self._retained_segments: set[tuple[float, float]] = set()
        self._stale = True

        self.representative = QLabel(tr("Drop visual and audio sources into the timeline"))
        self.representative.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.representative.setScaledContents(False)
        self.status_overlay = QLabel()
        self.status_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_overlay.setProperty("role", "previewOverlay")
        display = QWidget()
        display.setMinimumHeight(250)
        stack = QStackedLayout(display)
        stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        stack.addWidget(self.representative)
        stack.addWidget(self.video_widget)
        stack.addWidget(self.status_overlay)

        self.play_button = QPushButton()
        self.stop_button = QPushButton()
        self.time_label = QLabel("00:00:00.000 / 00:00:00.000")
        controls = QHBoxLayout()
        controls.addWidget(self.play_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.time_label)
        controls.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(display, 1)
        layout.addLayout(controls)

        self.play_button.clicked.connect(self._toggle_playback)
        self.stop_button.clicked.connect(self._stop_playback)
        self.player.positionChanged.connect(self._player_position_changed)
        self.player.durationChanged.connect(lambda _value: self._refresh_time())
        self.player.playbackStateChanged.connect(self._refresh_play_button)
        self.player.mediaStatusChanged.connect(self._media_status_changed)
        self.player.errorOccurred.connect(lambda _error, message: self.set_error(message))
        self.play_button.setIconSize(QSize(17, 17))
        self.stop_button.setIconSize(QSize(17, 17))
        self.stop_button.setAccessibleName(tr("Stop"))
        self.stop_button.setToolTip(tr("Stop"))
        self._refresh_play_button(self.player.playbackState())
        self._set_transport_enabled(False)

    def set_timeline_range(self, start: float, end: float) -> None:
        self.timeline_in, self.timeline_out = max(0.0, start), max(start, end)
        if self.window_start + self.player.position() / 1000 > self.timeline_out:
            self._pause_at_output_out()
        self._refresh_time()

    def set_representative(self, pixmap: QPixmap | None) -> None:
        if pixmap is None or pixmap.isNull(): return
        self.representative.setPixmap(pixmap.scaled(
            640, 360, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
        ))

    def mark_stale(self) -> None:
        """顯示 cache miss, 保留其他已完成 segments"""
        self.player.pause()
        self._refresh_play_button(QMediaPlayer.PlaybackState.PausedState)
        self._playback_session = False
        self._cache_miss_waiting = True
        self.status_overlay.hide()
        self._set_transport_enabled(False)

    def clear_cache(self) -> None:
        """時間軸內容改變時釋放所有 RAM preview segments"""
        self._segments.clear()
        self._current_segment = None
        self._playback_session = False
        self._cache_miss_waiting = False
        self._retained_segments.clear()
        self.release_preview()
        self.status_overlay.setText(tr("Preview needs to be updated"))
        self.status_overlay.show()

    def retain_cache_segments(self, ranges: list[tuple[float, float]]) -> None:
        """依 controller 淘汰結果釋放遠離 playhead 的 segments"""
        self._retained_segments = {
            (round(float(start), 3), round(float(end), 3)) for start, end in ranges
        }
        self._segments = [
            segment for segment in self._segments
            if segment is self._current_segment or self._segment_key(segment) in self._retained_segments
        ]

    def set_preview(self, data: bytes, window_start: float, window_end: float, focus: float) -> None:
        """保存完成的 RAM segment, 必要時載入到 QMediaPlayer"""
        segment = _PreviewSegment(data, window_start, window_end)
        self._segments.append(segment)
        self._segments.sort(key=lambda item: item.start)
        if self._cache_miss_waiting and self._contains(segment, focus):
            self._load_segment(segment, focus)
            self._cache_miss_waiting = False
        elif self._current_segment is None and self._contains(segment, focus):
            self._load_segment(segment, focus)

    def _load_segment(self, segment: _PreviewSegment, focus: float, resume: bool = False) -> None:
        """依序切換 player segment, 載入期間只保留最新目標"""
        if self._load_in_progress:
            self._queued_load = (segment, focus, resume)
            return
        self._queued_load = None
        blocker = QSignalBlocker(self.player)
        self.player.stop()
        self.player.setSource(QUrl())
        if self._buffer is not None:
            self._buffer.close()
            self._buffer.deleteLater()
        self._buffer = QBuffer(self)
        self._buffer.setData(QByteArray(segment.data))
        self._buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self._current_segment = segment
        self._segments = [
            item for item in self._segments
            if item is segment or self._segment_key(item) in self._retained_segments
        ]
        self.window_start, self.window_end = segment.start, segment.end
        self._pending_seek = max(0, round((focus - segment.start) * 1000))
        self._resume_after_load = resume
        self._stale = True
        self._load_in_progress = True
        self.player.setSourceDevice(self._buffer, QUrl("memory-preview.mp4"))
        del blocker
        if not resume: self._refresh_play_button(QMediaPlayer.PlaybackState.StoppedState)
        self.video_widget.show()
        if resume:
            self.status_overlay.hide()
        else:
            self.status_overlay.setText(tr("Loading preview..."))
            self.status_overlay.show()
        self._set_transport_enabled(False)

    def set_error(self, message: str) -> None:
        queued_load = self._queued_load if self._load_in_progress else None
        if self._load_in_progress:
            self.release_preview()
            if queued_load is not None:
                self._queued_load = queued_load
                QTimer.singleShot(0, self._load_queued_segment)
                return
        self._stale = True
        self.status_overlay.setText(f"{tr('Preview failed')}\n{tr(message)}")
        self.status_overlay.show()
        self._set_transport_enabled(False)

    def seek_timeline(self, seconds: float) -> bool:
        """在任一已完成 segment 內 seek, cache miss 時回傳 False"""
        segment = self._segment_at(seconds)
        if segment is None: return False
        self._playback_session = False
        if segment is not self._current_segment or self._stale or self._load_in_progress:
            self._load_segment(segment, seconds)
        else:
            self.player.setPosition(max(0, round((seconds - segment.start) * 1000)))
        return True

    def release_preview(self) -> None:
        blocker = QSignalBlocker(self.player)
        self._load_in_progress = False
        self._queued_load = None
        self._pending_handoff = None
        self._handoff_scheduled = False
        self._pending_seek = None
        self._resume_after_load = False
        self.player.stop()
        self.player.setSource(QUrl())
        if self._buffer is not None:
            self._buffer.close()
            self._buffer.deleteLater()
            self._buffer = None
        del blocker
        self._stale = True
        self._refresh_play_button(QMediaPlayer.PlaybackState.StoppedState)

    def _toggle_playback(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            position = self.player.position()
            self.player.pause()
            self.player.setPosition(position)
            self.position_changed.emit(self.window_start + position / 1000)
            return
        if self.player.duration() > 0 and self.player.position() >= self.player.duration() - 5:
            next_segment = self._next_segment()
            if next_segment is not None:
                self._load_segment(next_segment, next_segment.start, True)
                return
        if not self._playback_session:
            self._playback_origin = self.window_start + self.player.position() / 1000
            self._playback_session = True
        self.player.play()

    def _stop_playback(self) -> None:
        """停止時回到本次播放開始前的 playhead"""
        target = self._playback_origin if self._playback_session else self.window_start + self.player.position() / 1000
        self.player.pause()
        self._playback_session = False
        self.seek_timeline(target)
        self.position_changed.emit(target)

    def _player_position_changed(self, position: int) -> None:
        if self._load_in_progress: return
        current = self.window_start + position / 1000
        if self.timeline_out > self.timeline_in and current >= self.timeline_out:
            self._pause_at_output_out()
            return
        self._refresh_time()
        self.position_changed.emit(current)

    def _pause_at_output_out(self) -> None:
        """播放抵達整體 output out 時停在邊界"""
        self.player.pause()
        target = max(0, round((self.timeline_out - self.window_start) * 1000))
        if self.player.position() != target: self.player.setPosition(target)
        self._refresh_time()
        self.position_changed.emit(self.timeline_out)

    def _refresh_time(self) -> None:
        current = self.window_start + self.player.position() / 1000
        self.time_label.setText(f"{_format_time(current)} / {_format_time(self.timeline_out)}")

    def _media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            next_segment = self._next_segment()
            if self._playback_session and next_segment is not None and next_segment is not self._current_segment:
                self._pending_handoff = (next_segment, next_segment.start, True)
                if not self._handoff_scheduled:
                    self._handoff_scheduled = True
                    QTimer.singleShot(0, self._perform_handoff)
            return
        if status not in {QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia}: return
        if not self._load_in_progress: return
        if self._pending_seek is not None:
            pending_seek, self._pending_seek = self._pending_seek, None
            self.player.setPosition(pending_seek)
        self._load_in_progress = False
        self._stale = False
        self.status_overlay.hide()
        self._set_transport_enabled(True)
        resume, self._resume_after_load = self._resume_after_load, False
        if self._queued_load is not None:
            QTimer.singleShot(0, self._load_queued_segment)
        elif resume:
            self.player.play()

    def _load_queued_segment(self) -> None:
        """載入 scrub 期間留下的最後一個 segment 目標"""
        if self._load_in_progress or self._queued_load is None: return
        segment, focus, resume = self._queued_load
        self._queued_load = None
        if segment is self._current_segment and not self._stale:
            self.player.setPosition(max(0, round((focus - segment.start) * 1000)))
            if resume: self.player.play()
            return
        self._load_segment(segment, focus, resume)

    def _perform_handoff(self) -> None:
        """離開 EndOfMedia callback 後切換到下一個 segment"""
        self._handoff_scheduled = False
        if not self._playback_session or self._pending_handoff is None:
            self._pending_handoff = None
            return
        segment, focus, resume = self._pending_handoff
        self._pending_handoff = None
        self._load_segment(segment, focus, resume)

    def _contains(self, segment: _PreviewSegment, seconds: float) -> bool:
        """一般 segment 使用左閉右開, 整體 output out 才包含右端"""
        if abs(seconds - self.timeline_out) <= 0.001:
            return segment.start - 0.001 <= seconds <= segment.end + 0.001
        return segment.start - 0.001 <= seconds < segment.end

    def _segment_at(self, seconds: float) -> _PreviewSegment | None:
        return next((segment for segment in self._segments if self._contains(segment, seconds)), None)

    @staticmethod
    def _segment_key(segment: _PreviewSegment) -> tuple[float, float]:
        return round(segment.start, 3), round(segment.end, 3)

    def _next_segment(self) -> _PreviewSegment | None:
        return next((
            segment for segment in self._segments
            if segment is not self._current_segment and segment.start < self.timeline_out - 0.001
            and abs(segment.start - self.window_end) <= 0.01
        ), None)

    def _refresh_play_button(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setIcon(_editor_icon("media-pause" if playing else "media-play"))
        self.stop_button.setIcon(_editor_icon("media-stop"))
        self.play_button.setAccessibleName(tr("Pause") if playing else tr("Play"))
        self.play_button.setToolTip(tr("Pause") if playing else tr("Play"))

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in {QEvent.Type.PaletteChange, QEvent.Type.StyleChange} and hasattr(self, "player"):
            self._refresh_play_button(self.player.playbackState())

    def _set_transport_enabled(self, enabled: bool) -> None:
        self.play_button.setEnabled(enabled)
        self.stop_button.setEnabled(enabled)


class ReplacementEditor(QWidget):
    """組合 preview、時間軸與編輯工具列"""

    source_dropped = Signal(str, str)
    source_browse_requested = Signal(str)
    source_clear_requested = Signal(str)
    timeline_changed = Signal(object)
    output_range_changed = Signal(object)
    preview_requested = Signal(float)
    cache_focus_changed = Signal(float)
    cache_invalidated = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumWidth(240)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.preview = PreviewPane()
        self.timeline = TimelineWidget()
        self._scrub_target: float | None = None
        self._scrub_timer = QTimer(self)
        self._scrub_timer.setSingleShot(True)
        self._scrub_timer.setInterval(100)
        self._scrub_timer.timeout.connect(self._flush_scrub)
        self.zoom_out_button = QPushButton()
        self.zoom_in_button = QPushButton()
        self.fit_button = QPushButton()
        self.snap_button = QPushButton()
        self.add_marker_button = QPushButton()
        self.remove_marker_button = QPushButton()
        self.snap_button.setCheckable(True)
        self.snap_button.setChecked(True)
        self.add_marker_button.setCheckable(True)
        self.remove_marker_button.setEnabled(False)
        self.output_duration_title = QLabel("Output Duration")
        self.output_duration_label = QLabel()
        self.output_duration_title.setProperty("role", "muted")
        self.output_duration_label.setProperty("role", "muted")
        for button in (
            self.add_marker_button, self.remove_marker_button, self.snap_button,
            self.zoom_out_button, self.fit_button, self.zoom_in_button,
        ):
            button.setProperty("role", "ghost")
            button.setProperty("compact", True)
            button.setIconSize(QSize(17, 17))
        for button, label in (
            (self.add_marker_button, "Add Marker"), (self.remove_marker_button, "Remove Marker"),
            (self.snap_button, "Snap"), (self.zoom_out_button, "Zoom Out"),
            (self.fit_button, "Fit"), (self.zoom_in_button, "Zoom In"),
        ):
            button.setAccessibleName(tr(label))
            button.setToolTip(tr(label))
        self.add_marker_button.setToolTip(tr("Choose a visual or audio track position for its sync marker"))
        self.snap_button.setToolTip(tr("Hold Alt or Option while dragging to temporarily bypass snapping"))
        for button in (
            self.add_marker_button, self.remove_marker_button, self.snap_button,
            self.zoom_out_button, self.fit_button, self.zoom_in_button,
        ): button.setFixedWidth(30)
        self._refresh_control_icons()
        tools = QHBoxLayout()
        tools.setContentsMargins(0, 0, 0, 0)
        tools.addWidget(self.add_marker_button)
        tools.addWidget(self.remove_marker_button)
        tools.addStretch()
        tools.addWidget(self.output_duration_title)
        tools.addWidget(self.output_duration_label)
        tools.addSpacing(8)
        tools.addWidget(self.snap_button)
        tools.addWidget(self.zoom_out_button)
        tools.addWidget(self.fit_button)
        tools.addWidget(self.zoom_in_button)
        self.timeline_panel = QFrame()
        self.timeline_panel.setProperty("role", "card")
        timeline_layout = QVBoxLayout(self.timeline_panel)
        timeline_layout.setContentsMargins(10, 8, 10, 10)
        timeline_layout.setSpacing(6)
        timeline_layout.addLayout(tools)
        timeline_layout.addWidget(self.timeline)
        self.vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        self.vertical_splitter.setChildrenCollapsible(False)
        self.vertical_splitter.setHandleWidth(9)
        self.vertical_splitter.addWidget(self.preview)
        self.vertical_splitter.addWidget(self.timeline_panel)
        self.vertical_splitter.setStretchFactor(0, 3)
        self.vertical_splitter.setStretchFactor(1, 2)
        self.vertical_splitter.setSizes([420, 280])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 5, 0)
        layout.addWidget(self.vertical_splitter)

        self.timeline.source_dropped.connect(self.source_dropped)
        self.timeline.source_browse_requested.connect(self.source_browse_requested)
        self.timeline.source_clear_requested.connect(self.source_clear_requested)
        self.timeline.timeline_changed.connect(self._timeline_changed)
        self.timeline.output_range_changed.connect(self._output_range_changed)
        self.timeline.scrub_started.connect(self._start_scrub)
        self.timeline.scrub_moved.connect(self._queue_scrub)
        self.timeline.scrub_finished.connect(self._finish_scrub)
        self.preview.position_changed.connect(self._preview_position_changed)
        self.zoom_out_button.clicked.connect(lambda: self.timeline.zoom_by(1 / 1.25))
        self.zoom_in_button.clicked.connect(lambda: self.timeline.zoom_by(1.25))
        self.fit_button.clicked.connect(self.timeline.fit_timeline)
        self.snap_button.toggled.connect(self.timeline.set_snapping)
        self.add_marker_button.toggled.connect(self.timeline.set_marker_placement)
        self.remove_marker_button.clicked.connect(self.timeline.remove_selected_marker)
        self.timeline.marker_selection_changed.connect(self.remove_marker_button.setEnabled)
        self.timeline.marker_placement_finished.connect(lambda: self.add_marker_button.setChecked(False))
        self.refresh_output_duration()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in {QEvent.Type.PaletteChange, QEvent.Type.StyleChange} and hasattr(self, "snap_button"):
            self._refresh_control_icons()

    def _refresh_control_icons(self) -> None:
        """依目前 theme 更新時間軸控制圖示"""
        self.snap_button.setIcon(_editor_icon("timeline-snap"))
        self.add_marker_button.setIcon(_editor_icon("timeline-marker-add"))
        self.remove_marker_button.setIcon(_editor_icon("timeline-marker-remove"))
        self.zoom_out_button.setIcon(_editor_icon("timeline-zoom-out"))
        self.fit_button.setIcon(_editor_icon("timeline-fit"))
        self.zoom_in_button.setIcon(_editor_icon("timeline-zoom-in"))

    def _timeline_changed(self, timeline: ReplacementTimeline) -> None:
        self.refresh_output_duration(timeline)
        self.preview.set_timeline_range(timeline.output_in, timeline.output_out)
        self.invalidate_preview_cache()
        self.timeline_changed.emit(timeline)

    def _output_range_changed(self, timeline: ReplacementTimeline) -> None:
        """整體 in/out 改變時保留已有 cache 並更新延伸範圍"""
        self.refresh_output_duration(timeline)
        self.preview.set_timeline_range(timeline.output_in, timeline.output_out)
        self.output_range_changed.emit(timeline)

    def refresh_output_duration(self, timeline: ReplacementTimeline | None = None) -> None:
        """更新工具列顯示的整體輸出長度"""
        current = timeline or self.timeline.timeline()
        self.output_duration_label.setText(_format_duration(current.output_out - current.output_in))

    def _playhead_changed(self, seconds: float) -> None:
        self.cache_focus_changed.emit(seconds)
        if self.preview.seek_timeline(seconds): return
        self.preview.mark_stale()
        self.preview_requested.emit(seconds)

    def _start_scrub(self, seconds: float) -> None:
        """立即套用 scrub 起點並清除較舊請求"""
        self._scrub_timer.stop()
        self._scrub_target = None
        self._playhead_changed(seconds)

    def _queue_scrub(self, seconds: float) -> None:
        """拖曳期間每 100 ms 最多送出一次 seek"""
        self._scrub_target = seconds
        if not self._scrub_timer.isActive(): self._scrub_timer.start()

    def _flush_scrub(self) -> None:
        if self._scrub_target is None: return
        target, self._scrub_target = self._scrub_target, None
        self._playhead_changed(target)

    def _finish_scrub(self, seconds: float) -> None:
        """放開 playhead 時立即套用最後位置"""
        self._scrub_timer.stop()
        self._scrub_target = None
        self._playhead_changed(seconds)

    def _preview_position_changed(self, seconds: float) -> None:
        self.timeline.set_playhead(seconds)
        self.cache_focus_changed.emit(seconds)

    def invalidate_preview_cache(self) -> None:
        """釋放 player segments 並通知 controller 清除 cache metadata"""
        self.preview.clear_cache()
        self.timeline.set_cached_ranges([])
        self.timeline.set_cached_segments([])
        self.cache_invalidated.emit()
