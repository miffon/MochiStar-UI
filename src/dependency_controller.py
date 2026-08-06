from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal, Slot

from external_tools import DependencyReport, ExternalToolInspector


class DependencyController(QObject):
    """在背景驗證啟動依賴, 避免版本命令阻塞主視窗"""

    report_ready = Signal(object)
    check_failed = Signal(str)
    _finished = Signal(int, object, str)

    def __init__(self, inspector: ExternalToolInspector):
        super().__init__()
        self.inspector = inspector
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dependency-check")
        self._request_id = 0
        self._shutting_down = False
        self._finished.connect(self._apply_result)

    def check(self, ffmpeg_directory: str = "", js_runtime_directory: str = "") -> None:
        """開始新的依賴檢查並忽略較舊結果"""
        if self._shutting_down: return
        self._request_id += 1
        request_id = self._request_id
        self._executor.submit(self._run, request_id, ffmpeg_directory, js_runtime_directory)

    def shutdown(self) -> None:
        """停止接收結果並關閉 worker"""
        self._shutting_down = True
        self._request_id += 1
        self._executor.shutdown(wait=False, cancel_futures=True)

    def invalidate(self) -> None:
        """讓尚未完成的檢查結果失效"""
        self._request_id += 1

    def _run(self, request_id: int, ffmpeg_directory: str, js_runtime_directory: str) -> None:
        try:
            report = self.inspector.inspect(ffmpeg_directory, js_runtime_directory)
            self._finished.emit(request_id, report, "")
        except Exception as error:
            logging.getLogger(__name__).exception("External dependency check failed")
            self._finished.emit(request_id, None, str(error))

    @Slot(int, object, str)
    def _apply_result(self, request_id: int, report: DependencyReport | None, error: str) -> None:
        if self._shutting_down or request_id != self._request_id: return
        if error:
            self.check_failed.emit(error)
        elif report is not None:
            self.report_ready.emit(report)
