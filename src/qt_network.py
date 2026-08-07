from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


class QtNetworkError(RuntimeError):
    """表示 Qt network request 無法完成"""


class QtNetworkCancelled(QtNetworkError):
    """表示 Qt network request 已取消"""


@dataclass(frozen=True, slots=True)
class QtDownloadResult:
    """保存 Qt network 串流下載結果"""

    size: int
    sha256: str


class QtNetworkClient:
    """使用 Qt network stack 執行 blocking worker request"""

    def read(
        self,
        url: str,
        headers: Mapping[str, str],
        timeout: float,
        max_size: int,
    ) -> bytes:
        data = bytearray()
        self._transfer(url, headers, timeout, data.extend, max_size=max_size)
        return bytes(data)

    def download(
        self,
        url: str,
        headers: Mapping[str, str],
        timeout: float,
        output_path: Path,
        progress: Callable[[int], None],
        cancel_event: Event,
    ) -> QtDownloadResult:
        digest = hashlib.sha256()
        with output_path.open("wb") as output:
            def write_chunk(chunk: bytes) -> None:
                output.write(chunk)
                digest.update(chunk)

            size = self._transfer(
                url, headers, timeout, write_chunk, progress=progress, cancel_event=cancel_event,
            )
        return QtDownloadResult(size, digest.hexdigest())

    @staticmethod
    def _transfer(
        url: str,
        headers: Mapping[str, str],
        timeout: float,
        write_chunk: Callable[[bytes], None],
        max_size: int | None = None,
        progress: Callable[[int], None] | None = None,
        cancel_event: Event | None = None,
    ) -> int:
        """在目前 worker thread 建立 local Qt event loop 並串流接收資料"""
        if QCoreApplication.instance() is None: raise QtNetworkError("Qt application is not running")

        manager = QNetworkAccessManager()
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        request.setTransferTimeout(max(1, round(timeout * 1000)))
        for name, value in headers.items(): request.setRawHeader(name.encode("ascii"), value.encode("utf-8"))

        reply = manager.get(request)
        loop = QEventLoop()
        cancel_timer = QTimer()
        cancel_timer.setInterval(100)
        received = 0
        failure: Exception | None = None

        def consume() -> None:
            nonlocal received, failure
            if failure is not None: return
            chunk = bytes(reply.readAll())
            if not chunk: return
            if max_size is not None and received + len(chunk) > max_size:
                failure = QtNetworkError("Network response is too large")
                reply.abort()
                return
            try:
                write_chunk(chunk)
                received += len(chunk)
                if progress: progress(received)
            except Exception as error:
                failure = error
                reply.abort()

        def cancel_if_requested() -> None:
            nonlocal failure
            if cancel_event and cancel_event.is_set() and failure is None:
                failure = QtNetworkCancelled("Network request was cancelled")
                reply.abort()

        reply.readyRead.connect(consume)
        reply.finished.connect(loop.quit)
        if cancel_event:
            cancel_timer.timeout.connect(cancel_if_requested)
            cancel_timer.start()
            cancel_if_requested()
        if not reply.isFinished(): loop.exec()
        cancel_timer.stop()
        consume()

        error = reply.error()
        error_text = reply.errorString()
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        reply.readyRead.disconnect(consume)
        reply.finished.disconnect(loop.quit)
        if cancel_event: cancel_timer.timeout.disconnect(cancel_if_requested)
        reply.close()
        if failure: raise failure
        if error != QNetworkReply.NetworkError.NoError: raise QtNetworkError(error_text)
        if isinstance(status, int) and status >= 400: raise QtNetworkError(f"HTTP {status}")
        return received
