from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from update_service import (
    DownloadedUpdate,
    ReleaseProvider,
    SemanticVersion,
    UpdateCheckResult,
    UpdateCheckStatus,
    UpdateCancelled,
    UpdateDownloader,
    UpdateRelease,
    current_platform_key,
)


class UpdateController(QObject):
    """在背景執行更新檢查與下載"""

    check_started = Signal(bool)
    check_succeeded = Signal(object, bool)
    check_failed = Signal(str, bool)
    download_started = Signal(object)
    download_progress = Signal(int, int)
    download_succeeded = Signal(object)
    download_failed = Signal(str)
    download_cancelled = Signal()
    log_message = Signal(str)
    _check_finished = Signal(int, object, str, bool)
    _download_finished = Signal(int, object, str, bool)
    _progress_ready = Signal(int, int, int)

    def __init__(
        self,
        provider: ReleaseProvider,
        downloader: UpdateDownloader,
        current_version: str,
        platform_key: str | None = None,
        is_test_build: bool = False,
    ):
        super().__init__()
        self.provider = provider
        self.downloader = downloader
        self.current_version = SemanticVersion.parse(current_version)
        self.platform_key = platform_key or current_platform_key()
        self.is_test_build = is_test_build
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="app-update")
        self._check_id = 0
        self._download_id = 0
        self._download_cancel_event: threading.Event | None = None
        self._shutting_down = False
        self._check_finished.connect(self._apply_check_result)
        self._download_finished.connect(self._apply_download_result)
        self._progress_ready.connect(self._apply_progress)

    def check(self, manual: bool = False) -> None:
        """開始更新檢查並忽略較舊結果"""
        if self._shutting_down: return
        self._check_id += 1
        request_id = self._check_id
        self.check_started.emit(manual)
        self._executor.submit(self._run_check, request_id, manual)

    def download(self, release: UpdateRelease, target_root: Path) -> None:
        """下載並驗證指定 release"""
        if self._shutting_down: return
        self._download_id += 1
        request_id = self._download_id
        if self._download_cancel_event: self._download_cancel_event.set()
        self._download_cancel_event = threading.Event()
        self.download_started.emit(release)
        target_dir = target_root / str(release.version)
        self._executor.submit(self._run_download, request_id, release, target_dir, self._download_cancel_event)

    def cancel_download(self) -> None:
        if self._download_cancel_event: self._download_cancel_event.set()

    def shutdown(self) -> None:
        self._shutting_down = True
        if self._download_cancel_event: self._download_cancel_event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run_check(self, request_id: int, manual: bool) -> None:
        try:
            release = self.provider.check_latest(self.platform_key)
            self._check_finished.emit(request_id, self._classify_release(release), "", manual)
        except Exception as error:
            logging.getLogger(__name__).exception("Update check failed")
            self._check_finished.emit(request_id, None, str(error), manual)

    def _classify_release(self, release: UpdateRelease | None) -> UpdateCheckResult:
        """依正式版與目前 build 身分判斷更新狀態"""
        if release is None: return UpdateCheckResult(UpdateCheckStatus.NO_STABLE_RELEASE)
        if release.version > self.current_version or (self.is_test_build and release.version == self.current_version):
            return UpdateCheckResult(UpdateCheckStatus.AVAILABLE, release)
        if self.is_test_build and release.version < self.current_version:
            return UpdateCheckResult(UpdateCheckStatus.TEST_BUILD_AHEAD)
        return UpdateCheckResult(UpdateCheckStatus.UP_TO_DATE)

    def _run_download(
        self,
        request_id: int,
        release: UpdateRelease,
        target_dir: Path,
        cancel_event: threading.Event,
    ) -> None:
        try:
            update = self.downloader.download(
                release,
                target_dir,
                lambda received, total: self._progress_ready.emit(request_id, received, total),
                cancel_event,
            )
            self._download_finished.emit(request_id, update, "", False)
        except UpdateCancelled:
            self._download_finished.emit(request_id, None, "", True)
        except Exception as error:
            logging.getLogger(__name__).exception("Update download failed")
            self._download_finished.emit(request_id, None, str(error), False)

    @Slot(int, object, str, bool)
    def _apply_check_result(
        self,
        request_id: int,
        result: UpdateCheckResult | None,
        error: str,
        manual: bool,
    ) -> None:
        if self._shutting_down or request_id != self._check_id: return
        if error:
            self.check_failed.emit(error, manual)
            self.log_message.emit(f"Update check failed: {error}")
            return
        if result is None: return
        self.check_succeeded.emit(result, manual)
        self.log_message.emit(
            f"Update available: {result.release.tag}"
            if result.release else f"Update check result: {result.status.value}"
        )

    @Slot(int, object, str, bool)
    def _apply_download_result(
        self,
        request_id: int,
        update: DownloadedUpdate | None,
        error: str,
        cancelled: bool,
    ) -> None:
        if self._shutting_down or request_id != self._download_id: return
        if cancelled:
            self.download_cancelled.emit()
            self.log_message.emit("Update download cancelled")
        elif error:
            self.download_failed.emit(error)
            self.log_message.emit(f"Update download failed: {error}")
        elif update is not None:
            self.download_succeeded.emit(update)
            self.log_message.emit(f"Update downloaded: {update.path}")

    @Slot(int, int, int)
    def _apply_progress(self, request_id: int, received: int, total: int) -> None:
        if not self._shutting_down and request_id == self._download_id:
            self.download_progress.emit(received, total)
