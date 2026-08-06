from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Iterable

from PySide6.QtCore import QObject, Signal, Slot

from ffmpeg_service import FFmpegService
from media_service import ServiceCancelled, YtDlpService
from models import TaskRecord, TaskStatus
from storage import AppStorage


YOUTUBE_BLOCKED_ERROR = "YouTube blocked this request. Configure browser or Cookie file authentication in Settings, then retry."


def summarize_task_error(error: BaseException, maximum_length: int = 300) -> str:
    """將完整例外整理成適合顯示在列隊中的錯誤摘要"""
    detail = re.sub(r"\s+", " ", str(error)).strip()
    normalized = detail.casefold().replace("‘", "'").replace("’", "'")
    if "sign in to confirm you're not a bot" in normalized: return YOUTUBE_BLOCKED_ERROR
    if detail.upper().startswith("ERROR:"): detail = detail[6:].strip()
    if not detail: return type(error).__name__
    if len(detail) <= maximum_length: return detail
    return f"{detail[:maximum_length - 3].rstrip()}..."


class TaskController(QObject):
    """管理任務狀態、worker 派發與持久化"""

    tasks_changed = Signal(object)
    task_updated = Signal(object)
    queue_paused_changed = Signal(bool)
    worker_count_changed = Signal(int)
    log_message = Signal(str)
    _progress_received = Signal(str, float, str)
    _task_finished = Signal(str, str, str)

    def __init__(
        self,
        storage: AppStorage,
        media_service: YtDlpService,
        ffmpeg_service: FFmpegService,
        worker_count: int = 1,
    ):
        super().__init__()
        self.storage = storage
        self.media_service = media_service
        self.ffmpeg_service = ffmpeg_service
        self.tasks = storage.load_tasks()
        self.worker_count = max(1, min(4, worker_count))
        self.dispatch_paused = False
        self._active: dict[str, threading.Event] = {}
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="media-task")
        self._lock = threading.RLock()
        self._shutting_down = False
        self._progress_received.connect(self._apply_progress)
        self._task_finished.connect(self._finish_task)

    def publish_initial_state(self) -> None:
        """送出初始任務與 queue 狀態"""
        self.tasks_changed.emit(list(self.tasks))
        self.queue_paused_changed.emit(False)
        self._dispatch()

    def add_tasks(self, tasks: Iterable[TaskRecord]) -> None:
        """加入任務並保存"""
        new_tasks = list(tasks)
        if not new_tasks: return
        self.tasks.extend(new_tasks)
        self._save()
        self.tasks_changed.emit(list(self.tasks))
        if not self.dispatch_paused: self._dispatch()

    def set_worker_count(self, count: int) -> None:
        """限制同時執行的 worker 數量"""
        self.worker_count = max(1, min(4, count))
        self.worker_count_changed.emit(self.worker_count)
        if not self.dispatch_paused: self._dispatch()

    def start_queue(self) -> None:
        """開始派發 pending 任務"""
        self.dispatch_paused = False
        self.queue_paused_changed.emit(False)
        self._dispatch()

    def pause_queue(self) -> None:
        """停止派發新任務, 不影響執行中任務"""
        self.dispatch_paused = True
        self.queue_paused_changed.emit(True)

    def cancel_tasks(self, task_ids: Iterable[str]) -> None:
        """取消 running 或 pending 任務"""
        changed = False
        for task_id in task_ids:
            task = self._find(task_id)
            if not task or task.status not in {TaskStatus.PENDING, TaskStatus.RUNNING}: continue
            if task.status is TaskStatus.RUNNING:
                event = self._active.get(task.id)
                if event: event.set()
            else:
                self._set_status(task, TaskStatus.CANCELLED, error="Cancelled")
                changed = True
        if changed:
            self._save()
            self.tasks_changed.emit(list(self.tasks))

    def retry_tasks(self, task_ids: Iterable[str]) -> None:
        """將 paused、failed 或 cancelled 任務放回 queue"""
        changed = False
        for task_id in task_ids:
            task = self._find(task_id)
            if not task or task.status not in {TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.CANCELLED}: continue
            task.status = TaskStatus.PENDING
            task.progress = 0.0
            task.error = ""
            self._touch(task)
            changed = True
        if not changed: return
        self._save()
        self.tasks_changed.emit(list(self.tasks))
        if not self.dispatch_paused: self._dispatch()

    def remove_tasks(self, task_ids: Iterable[str]) -> None:
        """移除非 running 任務"""
        selected = set(task_ids)
        remaining = [task for task in self.tasks if task.id not in selected or task.status is TaskStatus.RUNNING]
        if len(remaining) == len(self.tasks): return
        self.tasks = remaining
        self._save()
        self.tasks_changed.emit(list(self.tasks))

    def move_tasks(self, task_ids: Iterable[str], direction: int) -> None:
        """移動 pending 任務, direction 使用 -1 或 1"""
        selected = set(task_ids)
        indexes = range(1, len(self.tasks)) if direction < 0 else range(len(self.tasks) - 2, -1, -1)
        changed = False
        for index in indexes:
            next_index = index + direction
            task = self.tasks[index]
            neighbor = self.tasks[next_index]
            if task.id not in selected or task.status is not TaskStatus.PENDING: continue
            if neighbor.status is not TaskStatus.PENDING: continue
            self.tasks[index], self.tasks[next_index] = neighbor, task
            changed = True
        if not changed: return
        self._save()
        self.tasks_changed.emit(list(self.tasks))

    def shutdown(self) -> None:
        """停止 worker 並保存可恢復狀態"""
        self._shutting_down = True
        self.dispatch_paused = True
        for event in self._active.values(): event.set()
        for task in self.tasks:
            if task.status is TaskStatus.RUNNING:
                self._set_status(task, TaskStatus.PAUSED, error="Interrupted when application closed")
        self._save()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _dispatch(self) -> None:
        """依照 queue 順序填滿可用 worker"""
        if self._shutting_down or self.dispatch_paused: return
        with self._lock:
            while len(self._active) < self.worker_count:
                task = next((item for item in self.tasks if item.status is TaskStatus.PENDING), None)
                if task is None: break
                cancel_event = threading.Event()
                self._active[task.id] = cancel_event
                self._set_status(task, TaskStatus.RUNNING)
                self.task_updated.emit(task)
                self._executor.submit(self._run_task, task, cancel_event)
        self._save()
        self.tasks_changed.emit(list(self.tasks))

    def _run_task(self, task: TaskRecord, cancel_event: threading.Event) -> None:
        """在背景 thread 執行下載或轉檔"""
        try:
            progress = lambda value, detail="": self._progress_received.emit(task.id, float(value), str(detail))
            log = lambda message: self.log_message.emit(str(message))
            if task.kind.value == "download" and task.download_options is not None:
                output = self.media_service.execute_download(task, progress, log, cancel_event)
            elif task.kind.value == "subtitle" and task.subtitle_options is not None:
                output = self.media_service.execute_subtitle(task, progress, log, cancel_event)
            elif task.kind.value == "conversion" and task.conversion_options is not None:
                output = self.ffmpeg_service.execute_conversion(task, progress, log, cancel_event)
            elif task.kind.value == "replacement" and task.replacement_options is not None:
                output = self.ffmpeg_service.execute_replacement(task, progress, log, cancel_event)
            else:
                raise ValueError("Task has no valid payload")
            self._task_finished.emit(task.id, "completed", output or "")
        except ServiceCancelled:
            self._task_finished.emit(task.id, "cancelled", "Cancelled")
        except Exception as error:
            logging.getLogger(__name__).exception("Task failed: %s", task.title)
            self._task_finished.emit(task.id, "failed", summarize_task_error(error))

    @Slot(str, float, str)
    def _apply_progress(self, task_id: str, value: float, detail: str) -> None:
        task = self._find(task_id)
        if not task or task.status is not TaskStatus.RUNNING: return
        task.progress = max(-1.0, min(1.0, value))
        self._touch(task)
        self.task_updated.emit(task)

    @Slot(str, str, str)
    def _finish_task(self, task_id: str, result: str, detail: str) -> None:
        with self._lock:
            self._active.pop(task_id, None)
        if self._shutting_down: return
        task = self._find(task_id)
        if not task: return
        if result == "completed":
            task.output_path = detail
            task.progress = 1.0
            self._set_status(task, TaskStatus.COMPLETED)
        elif result == "cancelled":
            self._set_status(task, TaskStatus.CANCELLED, error=detail)
        else:
            self._set_status(task, TaskStatus.FAILED, error=detail)
        self._save()
        self.task_updated.emit(task)
        self.tasks_changed.emit(list(self.tasks))
        self._dispatch()

    def _find(self, task_id: str) -> TaskRecord | None:
        return next((task for task in self.tasks if task.id == task_id), None)

    def _save(self) -> None:
        try:
            self.storage.save_tasks(self.tasks)
        except Exception:
            logging.getLogger(__name__).exception("Unable to save task queue")

    @staticmethod
    def _touch(task: TaskRecord) -> None:
        task.updated_at = datetime.now(UTC).isoformat()

    @classmethod
    def _set_status(cls, task: TaskRecord, status: TaskStatus, error: str = "") -> None:
        task.status = status
        task.error = error
        cls._touch(task)
