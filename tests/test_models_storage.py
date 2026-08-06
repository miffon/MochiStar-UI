from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from models import (
    ConversionOptions,
    ConversionPreset,
    CookieConfig,
    DownloadOptions,
    FormatInfo,
    MediaInfo,
    ReplacementOptions,
    SubtitleOptions,
    SubtitleSelection,
    SubtitleTrack,
    TaskKind,
    TaskRecord,
    TaskStatus,
)
from storage import SCHEMA_VERSION, AppStorage, Settings
from version import __version__

TEST_PATH = Path("test-data")


def test_models_round_trip_nested_payloads_and_ignore_extra_fields() -> None:
    media = MediaInfo(
        media_id="playlist",
        title="Playlist",
        entries=[MediaInfo(media_id="video", title="Video")],
        formats=[FormatInfo("137", "mp4", "1080p", 30, "h264", "none", 2048, 1920, 1080)],
        subtitles=[SubtitleTrack("en", "English", "manual", ["vtt", "srt"])],
    )
    media_data = {**media.to_dict(), "future_field": True}
    assert MediaInfo.from_dict(media_data) == media

    options = DownloadOptions(
        url="https://example.test/video",
        output_dir=str(TEST_PATH),
        playlist_item_ids=["one", "two"],
        cookie=CookieConfig(source="browser", browser="firefox", profile="default"),
    )
    task = TaskRecord(title="Video", progress=0.425, download_options=options)
    restored = TaskRecord.from_dict({**task.to_dict(), "future_field": {"ignored": True}})
    assert restored.id == task.id
    assert restored.kind is TaskKind.DOWNLOAD
    assert restored.download_options == options
    assert isinstance(restored.created_at, datetime)

    subtitle_options = SubtitleOptions(
        url="https://example.test/video",
        output_dir=str(TEST_PATH),
        selections=[SubtitleSelection("en", "manual", "vtt"), SubtitleSelection("ja", "automatic", "best")],
    )
    subtitle_task = TaskRecord(title="Subtitles", subtitle_options=subtitle_options)
    restored_subtitle = TaskRecord.from_dict(subtitle_task.to_dict())
    assert restored_subtitle.kind is TaskKind.SUBTITLE
    assert restored_subtitle.subtitle_options == subtitle_options


def test_conversion_task_infers_kind_and_defaults_are_not_shared() -> None:
    first = TaskRecord(conversion_options=ConversionOptions(input_path="clip.mov"))
    second = TaskRecord(download_options=DownloadOptions())
    first.conversion_options.encoder = "h264_nvenc"
    second.download_options.playlist_item_ids.append("one")

    assert first.kind is TaskKind.CONVERSION
    assert second.kind is TaskKind.DOWNLOAD
    assert first.id != second.id
    assert DownloadOptions().playlist_item_ids == []

    advanced = ConversionOptions(
        input_path="clip.mp4", target_format="mov", encoder="", video_codec="prores",
        prores_profile="hq", resolution_height=1080, allow_upscale=True, fps="24000/1001",
        quality_mode="vbr_2pass", quality_value=18, maximum_bitrate=30, gop=60, h264_profile="high",
        pixel_format="yuv420p", audio_codec="pcm_s24le", audio_sample_rate=96000, acceleration="nvidia",
    )
    assert ConversionOptions.from_dict(advanced.to_dict()) == advanced
    legacy = ConversionOptions.from_dict({"encoder": "h264_qsv"})
    assert legacy.acceleration == "intel" and legacy.encoder == ""
    legacy_quality = ConversionOptions.from_dict({"quality_mode": "bitrate", "quality_value": 12})
    assert legacy_quality.quality_mode == "vbr" and legacy_quality.quality_value == 12
    migrated_auto = ConversionOptions.from_dict({"quality_mode": "auto"})
    assert migrated_auto.quality_mode == "vbr" and migrated_auto.quality_value == 7.5


def test_replacement_task_round_trip_keeps_complete_timeline_snapshot() -> None:
    options = ReplacementOptions(
        visual_path="picture.gif", audio_path="music.wav", duration_mode="custom", custom_duration=12.5,
        visual_loop=True, audio_loop=True, visual_delay=0.25, audio_delay=-0.5,
        trim_start=1.25, trim_end=0.75, aspect_ratio="9:16", fit_mode="cover", force_reencode=True,
        conversion=ConversionOptions(
            output_dir="output", target_format="mov", video_codec="prores", prores_profile="hq",
            audio_codec="pcm_s24le", audio_sample_rate=48000,
        ),
    )
    task = TaskRecord(title="replace", replacement_options=options)
    restored = TaskRecord.from_dict({**task.to_dict(), "future_field": True})

    assert task.kind is TaskKind.REPLACEMENT
    assert restored.kind is TaskKind.REPLACEMENT
    assert restored.replacement_options == options
    assert restored.payload == options


def test_from_dict_is_robust_to_invalid_and_legacy_values() -> None:
    cookie = CookieConfig.from_dict({
        "mode": "cookiefile",
        "path": "cookies.txt",
        "content": "must never survive",
        "future": "ignored",
    })
    task = TaskRecord.from_dict({
        "kind": "unknown",
        "status": "unknown",
        "progress": "not-a-number",
        "created_at": "invalid",
        "payload": {"url": "https://example.test"},
    })

    assert cookie == CookieConfig(source="file", file_path="cookies.txt")
    assert "content" not in cookie.to_dict()
    assert task.kind is TaskKind.DOWNLOAD
    assert task.status is TaskStatus.PENDING
    assert task.progress is None
    assert task.download_options.url == "https://example.test"


def test_task_progress_uses_ratio_and_negative_value_is_indeterminate() -> None:
    assert TaskRecord(progress=0.25).progress == 0.25
    assert TaskRecord(progress=25).progress == 1.0
    assert TaskRecord(progress=-0.25).progress == -1.0
    assert TaskRecord.from_dict({}).progress == 0.0


def test_settings_round_trip_clamps_worker_count_and_encodes_binary() -> None:
    settings = Settings(
        output_dir=str(TEST_PATH),
        cookie=CookieConfig(source="file", file_path="cookies.txt"),
        last_conversion_output_dir=str(TEST_PATH),
        last_conversion_format="mkv",
        last_conversion_mode="remux",
        last_conversion_encoder="h264_nvenc",
        last_conversion_preset_id="work-proxy",
        last_conversion_acceleration="nvidia",
        conversion_presets=[ConversionPreset(
            id="work-proxy", name="ProRes Proxy", target_format="mov", video_codec="prores",
            audio_sample_rate=48000,
        )],
        theme_name="cute_light",
        experimental_custom_title_bar=True,
        manual_ffmpeg_enabled=True,
        ffmpeg_bin_dir="C:/tools/ffmpeg/bin",
        manual_js_runtime_enabled=True,
        js_runtime_bin_dir="C:/tools/deno/bin",
        ignored_missing_dependencies=["ffmpeg", "js_runtime"],
        auto_check_updates=False,
        include_automatic_subtitles=False,
        last_update_check_at="2026-07-29T00:00:00+00:00",
        worker_count=99,
        download_column_widths=[70, 300, 100, 180],
        subtitle_column_widths=[70, 240, 90, 120, 90, 100],
        queue_column_widths=[70, 200, 250, 90, 80, 220, 180],
        conversion_splitter_sizes=[430, 710],
        replacement_settings={"output_dir": str(TEST_PATH), "duration_mode": "shortest", "audio_delay": 0.5},
        replacement_splitter_sizes=[600, 600],
        geometry=b"\x00geometry",
        window_state=b"\xffstate",
    )
    data = settings.to_dict()
    restored = Settings.from_dict(data)

    assert settings.worker_count == 4
    assert restored == settings
    assert isinstance(data["geometry"], str)
    assert Settings.from_dict({"worker_count": 0, "geometry": "invalid!"}).worker_count == 1
    assert Settings.from_dict({"geometry": "invalid!"}).geometry == b""
    assert Settings.from_dict({"queue_column_widths": [1, 3000, 100, 100, 100, 100, 100]}).queue_column_widths == [
        60, 2000, 100, 100, 100, 100, 100,
    ]
    assert Settings.from_dict({"queue_column_widths": [100, "bad"]}).queue_column_widths == []
    assert Settings.from_dict({"download_column_widths": [1, 3000, 100, 100]}).download_column_widths == [
        48, 2000, 100, 100,
    ]
    assert Settings.from_dict({"subtitle_column_widths": [100, 100]}).subtitle_column_widths == []
    assert Settings.from_dict({"conversion_splitter_sizes": [20, 9000]}).conversion_splitter_sizes == [100, 5000]
    assert Settings.from_dict({"conversion_splitter_sizes": [500]}).conversion_splitter_sizes == []
    assert Settings.from_dict({}).theme_name == "cute_light"
    assert Settings.from_dict({"theme_name": "modern_dark"}).theme_name == "starlit_night"
    assert Settings.from_dict({}).experimental_custom_title_bar is False
    assert Settings.from_dict({"experimental_custom_title_bar": 1}).experimental_custom_title_bar is False
    assert Settings.from_dict({"experimental_extended_title_bar": True}).experimental_custom_title_bar is True
    assert Settings.from_dict({}).language == "zh_TW"
    assert Settings.from_dict({}).auto_check_updates is True
    assert Settings.from_dict({"ignored_missing_dependencies": ["bad", "js_runtime"]}).ignored_missing_dependencies == [
        "js_runtime",
    ]
    assert Settings.from_dict({"last_conversion_encoder": "h264_amf"}).last_conversion_acceleration == "amd"
    legacy = Settings.from_dict({
        "output_dir": "C:/Downloads", "cookie": {"source": "browser", "browser": "firefox"},
    })
    assert legacy.output_dir == "C:/Downloads"
    assert legacy.cookie == CookieConfig(source="browser", browser="firefox")
    subtitle_legacy = Settings.from_dict({
        "subtitle_output_dir": "C:/Subtitles",
        "subtitle_cookie": {"source": "browser", "browser": "firefox"},
    })
    assert subtitle_legacy.output_dir == "C:/Subtitles"
    assert subtitle_legacy.cookie == CookieConfig(source="browser", browser="firefox")
    assert Settings.from_dict({"conversion_presets": [
        {"name": "Default"}, {"name": "Work"}, {"name": "work"}, {"name": ""},
    ]}).conversion_presets[0].name == "Work"
    migrated = Settings.from_dict({
        "last_conversion_preset_ids": {"video": "legacy-video", "audio": "legacy-audio"},
        "conversion_presets": [
            {"id": "video", "name": "Video", "target_format": "mov"},
            {"id": "audio", "name": "Audio", "media_type": "audio", "target_format": "flac"},
        ],
    })
    assert migrated.last_conversion_preset_id == "legacy-video"
    assert [preset.id for preset in migrated.conversion_presets] == ["video"]


def test_runtime_version_matches_project_metadata() -> None:
    import tomllib

    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert __version__ == project["project"]["version"]


def test_storage_filters_completed_tasks_and_recovers_running(tmp_path: Path) -> None:
    storage = AppStorage(tmp_path)
    running = TaskRecord(
        title="Running",
        status=TaskStatus.RUNNING,
        download_options=DownloadOptions(url="https://example.test", output_dir=str(tmp_path)),
    )
    failed = TaskRecord(title="Failed", status=TaskStatus.FAILED, error="network")
    completed = TaskRecord(title="Completed", status=TaskStatus.COMPLETED)

    assert storage.save_tasks([running, failed, completed])
    document = json.loads((tmp_path / "tasks.json").read_text(encoding="utf-8"))
    assert document["schema_version"] == SCHEMA_VERSION
    assert [task["title"] for task in document["tasks"]] == ["Running", "Failed"]

    restored = storage.load_tasks()
    assert [task.status for task in restored] == [TaskStatus.PAUSED, TaskStatus.FAILED]
    assert "Interrupted" in restored[0].error


def test_storage_settings_round_trip_never_persists_cookie_content(tmp_path: Path) -> None:
    storage = AppStorage(tmp_path)
    settings = Settings(
        output_dir=str(tmp_path),
        cookie=CookieConfig(source="browser", browser="firefox", profile="default-release"),
        worker_count=3,
        geometry=b"geometry",
    )

    assert storage.save_settings(settings)
    raw = (tmp_path / "settings.json").read_text(encoding="utf-8")
    restored = storage.load_settings()

    assert restored == settings
    assert "cookie content" not in raw
    assert set(json.loads(raw)["settings"]["cookie"]) == {"source", "browser", "profile", "file_path"}
    assert "subtitle_cookie" not in json.loads(raw)["settings"]


def test_storage_corruption_version_and_invalid_output_fall_back(tmp_path: Path, caplog) -> None:
    storage = AppStorage(tmp_path)
    (tmp_path / "settings.json").write_text("{broken", encoding="utf-8")
    assert storage.load_settings() == Settings()

    (tmp_path / "tasks.json").write_text(
        json.dumps({"schema_version": SCHEMA_VERSION + 1, "tasks": [{"status": "running"}]}),
        encoding="utf-8",
    )
    assert storage.load_tasks() == []

    missing = tmp_path / "missing"
    (tmp_path / "settings.json").write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "settings": {"output_dir": str(missing)}}),
        encoding="utf-8",
    )
    assert storage.load_settings().output_dir != str(missing)
    assert "using defaults" in caplog.text or "unavailable" in caplog.text
