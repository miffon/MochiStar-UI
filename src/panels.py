import html
import logging
import math
import re
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import Property, QAbstractTableModel, QEvent, QModelIndex, QObject, QSize, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import (
    QColor, QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent,
    QPaintEvent, QPainter, QPen, QPixmap, QTextBlockFormat, QTextCharFormat, QTextCursor, QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from models import ConversionPreset, FormatInfo, MediaInfo, SubtitleTrack, TaskKind, TaskRecord, TaskStatus
from theme import theme_color


class NoWheelComboBox(QComboBox):
    """忽略滑鼠滾輪, 避免誤改選項"""

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


class FileDropListWidget(QListWidget):
    """接收檔案總管拖入的本機檔案"""

    files_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setProperty("dragActive", False)

    @staticmethod
    def local_file_paths(mime_data: Any) -> list[str]:
        """從 mime data 取出存在的本機檔案, 忽略資料夾與網路 URL"""
        if not mime_data or not mime_data.hasUrls(): return []
        paths = []
        for url in mime_data.urls():
            path = url.toLocalFile() if url.isLocalFile() else ""
            normalized = str(Path(path)) if path else ""
            if normalized and Path(normalized).is_file() and normalized not in paths: paths.append(normalized)
        return paths

    def _set_drag_active(self, active: bool) -> None:
        if self.property("dragActive") is active: return
        self.setProperty("dragActive", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self.local_file_paths(event.mimeData()):
            self._set_drag_active(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self.local_file_paths(event.mimeData()): event.acceptProposedAction()
        else: event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._set_drag_active(False)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = self.local_file_paths(event.mimeData())
        self._set_drag_active(False)
        if not paths:
            event.ignore()
            return
        self.files_dropped.emit(paths)
        event.acceptProposedAction()


def _add_option_row(
    layout: QFormLayout, title: str, field: QWidget | QHBoxLayout | QGridLayout, tooltip: str = "",
) -> QLabel:
    """加入帶說明 tooltip 的設定標題"""
    label = QLabel(title)
    if tooltip: label.setToolTip(tooltip)
    if isinstance(field, QWidget):
        label.setBuddy(field)
        layout.addRow(label, field)
    else:
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        field.setContentsMargins(0, 0, 0, 0)
        container.setLayout(field)
        layout.addRow(label, container)
    return label


def _set_combo_item_enabled(combo: QComboBox, value: Any, enabled: bool) -> None:
    """依 item data 啟用或停用選項"""
    index = combo.findData(value)
    item = combo.model().item(index) if index >= 0 and hasattr(combo.model(), "item") else None
    if item is not None: item.setEnabled(enabled)


class _SortableTableWidgetItem(QTableWidgetItem):
    """支援使用顯示文字以外的值排序"""

    SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 20

    def __init__(self, text: str = "", sort_value: Any = None):
        super().__init__(text)
        if sort_value is not None: self.setData(self.SORT_ROLE, sort_value)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        left, right = self.data(self.SORT_ROLE), other.data(self.SORT_ROLE)
        if left is not None and right is not None: return left < right
        return super().__lt__(other)


class _CheckableTableWidgetItem(QTableWidgetItem):
    """依 checkbox 狀態排序的 table item"""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        return self.checkState().value < other.checkState().value


_ORIGINAL_ROW_ROLE = int(Qt.ItemDataRole.UserRole) + 21


def _restore_table_original_order(table: QTableWidget) -> None:
    """取消排序時依建立順序還原 QTableWidget rows"""
    if not table.rowCount(): return
    table.setSortingEnabled(False)
    rows = []
    for row in range(table.rowCount()):
        items = [table.takeItem(row, column) for column in range(table.columnCount())]
        widgets = [table.cellWidget(row, column) for column in range(table.columnCount())]
        for column, widget in enumerate(widgets):
            if widget is not None: table.removeCellWidget(row, column)
        order = items[0].data(_ORIGINAL_ROW_ROLE) if items[0] is not None else row
        rows.append((int(order), items, widgets))
    table.setRowCount(0)
    for order, items, widgets in sorted(rows, key=lambda values: values[0]):
        row = table.rowCount()
        table.insertRow(row)
        for column, item in enumerate(items):
            if item is not None: table.setItem(row, column, item)
        for column, widget in enumerate(widgets):
            if widget is not None: table.setCellWidget(row, column, widget)
    table.setSortingEnabled(True)


def _enable_clearable_sorting(table: QTableView, restore_widget_order: bool = False) -> None:
    """讓 header 依序切換升冪、降冪與原始順序"""
    header = table.horizontalHeader()
    header.setSortIndicatorClearable(True)
    if restore_widget_order and isinstance(table, QTableWidget):
        header.sortIndicatorChanged.connect(
            lambda section, _order: _restore_table_original_order(table) if section < 0 else None
        )


def _set_all_table_items_checked(table: QTableWidget, checked: bool) -> None:
    """批次更新 checkbox, 避免排序中的 row 移動造成遺漏"""
    sorting_enabled = table.isSortingEnabled()
    table.setSortingEnabled(False)
    state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
    for row in range(table.rowCount()): table.item(row, 0).setCheckState(state)
    table.setSortingEnabled(sorting_enabled)


class _TableWidthController(QObject):
    """套用預設欄寬並限制總寬不超過 viewport"""

    def __init__(self, table: QTableView, title_column: int):
        super().__init__(table)
        self.table = table
        self.title_column = title_column
        self._adjusting = False
        self._initialized = False
        self._has_saved_widths = False
        table.viewport().installEventFilter(self)
        table.horizontalHeader().sectionResized.connect(self._section_resized)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in {QEvent.Type.Show, QEvent.Type.Resize}:
            try:
                self.fit()
            except RuntimeError:
                pass # Qt 關閉時 table 可能已先被釋放
        return super().eventFilter(watched, event)

    def fit(self, *_: Any) -> None:
        """縮放欄位以填滿目前可見寬度"""
        if self._adjusting or not self.table.isVisible(): return
        header = self.table.horizontalHeader()
        available = self.table.viewport().width()
        if available <= 0 or header.count() == 0: return
        if not self._initialized:
            self._initialized = True
            if not self._has_saved_widths: self._apply_default_widths(available)
        widths = [header.sectionSize(column) for column in range(header.count())]
        difference = available - sum(widths)
        if difference == 0: return

        self._adjusting = True
        try:
            if difference > 0:
                header.resizeSection(self.title_column, widths[self.title_column] + difference)
                return
            remaining = -difference
            shrink_order = [self.title_column, *(
                column for column in reversed(range(header.count())) if column != self.title_column
            )]
            for column in shrink_order:
                width = header.sectionSize(column)
                reduction = min(remaining, max(0, width - header.minimumSectionSize()))
                if reduction: header.resizeSection(column, width - reduction)
                remaining -= reduction
                if remaining <= 0: break
        finally:
            self._adjusting = False

    def _section_resized(self, column: int, _old_size: int, _new_size: int) -> None:
        """手動拖曳時由相鄰欄位補償, 保持分隔線移動方向直覺"""
        if self._adjusting or not self.table.isVisible(): return
        header = self.table.horizontalHeader()
        available = self.table.viewport().width()
        difference = available - sum(header.sectionSize(index) for index in range(header.count()))
        if difference == 0: return
        self._adjusting = True
        try:
            if difference > 0:
                if column + 1 < header.count():
                    neighbor = column + 1
                    header.resizeSection(neighbor, header.sectionSize(neighbor) + difference)
                else:
                    header.resizeSection(column, header.sectionSize(column) + difference)
                return
            remaining = -difference
            for index in range(column + 1, header.count()):
                width = header.sectionSize(index)
                reduction = min(remaining, max(0, width - header.minimumSectionSize()))
                if reduction: header.resizeSection(index, width - reduction)
                remaining -= reduction
                if remaining <= 0: break
            if remaining: header.resizeSection(column, header.sectionSize(column) - remaining)
        finally:
            self._adjusting = False

    def _apply_default_widths(self, available: int) -> None:
        """第一欄最小化, 標題欄以 1.5 倍權重分配剩餘空間"""
        header = self.table.horizontalHeader()
        minimum = header.minimumSectionSize()
        remaining = max(0, available - minimum)
        weights = [1.5 if column == self.title_column else 1.0 for column in range(1, header.count())]
        unit = remaining / sum(weights) if weights else 0
        self._adjusting = True
        try:
            header.resizeSection(0, minimum)
            assigned = minimum
            for column, weight in zip(range(1, header.count()), weights):
                width = round(unit * weight) if column < header.count() - 1 else max(minimum, available - assigned)
                width = max(minimum, width)
                header.resizeSection(column, width)
                assigned += width
        finally:
            self._adjusting = False

    def apply_saved_widths(self, widths: Sequence[int]) -> None:
        """套用使用者保存欄寬, 避免逐欄更新時提前重新平衡"""
        self._has_saved_widths = True
        self._initialized = True
        self._adjusting = True
        try:
            for column, width in enumerate(widths): self.table.horizontalHeader().resizeSection(column, width)
        finally:
            self._adjusting = False
        self.fit()

    def use_default_widths(self) -> None:
        """清除保存狀態並在 table 可見時重新套用預設比例"""
        self._has_saved_widths = False
        self._initialized = False
        self.fit()


def _keep_table_within_viewport(table: QTableView, title_column: int = 1) -> None:
    """讓 table 欄位可拖曳並維持在可見寬度內"""
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.horizontalHeader().setStretchLastSection(False)
    table._width_controller = _TableWidthController(table, title_column) # type: ignore[attr-defined]


def _table_column_widths(table: QTableView) -> list[int]:
    """取得 table 目前各欄寬度"""
    header = table.horizontalHeader()
    return [header.sectionSize(column) for column in range(header.count())]


def _set_table_column_widths(table: QTableView, widths: Sequence[int], minimum: int) -> None:
    """套用完整 table 欄寬, 無效資料時維持目前設定"""
    values = list(widths)
    try:
        if len(values) != table.horizontalHeader().count() or any(isinstance(width, bool) for width in values):
            table._width_controller.use_default_widths() # type: ignore[attr-defined]
            return
        values = [max(minimum, min(2000, int(width))) for width in values]
    except (TypeError, ValueError):
        table._width_controller.use_default_widths() # type: ignore[attr-defined]
        return
    table._width_controller.apply_saved_widths(values) # type: ignore[attr-defined]


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


def _enum_text(value: Any) -> str:
    """將 enum 或一般值轉成顯示文字"""
    raw_value = getattr(value, "value", value)
    return str(raw_value).replace("_", " ").title()


def _format_duration(seconds: int | float | None) -> str:
    """將秒數轉成容易閱讀的時間"""
    if seconds is None: return "-"
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _format_filesize(size: int | None) -> str:
    """將 byte 數轉成一位小數的簡短大小"""
    if size is None: return "-"
    amount = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB": return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return "-"


def _format_fps(fps: float | None) -> str:
    """將 FPS 限制為最多三位小數並移除尾端 0"""
    if fps is None or not math.isfinite(fps) or fps <= 0: return "-"
    value = f"{fps:.3f}".rstrip("0").rstrip(".")
    return f"{value} FPS"


def _encoder_label(encoder: str) -> str:
    """將 FFmpeg hardware encoder 轉成易讀名稱"""
    codec, separator, backend = encoder.lower().rpartition("_")
    if not separator: return encoder
    vendor = {"nvenc": "NVIDIA", "amf": "AMD", "qsv": "Intel"}.get(backend, backend.upper())
    codec_label = {
        "h264": "H.264",
        "hevc": "H.265 / HEVC",
        "av1": "AV1",
        "vp9": "VP9",
        "vp8": "VP8",
    }.get(codec, codec.upper())
    return f"{vendor} {codec_label}"


def _task_id(task: TaskRecord) -> str:
    """取得 task 的穩定識別值"""
    return str(task.id)


def _set_role(role: str, *widgets: QWidget) -> None:
    """設定 QSS 使用的 widget role"""
    for widget in widgets:
        widget.setProperty("role", role)
        if isinstance(widget, QPushButton): widget.setCursor(Qt.CursorShape.PointingHandCursor)


def _add_page_header(
    layout: QVBoxLayout,
    title: str,
    subtitle: str,
    action: QWidget | None = None,
) -> None:
    """加入 panel title 與用途說明"""
    title_label = QLabel(title)
    subtitle_label = QLabel(subtitle)
    subtitle_label.setWordWrap(True)
    _set_role("pageTitle", title_label)
    _set_role("pageSubtitle", subtitle_label)
    header = QGridLayout()
    header.setContentsMargins(0, 0, 0, 0)
    header.addWidget(title_label, 0, 0)
    if action: header.addWidget(action, 0, 1, alignment=Qt.AlignmentFlag.AlignRight)
    header.addWidget(subtitle_label, 1, 0, 1, 2)
    header.setColumnStretch(0, 1)
    layout.addLayout(header)
    layout.addSpacing(6)


class AnalyzePanel(QWidget):
    """分析網址並建立下載設定的 panel"""

    analyze_requested = Signal(dict)
    cancel_analysis_requested = Signal()
    add_requested = Signal(dict)
    browse_output_requested = Signal()
    browse_cookie_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("analyzePanel")
        self.setProperty("role", "panel")
        self.media: MediaInfo | None = None

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Paste one media or playlist URL")
        self.url_edit.setClearButtonEnabled(True)
        self.analyze_button = QPushButton("Analyze")
        self.analyze_button.setProperty("i18nDynamic", True)
        self.analyze_button.setText("Analyzing...")
        self.analyze_button.setMinimumWidth(self.analyze_button.sizeHint().width())
        self.analyze_button.setText("Analyze")
        self.cancel_analysis_button = QPushButton("Cancel")
        self.analysis_progress_label = QLabel()
        self.analysis_progress_label.setProperty("i18nDynamic", True)
        _set_role("muted", self.analysis_progress_label)

        # Metadata 顯示
        self.thumbnail_label = QLabel()
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setMinimumSize(240, 135)
        self.thumbnail_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.metadata_text = QLabel()
        self.metadata_text.setProperty("i18nDynamic", True)
        self.metadata_text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.metadata_text.setMinimumHeight(135)
        self.metadata_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.metadata_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.metadata_text.setWordWrap(True)
        _set_role("mediaPreview", self.thumbnail_label)
        _set_role("metadataText", self.metadata_text)

        self.playlist_table = QTableWidget(0, 4)
        self.playlist_table.setHorizontalHeaderLabels(["Sel.", "Title", "Duration", "Uploader"])
        self.playlist_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.playlist_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.playlist_table.setAlternatingRowColors(True)
        self.playlist_table.setShowGrid(False)
        self.playlist_table.verticalHeader().setVisible(False)
        self.playlist_table.setSortingEnabled(True)
        self.playlist_table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        playlist_header = self.playlist_table.horizontalHeader()
        _enable_clearable_sorting(self.playlist_table, restore_widget_order=True)
        playlist_header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        playlist_header.setMinimumSectionSize(48)
        playlist_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        _keep_table_within_viewport(self.playlist_table)
        self.select_all_button = QPushButton("Select All")
        self.select_none_button = QPushButton("Select None")

        # 下載 options
        self.preset_combo = NoWheelComboBox()
        self.preset_combo.addItem("Best Video + Audio", "best_video_audio")
        self.preset_combo.addItem("Video Only", "video_only")
        self.preset_combo.addItem("Audio Only", "audio_only")
        self.resolution_combo = NoWheelComboBox()
        for resolution in ("Best", "2160p", "1440p", "1080p", "720p", "480p"):
            self.resolution_combo.addItem(resolution, resolution.lower())
        self.container_combo = NoWheelComboBox()
        for label, value in (
            ("Auto", "auto"), ("MP4", "mp4"), ("MOV", "mov"), ("MKV", "mkv"), ("WebM", "webm"),
        ):
            self.container_combo.addItem(label, value)
        self.audio_output_combo = NoWheelComboBox()
        for label, value in (("Original", "original"), ("MP3", "mp3"), ("M4A", "m4a"), ("Opus", "opus"), ("FLAC", "flac"), ("WAV", "wav")):
            self.audio_output_combo.addItem(label, value)

        # 進階 format 選擇
        self.video_format_combo = NoWheelComboBox()
        self.audio_format_combo = NoWheelComboBox()
        self.video_format_combo.addItem("Automatic\tUse download preset", None)
        self.audio_format_combo.addItem("Automatic\tUse download preset", None)
        _set_role("formatSelector", self.video_format_combo, self.audio_format_combo)

        # 輸出位置與 Cookie
        self.output_directory_edit = QLineEdit()
        self.output_directory_edit.setPlaceholderText("Choose an output folder")
        self.output_button = QPushButton("Browse")
        self.cookie_mode_combo = NoWheelComboBox()
        self.cookie_mode_combo.addItem("None", "none")
        self.cookie_mode_combo.addItem("Browser", "browser")
        self.cookie_mode_combo.addItem("Netscape Cookie File", "file")
        self.cookie_browser_combo = NoWheelComboBox()
        self.cookie_browser_combo.setEditable(True)
        for browser in ("chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium", "safari"):
            self.cookie_browser_combo.addItem(browser.title(), browser)
        self.cookie_profile_edit = QLineEdit()
        self.cookie_profile_edit.setPlaceholderText("Optional browser profile")
        self.cookie_file_edit = QLineEdit()
        self.cookie_file_edit.setPlaceholderText("cookies.txt")
        self.cookie_file_button = QPushButton("Browse")
        self.add_button = QPushButton("Add to Queue")
        self.add_button.setEnabled(False)
        _set_role("primary", self.add_button)
        _set_role("default", self.analyze_button, self.output_button, self.cookie_file_button)
        _set_role("ghost", self.cancel_analysis_button, self.select_all_button, self.select_none_button)

        self._build_layout()
        self.analyze_button.clicked.connect(self._emit_analyze)
        self.cancel_analysis_button.clicked.connect(self.cancel_analysis_requested)
        self.url_edit.returnPressed.connect(self._emit_analyze)
        self.add_button.clicked.connect(lambda: self.add_requested.emit(self.request_payload()))
        self.output_button.clicked.connect(self.browse_output_requested)
        self.cookie_file_button.clicked.connect(self.browse_cookie_requested)
        self.cookie_mode_combo.currentIndexChanged.connect(self._update_cookie_controls)
        self.preset_combo.currentIndexChanged.connect(self._update_preset_controls)
        self.video_format_combo.currentIndexChanged.connect(self._update_advanced_selection_controls)
        self.select_all_button.clicked.connect(lambda: self.set_all_entries_checked(True))
        self.select_none_button.clicked.connect(lambda: self.set_all_entries_checked(False))
        self._update_cookie_controls()
        self._update_preset_controls()
        self.set_analyzing(False)

    def _build_layout(self) -> None:
        # URL 輸入
        content = QWidget()
        grid = QGridLayout(content)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        url_layout = QHBoxLayout()
        url_layout.addWidget(self.url_edit, 1)
        url_layout.addWidget(self.analysis_progress_label)
        url_layout.addWidget(self.analyze_button)
        url_layout.addWidget(self.cancel_analysis_button)
        grid.addLayout(url_layout, 0, 0, 1, 2)

        # Metadata 顯示
        self.metadata_group = QGroupBox("Metadata")
        self.metadata_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        metadata_layout = QGridLayout(self.metadata_group)
        metadata_layout.addWidget(self.thumbnail_label, 0, 0, alignment=Qt.AlignmentFlag.AlignVCenter)
        metadata_layout.addWidget(self.metadata_text, 0, 1, alignment=Qt.AlignmentFlag.AlignVCenter)
        metadata_layout.setColumnStretch(1, 1)
        grid.addWidget(self.metadata_group, 1, 0, 1, 2)

        # Playlist 項目
        self.playlist_group = QGroupBox("Playlist Items")
        playlist_layout = QVBoxLayout(self.playlist_group)
        playlist_layout.addWidget(self.playlist_table)
        playlist_buttons = QHBoxLayout()
        playlist_buttons.addWidget(self.select_all_button)
        playlist_buttons.addWidget(self.select_none_button)
        playlist_buttons.addStretch()
        playlist_layout.addLayout(playlist_buttons)
        self.playlist_group.setVisible(False)
        grid.addWidget(self.playlist_group, 2, 0, 1, 2)

        # 常用下載 options
        options_group = QGroupBox("Download Options")
        options_form = QFormLayout(options_group)
        options_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        options_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        options_form.addRow("Preset", self.preset_combo)
        options_form.addRow("Resolution", self.resolution_combo)
        options_form.addRow("Video Container", self.container_combo)
        options_form.addRow("Audio Output", self.audio_output_combo)
        grid.addWidget(options_group, 3, 0)

        destination_group = QGroupBox("Output and Cookies")
        destination_form = QFormLayout(destination_group)
        destination_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        destination_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        output_layout = QHBoxLayout()
        output_layout.addWidget(self.output_directory_edit, 1)
        output_layout.addWidget(self.output_button)
        destination_form.addRow("Output Folder", output_layout)
        destination_form.addRow("Cookie Source", self.cookie_mode_combo)
        destination_form.addRow("Browser", self.cookie_browser_combo)
        destination_form.addRow("Browser Profile", self.cookie_profile_edit)
        cookie_file_layout = QHBoxLayout()
        cookie_file_layout.addWidget(self.cookie_file_edit, 1)
        cookie_file_layout.addWidget(self.cookie_file_button)
        destination_form.addRow("Cookie File", cookie_file_layout)
        grid.addWidget(destination_group, 3, 1)

        # 進階 formats
        self.advanced_group = QGroupBox("Advanced Formats")
        self.advanced_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        advanced_layout = QVBoxLayout(self.advanced_group)
        format_selection = QFormLayout()
        format_selection.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        format_selection.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        format_selection.addRow("Video Format", self.video_format_combo)
        format_selection.addRow("Audio Format", self.audio_format_combo)
        advanced_layout.addLayout(format_selection)
        grid.addWidget(self.advanced_group, 4, 0, 1, 2)
        grid.setRowStretch(1, 1)
        grid.setRowStretch(2, 2)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_area.setWidget(content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        _add_page_header(
            layout,
            "Download Media",
            "Review the media, then choose what you want to download and in which format",
            self.add_button,
        )
        layout.addWidget(scroll_area)

    def _emit_analyze(self) -> None:
        url = self.url_edit.text().strip()
        if not url: return
        self.analyze_requested.emit({"url": url, "cookie": self.cookie_payload()})

    def _update_cookie_controls(self) -> None:
        mode = self.cookie_mode_combo.currentData()
        browser_enabled = mode == "browser"
        file_enabled = mode == "file"
        self.cookie_browser_combo.setEnabled(browser_enabled)
        self.cookie_profile_edit.setEnabled(browser_enabled)
        self.cookie_file_edit.setEnabled(file_enabled)
        self.cookie_file_button.setEnabled(file_enabled)

    def _update_preset_controls(self) -> None:
        preset = self.preset_combo.currentData()
        self.resolution_combo.setEnabled(preset != "audio_only")
        self.container_combo.setEnabled(preset != "audio_only")
        self.audio_output_combo.setEnabled(preset == "audio_only")
        self._update_advanced_selection_controls()

    def _update_advanced_selection_controls(self) -> None:
        preset = self.preset_combo.currentData()
        is_playlist = bool(self.media and self.media.entries)
        combined_video = bool(self.video_format_combo.currentData(Qt.ItemDataRole.UserRole + 1))
        self.video_format_combo.setEnabled(not is_playlist and preset != "audio_only")
        self.audio_format_combo.setEnabled(not is_playlist and preset != "video_only" and not combined_video)

    def set_analyzing(self, analyzing: bool) -> None:
        """切換分析中的操作狀態"""
        self._analyzing = analyzing
        self.url_edit.setEnabled(not analyzing)
        self.analyze_button.setEnabled(not analyzing)
        self.analyze_button.setText(tr("Analyzing...") if analyzing else tr("Analyze"))
        self.cancel_analysis_button.setText(tr("Cancel"))
        self.cancel_analysis_button.setVisible(analyzing)
        if not analyzing: self.set_analysis_progress(0, 0)

    def set_analysis_progress(self, current: int, total: int) -> None:
        """顯示 playlist 分析進度"""
        self.analysis_progress_label.setText(f"{current}/{total}" if current > 0 and total > 0 else "")
        self.analysis_progress_label.setVisible(bool(self.analysis_progress_label.text()))

    def set_media(self, media: MediaInfo | None) -> None:
        """顯示分析後的 metadata 和 formats"""
        self.media = media
        if media is None:
            self.metadata_text.clear()
            self.metadata_text.setToolTip("")
            self.set_thumbnail(None)
            self._set_playlist_entries(())
            self._set_formats(())
            self.add_button.setEnabled(False)
            return

        self.set_thumbnail(None)
        self.metadata_text.setText("\n".join((
            f"{tr('Title')}: {media.title or '-'}",
            f"{tr('Uploader')}: {media.uploader or '-'}",
            f"{tr('Duration')}: {_format_duration(media.duration)}",
            f"{tr('Site')}: {media.site or '-'}",
            f"URL: {media.webpage_url or '-'}",
        )))
        self.metadata_text.setToolTip(media.webpage_url or "")
        self._set_playlist_entries(media.entries)
        self._set_formats(media.formats)
        self.add_button.setEnabled(True)

    def set_thumbnail(self, pixmap: QPixmap | bytes | None) -> None:
        """顯示外部載入完成的 thumbnail"""
        if isinstance(pixmap, bytes):
            loaded = QPixmap()
            loaded.loadFromData(pixmap)
            pixmap = loaded
        if pixmap is None or pixmap.isNull():
            self.thumbnail_label.clear()
            return
        target = self.thumbnail_label.size()
        self.thumbnail_label.setPixmap(pixmap.scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _set_playlist_entries(self, entries: Sequence[MediaInfo]) -> None:
        self.playlist_table.setSortingEnabled(False)
        self.playlist_table.setRowCount(0)
        for row, entry in enumerate(entries):
            self.playlist_table.insertRow(row)
            selected_item = _CheckableTableWidgetItem()
            selected_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            selected_item.setCheckState(Qt.CheckState.Checked)
            selected_item.setData(Qt.ItemDataRole.UserRole, entry.media_id)
            selected_item.setData(_ORIGINAL_ROW_ROLE, row)
            self.playlist_table.setItem(row, 0, selected_item)
            self.playlist_table.setItem(row, 1, QTableWidgetItem(entry.title))
            self.playlist_table.setItem(row, 2, _SortableTableWidgetItem(
                _format_duration(entry.duration), entry.duration if entry.duration is not None else -1,
            ))
            self.playlist_table.setItem(row, 3, QTableWidgetItem(entry.uploader or "-"))
        self.playlist_table.setSortingEnabled(True)
        is_playlist = bool(entries)
        self.playlist_group.setVisible(is_playlist)
        self.advanced_group.setEnabled(not is_playlist)
        self._update_advanced_selection_controls()

    def _set_formats(self, formats: Sequence[FormatInfo]) -> None:
        self.video_format_combo.clear()
        self.audio_format_combo.clear()
        self.video_format_combo.addItem(tr("Automatic\tUse download preset"), None)
        self.audio_format_combo.addItem(tr("Automatic\tUse download preset"), None)
        ordered_formats = sorted(
            formats,
            key=lambda media_format: (media_format.filesize is not None, media_format.filesize or 0),
            reverse=True,
        )
        for media_format in ordered_formats:
            option_text = self._format_option_text(media_format)
            if media_format.video_codec and media_format.video_codec != "none":
                self.video_format_combo.addItem(option_text, media_format.format_id)
                self.video_format_combo.setItemData(
                    self.video_format_combo.count() - 1,
                    bool(media_format.audio_codec and media_format.audio_codec != "none"),
                    Qt.ItemDataRole.UserRole + 1,
                )
            if media_format.video_codec in {"", "none"} and media_format.audio_codec and media_format.audio_codec != "none":
                self.audio_format_combo.addItem(option_text, media_format.format_id)
        self._update_advanced_selection_controls()

    @staticmethod
    def _format_option_text(media_format: FormatInfo) -> str:
        """建立包含完整 format 資訊的選單文字"""
        return "\t".join((
            media_format.format_id,
            (media_format.extension or "-").upper(),
            media_format.resolution or "-",
            _format_fps(media_format.fps),
            f"V: {media_format.video_codec or '-'}",
            f"A: {media_format.audio_codec or '-'}",
            _format_filesize(media_format.filesize),
        ))

    def set_all_entries_checked(self, checked: bool) -> None:
        """勾選或取消所有 playlist 項目"""
        _set_all_table_items_checked(self.playlist_table, checked)

    def column_widths(self) -> list[int]:
        """取得播放清單 table 欄寬"""
        return _table_column_widths(self.playlist_table)

    def set_column_widths(self, widths: Sequence[int]) -> None:
        """還原播放清單 table 欄寬"""
        _set_table_column_widths(self.playlist_table, widths, 48)

    def selected_entry_ids(self) -> list[str]:
        """回傳使用者勾選的 playlist media ID"""
        return [
            str(self.playlist_table.item(row, 0).data(Qt.ItemDataRole.UserRole))
            for row in range(self.playlist_table.rowCount())
            if self.playlist_table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]

    def cookie_payload(self) -> dict[str, str]:
        """回傳不包含 Cookie 內容的來源設定"""
        return {
            "source": str(self.cookie_mode_combo.currentData()),
            "browser": str(self.cookie_browser_combo.currentData() or self.cookie_browser_combo.currentText()).strip().lower(),
            "profile": self.cookie_profile_edit.text().strip(),
            "file_path": self.cookie_file_edit.text().strip(),
        }

    def request_payload(self) -> dict[str, Any]:
        """建立 controller 可轉成 DownloadOptions 的 payload"""
        preset = self.preset_combo.currentData()
        video_format_id = self.video_format_combo.currentData() or ""
        combined_video = bool(self.video_format_combo.currentData(Qt.ItemDataRole.UserRole + 1))
        audio_format_id = self.audio_format_combo.currentData() or ""
        if preset == "audio_only": video_format_id = ""
        if preset == "video_only" or combined_video: audio_format_id = ""
        return {
            "url": self.url_edit.text().strip(),
            "media_id": self.media.media_id if self.media else "",
            "title": self.media.title if self.media else "",
            "playlist_item_ids": self.selected_entry_ids(),
            "preset": preset,
            "resolution": self.resolution_combo.currentData(),
            "video_container": self.container_combo.currentData(),
            "audio_output": self.audio_output_combo.currentData(),
            "video_format_id": video_format_id,
            "audio_format_id": audio_format_id,
            "output_dir": self.output_directory_edit.text().strip(),
            "cookie": self.cookie_payload(),
        }


class SubtitlePanel(QWidget):
    """分析並選擇人工字幕或 auto captions"""

    analyze_requested = Signal(dict)
    cancel_analysis_requested = Signal()
    add_requested = Signal(dict)
    browse_output_requested = Signal()
    browse_cookie_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("subtitlePanel")
        self.setProperty("role", "panel")
        self.media: MediaInfo | None = None
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Paste one media or playlist URL")
        self.url_edit.setClearButtonEnabled(True)
        self.analyze_button = QPushButton("Analyze")
        self.analyze_button.setProperty("i18nDynamic", True)
        self.analyze_button.setText("Analyzing...")
        self.analyze_button.setMinimumWidth(self.analyze_button.sizeHint().width())
        self.analyze_button.setText("Analyze")
        self.cancel_analysis_button = QPushButton("Cancel")
        self.analysis_progress_label = QLabel()
        self.analysis_progress_label.setProperty("i18nDynamic", True)
        _set_role("muted", self.analysis_progress_label)
        self.include_automatic_checkbox = QCheckBox("Include auto-generated subtitles")
        self.include_automatic_checkbox.setChecked(True)
        self.include_automatic_checkbox.setToolTip(
            "Include automatically generated captions when analyzing the URL"
        )

        self.subtitle_table = QTableWidget(0, 6)
        self.subtitle_table.setHorizontalHeaderLabels(["Sel.", "Video", "Language", "Name", "Source", "Format"])
        self.subtitle_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.subtitle_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.subtitle_table.setAlternatingRowColors(True)
        self.subtitle_table.setShowGrid(False)
        self.subtitle_table.verticalHeader().setVisible(False)
        self.subtitle_table.verticalHeader().setMinimumSectionSize(34)
        self.subtitle_table.verticalHeader().setDefaultSectionSize(34)
        self.subtitle_table.setSortingEnabled(True)
        self.subtitle_table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        header = self.subtitle_table.horizontalHeader()
        _enable_clearable_sorting(self.subtitle_table, restore_widget_order=True)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setMinimumSectionSize(48)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        _keep_table_within_viewport(self.subtitle_table)
        self.empty_label = QLabel("Analyze a URL to list available subtitles")
        self.empty_label.setProperty("i18nDynamic", True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _set_role("muted", self.empty_label)
        self.select_all_button = QPushButton("Select All")
        self.select_none_button = QPushButton("Select None")

        self.output_directory_edit = QLineEdit()
        self.output_directory_edit.setPlaceholderText("Choose an output folder")
        self.output_button = QPushButton("Browse")
        self.cookie_mode_combo = NoWheelComboBox()
        self.cookie_mode_combo.addItem("None", "none")
        self.cookie_mode_combo.addItem("Browser", "browser")
        self.cookie_mode_combo.addItem("Netscape Cookie File", "file")
        self.cookie_browser_combo = NoWheelComboBox()
        self.cookie_browser_combo.setEditable(True)
        for browser in ("chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium", "safari"):
            self.cookie_browser_combo.addItem(browser.title(), browser)
        self.cookie_profile_edit = QLineEdit()
        self.cookie_profile_edit.setPlaceholderText("Optional browser profile")
        self.cookie_file_edit = QLineEdit()
        self.cookie_file_edit.setPlaceholderText("cookies.txt")
        self.cookie_file_button = QPushButton("Browse")
        self.add_button = QPushButton("Add to Queue")
        self.add_button.setEnabled(False)
        _set_role("primary", self.add_button)
        _set_role("default", self.analyze_button, self.output_button, self.cookie_file_button)
        _set_role("ghost", self.cancel_analysis_button, self.select_all_button, self.select_none_button)
        self._build_layout()

        self.analyze_button.clicked.connect(self._emit_analyze)
        self.cancel_analysis_button.clicked.connect(self.cancel_analysis_requested)
        self.url_edit.returnPressed.connect(self._emit_analyze)
        self.output_button.clicked.connect(self.browse_output_requested)
        self.cookie_file_button.clicked.connect(self.browse_cookie_requested)
        self.cookie_mode_combo.currentIndexChanged.connect(self._update_cookie_controls)
        self.select_all_button.clicked.connect(lambda: self.set_all_checked(True))
        self.select_none_button.clicked.connect(lambda: self.set_all_checked(False))
        self.subtitle_table.itemChanged.connect(lambda *_: self._refresh_add_button())
        self.output_directory_edit.textChanged.connect(self._refresh_add_button)
        self.include_automatic_checkbox.toggled.connect(self._automatic_subtitles_toggled)
        self.add_button.clicked.connect(lambda: self.add_requested.emit(self.request_payload()))
        self._update_cookie_controls()
        self.set_analyzing(False)

    def _build_layout(self) -> None:
        url_layout = QHBoxLayout()
        url_layout.addWidget(self.url_edit, 1)
        url_layout.addWidget(self.analysis_progress_label)
        url_layout.addWidget(self.analyze_button)
        url_layout.addWidget(self.cancel_analysis_button)
        selection_buttons = QHBoxLayout()
        selection_buttons.addWidget(self.select_all_button)
        selection_buttons.addWidget(self.select_none_button)
        selection_buttons.addStretch()
        destination_group = QGroupBox("Output and Cookies")
        destination_form = QFormLayout(destination_group)
        destination_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        output_layout = QHBoxLayout()
        output_layout.addWidget(self.output_directory_edit, 1)
        output_layout.addWidget(self.output_button)
        destination_form.addRow("Output Folder", output_layout)
        destination_form.addRow("Cookie Source", self.cookie_mode_combo)
        destination_form.addRow("Browser", self.cookie_browser_combo)
        destination_form.addRow("Browser Profile", self.cookie_profile_edit)
        cookie_file_layout = QHBoxLayout()
        cookie_file_layout.addWidget(self.cookie_file_edit, 1)
        cookie_file_layout.addWidget(self.cookie_file_button)
        destination_form.addRow("Cookie File", cookie_file_layout)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        _add_page_header(
            layout,
            "Download Subtitles",
            "Find and download subtitles available for a video",
            self.add_button,
        )
        layout.addLayout(url_layout)
        layout.addWidget(self.include_automatic_checkbox)
        layout.addSpacing(4)
        layout.addWidget(self.empty_label, 1)
        layout.addWidget(self.subtitle_table, 1)
        layout.addLayout(selection_buttons)
        layout.addWidget(destination_group)
        self.subtitle_table.hide()

    def _emit_analyze(self) -> None:
        url = self.url_edit.text().strip()
        if url: self.analyze_requested.emit({
            "url": url,
            "cookie": self.cookie_payload(),
            "include_automatic_subtitles": self.include_automatic_checkbox.isChecked(),
        })

    def _automatic_subtitles_toggled(self) -> None:
        """非分析期間切換選項時清除不再相符的結果"""
        if self._analyzing: return
        self.set_media(None)

    def _update_cookie_controls(self) -> None:
        mode = self.cookie_mode_combo.currentData()
        browser_enabled, file_enabled = mode == "browser", mode == "file"
        self.cookie_browser_combo.setEnabled(browser_enabled)
        self.cookie_profile_edit.setEnabled(browser_enabled)
        self.cookie_file_edit.setEnabled(file_enabled)
        self.cookie_file_button.setEnabled(file_enabled)

    def set_analyzing(self, analyzing: bool) -> None:
        """切換字幕分析中的操作狀態"""
        self._analyzing = analyzing
        self.url_edit.setEnabled(not analyzing)
        self.include_automatic_checkbox.setEnabled(True)
        self.analyze_button.setEnabled(not analyzing)
        self.analyze_button.setText(tr("Analyzing...") if analyzing else tr("Analyze"))
        self.cancel_analysis_button.setText(tr("Cancel"))
        self.cancel_analysis_button.setVisible(analyzing)
        if not analyzing: self.set_analysis_progress(0, 0)

    def set_analysis_progress(self, current: int, total: int) -> None:
        """顯示 playlist 字幕分析進度"""
        self.analysis_progress_label.setText(f"{current}/{total}" if current > 0 and total > 0 else "")
        self.analysis_progress_label.setVisible(bool(self.analysis_progress_label.text()))

    def set_media(self, media: MediaInfo | None) -> None:
        """顯示單片或 playlist 的字幕 tracks"""
        self.media = media
        self.subtitle_table.setSortingEnabled(False)
        self.subtitle_table.setRowCount(0)
        if media:
            items = media.entries if media.entries else [media]
            for item in items:
                for track in item.subtitles: self._append_track(item, track)
        self.subtitle_table.setSortingEnabled(True)
        has_tracks = self.subtitle_table.rowCount() > 0
        self.subtitle_table.setVisible(has_tracks)
        self.empty_label.setVisible(not has_tracks)
        empty_source = "Analyze a URL to list available subtitles"
        if media and not has_tracks:
            empty_source = (
                "No manual subtitles or auto captions were found"
                if self.include_automatic_checkbox.isChecked() else "No manual subtitles were found"
            )
        self.empty_label.setText(tr(empty_source))
        self._refresh_add_button()

    def _append_track(self, media: MediaInfo, track: SubtitleTrack) -> None:
        row = self.subtitle_table.rowCount()
        self.subtitle_table.insertRow(row)
        selected = _CheckableTableWidgetItem()
        selected.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        selected.setCheckState(Qt.CheckState.Unchecked)
        selected.setData(Qt.ItemDataRole.UserRole, {
            "media_id": media.media_id,
            "title": media.title,
            "url": media.webpage_url,
            "language": track.language,
            "source": track.source,
        })
        selected.setData(_ORIGINAL_ROW_ROLE, row)
        self.subtitle_table.setItem(row, 0, selected)
        self.subtitle_table.setItem(row, 1, QTableWidgetItem(media.title))
        self.subtitle_table.setItem(row, 2, QTableWidgetItem(track.language))
        self.subtitle_table.setItem(row, 3, QTableWidgetItem(track.name or "-"))
        self.subtitle_table.setItem(row, 4, QTableWidgetItem(tr("Auto") if track.source == "automatic" else tr("Manual")))
        format_item = QTableWidgetItem(tr("Best"))
        self.subtitle_table.setItem(row, 5, format_item)
        format_combo = NoWheelComboBox()
        format_combo.setProperty("role", "tableCell")
        format_combo.addItem(tr("Best"), "best")
        for extension in track.formats: format_combo.addItem(extension.upper(), extension)
        format_combo.currentTextChanged.connect(format_item.setText)
        self.subtitle_table.setCellWidget(row, 5, format_combo)

    def set_all_checked(self, checked: bool) -> None:
        """勾選或取消所有字幕 track"""
        _set_all_table_items_checked(self.subtitle_table, checked)

    def column_widths(self) -> list[int]:
        """取得字幕 table 欄寬"""
        return _table_column_widths(self.subtitle_table)

    def set_column_widths(self, widths: Sequence[int]) -> None:
        """還原字幕 table 欄寬"""
        _set_table_column_widths(self.subtitle_table, widths, 48)

    def cookie_payload(self) -> dict[str, str]:
        """回傳字幕分析與下載使用的 Cookie 設定"""
        return {
            "source": str(self.cookie_mode_combo.currentData()),
            "browser": str(self.cookie_browser_combo.currentData() or self.cookie_browser_combo.currentText()).strip().lower(),
            "profile": self.cookie_profile_edit.text().strip(),
            "file_path": self.cookie_file_edit.text().strip(),
        }

    def selected_tracks(self) -> list[dict[str, str]]:
        """回傳已勾選字幕與各列格式"""
        tracks = []
        for row in range(self.subtitle_table.rowCount()):
            item = self.subtitle_table.item(row, 0)
            if item.checkState() != Qt.CheckState.Checked: continue
            track = dict(item.data(Qt.ItemDataRole.UserRole))
            track["format"] = str(self.subtitle_table.cellWidget(row, 5).currentData())
            tracks.append(track)
        return tracks

    def request_payload(self) -> dict[str, Any]:
        """建立可依影片分組成 SubtitleOptions 的 payload"""
        return {
            "tracks": self.selected_tracks(),
            "output_dir": self.output_directory_edit.text().strip(),
            "cookie": self.cookie_payload(),
        }

    def _refresh_add_button(self) -> None:
        self.add_button.setEnabled(bool(self.selected_tracks() and self.output_directory_edit.text().strip()))


class TaskTableModel(QAbstractTableModel):
    """顯示共用 media、subtitle 和 conversion task"""

    COLUMNS = (
        ("kind", "Type"),
        ("title", "Title"),
        ("url", "URL"),
        ("status", "Status"),
        ("progress", "Progress"),
        ("output_path", "Output"),
        ("error", "Error"),
    )
    SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, tasks: Sequence[TaskRecord] = (), parent: QWidget | None = None):
        super().__init__(parent)
        self.tasks = list(tasks)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.tasks)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self.tasks): return None
        task = self.tasks[index.row()]
        field = self.COLUMNS[index.column()][0]
        if field == "url":
            if task.kind is TaskKind.DOWNLOAD and task.download_options:
                value = task.download_options.url
            elif task.kind is TaskKind.SUBTITLE and task.subtitle_options:
                value = task.subtitle_options.url
            else:
                value = ""
        else:
            value = getattr(task, field)
        if role == Qt.ItemDataRole.UserRole: return task
        if role == self.SORT_ROLE:
            if field in {"kind", "status"}: return value.value
            if field == "progress": return float(value) if value is not None else -1.0
            return str(value or "").casefold()
        if role == Qt.ItemDataRole.ToolTipRole and field in {"title", "url", "output_path", "error"}:
            return tr(str(value or "")) if field == "error" else str(value or "")
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and field == "status":
            if task.status is TaskStatus.COMPLETED: return QColor(theme_color("success"))
            if task.status is TaskStatus.FAILED: return QColor(theme_color("error"))
        if role != Qt.ItemDataRole.DisplayRole: return None
        if field in {"kind", "status"}: return tr(_enum_text(value))
        if field == "progress":
            if value is None or float(value) < 0: return "-"
            return f"{float(value) * 100:.1f}%"
        if field == "error": return tr(str(value or ""))
        return str(value or "")

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.TextAlignmentRole and orientation == Qt.Orientation.Horizontal:
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role != Qt.ItemDataRole.DisplayRole: return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.COLUMNS):
            return tr(self.COLUMNS[section][1])
        if orientation == Qt.Orientation.Vertical: return str(section + 1)
        return None

    def set_tasks(self, tasks: Sequence[TaskRecord]) -> None:
        """以 controller 的 task snapshot 更新 model"""
        self.beginResetModel()
        self.tasks = list(tasks)
        self.endResetModel()

    def update_task(self, task: TaskRecord) -> None:
        """更新既有 task, 找不到時加入尾端"""
        task_identity = _task_id(task)
        for row, current in enumerate(self.tasks):
            if _task_id(current) != task_identity: continue
            self.tasks[row] = task
            self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1))
            return
        row = len(self.tasks)
        self.beginInsertRows(QModelIndex(), row, row)
        self.tasks.append(task)
        self.endInsertRows()

    def task_at(self, row: int) -> TaskRecord | None:
        """依 row 取得 task"""
        return self.tasks[row] if 0 <= row < len(self.tasks) else None

    def retranslate(self) -> None:
        """通知 view 重新讀取翻譯後的 headers 與資料"""
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, self.columnCount() - 1)
        if self.rowCount(): self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, self.columnCount() - 1))


class QueuePanel(QWidget):
    """顯示 task queue 並發送操作要求"""

    start_requested = Signal()
    pause_requested = Signal()
    cancel_requested = Signal(list)
    retry_requested = Signal(list)
    remove_requested = Signal(list)
    move_requested = Signal(list, int)
    concurrency_changed = Signal(int)
    open_output_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("queuePanel")
        self.setProperty("role", "panel")
        self.model = TaskTableModel(parent=self)
        self.proxy_model = QSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.model)
        self.proxy_model.setSortRole(TaskTableModel.SORT_ROLE)
        self.proxy_model.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy_model.setDynamicSortFilter(True)
        self.table = QTableView()
        self.table.setObjectName("queueTable")
        self.table.setModel(self.proxy_model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setMinimumSectionSize(60)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        _keep_table_within_viewport(self.table)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        _enable_clearable_sorting(self.table)

        self.start_button = QPushButton("Pause Queue")
        self.start_button.setProperty("i18nDynamic", True)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Stop selected running or pending tasks")
        self.retry_button = QPushButton("Retry / Resume")
        self.remove_button = QPushButton("Remove")
        self.up_button = QPushButton("Move Up")
        self.down_button = QPushButton("Move Down")
        self.summary_label = QLabel()
        self.summary_label.setProperty("i18nDynamic", True)
        _set_role("muted", self.summary_label)
        self.concurrency_combo = NoWheelComboBox()
        for worker_count in range(1, 5):
            self.concurrency_combo.addItem(str(worker_count), worker_count)
        _set_role("default", self.start_button)
        _set_role(
            "default",
            self.cancel_button,
            self.retry_button,
            self.remove_button,
        )
        _set_role("ghost", self.up_button, self.down_button)

        controls = QHBoxLayout()
        controls.addWidget(self.start_button)
        controls.addWidget(self.cancel_button)
        controls.addWidget(self.retry_button)
        controls.addWidget(self.remove_button)
        controls.addWidget(self.up_button)
        controls.addWidget(self.down_button)
        controls.addStretch()
        controls.addWidget(QLabel("Workers"))
        controls.addWidget(self.concurrency_combo)
        summary = QHBoxLayout()
        summary.addWidget(self.summary_label)
        summary.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        _add_page_header(layout, "Task Queue", "View and manage tasks that are waiting, running, or completed")
        layout.addLayout(controls)
        layout.addSpacing(4)
        layout.addWidget(self.table, 1)
        layout.addLayout(summary)

        self._dispatch_paused = False
        self.start_button.clicked.connect(self._toggle_dispatch)
        self.cancel_button.clicked.connect(lambda: self.cancel_requested.emit(self.selected_task_ids()))
        self.retry_button.clicked.connect(lambda: self.retry_requested.emit(self.selected_task_ids()))
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self.selected_task_ids()))
        self.up_button.clicked.connect(lambda: self._move_selected(-1))
        self.down_button.clicked.connect(lambda: self._move_selected(1))
        self.concurrency_combo.currentIndexChanged.connect(
            lambda: self.concurrency_changed.emit(int(self.concurrency_combo.currentData()))
        )
        self.table.doubleClicked.connect(self._open_task_output)
        self.set_dispatch_paused(False)
        self.refresh_summary()

    def set_tasks(self, tasks: Sequence[TaskRecord]) -> None:
        """更新完整 queue snapshot"""
        self.model.set_tasks(tasks)
        self.refresh_summary()

    def update_task(self, task: TaskRecord) -> None:
        """更新單筆 task"""
        self.model.update_task(task)
        self.refresh_summary()

    def refresh_summary(self) -> None:
        """更新 queue 頁面的等待、完成與錯誤統計"""
        pending = sum(task.status is TaskStatus.PENDING for task in self.model.tasks)
        completed = sum(task.status is TaskStatus.COMPLETED for task in self.model.tasks)
        failed = sum(task.status is TaskStatus.FAILED for task in self.model.tasks)
        self.summary_label.setText(tr(
            "Pending: {pending}  Completed: {completed}  Failed: {failed}",
            pending=pending, completed=completed, failed=failed,
        ))

    def selected_task_ids(self) -> list[str]:
        """取得目前選取的 task ID"""
        rows = sorted({self.proxy_model.mapToSource(index).row() for index in self.table.selectionModel().selectedRows()})
        return [_task_id(task) for row in rows if (task := self.model.task_at(row)) is not None]

    def _move_selected(self, direction: int) -> None:
        """回到 queue 原始順序後移動選取 tasks"""
        task_ids = self.selected_task_ids()
        self.proxy_model.sort(-1)
        self.move_requested.emit(task_ids, direction)

    def column_widths(self) -> list[int]:
        """取得目前 Queue 欄寬"""
        return _table_column_widths(self.table)

    def set_column_widths(self, widths: Sequence[int]) -> None:
        """套用完整 Queue 欄寬, 無效資料使用預設比例"""
        _set_table_column_widths(self.table, widths, 60)

    def set_dispatch_paused(self, paused: bool) -> None:
        """同步 dispatch 暫停狀態"""
        self._dispatch_paused = paused
        self.start_button.setText(tr("Start Queue") if paused else tr("Pause Queue"))
        self.start_button.setProperty("role", "primary" if paused else "default")
        self.start_button.style().unpolish(self.start_button)
        self.start_button.style().polish(self.start_button)
        self.start_button.setToolTip(
            tr("Start pending queue tasks")
            if paused
            else tr("Pause new task dispatch. Running tasks will finish normally; use Cancel on selected tasks to stop them immediately")
        )

    def _open_task_output(self, index: QModelIndex) -> None:
        """雙擊 task 時送出可開啟的輸出資料夾"""
        task = self.model.task_at(self.proxy_model.mapToSource(index).row())
        if task is None: return
        options = task.download_options or task.subtitle_options or task.conversion_options
        configured = str(getattr(options, "output_dir", "") or "")
        if task.status is not TaskStatus.COMPLETED and configured:
            self.open_output_requested.emit(configured)
            return
        output = Path(task.output_path).expanduser() if task.output_path else None
        if output is not None:
            self.open_output_requested.emit(str(output if output.is_dir() or not output.suffix else output.parent))
        elif configured:
            self.open_output_requested.emit(configured)

    def _toggle_dispatch(self) -> None:
        """依目前 dispatch 狀態送出 Start 或 Pause"""
        if self._dispatch_paused:
            self.start_requested.emit()
        else:
            self.pause_requested.emit()


def _probe_number(value: Any) -> float | None:
    """解析 FFprobe 數值, N/A 或無效內容回傳 None"""
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _probe_bitrate(value: Any) -> str:
    """將 FFprobe bit/s 轉成容易閱讀的單位"""
    bitrate = _probe_number(value)
    if bitrate is None: return "-"
    return f"{bitrate / 1_000_000:.3f} Mbps" if bitrate >= 1_000_000 else f"{bitrate / 1000:.0f} kbps"


def _probe_fps(value: Any) -> str:
    """解析 FFprobe 分數形式的 FPS"""
    text = str(value or "")
    try:
        numerator, denominator = text.split("/", 1)
        return _format_fps(float(numerator) / float(denominator))
    except (ValueError, ZeroDivisionError):
        return _format_fps(_probe_number(value))


def _probe_gop(probe: dict[str, Any]) -> str:
    """格式化 keyframe 間隔分析結果"""
    analysis = probe.get("gop_analysis") or {}
    value = analysis.get("value")
    if value is None: return "-"
    minimum, maximum = analysis.get("minimum"), analysis.get("maximum")
    if minimum == maximum:
        return tr("{value} frames", value=value)
    return tr(
        "{value} frames (average {average}, range {minimum}-{maximum})",
        value=value, average=f"{float(analysis.get('average') or 0):.3f}", minimum=minimum, maximum=maximum,
    )


def _probe_report_html(path: str, probe: dict[str, Any]) -> str:
    """將 FFprobe JSON 整理成可快速掃描的 HTML 報告"""
    def safe(value: Any) -> str: return html.escape(str(value if value not in {None, "", "N/A"} else "-"))
    def row(label: str, value: Any) -> str:
        return f"<tr><td style='padding-right: 16px'><b>{safe(tr(label))}</b></td><td>{safe(value)}</td></tr>"

    format_info = probe.get("format") or {}
    size = _probe_number(format_info.get("size"))
    duration = _probe_number(format_info.get("duration")) or _probe_number(probe.get("duration"))
    format_name = format_info.get("format_long_name") or format_info.get("format_name")
    blocks = [
        f"<p><b>{safe(tr('File'))}</b><br>{safe(path)}</p>",
        "<table cellspacing='4'>",
        row("Container", format_name), row("Duration", _format_duration(duration)),
        row("File Size", _format_filesize(int(size)) if size is not None else "-"),
        row("Overall Bitrate", _probe_bitrate(format_info.get("bit_rate"))), "</table>",
    ]
    type_counts: dict[str, int] = {}
    for stream in probe.get("streams") or []:
        stream_type = str(stream.get("codec_type") or "other")
        type_counts[stream_type] = type_counts.get(stream_type, 0) + 1
        title = {"video": "Video Stream", "audio": "Audio Stream", "subtitle": "Subtitle Stream"}.get(
            stream_type, "Other Stream"
        )
        tags = stream.get("tags") or {}
        blocks += [f"<hr><p><b>{safe(tr(title))} #{type_counts[stream_type]}</b></p>", "<table cellspacing='4'>"]
        blocks += [row("Codec", stream.get("codec_long_name") or stream.get("codec_name"))]
        if stream_type == "video":
            resolution = f"{stream.get('width')} x {stream.get('height')}" if stream.get("width") and stream.get("height") else "-"
            blocks += [
                row("Profile", stream.get("profile")), row("Resolution", resolution),
                row("Frame Rate", _probe_fps(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))),
                row("GOP", _probe_gop(probe)),
                row("Pixel Format", stream.get("pix_fmt")), row("Bitrate", _probe_bitrate(stream.get("bit_rate"))),
                row("Color Space", stream.get("color_space")), row("Color Transfer", stream.get("color_transfer")),
            ]
        elif stream_type == "audio":
            blocks += [
                row("Profile", stream.get("profile")), row("Sample Rate", f"{stream.get('sample_rate')} Hz" if stream.get("sample_rate") else "-"),
                row("Channels", stream.get("channels")), row("Channel Layout", stream.get("channel_layout")),
                row("Bitrate", _probe_bitrate(stream.get("bit_rate"))),
            ]
        blocks += [row("Language", tags.get("language")), row("Stream Title", tags.get("title")), "</table>"]
    chapters = probe.get("chapters") or []
    if chapters: blocks += [f"<hr><p><b>{safe(tr('Chapters'))}</b>: {len(chapters)}</p>"]
    return "".join(blocks)


class _FileAnalysisCard(QFrame):
    """顯示單一檔案的 FFprobe 分析結果"""

    remove_requested = Signal(str)
    gop_requested = Signal(str)

    def __init__(self, path: str, number: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.path = path
        self.probe: dict[str, Any] | None = None
        self.error = ""
        self.setProperty("role", "analysisCard")
        self.title_label = QLabel(f"#{number}")
        self.title_label.setProperty("role", "sectionTitle")
        self.remove_button = QToolButton()
        self.remove_button.setProperty("role", "windowClose")
        self.remove_button.setProperty("controlType", "close")
        self.remove_button.setFixedSize(QSize(22, 20))
        self.gop_button = QPushButton("Analyze GOP")
        self._gop_busy = False
        _set_role("ghost", self.gop_button)
        self.report_label = QLabel()
        self.report_label.setProperty("i18nDynamic", True)
        self.report_label.setTextFormat(Qt.TextFormat.RichText)
        self.report_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.report_label.setWordWrap(True)
        header = QHBoxLayout()
        header.addWidget(self.title_label, 1)
        header.addWidget(self.gop_button)
        header.addWidget(self.remove_button)
        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.report_label)
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self.path))
        self.gop_button.clicked.connect(self._request_gop)
        self.retranslate()

    def set_result(self, probe: dict[str, Any] | None, error: str) -> None:
        self.probe, self.error = probe, error
        self._gop_busy = False
        path_html = f"<p><b>{html.escape(tr('File'))}</b><br>{html.escape(self.path)}</p>"
        if error:
            self.report_label.setText(
                f"{path_html}<p><b>{html.escape(tr('Analysis Failed'))}</b><br>{html.escape(error)}</p>"
            )
            self.gop_button.hide()
        elif probe is None:
            self.report_label.setText(f"{path_html}<p>{html.escape(tr('Analyzing...'))}</p>")
            self.gop_button.hide()
        else:
            self.report_label.setText(_probe_report_html(self.path, probe))
            has_video = any(
                stream.get("codec_type") == "video"
                and (stream.get("disposition") or {}).get("attached_pic") not in {1, True, "1"}
                for stream in probe.get("streams") or []
            )
            analysis = probe.get("gop_analysis")
            self.gop_button.setVisible(has_video)
            self.gop_button.setEnabled(has_video and (not analysis or bool(analysis.get("error"))))
            self.gop_button.setText(tr("Retry GOP Analysis") if analysis and analysis.get("error") else (
                tr("GOP Analyzed") if analysis else tr("Analyze GOP")
            ))

    def set_gop_busy(self, busy: bool) -> None:
        """更新個別字卡的 GOP 分析狀態"""
        self._gop_busy = busy
        self.gop_button.setVisible(True)
        self.gop_button.setEnabled(not busy)
        self.gop_button.setText(tr("Analyzing GOP...") if busy else tr("Analyze GOP"))

    def _request_gop(self) -> None:
        self.set_gop_busy(True)
        self.gop_requested.emit(self.path)

    def set_number(self, number: int) -> None:
        self.title_label.setText(f"#{number}")

    def retranslate(self) -> None:
        busy = self._gop_busy
        self.remove_button.setAccessibleName(tr("Close Analysis Card"))
        self.remove_button.setToolTip(tr("Close Analysis Card"))
        self.gop_button.setToolTip(tr("Scan video frames to measure the keyframe interval"))
        self.set_result(self.probe, self.error)
        if busy: self.set_gop_busy(True)


class _FileAnalysisScrollArea(QScrollArea):
    """接收本機檔案拖放的分析結果捲動區"""

    files_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if FileDropListWidget.local_file_paths(event.mimeData()): event.acceptProposedAction()
        else: event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if FileDropListWidget.local_file_paths(event.mimeData()): event.acceptProposedAction()
        else: event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = FileDropListWidget.local_file_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        self.files_dropped.emit(paths)
        event.acceptProposedAction()


class FileAnalysisPanel(QWidget):
    """以向下捲動卡片顯示多個本機檔案的 FFprobe 報告"""

    browse_files_requested = Signal()
    analyze_requested = Signal(list)
    gop_analysis_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("fileAnalysisPanel")
        self.setProperty("role", "panel")
        self._cards: dict[str, _FileAnalysisCard] = {}
        self.browse_button = QPushButton("Browse Files")
        self.clear_button = QPushButton("Clear All")
        self.empty_label = QLabel("Drop files here or browse for files to inspect")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _set_role("default", self.browse_button)
        _set_role("ghost", self.clear_button)
        _set_role("muted", self.empty_label)
        buttons = QHBoxLayout()
        buttons.setSpacing(5)
        buttons.addWidget(self.browse_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch()
        self.results_widget = QWidget()
        self.results_layout = QVBoxLayout(self.results_widget)
        self.results_layout.setContentsMargins(4, 4, 4, 4)
        self.results_layout.setSpacing(8)
        self.results_layout.addWidget(self.empty_label)
        self.results_layout.addStretch()
        self.scroll_area = _FileAnalysisScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidget(self.results_widget)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        _add_page_header(layout, "File Analysis", "View format, video, audio, and other details about media files")
        layout.addLayout(buttons)
        layout.addWidget(self.scroll_area, 1)
        self.browse_button.clicked.connect(self.browse_files_requested)
        self.clear_button.clicked.connect(self.clear_all)
        self.scroll_area.files_dropped.connect(self.analyze_files)
        self._refresh_empty_state()

    def analyze_files(self, paths: Iterable[str | Path]) -> None:
        """加入報告卡片並送出尚未分析的檔案"""
        requested = []
        for raw_path in paths:
            path = str(Path(raw_path))
            if not Path(path).is_file(): continue
            card = self._cards.get(path)
            if card is None:
                card = _FileAnalysisCard(path, len(self._cards) + 1)
                card.remove_requested.connect(self.remove_report)
                card.gop_requested.connect(self.gop_analysis_requested)
                self._cards[path] = card
                self.results_layout.insertWidget(self.results_layout.count() - 1, card)
            card.set_result(None, "")
            requested.append(path)
        self._refresh_empty_state()
        if requested: self.analyze_requested.emit(requested)

    def set_result(self, path: str, probe: dict[str, Any] | None, error: str = "") -> None:
        card = self._cards.get(path)
        if card is not None: card.set_result(probe, error)

    def remove_report(self, path: str) -> None:
        card = self._cards.pop(path, None)
        if card is not None:
            self.results_layout.removeWidget(card)
            card.deleteLater()
            for number, remaining in enumerate(self._cards.values(), 1): remaining.set_number(number)
        self._refresh_empty_state()

    def clear_all(self) -> None:
        for path in list(self._cards): self.remove_report(path)

    def report_paths(self) -> list[str]:
        return list(self._cards)

    def retranslate_reports(self) -> None:
        for card in self._cards.values(): card.retranslate()

    def _refresh_empty_state(self) -> None:
        self.empty_label.setVisible(not self._cards)
        self.clear_button.setEnabled(bool(self._cards))


class ConversionPanel(QWidget):
    """建立獨立 FFmpeg conversion task 與可重用 preset"""

    browse_files_requested = Signal()
    browse_output_requested = Signal()
    files_changed = Signal(list)
    validation_requested = Signal(dict)
    add_requested = Signal(dict)
    presets_changed = Signal(list)
    analyze_selected_requested = Signal(list)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("conversionPanel")
        self.setProperty("role", "panel")
        self._loading = False
        self._presets: list[ConversionPreset] = []
        self._setting_row_layouts: list[Any] = []
        self.files_list = FileDropListWidget()
        self.files_list.setObjectName("conversionFiles")
        self.files_list.setToolTip("Drag and drop local files here")
        self.files_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.browse_files_button = QPushButton("Add Files")
        self.analyze_files_button = QPushButton("Analyze Items")
        self.remove_files_button = QPushButton("Remove Items")
        self.clear_files_button = QPushButton("Clear")
        for button in (self.browse_files_button, self.remove_files_button, self.clear_files_button, self.analyze_files_button):
            button.setProperty("compact", True)

        self.output_directory_edit = QLineEdit()
        self.output_directory_edit.setPlaceholderText("Choose an output folder")
        self.output_button = QPushButton("Browse")

        self.output_type_combo = NoWheelComboBox()
        self.output_type_combo.addItem("Video", "video")
        self.output_type_combo.addItem("Audio", "audio")
        self.output_type_combo.addItem("Subtitle", "subtitle")
        self._remux_states = {"video": False, "audio": False}
        self._displayed_output_type = "video"
        self._preset_controls: dict[str, dict[str, Any]] = {}
        self._active_preset_ids = {"video": "default:video"}
        self._preset_detached = False
        self._create_preset_controls("video")
        video_presets = self._preset_controls["video"]
        self.preset_combo = video_presets["combo"]
        self.preset_status_label = video_presets["status"]
        self.save_preset_button = video_presets["save"]
        self.update_preset_button = video_presets["update"]
        self.rename_preset_button = video_presets["rename"]
        self.delete_preset_button = video_presets["delete"]

        self.target_format_combo = NoWheelComboBox()
        for label, value in (("MP4", "mp4"), ("MOV", "mov"), ("MKV", "mkv"), ("WebM", "webm")):
            self.target_format_combo.addItem(label, value)
        self.audio_format_combo = NoWheelComboBox()
        for label, value in (("MP3", "mp3"), ("M4A", "m4a"), ("Opus", "opus"), ("FLAC", "flac"), ("WAV", "wav")):
            self.audio_format_combo.addItem(label, value)
        self.subtitle_format_combo = NoWheelComboBox()
        for label, value in (("SRT", "srt"), ("WebVTT", "vtt"), ("ASS", "ass")):
            self.subtitle_format_combo.addItem(label, value)
        self.remux_checkbox = QCheckBox("Remux / Stream Copy")
        self.remux_checkbox.setToolTip(
            "Convert the file format without encoding. This is faster, but only works with compatible source and output formats"
        )
        self.encoder_combo = NoWheelComboBox()
        self.encoder_combo.addItem("Auto", "auto")
        self.encoder_combo.addItem(tr("CPU (Software)"), "cpu")

        self.video_codec_combo = NoWheelComboBox()
        for label, value in (("Auto", "auto"), ("H.264", "h264"), ("Apple ProRes", "prores")):
            self.video_codec_combo.addItem(label, value)
        self.prores_profile_combo = NoWheelComboBox()
        for label, value in (("Proxy", "proxy"), ("LT", "lt"), ("422", "422"), ("HQ", "hq")):
            self.prores_profile_combo.addItem(label, value)
        self.profile_stack = QStackedWidget()
        self.profile_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.resolution_combo = NoWheelComboBox()
        self.resolution_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.resolution_combo.addItem("Source", None)
        for height in (2160, 1440, 1080, 720, 480): self.resolution_combo.addItem(f"{height}p", height)
        self.resolution_combo.addItem("Custom", "custom")
        self.resolution_spin = QSpinBox()
        self.resolution_spin.setRange(2, 8192)
        self.resolution_spin.setSingleStep(2)
        self.resolution_spin.setValue(1080)
        self.resolution_spin.setSuffix(" px")
        self.allow_upscale_checkbox = QCheckBox("Upscale")
        self.allow_upscale_checkbox.setToolTip(
            "Allow output larger than the source; otherwise the selected resolution only scales down"
        )
        self.fps_combo = NoWheelComboBox()
        self.fps_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        for label, value in (
            ("Source", "source"), ("23.976", "24000/1001"), ("24", "24"), ("25", "25"),
            ("29.97", "30000/1001"), ("30", "30"), ("50", "50"), ("59.94", "60000/1001"),
            ("60", "60"), ("Custom", "custom"),
        ):
            self.fps_combo.addItem(label, value)
        self.fps_spin = QDoubleSpinBox()
        self.fps_spin.setRange(1, 240)
        self.fps_spin.setDecimals(3)
        self.fps_spin.setValue(30)
        self.fps_spin.setSuffix(" FPS")
        self.quality_mode_combo = NoWheelComboBox()
        self.quality_mode_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        for label, value in (
            ("Constant Bitrate (CBR)", "cbr"), ("Constant Rate Factor (CRF)", "crf"),
            ("Average Bitrate (VBR)", "vbr"), ("Average Bitrate (VBR 2-Pass)", "vbr_2pass"),
        ):
            self.quality_mode_combo.addItem(label, value)
        self.quality_mode_combo.setCurrentIndex(self.quality_mode_combo.findData("vbr"))
        self.quality_value_spin = QDoubleSpinBox()
        self.quality_value_spin.setDecimals(3)
        self.quality_value_spin.setValue(7.5)
        self.maximum_bitrate_spin = QDoubleSpinBox()
        self.maximum_bitrate_spin.setDecimals(3)
        self.maximum_bitrate_spin.setSingleStep(0.1)
        self.maximum_bitrate_spin.setRange(0.001, 1000)
        self.maximum_bitrate_spin.setValue(20)
        self.maximum_bitrate_spin.setSuffix(" Mbps")
        self.gop_combo = NoWheelComboBox()
        for label, value in (("Auto", None), ("All-I", 1), ("Low - 30", 30), ("Medium - 60", 60), ("High - 120", 120)):
            self.gop_combo.addItem(label, value)
        self.h264_profile_combo = NoWheelComboBox()
        for label, value in (("Auto", "auto"), ("Baseline", "baseline"), ("Main", "main"), ("High", "high")):
            self.h264_profile_combo.addItem(label, value)
        self.profile_stack.addWidget(self.h264_profile_combo)
        self.profile_stack.addWidget(self.prores_profile_combo)
        self.audio_codec_combo = NoWheelComboBox()
        for label, value in (
            ("Auto", "auto"), ("Stream Copy", "copy"), ("AAC", "aac"),
            ("PCM 16-bit", "pcm_s16le"), ("PCM 24-bit", "pcm_s24le"),
        ):
            self.audio_codec_combo.addItem(label, value)
        self.mute_audio_checkbox = QCheckBox("Mute")
        self.mute_audio_checkbox.setToolTip("Remove the audio track from the output video")
        self.audio_bitrate_combo = NoWheelComboBox()
        self.audio_bitrate_combo.addItem("Auto", None)
        for bitrate in (128, 192, 256, 320): self.audio_bitrate_combo.addItem(f"{bitrate} kbps", bitrate)
        self.audio_bitrate_combo.addItem("Custom", "custom")
        self.audio_bitrate_spin = QSpinBox()
        self.audio_bitrate_spin.setRange(8, 1536)
        self.audio_bitrate_spin.setValue(320)
        self.audio_bitrate_spin.setSuffix(" kbps")
        self.video_audio_sample_rate_combo = NoWheelComboBox()
        self.audio_sample_rate_combo = NoWheelComboBox()
        for combo in (self.video_audio_sample_rate_combo, self.audio_sample_rate_combo):
            combo.addItem("Auto", None)
            for sample_rate, label in ((44100, "44.1 kHz"), (48000, "48 kHz"), (96000, "96 kHz"), (192000, "192 kHz")):
                combo.addItem(label, sample_rate)
        self.audio_quality_combo = NoWheelComboBox()
        self.audio_quality_combo.addItem("Auto", None)
        for bitrate in (128, 192, 256, 320): self.audio_quality_combo.addItem(f"{bitrate} kbps", bitrate)
        self.audio_quality_combo.addItem("Custom", "custom")
        self.audio_quality_spin = QSpinBox()
        self.audio_quality_spin.setRange(8, 1536)
        self.audio_quality_spin.setValue(192)
        self.audio_quality_spin.setSuffix(" kbps")
        for spin in (
            self.resolution_spin, self.fps_spin, self.quality_value_spin,
            self.audio_bitrate_spin, self.audio_quality_spin,
        ):
            spin.setFixedWidth(140)
        self.maximum_bitrate_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.validation_label = QLabel()
        self.validation_label.setWordWrap(True)
        self.validation_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.add_button = QPushButton("Add to Queue")
        self.add_button.setEnabled(False)
        _set_role("primary", self.add_button)
        _set_role("default", self.browse_files_button, self.output_button, *(controls["save"] for controls in self._preset_controls.values()))
        _set_role("ghost", self.analyze_files_button, self.remove_files_button, self.clear_files_button, *(
            controls[key] for controls in self._preset_controls.values() for key in ("update", "rename", "delete")
        ))
        _set_role("validation", self.validation_label)

        self.file_actions = QWidget()
        self.file_actions.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        file_buttons = QHBoxLayout(self.file_actions)
        file_buttons.setContentsMargins(0, 0, 0, 0)
        file_buttons.setSpacing(5)
        file_buttons.addWidget(self.browse_files_button)
        file_buttons.addWidget(self.remove_files_button)
        file_buttons.addWidget(self.clear_files_button)
        file_buttons.addWidget(self.analyze_files_button)
        file_buttons.addStretch()
        simple_group = QGroupBox("General Settings")
        simple_group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        simple_options = QFormLayout(simple_group)
        simple_options.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        simple_options.setHorizontalSpacing(5)
        simple_options.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.option_labels: dict[str, QLabel] = {}
        output_layout = QHBoxLayout()
        output_layout.setSpacing(5)
        output_layout.addWidget(self.output_directory_edit, 1)
        output_layout.addWidget(self.output_button)
        self.option_labels["output_folder"] = _add_option_row(
            simple_options, "Output Folder", output_layout
        )
        self.option_labels["output_type"] = _add_option_row(
            simple_options, "Output Type", self.output_type_combo
        )
        simple_options.addRow("", self.remux_checkbox)
        self.option_labels["acceleration"] = _add_option_row(
            simple_options, "Acceleration", self.encoder_combo,
            "Prefer supported hardware transcoding acceleration"
        )
        simple_options.addRow("", self.validation_label)

        left_widget = QWidget()
        self.general_container = left_widget
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 5, 0)
        left_layout.addWidget(self.file_actions)
        left_layout.addWidget(self.files_list, 1)
        left_layout.addWidget(simple_group)

        video_group = QGroupBox("Advanced Settings")
        video_group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        video_advanced = QFormLayout(video_group)
        video_advanced.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        video_advanced.setHorizontalSpacing(5)
        video_advanced.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        resolution_layout = QHBoxLayout()
        resolution_layout.setSpacing(5)
        resolution_layout.addWidget(self.resolution_combo, 1)
        resolution_layout.addWidget(self.allow_upscale_checkbox)
        resolution_layout.addWidget(self.resolution_spin)
        fps_layout = QHBoxLayout()
        fps_layout.setSpacing(5)
        fps_layout.addWidget(self.fps_combo, 1)
        fps_layout.addWidget(self.fps_spin)
        quality_layout = QHBoxLayout()
        quality_layout.setSpacing(5)
        quality_layout.addWidget(self.quality_mode_combo, 1)
        quality_layout.addWidget(self.quality_value_spin)
        audio_bitrate_layout = QHBoxLayout()
        audio_bitrate_layout.setSpacing(5)
        audio_bitrate_layout.addWidget(self.audio_bitrate_combo, 1)
        audio_bitrate_layout.addWidget(self.audio_bitrate_spin)
        video_audio_codec_layout = QHBoxLayout()
        video_audio_codec_layout.setSpacing(5)
        video_audio_codec_layout.addWidget(self.audio_codec_combo, 1)
        video_audio_codec_layout.addWidget(self.mute_audio_checkbox)
        self.option_labels["video_preset"] = _add_option_row(
            video_advanced, "Preset", self._preset_layout("video")
        )
        self.option_labels["video_format"] = _add_option_row(
            video_advanced, "Output Format", self.target_format_combo,
            "Choose the output file format. ProRes always uses MOV"
        )
        self.option_labels["video_codec"] = _add_option_row(
            video_advanced, "Video Transcoder", self.video_codec_combo,
            "Choose how the video is compressed. Auto selects a suitable option for the output format"
        )
        self.option_labels["profile"] = _add_option_row(
            video_advanced, "Transcoder Settings", self.profile_stack,
            "Adjust compatibility for H.264, or choose the editing quality level for ProRes"
        )
        self.option_labels["resolution"] = _add_option_row(
            video_advanced, "Resolution", resolution_layout,
            "Choose the output height. The width is calculated automatically to keep the original proportions"
        )
        self.option_labels["frame_rate"] = _add_option_row(
            video_advanced, "Frames Per Second", fps_layout,
            "Keep the source frame rate, or convert it to a fixed value. A higher frame rate may increase the file size"
        )
        self.option_labels["video_bitrate"] = _add_option_row(
            video_advanced, "Video Bitrate", quality_layout,
            "Control video quality and file size. A higher bitrate usually produces a larger file"
        )
        self.option_labels["maximum_bitrate"] = _add_option_row(
            video_advanced, "Maximum Bitrate", self.maximum_bitrate_spin,
            "Limit the highest bitrate used during VBR 2-Pass encoding"
        )
        self.option_labels["gop"] = _add_option_row(
            video_advanced, "GOP", self.gop_combo,
            "Choose the keyframe interval. Shorter intervals are easier to edit, but usually create larger files"
        )
        media_separator = QFrame()
        media_separator.setFrameShape(QFrame.Shape.HLine)
        media_separator.setProperty("role", "separator")
        video_advanced.addRow(media_separator)
        self.option_labels["video_audio_codec"] = _add_option_row(
            video_advanced, "Audio Transcoder", video_audio_codec_layout,
            "Choose how the audio track is compressed, or copy the source track without encoding"
        )
        self.option_labels["video_audio_sample_rate"] = _add_option_row(
            video_advanced, "Audio Sample Rate", self.video_audio_sample_rate_combo,
            "Choose how many audio samples are used per second. Auto keeps a suitable setting"
        )
        self.option_labels["video_audio_bitrate"] = _add_option_row(
            video_advanced, "Audio Bitrate", audio_bitrate_layout,
            "Set the AAC audio quality. A higher bitrate usually produces a larger file"
        )

        audio_group = QGroupBox("Advanced Settings")
        audio_group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        audio_advanced = QFormLayout(audio_group)
        audio_advanced.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        audio_advanced.setHorizontalSpacing(5)
        audio_advanced.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        audio_quality_layout = QHBoxLayout()
        audio_quality_layout.setSpacing(5)
        audio_quality_layout.addWidget(self.audio_quality_combo, 1)
        audio_quality_layout.addWidget(self.audio_quality_spin)
        self.option_labels["audio_format"] = _add_option_row(
            audio_advanced, "Output Format", self.audio_format_combo, "Choose the output audio file format"
        )
        self.option_labels["audio_sample_rate"] = _add_option_row(
            audio_advanced, "Audio Sample Rate", self.audio_sample_rate_combo,
            "Choose how many audio samples are used per second. Auto keeps a suitable setting"
        )
        self.option_labels["audio_quality"] = _add_option_row(
            audio_advanced, "Audio Bitrate", audio_quality_layout,
            "Set audio quality for MP3, M4A, or Opus. A higher bitrate usually produces a larger file"
        )

        subtitle_group = QGroupBox("Advanced Settings")
        subtitle_group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        subtitle_advanced = QFormLayout(subtitle_group)
        subtitle_advanced.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        subtitle_advanced.setHorizontalSpacing(5)
        subtitle_advanced.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.option_labels["subtitle_format"] = _add_option_row(
            subtitle_advanced, "Output Format", self.subtitle_format_combo, "Choose the output subtitle file format"
        )
        self._setting_row_layouts.extend([
            output_layout, resolution_layout, fps_layout, quality_layout,
            audio_bitrate_layout, video_audio_codec_layout, audio_quality_layout,
        ])

        self.advanced_stack = QStackedWidget()
        for group in (video_group, audio_group, subtitle_group):
            scroll = QScrollArea()
            scroll.setProperty("role", "conversionSettings")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(group)
            self.advanced_stack.addWidget(scroll)

        self.advanced_container = QWidget()
        advanced_container_layout = QVBoxLayout(self.advanced_container)
        advanced_container_layout.setContentsMargins(5, 0, 0, 0)
        advanced_container_layout.addWidget(self.advanced_stack)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(left_widget)
        self.splitter.addWidget(self.advanced_container)
        self.splitter.setHandleWidth(1)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self._splitter_ratio = 0.5
        self._applying_splitter = False
        self.splitter.setSizes([500, 500])
        self.splitter.splitterMoved.connect(self._splitter_moved)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        _add_page_header(
            layout,
            "Convert Media",
            "Convert video, audio, or subtitle files to the format you need",
            self.add_button,
        )
        layout.addWidget(self.splitter, 1)

        self.browse_files_button.clicked.connect(self.browse_files_requested)
        self.files_list.files_dropped.connect(self.add_files)
        self.analyze_files_button.clicked.connect(self.analyze_selected_files)
        self.remove_files_button.clicked.connect(self.remove_selected_files)
        self.clear_files_button.clicked.connect(self.clear_files)
        self.output_button.clicked.connect(self.browse_output_requested)
        for category, controls in self._preset_controls.items():
            controls["combo"].currentIndexChanged.connect(lambda _index, value=category: self._preset_selected(value))
            controls["save"].clicked.connect(lambda _checked=False, value=category: self._save_preset(value))
            controls["update"].clicked.connect(lambda _checked=False, value=category: self._update_preset(value))
            controls["rename"].clicked.connect(lambda _checked=False, value=category: self._rename_preset(value))
            controls["delete"].clicked.connect(lambda _checked=False, value=category: self._delete_preset(value))
        self.output_type_combo.currentIndexChanged.connect(self._output_type_changed)
        for combo in (
            self.target_format_combo, self.audio_format_combo, self.subtitle_format_combo,
            self.encoder_combo, self.video_codec_combo,
            self.prores_profile_combo, self.resolution_combo, self.fps_combo, self.quality_mode_combo,
            self.gop_combo, self.h264_profile_combo, self.audio_codec_combo,
            self.audio_bitrate_combo, self.video_audio_sample_rate_combo,
            self.audio_quality_combo, self.audio_sample_rate_combo,
        ):
            combo.currentIndexChanged.connect(self._settings_changed)
        for spin in (
            self.resolution_spin, self.fps_spin, self.quality_value_spin, self.maximum_bitrate_spin,
            self.audio_bitrate_spin, self.audio_quality_spin,
        ):
            spin.valueChanged.connect(self._settings_changed)
        self.allow_upscale_checkbox.toggled.connect(self._settings_changed)
        self.mute_audio_checkbox.toggled.connect(self._settings_changed)
        self.remux_checkbox.toggled.connect(self._remux_changed)
        self.output_directory_edit.textChanged.connect(self._refresh_add_button)
        self.add_button.clicked.connect(lambda: self.add_requested.emit(self.request_payload()))
        self._update_mode_controls()

    def _create_preset_controls(self, category: str) -> None:
        """建立指定轉檔類型的獨立 preset 控制元件"""
        combo = NoWheelComboBox()
        combo.setProperty("i18nDynamic", True)
        combo.addItem(tr("Default"), f"default:{category}")
        controls = {
            "combo": combo, "status": QLabel(), "save": QPushButton("Save As"),
            "update": QPushButton("Update"), "rename": QPushButton("Rename"), "delete": QPushButton("Delete"),
        }
        controls["update"].setEnabled(False)
        controls["rename"].setEnabled(False)
        controls["delete"].setEnabled(False)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for key in ("save", "update", "rename", "delete"):
            controls[key].setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._preset_controls[category] = controls

    def _preset_layout(self, category: str) -> QGridLayout:
        """排列單一類型的 preset 選單與管理按鈕"""
        controls = self._preset_controls[category]
        layout = QGridLayout()
        layout.setHorizontalSpacing(5)
        layout.setVerticalSpacing(5)
        self._setting_row_layouts.append(layout)
        layout.addWidget(controls["combo"], 0, 0, 1, 4)
        layout.addWidget(controls["save"], 1, 0)
        layout.addWidget(controls["update"], 1, 1)
        layout.addWidget(controls["rename"], 1, 2)
        layout.addWidget(controls["delete"], 1, 3)
        for column in range(4): layout.setColumnStretch(column, 1)
        return layout

    def add_files(self, paths: Iterable[str | Path]) -> None:
        """加入檔案並忽略重複路徑"""
        existing = set(self.file_paths())
        for path in paths:
            text = str(Path(path))
            if text in existing: continue
            item = QListWidgetItem(Path(text).name)
            item.setData(Qt.ItemDataRole.UserRole, text)
            item.setToolTip(text)
            self.files_list.addItem(item)
            existing.add(text)
        self._files_did_change()

    def remove_selected_files(self) -> None:
        """移除目前選取的輸入檔"""
        for item in self.files_list.selectedItems():
            self.files_list.takeItem(self.files_list.row(item))
        self._files_did_change()

    def clear_files(self) -> None:
        """清除所有輸入檔"""
        self.files_list.clear()
        self._files_did_change()

    def file_paths(self) -> list[str]:
        """回傳輸入檔路徑"""
        return [str(self.files_list.item(row).data(Qt.ItemDataRole.UserRole)) for row in range(self.files_list.count())]

    def selected_file_paths(self) -> list[str]:
        """取得檔案池中目前選取的檔案"""
        return [str(item.data(Qt.ItemDataRole.UserRole)) for item in self.files_list.selectedItems()]

    def analyze_selected_files(self) -> None:
        """將目前選取的檔案送去分析, 沒有選取時不執行"""
        paths = self.selected_file_paths()
        if paths: self.analyze_selected_requested.emit(paths)

    def set_available_backends(self, backends: Iterable[str]) -> None:
        """只顯示 service 已實際初始化成功的硬體品牌"""
        selected = self.encoder_combo.currentData()
        self.encoder_combo.clear()
        self.encoder_combo.addItem(tr("Auto"), "auto")
        self.encoder_combo.addItem(tr("CPU (Software)"), "cpu")
        labels = {"nvidia": "NVIDIA", "amd": "AMD", "intel": "Intel"}
        for backend in ("nvidia", "amd", "intel"):
            if backend in set(backends): self.encoder_combo.addItem(labels[backend], backend)
        index = self.encoder_combo.findData(selected)
        self.encoder_combo.setCurrentIndex(max(index, 0))

    def set_available_encoders(self, encoders: Iterable[str]) -> None:
        """相容舊介面, 將 FFmpeg encoder 名稱轉成硬體品牌"""
        values = set(encoders)
        self.set_available_backends(
            backend for backend, encoder in {
                "nvidia": "h264_nvenc", "amd": "h264_amf", "intel": "h264_qsv"
            }.items() if encoder in values
        )

    def restore_selection(self, target_format: str, mode: str, category: str = "") -> None:
        """還原最後使用的轉檔類型、格式與模式"""
        inferred = "audio" if target_format in {"mp3", "m4a", "opus", "flac", "wav"} else (
            "subtitle" if target_format in {"srt", "vtt", "ass"} else "video"
        )
        category = category if category in {"video", "audio", "subtitle"} else inferred
        self._loading = True
        self.output_type_combo.setCurrentIndex(max(self.output_type_combo.findData(category), 0))
        format_combo = {
            "video": self.target_format_combo, "audio": self.audio_format_combo, "subtitle": self.subtitle_format_combo,
        }[category]
        format_index = format_combo.findData(target_format)
        if format_index >= 0: format_combo.setCurrentIndex(format_index)
        if category in self._remux_states: self._remux_states[category] = mode == "remux"
        self._displayed_output_type = category
        self.remux_checkbox.setChecked(self._remux_states.get(category, False))
        self._loading = False
        self.advanced_stack.setCurrentIndex({"video": 0, "audio": 1, "subtitle": 2}[category])
        self._update_mode_controls()

    def set_splitter_sizes(self, sizes: Iterable[int]) -> None:
        """還原一般設定與進階設定的面板寬度"""
        values = list(sizes)
        if len(values) == 2 and all(value > 0 for value in values):
            self._splitter_ratio = values[0] / sum(values)
            self._apply_splitter_ratio()

    def splitter_sizes(self) -> list[int]:
        self._apply_splitter_ratio()
        return self.splitter.sizes()

    def _splitter_moved(self, _position: int = 0, _index: int = 0) -> None:
        if self._applying_splitter: return
        sizes = self.splitter.sizes()
        if sum(sizes): self._splitter_ratio = sizes[0] / sum(sizes)

    def _apply_splitter_ratio(self) -> None:
        """依目前寬度套用 splitter 比例"""
        available = self.splitter.width() - self.splitter.handleWidth()
        if available <= 0: return
        left = round(available * self._splitter_ratio)
        self._applying_splitter = True
        self.splitter.setSizes([left, available - left])
        self._applying_splitter = False

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if hasattr(self, "splitter"): self._apply_splitter_ratio()

    def set_presets(
        self,
        presets: Iterable[ConversionPreset],
        selected_ids: str | dict[str, str] = "default:video",
    ) -> None:
        """載入自訂影片轉檔 preset"""
        requested = selected_ids if isinstance(selected_ids, dict) else {"video": selected_ids}
        self._replace_preset_catalog(presets, requested.get("video", "default:video"))
        self._preset_detached = False
        self._apply_preset(self._selected_preset("video"), "video")

    def refresh_presets(self, presets: Iterable[ConversionPreset]) -> None:
        """刷新共用 preset catalog, 不改變目前表單值"""
        values = list(presets)
        selected_id = self._active_preset_ids["video"]
        available_ids = {preset.id for preset in values if preset.media_type == "video"}
        detached = not selected_id.startswith("default:") and selected_id not in available_ids
        self._replace_preset_catalog(values, selected_id)
        self._preset_detached = self._preset_detached or detached
        self._update_preset_state("video")

    def _replace_preset_catalog(self, presets: Iterable[ConversionPreset], selected_id: str) -> None:
        """替換 preset catalog 並維持指定的選取 ID"""
        self._loading = True
        self._presets = [
            ConversionPreset.from_dict(preset.to_dict()) for preset in presets if preset.media_type == "video"
        ]
        combo = self._preset_controls["video"]["combo"]
        combo.clear()
        combo.addItem(tr("Default"), "default:video")
        for preset in self._presets: combo.addItem(preset.name, preset.id)
        combo.setPlaceholderText("")
        if selected_id == "default": selected_id = "default:video"
        index = combo.findData(selected_id)
        combo.setCurrentIndex(max(index, 0))
        self._active_preset_ids["video"] = str(combo.currentData())
        self._loading = False

    def custom_presets(self) -> list[ConversionPreset]:
        return [ConversionPreset.from_dict(preset.to_dict()) for preset in self._presets]

    def current_preset_id(self, category: str | None = None) -> str:
        category = category or "video"
        return self._active_preset_ids[category]

    def selected_preset_ids(self) -> dict[str, str]:
        return dict(self._active_preset_ids)

    def current_output_type(self) -> str:
        return str(self.output_type_combo.currentData() or "video")

    def refresh_preset_language(self) -> None:
        """切換語言時只翻譯內建項目, 保留使用者名稱原文"""
        for category, controls in self._preset_controls.items():
            combo = controls["combo"]
            if combo.count(): combo.setItemText(0, tr("Default"))
            if combo.currentIndex() < 0:
                preset = self._selected_preset(category)
                combo.setPlaceholderText(f"{tr('Custom') if preset.id.startswith('default:') else preset.name}*")

    @staticmethod
    def _default_preset(category: str) -> ConversionPreset:
        target = {"video": "mp4", "audio": "mp3", "subtitle": "srt"}[category]
        return ConversionPreset(id=f"default:{category}", name="Default", media_type=category, target_format=target)

    def _selected_preset(self, category: str) -> ConversionPreset:
        preset_id = self._active_preset_ids[category]
        return next((preset for preset in self._presets if preset.id == preset_id), self._default_preset(category))

    def _preset_selected(self, category: str) -> None:
        if self._loading: return
        preset_id = self._preset_controls[category]["combo"].currentData()
        if preset_id is None: return
        self._preset_detached = False
        self._active_preset_ids[category] = str(preset_id)
        self._apply_preset(self._selected_preset(category), category)

    def _apply_preset(self, preset: ConversionPreset, category: str) -> None:
        self._loading = True
        common = []
        if category == "video":
            self._remux_states[category] = preset.stream_copy
            if self.current_output_type() == category: self.remux_checkbox.setChecked(preset.stream_copy)
            self.mute_audio_checkbox.setChecked(preset.audio_codec == "none")
            audio_codec = "auto" if preset.audio_codec == "none" else preset.audio_codec
            common = [
                (self.target_format_combo, preset.target_format),
                (self.video_codec_combo, preset.video_codec), (self.prores_profile_combo, preset.prores_profile),
                (self.fps_combo, preset.fps), (self.quality_mode_combo, preset.quality_mode),
                (self.gop_combo, preset.gop), (self.h264_profile_combo, preset.h264_profile),
                (self.audio_codec_combo, audio_codec),
                (self.audio_bitrate_combo, preset.audio_bitrate),
                (self.video_audio_sample_rate_combo, preset.audio_sample_rate),
            ]
        elif category == "audio":
            self._remux_states[category] = preset.stream_copy
            if self.current_output_type() == category: self.remux_checkbox.setChecked(preset.stream_copy)
            common = [
                (self.audio_format_combo, preset.target_format),
                (self.audio_quality_combo, preset.audio_bitrate),
                (self.audio_sample_rate_combo, preset.audio_sample_rate),
            ]
        else:
            common = [(self.subtitle_format_combo, preset.target_format)]
        for combo, value in common:
            index = combo.findData(value)
            if index >= 0: combo.setCurrentIndex(index)
        if category == "video":
            resolution_index = self.resolution_combo.findData(preset.resolution_height)
            if resolution_index < 0 and preset.resolution_height is not None:
                resolution_index = self.resolution_combo.findData("custom")
                self.resolution_spin.setValue(preset.resolution_height)
            self.resolution_combo.setCurrentIndex(max(resolution_index, 0))
            if self.fps_combo.findData(preset.fps) < 0:
                self.fps_combo.setCurrentIndex(self.fps_combo.findData("custom"))
                self.fps_spin.setValue(float(preset.fps))
            if preset.audio_bitrate is not None and self.audio_bitrate_combo.findData(preset.audio_bitrate) < 0:
                self.audio_bitrate_combo.setCurrentIndex(self.audio_bitrate_combo.findData("custom"))
                self.audio_bitrate_spin.setValue(preset.audio_bitrate)
            if preset.quality_value is not None: self.quality_value_spin.setValue(preset.quality_value)
            if preset.maximum_bitrate is not None: self.maximum_bitrate_spin.setValue(preset.maximum_bitrate)
            self.allow_upscale_checkbox.setChecked(preset.allow_upscale)
        elif category == "audio" and preset.audio_bitrate is not None and self.audio_quality_combo.findData(preset.audio_bitrate) < 0:
            self.audio_quality_combo.setCurrentIndex(self.audio_quality_combo.findData("custom"))
            self.audio_quality_spin.setValue(preset.audio_bitrate)
        self._loading = False
        self._update_mode_controls()
        self._update_preset_state(category)

    def _preset_values(self, category: str | None = None) -> dict[str, Any]:
        category = category or self.current_output_type()
        if category == "subtitle": return {"target_format": self.subtitle_format_combo.currentData()}
        if category == "audio":
            target_format, stream_copy = self.audio_format_combo.currentData(), self._remux_states[category]
            bitrate = self.audio_quality_combo.currentData()
            if bitrate == "custom": bitrate = self.audio_quality_spin.value()
            if target_format not in {"mp3", "m4a", "opus"} or stream_copy: bitrate = None
            return {
                "target_format": target_format, "stream_copy": stream_copy, "audio_bitrate": bitrate,
                "audio_sample_rate": self.audio_sample_rate_combo.currentData() if not stream_copy else None,
            }
        resolution = self.resolution_combo.currentData()
        if resolution == "custom": resolution = self.resolution_spin.value()
        fps = self.fps_combo.currentData()
        if fps == "custom": fps = f"{self.fps_spin.value():g}"
        quality_mode = self.quality_mode_combo.currentData()
        audio_codec = "none" if self.mute_audio_checkbox.isChecked() else self.audio_codec_combo.currentData()
        audio_bitrate = self.audio_bitrate_combo.currentData()
        if audio_bitrate == "custom": audio_bitrate = self.audio_bitrate_spin.value()
        if audio_codec != "aac": audio_bitrate = None
        audio_sample_rate = (
            self.video_audio_sample_rate_combo.currentData()
            if audio_codec not in {"none", "copy"} and not self._remux_states[category] else None
        )
        return {
            "target_format": self.target_format_combo.currentData(), "stream_copy": self._remux_states[category],
            "video_codec": self.video_codec_combo.currentData(), "prores_profile": self.prores_profile_combo.currentData(),
            "resolution_height": resolution, "allow_upscale": self.allow_upscale_checkbox.isChecked(),
            "fps": fps, "quality_mode": quality_mode,
            "quality_value": self.quality_value_spin.value(),
            "maximum_bitrate": self.maximum_bitrate_spin.value() if quality_mode == "vbr_2pass" else None,
            "gop": self.gop_combo.currentData(), "h264_profile": self.h264_profile_combo.currentData(),
            "pixel_format": "auto", "audio_codec": audio_codec, "audio_bitrate": audio_bitrate,
            "audio_sample_rate": audio_sample_rate,
        }

    def _preset_from_current(
        self,
        name: str,
        preset_id: str | None = None,
        category: str | None = None,
    ) -> ConversionPreset:
        category = category or self.current_output_type()
        return ConversionPreset(
            id=preset_id or str(uuid4()), name=name, media_type=category, **self._preset_values(category)
        )

    def _next_preset_name(self, category: str) -> str:
        """依目前名稱產生不重複的另存預設名稱"""
        preset = self._selected_preset(category)
        base = (tr("Custom") if preset.id.startswith("default:") else preset.name).removesuffix("*").strip()
        if not base: base = tr("Custom")
        used = {item.name.casefold() for item in self._presets if item.media_type == category}
        if base.casefold() not in used: return base
        match = re.fullmatch(r"(.+)-(\d+)", base)
        root, number = (match.group(1), int(match.group(2)) + 1) if match else (base, 1)
        while f"{root}-{number}".casefold() in used: number += 1
        return f"{root}-{number}"

    def _prompt_preset_name(self, title: str, current: str = "", category: str | None = None) -> str:
        category = category or "video"
        name, accepted = QInputDialog.getText(self, tr(title), tr("Preset name"), text=current)
        if not accepted: return ""
        name = name.strip()
        duplicate = any(
            preset.media_type == category and preset.name.casefold() == name.casefold() and preset.name != current
            for preset in self._presets
        )
        if not name or name.casefold() == "default" or duplicate:
            QMessageBox.warning(self, tr("Invalid Preset Name"), tr("Preset name must be unique and cannot be Default"))
            return ""
        return name

    def _save_preset(self, category: str | None = None) -> None:
        category = category or "video"
        name = self._prompt_preset_name(
            "Save Conversion Preset", self._next_preset_name(category), category
        )
        if not name: return
        preset = self._preset_from_current(name, category=category)
        self._presets.append(preset)
        selected = self.selected_preset_ids()
        selected[category] = preset.id
        self.set_presets(self._presets, selected)
        self.presets_changed.emit(self.custom_presets())

    def _update_preset(self, category: str | None = None) -> None:
        category = category or "video"
        preset = self._selected_preset(category)
        if preset.id.startswith("default:"): return
        if QMessageBox.question(self, tr("Update Preset"), tr(
            "Replace {name} with the current settings?", name=preset.name
        )) != QMessageBox.StandardButton.Yes:
            return
        index = self._presets.index(preset)
        self._presets[index] = self._preset_from_current(preset.name, preset.id, category)
        self.set_presets(self._presets, self.selected_preset_ids())
        self.presets_changed.emit(self.custom_presets())

    def _rename_preset(self, category: str | None = None) -> None:
        category = category or "video"
        preset = self._selected_preset(category)
        if preset.id.startswith("default:"): return
        name = self._prompt_preset_name("Rename Conversion Preset", preset.name, category)
        if not name: return
        preset.name = name
        self.set_presets(self._presets, self.selected_preset_ids())
        self.presets_changed.emit(self.custom_presets())

    def _delete_preset(self, category: str | None = None) -> None:
        category = category or "video"
        preset = self._selected_preset(category)
        if preset.id.startswith("default:"): return
        if QMessageBox.question(self, tr("Delete Preset"), tr("Delete {name}?", name=preset.name)) != QMessageBox.StandardButton.Yes:
            return
        self._presets.remove(preset)
        selected = self.selected_preset_ids()
        selected[category] = f"default:{category}"
        self.set_presets(self._presets, selected)
        self.presets_changed.emit(self.custom_presets())

    def set_request_error(self, message: str) -> None:
        """顯示 remux 或輸入驗證錯誤"""
        self.validation_label.setText(message)
        self._refresh_add_button()

    def _files_did_change(self) -> None:
        paths = self.file_paths()
        self.files_changed.emit(paths)
        self._refresh_add_button()
        self._request_validation()

    def _update_mode_controls(self) -> None:
        category = self.current_output_type()
        target, codec = self.target_format_combo.currentData(), self.video_codec_combo.currentData()
        video_target = target in {"mp4", "mov", "mkv", "webm"}
        _set_combo_item_enabled(self.video_codec_combo, "h264", target in {"mp4", "mov", "mkv"})
        if codec == "prores" and target != "mov": self.target_format_combo.setCurrentIndex(self.target_format_combo.findData("mov"))
        if codec == "h264" and target not in {"mp4", "mov", "mkv"}:
            self.video_codec_combo.setCurrentIndex(self.video_codec_combo.findData("auto"))
            codec = "auto"
        if category == "video" and codec == "prores" and self._remux_states["video"]:
            self._remux_states["video"] = False
            self._loading = True
            self.remux_checkbox.setChecked(False)
            self._loading = False
        target = self.target_format_combo.currentData()
        video_target = target in {"mp4", "mov", "mkv", "webm"}
        remux = self._remux_states.get(category, False)
        self.remux_checkbox.setEnabled(category in self._remux_states and not (category == "video" and codec == "prores"))
        encode_video = category == "video" and not remux and video_target
        advanced_video = encode_video and target in {"mp4", "mov", "mkv"}
        h264 = advanced_video and codec in {"auto", "h264"}
        prores = advanced_video and codec == "prores"
        pcm_allowed = target == "mov"
        for audio_codec in ("pcm_s16le", "pcm_s24le"):
            _set_combo_item_enabled(self.audio_codec_combo, audio_codec, pcm_allowed)
        if not pcm_allowed and self.audio_codec_combo.currentData() in {"pcm_s16le", "pcm_s24le"}:
            self.audio_codec_combo.setCurrentIndex(self.audio_codec_combo.findData("auto"))
        self.target_format_combo.setEnabled(not prores)
        self.encoder_combo.setEnabled(h264)
        self.option_labels["acceleration"].setEnabled(h264)
        self.video_codec_combo.setEnabled(advanced_video)
        for widget in (self.resolution_combo, self.fps_combo):
            widget.setEnabled(advanced_video)
        for key in ("resolution", "frame_rate"):
            self.option_labels[key].setEnabled(advanced_video)
        self.allow_upscale_checkbox.setEnabled(advanced_video and self.resolution_combo.currentData() is not None)
        self.resolution_spin.setEnabled(advanced_video and self.resolution_combo.currentData() == "custom")
        self.fps_spin.setEnabled(advanced_video and self.fps_combo.currentData() == "custom")
        self.profile_stack.setCurrentWidget(self.prores_profile_combo if prores else self.h264_profile_combo)
        self.profile_stack.setEnabled(h264 or prores)
        self.option_labels["profile"].setEnabled(h264 or prores)
        self.prores_profile_combo.setEnabled(prores)
        for widget in (self.quality_mode_combo, self.gop_combo, self.h264_profile_combo):
            widget.setEnabled(h264)
        self.option_labels["video_bitrate"].setEnabled(h264)
        two_pass = h264 and self.quality_mode_combo.currentData() == "vbr_2pass"
        self.maximum_bitrate_spin.setEnabled(two_pass)
        self.option_labels["maximum_bitrate"].setEnabled(two_pass)
        self.option_labels["gop"].setEnabled(h264)
        self.quality_value_spin.setEnabled(h264)
        if self.quality_mode_combo.currentData() == "crf":
            self.quality_value_spin.setDecimals(0)
            self.quality_value_spin.setSingleStep(1)
            self.quality_value_spin.setRange(0, 51)
            self.quality_value_spin.setSuffix(" CRF")
            if self.quality_value_spin.value() > 51: self.quality_value_spin.setValue(20)
        else:
            self.quality_value_spin.setDecimals(3)
            self.quality_value_spin.setSingleStep(0.1)
            self.quality_value_spin.setRange(0.001, 1000)
            self.quality_value_spin.setSuffix(" Mbps")
        self.mute_audio_checkbox.setEnabled(advanced_video)
        video_audio = advanced_video and not self.mute_audio_checkbox.isChecked()
        self.audio_codec_combo.setEnabled(video_audio)
        self.option_labels["video_audio_codec"].setEnabled(video_audio)
        audio_encoding = video_audio and self.audio_codec_combo.currentData() != "copy"
        self.video_audio_sample_rate_combo.setEnabled(audio_encoding)
        self.option_labels["video_audio_sample_rate"].setEnabled(audio_encoding)
        audio_aac = video_audio and self.audio_codec_combo.currentData() == "aac"
        self.audio_bitrate_combo.setEnabled(audio_aac)
        self.audio_bitrate_spin.setEnabled(audio_aac and self.audio_bitrate_combo.currentData() == "custom")
        self.option_labels["video_audio_bitrate"].setEnabled(audio_aac)
        audio_target = self.audio_format_combo.currentData()
        audio_lossy = audio_target in {"mp3", "m4a", "opus"}
        audio_quality = category == "audio" and not remux and audio_lossy
        audio_encoding = category == "audio" and not remux
        self.audio_quality_spin.setMaximum({"mp3": 320, "opus": 512}.get(audio_target, 1536))
        self.audio_quality_combo.setEnabled(audio_quality)
        self.audio_quality_spin.setEnabled(audio_quality and self.audio_quality_combo.currentData() == "custom")
        self.option_labels["audio_quality"].setEnabled(audio_quality)
        self.audio_sample_rate_combo.setEnabled(audio_encoding)
        self.option_labels["audio_sample_rate"].setEnabled(audio_encoding)
        self._refresh_add_button()
        self._request_validation()

    def _output_type_changed(self) -> None:
        if self._loading: return
        if self._displayed_output_type in self._remux_states:
            self._remux_states[self._displayed_output_type] = self.remux_checkbox.isChecked()
        category = self.current_output_type()
        self._loading = True
        self.remux_checkbox.setChecked(self._remux_states.get(category, False))
        self._loading = False
        self._displayed_output_type = category
        self.advanced_stack.setCurrentIndex({"video": 0, "audio": 1, "subtitle": 2}[category])
        self._update_mode_controls()

    def _remux_changed(self, checked: bool) -> None:
        """保存目前輸出類型的重新封裝狀態"""
        if self._loading: return
        category = self.current_output_type()
        if category not in self._remux_states: return
        self._remux_states[category] = checked
        self._settings_changed()

    def _settings_changed(self) -> None:
        if self._loading: return
        self._update_mode_controls()
        category = self.current_output_type()
        if category in self._preset_controls: self._update_preset_state(category)

    def _update_preset_state(self, category: str) -> None:
        preset = self._selected_preset(category)
        expected = preset.to_dict()
        current = self._preset_values(category)
        modified = self._preset_detached or any(
            current[key] != expected[key] for key in self._preset_comparison_keys(category, current)
        )
        controls = self._preset_controls[category]
        combo = controls["combo"]
        self._loading = True
        if modified:
            combo.setPlaceholderText(f"{tr('Custom') if preset.id.startswith('default:') else preset.name}*")
            combo.setCurrentIndex(-1)
        else:
            combo.setPlaceholderText("")
            index = combo.findData(preset.id)
            if index >= 0: combo.setCurrentIndex(index)
        self._loading = False
        controls["status"].setText("")
        custom_preset = not preset.id.startswith("default:")
        controls["update"].setEnabled(custom_preset and modified)
        controls["rename"].setEnabled(custom_preset)
        controls["delete"].setEnabled(custom_preset)

    def _preset_comparison_keys(self, category: str, current: dict[str, Any]) -> list[str]:
        """取得目前 panel 需要比對的 preset 欄位"""
        if category == "video": return list(current)
        return ["target_format", "stream_copy", "audio_bitrate", "audio_sample_rate"]

    def _request_validation(self) -> None:
        self.validation_requested.emit(self.request_payload())

    def _refresh_add_button(self) -> None:
        valid = bool(self.file_paths() and self.output_directory_edit.text().strip() and not self.validation_label.text())
        self.add_button.setEnabled(valid)

    def request_payload(self) -> dict[str, Any]:
        """建立 controller 可轉成 ConversionOptions 的 payload"""
        category = self.current_output_type()
        if category == "video":
            target_format, stream_copy = self.target_format_combo.currentData(), self._remux_states[category]
        elif category == "audio":
            target_format, stream_copy = self.audio_format_combo.currentData(), self._remux_states[category]
        else:
            target_format, stream_copy = self.subtitle_format_combo.currentData(), False
        resolution = self.resolution_combo.currentData()
        if resolution == "custom": resolution = self.resolution_spin.value()
        fps = self.fps_combo.currentData()
        if fps == "custom": fps = f"{self.fps_spin.value():g}"
        quality_mode = self.quality_mode_combo.currentData()
        audio_bitrate = self.audio_bitrate_combo.currentData()
        if audio_bitrate == "custom": audio_bitrate = self.audio_bitrate_spin.value()
        audio_quality = self.audio_quality_combo.currentData()
        if audio_quality == "custom": audio_quality = self.audio_quality_spin.value()
        video_audio_codec = "none" if self.mute_audio_checkbox.isChecked() else self.audio_codec_combo.currentData()
        if category == "audio":
            audio_bitrate = audio_quality if target_format in {"mp3", "m4a", "opus"} and not stream_copy else None
            audio_sample_rate = self.audio_sample_rate_combo.currentData() if not stream_copy else None
        elif video_audio_codec != "aac":
            audio_bitrate = None
            audio_sample_rate = (
                self.video_audio_sample_rate_combo.currentData()
                if video_audio_codec not in {"none", "copy"} and not stream_copy else None
            )
        else:
            audio_sample_rate = self.video_audio_sample_rate_combo.currentData() if not stream_copy else None
        return {
            "input_paths": self.file_paths(),
            "output_dir": self.output_directory_edit.text().strip(),
            "media_type": category,
            "target_format": target_format,
            "stream_copy": stream_copy,
            "encoder": "",
            "acceleration": self.encoder_combo.currentData() or "auto",
            "video_codec": self.video_codec_combo.currentData(),
            "prores_profile": self.prores_profile_combo.currentData(),
            "resolution_height": resolution,
            "allow_upscale": self.allow_upscale_checkbox.isChecked(),
            "fps": fps,
            "quality_mode": quality_mode,
            "quality_value": self.quality_value_spin.value(),
            "maximum_bitrate": self.maximum_bitrate_spin.value() if quality_mode == "vbr_2pass" else None,
            "gop": self.gop_combo.currentData(),
            "h264_profile": self.h264_profile_combo.currentData(),
            "pixel_format": "auto",
            "audio_codec": video_audio_codec if category == "video" else "auto",
            "audio_bitrate": audio_bitrate,
            "audio_sample_rate": audio_sample_rate if category in {"video", "audio"} else None,
        }


class _ReplacementSourceCard(QFrame):
    """接收單一替換素材並顯示 FFprobe 摘要"""

    browse_requested = Signal()
    source_changed = Signal(str)

    def __init__(self, title: str, media_type: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.media_type = media_type
        self.path = ""
        self.setAcceptDrops(True)
        self.setProperty("role", "card")
        self.title_label = QLabel(title)
        self.title_label.setProperty("role", "sectionTitle")
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("Drop one file here or browse")
        self.browse_button = QPushButton("Browse")
        self.clear_button = QPushButton("Clear")
        self.summary_label = QLabel("No file selected")
        self.summary_label.setWordWrap(True)
        self.summary_label.setProperty("role", "muted")
        self.loop_checkbox = QCheckBox("Loop")
        self.delay_spin = QDoubleSpinBox()
        self.delay_spin.setRange(-86400, 86400)
        self.delay_spin.setDecimals(3)
        self.delay_spin.setSingleStep(0.1)
        self.delay_spin.setSuffix(" s")
        self.delay_spin.setToolTip(
            "Positive values delay the source with black video or silence; negative values skip the beginning"
        )
        _set_role("default", self.browse_button)
        _set_role("ghost", self.clear_button)
        path_layout = QHBoxLayout()
        path_layout.setSpacing(5)
        path_layout.addWidget(self.path_edit, 1)
        path_layout.addWidget(self.browse_button)
        path_layout.addWidget(self.clear_button)
        options = QFormLayout()
        options.setHorizontalSpacing(5)
        options.addRow("Delay", self.delay_spin)
        options.addRow("", self.loop_checkbox)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        layout.addWidget(self.title_label)
        layout.addLayout(path_layout)
        layout.addWidget(self.summary_label)
        layout.addLayout(options)
        layout.addStretch()
        self.browse_button.clicked.connect(self.browse_requested)
        self.clear_button.clicked.connect(lambda: self.set_path(""))

    def set_path(self, path: str) -> None:
        normalized = str(Path(path)) if path and Path(path).is_file() else ""
        self.path = normalized
        self.path_edit.setText(normalized)
        self.summary_label.setText("Analyzing..." if normalized else "No file selected")
        self.summary_label.setProperty("role", "muted")
        self.loop_checkbox.setEnabled(bool(normalized))
        self.source_changed.emit(normalized)

    def set_probe(self, probe: dict[str, Any] | None, error: str = "") -> None:
        if error or not probe:
            self.summary_label.setText(error or "Unable to inspect this file")
            self.summary_label.setProperty("role", "validation")
            return
        stream = next((item for item in probe.get("streams") or [] if item.get("codec_type") == self.media_type), {})
        duration = probe.get("duration")
        details = [str(stream.get("codec_name") or "Unknown").upper()]
        if self.media_type == "video" and stream:
            details.append(f"{stream.get('width') or '?'} x {stream.get('height') or '?'}")
        if duration is not None: details.append(_format_duration(float(duration)))
        elif self.media_type == "video": details.append(tr("Static image"))
        self.summary_label.setText("  |  ".join(details))
        self.summary_label.setProperty("role", "muted")
        self.loop_checkbox.setEnabled(not (
            self.media_type == "video" and Path(self.path).suffix.lower()
            in {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".avif"}
        ))
        if not self.loop_checkbox.isEnabled(): self.loop_checkbox.setChecked(False)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if FileDropListWidget.local_file_paths(event.mimeData()): event.acceptProposedAction()
        else: event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if FileDropListWidget.local_file_paths(event.mimeData()): event.acceptProposedAction()
        else: event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = FileDropListWidget.local_file_paths(event.mimeData())
        if len(paths) != 1:
            self.summary_label.setText(tr("Drop exactly one file into this card"))
            self.summary_label.setProperty("role", "validation")
            event.ignore()
            return
        self.set_path(paths[0])
        event.acceptProposedAction()


class ReplacementPanel(ConversionPanel):
    """建立單一畫面與音訊合成任務"""

    browse_visual_requested = Signal()
    browse_audio_requested = Signal()
    sources_changed = Signal(list)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("replacementPanel")
        self._source_probes: dict[str, dict[str, Any]] = {}
        self._source_errors: dict[str, str] = {}
        self.output_type_combo.setCurrentIndex(self.output_type_combo.findData("video"))
        self.output_type_combo.hide()
        self.option_labels["output_type"].hide()
        self.remux_checkbox.hide()
        self.mute_audio_checkbox.setChecked(False)
        self.mute_audio_checkbox.hide()

        self.visual_card = _ReplacementSourceCard("Visual", "video")
        self.audio_card = _ReplacementSourceCard("Audio", "audio")
        source_widget = QWidget()
        source_layout = QVBoxLayout(source_widget)
        source_layout.setContentsMargins(0, 0, 5, 0)
        source_layout.setSpacing(8)
        source_layout.addWidget(self.visual_card, 1)
        source_layout.addWidget(self.audio_card, 1)
        old_widget = self.splitter.widget(0)
        old_widget.setParent(None)
        self._source_widget = source_widget
        self.splitter.insertWidget(0, source_widget)
        self._unused_general_widget = old_widget
        old_widget.hide()

        self.duration_mode_combo = NoWheelComboBox()
        for label, value in (("Longest Source", "longest"), ("Shortest Source", "shortest"), ("Custom", "custom")):
            self.duration_mode_combo.addItem(label, value)
        self.custom_duration_edit = QLineEdit()
        self.custom_duration_edit.setPlaceholderText("Hours:Minutes:Seconds.ms")
        self.aspect_ratio_combo = NoWheelComboBox()
        for label, value in (("Source", "source"), ("16:9", "16:9"), ("9:16", "9:16"), ("1:1", "1:1")):
            self.aspect_ratio_combo.addItem(label, value)
        self.fit_mode_combo = NoWheelComboBox()
        self.fit_mode_combo.addItem("Fit with Black Bars", "contain")
        self.fit_mode_combo.addItem("Fill and Crop", "cover")
        self.trim_start_spin = QDoubleSpinBox()
        self.trim_end_spin = QDoubleSpinBox()
        for spin in (self.trim_start_spin, self.trim_end_spin):
            spin.setRange(0, 86400)
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setSuffix(" s")
        self.force_reencode_checkbox = QCheckBox("Force Re-encoding")
        self.force_reencode_checkbox.setToolTip(
            "Encode both video and audio even when compatible streams could be copied"
        )
        common_group = QGroupBox("Common Settings")
        self.processing_summary_label = QLabel()
        self.processing_summary_label.setWordWrap(True)
        self.processing_summary_label.setProperty("role", "muted")
        common = QFormLayout(common_group)
        common.setHorizontalSpacing(5)
        common.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        output_layout = QHBoxLayout()
        output_layout.setSpacing(5)
        output_layout.addWidget(self.output_directory_edit, 1)
        output_layout.addWidget(self.output_button)
        _add_option_row(common, "Output Folder", output_layout)
        _add_option_row(common, "Total Duration", self.duration_mode_combo, "Choose the finished timeline length")
        _add_option_row(common, "Custom Duration", self.custom_duration_edit, "Enter seconds, MM:SS, or HH:MM:SS.mmm")
        _add_option_row(common, "Aspect Ratio", self.aspect_ratio_combo, "Keep the source shape or use a common video canvas")
        _add_option_row(common, "Image Fit", self.fit_mode_combo, "Show the whole image with black bars or crop it to fill the canvas")
        _add_option_row(common, "Cut Head", self.trim_start_spin, "Remove this amount from the beginning of the finished timeline")
        _add_option_row(common, "Cut Tail", self.trim_end_spin, "Remove this amount from the end of the finished timeline")
        common.addRow("", self.force_reencode_checkbox)
        common.addRow("", self.processing_summary_label)
        common.addRow("", self.validation_label)
        common.addRow("Acceleration", self.encoder_combo)
        video_scroll = self.advanced_stack.widget(0)
        video_group = video_scroll.takeWidget()
        right_content = QWidget()
        right_layout = QVBoxLayout(right_content)
        right_layout.setContentsMargins(5, 0, 0, 0)
        right_layout.setSpacing(8)
        right_layout.addWidget(common_group)
        right_layout.addWidget(video_group)
        right_layout.addStretch()
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setProperty("role", "conversionSettings")
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_scroll.setWidget(right_content)
        old_advanced = self.splitter.widget(1)
        old_advanced.setParent(None)
        self._unused_advanced_widget = old_advanced
        self.splitter.insertWidget(1, self.settings_scroll)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)

        header_labels = self.findChildren(QLabel)
        for label in header_labels:
            if label.text() == "Convert Media": label.setText("Replace Audio")
            elif label.text().startswith("Convert video"): label.setText(
                "Replace a video's audio or combine visual media with new audio"
            )
        self.visual_card.browse_requested.connect(self.browse_visual_requested)
        self.audio_card.browse_requested.connect(self.browse_audio_requested)
        self.visual_card.source_changed.connect(lambda _path: self._source_changed())
        self.audio_card.source_changed.connect(lambda _path: self._source_changed())
        for widget in (
            self.duration_mode_combo, self.aspect_ratio_combo, self.fit_mode_combo,
        ):
            widget.currentIndexChanged.connect(self._replacement_changed)
        for spin in (
            self.visual_card.delay_spin, self.audio_card.delay_spin,
            self.trim_start_spin, self.trim_end_spin,
        ):
            spin.valueChanged.connect(self._replacement_changed)
        self.visual_card.loop_checkbox.toggled.connect(self._replacement_changed)
        self.audio_card.loop_checkbox.toggled.connect(self._replacement_changed)
        self.force_reencode_checkbox.toggled.connect(self._replacement_changed)
        self.custom_duration_edit.textChanged.connect(self._replacement_changed)
        self._replacement_changed()

    @staticmethod
    def parse_duration(text: str) -> float | None:
        """由右向左解析秒、分:秒或時:分:秒"""
        value = text.strip()
        if not value: return None
        try:
            parts = value.split(":")
            if not 1 <= len(parts) <= 3 or any(not part for part in parts): return None
            seconds = float(parts[-1])
            if not math.isfinite(seconds) or seconds < 0: return None
            if len(parts) == 1: return seconds
            minutes = int(parts[-2])
            hours = int(parts[-3]) if len(parts) == 3 else 0
            if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60: return None
            return hours * 3600 + minutes * 60 + seconds
        except ValueError:
            return None

    @staticmethod
    def format_duration(seconds: float | None) -> str:
        if seconds is None: return ""
        try: value = max(0.0, float(seconds))
        except (TypeError, ValueError): return ""
        hours, remainder = divmod(value, 3600)
        minutes, value = divmod(remainder, 60)
        return f"{int(hours):02d}:{int(minutes):02d}:{value:06.3f}"

    def _source_changed(self) -> None:
        paths = [path for path in (self.visual_card.path, self.audio_card.path) if path]
        for path in paths:
            self._source_probes.pop(path, None)
            self._source_errors.pop(path, None)
        self.sources_changed.emit(paths)
        self._replacement_changed()

    def set_source_probe(self, path: str, probe: dict[str, Any] | None, error: str = "") -> None:
        if path not in {self.visual_card.path, self.audio_card.path}: return
        if probe is not None: self._source_probes[path] = probe
        if error: self._source_errors[path] = error
        card = self.visual_card if path == self.visual_card.path else self.audio_card
        card.set_probe(probe, error)
        self._request_validation()

    def source_probes(self) -> dict[str, dict[str, Any]]:
        return dict(self._source_probes)

    def source_errors(self) -> dict[str, str]:
        return dict(self._source_errors)

    def set_processing_summary(self, message: str) -> None:
        self.processing_summary_label.setText(message)

    def _replacement_changed(self, *_args: Any) -> None:
        if not hasattr(self, "duration_mode_combo"): return
        custom = self.duration_mode_combo.currentData() == "custom"
        self.custom_duration_edit.setEnabled(custom)
        self.fit_mode_combo.setEnabled(self.aspect_ratio_combo.currentData() != "source")
        self._request_validation()

    def _refresh_add_button(self) -> None:
        if not hasattr(self, "visual_card"):
            super()._refresh_add_button()
            return
        ready = (
            bool(self.visual_card.path and self.audio_card.path and self.output_directory_edit.text().strip())
            and self.visual_card.path in self._source_probes and self.audio_card.path in self._source_probes
            and not self.validation_label.text()
        )
        self.add_button.setEnabled(ready)

    def request_payload(self) -> dict[str, Any]:
        payload = super().request_payload()
        if not hasattr(self, "visual_card"): return payload
        payload.update({
            "visual_path": self.visual_card.path, "audio_path": self.audio_card.path,
            "duration_mode": self.duration_mode_combo.currentData(),
            "custom_duration": self.parse_duration(self.custom_duration_edit.text()),
            "visual_loop": self.visual_card.loop_checkbox.isChecked(),
            "audio_loop": self.audio_card.loop_checkbox.isChecked(),
            "visual_delay": self.visual_card.delay_spin.value(),
            "audio_delay": self.audio_card.delay_spin.value(),
            "trim_start": self.trim_start_spin.value(), "trim_end": self.trim_end_spin.value(),
            "aspect_ratio": self.aspect_ratio_combo.currentData(), "fit_mode": self.fit_mode_combo.currentData(),
            "force_reencode": self.force_reencode_checkbox.isChecked(),
        })
        payload["input_paths"] = []
        payload["stream_copy"] = False
        return payload

    def _apply_preset(self, preset: ConversionPreset, category: str) -> None:
        """套用共用轉碼設定, ReplacementPanel 固定不使用 remux"""
        values = preset.to_dict()
        values["stream_copy"] = False
        super()._apply_preset(ConversionPreset.from_dict(values), category)

    def _preset_comparison_keys(self, category: str, current: dict[str, Any]) -> list[str]:
        """ReplacementPanel 忽略不支援的 remux 差異"""
        return [key for key in super()._preset_comparison_keys(category, current) if key != "stream_copy"]

    def persistent_settings(self) -> dict[str, Any]:
        payload = self.request_payload()
        for key in ("visual_path", "audio_path", "input_paths", "media_type", "stream_copy"):
            payload.pop(key, None)
        payload["preset_id"] = self.current_preset_id()
        return payload

    def restore_settings(self, values: dict[str, Any]) -> None:
        """還原不包含來源檔案的替換設定"""
        self._loading = True
        combos = (
            (self.duration_mode_combo, values.get("duration_mode")),
            (self.aspect_ratio_combo, values.get("aspect_ratio")),
            (self.fit_mode_combo, values.get("fit_mode")),
            (self.target_format_combo, values.get("target_format")),
            (self.encoder_combo, values.get("acceleration")),
            (self.video_codec_combo, values.get("video_codec")),
            (self.prores_profile_combo, values.get("prores_profile")),
            (self.fps_combo, values.get("fps")),
            (self.quality_mode_combo, values.get("quality_mode")),
            (self.gop_combo, values.get("gop")),
            (self.h264_profile_combo, values.get("h264_profile")),
            (self.audio_codec_combo, values.get("audio_codec")),
            (self.video_audio_sample_rate_combo, values.get("audio_sample_rate")),
            (self.audio_bitrate_combo, values.get("audio_bitrate")),
        )
        for combo, value in combos:
            index = combo.findData(value)
            if index >= 0: combo.setCurrentIndex(index)
        resolution = values.get("resolution_height")
        resolution_index = self.resolution_combo.findData(resolution)
        if resolution_index < 0 and resolution is not None:
            resolution_index = self.resolution_combo.findData("custom")
            self.resolution_spin.setValue(int(resolution))
        self.resolution_combo.setCurrentIndex(max(0, resolution_index))
        self.custom_duration_edit.setText(self.format_duration(values.get("custom_duration")))
        self.visual_card.loop_checkbox.setChecked(bool(values.get("visual_loop")))
        self.audio_card.loop_checkbox.setChecked(bool(values.get("audio_loop")))
        self.visual_card.delay_spin.setValue(float(values.get("visual_delay") or 0))
        self.audio_card.delay_spin.setValue(float(values.get("audio_delay") or 0))
        self.trim_start_spin.setValue(float(values.get("trim_start") or 0))
        self.trim_end_spin.setValue(float(values.get("trim_end") or 0))
        self.force_reencode_checkbox.setChecked(bool(values.get("force_reencode")))
        self.allow_upscale_checkbox.setChecked(bool(values.get("allow_upscale")))
        if values.get("quality_value") is not None: self.quality_value_spin.setValue(float(values["quality_value"]))
        if values.get("maximum_bitrate") is not None: self.maximum_bitrate_spin.setValue(float(values["maximum_bitrate"]))
        self._loading = False
        self._update_mode_controls()
        self._update_preset_state("video")
        self._replacement_changed()


class SettingsPanel(QWidget):
    """管理 theme 與 app 私有工具搜尋目錄"""

    theme_changed = Signal(str)
    custom_title_bar_changed = Signal(bool)
    language_changed = Signal(str)
    browse_ffmpeg_requested = Signal()
    browse_js_runtime_requested = Signal()
    apply_tools_requested = Signal(dict)
    reset_dependency_reminders_requested = Signal()
    auto_check_updates_changed = Signal(bool)
    check_updates_requested = Signal()
    open_app_data_requested = Signal()
    factory_reset_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("settingsPanel")
        self.setProperty("role", "panel")
        self._loading = False
        self.theme_combo = NoWheelComboBox()
        self.theme_combo.addItem("Starlit Night", "starlit_night")
        self.theme_combo.addItem("Cute Light", "cute_light")
        self.custom_title_bar_checkbox = QCheckBox("Use integrated title bar after restart (Experimental)")
        self.custom_title_bar_checkbox.setToolTip(
            "Replaces the system frame with themed window controls the next time the application starts."
        )
        self.language_combo = NoWheelComboBox()
        self.language_combo.addItem("繁體中文", "zh_TW")
        self.language_combo.addItem("English", "en")

        self.current_version_label = QLabel()
        self.current_version_label.setProperty("i18nDynamic", True)
        self.auto_check_updates_checkbox = QCheckBox("Automatically check for updates")
        self.update_status_label = QLabel()
        self.update_status_label.setProperty("i18nDynamic", True)
        self.update_status_label.setWordWrap(True)
        self.check_updates_button = QPushButton("Check for Updates")
        self._update_status_source = "Update service is not configured"
        self._update_status_values: dict[str, Any] = {}

        self.manual_ffmpeg_checkbox = QCheckBox("Use a manual FFmpeg bin directory")
        self.ffmpeg_directory_edit = QLineEdit()
        self.ffmpeg_directory_edit.setPlaceholderText("Directory containing ffmpeg and ffprobe")
        self.ffmpeg_browse_button = QPushButton("Browse")
        self.ffmpeg_status_label = QLabel()
        self.ffmpeg_status_label.setProperty("i18nDynamic", True)
        self.ffmpeg_status_label.setWordWrap(True)

        self.manual_js_checkbox = QCheckBox("Use a manual JavaScript runtime bin directory")
        self.js_directory_edit = QLineEdit()
        self.js_directory_edit.setPlaceholderText("Directory containing deno, node, qjs, or bun")
        self.js_browse_button = QPushButton("Browse")
        self.js_status_label = QLabel()
        self.js_status_label.setProperty("i18nDynamic", True)
        self.js_status_label.setWordWrap(True)
        self.apply_button = QPushButton("Apply Tool Paths")
        self.reset_dependency_reminders_button = QPushButton("Reset Dependency Reminders")
        self.reset_dependency_reminders_button.setEnabled(False)
        self.open_app_data_button = QPushButton("Open Application Data Folder")
        self.factory_reset_button = QPushButton("Restore Factory Settings")
        _set_role("primary", self.apply_button)
        _set_role(
            "default",
            self.ffmpeg_browse_button,
            self.js_browse_button,
            self.check_updates_button,
            self.reset_dependency_reminders_button,
            self.open_app_data_button,
            self.factory_reset_button,
        )
        _set_role("muted", self.ffmpeg_status_label, self.js_status_label, self.update_status_label)
        self._build_layout()

        self.theme_combo.currentIndexChanged.connect(self._emit_theme_changed)
        self.custom_title_bar_checkbox.toggled.connect(self._emit_custom_title_bar_changed)
        self.language_combo.currentIndexChanged.connect(self._emit_language_changed)
        self.auto_check_updates_checkbox.toggled.connect(self._emit_auto_check_updates_changed)
        self.check_updates_button.clicked.connect(self.check_updates_requested)
        self.manual_ffmpeg_checkbox.toggled.connect(self._update_path_controls)
        self.manual_js_checkbox.toggled.connect(self._update_path_controls)
        self.ffmpeg_browse_button.clicked.connect(self.browse_ffmpeg_requested)
        self.js_browse_button.clicked.connect(self.browse_js_runtime_requested)
        self.apply_button.clicked.connect(lambda: self.apply_tools_requested.emit(self.tool_payload()))
        self.reset_dependency_reminders_button.clicked.connect(self.reset_dependency_reminders_requested)
        self.open_app_data_button.clicked.connect(self.open_app_data_requested)
        self.factory_reset_button.clicked.connect(self.factory_reset_requested)
        self._update_path_controls()

    def _build_layout(self) -> None:
        theme_group = QGroupBox("Appearance")
        theme_form = QFormLayout(theme_group)
        theme_form.addRow("Theme", self.theme_combo)
        theme_form.addRow("Language", self.language_combo)
        theme_form.addRow("", self.custom_title_bar_checkbox)

        self.updates_group = QGroupBox("Update Notifications and Downloads")
        updates_layout = QVBoxLayout(self.updates_group)
        updates_layout.addWidget(self.current_version_label)
        updates_layout.addWidget(self.auto_check_updates_checkbox)
        update_actions = QHBoxLayout()
        update_actions.addWidget(self.update_status_label, 1)
        update_actions.addWidget(self.check_updates_button)
        update_actions.addWidget(self.open_app_data_button)
        update_actions.addWidget(self.factory_reset_button)
        updates_layout.addLayout(update_actions)

        self.tools_group = QGroupBox("External Tools")
        self.tools_group.setToolTip(
            "Local command-line programs required for downloading and conversion; these are dependencies, not plugins"
        )
        tools_layout = QVBoxLayout(self.tools_group)
        tools_layout.addWidget(self.manual_ffmpeg_checkbox)
        ffmpeg_path = QHBoxLayout()
        ffmpeg_path.addWidget(self.ffmpeg_directory_edit, 1)
        ffmpeg_path.addWidget(self.ffmpeg_browse_button)
        tools_layout.addLayout(ffmpeg_path)
        tools_layout.addWidget(self.ffmpeg_status_label)
        tools_layout.addSpacing(8)
        tools_layout.addWidget(self.manual_js_checkbox)
        js_path = QHBoxLayout()
        js_path.addWidget(self.js_directory_edit, 1)
        js_path.addWidget(self.js_browse_button)
        tools_layout.addLayout(js_path)
        tools_layout.addWidget(self.js_status_label)
        tools_layout.addSpacing(8)
        apply_layout = QHBoxLayout()
        apply_layout.addWidget(self.reset_dependency_reminders_button)
        apply_layout.addStretch()
        apply_layout.addWidget(self.apply_button)
        tools_layout.addLayout(apply_layout)

        content = QWidget()
        content.setProperty("role", "panel")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 8, 8, 8)
        _add_page_header(
            layout,
            "Settings",
            "Change appearance, updates, and how the application works",
        )
        layout.addWidget(theme_group)
        layout.addWidget(self.updates_group)
        layout.addWidget(self.tools_group)
        layout.addStretch()

        self.settings_scroll = QScrollArea()
        self.settings_scroll.setObjectName("settingsScroll")
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_scroll.setWidget(content)
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(self.settings_scroll)

    def set_settings(self, settings: Any) -> None:
        """將已保存設定載入 controls"""
        self._loading = True
        index = self.theme_combo.findData(settings.theme_name)
        self.theme_combo.setCurrentIndex(max(index, 0))
        self.custom_title_bar_checkbox.setChecked(settings.experimental_custom_title_bar)
        language_index = self.language_combo.findData(settings.language)
        self.language_combo.setCurrentIndex(max(language_index, 0))
        self.manual_ffmpeg_checkbox.setChecked(settings.manual_ffmpeg_enabled)
        self.ffmpeg_directory_edit.setText(settings.ffmpeg_bin_dir)
        self.manual_js_checkbox.setChecked(settings.manual_js_runtime_enabled)
        self.js_directory_edit.setText(settings.js_runtime_bin_dir)
        self.auto_check_updates_checkbox.setChecked(settings.auto_check_updates)
        self.set_dependency_reminders_ignored(bool(settings.ignored_missing_dependencies))
        self._loading = False
        self._update_path_controls()

    def set_dependency_reminders_ignored(self, ignored: bool) -> None:
        """依目前略過狀態更新提醒重設按鈕"""
        self.reset_dependency_reminders_button.setEnabled(ignored)

    def set_update_info(self, version: str, status_source: str, **values: Any) -> None:
        """更新目前版本與更新服務狀態"""
        self.current_version_label.setText(tr("Current Version: {version}", version=version))
        self._update_status_source = status_source
        self._update_status_values = values
        self.update_status_label.setText(tr(status_source, **values))

    def retranslate_update_info(self, version: str) -> None:
        """切換語言後重新產生動態更新文字"""
        self.set_update_info(version, self._update_status_source, **self._update_status_values)

    def tool_payload(self) -> dict[str, Any]:
        """建立工具路徑設定 payload"""
        return {
            "manual_ffmpeg_enabled": self.manual_ffmpeg_checkbox.isChecked(),
            "ffmpeg_bin_dir": self.ffmpeg_directory_edit.text().strip(),
            "manual_js_runtime_enabled": self.manual_js_checkbox.isChecked(),
            "js_runtime_bin_dir": self.js_directory_edit.text().strip(),
        }

    def set_tool_status(
        self, ffmpeg: str, js_runtime: str, ffmpeg_details: str = "", js_runtime_details: str = ""
    ) -> None:
        """顯示目前實際解析到的工具"""
        self.ffmpeg_status_label.setText(ffmpeg)
        self.js_status_label.setText(js_runtime)
        self.ffmpeg_status_label.setToolTip(ffmpeg_details)
        self.js_status_label.setToolTip(js_runtime_details)

    def _update_path_controls(self) -> None:
        ffmpeg_enabled = self.manual_ffmpeg_checkbox.isChecked()
        js_enabled = self.manual_js_checkbox.isChecked()
        self.ffmpeg_directory_edit.setEnabled(ffmpeg_enabled)
        self.ffmpeg_browse_button.setEnabled(ffmpeg_enabled)
        self.js_directory_edit.setEnabled(js_enabled)
        self.js_browse_button.setEnabled(js_enabled)

    def _emit_theme_changed(self) -> None:
        if not self._loading: self.theme_changed.emit(str(self.theme_combo.currentData()))

    def _emit_custom_title_bar_changed(self, enabled: bool) -> None:
        if not self._loading: self.custom_title_bar_changed.emit(enabled)

    def _emit_language_changed(self) -> None:
        if not self._loading: self.language_changed.emit(str(self.language_combo.currentData()))

    def _emit_auto_check_updates_changed(self, enabled: bool) -> None:
        if not self._loading: self.auto_check_updates_changed.emit(enabled)


@dataclass(frozen=True)
class _LogEntry:
    """保留單筆 log 的文字與 level"""

    message: str
    level: int

    @property
    def block_count(self) -> int:
        return self.message.count("\n") + 1


class LogPanel(QWidget):
    """顯示可選取、著色和篩選的 application log"""

    def __init__(self, parent: QWidget | None = None, maximum_blocks: int = 5000):
        super().__init__(parent)
        self.setObjectName("logPanel")
        self.setProperty("role", "panel")
        self.maximum_blocks = max(1, int(maximum_blocks))
        self._entries: deque[_LogEntry] = deque()
        self._block_count = 0
        self.text_edit = QPlainTextEdit()
        self.text_edit.setObjectName("logOutput")
        self.text_edit.setReadOnly(True)
        self.text_edit.setMaximumBlockCount(self.maximum_blocks)
        self.text_edit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.error_filter_button = QPushButton("Only Errors")
        self.error_filter_button.setCheckable(True)
        self.clear_button = QPushButton("Clear")
        _set_role("logFilter", self.error_filter_button)
        _set_role("ghost", self.clear_button)
        buttons = QHBoxLayout()
        buttons.addWidget(self.error_filter_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        _add_page_header(
            layout, "Application Log", "Review application activity and find errors when something goes wrong"
        )
        layout.addLayout(buttons)
        layout.addWidget(self.text_edit, 1)
        self.error_filter_button.toggled.connect(lambda _checked: self._render(scroll_to_end=True))
        self.clear_button.clicked.connect(self.clear)

    def append(self, message: str, level: int = logging.INFO) -> None:
        """加入一行 log"""
        lines = message.rstrip("\n").split("\n")
        if len(lines) > self.maximum_blocks:
            if self.maximum_blocks == 1:
                lines = lines[-1:]
            elif self.maximum_blocks == 2:
                lines = [lines[0], lines[-1]]
            else:
                omitted = len(lines) - self.maximum_blocks + 1
                lines = [lines[0], f"... {omitted} earlier lines omitted ...", *lines[-(self.maximum_blocks - 2):]]
        entry = _LogEntry("\n".join(lines), int(level))
        scrollbar = self.text_edit.verticalScrollBar()
        scroll_value = scrollbar.value()
        follow_output = scrollbar.value() >= scrollbar.maximum() - 1
        self._entries.append(entry)
        self._block_count += entry.block_count
        evicted = False
        while len(self._entries) > 1 and self._block_count > self.maximum_blocks:
            self._block_count -= self._entries.popleft().block_count
            evicted = True
        if evicted:
            self._render(scroll_to_end=follow_output)
        elif self._entry_visible(entry):
            self._append_entry(entry)
            scrollbar.setValue(scrollbar.maximum() if follow_output else min(scroll_value, scrollbar.maximum()))

    def clear(self) -> None:
        """清除目前保留的所有 log"""
        self._entries.clear()
        self._block_count = 0
        self.text_edit.clear()

    def refresh_colors(self) -> None:
        """Theme 切換後重新套用 log level 色彩"""
        scrollbar = self.text_edit.verticalScrollBar()
        follow_output = scrollbar.value() >= scrollbar.maximum() - 1
        self._render(scroll_to_end=follow_output, scroll_value=scrollbar.value())

    def _entry_visible(self, entry: _LogEntry) -> bool:
        return not self.error_filter_button.isChecked() or entry.level >= logging.ERROR

    def _entry_formats(self, level: int) -> tuple[QTextBlockFormat, QTextCharFormat]:
        """依 log level 建立整筆紀錄的顯示樣式"""
        block_format = QTextBlockFormat()
        char_format = QTextCharFormat()
        if level >= logging.ERROR:
            block_format.setBackground(QColor(theme_color("error_soft")))
            char_format.setForeground(QColor(theme_color("error")))
        elif level >= logging.WARNING:
            block_format.setBackground(QColor(theme_color("warning_soft")))
            char_format.setForeground(QColor(theme_color("warning")))
        return block_format, char_format

    def _append_entry(self, entry: _LogEntry) -> None:
        """將單筆紀錄以相同格式加入文字區"""
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        block_format, char_format = self._entry_formats(entry.level)
        for index, line in enumerate(entry.message.split("\n")):
            if not self.text_edit.document().isEmpty() or index:
                cursor.insertBlock(block_format, char_format)
            else:
                cursor.setBlockFormat(block_format)
                cursor.setCharFormat(char_format)
            cursor.insertText(line, char_format)
        self.text_edit.setTextCursor(cursor)

    def _render(self, scroll_to_end: bool, scroll_value: int = 0) -> None:
        """依目前 filter 重建可見 log"""
        self.text_edit.clear()
        for entry in self._entries:
            if self._entry_visible(entry): self._append_entry(entry)
        scrollbar = self.text_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum() if scroll_to_end else min(scroll_value, scrollbar.maximum()))

    append_log = append


class _TaskProgressRow(QWidget):
    """顯示單一執行中 task 的摘要與進度"""

    def __init__(self, task: TaskRecord, parent: QWidget | None = None):
        super().__init__(parent)
        self.summary_label = QLabel()
        self.summary_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.progress_bar = RoundedProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMinimumWidth(260)
        self.progress_bar.setMaximumWidth(420)
        self.progress_label = QLabel()
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.progress_label.setMinimumWidth(36)
        _set_role("muted", self.progress_label)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.summary_label, 1, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.progress_bar, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.progress_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.set_task(task)

    def set_task(self, task: TaskRecord) -> None:
        """更新 task 名稱與進度"""
        self.summary_label.setText(f"{tr(_enum_text(task.kind))}: {task.title}")
        if task.progress is None or task.progress < 0:
            self.progress_label.setText("--")
            self.progress_bar.setRange(0, 0)
            return
        percentage = round(max(0.0, min(1.0, task.progress)) * 100)
        self.progress_label.setText(f"{percentage}%")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(percentage)


class BottomStatusBar(QFrame):
    """逐列顯示每個執行中 task 的進度"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("bottomStatusBar")
        self.setProperty("role", "statusBar")
        self._tasks: list[TaskRecord] = []
        self._rows: dict[str, _TaskProgressRow] = {}
        self._paused = False
        self._message = ""
        self.message_label = QLabel(tr("Ready"))
        self.message_label.setProperty("i18nDynamic", True)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.rows_layout = QVBoxLayout(self)
        self.rows_layout.setContentsMargins(8, 5, 8, 5)
        self.rows_layout.setSpacing(4)
        self.rows_layout.addWidget(self.message_label, alignment=Qt.AlignmentFlag.AlignVCenter)

    def set_tasks(self, tasks: Sequence[TaskRecord]) -> None:
        """同步完整 task snapshot"""
        self._tasks = list(tasks)
        self.refresh()

    def update_task(self, task: TaskRecord) -> None:
        """更新單一 task snapshot"""
        for index, current in enumerate(self._tasks):
            if _task_id(current) != _task_id(task): continue
            self._tasks[index] = task
            break
        else:
            self._tasks.append(task)
        self.refresh()

    def set_dispatch_paused(self, paused: bool) -> None:
        """同步 queue 暫停狀態"""
        self._paused = paused
        self.refresh()

    def set_message(self, message: str) -> None:
        """設定沒有執行中 task 時顯示的訊息"""
        self._message = message
        self.refresh()

    def refresh(self, *_values: Any) -> None:
        """依實際執行中 task 數量重建可見 rows"""
        active = [task for task in self._tasks if task.status is TaskStatus.RUNNING]
        active_ids = {_task_id(task) for task in active}
        for task_id in list(self._rows):
            if task_id in active_ids: continue
            row = self._rows.pop(task_id)
            self.rows_layout.removeWidget(row)
            row.deleteLater()
        for index, task in enumerate(active):
            task_id = _task_id(task)
            row = self._rows.get(task_id)
            if row is None:
                row = _TaskProgressRow(task, self)
                self._rows[task_id] = row
            else:
                row.set_task(task)
            self.rows_layout.removeWidget(row)
            self.rows_layout.insertWidget(index, row)
        self.message_label.setVisible(not active)
        if not active:
            self.message_label.setText(tr("Queue paused") if self._paused else self._message or tr("Ready"))
        self.updateGeometry()
        if self.layout() is not None: self.layout().activate()

    def activity_rows(self) -> list[_TaskProgressRow]:
        """回傳目前依 task 順序顯示的進度 rows"""
        return [self._rows[_task_id(task)] for task in self._tasks if _task_id(task) in self._rows]
