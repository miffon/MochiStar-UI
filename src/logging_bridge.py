from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal


LOG_FILE_PREFIX = "mochistar-"
LOG_RETENTION_COUNT = 5


def _dated_log_files(log_dir: Path) -> list[Path]:
    """取得依日期排序的 application log, 最新日期排在前面"""
    dated_files: list[tuple[date, Path]] = []
    for path in log_dir.glob(f"{LOG_FILE_PREFIX}*.log"):
        date_text = path.stem.removeprefix(LOG_FILE_PREFIX)
        try:
            log_date = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError:
            continue
        dated_files.append((log_date, path))
    return [path for _, path in sorted(dated_files, key=lambda item: item[0], reverse=True)]


def _prune_log_files(log_dir: Path, keep: int = LOG_RETENTION_COUNT) -> None:
    """只保留最近指定數量的每日 application log"""
    for path in _dated_log_files(log_dir)[max(0, keep):]:
        try:
            path.unlink()
        except OSError:
            pass # Log 回收失敗不應阻止 application 啟動


class _DailyFileHandler(logging.Handler):
    """依本機日期寫入 error log, 跨日後自動切換並回收舊檔"""

    def __init__(self, log_dir: Path, keep: int = LOG_RETENTION_COUNT):
        super().__init__()
        self.log_dir = log_dir
        self.keep = keep
        self._log_date: date | None = None
        self._file_handler: logging.FileHandler | None = None
        self._needs_prune = True
        self.setLevel(logging.ERROR)
        self._open_for_date(date.today())

    def _open_for_date(self, log_date: date) -> None:
        if self._file_handler:
            self._file_handler.close()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._log_date = log_date
        self._file_handler = logging.FileHandler(
            self.log_dir / f"{LOG_FILE_PREFIX}{log_date.isoformat()}.log", encoding="utf-8", delay=True
        )
        if self.formatter: self._file_handler.setFormatter(self.formatter)
        self._needs_prune = True

    def setFormatter(self, fmt: logging.Formatter | None) -> None:
        super().setFormatter(fmt)
        if self._file_handler: self._file_handler.setFormatter(fmt)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            today = date.today()
            if today != self._log_date: self._open_for_date(today)
            if self._file_handler: self._file_handler.emit(record)
            if self._needs_prune:
                _prune_log_files(self.log_dir, self.keep)
                self._needs_prune = False
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._file_handler:
            self._file_handler.close()
            self._file_handler = None
        super().close()


class QtLogBridge(QObject):
    """將 logging 與 stdout/stderr 安全轉送到 Qt UI"""

    message = Signal(str, int)

    def __init__(self, log_dir: Path):
        super().__init__()
        self.log_dir = log_dir
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        self._handler = _QtLogHandler(self)
        self._file_handler: _DailyFileHandler | None = None
        self._stdout_proxy = _StreamProxy(self._stdout, logging.getLogger("stdout"), logging.INFO)
        self._stderr_proxy = _StreamProxy(self._stderr, logging.getLogger("stderr"), logging.ERROR)

    def install(self) -> None:
        """安裝 UI 與 error file logging handler"""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s", "%H:%M:%S")
        self._handler.setFormatter(formatter)

        self._file_handler = _DailyFileHandler(self.log_dir)
        self._file_handler.setFormatter(formatter)

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(self._handler)
        root_logger.addHandler(self._file_handler)
        sys.stdout = self._stdout_proxy
        sys.stderr = self._stderr_proxy

    def restore_streams(self) -> None:
        """還原 process 原本的 stdout、stderr 與 logging handler"""
        if sys.stdout is self._stdout_proxy: sys.stdout = self._stdout
        if sys.stderr is self._stderr_proxy: sys.stderr = self._stderr
        root_logger = logging.getLogger()
        root_logger.removeHandler(self._handler)
        if self._file_handler:
            root_logger.removeHandler(self._file_handler)
            self._file_handler.close()
            self._file_handler = None


class _QtLogHandler(logging.Handler):
    """將格式化後的 log 送到 Qt signal"""

    def __init__(self, bridge: QtLogBridge):
        super().__init__()
        self.bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.bridge.message.emit(self.format(record), record.levelno)
        except Exception:
            self.handleError(record)


class _StreamProxy:
    """保留原 stream 並將完整文字行送進 logging"""

    def __init__(self, stream: Any, logger: logging.Logger, level: int):
        self.stream = stream
        self.logger = logger
        self.level = level
        self.buffer = ""

    def write(self, text: str) -> int:
        writer = getattr(self.stream, "write", None)
        if callable(writer): writer(text)
        flusher = getattr(self.stream, "flush", None)
        if callable(flusher): flusher()
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip(): self.logger.log(self.level, line.rstrip())
        return len(text)

    def flush(self) -> None:
        flusher = getattr(self.stream, "flush", None)
        if callable(flusher): flusher()
        if self.buffer.strip():
            self.logger.log(self.level, self.buffer.rstrip())
            self.buffer = ""

    def isatty(self) -> bool:
        checker = getattr(self.stream, "isatty", None)
        return bool(checker()) if callable(checker) else False
