from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from ffmpeg_service import FFmpegService


class FileAnalysisController(QObject):
    """在背景執行多檔 FFprobe, 避免分析時阻塞介面"""

    analysis_finished = Signal(str, object, str)
    log_message = Signal(str)
    _result_received = Signal(str, object, str)

    def __init__(self, service: FFmpegService):
        super().__init__()
        self.service = service
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="file-analysis")
        self._probes: dict[str, dict[str, Any]] = {}
        self._shutting_down = False
        self._result_received.connect(self._apply_result)

    def analyze(self, paths: list[str]) -> None:
        """只讀取多個檔案的 metadata, 每個檔案完成時個別回報"""
        for path in dict.fromkeys(paths): self._executor.submit(self._run_metadata, str(Path(path)))

    def analyze_gop(self, path: str) -> None:
        """依使用者要求深入掃描單一影片的 GOP"""
        if path: self._executor.submit(self._run_gop, str(Path(path)))

    def shutdown(self) -> None:
        """停止接收結果並關閉 worker"""
        self._shutting_down = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run_metadata(self, path: str) -> None:
        try:
            probe = self.service.probe(path)
            self._probes[path] = probe
            self._result_received.emit(path, probe, "")
        except Exception as error:
            logging.getLogger(__name__).exception("File analysis failed for %s", path)
            self._result_received.emit(path, None, str(error))

    def _run_gop(self, path: str) -> None:
        try:
            analyze_gop = getattr(self.service, "analyze_gop", None)
            probe = analyze_gop(path, self._probes.get(path)) if analyze_gop else self.service.analyze_file(path)
            self._probes[path] = probe
            self._result_received.emit(path, probe, "")
        except Exception as error:
            logging.getLogger(__name__).exception("GOP analysis failed for %s", path)
            probe = dict(self._probes.get(path) or {})
            probe["gop_analysis"] = {"value": None, "error": str(error)}
            self._probes[path] = probe
            self._result_received.emit(path, probe, "")
            self.log_message.emit(f"GOP analysis failed for {path}: {error}")

    @Slot(str, object, str)
    def _apply_result(self, path: str, probe: dict[str, Any] | None, error: str) -> None:
        if self._shutting_down: return
        self.analysis_finished.emit(path, probe, error)
        if error: self.log_message.emit(f"File analysis failed for {path}: {error}")
