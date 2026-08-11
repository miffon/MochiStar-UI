from __future__ import annotations

import ctypes
import logging
import os
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QCoreApplication, QLockFile
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from executable_finder import configure_macos_executable_path
from i18n import set_language, system_ui_font
from logging_bridge import QtLogBridge
from storage import AppStorage, Settings
from theme import ThemeError, apply_theme
from update_service import QtGitHubReleaseProvider, current_platform_key
from version import DISPLAY_VERSION
from window import MainWindow


def _configure_platform_identity() -> None:
    """設定各平台需要的 application identity"""
    if sys.platform != "win32": return
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MochiStar.MochiStar.1")


def _application_icon_path() -> Path:
    """取得目前平台偏好的 application icon"""
    assets = Path(__file__).resolve().parent / "assets"
    extension = ".ico" if sys.platform == "win32" else ".icns" if sys.platform == "darwin" else ".svg"
    icon_path = assets / f"logo{extension}"
    return icon_path if icon_path.is_file() else assets / "logo.svg"


def _acquire_instance_lock(app_dir: Path) -> QLockFile | None:
    """取得跨平台單一實例鎖, 已有程式執行時回傳 None"""
    app_dir.mkdir(parents=True, exist_ok=True) # 全新安裝時先建立 application data 目錄
    lock = QLockFile(str(app_dir / "MochiStar.lock"))
    lock.setStaleLockTime(0)
    return lock if lock.tryLock(0) else None


def _ask_preferred_language() -> str:
    """第一次啟動時使用英文詢問偏好語言"""
    dialog = QMessageBox()
    dialog.setWindowTitle("Choose Language")
    dialog.setIcon(QMessageBox.Icon.Question)
    dialog.setText("Choose your preferred language")
    dialog.setInformativeText("You can change this later in Settings")
    english_button = dialog.addButton("English", QMessageBox.ButtonRole.AcceptRole)
    chinese_button = dialog.addButton("繁體中文", QMessageBox.ButtonRole.AcceptRole)
    dialog.setDefaultButton(english_button)
    dialog.exec()
    return "zh_TW" if dialog.clickedButton() is chinese_button else "en"


def _initialize_language(
    storage: AppStorage,
    settings: Settings,
    chooser: Callable[[], str] = _ask_preferred_language,
) -> str:
    """第一次啟動時保存語言選擇, 既有設定直接沿用"""
    settings_path = storage.app_dir / storage.SETTINGS_FILENAME
    if settings_path.is_file(): return settings.language
    selected_language = chooser()
    settings.language = selected_language if selected_language in {"en", "zh_TW"} else "en"
    storage.save_settings(settings)
    return settings.language


def _run_update_smoke_test(provider: QtGitHubReleaseProvider | None = None) -> int:
    """讓 packaged application 驗證 Qt network update request"""
    (provider or QtGitHubReleaseProvider()).check_latest(current_platform_key())
    return 0


def _write_probe_stage(stage: str) -> None:
    """將 packaged probe 啟動階段寫入外部 trace"""
    trace_path = os.environ.get("MOCHISTAR_PROBE_TRACE_FILE", "")
    if not trace_path: return
    try:
        with Path(trace_path).open("a", encoding="utf-8") as trace_file:
            trace_file.write(f"pid={os.getpid()} stage={stage}\n")
    except OSError:
        pass


def main() -> int:
    """建立 application 並啟動 Qt event loop"""
    configure_macos_executable_path(os.environ)
    QCoreApplication.setOrganizationName("Miffon")
    QCoreApplication.setApplicationName("MochiStar")
    QCoreApplication.setApplicationVersion(DISPLAY_VERSION)
    _configure_platform_identity()
    update_smoke_test = os.environ.get("MOCHISTAR_UPDATE_SMOKE_TEST") == "1"
    system_test = os.environ.get("MOCHISTAR_SYSTEM_TEST", "")
    if update_smoke_test or system_test:
        probe_name = "update-smoke" if update_smoke_test else system_test
        _write_probe_stage(f"{probe_name}:before-qapplication")
        app = QApplication(sys.argv)
        _write_probe_stage(f"{probe_name}:after-qapplication")
        if update_smoke_test: return _run_update_smoke_test()
        from system_probe import run_system_probe
        _write_probe_stage(f"{probe_name}:before-system-probe")
        return run_system_probe(system_test, os.environ.get("MOCHISTAR_SYSTEM_TEST_URL", ""))
    app = QApplication(sys.argv)
    app.setApplicationDisplayName("MochiStar")
    app.setWindowIcon(QIcon(str(_application_icon_path()))) # 設定工作列與視窗 icon
    app.setFont(system_ui_font()) # 使用跨平台繁中 UI font

    storage = AppStorage()
    instance_lock = _acquire_instance_lock(storage.app_dir)
    if instance_lock is None:
        QMessageBox.warning(None, "MochiStar", "MochiStar is already running\nMochiStar 已經在執行中")
        return 0
    settings = storage.load_settings()
    log_bridge = QtLogBridge(storage.app_dir / "logs")
    log_bridge.install()
    logging.getLogger(__name__).info("Starting MochiStar")
    try:
        try:
            apply_theme(app, settings.theme_name)
        except ThemeError:
            logging.getLogger(__name__).exception("Unable to apply application theme")
            try:
                apply_theme(app, "cute_light")
            except ThemeError:
                logging.getLogger(__name__).exception("Unable to apply fallback application theme")
        set_language(_initialize_language(storage, settings))
        window = MainWindow(storage, log_bridge)
        window.show()
        return app.exec()
    finally:
        log_bridge.restore_streams()
        instance_lock.unlock()

if __name__ == "__main__":
    raise SystemExit(main())
