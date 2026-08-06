from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal, Slot

from media_service import ServiceCancelled, YtDlpService
from models import CookieConfig, MediaInfo


class AnalysisController(QObject):
    """在背景分析 URL 並忽略過期結果"""

    analysis_ready = Signal(object)
    analysis_failed = Signal(str)
    progress_changed = Signal(int, int)
    busy_changed = Signal(bool)
    log_message = Signal(str)
    _finished = Signal(int, object, str)
    _progress_received = Signal(int, int, int)

    def __init__(self, service: YtDlpService):
        super().__init__()
        self.service = service
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="media-analysis")
        self._cancel_event: threading.Event | None = None
        self._request_id = 0
        self._shutting_down = False
        self._finished.connect(self._apply_result)
        self._progress_received.connect(self._apply_progress)

    def analyze(self, url: str, cookie: CookieConfig, include_automatic_subtitles: bool = True) -> None:
        """開始新的分析並使舊 request token 失效"""
        self._request_id += 1
        request_id = self._request_id
        if self._cancel_event: self._cancel_event.set()
        self._cancel_event = threading.Event()
        self.busy_changed.emit(True)
        self.progress_changed.emit(0, 0)
        self.log_message.emit(f"Analyzing {url}")
        self._executor.submit(
            self._run, request_id, url, cookie, self._cancel_event, include_automatic_subtitles
        )

    def cancel(self) -> None:
        """取消目前分析並立即讓 UI 離開 busy 狀態"""
        if self._cancel_event is None: return
        self._request_id += 1
        self._cancel_event.set()
        self._cancel_event = None
        self.busy_changed.emit(False)
        self.progress_changed.emit(0, 0)
        self.log_message.emit("Analysis cancelled")

    def shutdown(self) -> None:
        """取消分析 worker"""
        self._shutting_down = True
        if self._cancel_event: self._cancel_event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(
        self, request_id: int, url: str, cookie: CookieConfig,
        cancel_event: threading.Event, include_automatic_subtitles: bool,
    ) -> None:
        try:
            progress = lambda current, total: self._progress_received.emit(request_id, current, total)
            media = self.service.analyze(
                url, cookie, cancel_event, progress, include_automatic_subtitles
            )
            self._finished.emit(request_id, media, "")
        except ServiceCancelled:
            self._finished.emit(request_id, None, "")
        except Exception as error:
            logging.getLogger(__name__).exception("Analysis failed for %s", url)
            self._finished.emit(request_id, None, str(error))

    @Slot(int, int, int)
    def _apply_progress(self, request_id: int, current: int, total: int) -> None:
        if self._shutting_down or request_id != self._request_id: return
        self.progress_changed.emit(current, total)

    @Slot(int, object, str)
    def _apply_result(self, request_id: int, media: MediaInfo | None, error: str) -> None:
        if self._shutting_down or request_id != self._request_id: return
        self._cancel_event = None
        self.busy_changed.emit(False)
        if error:
            self.analysis_failed.emit(error)
            self.log_message.emit(f"Analysis failed: {error}")
        elif media is not None:
            self.analysis_ready.emit(media)
            self.log_message.emit(f"Analyzed {media.title}")
