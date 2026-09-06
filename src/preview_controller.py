from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, QStandardPaths, QTimer, Signal, Slot
from PySide6.QtGui import QColor

from ffmpeg_service import FFmpegService
from media_service import ServiceCancelled
from models import ReplacementOptions
from theme import theme_color


class PreviewCache:
    """管理本次執行使用的 preview 暫存目錄"""

    def __init__(self, root: str | Path | None = None):
        if root is None:
            location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
            root = Path(location) / "previews" if location else Path.home() / ".cache" / "MochiStar" / "previews"
        self.root = Path(root).resolve()
        self._clear_children()
        self.session_dir = self.root / str(uuid4())
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def new_asset_path(self, track: str) -> Path:
        """建立縮圖或波形圖片路徑"""
        return self.session_dir / f"{track}-{uuid4()}.png"

    def cleanup(self) -> None:
        """清除本次 session 與空的 preview 根目錄"""
        if self.session_dir.parent == self.root: shutil.rmtree(self.session_dir, ignore_errors=True)
        try:
            self.root.rmdir()
        except OSError:
            pass

    def _clear_children(self) -> None:
        """啟動時清除先前異常結束留下的 preview session"""
        self.root.mkdir(parents=True, exist_ok=True)
        for child in self.root.iterdir():
            if child.is_dir(): shutil.rmtree(child, ignore_errors=True)
            elif child.is_file(): child.unlink(missing_ok=True)


class PreviewController(QObject):
    """由播放頭向兩側持續建立低解析度 RAM preview segments"""

    DEBOUNCE_MS = 300
    MAXIMUM_BYTES = 256 * 1024 * 1024
    preview_ready = Signal(object, float, float, float)
    preview_failed = Signal(str)
    cache_reset = Signal()
    cache_ranges_changed = Signal(object)
    cache_segments_changed = Signal(object)
    asset_ready = Signal(str, str, str)
    _result_received = Signal(str, object, float, float, float, str)

    def __init__(self, service: FFmpegService, cache: PreviewCache | None = None):
        super().__init__()
        self.service = service
        self.cache = cache or PreviewCache()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="replacement-preview")
        self._asset_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="replacement-assets")
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self.DEBOUNCE_MS)
        self._debounce.timeout.connect(self._start_pending)
        self._pending: list[tuple[str, ReplacementOptions, dict, dict, float, float, float]] = []
        self._active: dict[str, tuple[float, float, threading.Event]] = {}
        self._cache_key = ""
        self._cached_segments: list[tuple[float, float, int]] = []
        self._cached_bytes = 0
        self._maximum_bytes = self.MAXIMUM_BYTES
        self._maximum_segments = 0
        self._memory_saturated = False
        self._segment_duration = 20.0
        self._lead_in = 10.0
        self._context: tuple[ReplacementOptions, dict, dict, float] | None = None
        self._shutting_down = False
        self._result_received.connect(self._apply_result)

    def schedule(
        self, options: ReplacementOptions, visual_probe: dict, audio_probe: dict,
        playhead: float, segment_duration: float, lead_in: float, maximum_bytes: int,
        maximum_segments: int = 0,
    ) -> None:
        """優先快取 playhead, 完成後持續往兩側延伸"""
        if self._shutting_down or options.timeline is None: return
        snapshot = ReplacementOptions.from_dict(options.to_dict())
        cache_values = snapshot.to_dict()
        cache_values["timeline"].pop("output_in", None)
        cache_values["timeline"].pop("output_out", None)
        cache_key = json.dumps(cache_values, sort_keys=True, ensure_ascii=True)
        if cache_key != self._cache_key:
            self.invalidate()
            self._cache_key = cache_key
        previous_maximum = self._maximum_bytes
        self._maximum_bytes = max(1, int(maximum_bytes))
        self._maximum_segments = max(0, int(maximum_segments))
        if self._maximum_bytes > previous_maximum: self._memory_saturated = False
        self._segment_duration = max(0.1, float(segment_duration))
        self._lead_in = max(0.0, float(lead_in))
        output_in, output_out = snapshot.timeline.output_in, snapshot.timeline.output_out
        if output_out - output_in < 0.001:
            self.preview_failed.emit("Preview range must have a positive duration")
            return
        focus = min(max(float(playhead), output_in), output_out)
        self._context = (snapshot, dict(visual_probe), dict(audio_probe), focus)
        if self._evict_distant(focus): self._memory_saturated = True
        if self._range_containing(focus) is None:
            self.cancel()
            self._memory_saturated = False
            self._evict_distant(focus, reserve_segments=1)
        self._fill_workers(True)

    def invalidate(self) -> None:
        """取消工作並清除所有 RAM cache metadata"""
        self.cancel()
        self._cache_key = ""
        self._cached_segments.clear()
        self._cached_bytes = 0
        self._memory_saturated = False
        self._context = None
        self.cache_reset.emit()
        self.cache_ranges_changed.emit([])
        self.cache_segments_changed.emit([])

    def _queue_range(self, window_start: float, window_end: float) -> None:
        if self._context is None or window_end - window_start < 0.001: return
        options, visual_probe, audio_probe, focus = self._context
        generation = str(uuid4())
        segment = ReplacementOptions.from_dict(options.to_dict())
        segment.timeline.output_in, segment.timeline.output_out = window_start, window_end
        self._pending.append((
            generation, segment, dict(visual_probe), dict(audio_probe), focus, window_start, window_end,
        ))

    def _fill_workers(self, debounce: bool = False) -> None:
        """先建立包含 playhead 的單一 segment, 後續向右延伸"""
        if self._context is None or self._memory_saturated: return
        options, _visual_probe, _audio_probe, focus = self._context
        output_in, output_out = options.timeline.output_in, options.timeline.output_out
        available = max(0, 2 - len(self._active) - len(self._pending))
        if self._maximum_segments:
            retained = len(self._cached_segments) + len(self._active) + len(self._pending)
            available = min(available, max(0, self._maximum_segments - retained))
        for _slot in range(available):
            coverage_ranges = self._coverage_ranges()
            coverage = self._range_containing(focus, coverage_ranges)
            if coverage is None:
                duration = min(self._segment_duration, output_out - output_in)
                start = min(max(focus - min(self._lead_in, duration), output_in), output_out - duration)
                self._queue_range(start, start + duration)
                continue
            if coverage[1] >= output_out - 0.001: break
            right_bound = min(
                (start for start, _end in coverage_ranges if start > coverage[1] + 0.001),
                default=output_out,
            )
            self._queue_range(coverage[1], min(right_bound, coverage[1] + self._segment_duration))
        if not self._pending: return
        if debounce: self._debounce.start()
        else: self._start_pending()

    @Slot(float)
    def update_focus(self, seconds: float) -> None:
        """只更新 cache 優先位置, 不控制 player 或 playhead"""
        if self._context is None or self._shutting_down: return
        options, visual_probe, audio_probe, _old_focus = self._context
        focus = min(max(float(seconds), options.timeline.output_in), options.timeline.output_out)
        self._context = (options, visual_probe, audio_probe, focus)
        if self._pending or self._active: return
        cached = self._range_containing(focus)
        if cached is None:
            self._memory_saturated = False
            self._evict_distant(focus, reserve_segments=1)
            self._fill_workers()

    def _range_containing(
        self, seconds: float, ranges: list[tuple[float, float]] | None = None,
    ) -> tuple[float, float] | None:
        ranges = self._cached_ranges() if ranges is None else ranges
        return next((item for item in ranges if item[0] - 0.001 <= seconds <= item[1] + 0.001), None)

    def _cached_ranges(self) -> list[tuple[float, float]]:
        return self._merged_ranges([(start, end) for start, end, _size in self._cached_segments])

    def _coverage_ranges(self) -> list[tuple[float, float]]:
        """合併完成、等待與執行中的區段, 避免兩個 workers 重複工作"""
        ranges = [(start, end) for start, end, _size in self._cached_segments]
        ranges += [(item[-2], item[-1]) for item in self._pending]
        ranges += [(start, end) for start, end, _event in self._active.values()]
        return self._merged_ranges(ranges)

    @staticmethod
    def _merged_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
        ranges = sorted(ranges)
        merged: list[tuple[float, float]] = []
        for item in ranges:
            if merged and item[0] <= merged[-1][1] + 0.001:
                merged[-1] = (merged[-1][0], max(merged[-1][1], item[1]))
            else:
                merged.append(item)
        return merged

    def _evict_distant(self, focus: float, reserve_segments: int = 0) -> bool:
        """超過上限時優先逐出離 playhead 最遠的 segments"""
        evicted = False
        memory_exceeded = self._cached_bytes > self._maximum_bytes
        count_limit = max(0, self._maximum_segments - reserve_segments)
        while self._cached_segments and (
            self._cached_bytes > self._maximum_bytes
            or self._maximum_segments > 0 and len(self._cached_segments) > count_limit
        ):
            farthest = max(
                range(len(self._cached_segments)),
                key=lambda index: 0.0
                if self._cached_segments[index][0] <= focus <= self._cached_segments[index][1]
                else min(abs(focus - self._cached_segments[index][0]), abs(focus - self._cached_segments[index][1])),
            )
            _start, _end, size = self._cached_segments.pop(farthest)
            self._cached_bytes -= size
            evicted = True
        ranges = self._cached_ranges()
        self.cache_ranges_changed.emit(ranges)
        self.cache_segments_changed.emit([(start, end) for start, end, _size in self._cached_segments])
        return memory_exceeded and evicted

    @Slot()
    def _start_pending(self) -> None:
        if not self._pending or self._shutting_down: return
        pending, self._pending = self._pending, []
        for generation, options, visual_probe, audio_probe, focus, window_start, window_end in pending:
            cancel_event = threading.Event()
            self._active[generation] = (window_start, window_end, cancel_event)
            self._executor.submit(
                self._run, generation, options, visual_probe, audio_probe,
                focus, window_start, window_end, cancel_event,
            )

    def cancel(self) -> None:
        """取消 debounce 與目前 FFmpeg 工作, 保留已完成 cache"""
        self._debounce.stop()
        self._pending.clear()
        for _start, _end, cancel_event in self._active.values(): cancel_event.set()

    def generate_asset(self, track: str, path: str, probe: dict) -> None:
        """在背景建立固定寬度的影片縮圖列或音訊波形"""
        if track not in {"visual", "audio"} or not path: return
        output = self.cache.new_asset_path(track)
        self._asset_executor.submit(self._run_asset, track, path, dict(probe), output)

    def shutdown(self) -> None:
        """停止 worker 後清除本次 preview cache"""
        self._shutting_down = True
        self.invalidate()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._asset_executor.shutdown(wait=True, cancel_futures=True)
        self.cache.cleanup()

    def _run(
        self, generation: str, options: ReplacementOptions, visual_probe: dict, audio_probe: dict,
        focus: float, window_start: float, window_end: float, cancel_event: threading.Event,
    ) -> None:
        error = ""
        data = b""
        try:
            command = self.service.build_preview_command(options, visual_probe, audio_probe)
            duration = window_end - window_start
            data = self.service.execute_preview_to_memory(
                command, duration,
                lambda _value, _detail: None,
                lambda message: logging.getLogger(__name__).debug("Preview FFmpeg: %s", message),
                cancel_event, self._maximum_bytes,
            )
            if cancel_event.is_set(): raise ServiceCancelled("Preview cancelled")
        except ServiceCancelled:
            error = "cancelled"
        except Exception as exception:
            logging.getLogger(__name__).exception("Preview generation failed")
            error = str(exception)
        self._result_received.emit(
            generation, data if not error else b"", window_start, window_end, focus, error,
        )

    def _run_asset(self, track: str, source: str, probe: dict, output: Path) -> None:
        """使用 FFmpeg 產生可重複繪製的 timeline overview"""
        ffmpeg_path = getattr(self.service, "ffmpeg_path", "")
        if self._shutting_down or not ffmpeg_path: return
        if track == "visual":
            try: duration = max(0.1, float(probe.get("duration") or (probe.get("format") or {}).get("duration") or 5))
            except (TypeError, ValueError): duration = 5.0
            filters = (
                f"fps=12/{duration:g},scale=160:90:force_original_aspect_ratio=decrease,"
                "pad=160:90:(ow-iw)/2:(oh-ih)/2:black,tile=12x1"
            )
            command = [
                ffmpeg_path, "-hide_banner", "-loglevel", "error", "-i", source,
                "-vf", filters, "-frames:v", "1", "-y", str(output),
            ]
        else:
            waveform_color = QColor(theme_color("accent")).darker(160).name()
            gain = self._waveform_gain(source)
            command = [
                ffmpeg_path, "-hide_banner", "-loglevel", "error", "-i", source,
                "-filter_complex", (
                    f"aformat=channel_layouts=mono,volume={gain:g}dB,"
                    f"showwavespic=s=1920x96:colors={waveform_color}:scale=lin"
                ),
                "-frames:v", "1", "-y", str(output),
            ]
        try:
            result = self.service.run_command(
                command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                **self.service._window_flags(),
            )
            if result.returncode != 0: raise RuntimeError(result.stderr.strip() or "FFmpeg asset generation failed")
            if not self._shutting_down and output.is_file(): self.asset_ready.emit(track, source, str(output))
        except Exception:
            output.unlink(missing_ok=True)
            logging.getLogger(__name__).exception("Timeline asset generation failed for %s", source)

    def _waveform_gain(self, source: str) -> float:
        """偵測整段音訊 peak, 計算只供波形圖片使用的線性增益"""
        try:
            result = self.service.run_command(
                [
                    self.service.ffmpeg_path, "-hide_banner", "-nostats", "-i", source,
                    "-map", "0:a:0", "-af", "aformat=channel_layouts=mono,volumedetect", "-f", "null", "-",
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace", **self.service._window_flags(),
            )
        except Exception:
            logging.getLogger(__name__).debug("Waveform peak detection failed for %s", source, exc_info=True)
            return 0.0
        match = re.search(r"max_volume:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*dB", result.stderr or "", re.IGNORECASE)
        if result.returncode != 0 or match is None or match.group(1).lower() == "-inf": return 0.0
        return min(96.0, max(0.0, -float(match.group(1))))

    @Slot(str, object, float, float, float, str)
    def _apply_result(
        self, generation: str, data: bytes, window_start: float,
        window_end: float, focus: float, error: str,
    ) -> None:
        if generation not in self._active or self._shutting_down: return
        self._active.pop(generation)
        if data:
            live_focus = self._context[3] if self._context is not None else focus
            self._cached_bytes += len(data)
            completed = (window_start, window_end, len(data))
            self._cached_segments.append(completed)
            evicted = self._evict_distant(live_focus)
            if evicted or self._cached_bytes >= self._maximum_bytes: self._memory_saturated = True
            if self._range_containing(live_focus) is None:
                self._memory_saturated = False
                self._evict_distant(live_focus, reserve_segments=1)
            if completed in self._cached_segments:
                self.preview_ready.emit(data, window_start, window_end, focus)
        elif error != "cancelled":
            if "memory limit" not in error.lower(): self.preview_failed.emit(error)
        self._fill_workers()
