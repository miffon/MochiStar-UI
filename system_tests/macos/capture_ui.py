from __future__ import annotations

import argparse
import html
import json
import logging
import os
import platform
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, qVersion
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QInputDialog, QLabel, QMessageBox, QProgressDialog,
    QPushButton, QScrollArea, QWidget,
)

from dependency_dialog import DependencyDialog
from external_tools import DependencyReport, InstallGuide, ToolStatus
from i18n import set_language, system_ui_font, translate_widget_tree
from local_media_server import start_local_media_server
from models import DownloadOptions, FormatInfo, MediaInfo, SubtitleTrack, TaskKind, TaskRecord, TaskStatus
from storage import AppStorage, Settings
from theme import apply_theme
from window import MainWindow


PANELS = (
    ("media", "analyze"), ("subtitle", "subtitle"), ("analyze", "file_analysis"),
    ("convert", "conversion"), ("replace", "replacement"), ("queue", "queue"),
    ("log", "log"), ("settings", "settings"),
)
SCREENSHOT_THEME = "cute_light"
GEOMETRY_THEME = "starlit_night"
LANGUAGES = ("zh_TW", "en")
SCROLLED_PAGES = {"media", "analyze", "convert", "replace", "settings"}
REQUESTED_SIZE = (1180, 820)
MINIMUM_SIZE = (960, 680)


class _OfflineUpdateProvider:
    repository = ""

    def check_latest(self, _platform_key: str) -> None:
        return None


class _StableDependencyInspector:
    def inspect(self, _ffmpeg_directory: str = "", _js_directory: str = "") -> DependencyReport:
        available = lambda key, name: ToolStatus(key, name, key, "/usr/bin/true", "1.0", state="available")
        return DependencyReport(
            available("ffmpeg", "FFmpeg"), available("ffprobe", "FFprobe"),
            (available("deno", "Deno"),),
        )


class _CaptureWindow(MainWindow):
    """略過非 UI dependency scan, 讓 screenshot fixture 不依賴 runner 預裝工具"""

    def _check_external_tools(self) -> None:
        return

    def _run_startup_dependency_check(self) -> None:
        return


def _media_fixture() -> MediaInfo:
    """建立能覆蓋長字串, playlist, format 和 subtitle 的固定資料"""
    formats = [
        FormatInfo("401", "mp4", "3840x2160", 60, "av01.0.12M.08", "none", 1_840_000_000, 3840, 2160),
        FormatInfo("140", "m4a", "audio only", None, "none", "mp4a.40.2", 18_400_000),
    ]
    subtitles = [
        SubtitleTrack("zh-Hant", "繁體中文字幕 - 人工校對版本", "manual", ["vtt", "srt"]),
        SubtitleTrack("en", "English automatic captions", "automatic", ["vtt"]),
    ]
    entries = [
        MediaInfo(
            f"fixture-{index}", title, "MochiStar 測試頻道", 95 + index * 73,
            "YouTube", f"https://example.test/{index}", subtitles=subtitles,
        )
        for index, title in enumerate((
            "跨平台媒體工作流程與很長的測試標題 - 第一集",
            "macOS typography, spacing and native control integration",
            "Unicode 路徑測試: 星星, 麻糬, 日本語, emoji ✨",
        ), 1)
    ]
    return MediaInfo(
        "fixture-playlist", "MochiStar macOS 綜合版面測試播放清單", "MochiStar 測試頻道", 742,
        "YouTube", "https://example.test/watch?v=fixture-playlist", formats=formats,
        subtitles=subtitles, entries=entries,
    )


def _task_fixtures(output_dir: Path) -> list[TaskRecord]:
    """建立 Queue 和 bottom status 使用的固定 tasks"""
    return [
        TaskRecord(kind=TaskKind.DOWNLOAD, title="正在下載的 4K 測試影片", status=TaskStatus.RUNNING, progress=0.63,
                   download_options=DownloadOptions(output_dir=str(output_dir))),
        TaskRecord(kind=TaskKind.DOWNLOAD, title="含有很長標題與 Unicode ✨ 的等待項目", status=TaskStatus.PENDING, progress=0,
                   download_options=DownloadOptions(output_dir=str(output_dir))),
        TaskRecord(kind=TaskKind.DOWNLOAD, title="失敗項目 - 網路暫時無法使用", status=TaskStatus.FAILED, progress=0.27,
                   error="Network connection failed", download_options=DownloadOptions(output_dir=str(output_dir))),
    ]


def _probe_fixture() -> dict[str, Any]:
    return {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "123.456", "size": "18400000"},
        "duration": 123.456,
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 3840, "height": 2160,
             "r_frame_rate": "60000/1001", "pix_fmt": "yuv420p", "bit_rate": "12000000", "disposition": {}},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2,
             "channel_layout": "stereo", "bit_rate": "192000", "disposition": {}},
        ],
    }


def _populate_window(window: MainWindow, workspace: Path, media: MediaInfo) -> None:
    """填入不會觸發下載或轉檔的代表性 UI 狀態"""
    video = workspace / "來源影片 with spaces 測試.mp4"
    audio = workspace / "替換音訊 日本語.m4a"
    video.touch(exist_ok=True)
    audio.touch(exist_ok=True)
    window.current_media = media
    window.analyze_panel.url_edit.setText(media.webpage_url)
    window.analyze_panel.set_media(media)
    window.subtitle_panel.url_edit.setText(media.webpage_url)
    window.subtitle_panel.set_media(media)
    window.subtitle_panel.set_all_checked(True)
    window.queue_panel.set_tasks(_task_fixtures(workspace))
    window.bottom_status.set_tasks(_task_fixtures(workspace))
    window.conversion_panel.add_files((video, audio))
    window.conversion_panel.setEnabled(True)
    window.replacement_panel.setEnabled(True)
    window.replacement_panel.blockSignals(True)
    window.replacement_panel.visual_card.set_path(str(video))
    window.replacement_panel.audio_card.set_path(str(audio))
    window.replacement_panel.blockSignals(False)
    window.replacement_panel.set_source_probe(str(video), _probe_fixture())
    window.replacement_panel.set_source_probe(str(audio), _probe_fixture())
    window.file_analysis_panel.setEnabled(True)
    window.file_analysis_panel.blockSignals(True)
    window.file_analysis_panel.analyze_files((video,))
    window.file_analysis_panel.blockSignals(False)
    window.file_analysis_panel.set_result(str(video), _probe_fixture())
    window.log_panel.append("10:12:30 | INFO | analysis | Metadata analysis completed", logging.INFO)
    window.log_panel.append("10:12:31 | WARNING | yt_dlp | JavaScript runtime fallback was used", logging.WARNING)
    window.log_panel.append("10:12:32 | ERROR | sample | Diagnostic example stays in the Log page", logging.ERROR)
    window.settings_panel.set_tool_status(
        "FFmpeg: OK", "JavaScript runtime: OK", "/opt/homebrew/bin/ffmpeg", "/opt/homebrew/bin/deno",
    )


def _image_is_nonblank(image: Any) -> bool:
    """取樣檢查 screenshot 不是空白或單色"""
    colors = set()
    x_step, y_step = max(1, image.width() // 20), max(1, image.height() // 20)
    for x in range(0, image.width(), x_step):
        for y in range(0, image.height(), y_step):
            colors.add(image.pixelColor(x, y).rgba())
            if len(colors) >= 4: return True
    return False


def _widget_layout_checks(root: QWidget) -> list[str]:
    """回報單行文字與 checkbox 的裁切問題"""
    issues = []
    for button in root.findChildren(QCheckBox):
        if button.isVisible() and button.text() and button.sizeHint().width() > button.width() + 2:
            issues.append(f"Checkbox clipped: {button.text()}")
    for label in root.findChildren(QLabel):
        if label.isVisible() and label.text() and not label.wordWrap() and label.sizeHint().width() > label.width() + 4:
            issues.append(f"Label clipped: {label.text()[:80]}")
    return issues


def _layout_checks(window: MainWindow) -> list[str]:
    """回報 navigation 和目前 panel 的裁切問題"""
    issues = _widget_layout_checks(window.panel_stack.currentWidget())
    for button in window.navigation.findChildren(QPushButton):
        text_width = button.fontMetrics().horizontalAdvance(button.text())
        if button.isVisible() and text_width + 4 > button.width():
            issues.append(f"Navigation button clipped: {button.text()}")
    return issues


def _capture(
    window: MainWindow, output_dir: Path, theme: str, language: str, page: str, panel_id: str,
    scroll_bottom: bool = False,
) -> dict[str, Any]:
    panel = window._panels[panel_id]
    window.panel_stack.setCurrentWidget(panel)
    window._check_navigation_button(panel)
    QApplication.processEvents()
    scroll_positions = []
    for scroll in panel.findChildren(QScrollArea):
        if not scroll.isVisible(): continue
        bar = scroll.verticalScrollBar()
        scroll_positions.append((bar, bar.value()))
        if scroll_bottom: bar.setValue(bar.maximum())
    QApplication.processEvents()
    image = window.grab().toImage()
    suffix = "-bottom" if scroll_bottom else ""
    path = output_dir / theme / language / f"{page}{suffix}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = image.save(str(path), "PNG")
    for bar, value in scroll_positions: bar.setValue(value)
    result = {
        "page": f"{page}{suffix}", "panel_id": panel_id, "theme": theme, "language": language,
        "path": path.relative_to(output_dir).as_posix(), "width": image.width(), "height": image.height(),
        "requested_width": REQUESTED_SIZE[0], "requested_height": REQUESTED_SIZE[1],
        "size_adjusted_by_macos": (image.width(), image.height()) != REQUESTED_SIZE,
        "saved": saved, "nonblank": _image_is_nonblank(image), "layout_issues": _layout_checks(window),
    }
    if not saved or not result["nonblank"] or image.width() < MINIMUM_SIZE[0] or image.height() < MINIMUM_SIZE[1]:
        raise RuntimeError(f"Invalid screenshot: {result}")
    if result["layout_issues"]: raise RuntimeError(f"Layout clipping detected: {result}")
    return result


def _capture_dropdown(window: MainWindow, combo: QComboBox, output_dir: Path, name: str) -> dict[str, Any]:
    """擷取實際 QComboBox popup window"""
    combo.showPopup()
    QApplication.processEvents()
    popup = combo.view().window()
    image = popup.grab().toImage()
    path = output_dir / "dropdowns" / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = image.save(str(path), "PNG")
    combo.hidePopup()
    QApplication.processEvents()
    result = {
        "name": name, "path": path.relative_to(output_dir).as_posix(),
        "width": image.width(), "height": image.height(), "saved": saved, "nonblank": _image_is_nonblank(image),
    }
    if not saved or not result["nonblank"]: raise RuntimeError(f"Invalid dropdown screenshot: {result}")
    return result


def _capture_dialog(dialog: QWidget, output_dir: Path, name: str) -> dict[str, Any]:
    """顯示並擷取 application-owned dialog"""
    dialog.adjustSize()
    dialog.show()
    QApplication.processEvents()
    image = dialog.grab().toImage()
    path = output_dir / "dialogs" / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = image.save(str(path), "PNG")
    issues = _widget_layout_checks(dialog)
    dialog.close()
    QApplication.processEvents()
    result = {
        "name": name, "path": path.relative_to(output_dir).as_posix(),
        "width": image.width(), "height": image.height(), "saved": saved,
        "nonblank": _image_is_nonblank(image), "layout_issues": issues,
    }
    if not saved or not result["nonblank"] or issues: raise RuntimeError(f"Invalid dialog screenshot: {result}")
    return result


def _dialog_fixtures(parent: QWidget) -> list[tuple[str, QWidget]]:
    """建立 application 會使用的 dialog layout 代表案例"""
    warning = QMessageBox(
        QMessageBox.Icon.Warning, "分析失敗", "無法分析這個連結, 請檢查連結或到應用程式紀錄查看詳細資訊",
        QMessageBox.StandardButton.Close, parent,
    )
    question = QMessageBox(
        QMessageBox.Icon.Question, "刪除轉檔預設", "確定要刪除這個自訂預設嗎?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, parent,
    )
    language = QMessageBox(parent)
    language.setWindowTitle("Choose Language")
    language.setIcon(QMessageBox.Icon.Question)
    language.setText("Choose your preferred language")
    language.setInformativeText("You can change this later in Settings")
    language.addButton("English", QMessageBox.ButtonRole.AcceptRole)
    language.addButton("繁體中文", QMessageBox.ButtonRole.AcceptRole)
    update = QMessageBox(parent)
    update.setWindowTitle("有可用更新")
    update.setIcon(QMessageBox.Icon.Information)
    update.setText("MochiStar 2.0.0 已可下載")
    update.setInformativeText("這是一段較長的 Release notes, 用來檢查多行文字、按鈕排列與視窗寬度是否正常")
    update.addButton("下載更新檔", QMessageBox.ButtonRole.AcceptRole)
    update.addButton("開啟 Release 頁面", QMessageBox.ButtonRole.ActionRole)
    update.addButton("稍後提醒", QMessageBox.ButtonRole.RejectRole)
    preset = QInputDialog(parent)
    preset.setWindowTitle("儲存轉檔預設")
    preset.setLabelText("預設名稱")
    preset.setTextValue("macOS 測試預設")
    progress = QProgressDialog("正在下載更新檔...", "取消", 0, 100, parent)
    progress.setWindowTitle("應用程式更新")
    progress.setValue(42)
    missing = lambda key, name, minimum="": ToolStatus(key, name, key, minimum_version=minimum)
    dependency_report = DependencyReport(
        missing("ffmpeg", "FFmpeg"), missing("ffprobe", "FFprobe"), (missing("deno", "Deno", "2.3.0"),),
    )
    guides = {
        "ffmpeg": InstallGuide("brew install ffmpeg", "https://ffmpeg.org/download.html"),
        "js_runtime": InstallGuide("brew install deno", "https://deno.com/"),
    }
    dependency = DependencyDialog(dependency_report, {"ffmpeg", "js_runtime"}, guides, parent)
    return [
        ("warning", warning), ("question", question), ("language-selection", language),
        ("update-available", update), ("preset-name", preset), ("update-progress", progress),
        ("external-dependencies", dependency),
    ]


def _geometry_signature(window: MainWindow) -> list[tuple[Any, ...]]:
    """保存所有 widget 的 geometry 與 size hint"""
    widgets = [window, *window.findChildren(QWidget)]
    return [(
        type(widget).__name__, widget.objectName(),
        widget.geometry().x(), widget.geometry().y(), widget.width(), widget.height(),
        widget.sizeHint().width(), widget.sizeHint().height(),
    ) for widget in widgets]


def _check_theme_geometry(app: QApplication, window: MainWindow) -> dict[str, Any]:
    """確認切換色彩主題不改變 widget geometry"""
    apply_theme(app, SCREENSHOT_THEME)
    QApplication.processEvents()
    light = _geometry_signature(window)
    apply_theme(app, GEOMETRY_THEME)
    QApplication.processEvents()
    dark = _geometry_signature(window)
    apply_theme(app, SCREENSHOT_THEME)
    QApplication.processEvents()
    differences = [index for index, values in enumerate(zip(light, dark)) if values[0] != values[1]]
    result = {"themes": [SCREENSHOT_THEME, GEOMETRY_THEME], "widget_count": len(light), "differences": differences[:20]}
    if len(light) != len(dark) or differences: raise RuntimeError(f"Theme changed UI geometry: {result}")
    return result


def _capture_special_states(window: MainWindow, output_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """擷取每種 QComboBox popup 和 application dialog 代表案例"""
    combo_types: dict[str, list[QComboBox]] = {}
    for combo in window.findChildren(QComboBox):
        role = str(combo.property("role") or "")
        combo_type = role if role in {"formatSelector", "tableCell"} else "editable" if combo.isEditable() else "standard"
        combo_types.setdefault(combo_type, []).append(combo)
    expected = {"standard", "editable", "formatSelector", "tableCell"}
    if set(combo_types) != expected: raise RuntimeError(f"Unhandled QComboBox layout types: {sorted(combo_types)}")

    window.panel_stack.setCurrentWidget(window.settings_panel)
    window.settings_panel.settings_scroll.verticalScrollBar().setValue(0)
    QApplication.processEvents()
    dropdowns = [_capture_dropdown(window, window.settings_panel.theme_combo, output_dir, "standard")]

    window.panel_stack.setCurrentWidget(window.analyze_panel)
    window.analyze_panel.cookie_browser_combo.setEnabled(True)
    QApplication.processEvents()
    dropdowns.append(_capture_dropdown(window, window.analyze_panel.cookie_browser_combo, output_dir, "editable"))
    window.analyze_panel.video_format_combo.setEnabled(True)
    dropdowns.append(_capture_dropdown(window, window.analyze_panel.video_format_combo, output_dir, "fixed-font-format"))

    window.panel_stack.setCurrentWidget(window.subtitle_panel)
    QApplication.processEvents()
    table_combo = window.subtitle_panel.subtitle_table.cellWidget(0, 5)
    if not isinstance(table_combo, QComboBox): raise RuntimeError("Subtitle table combo fixture is missing")
    dropdowns.append(_capture_dropdown(window, table_combo, output_dir, "table-cell"))

    dialogs = [_capture_dialog(dialog, output_dir, name) for name, dialog in _dialog_fixtures(window)]
    return dropdowns, dialogs, {name: len(combos) for name, combos in sorted(combo_types.items())}


def _run_live_analysis(window: MainWindow, output_dir: Path, attempts: int = 3) -> dict[str, Any]:
    """透過 AnalysisController 執行真實 metadata analysis"""
    window.analysis_controller.analysis_ready.disconnect()
    window.analysis_controller.analysis_failed.disconnect()
    failures = []
    server, thread, fixture_url = start_local_media_server()
    try:
        for attempt in range(1, attempts + 1):
            loop, result = QEventLoop(), {"media": None, "error": ""}
            ready = lambda media: (result.update(media=media), loop.quit())
            failed = lambda error: (result.update(error=error), loop.quit())
            window.analysis_controller.analysis_ready.connect(ready)
            window.analysis_controller.analysis_failed.connect(failed)
            QTimer.singleShot(30_000, loop.quit)
            window._analyze({"url": fixture_url, "cookie": window.settings.cookie.to_dict()})
            loop.exec()
            window.analysis_controller.analysis_ready.disconnect(ready)
            window.analysis_controller.analysis_failed.disconnect(failed)
            media = result["media"]
            if media is not None:
                window.current_media = media
                window.analyze_panel.set_media(media)
                window.panel_stack.setCurrentWidget(window.analyze_panel)
                QApplication.processEvents()
                image = window.grab().toImage()
                path = output_dir / "live-analysis.png"
                image.save(str(path), "PNG")
                return {
                    "success": True, "attempt": attempt, "source": "local-http-fixture",
                    "title": media.title, "format_count": len(media.formats), "path": path.name,
                }
            failures.append(result["error"] or "Analysis timed out")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    raise RuntimeError(f"Live UI analysis failed after {attempts} attempts: {failures}")


def _write_index(output_dir: Path, captures: list[dict[str, Any]]) -> None:
    """建立可以直接瀏覽所有 screenshot 的 HTML index"""
    cards = "".join(
        f'<article><h2>{html.escape(item.get("title") or item.get("page") or item.get("name", "screenshot"))}</h2>'
        f'<img src="{html.escape(item["path"])}" alt="{html.escape(item.get("page") or item.get("name", "screenshot"))}"></article>'
        for item in captures
    )
    document = f"""<!doctype html><meta charset="utf-8"><title>MochiStar macOS UI</title>
<style>body{{font-family:-apple-system,sans-serif;margin:24px;background:#eceff4}}main{{display:grid;gap:20px}}
article{{background:white;padding:16px;border-radius:12px}}img{{width:100%;height:auto;border:1px solid #bbb}}h2{{font-size:16px}}</style>
<h1>MochiStar macOS UI Screenshots</h1><main>{cards}</main>"""
    (output_dir / "index.html").write_text(document, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture MochiStar macOS UI pages")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-live", action="store_true")
    arguments = parser.parse_args()
    arguments.output.mkdir(parents=True, exist_ok=True)

    QCoreApplication.setOrganizationName("Miffon")
    QCoreApplication.setApplicationName("MochiStarSystemTest")
    app = QApplication(sys.argv)
    app.setFont(system_ui_font())
    screen = app.primaryScreen().availableGeometry()
    captures = []
    live_result = {"skipped": True}
    workspace = arguments.output.parent / f"ui-workspace-{os.getpid()}"
    workspace.mkdir(parents=True, exist_ok=True)
    for language in LANGUAGES:
        storage = AppStorage(workspace / f"app-{SCREENSHOT_THEME}-{language}")
        storage.save_settings(Settings(
            output_dir=str(workspace), theme_name=SCREENSHOT_THEME, language=language, auto_check_updates=False,
            ignored_missing_dependencies=["ffmpeg", "js_runtime"],
        ))
        set_language(language)
        apply_theme(app, SCREENSHOT_THEME)
        window = _CaptureWindow(storage, update_provider=_OfflineUpdateProvider(), dependency_inspector=_StableDependencyInspector())
        window.resize(*REQUESTED_SIZE)
        _populate_window(window, workspace, _media_fixture())
        translate_widget_tree(window)
        window.show()
        QApplication.processEvents()
        for page, panel_id in PANELS:
            captures.append(_capture(window, arguments.output, SCREENSHOT_THEME, language, page, panel_id))
            if page in SCROLLED_PAGES:
                captures.append(_capture(
                    window, arguments.output, SCREENSHOT_THEME, language, page, panel_id, scroll_bottom=True,
                ))
        if language == LANGUAGES[-1] and not arguments.skip_live:
            live_result = _run_live_analysis(window, arguments.output)
        window.close()
        QApplication.processEvents()

    set_language("zh_TW")
    apply_theme(app, SCREENSHOT_THEME)
    special_storage = AppStorage(workspace / "app-special-states")
    special_storage.save_settings(Settings(
        output_dir=str(workspace), theme_name=SCREENSHOT_THEME, language="zh_TW", auto_check_updates=False,
        ignored_missing_dependencies=["ffmpeg", "js_runtime"],
    ))
    special_window = _CaptureWindow(
        special_storage, update_provider=_OfflineUpdateProvider(), dependency_inspector=_StableDependencyInspector(),
    )
    special_window.resize(*REQUESTED_SIZE)
    _populate_window(special_window, workspace, _media_fixture())
    translate_widget_tree(special_window)
    special_window.show()
    QApplication.processEvents()
    theme_geometry = _check_theme_geometry(app, special_window)
    dropdowns, dialogs, combo_inventory = _capture_special_states(special_window, arguments.output)
    special_window.close()
    QApplication.processEvents()

    expected_capture_count = len(LANGUAGES) * (len(PANELS) + len(SCROLLED_PAGES))
    index_items = [
        {**item, "title": f'{item["language"]} / {item["page"]}'} for item in captures
    ] + [
        {**item, "title": f'Dropdown / {item["name"]}'} for item in dropdowns
    ] + [
        {**item, "title": f'Dialog / {item["name"]}'} for item in dialogs
    ]
    report = {
        "environment": {"macos": platform.mac_ver()[0], "architecture": platform.machine(), "python": platform.python_version(),
                        "pyside": pyside_version, "qt": qVersion(), "scale_factor": os.environ.get("QT_SCALE_FACTOR", "1"),
                        "available_screen": {"width": screen.width(), "height": screen.height()}},
        "capture_count": len(captures), "captures": captures,
        "dropdown_count": len(dropdowns), "dropdowns": dropdowns, "combo_inventory": combo_inventory,
        "dialog_count": len(dialogs), "dialogs": dialogs, "theme_geometry": theme_geometry,
        "live_analysis": live_result,
        "success": len(captures) == expected_capture_count and len(dropdowns) == 4 and len(dialogs) == 7,
    }
    (arguments.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_index(arguments.output, index_items)
    return 0 if report["success"] else 1


if __name__ == "__main__": raise SystemExit(main())
