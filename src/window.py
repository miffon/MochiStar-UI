from __future__ import annotations

import ctypes
import logging
import math
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QEvent, QRect, QRectF, QSize, QTimer, QUrl, Qt
from PySide6.QtGui import QColor, QCloseEvent, QDesktopServices, QIcon, QMouseEvent, QPainter, QPen, QRegion, QResizeEvent, QShowEvent
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QApplication,
)

from analysis_controller import AnalysisController
from controller import TaskController
from dependency_controller import DependencyController
from dependency_dialog import DependencyDialog
from external_tools import DependencyReport, ExternalToolInspector, installation_guides
from ffmpeg_service import FFmpegService
from file_analysis_controller import FileAnalysisController
from i18n import set_language, tr, translate_widget_tree
from logging_bridge import QtLogBridge
from media_service import YtDlpService
from models import (
    CookieConfig,
    ConversionOptions,
    DownloadOptions,
    MediaInfo,
    ReplacementOptions,
    SubtitleOptions,
    SubtitleSelection,
    TaskKind,
    TaskRecord,
)
from panels import (
    AnalyzePanel, BottomStatusBar, ConversionPanel, FileAnalysisPanel, ReplacementPanel,
    LogPanel, QueuePanel, RoundedProgressBar, SettingsPanel, SubtitlePanel,
)
from release_config import IS_TEST_BUILD
from storage import AppStorage, Settings
from theme import ThemeError, apply_theme, theme_color
from update_controller import UpdateController
from update_service import (
    DownloadedUpdate,
    ManualUpdateInstaller,
    QtGitHubReleaseProvider,
    QtUpdateDownloader,
    UpdateCheckResult,
    UpdateCheckStatus,
    UpdateRelease,
    manual_update_instructions,
)
from version import DISPLAY_VERSION, __version__


WINDOW_CORNER_RADIUS = 8
WINDOW_BORDER_INSET = 2.5
WINDOW_BORDER_RADIUS = 6


def create_update_progress_dialog(parent: QWidget) -> QProgressDialog:
    """建立使用共用圓角進度條的更新下載視窗"""
    dialog = QProgressDialog("", tr("Cancel"), 0, 100, parent)
    progress_bar = RoundedProgressBar(dialog)
    progress_bar.setTextVisible(False)
    dialog.setBar(progress_bar)
    dialog.setWindowTitle(tr("Application Updates"))
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    update_progress_dialog(dialog, 0, 100)
    return dialog


def update_progress_dialog(dialog: QProgressDialog, received: int, total: int) -> None:
    """更新下載進度, 未知總大小時不顯示百分比"""
    if total <= 0:
        dialog.setRange(0, 0)
        dialog.setLabelText(tr("Downloading update file..."))
        return
    percent = min(100, max(0, int(received * 100 / total)))
    dialog.setRange(0, 100)
    dialog.setValue(percent)
    dialog.setLabelText(f"{tr('Downloading update file...')} {percent}%")


def _rounded_rect_region(rect: QRect, radius: int) -> QRegion:
    """用逐列圓弧建立 1-bit rounded mask, 避免 polygon 產生大型銳角"""
    radius = max(0, min(int(radius), rect.width() // 2, rect.height() // 2))
    if radius == 0: return QRegion(rect)
    region = QRegion(QRect(rect.left(), rect.top() + radius, rect.width(), rect.height() - radius * 2))
    for row in range(radius):
        distance = radius - row - 0.5
        inset = math.ceil(radius - math.sqrt(radius * radius - distance * distance))
        width = rect.width() - inset * 2
        region |= QRegion(QRect(rect.left() + inset, rect.top() + row, width, 1))
        region |= QRegion(QRect(rect.left() + inset, rect.bottom() - row, width, 1))
    return region


class WindowBorderOverlay(QWidget):
    """在整合式視窗內側繪製平滑的 accent 框線"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(theme_color("accent")).darker(300), 1))
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(
                WINDOW_BORDER_INSET, WINDOW_BORDER_INSET, -WINDOW_BORDER_INSET, -WINDOW_BORDER_INSET
            ),
            WINDOW_BORDER_RADIUS,
            WINDOW_BORDER_RADIUS,
        )


class CustomTitleBar(QFrame):
    """在 frameless 模式提供可套用 theme 的視窗控制與拖曳區域"""

    def __init__(self, parent: QMainWindow):
        super().__init__(parent)
        self.setObjectName("topBar")
        self.setProperty("role", "topBar")
        self.setMinimumHeight(68)
        self.content_layout = QHBoxLayout(self)
        self.content_layout.setContentsMargins(18, 8, 14, 8)
        self.content_layout.setSpacing(8)

        self.logo_label = QLabel(self)
        self.logo_label.setObjectName("titleBarLogo")
        self.logo_label.setAccessibleName("MochiStar logo")
        self.logo_label.setFixedSize(QSize(40, 40))
        self.logo_label.setPixmap(QIcon(str(Path(__file__).resolve().parent / "assets" / "logo.svg")).pixmap(40, 40))
        self.content_layout.addWidget(self.logo_label)

        self.controls = QWidget(self)
        controls_layout = QHBoxLayout(self.controls)
        controls_layout.setContentsMargins(4, 0, 0, 0)
        controls_layout.setSpacing(2)
        self.minimize_button = self._control_button("minimize", "Minimize")
        self.maximize_button = self._control_button("maximize", "Maximize")
        self.restore_button = self._control_button("restore", "Restore")
        self.close_button = self._control_button("close", "Close", "windowClose")
        for button in (self.minimize_button, self.maximize_button, self.restore_button, self.close_button):
            controls_layout.addWidget(button)

        self.minimize_button.clicked.connect(parent.showMinimized)
        self.maximize_button.clicked.connect(parent.showMaximized)
        self.restore_button.clicked.connect(parent.showNormal)
        self.close_button.clicked.connect(parent.close)
        self.set_custom_enabled(False)

    def _control_button(self, icon_name: str, name: str, role: str = "windowControl") -> QToolButton:
        button = QToolButton(self.controls)
        button.setObjectName(name.lower())
        button.setProperty("role", role)
        button.setProperty("controlType", icon_name)
        button.setAccessibleName(name)
        button.setToolTip(name)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setFixedSize(QSize(18, 16))
        return button

    def add_controls(self) -> None:
        """在 title bar 內容完成後加入 Window Controls"""
        self.content_layout.addWidget(self.controls)

    def set_custom_enabled(self, enabled: bool) -> None:
        self.logo_label.setVisible(True)
        self.controls.setVisible(enabled)
        self.setProperty("customTitleBar", enabled)
        self.style().unpolish(self)
        self.style().polish(self)
        self.window_state_changed(self.window().windowState())

    def window_state_changed(self, state: Qt.WindowState) -> None:
        maximized = bool(state & Qt.WindowState.WindowMaximized)
        self.maximize_button.setVisible(not maximized)
        self.restore_button.setVisible(maximized)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.controls.isVisible():
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.controls.isVisible():
            self.window().showNormal() if self.window().isMaximized() else self.window().showMaximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class WindowResizeHandle(QWidget):
    """將 frameless window 邊緣交給系統執行 resize"""

    def __init__(self, parent: QMainWindow, edges: Qt.Edge, cursor: Qt.CursorShape):
        super().__init__(parent)
        self.edges = edges
        self.setCursor(cursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemResize(self.edges):
                event.accept()
                return
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    """組合 panel、service 與共用 task queue"""

    def __init__(
        self,
        storage: AppStorage,
        log_bridge: QtLogBridge | None = None,
        update_provider: Any | None = None,
        update_downloader: Any | None = None,
        dependency_inspector: ExternalToolInspector | None = None,
    ):
        super().__init__()
        self.storage = storage
        self.settings = storage.load_settings()
        self.dependency_inspector = dependency_inspector or ExternalToolInspector()
        self.dependency_controller = DependencyController(self.dependency_inspector)
        self.dependency_controller.report_ready.connect(self._startup_dependency_report_ready)
        self.dependency_controller.check_failed.connect(
            lambda error: logging.getLogger(__name__).warning("External dependency check failed: %s", error)
        )
        self._dependency_report: DependencyReport | None = None
        self._dependency_dialog: DependencyDialog | None = None
        set_language(self.settings.language)
        ffmpeg_directory = self.settings.ffmpeg_bin_dir if self.settings.manual_ffmpeg_enabled else ""
        js_directory = self.settings.js_runtime_bin_dir if self.settings.manual_js_runtime_enabled else ""
        self.media_service = YtDlpService()
        self.ffmpeg_service = FFmpegService()
        self.file_analysis_controller = FileAnalysisController(self.ffmpeg_service)
        self._conversion_probe_cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
        if hasattr(self.media_service, "configure_tools"):
            self.media_service.configure_tools(ffmpeg_directory, js_directory)
        if hasattr(self.ffmpeg_service, "configure_tools"):
            self.ffmpeg_service.configure_tools(ffmpeg_directory)
        self.analysis_controller = AnalysisController(self.media_service)
        self.subtitle_analysis_controller = AnalysisController(self.media_service)
        self.task_controller = TaskController(
            storage, self.media_service, self.ffmpeg_service, self.settings.worker_count
        )
        provider = update_provider or QtGitHubReleaseProvider()
        self.update_controller = UpdateController(
            provider, update_downloader or QtUpdateDownloader(), __version__, is_test_build=IS_TEST_BUILD,
        )
        self.update_installer = ManualUpdateInstaller(self._open_directory)
        self._updates_configured = bool(getattr(provider, "repository", True))
        self._update_progress_dialog: QProgressDialog | None = None
        self._downloading_release: UpdateRelease | None = None
        self.network = QNetworkAccessManager(self)
        self.log_bridge = log_bridge
        self.current_media: MediaInfo | None = None
        self._thumbnail_reply: QNetworkReply | None = None
        self._custom_title_bar_active = False
        self._native_window_corners = False
        self._resize_handles: dict[Qt.Edge, WindowResizeHandle] = {}
        self._panels: dict[str, QWidget] = {}
        self._navigation_buttons = QButtonGroup(self)
        self._navigation_buttons.setExclusive(True)

        self.setWindowTitle("MochiStar")
        self.setMinimumSize(960, 680)
        self.resize(1180, 820)
        self._build_ui()
        self._build_resize_handles()
        self._apply_custom_title_bar(self.settings.experimental_custom_title_bar)
        self._connect_signals()
        self._restore_settings()
        self.task_controller.publish_initial_state()
        self._check_external_tools()
        translate_widget_tree(self)
        self._set_initial_update_status()
        QTimer.singleShot(0, self._run_startup_dependency_check)
        QTimer.singleShot(1500, self._start_automatic_update_check)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        self.root = root
        self.root_layout = QGridLayout(root)
        self.root_layout.setContentsMargins(14, 14, 14, 12)
        self.root_layout.setSpacing(10)

        self.top_bar = CustomTitleBar(self)
        self.top_bar_layout = self.top_bar.content_layout

        brand = QWidget()
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(0)
        brand_title = QLabel("MochiStar")
        brand_subtitle = QLabel("Media toolkit")
        brand_title.setObjectName("brandTitle")
        brand_subtitle.setObjectName("brandSubtitle")
        brand_title.setProperty("role", "brandTitle")
        brand_subtitle.setProperty("role", "brandSubtitle")
        brand_layout.addWidget(brand_title)
        brand_layout.addWidget(brand_subtitle)
        self.top_bar_layout.addWidget(brand)
        self.top_bar_layout.addSpacing(24)

        self.navigation = QFrame()
        self.navigation.setObjectName("topNavigation")
        self.navigation_layout = QHBoxLayout(self.navigation)
        self.navigation_layout.setContentsMargins(0, 0, 0, 0)
        self.navigation_layout.setSpacing(4)
        self.top_bar_layout.addWidget(self.navigation)
        self.top_bar_layout.addStretch()
        self.top_bar.add_controls()
        self.panel_stack = QStackedWidget()
        self.panel_stack.setObjectName("panelStack")
        self.bottom_status = BottomStatusBar()

        self.root_layout.addWidget(self.top_bar, 0, 0)
        self.root_layout.addWidget(self.panel_stack, 1, 0)
        self.root_layout.addWidget(self.bottom_status, 2, 0)
        self.root_layout.setRowStretch(1, 1)
        self.setCentralWidget(root)

        self.analyze_panel = AnalyzePanel()
        self.subtitle_panel = SubtitlePanel()
        self.queue_panel = QueuePanel()
        self.file_analysis_panel = FileAnalysisPanel()
        self.conversion_panel = ConversionPanel()
        self.replacement_panel = ReplacementPanel()
        self.log_panel = LogPanel()
        self.settings_panel = SettingsPanel()
        self.register_panel("analyze", "Media", self.analyze_panel)
        self.register_panel("subtitle", "Subtitle", self.subtitle_panel)
        self.register_panel("file_analysis", "Analyze", self.file_analysis_panel)
        self.register_panel("conversion", "Convert", self.conversion_panel)
        self.register_panel("replacement", "Replace", self.replacement_panel)
        self.register_panel("queue", "Queue", self.queue_panel)
        self.register_panel("log", "Log", self.log_panel)
        self.register_panel("settings", "Settings", self.settings_panel)
        self._navigation_buttons.button(0).setChecked(True)
        self.panel_stack.setCurrentIndex(0)

    def register_panel(self, panel_id: str, label: str, widget: QWidget) -> None:
        """註冊可由 topBar 切換的新 panel"""
        if panel_id in self._panels: raise ValueError(f"Panel already registered: {panel_id}")
        button = QPushButton(label)
        button.setCheckable(True)
        button.setProperty("role", "navigation")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        index = self.panel_stack.addWidget(widget)
        self.navigation_layout.addWidget(button)
        self._navigation_buttons.addButton(button, index)
        button.clicked.connect(lambda checked=False, page=index: self.panel_stack.setCurrentIndex(page))
        self._panels[panel_id] = widget

    def _build_resize_handles(self) -> None:
        """建立 frameless window 的四邊與四角 resize 區域"""
        self._window_border_overlay = WindowBorderOverlay(self)
        specs = {
            Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
            Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
            Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
            Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
            Qt.Edge.TopEdge | Qt.Edge.LeftEdge: Qt.CursorShape.SizeFDiagCursor,
            Qt.Edge.BottomEdge | Qt.Edge.RightEdge: Qt.CursorShape.SizeFDiagCursor,
            Qt.Edge.TopEdge | Qt.Edge.RightEdge: Qt.CursorShape.SizeBDiagCursor,
            Qt.Edge.BottomEdge | Qt.Edge.LeftEdge: Qt.CursorShape.SizeBDiagCursor,
        }
        self._resize_handles = {
            edges: WindowResizeHandle(self, edges, cursor) for edges, cursor in specs.items()
        }
        self._update_resize_handles()

    def _connect_signals(self) -> None:
        # Analyze panel 事件
        self.analyze_panel.analyze_requested.connect(self._analyze)
        self.analyze_panel.cancel_analysis_requested.connect(self.analysis_controller.cancel)
        self.analyze_panel.add_requested.connect(self._add_download)
        self.analyze_panel.browse_output_requested.connect(
            lambda: self._browse_output(self.analyze_panel.output_directory_edit)
        )
        self.analyze_panel.browse_cookie_requested.connect(self._browse_cookie_file)
        self.analysis_controller.analysis_ready.connect(self._analysis_ready)
        self.analysis_controller.analysis_failed.connect(self._analysis_failed)
        self.analysis_controller.progress_changed.connect(self.analyze_panel.set_analysis_progress)
        self.analysis_controller.busy_changed.connect(self.analyze_panel.set_analyzing)

        # Subtitle panel 事件
        self.subtitle_panel.analyze_requested.connect(self._analyze_subtitles)
        self.subtitle_panel.cancel_analysis_requested.connect(self.subtitle_analysis_controller.cancel)
        self.subtitle_panel.add_requested.connect(self._add_subtitles)
        self.subtitle_panel.browse_output_requested.connect(
            lambda: self._browse_output(self.subtitle_panel.output_directory_edit)
        )
        self.subtitle_panel.browse_cookie_requested.connect(
            lambda: self._browse_cookie_file(self.subtitle_panel.cookie_file_edit)
        )
        self._connect_download_setting_sync()
        self.subtitle_analysis_controller.analysis_ready.connect(self._subtitle_analysis_ready)
        self.subtitle_analysis_controller.analysis_failed.connect(self._subtitle_analysis_failed)
        self.subtitle_analysis_controller.progress_changed.connect(self.subtitle_panel.set_analysis_progress)
        self.subtitle_analysis_controller.busy_changed.connect(self.subtitle_panel.set_analyzing)

        # Queue panel 事件
        self.queue_panel.start_requested.connect(self.task_controller.start_queue)
        self.queue_panel.pause_requested.connect(self.task_controller.pause_queue)
        self.queue_panel.cancel_requested.connect(self.task_controller.cancel_tasks)
        self.queue_panel.retry_requested.connect(self.task_controller.retry_tasks)
        self.queue_panel.remove_requested.connect(self.task_controller.remove_tasks)
        self.queue_panel.move_requested.connect(self.task_controller.move_tasks)
        self.queue_panel.concurrency_changed.connect(self._change_worker_count)
        self.queue_panel.open_output_requested.connect(self._open_task_output_folder)
        self.task_controller.tasks_changed.connect(self.queue_panel.set_tasks)
        self.task_controller.task_updated.connect(self.queue_panel.update_task)
        self.task_controller.queue_paused_changed.connect(self.queue_panel.set_dispatch_paused)
        self.task_controller.tasks_changed.connect(self.bottom_status.set_tasks)
        self.task_controller.task_updated.connect(self.bottom_status.update_task)
        self.task_controller.queue_paused_changed.connect(self.bottom_status.set_dispatch_paused)
        self.task_controller.worker_count_changed.connect(self.bottom_status.refresh)

        # Conversion panel 事件
        self.conversion_panel.browse_files_requested.connect(self._browse_conversion_files)
        self.conversion_panel.analyze_selected_requested.connect(self._analyze_conversion_files)
        self.conversion_panel.browse_output_requested.connect(
            lambda: self._browse_output(self.conversion_panel.output_directory_edit)
        )
        self.conversion_panel.validation_requested.connect(self._validate_conversion)
        self.conversion_panel.add_requested.connect(self._add_conversion)
        self.conversion_panel.presets_changed.connect(
            lambda presets: self._shared_presets_changed(self.conversion_panel, presets)
        )

        # Replacement panel 事件
        self.replacement_panel.browse_visual_requested.connect(lambda: self._browse_replacement_source("visual"))
        self.replacement_panel.browse_audio_requested.connect(lambda: self._browse_replacement_source("audio"))
        self.replacement_panel.browse_output_requested.connect(
            lambda: self._browse_output(self.replacement_panel.output_directory_edit)
        )
        self.replacement_panel.sources_changed.connect(self.file_analysis_controller.analyze)
        self.replacement_panel.validation_requested.connect(self._validate_replacement)
        self.replacement_panel.add_requested.connect(self._add_replacement)
        self.replacement_panel.presets_changed.connect(
            lambda presets: self._shared_presets_changed(self.replacement_panel, presets)
        )

        # File analysis panel 訊號
        self.file_analysis_panel.browse_files_requested.connect(self._browse_analysis_files)
        self.file_analysis_panel.analyze_requested.connect(self.file_analysis_controller.analyze)
        self.file_analysis_panel.gop_analysis_requested.connect(self.file_analysis_controller.analyze_gop)
        self.file_analysis_controller.analysis_finished.connect(self.file_analysis_panel.set_result)
        self.file_analysis_controller.analysis_finished.connect(self.replacement_panel.set_source_probe)
        self.file_analysis_controller.log_message.connect(
            lambda message: logging.getLogger("file-analysis").error(message)
        )

        # Log 轉送
        if self.log_bridge: self.log_bridge.message.connect(self.log_panel.append_log)
        self.analysis_controller.log_message.connect(lambda message: logging.getLogger("analysis").info(message))
        self.task_controller.log_message.connect(lambda message: logging.getLogger("task").info(message))
        self.subtitle_analysis_controller.log_message.connect(
            lambda message: logging.getLogger("subtitle").info(message)
        )
        self.update_controller.log_message.connect(lambda message: logging.getLogger("update").info(message))

        # Settings panel 事件
        self.settings_panel.theme_changed.connect(self._change_theme)
        self.settings_panel.custom_title_bar_changed.connect(self._set_custom_title_bar_preference)
        self.settings_panel.language_changed.connect(self._change_language)
        self.settings_panel.browse_ffmpeg_requested.connect(
            lambda: self._browse_tool_directory(self.settings_panel.ffmpeg_directory_edit)
        )
        self.settings_panel.browse_js_runtime_requested.connect(
            lambda: self._browse_tool_directory(self.settings_panel.js_directory_edit)
        )
        self.settings_panel.apply_tools_requested.connect(self._apply_tool_settings)
        self.settings_panel.reset_dependency_reminders_requested.connect(self._reset_dependency_reminders)
        self.settings_panel.auto_check_updates_changed.connect(self._set_auto_check_updates)
        self.settings_panel.check_updates_requested.connect(lambda: self._check_for_updates(True))
        self.settings_panel.open_app_data_requested.connect(self._open_application_data_folder)
        self.settings_panel.factory_reset_requested.connect(self._restore_factory_settings)
        self.update_controller.check_started.connect(self._update_check_started)
        self.update_controller.check_succeeded.connect(self._update_check_succeeded)
        self.update_controller.check_failed.connect(self._update_check_failed)
        self.update_controller.download_started.connect(self._update_download_started)
        self.update_controller.download_progress.connect(self._update_download_progress)
        self.update_controller.download_succeeded.connect(self._update_download_succeeded)
        self.update_controller.download_failed.connect(self._update_download_failed)
        self.update_controller.download_cancelled.connect(self._update_download_cancelled)

    def _connect_download_setting_sync(self) -> None:
        """同步媒體與字幕下載共用的輸出及 cookie 設定"""
        line_edits = (
            (self.analyze_panel.output_directory_edit, self.subtitle_panel.output_directory_edit),
            (self.analyze_panel.cookie_profile_edit, self.subtitle_panel.cookie_profile_edit),
            (self.analyze_panel.cookie_file_edit, self.subtitle_panel.cookie_file_edit),
        )
        for first, second in line_edits:
            first.textChanged.connect(second.setText)
            second.textChanged.connect(first.setText)

        combos = (
            (self.analyze_panel.cookie_mode_combo, self.subtitle_panel.cookie_mode_combo),
            (self.analyze_panel.cookie_browser_combo, self.subtitle_panel.cookie_browser_combo),
        )
        for first, second in combos:
            first.currentTextChanged.connect(lambda _text, source=first, target=second: self._sync_combo(source, target))
            second.currentTextChanged.connect(lambda _text, source=second, target=first: self._sync_combo(source, target))

    @staticmethod
    def _sync_combo(source: Any, target: Any) -> None:
        """同步 combo 的 data, editable combo 找不到項目時同步輸入文字"""
        index = target.findData(source.currentData())
        if index >= 0: target.setCurrentIndex(index)
        elif target.isEditable(): target.setCurrentText(source.currentText())

    def _restore_settings(self) -> None:
        settings = self.settings
        self.analyze_panel.output_directory_edit.setText(settings.output_dir)
        self.conversion_panel.output_directory_edit.setText(settings.last_conversion_output_dir or settings.output_dir)
        self.replacement_panel.output_directory_edit.setText(
            str(settings.replacement_settings.get("output_dir") or settings.output_dir)
        )
        self._set_combo(self.queue_panel.concurrency_combo, settings.worker_count)
        self.analyze_panel.set_column_widths(settings.download_column_widths)
        self.subtitle_panel.set_column_widths(settings.subtitle_column_widths)
        self.queue_panel.set_column_widths(settings.queue_column_widths)
        self._set_combo(self.analyze_panel.preset_combo, settings.last_preset)
        self._set_combo(self.analyze_panel.resolution_combo, settings.last_resolution)
        self._set_combo(self.analyze_panel.container_combo, settings.last_video_container)
        self._set_combo(self.analyze_panel.audio_output_combo, settings.last_audio_output)
        self.subtitle_panel.include_automatic_checkbox.setChecked(settings.include_automatic_subtitles)
        self.conversion_panel.set_presets(settings.conversion_presets, settings.last_conversion_preset_id)
        replacement_preset_id = str(settings.replacement_settings.get("preset_id") or "default:video")
        self.replacement_panel.set_presets(settings.conversion_presets, replacement_preset_id)
        self.conversion_panel.restore_selection(
            settings.last_conversion_format, settings.last_conversion_mode, settings.last_conversion_type
        )
        self.conversion_panel.set_splitter_sizes(settings.conversion_splitter_sizes)
        self._set_combo(self.conversion_panel.encoder_combo, settings.last_conversion_acceleration)
        self.replacement_panel.restore_settings(settings.replacement_settings)
        self.replacement_panel.set_splitter_sizes(settings.replacement_splitter_sizes)
        self._set_combo(self.analyze_panel.cookie_mode_combo, settings.cookie.source)
        self._set_combo(self.analyze_panel.cookie_browser_combo, settings.cookie.browser)
        self.analyze_panel.cookie_profile_edit.setText(settings.cookie.profile)
        self.analyze_panel.cookie_file_edit.setText(settings.cookie.file_path)
        self.settings_panel.set_settings(settings)
        self._set_initial_update_status()
        if settings.geometry: self.restoreGeometry(QByteArray(settings.geometry))
        if settings.window_state: self.restoreState(QByteArray(settings.window_state))

    def _check_external_tools(self) -> None:
        self.bottom_status.set_message("")
        availability = self.ffmpeg_service.availability
        conversion_available = availability["ffmpeg"] and availability["ffprobe"]
        self.conversion_panel.setEnabled(conversion_available)
        self.replacement_panel.setEnabled(conversion_available)
        ffprobe_available = bool(availability["ffprobe"])
        self.file_analysis_panel.setEnabled(ffprobe_available)
        self.file_analysis_panel.setToolTip("" if ffprobe_available else tr("File analysis disabled: FFprobe is unavailable"))
        if conversion_available:
            try:
                self.conversion_panel.set_available_backends(self.ffmpeg_service.list_hardware_backends())
                self.replacement_panel.set_available_backends(self.ffmpeg_service.list_hardware_backends())
                self._set_combo(self.conversion_panel.encoder_combo, self.settings.last_conversion_acceleration)
            except Exception as error:
                logging.getLogger(__name__).warning("Unable to inspect hardware encoders: %s", error)
        else:
            missing = ", ".join(name for name in ("ffmpeg", "ffprobe") if not availability[name])
            message = tr("Conversion disabled: {missing} is unavailable", missing=missing)
            self.conversion_panel.setToolTip(message)
            self.replacement_panel.setToolTip(message)
            logging.getLogger(__name__).warning(message)
            self.bottom_status.set_message(message)
        runtimes = self.media_service.detect_js_runtimes() if hasattr(self.media_service, "detect_js_runtimes") else {}
        ffmpeg_available = availability["ffmpeg"] and availability["ffprobe"]
        ffmpeg_status = tr("FFmpeg: {value}", value="OK" if ffmpeg_available else tr("Unavailable"))
        ffmpeg_path = "\n".join((
            f"FFmpeg: {getattr(self.ffmpeg_service, 'ffmpeg_path', '')}",
            f"FFprobe: {getattr(self.ffmpeg_service, 'ffprobe_path', '')}",
        )) if ffmpeg_available else ""
        js_status = tr("JavaScript runtime: {value}", value="OK" if runtimes else tr("Unavailable"))
        js_runtime_path = "\n".join(f"{name}: {path}" for name, path in runtimes.items())
        self.settings_panel.set_tool_status(ffmpeg_status, js_status, ffmpeg_path, js_runtime_path)
        if not runtimes:
            message = tr("No supported JavaScript runtime was found; some websites may fail JavaScript challenges")
            logging.getLogger(__name__).warning(message)
            self.bottom_status.set_message(message)

    def _run_startup_dependency_check(self) -> None:
        """主視窗顯示後驗證外部依賴並視需要顯示安裝指引"""
        ffmpeg_directory = self.settings.ffmpeg_bin_dir if self.settings.manual_ffmpeg_enabled else ""
        js_directory = self.settings.js_runtime_bin_dir if self.settings.manual_js_runtime_enabled else ""
        self.dependency_controller.check(ffmpeg_directory, js_directory)

    def _startup_dependency_report_ready(self, report: DependencyReport) -> None:
        """套用背景檢查結果並顯示缺少依賴指引"""
        self._apply_dependency_report(report)
        self._show_dependency_guide()

    def _refresh_dependency_report(self, show_guide: bool = False) -> DependencyReport:
        """重新驗證目前工具設定並同步 services 與 UI"""
        self.dependency_controller.invalidate()
        ffmpeg_directory = self.settings.ffmpeg_bin_dir if self.settings.manual_ffmpeg_enabled else ""
        js_directory = self.settings.js_runtime_bin_dir if self.settings.manual_js_runtime_enabled else ""
        report = self.dependency_inspector.inspect(ffmpeg_directory, js_directory)
        self._apply_dependency_report(report)
        if show_guide: self._show_dependency_guide()
        return report

    def _apply_dependency_report(self, report: DependencyReport) -> None:
        """讓 services 使用驗證後的工具並清除已恢復依賴的略過狀態"""
        self._dependency_report = report
        if hasattr(self.ffmpeg_service, "set_validated_tools"):
            self.ffmpeg_service.set_validated_tools(
                report.ffmpeg.path if report.ffmpeg.available else "",
                report.ffprobe.path if report.ffprobe.available else "",
            )
        if hasattr(self.media_service, "set_validated_runtimes"):
            self.media_service.set_validated_runtimes(report.valid_runtimes)
        self._check_external_tools()

        ignored = set(self.settings.ignored_missing_dependencies)
        resolved = {"ffmpeg", "js_runtime"} - report.missing_dependency_ids
        updated = ignored - resolved
        if updated != ignored:
            self.settings.ignored_missing_dependencies = sorted(updated)
            self.storage.save_settings(self.settings)
        self.settings_panel.set_dependency_reminders_ignored(bool(self.settings.ignored_missing_dependencies))

    def _show_dependency_guide(self) -> None:
        """顯示尚未略過的缺少依賴安裝說明"""
        if self._dependency_report is None: return
        ignored = set(self.settings.ignored_missing_dependencies)
        missing_ids = self._dependency_report.missing_dependency_ids - ignored
        if not missing_ids: return
        if self._dependency_dialog is not None and self._dependency_dialog.isVisible():
            self._dependency_dialog.raise_()
            self._dependency_dialog.activateWindow()
            return
        dialog = DependencyDialog(self._dependency_report, missing_ids, installation_guides(), self)
        dialog.ignored_requested.connect(self._ignore_dependency_reminders)
        self._dependency_dialog = dialog
        dialog.open()

    def _ignore_dependency_reminders(self, dependency_ids: list[str]) -> None:
        """永久略過目前選取的缺少依賴提示"""
        ignored = set(self.settings.ignored_missing_dependencies)
        ignored.update(name for name in dependency_ids if name in {"ffmpeg", "js_runtime"})
        self.settings.ignored_missing_dependencies = sorted(ignored)
        self.settings_panel.set_dependency_reminders_ignored(bool(ignored))
        self.storage.save_settings(self.settings)

    def _reset_dependency_reminders(self) -> None:
        """清除依賴提示略過狀態並重新顯示目前缺少項目"""
        self.settings.ignored_missing_dependencies = []
        self.settings_panel.set_dependency_reminders_ignored(False)
        self.storage.save_settings(self.settings)
        if self._dependency_report is None: self._refresh_dependency_report()
        self._show_dependency_guide()

    def _analyze(self, payload: dict[str, Any]) -> None:
        self.current_media = None
        self.analyze_panel.set_media(None)
        self._abort_thumbnail()
        self.analysis_controller.analyze(payload["url"], CookieConfig.from_dict(payload.get("cookie")))

    def _analysis_ready(self, media: MediaInfo) -> None:
        self.current_media = media
        self.analyze_panel.set_media(media)
        if media.thumbnail: self._load_thumbnail(media.thumbnail)

    def _analysis_failed(self, _message: str) -> None:
        self.current_media = None
        self.analyze_panel.set_media(None)
        QMessageBox.warning(self, tr("Analysis Failed"), tr("Analysis failed. Try again or check the Application Log."))

    def _analyze_subtitles(self, payload: dict[str, Any]) -> None:
        self.subtitle_panel.set_media(None)
        self.subtitle_analysis_controller.analyze(
            payload["url"],
            CookieConfig.from_dict(payload.get("cookie")),
            bool(payload.get("include_automatic_subtitles", True)),
        )

    def _subtitle_analysis_ready(self, media: MediaInfo) -> None:
        self.subtitle_panel.set_media(media)

    def _subtitle_analysis_failed(self, _message: str) -> None:
        self.subtitle_panel.set_media(None)
        QMessageBox.warning(
            self, tr("Subtitle Analysis Failed"), tr("Analysis failed. Try again or check the Application Log.")
        )

    def _load_thumbnail(self, url: str) -> None:
        self._abort_thumbnail()
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", b"Mozilla/5.0 MochiStar")
        reply = self.network.get(request)
        self._thumbnail_reply = reply
        reply.finished.connect(lambda current=reply: self._thumbnail_finished(current))

    def _thumbnail_finished(self, reply: QNetworkReply) -> None:
        if reply is not self._thumbnail_reply:
            reply.deleteLater()
            return
        self._thumbnail_reply = None
        if reply.error() == QNetworkReply.NetworkError.NoError:
            self.analyze_panel.set_thumbnail(bytes(reply.readAll()))
        else:
            logging.getLogger(__name__).warning("Thumbnail download failed: %s", reply.errorString())
        reply.deleteLater()

    def _abort_thumbnail(self) -> None:
        reply = self._thumbnail_reply
        if not reply: return
        self._thumbnail_reply = None
        reply.abort()
        reply.deleteLater()

    def _add_download(self, payload: dict[str, Any]) -> None:
        media = self.current_media
        if media is None: return
        output_dir = self._validated_output_dir(payload.get("output_dir", ""))
        if not output_dir: return
        cookie = CookieConfig.from_dict(payload.get("cookie"))
        if cookie.source == "file" and (not cookie.file_path or not Path(cookie.file_path).is_file()):
            QMessageBox.warning(self, tr("Invalid Cookie File"), tr("Choose an existing Netscape cookies.txt file"))
            return
        if media.is_playlist and not payload.get("playlist_item_ids"):
            QMessageBox.warning(self, tr("No Playlist Items"), tr("Select at least one playlist item"))
            return
        if self._download_requires_ffmpeg(payload) and not self.ffmpeg_service.availability["ffmpeg"]:
            QMessageBox.warning(
                self,
                tr("FFmpeg Required"),
                tr("This download preset requires FFmpeg. Configure it in Settings or PATH"),
            )
            return

        common_options = {
            "output_dir": output_dir,
            "preset": payload["preset"],
            "resolution": payload["resolution"],
            "video_container": payload["video_container"],
            "audio_output": payload["audio_output"],
            "video_format_id": payload.get("video_format_id") or "",
            "audio_format_id": payload.get("audio_format_id") or "",
            "cookie": cookie,
        }
        if media.is_playlist:
            entries = {entry.media_id: entry for entry in media.entries}
            selected = [entries[media_id] for media_id in payload.get("playlist_item_ids") or [] if media_id in entries]
            tasks = []
            for entry in selected:
                direct_url = entry.webpage_url.strip()
                options = DownloadOptions(
                    url=direct_url or payload["url"], playlist_item_ids=[] if direct_url else [entry.media_id],
                    **common_options,
                )
                tasks.append(TaskRecord(
                    kind=TaskKind.DOWNLOAD, title=entry.title, output_path=output_dir, download_options=options,
                ))
        else:
            options = DownloadOptions(url=payload["url"], **common_options)
            tasks = [TaskRecord(
                kind=TaskKind.DOWNLOAD, title=media.title, output_path=output_dir, download_options=options,
            )]
        if not tasks:
            QMessageBox.warning(self, tr("No Playlist Items"), tr("Select at least one playlist item"))
            return
        self.task_controller.add_tasks(tasks)
        self._remember_download_settings(tasks[0].download_options)

    def _add_subtitles(self, payload: dict[str, Any]) -> None:
        output_dir = self._validated_output_dir(payload.get("output_dir", ""))
        if not output_dir: return
        cookie = CookieConfig.from_dict(payload.get("cookie"))
        if cookie.source == "file" and (not cookie.file_path or not Path(cookie.file_path).is_file()):
            QMessageBox.warning(self, tr("Invalid Cookie File"), tr("Choose an existing Netscape cookies.txt file"))
            return
        grouped: dict[tuple[str, str, str], list[SubtitleSelection]] = {}
        fallback_url = self.subtitle_panel.url_edit.text().strip()
        for track in payload.get("tracks") or []:
            url = str(track.get("url") or fallback_url)
            key = (str(track.get("media_id") or url), str(track.get("title") or "Untitled"), url)
            grouped.setdefault(key, []).append(SubtitleSelection(
                language=str(track.get("language") or ""),
                source=str(track.get("source") or "manual"),
                format=str(track.get("format") or "best"),
            ))
        tasks = [
            TaskRecord(
                kind=TaskKind.SUBTITLE,
                title=title,
                output_path=output_dir,
                subtitle_options=SubtitleOptions(url=url, output_dir=output_dir, selections=selections, cookie=cookie),
            )
            for (_media_id, title, url), selections in grouped.items()
            if url and selections
        ]
        if not tasks: return
        self.task_controller.add_tasks(tasks)
        self.settings.include_automatic_subtitles = self.subtitle_panel.include_automatic_checkbox.isChecked()

    def _browse_conversion_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, tr("Choose Media Files"), str(Path.home()))
        if files: self.conversion_panel.add_files(files)

    def _browse_replacement_source(self, source: str) -> None:
        """選擇單一畫面或音訊來源"""
        title = "Choose Visual Source" if source == "visual" else "Choose Audio Source"
        path, _ = QFileDialog.getOpenFileName(self, tr(title), str(Path.home()), tr("All files (*)"))
        if not path: return
        card = self.replacement_panel.visual_card if source == "visual" else self.replacement_panel.audio_card
        card.set_path(path)

    def _browse_analysis_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, tr("Choose Media Files"), str(Path.home()))
        if files: self.file_analysis_panel.analyze_files(files)

    def _analyze_conversion_files(self, paths: list[str]) -> None:
        """將轉檔檔案池的選取項目送到檔案分析頁"""
        if not paths: return
        self.file_analysis_panel.analyze_files(paths)
        self.panel_stack.setCurrentWidget(self.file_analysis_panel)
        self._check_navigation_button(self.file_analysis_panel)

    @staticmethod
    def _conversion_options(payload: dict[str, Any], input_path: str = "", output_dir: str = "") -> ConversionOptions:
        """將 ConversionPanel payload 固定成 queue 可保存的完整快照"""
        return ConversionOptions(
            input_path=input_path, output_dir=output_dir,
            target_format=str(payload.get("target_format") or "mp4"),
            stream_copy=bool(payload.get("stream_copy")), encoder=str(payload.get("encoder") or ""),
            video_codec=str(payload.get("video_codec") or "auto"),
            prores_profile=str(payload.get("prores_profile") or "proxy"),
            resolution_height=payload.get("resolution_height"), allow_upscale=bool(payload.get("allow_upscale")),
            fps=str(payload.get("fps") or "source"), quality_mode=str(payload.get("quality_mode") or "vbr"),
            quality_value=payload.get("quality_value"), maximum_bitrate=payload.get("maximum_bitrate"),
            gop=payload.get("gop"),
            h264_profile=str(payload.get("h264_profile") or "auto"),
            pixel_format=str(payload.get("pixel_format") or "auto"),
            audio_codec=str(payload.get("audio_codec") or "auto"), audio_bitrate=payload.get("audio_bitrate"),
            audio_sample_rate=payload.get("audio_sample_rate"),
            acceleration=str(payload.get("acceleration") or "auto"),
        )

    @classmethod
    def _replacement_options(cls, payload: dict[str, Any], output_dir: str = "") -> ReplacementOptions:
        """將 ReplacementPanel payload 固定成 queue 可保存的完整快照"""
        return ReplacementOptions(
            visual_path=str(payload.get("visual_path") or ""), audio_path=str(payload.get("audio_path") or ""),
            duration_mode=str(payload.get("duration_mode") or "longest"),
            custom_duration=payload.get("custom_duration"), visual_loop=bool(payload.get("visual_loop")),
            audio_loop=bool(payload.get("audio_loop")), visual_delay=float(payload.get("visual_delay") or 0),
            audio_delay=float(payload.get("audio_delay") or 0), trim_start=float(payload.get("trim_start") or 0),
            trim_end=float(payload.get("trim_end") or 0), aspect_ratio=str(payload.get("aspect_ratio") or "source"),
            fit_mode=str(payload.get("fit_mode") or "contain"), force_reencode=bool(payload.get("force_reencode")),
            conversion=cls._conversion_options(payload, str(payload.get("visual_path") or ""), output_dir),
        )

    def _probe_conversion_input(self, input_path: str) -> dict[str, Any]:
        """快取未變更檔案的 ffprobe 結果, 避免調整設定時重複啟動程序"""
        path = Path(input_path)
        stat = path.stat()
        key, signature = str(path.resolve()), (stat.st_mtime_ns, stat.st_size)
        cached = self._conversion_probe_cache.get(key)
        if cached and cached[0] == signature: return cached[1]
        probe = self.ffmpeg_service.probe(path)
        self._conversion_probe_cache[key] = (signature, probe)
        return probe

    def _shared_presets_changed(self, source: ConversionPanel, presets: list[Any]) -> None:
        """同步共用 preset catalog, 不套用另一個 panel 的表單值"""
        self.settings.conversion_presets = list(presets)
        target = self.replacement_panel if source is self.conversion_panel else self.conversion_panel
        target.refresh_presets(presets)

    def _validate_conversion(self, payload: dict[str, Any]) -> None:
        self.conversion_panel.set_request_error("")
        paths = payload.get("input_paths") or []
        if not paths: return
        options = self._conversion_options(payload)
        error = self.ffmpeg_service.validate_options(options)
        if error:
            self.conversion_panel.set_request_error(tr(error))
            return
        if not self.ffmpeg_service.availability["ffprobe"]:
            self.conversion_panel.set_request_error(tr("ffprobe is unavailable. Configure FFmpeg in Settings or PATH"))
            return
        required_stream = self.ffmpeg_service.required_stream_type(payload["target_format"])
        problems = []
        for input_path in paths:
            if not Path(input_path).is_file():
                problems.append(tr("Input file does not exist: {path}", path=input_path))
                continue
            try:
                probe = self._probe_conversion_input(input_path)
                if required_stream and not self.ffmpeg_service.has_media_stream(probe, required_stream):
                    problems.append(
                        f"{Path(input_path).name}: {tr(self.ffmpeg_service.MISSING_STREAM_ERRORS[required_stream])}"
                    )
                    continue
                if payload.get("audio_codec") == "copy" and not payload.get("stream_copy"):
                    compatible, reason = self.ffmpeg_service.validate_audio_copy(probe, payload["target_format"])
                elif payload.get("stream_copy"):
                    compatible, reason = self.ffmpeg_service.validate_stream_copy(probe, payload["target_format"])
                else:
                    compatible, reason = True, ""
            except Exception as error:
                problems.append(f"{Path(input_path).name}: {error}")
                continue
            if not compatible:
                problems.append(f"{Path(input_path).name}: {tr(reason)}")
        if problems:
            self.conversion_panel.set_request_error(
                f"{tr('Some files are incompatible with the selected output:')}\n" + "\n".join(problems)
            )

    def _add_conversion(self, payload: dict[str, Any]) -> None:
        output_dir = self._validated_output_dir(payload.get("output_dir", ""))
        if not output_dir: return
        self._validate_conversion(payload)
        if self.conversion_panel.validation_label.text(): return

        tasks = []
        for input_path in payload.get("input_paths") or []:
            if not Path(input_path).is_file():
                QMessageBox.warning(
                    self,
                    tr("Missing Input"),
                    tr("Input file does not exist:\n{path}", path=input_path),
                )
                return
            options = self._conversion_options(payload, input_path, output_dir)
            tasks.append(
                TaskRecord(
                    kind=TaskKind.CONVERSION,
                    title=Path(input_path).name,
                    output_path=output_dir,
                    conversion_options=options,
                )
            )
        if not tasks: return
        self.task_controller.add_tasks(tasks)
        self.settings.last_conversion_output_dir = output_dir
        self.settings.last_conversion_format = payload["target_format"]
        self.settings.last_conversion_mode = "remux" if payload["stream_copy"] else "encode"
        self.settings.last_conversion_acceleration = payload.get("acceleration") or "auto"
        self.settings.last_conversion_preset_id = self.conversion_panel.current_preset_id()
        self.settings.last_conversion_type = payload.get("media_type") or "video"
        self.settings.conversion_presets = self.conversion_panel.custom_presets()
        self.settings.conversion_splitter_sizes = self.conversion_panel.splitter_sizes()
        self.conversion_panel.clear_files()

    def _validate_replacement(self, payload: dict[str, Any]) -> None:
        """使用已完成的 FFprobe 結果驗證替換設定"""
        self.replacement_panel.set_request_error("")
        self.replacement_panel.set_processing_summary("")
        visual_path, audio_path = str(payload.get("visual_path") or ""), str(payload.get("audio_path") or "")
        if not visual_path or not audio_path: return
        probes = self.replacement_panel.source_probes()
        source_errors = self.replacement_panel.source_errors()
        if visual_path in source_errors or audio_path in source_errors:
            self.replacement_panel.set_request_error(source_errors.get(visual_path) or source_errors.get(audio_path) or "")
            return
        if visual_path not in probes or audio_path not in probes: return
        options = self._replacement_options(payload, payload.get("output_dir") or "")
        error = self.ffmpeg_service.validate_replacement(options, probes[visual_path], probes[audio_path])
        if error:
            self.replacement_panel.set_request_error(tr(error))
            return
        _copy_video, _copy_audio, summary = self.ffmpeg_service.replacement_actions(
            options, probes[visual_path], probes[audio_path]
        )
        match = re.fullmatch(r"Video: (.+); Audio: (.+); Duration: ([0-9.]+)s", summary)
        if match:
            summary = tr(
                "Video: {video}; Audio: {audio}; Duration: {duration}s",
                video=tr(match.group(1)), audio=tr(match.group(2)), duration=match.group(3),
            )
        self.replacement_panel.set_processing_summary(summary)

    def _add_replacement(self, payload: dict[str, Any]) -> None:
        """建立一筆畫面與音訊合成 queue task"""
        output_dir = self._validated_output_dir(payload.get("output_dir", ""))
        if not output_dir: return
        self._validate_replacement(payload)
        if self.replacement_panel.validation_label.text(): return
        visual_path, audio_path = str(payload.get("visual_path") or ""), str(payload.get("audio_path") or "")
        if not Path(visual_path).is_file() or not Path(audio_path).is_file():
            QMessageBox.warning(self, tr("Missing Input"), tr("Choose existing visual and audio source files"))
            return
        options = self._replacement_options(payload, output_dir)
        self.task_controller.add_tasks([TaskRecord(
            kind=TaskKind.REPLACEMENT, title=f"{Path(visual_path).name} + {Path(audio_path).name}",
            output_path=output_dir, replacement_options=options,
        )])

    def _browse_output(self, line_edit: Any) -> None:
        initial = line_edit.text().strip() or self.settings.output_dir
        directory = QFileDialog.getExistingDirectory(self, tr("Choose Output Folder"), initial)
        if directory: line_edit.setText(directory)

    def _browse_cookie_file(self, target: Any | None = None) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Choose Netscape Cookie File"), str(Path.home()), tr("Text files (*.txt);;All files (*)")
        )
        if path: (target or self.analyze_panel.cookie_file_edit).setText(path)

    def _browse_tool_directory(self, target: Any) -> None:
        initial = target.text().strip() or str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, tr("Choose Tool Bin Directory"), initial)
        if directory: target.setText(directory)

    def _apply_tool_settings(self, payload: dict[str, Any]) -> None:
        self.dependency_controller.invalidate()
        ffmpeg_directory = str(payload.get("ffmpeg_bin_dir") or "")
        js_directory = str(payload.get("js_runtime_bin_dir") or "")
        active_ffmpeg = ffmpeg_directory if payload.get("manual_ffmpeg_enabled") else ""
        active_js = js_directory if payload.get("manual_js_runtime_enabled") else ""
        report = self.dependency_inspector.inspect(active_ffmpeg, active_js)
        if payload.get("manual_ffmpeg_enabled"):
            missing = [status.name for status in (report.ffmpeg, report.ffprobe) if not status.available]
            if missing:
                QMessageBox.warning(
                    self,
                    tr("Invalid FFmpeg Directory"),
                    tr("The selected directory does not contain: {missing}", missing=", ".join(missing)),
                )
                return
        if payload.get("manual_js_runtime_enabled"):
            if not report.js_runtime_available:
                QMessageBox.warning(
                    self,
                    tr("Invalid JavaScript Runtime Directory"),
                    tr("The selected directory does not contain a supported deno, node, qjs, or bun version"),
                )
                return
        self.settings.manual_ffmpeg_enabled = bool(payload.get("manual_ffmpeg_enabled"))
        self.settings.ffmpeg_bin_dir = ffmpeg_directory
        self.settings.manual_js_runtime_enabled = bool(payload.get("manual_js_runtime_enabled"))
        self.settings.js_runtime_bin_dir = js_directory
        self.ffmpeg_service.configure_tools(active_ffmpeg)
        self.media_service.configure_tools(active_ffmpeg, active_js)
        self._apply_dependency_report(report)

    def _change_theme(self, theme_name: str) -> None:
        app = QApplication.instance()
        if app is None: return
        previous = self.settings.theme_name
        try:
            apply_theme(app, theme_name)
        except ThemeError as error:
            QMessageBox.warning(self, tr("Unable to Apply Theme"), str(error))
            index = self.settings_panel.theme_combo.findData(previous)
            if index >= 0: self.settings_panel.theme_combo.setCurrentIndex(index)
            return
        self.settings.theme_name = theme_name
        self.log_panel.refresh_colors()
        self._window_border_overlay.update()

    def _apply_custom_title_bar(self, enabled: bool) -> None:
        """啟動時選擇原生 frame 或自製 Title Bar"""
        enabled = bool(enabled)
        self._custom_title_bar_active = enabled
        self.top_bar.set_custom_enabled(enabled)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setProperty("customTitleBar", enabled)
        self.root.setProperty("customTitleBar", enabled)
        self.root.style().unpolish(self.root)
        self.root.style().polish(self.root)
        frameless_flag = Qt.WindowType.FramelessWindowHint
        flags = self.windowFlags()
        already_applied = bool(flags & frameless_flag) == enabled
        if not already_applied:
            was_visible, state = self.isVisible(), self.windowState()
            self.setWindowFlags(flags | frameless_flag if enabled else flags & ~frameless_flag)
            if was_visible:
                if state & Qt.WindowState.WindowFullScreen: self.showFullScreen()
                elif state & Qt.WindowState.WindowMaximized: self.showMaximized()
                elif state & Qt.WindowState.WindowMinimized: self.showMinimized()
                else: self.show()
        self._native_window_corners = enabled and self._set_windows_corner_preference(2)
        if not enabled: self._set_windows_corner_preference(0)
        self._update_resize_handles()

    def _set_windows_corner_preference(self, preference: int) -> bool:
        """在 Windows 11 使用 DWM 繪製平滑圓角, 不支援時交回 Qt mask"""
        if sys.platform == "win32": return False # 強制使用 Qt mask fallback, 測試後註解這行
        if sys.platform != "win32": return False
        try:
            from ctypes import wintypes

            function = ctypes.windll.dwmapi.DwmSetWindowAttribute
            function.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
            function.restype = ctypes.c_long
            value = ctypes.c_int(preference)
            result = function(
                wintypes.HWND(int(self.winId())), 33, ctypes.byref(value), ctypes.sizeof(value)
            )
            return result == 0
        except (AttributeError, OSError, ValueError):
            return False

    def _set_custom_title_bar_preference(self, enabled: bool) -> None:
        """保存下次啟動時套用的 Title Bar 模式"""
        self.settings.experimental_custom_title_bar = bool(enabled)

    def _change_language(self, language: str) -> None:
        """立即切換 UI 語言並更新記憶體 settings"""
        set_language(language)
        self.settings.language = language
        self.analyze_panel.set_analyzing(self.analyze_panel._analyzing)
        self.subtitle_panel.set_analyzing(self.subtitle_panel._analyzing)
        self.analyze_panel.set_media(self.current_media)
        self.subtitle_panel.set_media(self.subtitle_panel.media)
        self.queue_panel.set_dispatch_paused(self.task_controller.dispatch_paused)
        self._check_external_tools()
        translate_widget_tree(self)
        self.file_analysis_panel.retranslate_reports()
        self.conversion_panel.refresh_preset_language()
        self.replacement_panel.refresh_preset_language()
        self.settings_panel.retranslate_update_info(DISPLAY_VERSION)
        self.queue_panel.refresh_summary()
        self.bottom_status.refresh()

    def _restore_factory_settings(self) -> None:
        """確認後立即套用原廠設定, 退出時再保存"""
        answer = QMessageBox.question(
            self,
            tr("Restore Factory Settings?"),
            tr("All application preferences will be reset. Queue tasks and downloaded files will not be deleted."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes: return

        self.settings = Settings()
        set_language(self.settings.language)
        app = QApplication.instance()
        if app is not None:
            try:
                apply_theme(app, self.settings.theme_name)
            except ThemeError as error:
                QMessageBox.warning(self, tr("Unable to Apply Theme"), str(error))
        if hasattr(self.media_service, "configure_tools"): self.media_service.configure_tools("", "")
        if hasattr(self.ffmpeg_service, "configure_tools"): self.ffmpeg_service.configure_tools("")
        self.task_controller.set_worker_count(self.settings.worker_count)
        self._restore_settings()
        self.analyze_panel.set_analyzing(False)
        self.subtitle_panel.set_analyzing(False)
        self.queue_panel.set_column_widths(self.settings.queue_column_widths)
        self._refresh_dependency_report(show_guide=True)
        translate_widget_tree(self)
        self._set_initial_update_status()
        self.queue_panel.refresh_summary()
        self.bottom_status.refresh()

    def _set_initial_update_status(self) -> None:
        source = "Ready to check for updates" if self._updates_configured else "Update service is not configured"
        self.settings_panel.set_update_info(DISPLAY_VERSION, source)

    def _set_auto_check_updates(self, enabled: bool) -> None:
        self.settings.auto_check_updates = enabled

    def _change_worker_count(self, count: int) -> None:
        """即時套用 worker 數量, 退出時再保存"""
        self.settings.worker_count = count
        self.task_controller.set_worker_count(count)

    def _start_automatic_update_check(self) -> None:
        if not self.settings.auto_check_updates or not self._updates_configured: return
        try:
            checked_at = datetime.fromisoformat(self.settings.last_update_check_at)
            if checked_at.tzinfo is None: checked_at = checked_at.replace(tzinfo=UTC)
            if datetime.now(UTC) - checked_at.astimezone(UTC) < timedelta(hours=24): return
        except (TypeError, ValueError):
            pass
        self._check_for_updates(False)

    def _check_for_updates(self, manual: bool) -> None:
        if not self._updates_configured:
            self.settings_panel.set_update_info(DISPLAY_VERSION, "Update service is not configured")
            if manual:
                QMessageBox.information(
                    self,
                    tr("Application Updates"),
                    tr("Update service is not configured"),
                )
            return
        self.update_controller.check(manual)

    def _update_check_started(self, _manual: bool) -> None:
        self.settings_panel.check_updates_button.setEnabled(False)
        self.settings_panel.set_update_info(DISPLAY_VERSION, "Checking for updates...")

    def _update_check_succeeded(self, result: UpdateCheckResult, manual: bool) -> None:
        self.settings_panel.check_updates_button.setEnabled(True)
        self.settings.last_update_check_at = datetime.now(UTC).isoformat()
        if result.status is UpdateCheckStatus.AVAILABLE and result.release is not None:
            self.settings_panel.set_update_info(
                DISPLAY_VERSION, "Update available: {version}", version=str(result.release.version),
            )
            self._show_available_update(result.release)
            return
        source = {
            UpdateCheckStatus.NO_STABLE_RELEASE: "No stable release is available yet",
            UpdateCheckStatus.TEST_BUILD_AHEAD: "This test build is newer than the latest stable release",
        }.get(result.status, "Application is up to date")
        self.settings_panel.set_update_info(DISPLAY_VERSION, source)
        if manual: QMessageBox.information(self, tr("Application Updates"), tr(source))

    def _update_check_failed(self, error: str, manual: bool) -> None:
        self.settings_panel.check_updates_button.setEnabled(True)
        self.settings_panel.set_update_info(DISPLAY_VERSION, "Update check failed: {error}", error=error)
        if manual: QMessageBox.warning(self, tr("Update Check Failed"), error)

    def _show_available_update(self, release: UpdateRelease) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle(tr("Update Available"))
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setText(tr("Version {version} is available", version=str(release.version)))
        notes = release.notes.strip()[:4000] or tr("No release notes were provided")
        if release.asset is None:
            notes = f"{tr('A verified update asset is unavailable. Use the Release page to download manually.')}\n\n{notes}"
        dialog.setInformativeText(notes)
        download_button = (
            dialog.addButton(tr("Download Update File"), QMessageBox.ButtonRole.AcceptRole)
            if release.asset is not None else None
        )
        release_button = (
            dialog.addButton(tr("Open Release Page"), QMessageBox.ButtonRole.ActionRole)
            if release.page_url else None
        )
        dialog.addButton(tr("Remind Me Later"), QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        clicked_button = dialog.clickedButton()
        if download_button is not None and clicked_button is download_button:
            self.update_controller.download(release, self.storage.app_dir / "updates")
        elif release_button is not None and clicked_button is release_button:
            QDesktopServices.openUrl(QUrl(release.page_url))

    def _update_download_started(self, release: UpdateRelease) -> None:
        self._downloading_release = release
        self.settings_panel.set_update_info(
            DISPLAY_VERSION, "Downloading version {version}...", version=str(release.version),
        )
        dialog = create_update_progress_dialog(self)
        dialog.canceled.connect(self.update_controller.cancel_download)
        dialog.show()
        self._update_progress_dialog = dialog

    def _update_download_progress(self, received: int, total: int) -> None:
        if self._update_progress_dialog:
            update_progress_dialog(self._update_progress_dialog, received, total)

    def _update_download_succeeded(self, update: DownloadedUpdate) -> None:
        self._close_update_progress()
        self._downloading_release = None
        self.settings_panel.set_update_info(
            DISPLAY_VERSION,
            "Update downloaded: {version}",
            version=str(update.release.version),
        )
        dialog = QMessageBox(self)
        dialog.setWindowTitle(tr("Update File Downloaded"))
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setText(tr("The update file was downloaded and verified."))
        dialog.setInformativeText(tr(manual_update_instructions(self.update_controller.platform_key)))
        open_button = dialog.addButton(tr("Open Download Folder"), QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton(tr("Later"), QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        if dialog.clickedButton() is open_button: self.update_installer.install(update)

    def _update_download_failed(self, error: str) -> None:
        self._close_update_progress()
        release = self._downloading_release
        self._downloading_release = None
        self.settings_panel.set_update_info(DISPLAY_VERSION, "Update download failed: {error}", error=error)
        dialog = QMessageBox(self)
        dialog.setWindowTitle(tr("Update File Download Failed"))
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(error)
        release_button = (
            dialog.addButton(tr("Open Release Page"), QMessageBox.ButtonRole.ActionRole)
            if release is not None and release.page_url else None
        )
        dialog.addButton(QMessageBox.StandardButton.Close)
        dialog.exec()
        if release_button is not None and dialog.clickedButton() is release_button:
            QDesktopServices.openUrl(QUrl(release.page_url))

    def _update_download_cancelled(self) -> None:
        self._close_update_progress()
        self._downloading_release = None
        self.settings_panel.set_update_info(DISPLAY_VERSION, "Update download cancelled")

    def _close_update_progress(self) -> None:
        if not self._update_progress_dialog: return
        self._update_progress_dialog.close()
        self._update_progress_dialog.deleteLater()
        self._update_progress_dialog = None

    @staticmethod
    def _open_directory(path: Path) -> bool:
        path.mkdir(parents=True, exist_ok=True)
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def _open_application_data_folder(self) -> None:
        """建立並交由系統檔案管理器開啟應用程式資料夾"""
        try:
            opened = self._open_directory(self.storage.app_dir)
        except OSError as error:
            QMessageBox.warning(self, tr("Unable to Open Application Data Folder"), str(error))
            return
        if not opened:
            QMessageBox.warning(
                self,
                tr("Unable to Open Application Data Folder"),
                str(self.storage.app_dir.resolve()),
            )

    def _open_task_output_folder(self, directory: str) -> None:
        """交由各系統的檔案管理器開啟 task 輸出資料夾"""
        path = Path(directory).expanduser()
        try:
            opened = self._open_directory(path)
        except OSError as error:
            QMessageBox.warning(self, tr("Unable to Open Output Folder"), str(error))
            return
        if not opened: QMessageBox.warning(self, tr("Unable to Open Output Folder"), str(path.resolve()))

    def _validated_output_dir(self, value: str) -> str:
        if not value.strip():
            QMessageBox.warning(self, tr("Output Folder Required"), tr("Choose an output folder"))
            return ""
        path = Path(value).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            QMessageBox.warning(self, tr("Invalid Output Folder"), str(error))
            return ""
        return str(path.resolve())

    def _remember_download_settings(self, options: DownloadOptions) -> None:
        self.settings.output_dir = options.output_dir
        self.settings.cookie = options.cookie
        self.settings.last_preset = options.preset
        self.settings.last_resolution = options.resolution
        self.settings.last_video_container = options.video_container
        self.settings.last_audio_output = options.audio_output

    @staticmethod
    def _download_requires_ffmpeg(payload: dict[str, Any]) -> bool:
        preset = payload.get("preset")
        if preset == "audio_only": return payload.get("audio_output") not in {"", "original", "auto"}
        if payload.get("video_container") not in {"", "auto", "original"}: return True
        return preset == "best_video_audio" and not payload.get("video_format_id")

    @staticmethod
    def _set_combo(combo: Any, value: Any) -> None:
        index = combo.findData(value)
        if index >= 0: combo.setCurrentIndex(index)

    def _check_navigation_button(self, widget: QWidget) -> None:
        index = self.panel_stack.indexOf(widget)
        button = self._navigation_buttons.button(index)
        if button: button.setChecked(True)

    def _update_resize_handles(self) -> None:
        """更新 frameless window resize hit area"""
        enabled = self._custom_title_bar_active and not self.isMaximized() and not self.isFullScreen()
        self._window_border_overlay.setVisible(enabled)
        self._window_border_overlay.setGeometry(self.rect())
        self._window_border_overlay.raise_()
        for handle in self._resize_handles.values(): handle.setVisible(enabled)
        self._update_window_mask()
        if not enabled: return

        width, height, edge, corner = self.width(), self.height(), 5, 10
        geometries = {
            Qt.Edge.TopEdge: QRect(corner, 0, width - corner * 2, edge),
            Qt.Edge.BottomEdge: QRect(corner, height - edge, width - corner * 2, edge),
            Qt.Edge.LeftEdge: QRect(0, corner, edge, height - corner * 2),
            Qt.Edge.RightEdge: QRect(width - edge, corner, edge, height - corner * 2),
            Qt.Edge.TopEdge | Qt.Edge.LeftEdge: QRect(0, 0, corner, corner),
            Qt.Edge.BottomEdge | Qt.Edge.RightEdge: QRect(width - corner, height - corner, corner, corner),
            Qt.Edge.TopEdge | Qt.Edge.RightEdge: QRect(width - corner, 0, corner, corner),
            Qt.Edge.BottomEdge | Qt.Edge.LeftEdge: QRect(0, height - corner, corner, corner),
        }
        for edges, geometry in geometries.items():
            self._resize_handles[edges].setGeometry(geometry)
            self._resize_handles[edges].raise_()

    def _update_window_mask(self) -> None:
        """在一般 frameless 狀態裁出圓角, 最大化時維持完整矩形"""
        if self._native_window_corners or not self._custom_title_bar_active or self.isMaximized() or self.isFullScreen():
            self.clearMask()
            return
        self.setMask(_rounded_rect_region(self.rect(), WINDOW_CORNER_RADIUS))

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() != QEvent.Type.WindowStateChange: return
        self.top_bar.window_state_changed(self.windowState())
        self._update_resize_handles()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_resize_handles()

    def showEvent(self, event: QShowEvent) -> None:
        """顯示或重建 native window 後同步 resize handles"""
        super().showEvent(event)
        if self._custom_title_bar_active:
            self._native_window_corners = self._set_windows_corner_preference(2)
        self._update_resize_handles()

    def closeEvent(self, event: QCloseEvent) -> None:
        """保存 UI 狀態並停止背景工作"""
        self._abort_thumbnail()
        self.settings.worker_count = int(self.queue_panel.concurrency_combo.currentData())
        self.settings.include_automatic_subtitles = self.subtitle_panel.include_automatic_checkbox.isChecked()
        self.settings.last_preset = self.analyze_panel.preset_combo.currentData() or "best_video_audio"
        self.settings.last_resolution = self.analyze_panel.resolution_combo.currentData() or "best"
        self.settings.last_video_container = self.analyze_panel.container_combo.currentData() or "auto"
        self.settings.last_audio_output = self.analyze_panel.audio_output_combo.currentData() or "original"
        self.settings.download_column_widths = self.analyze_panel.column_widths()
        self.settings.subtitle_column_widths = self.subtitle_panel.column_widths()
        self.settings.queue_column_widths = self.queue_panel.column_widths()
        self.settings.output_dir = self.analyze_panel.output_directory_edit.text().strip() or self.settings.output_dir
        self.settings.last_conversion_output_dir = (
            self.conversion_panel.output_directory_edit.text().strip() or self.settings.last_conversion_output_dir
        )
        conversion_payload = self.conversion_panel.request_payload()
        self.settings.last_conversion_format = str(conversion_payload["target_format"])
        self.settings.last_conversion_mode = "remux" if conversion_payload["stream_copy"] else "encode"
        self.settings.last_conversion_acceleration = self.conversion_panel.encoder_combo.currentData() or "auto"
        self.settings.last_conversion_preset_id = self.conversion_panel.current_preset_id()
        self.settings.last_conversion_type = conversion_payload["media_type"]
        self.settings.conversion_presets = self.conversion_panel.custom_presets()
        self.settings.conversion_splitter_sizes = self.conversion_panel.splitter_sizes()
        self.settings.replacement_settings = self.replacement_panel.persistent_settings()
        self.settings.replacement_splitter_sizes = self.replacement_panel.splitter_sizes()
        self.settings.cookie = CookieConfig.from_dict(self.analyze_panel.cookie_payload())
        self.settings.geometry = bytes(self.saveGeometry())
        self.settings.window_state = bytes(self.saveState())
        self.storage.save_settings(self.settings)
        self.analysis_controller.shutdown()
        self.subtitle_analysis_controller.shutdown()
        self.file_analysis_controller.shutdown()
        self.update_controller.shutdown()
        self.dependency_controller.shutdown()
        self.task_controller.shutdown()
        event.accept()
