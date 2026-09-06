from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from models import ReplacementClipTiming, ReplacementOptions, ReplacementTimeline
from preview_controller import PreviewCache, PreviewController


def _wait(app, condition, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition(): return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for preview result")


class FakePreviewService:
    ffmpeg_path = "ffmpeg"
    calls = 0

    def build_preview_command(self, options, _visual_probe, _audio_probe, output=None):
        return ["ffmpeg", str(options.timeline.output_in), str(options.timeline.output_out)]

    def execute_preview_to_memory(
        self, command, _duration, progress_cb, _log_cb, _cancel_event, _maximum_bytes,
    ):
        self.calls += 1
        progress_cb(1.0, "progress=end")
        return "|".join(command).encode()


def _options() -> ReplacementOptions:
    timing = ReplacementClipTiming(0, 2, 0, 2)
    return ReplacementOptions(
        visual_path="visual.mp4", audio_path="audio.wav",
        timeline=ReplacementTimeline(timing, ReplacementClipTiming.from_dict(timing.to_dict()), 0, 2),
    )


def _long_options(duration: float = 8) -> ReplacementOptions:
    timing = ReplacementClipTiming(0, duration, 0, duration)
    return ReplacementOptions(
        visual_path="visual.mp4", audio_path="audio.wav",
        timeline=ReplacementTimeline(timing, ReplacementClipTiming.from_dict(timing.to_dict()), 0, duration),
    )


def test_preview_cache_removes_crash_residue_and_current_session(tmp_path: Path) -> None:
    root = tmp_path / "previews"
    residue = root / "old-session"
    residue.mkdir(parents=True)
    (residue / "preview.mp4").touch()

    cache = PreviewCache(root)

    assert not residue.exists()
    assert cache.session_dir.is_dir()
    cache.cleanup()
    assert not root.exists()


def test_preview_controller_builds_clamped_window_in_memory(app, tmp_path: Path) -> None:
    cache = PreviewCache(tmp_path / "previews")
    service = FakePreviewService()
    controller = PreviewController(service, cache)
    ready = []
    controller.preview_ready.connect(lambda *values: ready.append(values))

    controller.schedule(_options(), {}, {}, playhead=1, segment_duration=10, lead_in=10, maximum_bytes=1024 * 1024)
    _wait(app, lambda: len(ready) == 1)

    assert {(start, end) for _data, start, end, _focus in ready} == {(0, 2)}
    assert all(focus == 1 for _data, _start, _end, focus in ready)
    assert service.calls == 1
    controller.shutdown()
    assert not cache.root.exists()


def test_preview_controller_debounce_keeps_only_latest_request(app, tmp_path: Path) -> None:
    service = FakePreviewService()
    controller = PreviewController(service, PreviewCache(tmp_path / "previews"))
    ready = []
    controller.preview_ready.connect(lambda *values: ready.append(values))

    controller.schedule(_options(), {}, {}, playhead=0.5, segment_duration=0.5, lead_in=10, maximum_bytes=1024 * 1024)
    controller.schedule(_options(), {}, {}, playhead=1.5, segment_duration=0.5, lead_in=10, maximum_bytes=1024 * 1024)
    _wait(app, lambda: bool(ready))

    assert service.calls >= 2
    assert all(result[3] == 1.5 for result in ready)
    controller.shutdown()


def test_preview_controller_rejects_empty_window_without_starting_worker(app, tmp_path: Path) -> None:
    service = FakePreviewService()
    controller = PreviewController(service, PreviewCache(tmp_path / "previews"))
    errors = []
    controller.preview_failed.connect(errors.append)

    options = _options()
    options.timeline.output_out = 0
    controller.schedule(options, {}, {}, playhead=0, segment_duration=1, lead_in=10, maximum_bytes=1024 * 1024)
    app.processEvents()

    assert errors == ["Preview range must have a positive duration"]
    assert service.calls == 0
    controller.shutdown()


def test_output_range_change_extends_existing_cache_without_reset(app, tmp_path: Path) -> None:
    service = FakePreviewService()
    controller = PreviewController(service, PreviewCache(tmp_path / "previews"))
    controller._debounce.setInterval(0)
    ready, resets = [], []
    controller.preview_ready.connect(lambda *values: ready.append(values))
    controller.cache_reset.connect(lambda: resets.append(True))

    options = _long_options(4)
    options.timeline.output_out = 2
    controller.schedule(options, {}, {}, playhead=1, segment_duration=1, lead_in=10, maximum_bytes=1024 * 1024)
    _wait(app, lambda: bool(ready))
    resets.clear()
    options.timeline.output_out = 4
    controller.schedule(options, {}, {}, playhead=1, segment_duration=1, lead_in=10, maximum_bytes=1024 * 1024)
    _wait(app, lambda: controller._cached_ranges() == [(0, 4)])

    assert not resets
    assert controller._cached_ranges() == [(0, 4)]
    controller.shutdown()


def test_memory_limit_evicts_segments_farthest_from_new_playhead(app, tmp_path: Path) -> None:
    class FixedSizeService(FakePreviewService):
        def execute_preview_to_memory(
            self, _command, _duration, progress_cb, _log_cb, _cancel_event, _maximum_bytes,
        ):
            self.calls += 1
            progress_cb(1.0, "progress=end")
            return b"x" * 10

    service = FixedSizeService()
    controller = PreviewController(service, PreviewCache(tmp_path / "previews"))
    controller._debounce.setInterval(0)
    controller.schedule(_long_options(), {}, {}, playhead=4, segment_duration=1, lead_in=10, maximum_bytes=25)
    _wait(app, lambda: controller._memory_saturated)

    controller.schedule(_long_options(), {}, {}, playhead=7, segment_duration=1, lead_in=10, maximum_bytes=25)
    _wait(app, lambda: controller._range_containing(7) is not None and not controller._active and not controller._pending)

    retained = controller._cached_ranges()
    assert any(start <= 7 <= end for start, end in retained)
    assert controller._cached_bytes <= 25
    assert not any(start <= 2.5 <= end for start, end in retained)
    controller.shutdown()


def test_waveform_gain_normalizes_detected_peak_without_log_scaling(tmp_path: Path) -> None:
    class PeakService(FakePreviewService):
        @staticmethod
        def _window_flags():
            return {}

        @staticmethod
        def run_command(_command, **_kwargs):
            return SimpleNamespace(returncode=0, stderr="[Parsed_volumedetect] max_volume: -24.5 dB")

    controller = PreviewController(PeakService(), PreviewCache(tmp_path / "previews"))

    assert controller._waveform_gain("quiet.wav") == 24.5
    controller.shutdown()


def test_cached_focus_uses_both_workers_to_extend_right(tmp_path: Path) -> None:
    controller = PreviewController(FakePreviewService(), PreviewCache(tmp_path / "previews"))
    controller._context = (_long_options(), {}, {}, 4)
    controller._segment_duration = 1
    controller._lead_in = 3

    controller._cached_segments = [(3, 5, 1)]
    controller._fill_workers(True)
    assert sorted(item[-2:] for item in controller._pending) == [(5, 6), (6, 7)]

    controller.cancel()
    controller._cached_segments = [(1, 5, 1)]
    controller._fill_workers(True)
    assert sorted(item[-2:] for item in controller._pending) == [(5, 6), (6, 7)]
    controller.shutdown()


def test_initial_segment_places_playhead_at_the_lead_in_offset(tmp_path: Path) -> None:
    controller = PreviewController(FakePreviewService(), PreviewCache(tmp_path / "previews"))
    controller._context = (_long_options(100), {}, {}, 50)
    controller._segment_duration = 20
    controller._lead_in = 10

    controller._fill_workers(True)

    assert sorted(item[-2:] for item in controller._pending) == [(40, 60), (60, 80)]
    controller.shutdown()


def test_preview_block_limit_counts_pending_and_cached_segments(tmp_path: Path) -> None:
    controller = PreviewController(FakePreviewService(), PreviewCache(tmp_path / "previews"))
    controller._context = (_long_options(100), {}, {}, 50)
    controller._segment_duration = 20
    controller._lead_in = 10
    controller._maximum_segments = 1

    controller._fill_workers(True)

    assert [item[-2:] for item in controller._pending] == [(40, 60)]
    controller.shutdown()


def test_preview_block_limit_releases_the_farthest_segment_for_new_focus(tmp_path: Path) -> None:
    controller = PreviewController(FakePreviewService(), PreviewCache(tmp_path / "previews"))
    controller._context = (_long_options(100), {}, {}, 50)
    controller._segment_duration = 20
    controller._lead_in = 10
    controller._maximum_segments = 2
    controller._cached_segments = [(0, 20, 1), (20, 40, 1)]
    controller._cached_bytes = 2

    controller._evict_distant(50, reserve_segments=1)
    controller._fill_workers(True)

    assert controller._cached_segments == [(20, 40, 1)]
    assert [item[-2:] for item in controller._pending] == [(40, 60)]
    controller.shutdown()


def test_cancelled_workers_keep_their_slots_until_they_exit(tmp_path: Path) -> None:
    controller = PreviewController(FakePreviewService(), PreviewCache(tmp_path / "previews"))
    cancel_event = threading.Event()
    controller._active["old"] = (0, 1, cancel_event)

    controller.cancel()

    assert cancel_event.is_set()
    assert "old" in controller._active
    controller._active.clear()
    controller.shutdown()
