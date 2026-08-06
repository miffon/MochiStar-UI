from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from analysis_controller import AnalysisController
from controller import TaskController, YOUTUBE_BLOCKED_ERROR, summarize_task_error
from external_tools import DependencyReport, ToolStatus
from ffmpeg_service import FFmpegService
from file_analysis_controller import FileAnalysisController
from media_service import ServiceCancelled
from models import (
    CookieConfig, ConversionOptions, ConversionPreset, DownloadOptions, MediaInfo,
    ReplacementOptions, TaskRecord, TaskStatus,
)
from panels import BottomStatusBar
from storage import AppStorage, Settings

TEST_PATH = Path("test-data")


def wait_until(app: QApplication, predicate, timeout: float = 3.0) -> None:
    """處理 Qt event 直到條件成立"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate(): return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for Qt event")


def test_rounded_rect_region_uses_scanline_circle() -> None:
    import window as window_module

    region = window_module._rounded_rect_region(window_module.QRect(0, 0, 100, 80), 12)
    widths = [region.intersected(window_module.QRegion(window_module.QRect(0, row, 100, 1))).boundingRect().width() for row in range(12)]

    assert widths == sorted(widths)
    assert widths[0] < widths[1] < widths[3] < widths[-1]
    assert region.boundingRect() == window_module.QRect(0, 0, 100, 80)
    assert window_module.WINDOW_CORNER_RADIUS == 8
    assert window_module.WINDOW_BORDER_RADIUS == 6


class FakeFFmpegService:
    def execute_conversion(self, task, progress_cb, log_cb, cancel_event):
        progress_cb(1.0, "Converted")
        return str(Path(task.conversion_options.output_dir) / "converted.mp4")

    def execute_replacement(self, task, progress_cb, log_cb, cancel_event):
        progress_cb(1.0, "Replaced")
        return str(Path(task.replacement_options.conversion.output_dir) / "replaced.mp4")


class FakeDownloadService:
    def __init__(self):
        self.active = 0
        self.maximum_active = 0
        self.release = threading.Event()
        self.wait_for_release = False

    def execute_download(self, task, progress_cb, log_cb, cancel_event):
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            progress_cb(0.5, "Downloading")
            while self.wait_for_release and not self.release.wait(0.01):
                if cancel_event.is_set(): raise ServiceCancelled()
            if cancel_event.is_set(): raise ServiceCancelled()
            progress_cb(1.0, "Completed")
            return str(Path(task.download_options.output_dir) / f"{task.title}.mp4")
        finally:
            self.active -= 1


class MemoryTaskStorage:
    """提供 controller 測試使用的記憶體 queue storage"""

    def __init__(self):
        self.tasks: list[TaskRecord] = []

    def load_tasks(self) -> list[TaskRecord]:
        return list(self.tasks)

    def save_tasks(self, tasks) -> bool:
        self.tasks = [task for task in tasks if task.status is not TaskStatus.COMPLETED]
        return True


def make_task(tmp_path: Path, title: str) -> TaskRecord:
    return TaskRecord(
        title=title,
        download_options=DownloadOptions(url=f"https://example.test/{title}", output_dir=str(tmp_path)),
    )


def test_summarize_task_error_recognizes_youtube_bot_check() -> None:
    error = RuntimeError(
        "ERROR: [youtube] video-id: Sign in to confirm you’re not a bot. "
        "Use --cookies-from-browser or --cookies for the authentication."
    )

    assert summarize_task_error(error) == YOUTUBE_BLOCKED_ERROR


def test_summarize_task_error_cleans_and_limits_unknown_errors() -> None:
    error = RuntimeError(f"ERROR: first line\n{'x' * 400}")

    summary = summarize_task_error(error)

    assert summary.startswith("first line ")
    assert summary.endswith("...")
    assert len(summary) == 300


def test_controller_summarizes_youtube_block_but_keeps_full_log(app, caplog) -> None:
    raw_error = (
        "ERROR: [youtube] video-id: Sign in to confirm you're not a bot. "
        "Use --cookies-from-browser or --cookies for the authentication."
    )

    class BlockedDownloadService:
        def execute_download(self, task, progress_cb, log_cb, cancel_event):
            raise RuntimeError(raw_error)

    task = make_task(TEST_PATH, "blocked")
    controller = TaskController(MemoryTaskStorage(), BlockedDownloadService(), FakeFFmpegService())
    controller.add_tasks([task])
    controller.start_queue()

    wait_until(app, lambda: task.status is TaskStatus.FAILED)

    assert task.error == YOUTUBE_BLOCKED_ERROR
    assert raw_error in caplog.text
    controller.shutdown()


def test_analysis_controller_ignores_stale_result(app):
    class AnalysisService:
        def __init__(self):
            self.release_first = threading.Event()
            self.first_done = threading.Event()

        def analyze(self, url, cookie, cancel_event, progress, include_automatic_subtitles):
            assert include_automatic_subtitles
            progress(1, 2)
            if url.endswith("first"):
                self.release_first.wait(2)
                self.first_done.set()
            return MediaInfo(media_id=url.rsplit("/", 1)[-1], title=url)

    service = AnalysisService()
    controller = AnalysisController(service)
    results = []
    progress = []
    controller.analysis_ready.connect(results.append)
    controller.progress_changed.connect(lambda current, total: progress.append((current, total)))
    controller.analyze("https://example.test/first", CookieConfig())
    controller.analyze("https://example.test/second", CookieConfig())
    wait_until(app, lambda: len(results) == 1)
    assert results[0].media_id == "second"
    assert (1, 2) in progress
    service.release_first.set()
    wait_until(app, service.first_done.is_set)
    for _ in range(3): app.processEvents()
    assert len(results) == 1
    controller.shutdown()


def test_file_analysis_controller_reports_each_file_without_blocking(app):
    class ProbeService:
        def probe(self, path):
            return {"format": {"filename": str(path)}, "streams": []}

    controller = FileAnalysisController(ProbeService())
    results = []
    controller.analysis_finished.connect(lambda path, probe, error: results.append((path, probe, error)))
    paths = [str(TEST_PATH / "one.mp4"), str(TEST_PATH / "two.wav")]

    controller.analyze(paths)
    wait_until(app, lambda: len(results) == 2)

    assert {result[0] for result in results} == set(paths)
    assert all(result[1]["streams"] == [] and not result[2] for result in results)
    controller.shutdown()


def test_file_analysis_controller_defers_gop_scan_until_requested(app) -> None:
    class ProbeService:
        def __init__(self): self.calls = []

        def probe(self, path):
            self.calls.append(("metadata", str(path)))
            return {"duration": 10, "streams": [{"codec_type": "video", "codec_name": "h264"}]}

        def analyze_gop(self, path, probe):
            self.calls.append(("gop", str(path)))
            return {**probe, "gop_analysis": {"value": 60, "minimum": 60, "maximum": 60}}

    path = str(TEST_PATH / "clip.mp4")
    service = ProbeService()
    controller = FileAnalysisController(service)
    results = []
    controller.analysis_finished.connect(lambda _path, probe, _error: results.append(probe))

    controller.analyze([path])
    wait_until(app, lambda: len(results) == 1)
    assert service.calls == [("metadata", path)]
    assert "gop_analysis" not in results[0]

    controller.analyze_gop(path)
    wait_until(app, lambda: len(results) == 2)
    assert service.calls == [("metadata", path), ("gop", path)]
    assert results[1]["gop_analysis"]["value"] == 60
    controller.shutdown()


def test_analysis_controller_cancel_immediately_invalidates_result(app):
    class BlockingService:
        def __init__(self): self.release = threading.Event()

        def analyze(self, _url, _cookie, _cancel_event, progress, include_automatic_subtitles):
            assert not include_automatic_subtitles
            progress(3, 300)
            self.release.wait(2)
            return MediaInfo(title="Stale")

    service = BlockingService()
    controller = AnalysisController(service)
    busy, results, progress = [], [], []
    controller.busy_changed.connect(busy.append)
    controller.analysis_ready.connect(results.append)
    controller.progress_changed.connect(lambda current, total: progress.append((current, total)))
    controller.analyze("https://example.test/playlist", CookieConfig(), False)
    wait_until(app, lambda: (3, 300) in progress)
    controller.cancel()
    assert busy[-1] is False
    service.release.set()
    for _ in range(3): app.processEvents()
    assert results == []
    controller.shutdown()


def test_controller_dispatches_with_limit_and_completes(app):
    service = FakeDownloadService()
    service.wait_for_release = True
    controller = TaskController(MemoryTaskStorage(), service, FakeFFmpegService(), worker_count=2)
    tasks = [make_task(TEST_PATH, str(index)) for index in range(3)]
    controller.add_tasks(tasks)
    controller.start_queue()
    wait_until(app, lambda: sum(task.status is TaskStatus.RUNNING for task in tasks) == 2)
    assert service.maximum_active == 2

    service.release.set()
    wait_until(app, lambda: all(task.status is TaskStatus.COMPLETED for task in tasks))
    assert service.maximum_active == 2
    assert controller.storage.load_tasks() == []
    controller.shutdown()


def test_controller_starts_persisted_pending_tasks_on_publish(app, tmp_path: Path):
    storage = AppStorage(tmp_path / "data")
    task = make_task(tmp_path, "persisted")
    storage.save_tasks([task])
    controller = TaskController(storage, FakeDownloadService(), FakeFFmpegService())

    controller.publish_initial_state()

    wait_until(app, lambda: controller.tasks[0].status is TaskStatus.COMPLETED)
    assert not controller.dispatch_paused
    controller.shutdown()


def test_controller_dispatches_replacement_task(app) -> None:
    task = TaskRecord(replacement_options=ReplacementOptions(
        visual_path="visual.mp4", audio_path="audio.wav",
        conversion=ConversionOptions(output_dir=str(TEST_PATH)),
    ))
    controller = TaskController(MemoryTaskStorage(), FakeDownloadService(), FakeFFmpegService())
    controller.add_tasks([task])
    controller.start_queue()

    wait_until(app, lambda: task.status is TaskStatus.COMPLETED)

    assert task.output_path.endswith("replaced.mp4")
    controller.shutdown()


def test_controller_reports_worker_limit_changes(app):
    controller = TaskController(MemoryTaskStorage(), FakeDownloadService(), FakeFFmpegService())
    worker_limits = []
    controller.worker_count_changed.connect(worker_limits.append)
    controller.set_worker_count(3)
    assert worker_limits == [3]
    controller.shutdown()


def test_lower_worker_limit_keeps_running_status_rows_until_tasks_finish(app):
    class ControlledService:
        def __init__(self):
            self.started = []
            self.releases = {name: threading.Event() for name in ("first", "second", "third")}

        def execute_download(self, task, progress_cb, _log_cb, cancel_event):
            self.started.append(task.title)
            progress_cb(0.25, "Downloading")
            while not self.releases[task.title].wait(0.01):
                if cancel_event.is_set(): raise ServiceCancelled()
            return str(TEST_PATH / f"{task.title}.mp4")

    service = ControlledService()
    controller = TaskController(MemoryTaskStorage(), service, FakeFFmpegService(), worker_count=2)
    status = BottomStatusBar()
    controller.tasks_changed.connect(status.set_tasks)
    controller.task_updated.connect(status.update_task)
    controller.worker_count_changed.connect(status.refresh)
    tasks = [make_task(TEST_PATH, name) for name in ("first", "second", "third")]
    controller.add_tasks(tasks)
    wait_until(app, lambda: len(service.started) == 2)
    assert len(status.activity_rows()) == 2

    controller.set_worker_count(1)
    assert len(status.activity_rows()) == 2
    service.releases["first"].set()
    wait_until(app, lambda: tasks[0].status is TaskStatus.COMPLETED)
    assert "third" not in service.started
    assert len(status.activity_rows()) == 1

    service.releases["second"].set()
    wait_until(app, lambda: "third" in service.started)
    assert len(status.activity_rows()) == 1
    service.releases["third"].set()
    wait_until(app, lambda: tasks[2].status is TaskStatus.COMPLETED)
    controller.shutdown()


def test_controller_cancels_and_retries_task(app):
    service = FakeDownloadService()
    service.wait_for_release = True
    controller = TaskController(MemoryTaskStorage(), service, FakeFFmpegService())
    task = make_task(TEST_PATH, "cancel")
    controller.add_tasks([task])
    controller.start_queue()
    wait_until(app, lambda: task.status is TaskStatus.RUNNING)
    controller.cancel_tasks([task.id])
    wait_until(app, lambda: task.status is TaskStatus.CANCELLED)

    controller.pause_queue()
    controller.retry_tasks([task.id])
    assert task.status is TaskStatus.PENDING
    service.release.set()
    controller.start_queue()
    wait_until(app, lambda: task.status is TaskStatus.COMPLETED)
    controller.shutdown()


def test_main_window_registers_panels_and_degrades_without_tools(app, tmp_path: Path, monkeypatch):
    import window as window_module

    class MissingTools:
        availability = {"ffmpeg": False, "ffprobe": False, "deno": False}

        def execute_conversion(self, *args):
            raise AssertionError("Conversion must remain disabled")

    class IdleMedia:
        def analyze(self, *args):
            raise AssertionError("No analysis expected")

        def execute_download(self, *args):
            raise AssertionError("No download expected")

    monkeypatch.setattr(window_module, "FFmpegService", MissingTools)
    monkeypatch.setattr(window_module, "YtDlpService", IdleMedia)
    storage = AppStorage(tmp_path / "app")
    storage.save_settings(Settings(output_dir=str(tmp_path), language="en"))
    window = window_module.MainWindow(storage)
    assert window.panel_stack.count() == 8
    assert list(window._panels) == [
        "analyze", "subtitle", "file_analysis", "conversion", "replacement", "queue", "log", "settings",
    ]
    assert not window.file_analysis_panel.isEnabled()
    assert [window._navigation_buttons.button(index).text() for index in range(8)] == [
        "Media", "Subtitle", "Analyze", "Convert", "Replace", "Queue", "Log", "Settings",
    ]
    window._change_language("zh_TW")
    assert window._navigation_buttons.button(0).text() == "影音"
    assert window._navigation_buttons.button(2).text() == "分析"
    assert window.queue_panel.start_button.text() == "暫停列隊"
    assert window.queue_panel.summary_label.text() == "等待: 0  完成: 0  錯誤: 0"
    assert "找不到支援的 JavaScript runtime" in window.bottom_status.message_label.text()
    assert window.settings_panel.apply_button.text() == "套用工具路徑"
    assert window.settings_panel.theme_combo.itemText(0) == "Starlit Night"
    assert window.settings.language == "zh_TW"
    assert storage.load_settings().language == "en"
    assert window.settings_panel.update_status_label.text() == "尚未設定更新服務"
    frameless_flag = window_module.Qt.WindowType.FramelessWindowHint
    assert not window.windowFlags() & frameless_flag
    assert window.top_bar.controls.isHidden()
    assert window.top_bar.logo_label.isHidden()
    assert not window.top_bar.logo_label.pixmap().isNull()
    assert window.top_bar.findChild(window_module.QLabel, "brandTitle").text() == "MochiStar"
    window.settings_panel.custom_title_bar_checkbox.setChecked(True)
    assert window.settings.experimental_custom_title_bar
    assert not window.windowFlags() & frameless_flag
    assert window.top_bar.controls.isHidden()
    monkeypatch.setattr(window, "_set_windows_corner_preference", lambda _preference: False)
    window._apply_custom_title_bar(True) # 模擬下次啟動套用設定
    assert window.windowFlags() & frameless_flag
    assert not window.testAttribute(window_module.Qt.WidgetAttribute.WA_TranslucentBackground)
    assert not window.top_bar.controls.isHidden()
    assert not window.top_bar.logo_label.isHidden()
    assert all(not handle.isHidden() for handle in window._resize_handles.values())
    assert not window._window_border_overlay.isHidden()
    assert window._window_border_overlay.testAttribute(window_module.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert not window.mask().isEmpty()
    window._native_window_corners = True
    window._update_resize_handles()
    assert window.mask().isEmpty()
    assert not window._window_border_overlay.isHidden()
    window._native_window_corners = False
    window._update_resize_handles()
    window.show()
    app.processEvents()
    window.resize(window.width() - 20, window.height())
    app.processEvents()
    assert window.mask().boundingRect().width() == window.width()
    assert window.top_bar.minimize_button.toolTip() == "最小化"
    window.top_bar.window_state_changed(window_module.Qt.WindowState.WindowMaximized)
    assert window.top_bar.maximize_button.isHidden()
    assert not window.top_bar.restore_button.isHidden()
    window.top_bar.window_state_changed(window_module.Qt.WindowState.WindowNoState)
    window._change_language("en")
    assert window._navigation_buttons.button(0).text() == "Media"
    assert window._navigation_buttons.button(3).text() == "Convert"
    assert window.queue_panel.summary_label.text() == "Pending: 0  Completed: 0  Failed: 0"
    assert "No supported JavaScript runtime" in window.bottom_status.message_label.text()
    assert not window.conversion_panel.isEnabled()

    update_checks = []
    window._updates_configured = True
    window.update_controller.check = lambda manual=False: update_checks.append(manual)
    window.settings.last_update_check_at = "2099-01-01T00:00:00+00:00"
    window._start_automatic_update_check()
    assert update_checks == []
    window.settings.last_update_check_at = "2000-01-01T00:00:00+00:00"
    window._start_automatic_update_check()
    window._check_for_updates(True)
    assert update_checks == [False, True]

    window.settings.manual_ffmpeg_enabled = True
    window.settings.ffmpeg_bin_dir = "C:/custom"
    monkeypatch.setattr(window_module.QMessageBox, "question", lambda *args: window_module.QMessageBox.StandardButton.Yes)
    window._restore_factory_settings()
    assert window.settings == Settings()
    assert window.windowFlags() & frameless_flag
    assert not window.top_bar.controls.isHidden()
    assert window.settings_panel.factory_reset_button.text() == "恢復原廠設定"
    assert window.subtitle_panel.analyze_button.text() == "分析"
    assert storage.load_settings() == Settings(output_dir=str(tmp_path), language="en")

    opened_urls = []
    monkeypatch.setattr(window_module.QDesktopServices, "openUrl", lambda url: opened_urls.append(url) or True)
    queue_output = tmp_path / "queue-output"
    output_task = make_task(queue_output, "open-folder")
    window.queue_panel.set_tasks([output_task])
    window.queue_panel.table.doubleClicked.emit(window.queue_panel.proxy_model.index(0, 1))
    assert Path(opened_urls[-1].toLocalFile()) == queue_output.resolve()
    window._open_application_data_folder()
    assert Path(opened_urls[-1].toLocalFile()) == storage.app_dir.resolve()

    class FinishingReply:
        def __init__(self):
            self.deleted = 0

        def abort(self):
            window._thumbnail_finished(self)

        def deleteLater(self):
            self.deleted += 1

    reply = FinishingReply()
    window._thumbnail_reply = reply
    window._abort_thumbnail()
    assert window._thumbnail_reply is None
    assert reply.deleted == 2

    assert not hasattr(window.queue_panel, "reuse_button")
    assert not hasattr(window.queue_panel, "reuse_requested")

    column_widths = [80, 210, 260, 100, 90, 230, 190]
    download_column_widths = [70, 300, 100, 180]
    subtitle_column_widths = [70, 240, 90, 120, 90, 100]
    window.analyze_panel.set_column_widths(download_column_widths)
    window.subtitle_panel.set_column_widths(subtitle_column_widths)
    window.subtitle_panel.include_automatic_checkbox.setChecked(False)
    window._set_combo(window.queue_panel.concurrency_combo, 3)
    assert window.task_controller.worker_count == 3
    assert storage.load_settings().worker_count == 1
    subtitle_dir = tmp_path / "subtitles"
    subtitle_dir.mkdir()
    window.subtitle_panel.output_directory_edit.setText(str(subtitle_dir))
    assert window.analyze_panel.output_directory_edit.text() == str(subtitle_dir)
    window._set_combo(window.subtitle_panel.cookie_mode_combo, "browser")
    window._set_combo(window.subtitle_panel.cookie_browser_combo, "firefox")
    window.subtitle_panel.cookie_profile_edit.setText("subtitle-profile")
    assert window.analyze_panel.cookie_mode_combo.currentData() == "browser"
    assert window.analyze_panel.cookie_browser_combo.currentData() == "firefox"
    assert window.analyze_panel.cookie_profile_edit.text() == "subtitle-profile"
    assert storage.load_settings().output_dir == str(tmp_path)
    assert storage.load_settings().cookie == CookieConfig()
    window.queue_panel.set_column_widths(column_widths)
    conversion_dir = tmp_path / "converted"
    conversion_dir.mkdir()
    window.conversion_panel.output_directory_edit.setText(str(conversion_dir))
    window._set_combo(window.conversion_panel.output_type_combo, "audio")
    window._set_combo(window.conversion_panel.audio_format_combo, "flac")
    window.conversion_panel.remux_checkbox.setChecked(True)
    window.panel_stack.setCurrentWidget(window.conversion_panel)
    window.show()
    app.processEvents()
    window.conversion_panel.set_splitter_sizes([430, 710])
    splitter_sizes = window.conversion_panel.splitter_sizes()
    window.replacement_panel.output_directory_edit.setText(str(conversion_dir))
    window._set_combo(window.replacement_panel.duration_mode_combo, "custom")
    window.replacement_panel.custom_duration_edit.setText("00:01:30.500")
    window.replacement_panel.audio_card.loop_checkbox.setChecked(True)
    window.replacement_panel.audio_card.delay_spin.setValue(0.25)
    window.replacement_panel.set_splitter_sizes([520, 620])
    replacement_splitter_sizes = window.replacement_panel.splitter_sizes()
    shared_preset = ConversionPreset(id="shared-video", name="Shared Video", quality_value=7.5)
    window.conversion_panel.set_presets([shared_preset], "shared-video")
    window._shared_presets_changed(window.conversion_panel, [shared_preset])
    window.replacement_panel.preset_combo.setCurrentIndex(
        window.replacement_panel.preset_combo.findData("shared-video")
    )
    window.replacement_panel.quality_value_spin.setValue(9)
    assert window.replacement_panel.preset_combo.placeholderText() == "Shared Video*"
    window.register_panel("future", "Future", QWidget())
    assert window.panel_stack.count() == 9
    window.close()
    app.processEvents()
    assert (storage.app_dir / "settings.json").is_file()
    assert storage.load_settings().download_column_widths == download_column_widths
    assert storage.load_settings().subtitle_column_widths == subtitle_column_widths
    assert storage.load_settings().include_automatic_subtitles is False
    assert storage.load_settings().worker_count == 3
    assert storage.load_settings().output_dir == str(subtitle_dir)
    assert storage.load_settings().cookie == CookieConfig(
        source="browser", browser="firefox", profile="subtitle-profile",
    )
    assert storage.load_settings().queue_column_widths == column_widths
    assert storage.load_settings().conversion_splitter_sizes == splitter_sizes
    assert storage.load_settings().replacement_settings["custom_duration"] == 90.5
    assert storage.load_settings().replacement_settings["audio_loop"] is True
    assert storage.load_settings().replacement_settings["audio_delay"] == 0.25
    assert storage.load_settings().replacement_settings["preset_id"] == "shared-video"
    assert storage.load_settings().last_conversion_preset_id == "shared-video"
    assert [preset.id for preset in storage.load_settings().conversion_presets] == ["shared-video"]
    assert storage.load_settings().replacement_splitter_sizes == replacement_splitter_sizes

    reopened = window_module.MainWindow(storage)
    assert not reopened.windowFlags() & frameless_flag
    assert reopened.top_bar.controls.isHidden()
    reopened.panel_stack.setCurrentWidget(reopened.conversion_panel)
    reopened.show()
    app.processEvents()
    assert reopened.analyze_panel.column_widths() == download_column_widths
    assert reopened.subtitle_panel.column_widths() == subtitle_column_widths
    assert not reopened.subtitle_panel.include_automatic_checkbox.isChecked()
    assert reopened.queue_panel.concurrency_combo.currentData() == 3
    assert reopened.subtitle_panel.output_directory_edit.text() == str(subtitle_dir)
    assert reopened.analyze_panel.output_directory_edit.text() == str(subtitle_dir)
    assert reopened.subtitle_panel.cookie_mode_combo.currentData() == "browser"
    assert reopened.subtitle_panel.cookie_browser_combo.currentData() == "firefox"
    assert reopened.analyze_panel.cookie_mode_combo.currentData() == "browser"
    assert reopened.analyze_panel.cookie_browser_combo.currentData() == "firefox"
    assert reopened.replacement_panel.custom_duration_edit.text() == "00:01:30.500"
    assert reopened.replacement_panel.audio_card.loop_checkbox.isChecked()
    assert reopened.replacement_panel.current_preset_id() == "shared-video"
    assert reopened.replacement_panel.quality_value_spin.value() == 9
    assert reopened.replacement_panel.preset_combo.placeholderText() == "Shared Video*"
    assert not reopened.replacement_panel.visual_card.path and not reopened.replacement_panel.audio_card.path
    assert reopened.subtitle_panel.cookie_profile_edit.text() == "subtitle-profile"
    assert reopened.analyze_panel.cookie_profile_edit.text() == "subtitle-profile"
    assert reopened.queue_panel.column_widths() == column_widths
    assert reopened.conversion_panel.output_directory_edit.text() == str(conversion_dir)
    assert reopened.conversion_panel.output_type_combo.currentData() == "audio"
    assert reopened.conversion_panel.audio_format_combo.currentData() == "flac"
    assert reopened.conversion_panel.remux_checkbox.isChecked()
    reopened_sizes = reopened.conversion_panel.splitter_sizes()
    assert reopened_sizes[0] / sum(reopened_sizes) == pytest.approx(splitter_sizes[0] / sum(splitter_sizes), abs=0.002)
    reopened.close()
    app.processEvents()


def test_startup_dependency_check_shows_only_unignored_missing_items(app, tmp_path: Path, monkeypatch):
    import window as window_module

    class ReportFFmpeg:
        def __init__(self):
            self.ffmpeg_path = None
            self.ffprobe_path = None

        @property
        def availability(self):
            return {"ffmpeg": bool(self.ffmpeg_path), "ffprobe": bool(self.ffprobe_path)}

        def configure_tools(self, _directory=""): return {}
        def set_validated_tools(self, ffmpeg_path="", ffprobe_path=""):
            self.ffmpeg_path, self.ffprobe_path = ffmpeg_path, ffprobe_path
        def list_hardware_backends(self): return []

    class ReportMedia:
        def __init__(self): self.runtimes = {}
        def configure_tools(self, *_directories): return None
        def set_validated_runtimes(self, runtimes): self.runtimes = dict(runtimes)
        def detect_js_runtimes(self): return dict(self.runtimes)

    report = DependencyReport(
        ToolStatus("ffmpeg", "FFmpeg", "ffmpeg", "C:/tools/ffmpeg.exe", "8.0", state="available"),
        ToolStatus("ffprobe", "FFprobe", "ffprobe", "C:/tools/ffprobe.exe", "8.0", state="available"),
        (ToolStatus(
            "deno", "Deno", "deno", "C:/old/deno.exe", "2.2.0", "2.3.0", state="outdated"
        ),),
    )

    class StaticInspector:
        def inspect(self, *_directories): return report

    monkeypatch.setattr(window_module, "FFmpegService", ReportFFmpeg)
    monkeypatch.setattr(window_module, "YtDlpService", ReportMedia)
    storage = AppStorage(tmp_path / "app")
    storage.save_settings(Settings(
        output_dir=str(tmp_path), language="en", ignored_missing_dependencies=["ffmpeg"],
    ))
    window = window_module.MainWindow(storage, dependency_inspector=StaticInspector())
    window.show()

    wait_until(app, lambda: window._dependency_dialog is not None and window._dependency_dialog.isVisible())

    assert window._dependency_dialog.missing_ids == {"js_runtime"}
    assert window.settings.ignored_missing_dependencies == []
    window._ignore_dependency_reminders(["js_runtime"])
    assert storage.load_settings().ignored_missing_dependencies == ["js_runtime"]
    assert window.settings_panel.reset_dependency_reminders_button.isEnabled()
    window.close()
    app.processEvents()


def test_playlist_download_is_split_into_independent_queue_tasks(app, tmp_path: Path, monkeypatch):
    import window as window_module

    class AvailableFFmpeg:
        availability = {"ffmpeg": True, "ffprobe": True}
        ffmpeg_path = "C:/tools/ffmpeg.exe"
        ffprobe_path = "C:/tools/ffprobe.exe"

        def configure_tools(self, _directory=""): return {}
        def list_hardware_backends(self): return []

    class IdleMedia:
        def configure_tools(self, *_directories): return None
        def detect_js_runtimes(self): return {"node": "C:/tools/node.exe"}
        def execute_download(self, *_args): raise AssertionError("Queue must remain paused")

    monkeypatch.setattr(window_module, "FFmpegService", AvailableFFmpeg)
    monkeypatch.setattr(window_module, "YtDlpService", IdleMedia)
    storage = AppStorage(tmp_path / "app")
    storage.save_settings(Settings(output_dir=str(tmp_path), language="en"))
    window = window_module.MainWindow(storage)
    window.task_controller.pause_queue()
    playlist = MediaInfo(media_id="playlist", title="Playlist", entries=[
        MediaInfo(media_id="one", title="One", webpage_url="https://example.test/one"),
        MediaInfo(media_id="two", title="Two", webpage_url="https://example.test/two"),
    ])
    window.current_media = playlist
    window.panel_stack.setCurrentWidget(window.analyze_panel)
    window._add_download({
        "url": "https://example.test/playlist", "output_dir": str(tmp_path),
        "preset": "video_only", "resolution": "best", "video_container": "mp4",
        "audio_output": "original", "playlist_item_ids": ["one", "two"], "cookie": {},
    })
    tasks = window.task_controller.tasks
    assert [task.title for task in tasks] == ["One", "Two"]
    assert [task.download_options.url for task in tasks] == [
        "https://example.test/one", "https://example.test/two",
    ]
    assert all(task.download_options.playlist_item_ids == [] for task in tasks)
    assert window.panel_stack.currentWidget() is window.analyze_panel
    window.close()
    app.processEvents()


def test_conversion_preflight_checks_all_required_streams_and_caches_probe(app, tmp_path: Path, sample_media, monkeypatch):
    import window as window_module

    probes = {
        "clip.mp4": {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]},
        "song.mp3": {"streams": [{"codec_type": "audio"}]},
        "cover.m4a": {"streams": [
            {"codec_type": "video", "disposition": {"attached_pic": 1}}, {"codec_type": "audio"},
        ]},
        "silent.mov": {"streams": [{"codec_type": "video"}]},
    }

    class ProbeFFmpeg:
        VIDEO_FORMATS = FFmpegService.VIDEO_FORMATS
        AUDIO_FORMATS = FFmpegService.AUDIO_FORMATS
        SUBTITLE_FORMATS = FFmpegService.SUBTITLE_FORMATS
        MISSING_STREAM_ERRORS = FFmpegService.MISSING_STREAM_ERRORS
        availability = {"ffmpeg": True, "ffprobe": True}

        def __init__(self): self.calls = []
        def configure_tools(self, _directory=""): return {}
        def list_hardware_backends(self): return []
        def validate_options(self, _options): return ""
        def required_stream_type(self, target): return FFmpegService.required_stream_type(self, target)
        def has_media_stream(self, probe, media_type): return FFmpegService.has_media_stream(probe, media_type)
        def validate_audio_copy(self, _probe, _target): return True, ""
        def validate_stream_copy(self, _probe, _target): return True, ""
        def probe(self, path):
            self.calls.append(Path(path).name)
            return probes[Path(path).name]
        def execute_conversion(self, *_args): raise AssertionError("No conversion expected")

    class IdleMedia:
        def execute_download(self, *_args): raise AssertionError("No download expected")

    monkeypatch.setattr(window_module, "FFmpegService", ProbeFFmpeg)
    monkeypatch.setattr(window_module, "YtDlpService", IdleMedia)
    storage = AppStorage(tmp_path / "app")
    storage.save_settings(Settings(output_dir=str(tmp_path), language="en"))
    window = window_module.MainWindow(storage)
    files = list(sample_media("clip.mp4", "song.mp3", "cover.m4a", "silent.mov"))
    window.conversion_panel.output_directory_edit.setText(str(tmp_path))
    window.conversion_panel.add_files(files[:3])

    message = window.conversion_panel.validation_label.text()
    assert "song.mp3" in message and "cover.m4a" in message
    assert "clip.mp4" not in message
    assert not window.conversion_panel.add_button.isEnabled()
    assert sorted(window.ffmpeg_service.calls) == ["clip.mp4", "cover.m4a", "song.mp3"]

    window._set_combo(window.conversion_panel.output_type_combo, "audio")
    assert not window.conversion_panel.validation_label.text()
    assert window.conversion_panel.add_button.isEnabled()
    assert len(window.ffmpeg_service.calls) == 3
    window.conversion_panel.add_files([files[3]])
    assert "silent.mov" in window.conversion_panel.validation_label.text()
    assert not window.conversion_panel.add_button.isEnabled()
    window.conversion_panel.files_list.item(0).setSelected(True)
    window.conversion_panel.analyze_files_button.click()
    wait_until(app, lambda: window.file_analysis_panel._cards[str(files[0])].probe is not None)
    assert window.panel_stack.currentWidget() is window.file_analysis_panel
    assert window.file_analysis_panel.report_paths() == [str(files[0])]
    window.close()
    app.processEvents()
