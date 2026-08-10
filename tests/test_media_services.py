from __future__ import annotations

import io
import logging
import os
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from ffmpeg_service import FFmpegError, FFmpegService
from media_service import ServiceCancelled, YtDlpService, _CollisionSafeYoutubeDL, _YtDlpLogger
from models import (
    CookieConfig,
    ConversionOptions,
    DownloadOptions,
    ReplacementOptions,
    SubtitleOptions,
    SubtitleSelection,
    TaskKind,
    TaskRecord,
)

TEST_PATH = Path("test-data")


class FakeYdl:
    def __init__(self, options: dict, info: dict, captured: list[dict]):
        self.options = options
        self.info = info
        captured.append(options)

    def __enter__(self) -> FakeYdl:
        return self

    def __exit__(self, *_args) -> None:
        return None

    def extract_info(self, _url: str, download: bool = False) -> dict:
        if download:
            for hook in self.options.get("progress_hooks", []):
                hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100, "filename": "partial.webm"})
                hook({"status": "finished", "filename": "partial.webm"})
        elif self.info.get("entries"):
            total = len(self.info["entries"])
            for index in range(total): self.options["logger"].debug(f"[download] Downloading item {index + 1} of {total}")
        return self.info

    def prepare_filename(self, _info: dict) -> str:
        return "prepared.webm"


def ydl_factory(info: dict) -> tuple[object, list[dict]]:
    captured: list[dict] = []

    def factory(options: dict) -> FakeYdl:
        return FakeYdl(options, info, captured)

    return factory, captured


def test_analyze_maps_playlist_formats_and_cookie() -> None:
    info = {
        "id": "playlist-1",
        "title": "Playlist",
        "extractor_key": "YoutubeTab",
        "entries": [{
            "id": "video-1",
            "title": "Video",
            "uploader": "Uploader",
            "duration": 12,
            "webpage_url": "https://example.test/watch/1",
            "formats": [{
                "format_id": "137",
                "ext": "mp4",
                "width": 1920,
                "height": 1080,
                "fps": 30,
                "vcodec": "avc1",
                "acodec": "none",
                "filesize_approx": 2048,
            }],
            "subtitles": {"en": [{"ext": "vtt", "name": "English"}, {"ext": "srt"}]},
            "automatic_captions": {"zh-Hant": [{"ext": "vtt", "name": "Chinese"}]},
        }],
    }
    factory, captured = ydl_factory(info)
    service = YtDlpService(factory, which=lambda name: "C:/bin/deno.exe" if name == "deno" else None)

    progress = []
    media = service.analyze(
        "https://example.test/playlist",
        CookieConfig(source="browser", browser="firefox", profile="default-release"),
        progress_cb=lambda current, total: progress.append((current, total)),
    )

    assert media.title == "Playlist"
    assert media.entries[0].media_id == "video-1"
    assert media.entries[0].formats[0].resolution == "1920x1080"
    assert media.entries[0].formats[0].filesize == 2048
    assert [(track.language, track.source, track.formats) for track in media.entries[0].subtitles] == [
        ("en", "manual", ["vtt", "srt"]),
        ("zh-Hant", "automatic", ["vtt"]),
    ]
    assert captured[0]["cookiesfrombrowser"] == ("firefox", "default-release")
    assert captured[0]["js_runtimes"]["deno"]["path"].endswith("deno.exe")
    assert captured[0]["remote_components"] == ["ejs:github"]
    assert captured[0]["color"] == "no_color"
    assert captured[0]["ignoreerrors"] is True
    assert progress == [(1, 1)]

    manual_only = service.map_media_info(info, include_automatic_subtitles=False)
    assert [(track.language, track.source) for track in manual_only.entries[0].subtitles] == [("en", "manual")]


def test_analyze_playlist_keeps_accessible_items_and_rejects_fully_inaccessible_playlist() -> None:
    accessible = {"id": "public", "title": "Public", "webpage_url": "https://example.test/public"}
    partial_info = {
        "_type": "playlist", "id": "partial", "title": "Partial",
        "entries": [accessible, None, {"id": "public-2", "title": "Public 2"}],
    }
    factory, _captured = ydl_factory(partial_info)
    service = YtDlpService(factory, which=lambda _name: None)
    progress = []
    media = service.analyze("https://example.test/partial", progress_cb=lambda current, total: progress.append((current, total)))
    assert [entry.media_id for entry in media.entries] == ["public", "public-2"]
    assert progress[-1] == (3, 3)

    inaccessible_factory, _captured = ydl_factory({
        "_type": "playlist", "id": "private", "title": "Private", "entries": [None],
    })
    inaccessible_service = YtDlpService(inaccessible_factory, which=lambda _name: None)
    with pytest.raises(ValueError, match="No accessible playlist items"):
        inaccessible_service.analyze("https://example.test/private")


def test_download_options_cover_presets_advanced_ids_and_cookie_file() -> None:
    service = YtDlpService(which=lambda _name: None)
    options = DownloadOptions(
        url="https://example.test/video",
        output_dir=str(TEST_PATH),
        preset="best_video_audio",
        resolution="1080p",
        video_container="mkv",
        playlist_item_ids=["1", "3"],
        cookie=CookieConfig(source="file", file_path="cookies.txt"),
    )

    result = service.build_download_options(options)

    assert result["format"] == "bestvideo*[height<=1080]+bestaudio/best[height<=1080]"
    assert result["paths"]["home"] == str(TEST_PATH)
    assert result["outtmpl"]["default"] == "%(title).180B.%(ext)s"
    assert result["cookiefile"] == "cookies.txt"
    assert result["remote_components"] == ["ejs:github"]
    assert result["color"] == "no_color"
    assert result["match_filter"]({"id": "1"}) is None
    assert result["match_filter"]({"id": "2"}) == "Not selected in the download queue"
    assert result["merge_output_format"] == "mkv"
    options.video_container = "mov"
    mov_result = service.build_download_options(options)
    assert mov_result["merge_output_format"] == "mov"
    assert mov_result["final_ext"] == "mov"
    options.video_format_id = "137"
    options.audio_format_id = "140"
    assert service.build_format_selector(options) == "137+140"


def test_manual_tool_directories_are_private_search_paths(monkeypatch) -> None:
    calls = []

    def fake_which(name: str, path: str | None = None) -> str | None:
        calls.append((name, path))
        found = (
            path == "C:/private/ffmpeg" and name in {"ffmpeg", "ffprobe"}
        ) or (
            path == "C:/private/js" and name == "node"
        )
        return f"{path}/{name}.exe" if found else None

    monkeypatch.setattr("ffmpeg_service.shutil.which", fake_which)
    monkeypatch.setattr("media_service.shutil.which", fake_which)
    ffmpeg = FFmpegService(tool_directory="C:/private/ffmpeg")
    media = YtDlpService(which=lambda _name: None, ffmpeg_directory="C:/private/ffmpeg", js_runtime_directory="C:/private/js")

    assert ffmpeg.ffmpeg_path == "C:/private/ffmpeg/ffmpeg.exe"
    assert ffmpeg.ffprobe_path == "C:/private/ffmpeg/ffprobe.exe"
    assert media.detect_js_runtimes() == {"node": "C:/private/js/node.exe"}
    assert media.ffmpeg_options() == {"ffmpeg_location": "C:/private/ffmpeg"}
    assert ("node", "C:/private/js") in calls
    media.js_runtime_directory = "C:/missing"
    assert media.runtime_options()["js_runtimes"] == {}


def test_validated_runtime_override_prevents_yt_dlp_from_using_rejected_path_runtime() -> None:
    service = YtDlpService(which=lambda name: "C:/old/deno.exe" if name == "deno" else None)
    service.set_validated_runtimes({})

    assert service.detect_js_runtimes() == {}
    assert service.runtime_options()["js_runtimes"] == {}


def test_yt_dlp_logger_removes_terminal_codes_and_duplicate_levels() -> None:
    messages = []
    logger = _YtDlpLogger(messages.append)
    logger.warning("\033[33mWARNING:\033[0m challenge solver unavailable")
    logger.error("\033[31mERROR:\033[0m format unavailable")
    assert messages == [
        "WARNING: challenge solver unavailable",
        "ERROR: format unavailable",
    ]


def test_analyze_writes_yt_dlp_error_to_error_log_without_expanding_ui_error(caplog) -> None:
    class FailedYdl(FakeYdl):
        def extract_info(self, _url: str, download: bool = False) -> dict:
            self.options["logger"].error("remote challenge solver failed")
            return {}

    service = YtDlpService(lambda options: FailedYdl(options, {}, []), which=lambda _name: None)

    with caplog.at_level(logging.ERROR, logger="yt_dlp"):
        with pytest.raises(ValueError, match="yt-dlp returned no media information"):
            service.analyze("https://example.test/video")

    assert "ERROR: remote challenge solver failed" in caplog.text


def test_execute_download_reports_progress_and_final_path(tmp_path: Path) -> None:
    info = {"id": "video", "title": "Video", "filepath": str(tmp_path / "Video [video].mkv")}
    factory, _captured = ydl_factory(info)
    service = YtDlpService(factory, which=lambda _name: None)
    task = TaskRecord(
        title="Video",
        download_options=DownloadOptions(
            url="https://example.test/video",
            output_dir=str(tmp_path),
        ),
    )
    progress: list[float] = []

    output = service.execute_download(task, lambda value, _detail: progress.append(value), lambda _message: None, threading.Event())

    assert output.endswith("Video [video].mkv")
    assert progress == [0.5, 1.0, 1.0]


def test_subtitle_options_and_execution_are_sidecar_only(tmp_path: Path) -> None:
    factory, captured = ydl_factory({"id": "video", "title": "Video"})
    service = YtDlpService(factory, which=lambda _name: None)
    options = SubtitleOptions(
        url="https://example.test/video",
        output_dir=str(tmp_path),
        selections=[
            SubtitleSelection("en", "manual", "vtt"),
            SubtitleSelection("ja", "automatic", "best"),
        ],
    )
    task = TaskRecord(kind=TaskKind.SUBTITLE, title="Video", subtitle_options=options)
    progress = []

    output = service.execute_subtitle(
        task,
        lambda value, _detail: progress.append(value),
        lambda _message: None,
        threading.Event(),
    )

    assert output == str(tmp_path.resolve())
    assert len(captured) == 2
    manual, automatic = captured
    assert manual["skip_download"] and manual["writesubtitles"] and not manual["writeautomaticsub"]
    assert manual["subtitleslangs"] == ["en"]
    assert manual["subtitlesformat"] == "vtt"
    assert manual["outtmpl"]["subtitle"] == "%(title).180B.%(language)s.%(ext)s"
    assert automatic["skip_download"] and automatic["writeautomaticsub"] and not automatic["writesubtitles"]
    assert automatic["outtmpl"]["subtitle"] == "%(title).180B.%(language)s.auto.%(ext)s"
    assert progress[-1] == 1.0


def test_download_filename_adds_number_for_existing_and_claimed_paths(tmp_path: Path) -> None:
    (tmp_path / "Video.mp4").touch()
    options = {
        "paths": {"home": str(tmp_path)},
        "outtmpl": {"default": YtDlpService.OUTPUT_TEMPLATE},
        "windowsfilenames": True,
        "quiet": True,
    }

    with _CollisionSafeYoutubeDL(options) as ydl:
        first = ydl.prepare_filename({"id": "one", "title": "Video", "ext": "mp4"})
        repeated = ydl.prepare_filename({"id": "one", "title": "Video", "ext": "mp4"})
        second = ydl.prepare_filename({"id": "two", "title": "Video", "ext": "mp4"})

    assert Path(first).name == "Video (1).mp4"
    assert repeated == first
    assert Path(second).name == "Video (2).mp4"


def test_download_filename_keeps_yt_dlp_windows_sanitization() -> None:
    options = {
        "paths": {"home": str(TEST_PATH)},
        "outtmpl": {"default": YtDlpService.OUTPUT_TEMPLATE},
        "windowsfilenames": True,
        "quiet": True,
    }

    with _CollisionSafeYoutubeDL(options) as ydl:
        output = ydl.prepare_filename({"id": "video", "title": 'A/B:C*D?E"F<G>H|I', "ext": "mp4"})

    assert not any(character in Path(output).name for character in '\\/:*?"<>|')
    assert Path(output).suffix == ".mp4"


def test_download_honors_pre_cancelled_event() -> None:
    factory, _captured = ydl_factory({})
    service = YtDlpService(factory)
    event = threading.Event()
    event.set()
    task = TaskRecord(download_options=DownloadOptions(url="https://example.test", output_dir=str(TEST_PATH)))

    with pytest.raises(ServiceCancelled):
        service.execute_download(task, lambda *_args: None, lambda _message: None, event)


def make_ffmpeg_service(run_command=None, popen_factory=None) -> FFmpegService:
    paths = {
        "ffmpeg": "C:/tools/ffmpeg.exe",
        "ffprobe": "C:/tools/ffprobe.exe",
        "deno": None,
    }
    return FFmpegService(
        which=lambda name: paths[name],
        run_command=run_command,
        popen_factory=popen_factory,
    )


def test_detect_encoders_and_validate_stream_copy() -> None:
    output = """
 Encoders:
 V....D h264_nvenc          NVIDIA NVENC H.264 encoder
 V..... h264_amf            AMD AMF H.264 Encoder
 V..... h264_qsv            H.264 Intel Quick Sync Video
 V..... libx264             H.264 software encoder
"""
    service = make_ffmpeg_service(lambda *_args, **_kwargs: SimpleNamespace(stdout=output, stderr="", returncode=0))

    assert service.list_hardware_encoders() == ["h264_amf", "h264_nvenc", "h264_qsv"]
    compatible = {"streams": [
        {"codec_type": "video", "codec_name": "vp9"},
        {"codec_type": "audio", "codec_name": "opus"},
    ]}
    incompatible = {"streams": [
        {"codec_type": "video", "codec_name": "h264"},
        {"codec_type": "audio", "codec_name": "aac"},
    ]}
    assert service.can_stream_copy(compatible, "webm")
    assert service.can_stream_copy(
        {"streams": [
            {"codec_type": "video", "codec_name": "h264"},
            {"codec_type": "audio", "codec_name": "aac"},
        ]},
        "mov",
    )
    valid, reason = service.validate_stream_copy(incompatible, "webm")
    assert not valid and "h264" in reason


def test_probe_and_command_generation_are_independently_testable() -> None:
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            stdout='{"format":{"duration":"20.5"},"streams":[{"codec_type":"video","codec_name":"h264"}]}',
            stderr="",
            returncode=0,
        )

    service = make_ffmpeg_service(fake_run)
    source = TEST_PATH / "clip.mov"
    probe = service.probe(source)
    options = ConversionOptions(
        input_path=str(source),
        output_dir=str(TEST_PATH),
        target_format="mp4",
        encoder="h264_nvenc",
    )
    command = service.build_command(options, TEST_PATH / "clip.mp4")

    assert probe["duration"] == 20.5
    assert command[0].endswith("ffmpeg.exe")
    assert command[-1].endswith("clip.mp4")
    assert ["-map", "0:v:0", "-map", "0:a?"] == command[
        command.index("-map"):command.index("-map") + 4
    ]
    assert ["-c:v", "h264_nvenc"] == command[command.index("-c:v"):command.index("-c:v") + 2]
    assert "-progress" in command and "-n" in command

    mov_options = ConversionOptions(
        input_path=str(source),
        output_dir=str(TEST_PATH),
        target_format="mov",
    )
    mov_command = service.build_command(mov_options, TEST_PATH / "clip-converted.mov")
    assert ["-c:v", "libx264"] == mov_command[mov_command.index("-c:v"):mov_command.index("-c:v") + 2]
    assert ["-c:a", "aac"] == mov_command[mov_command.index("-c:a"):mov_command.index("-c:a") + 2]
    assert "-pix_fmt" not in mov_command
    assert mov_command[-1].endswith("clip-converted.mov")

    subtitle_options = ConversionOptions(
        input_path=str(source),
        output_dir=str(TEST_PATH),
        target_format="vtt",
    )
    subtitle_command = service.build_command(subtitle_options, TEST_PATH / "clip.vtt")
    assert ["-map", "0:s:0", "-c:s", "webvtt"] == subtitle_command[
        subtitle_command.index("-map"):subtitle_command.index("-map") + 4
    ]
    assert "-c:v" not in subtitle_command and "-c:a" not in subtitle_command


def test_file_analysis_calculates_gop_from_keyframe_intervals() -> None:
    def fake_run(command, **_kwargs):
        if "frame=key_frame" in command:
            flags = ["1" if index in {0, 60, 120, 210} else "0" for index in range(240)]
            return SimpleNamespace(stdout="\n".join(flags), stderr="", returncode=0)
        return SimpleNamespace(
            stdout='{"format":{"duration":"10"},"streams":[{"codec_type":"video"}]}',
            stderr="", returncode=0,
        )

    service = make_ffmpeg_service(fake_run)
    result = service.analyze_file(TEST_PATH / "clip.mp4")

    assert result["gop_analysis"] == {
        "value": 60, "average": 70, "minimum": 60, "maximum": 90,
        "keyframes": 4, "frames_scanned": 240,
    }


def test_h264_working_video_command_maps_scale_fps_quality_gop_and_audio() -> None:
    service = make_ffmpeg_service()
    options = ConversionOptions(
        input_path=str(TEST_PATH / "source.mp4"), target_format="mp4", encoder="", acceleration="cpu",
        video_codec="h264", resolution_height=1080, fps="30000/1001", quality_mode="crf",
        quality_value=18, gop=60, h264_profile="high", pixel_format="yuv420p",
        audio_codec="aac", audio_bitrate=320, audio_sample_rate=48000,
    )

    command = service.build_command(options, TEST_PATH / "working.mp4")

    assert ["-c:v", "libx264"] == command[command.index("-c:v"):command.index("-c:v") + 2]
    assert ["-crf", "18"] == command[command.index("-crf"):command.index("-crf") + 2]
    assert ["-g", "60", "-keyint_min", "60", "-sc_threshold", "0"] == command[
        command.index("-g"):command.index("-g") + 6
    ]
    assert command[command.index("-vf") + 1] == "scale=-2:min(ih\\,1080),fps=30000/1001"
    assert ["-c:a", "aac", "-b:a", "320k"] == command[command.index("-c:a"):command.index("-c:a") + 4]
    assert ["-ar", "48000"] == command[command.index("-ar"):command.index("-ar") + 2]


def test_replacement_smart_copies_unchanged_video_and_pads_audio() -> None:
    service = make_ffmpeg_service()
    visual = {"duration": 10.0, "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
    ]}
    audio = {"duration": 6.0, "streams": [{"codec_type": "audio", "codec_name": "aac"}]}
    options = ReplacementOptions(
        visual_path="clip.mp4", audio_path="music.m4a",
        conversion=ConversionOptions(output_dir=str(TEST_PATH), target_format="mp4", encoder="", acceleration="cpu"),
    )

    command = service.build_replacement_command(options, visual, audio, TEST_PATH / "result.mp4")

    assert ["-map", "0:v:0"] == command[command.index("-map"):command.index("-map") + 2]
    assert ["-c:v", "copy"] == command[command.index("-c:v"):command.index("-c:v") + 2]
    assert "apad=pad_dur=4" in command[command.index("-filter_complex") + 1]
    assert ["-c:a", "aac"] == command[command.index("-c:a"):command.index("-c:a") + 2]
    assert command[command.index("-t") + 1] == "10"


def test_replacement_image_loop_aspect_delay_and_cuts_build_filters() -> None:
    service = make_ffmpeg_service()
    image = {"duration": None, "streams": [
        {"codec_type": "video", "codec_name": "png", "width": 1000, "height": 1000, "nb_frames": "1"},
    ]}
    audio = {"duration": 8.0, "streams": [{"codec_type": "audio", "codec_name": "pcm_s16le"}]}
    options = ReplacementOptions(
        visual_path="cover.png", audio_path="music.wav", duration_mode="custom", custom_duration=10,
        audio_loop=True, visual_delay=0.5, audio_delay=-0.25, trim_start=1, trim_end=2,
        aspect_ratio="16:9", fit_mode="contain", force_reencode=True,
        conversion=ConversionOptions(
            output_dir=str(TEST_PATH), target_format="mp4", encoder="", acceleration="cpu",
            resolution_height=1080, fps="30", audio_codec="aac", audio_bitrate=192,
        ),
    )

    command = service.build_replacement_command(options, image, audio, TEST_PATH / "result.mp4")
    filters = command[command.index("-filter_complex") + 1]

    assert command[command.index("-loop") + 1] == "1"
    assert "-stream_loop" in command
    assert "tpad=start_duration=0.5:start_mode=add:color=black" in filters
    assert "force_original_aspect_ratio=decrease" in filters and "pad=" in filters
    assert "atrim=start=0.25" in filters
    assert "trim=start=1:duration=7" in filters and "atrim=start=1:duration=7" in filters
    assert ["-pix_fmt", "yuv420p"] == command[command.index("-pix_fmt"):command.index("-pix_fmt") + 2]


def test_replacement_rejects_audio_copy_when_timeline_changes() -> None:
    service = make_ffmpeg_service()
    visual = {"duration": 10.0, "streams": [{"codec_type": "video", "codec_name": "h264"}]}
    audio = {"duration": 5.0, "streams": [{"codec_type": "audio", "codec_name": "aac"}]}
    options = ReplacementOptions(
        visual_path="clip.mp4", audio_path="music.m4a", audio_loop=True,
        conversion=ConversionOptions(target_format="mp4", audio_codec="copy"),
    )

    assert "Stream Copy" in service.validate_replacement(options, visual, audio)


def test_replacement_duration_uses_selected_stream_instead_of_other_tracks() -> None:
    service = make_ffmpeg_service()
    visual = {"duration": 20.0, "streams": [
        {"codec_type": "video", "codec_name": "h264", "duration": "8"},
        {"codec_type": "audio", "duration": "20"},
    ]}
    audio = {"duration": 30.0, "streams": [
        {"codec_type": "video", "duration": "30"},
        {"codec_type": "audio", "codec_name": "aac", "duration": "5"},
    ]}
    options = ReplacementOptions(
        visual_path="visual.mp4", audio_path="soundtrack.mp4", duration_mode="longest",
        conversion=ConversionOptions(target_format="mp4", acceleration="cpu"),
    )

    assert service._replacement_duration(options, visual, audio) == (8.0, 8.0)


def test_h264_bitrate_modes_map_cbr_vbr_and_two_pass_arguments() -> None:
    service = make_ffmpeg_service()
    output = TEST_PATH / "output.mp4"

    cbr = service.build_command(ConversionOptions(
        input_path="source.mp4", acceleration="cpu", quality_mode="cbr", quality_value=12,
    ), output)
    assert ["-b:v", "12M", "-minrate", "12M", "-maxrate", "12M"] == cbr[
        cbr.index("-b:v"):cbr.index("-b:v") + 6
    ]

    vbr = service.build_command(ConversionOptions(
        input_path="source.mp4", acceleration="cpu", quality_mode="vbr", quality_value=12,
    ), output)
    assert ["-b:v", "12M"] == vbr[vbr.index("-b:v"):vbr.index("-b:v") + 2]
    assert "-pass" not in vbr and "-maxrate" not in vbr

    two_pass_options = ConversionOptions(
        input_path="source.mp4", acceleration="cpu", quality_mode="vbr_2pass",
        quality_value=12, maximum_bitrate=20,
    )
    first = service.build_command(two_pass_options, output, 1, TEST_PATH / "passlog")
    second = service.build_command(two_pass_options, output, 2, TEST_PATH / "passlog")
    assert ["-maxrate", "20M"] == first[first.index("-maxrate"):first.index("-maxrate") + 2]
    assert ["-pass", "1"] == first[first.index("-pass"):first.index("-pass") + 2]
    assert "-an" in first and first[-1] == os.devnull
    assert ["-pass", "2"] == second[second.index("-pass"):second.index("-pass") + 2]
    assert second[-1] == str(output)


@pytest.mark.parametrize("target,codec", [("mp3", "libmp3lame"), ("m4a", "aac"), ("opus", "libopus")])
def test_audio_conversion_applies_selected_bitrate(target: str, codec: str) -> None:
    service = make_ffmpeg_service()
    options = ConversionOptions(
        input_path=str(TEST_PATH / "source.mov"), target_format=target,
        audio_bitrate=192, audio_sample_rate=44100,
    )

    command = service.build_command(options, TEST_PATH / f"audio.{target}")

    assert ["-map", "0:a:0", "-vn"] == command[command.index("-map"):command.index("-map") + 3]
    assert ["-c:a", codec, "-b:a", "192k"] == command[command.index("-c:a"):command.index("-c:a") + 4]
    assert ["-ar", "44100"] == command[command.index("-ar"):command.index("-ar") + 2]


def test_required_stream_detection_ignores_attached_cover_art() -> None:
    service = make_ffmpeg_service()
    audio_with_cover = {"streams": [
        {"codec_type": "video", "disposition": {"attached_pic": 1}},
        {"codec_type": "audio"},
    ]}

    assert not service.has_media_stream(audio_with_cover, "video")
    assert service.has_media_stream(audio_with_cover, "audio")
    assert service.has_media_stream({"streams": [{"codec_type": "video"}]}, "video")


@pytest.mark.parametrize("profile,value", [("proxy", "0"), ("lt", "1"), ("422", "2"), ("hq", "3")])
def test_prores_profiles_use_ten_bit_422_and_pcm(profile: str, value: str) -> None:
    service = make_ffmpeg_service()
    options = ConversionOptions(
        input_path=str(TEST_PATH / "source.mp4"), target_format="mov", encoder="",
        video_codec="prores", prores_profile=profile,
    )

    command = service.build_command(options, TEST_PATH / "proxy.mov")

    assert ["-c:v", "prores_ks"] == command[command.index("-c:v"):command.index("-c:v") + 2]
    assert ["-profile:v", value, "-pix_fmt", "yuv422p10le"] == command[
        command.index("-profile:v"):command.index("-profile:v") + 4
    ]
    assert ["-c:a", "pcm_s24le"] == command[command.index("-c:a"):command.index("-c:a") + 2]


def test_hardware_backends_require_initialization_and_auto_prefers_nvidia() -> None:
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if "-encoders" in command:
            return SimpleNamespace(stdout="""
 V..... h264_nvenc NVIDIA NVENC H.264 encoder
 V..... h264_amf AMD AMF H.264 encoder
 V..... h264_qsv Intel QSV H.264 encoder
""", stderr="", returncode=0)
        encoder = command[command.index("-c:v") + 1]
        return SimpleNamespace(stdout="", stderr="", returncode=0 if encoder in {"h264_nvenc", "h264_amf"} else 1)

    service = make_ffmpeg_service(fake_run)
    assert service.list_hardware_backends() == ["nvidia", "amd"]
    assert service.list_hardware_backends() == ["nvidia", "amd"]
    assert len([command for command in calls if "-frames:v" in command]) == 3
    encoder, reason = service.resolve_video_encoder(ConversionOptions(encoder="", acceleration="auto"))
    assert encoder == "h264_nvenc" and not reason
    encoder, reason = service.resolve_video_encoder(ConversionOptions(encoder="", acceleration="amd", gop=60))
    assert encoder == "libx264" and "fallback" in reason.lower()


def test_professional_option_validation_rejects_incompatible_values() -> None:
    service = make_ffmpeg_service()
    assert "MOV" in service.validate_options(ConversionOptions(target_format="mp4", video_codec="prores"))
    assert "even" in service.validate_options(ConversionOptions(resolution_height=721))
    assert "FPS" in service.validate_options(ConversionOptions(fps="300"))
    assert "CRF" in service.validate_options(ConversionOptions(quality_mode="crf", quality_value=52))
    assert "PCM" in service.validate_options(ConversionOptions(target_format="mp4", audio_codec="pcm_s24le"))
    assert "lossless" in service.validate_options(ConversionOptions(target_format="flac", audio_bitrate=192))
    assert "320" in service.validate_options(ConversionOptions(target_format="mp3", audio_bitrate=321))
    assert "512" in service.validate_options(ConversionOptions(target_format="opus", audio_bitrate=513))
    assert "sample rate" in service.validate_options(ConversionOptions(target_format="mp3", audio_sample_rate=12345))
    assert "greater than zero" in service.validate_options(ConversionOptions(
        quality_mode="vbr_2pass", quality_value=12, maximum_bitrate=0,
    ))
    assert "lower" in service.validate_options(ConversionOptions(
        quality_mode="vbr_2pass", quality_value=12, maximum_bitrate=10,
    ))
    assert not service.validate_options(ConversionOptions(
        target_format="mp4", stream_copy=True, video_codec="prores", audio_codec="pcm_s24le"
    ))


def test_collision_safe_output_never_overwrites_source_or_existing_file(tmp_path: Path) -> None:
    service = make_ffmpeg_service()
    source = tmp_path / "clip.mp4"
    source.touch()
    (tmp_path / "clip (1).mp4").touch()

    output = service.collision_safe_output(source, tmp_path, "mp4")

    assert output.name == "clip (2).mp4"


class FakeProcess:
    def __init__(self, *_args, **_kwargs):
        self.stdout = io.StringIO("out_time_us=5000000\nprogress=continue\nprogress=end\n")
        self.stderr = io.StringIO("ffmpeg diagnostic\n")
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 1

    def kill(self) -> None:
        self.returncode = 1


def test_execute_conversion_parses_progress_without_real_subprocess(tmp_path: Path) -> None:
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            stdout='{"format":{"duration":"10"},"streams":[{"codec_type":"video","codec_name":"h264"}]}',
            stderr="",
            returncode=0,
        )

    service = make_ffmpeg_service(fake_run, FakeProcess)
    task = TaskRecord(
        title="clip",
        conversion_options=ConversionOptions(
            input_path=str(tmp_path / "clip.mov"),
            output_dir=str(tmp_path),
            target_format="mp4",
        ),
    )
    progress: list[float] = []
    logs: list[str] = []

    output = service.execute_conversion(task, lambda value, _detail: progress.append(value), logs.append, threading.Event())

    assert output.endswith("clip.mp4")
    assert 0.5 in progress and progress[-1] == 1.0
    assert any("ffmpeg diagnostic" in line for line in logs)


def test_execute_conversion_runs_both_vbr_passes_and_removes_passlogs(tmp_path: Path) -> None:
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            stdout='{"format":{"duration":"10"},"streams":[{"codec_type":"video"}]}',
            stderr="", returncode=0,
        )

    commands = []
    pass_files: list[Path] = []

    def start_process(command, **kwargs):
        commands.append(command)
        passlog_path = Path(command[command.index("-passlogfile") + 1])
        pass_files[:] = [Path(f"{passlog_path}-0.log"), Path(f"{passlog_path}-0.log.mbtree")]
        for pass_file in pass_files: pass_file.write_text("x264 stats", encoding="utf-8")
        return FakeProcess(command, **kwargs)

    service = make_ffmpeg_service(fake_run, start_process)
    task = TaskRecord(conversion_options=ConversionOptions(
        input_path=str(tmp_path / "[A]ddiction.mov"), output_dir=str(tmp_path), acceleration="cpu",
        quality_mode="vbr_2pass", quality_value=12, maximum_bitrate=20,
    ))
    progress = []

    service.execute_conversion(task, lambda value, _detail: progress.append(value), lambda _line: None, threading.Event())

    assert len(commands) == 2
    assert commands[0][commands[0].index("-pass") + 1] == "1"
    assert commands[1][commands[1].index("-pass") + 1] == "2"
    assert 0.25 in progress and 0.75 in progress and progress[-1] == 1.0
    assert all(not pass_file.exists() for pass_file in pass_files)


def test_stream_copy_is_rejected_before_ffmpeg_starts(tmp_path: Path) -> None:
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            stdout='{"format":{"duration":"10"},"streams":[{"codec_type":"video","codec_name":"h264"}]}',
            stderr="",
            returncode=0,
        )

    service = make_ffmpeg_service(fake_run, lambda *_args, **_kwargs: pytest.fail("FFmpeg must not start"))
    task = TaskRecord(
        conversion_options=ConversionOptions(
            input_path=str(tmp_path / "clip.mp4"),
            output_dir=str(tmp_path),
            target_format="webm",
            stream_copy=True,
        ),
    )

    with pytest.raises(FFmpegError, match="h264"):
        service.execute_conversion(task, lambda *_args: None, lambda _message: None, threading.Event())


def test_video_output_rejects_audio_only_input_before_ffmpeg_starts(tmp_path: Path) -> None:
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            stdout='{"format":{"duration":"10"},"streams":['
            '{"codec_type":"video","disposition":{"attached_pic":1}},'
            '{"codec_type":"audio","codec_name":"aac"}]}',
            stderr="", returncode=0,
        )

    service = make_ffmpeg_service(fake_run, lambda *_args, **_kwargs: pytest.fail("FFmpeg must not start"))
    task = TaskRecord(conversion_options=ConversionOptions(
        input_path=str(tmp_path / "podcast.m4a"), output_dir=str(tmp_path), target_format="mp4",
    ))

    with pytest.raises(FFmpegError, match="video stream"):
        service.execute_conversion(task, lambda *_args: None, lambda _message: None, threading.Event())


def test_conversion_cancellation_terminates_running_process(tmp_path: Path) -> None:
    event = threading.Event()

    class CancelPipe:
        def readline(self) -> str:
            event.set()
            return ""

    class CancelProcess(FakeProcess):
        def __init__(self, *_args, **_kwargs):
            super().__init__()
            self.stdout = CancelPipe()

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            stdout='{"format":{"duration":"10"},"streams":[{"codec_type":"video"}]}',
            stderr="", returncode=0,
        )

    processes: list[CancelProcess] = []

    def start_process(*args, **kwargs) -> CancelProcess:
        process = CancelProcess(*args, **kwargs)
        processes.append(process)
        return process

    service = make_ffmpeg_service(fake_run, start_process)
    task = TaskRecord(conversion_options=ConversionOptions(
        input_path=str(tmp_path / "clip.mov"),
        output_dir=str(tmp_path),
        target_format="mp4",
    ))

    with pytest.raises(ServiceCancelled):
        service.execute_conversion(task, lambda *_args: None, lambda _message: None, event)
    assert processes[0].terminated
