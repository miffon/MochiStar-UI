from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Self
from uuid import uuid4


class TaskKind(str, Enum):
    """任務類型"""

    DOWNLOAD = "download"
    SUBTITLE = "subtitle"
    CONVERSION = "conversion"
    REPLACEMENT = "replacement"


class TaskStatus(str, Enum):
    """任務狀態"""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _mapping(value: Any) -> Mapping[str, Any]:
    """把未知輸入限制為 mapping"""
    return value if isinstance(value, Mapping) else {}


def _text(value: Any, default: str = "") -> str:
    """安全讀取 JSON 文字"""
    return value if isinstance(value, str) else default


def _optional_number(value: Any) -> float | None:
    """安全讀取有限數值"""
    if value is None or isinstance(value, bool): return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _optional_integer(value: Any) -> int | None:
    """安全讀取整數"""
    if value is None or isinstance(value, bool): return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result


def _task_progress(value: Any) -> float | None:
    """限制 task progress 為 -1 或 0 到 1"""
    number = _optional_number(value)
    if number is None: return None
    return -1.0 if number < 0 else min(1.0, number)


def _string_list(value: Any) -> list[str]:
    """安全讀取字串 list"""
    if not isinstance(value, list): return []
    return [item for item in value if isinstance(item, str)]


def _enum_member(enum_type: type[Enum], value: Any, default: Enum) -> Any:
    """讀取 enum value, 未知值使用預設狀態"""
    if isinstance(value, enum_type): return value
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return default


def _timestamp(value: Any) -> datetime:
    """讀取 ISO timestamp, 無效資料使用目前 UTC 時間"""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


def _timestamp_text(value: datetime | str) -> str:
    """把 timestamp 轉成 JSON 文字"""
    return value.isoformat() if isinstance(value, datetime) else _text(value, datetime.now(UTC).isoformat())


@dataclass(slots=True)
class CookieConfig:
    """保存 Cookie 來源設定, 不保存 Cookie 內容"""

    source: str = "none"
    browser: str = ""
    profile: str = ""
    file_path: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "browser": self.browser,
            "profile": self.profile,
            "file_path": self.file_path,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | Any) -> Self:
        values = _mapping(data)
        source = _text(values.get("source", values.get("mode", "none")), "none").lower()
        source = {"cookiesfrombrowser": "browser", "cookiefile": "file", "netscape": "file"}.get(source, source)
        if source not in {"none", "browser", "file"}: source = "none"
        return cls(
            source=source,
            browser=_text(values.get("browser")),
            profile=_text(values.get("profile")),
            file_path=_text(values.get("file_path", values.get("path"))),
        )


@dataclass(slots=True)
class DownloadOptions:
    """下載任務參數"""

    url: str = ""
    output_dir: str = ""
    preset: str = "best_video_audio"
    resolution: str = "best"
    video_container: str = "auto"
    audio_output: str = "original"
    video_format_id: str = ""
    audio_format_id: str = ""
    playlist_item_ids: list[str] = field(default_factory=list)
    cookie: CookieConfig = field(default_factory=CookieConfig)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "output_dir": self.output_dir,
            "preset": self.preset,
            "resolution": self.resolution,
            "video_container": self.video_container,
            "audio_output": self.audio_output,
            "video_format_id": self.video_format_id,
            "audio_format_id": self.audio_format_id,
            "playlist_item_ids": list(self.playlist_item_ids),
            "cookie": self.cookie.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | Any) -> Self:
        values = _mapping(data)
        return cls(
            url=_text(values.get("url")),
            output_dir=_text(values.get("output_dir")),
            preset=_text(values.get("preset"), "best_video_audio"),
            resolution=_text(values.get("resolution"), "best"),
            video_container=_text(values.get("video_container"), "auto"),
            audio_output=_text(values.get("audio_output"), "original"),
            video_format_id=_text(values.get("video_format_id")),
            audio_format_id=_text(values.get("audio_format_id")),
            playlist_item_ids=_string_list(values.get("playlist_item_ids")),
            cookie=CookieConfig.from_dict(values.get("cookie")),
        )


@dataclass(slots=True)
class ConversionOptions:
    """獨立轉檔任務參數"""

    input_path: str = ""
    output_dir: str = ""
    target_format: str = "mp4"
    stream_copy: bool = False
    encoder: str = "software"
    video_codec: str = "auto"
    prores_profile: str = "proxy"
    resolution_height: int | None = None
    allow_upscale: bool = False
    fps: str = "source"
    quality_mode: str = "vbr"
    quality_value: float | None = 7.5
    maximum_bitrate: float | None = None
    gop: int | None = None
    h264_profile: str = "auto"
    pixel_format: str = "auto"
    audio_codec: str = "auto"
    audio_bitrate: int | None = None
    audio_sample_rate: int | None = None
    acceleration: str = "auto"

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_path": self.input_path,
            "output_dir": self.output_dir,
            "target_format": self.target_format,
            "stream_copy": self.stream_copy,
            "encoder": self.encoder,
            "video_codec": self.video_codec,
            "prores_profile": self.prores_profile,
            "resolution_height": self.resolution_height,
            "allow_upscale": self.allow_upscale,
            "fps": self.fps,
            "quality_mode": self.quality_mode,
            "quality_value": self.quality_value,
            "maximum_bitrate": self.maximum_bitrate,
            "gop": self.gop,
            "h264_profile": self.h264_profile,
            "pixel_format": self.pixel_format,
            "audio_codec": self.audio_codec,
            "audio_bitrate": self.audio_bitrate,
            "audio_sample_rate": self.audio_sample_rate,
            "acceleration": self.acceleration,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | Any) -> Self:
        values = _mapping(data)
        raw_encoder = _text(values.get("encoder"), "software")
        migrated_encoder = "" if "acceleration" not in values and raw_encoder.endswith(("_nvenc", "_amf", "_qsv")) else raw_encoder
        return cls(
            input_path=_text(values.get("input_path")),
            output_dir=_text(values.get("output_dir")),
            target_format=_text(values.get("target_format"), "mp4"),
            stream_copy=values.get("stream_copy") is True,
            encoder=migrated_encoder,
            video_codec=_text(values.get("video_codec"), "auto"),
            prores_profile=_text(values.get("prores_profile"), "proxy"),
            resolution_height=_optional_integer(values.get("resolution_height")),
            allow_upscale=values.get("allow_upscale") is True,
            fps=_text(values.get("fps"), "source"),
            quality_mode=_quality_mode(values.get("quality_mode")),
            quality_value=_quality_value(values.get("quality_mode"), values.get("quality_value")),
            maximum_bitrate=_optional_number(values.get("maximum_bitrate")),
            gop=_optional_integer(values.get("gop")),
            h264_profile=_text(values.get("h264_profile"), "auto"),
            pixel_format=_text(values.get("pixel_format"), "auto"),
            audio_codec=_text(values.get("audio_codec"), "auto"),
            audio_bitrate=_optional_integer(values.get("audio_bitrate")),
            audio_sample_rate=_optional_integer(values.get("audio_sample_rate")),
            acceleration=_legacy_acceleration(values.get("acceleration", values.get("encoder", "auto"))),
        )


@dataclass(slots=True)
class ReplacementOptions:
    """畫面與音訊合成任務參數"""

    visual_path: str = ""
    audio_path: str = ""
    duration_mode: str = "longest"
    custom_duration: float | None = None
    visual_loop: bool = False
    audio_loop: bool = False
    visual_delay: float = 0.0
    audio_delay: float = 0.0
    trim_start: float = 0.0
    trim_end: float = 0.0
    aspect_ratio: str = "source"
    fit_mode: str = "contain"
    force_reencode: bool = False
    conversion: ConversionOptions = field(default_factory=ConversionOptions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "visual_path": self.visual_path, "audio_path": self.audio_path,
            "duration_mode": self.duration_mode, "custom_duration": self.custom_duration,
            "visual_loop": self.visual_loop, "audio_loop": self.audio_loop,
            "visual_delay": self.visual_delay, "audio_delay": self.audio_delay,
            "trim_start": self.trim_start, "trim_end": self.trim_end,
            "aspect_ratio": self.aspect_ratio, "fit_mode": self.fit_mode,
            "force_reencode": self.force_reencode, "conversion": self.conversion.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | Any) -> Self:
        values = _mapping(data)
        duration_mode = _text(values.get("duration_mode"), "longest")
        aspect_ratio = _text(values.get("aspect_ratio"), "source")
        fit_mode = _text(values.get("fit_mode"), "contain")
        return cls(
            visual_path=_text(values.get("visual_path")), audio_path=_text(values.get("audio_path")),
            duration_mode=duration_mode if duration_mode in {"longest", "shortest", "custom"} else "longest",
            custom_duration=_optional_number(values.get("custom_duration")),
            visual_loop=values.get("visual_loop") is True, audio_loop=values.get("audio_loop") is True,
            visual_delay=_optional_number(values.get("visual_delay")) or 0.0,
            audio_delay=_optional_number(values.get("audio_delay")) or 0.0,
            trim_start=max(0.0, _optional_number(values.get("trim_start")) or 0.0),
            trim_end=max(0.0, _optional_number(values.get("trim_end")) or 0.0),
            aspect_ratio=aspect_ratio if aspect_ratio in {"source", "16:9", "9:16", "1:1"} else "source",
            fit_mode=fit_mode if fit_mode in {"contain", "cover"} else "contain",
            force_reencode=values.get("force_reencode") is True,
            conversion=ConversionOptions.from_dict(values.get("conversion", values.get("output"))),
        )


def _legacy_acceleration(value: Any) -> str:
    """將舊 encoder 名稱轉成硬體品牌偏好"""
    name = _text(value, "auto").lower()
    aliases = {
        "h264_nvenc": "nvidia", "hevc_nvenc": "nvidia", "av1_nvenc": "nvidia",
        "h264_amf": "amd", "hevc_amf": "amd", "av1_amf": "amd",
        "h264_qsv": "intel", "hevc_qsv": "intel", "av1_qsv": "intel",
        "software": "cpu", "libx264": "cpu", "cpu": "cpu",
    }
    return aliases.get(name, name if name in {"auto", "nvidia", "amd", "intel"} else "auto")


def _quality_mode(value: Any) -> str:
    """將舊版品質模式轉成目前的位元率模式"""
    mode = _text(value, "vbr").lower()
    return {"auto": "vbr", "bitrate": "vbr"}.get(mode, mode)


def _quality_value(mode: Any, value: Any) -> float | None:
    """未指定數值時套用預設 VBR 7.5 Mbps"""
    parsed = _optional_number(value)
    return 7.5 if _quality_mode(mode) == "vbr" and parsed is None else parsed


@dataclass(slots=True)
class ConversionPreset:
    """不包含路徑與硬體偏好的可重用轉檔規格"""

    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    media_type: str = "video"
    target_format: str = "mp4"
    stream_copy: bool = False
    video_codec: str = "auto"
    prores_profile: str = "proxy"
    resolution_height: int | None = None
    allow_upscale: bool = False
    fps: str = "source"
    quality_mode: str = "vbr"
    quality_value: float | None = 7.5
    maximum_bitrate: float | None = None
    gop: int | None = None
    h264_profile: str = "auto"
    pixel_format: str = "auto"
    audio_codec: str = "auto"
    audio_bitrate: int | None = None
    audio_sample_rate: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {field_name: getattr(self, field_name) for field_name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | Any) -> Self:
        values = _mapping(data)
        preset_id = _text(values.get("id")) or str(uuid4())
        target_format = _text(values.get("target_format"), "mp4")
        inferred_type = "audio" if target_format in {"mp3", "m4a", "opus", "flac", "wav"} else (
            "subtitle" if target_format in {"srt", "vtt", "ass"} else "video"
        )
        media_type = _text(values.get("media_type"), inferred_type)
        if media_type not in {"video", "audio", "subtitle"}: media_type = inferred_type
        return cls(
            id=preset_id, name=_text(values.get("name")).strip(), media_type=media_type,
            target_format=target_format,
            stream_copy=values.get("stream_copy") is True,
            video_codec=_text(values.get("video_codec"), "auto"),
            prores_profile=_text(values.get("prores_profile"), "proxy"),
            resolution_height=_optional_integer(values.get("resolution_height")),
            allow_upscale=values.get("allow_upscale") is True,
            fps=_text(values.get("fps"), "source"),
            quality_mode=_quality_mode(values.get("quality_mode")),
            quality_value=_quality_value(values.get("quality_mode"), values.get("quality_value")),
            maximum_bitrate=_optional_number(values.get("maximum_bitrate")),
            gop=_optional_integer(values.get("gop")),
            h264_profile=_text(values.get("h264_profile"), "auto"),
            pixel_format=_text(values.get("pixel_format"), "auto"),
            audio_codec=_text(values.get("audio_codec"), "auto"),
            audio_bitrate=_optional_integer(values.get("audio_bitrate")),
            audio_sample_rate=_optional_integer(values.get("audio_sample_rate")),
        )


@dataclass(slots=True)
class SubtitleTrack:
    """可供使用者選擇的字幕 track"""

    language: str = ""
    name: str = ""
    source: str = "manual"
    formats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "name": self.name,
            "source": self.source,
            "formats": list(self.formats),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | Any) -> Self:
        values = _mapping(data)
        source = _text(values.get("source"), "manual")
        return cls(
            language=_text(values.get("language")),
            name=_text(values.get("name")),
            source=source if source in {"manual", "automatic"} else "manual",
            formats=_string_list(values.get("formats")),
        )


@dataclass(slots=True)
class SubtitleSelection:
    """Queue task 中選定的字幕與格式"""

    language: str = ""
    source: str = "manual"
    format: str = "best"

    def to_dict(self) -> dict[str, str]:
        return {"language": self.language, "source": self.source, "format": self.format}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | Any) -> Self:
        values = _mapping(data)
        source = _text(values.get("source"), "manual")
        return cls(
            language=_text(values.get("language")),
            source=source if source in {"manual", "automatic"} else "manual",
            format=_text(values.get("format"), "best"),
        )


@dataclass(slots=True)
class SubtitleOptions:
    """Sidecar 字幕下載任務參數"""

    url: str = ""
    output_dir: str = ""
    selections: list[SubtitleSelection] = field(default_factory=list)
    cookie: CookieConfig = field(default_factory=CookieConfig)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "output_dir": self.output_dir,
            "selections": [item.to_dict() for item in self.selections],
            "cookie": self.cookie.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | Any) -> Self:
        values = _mapping(data)
        selections = values.get("selections")
        return cls(
            url=_text(values.get("url")),
            output_dir=_text(values.get("output_dir")),
            selections=[SubtitleSelection.from_dict(item) for item in selections if isinstance(item, Mapping)]
            if isinstance(selections, list) else [],
            cookie=CookieConfig.from_dict(values.get("cookie")),
        )


@dataclass(slots=True)
class FormatInfo:
    """yt-dlp format 的 UI 顯示資料"""

    format_id: str = ""
    extension: str = ""
    resolution: str = ""
    fps: float | None = None
    video_codec: str = ""
    audio_codec: str = ""
    filesize: int | None = None
    width: int | None = None
    height: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_id": self.format_id,
            "extension": self.extension,
            "resolution": self.resolution,
            "fps": self.fps,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "filesize": self.filesize,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | Any) -> Self:
        values = _mapping(data)
        return cls(
            format_id=_text(values.get("format_id")),
            extension=_text(values.get("extension", values.get("ext"))),
            resolution=_text(values.get("resolution")),
            fps=_optional_number(values.get("fps")),
            video_codec=_text(values.get("video_codec", values.get("vcodec"))),
            audio_codec=_text(values.get("audio_codec", values.get("acodec"))),
            filesize=_optional_integer(values.get("filesize")),
            width=_optional_integer(values.get("width")),
            height=_optional_integer(values.get("height")),
        )


@dataclass(slots=True)
class MediaInfo:
    """分析完成後的 media 或 playlist metadata"""

    media_id: str = ""
    title: str = ""
    uploader: str = ""
    duration: float | None = None
    site: str = ""
    webpage_url: str = ""
    thumbnail: str = ""
    formats: list[FormatInfo] = field(default_factory=list)
    subtitles: list[SubtitleTrack] = field(default_factory=list)
    entries: list[MediaInfo] = field(default_factory=list)

    @property
    def is_playlist(self) -> bool:
        return bool(self.entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "media_id": self.media_id,
            "title": self.title,
            "uploader": self.uploader,
            "duration": self.duration,
            "site": self.site,
            "webpage_url": self.webpage_url,
            "thumbnail": self.thumbnail,
            "formats": [item.to_dict() for item in self.formats],
            "subtitles": [item.to_dict() for item in self.subtitles],
            "entries": [item.to_dict() for item in self.entries],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | Any) -> Self:
        values = _mapping(data)
        formats = values.get("formats")
        subtitles = values.get("subtitles")
        entries = values.get("entries")
        return cls(
            media_id=_text(values.get("media_id", values.get("id"))),
            title=_text(values.get("title")),
            uploader=_text(values.get("uploader")),
            duration=_optional_number(values.get("duration")),
            site=_text(values.get("site")),
            webpage_url=_text(values.get("webpage_url", values.get("url"))),
            thumbnail=_text(values.get("thumbnail")),
            formats=[FormatInfo.from_dict(item) for item in formats if isinstance(item, Mapping)]
            if isinstance(formats, list) else [],
            subtitles=[SubtitleTrack.from_dict(item) for item in subtitles if isinstance(item, Mapping)]
            if isinstance(subtitles, list) else [],
            entries=[cls.from_dict(item) for item in entries if isinstance(item, Mapping)]
            if isinstance(entries, list) else [],
        )


@dataclass(slots=True)
class TaskRecord:
    """下載、字幕、轉檔或替換 queue task"""

    id: str = field(default_factory=lambda: str(uuid4()))
    kind: TaskKind = TaskKind.DOWNLOAD
    title: str = ""
    status: TaskStatus = TaskStatus.PENDING
    progress: float | None = 0.0
    output_path: str = ""
    error: str = ""
    created_at: datetime | str = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | str = field(default_factory=lambda: datetime.now(UTC))
    download_options: DownloadOptions | None = None
    subtitle_options: SubtitleOptions | None = None
    conversion_options: ConversionOptions | None = None
    replacement_options: ReplacementOptions | None = None

    def __post_init__(self) -> None:
        self.kind = _enum_member(TaskKind, self.kind, TaskKind.DOWNLOAD)
        self.status = _enum_member(TaskStatus, self.status, TaskStatus.PENDING)
        self.progress = _task_progress(self.progress)
        if self.replacement_options is not None and self.download_options is None:
            self.kind = TaskKind.REPLACEMENT
        elif self.conversion_options is not None and self.download_options is None:
            self.kind = TaskKind.CONVERSION
        elif self.subtitle_options is not None and self.download_options is None:
            self.kind = TaskKind.SUBTITLE

    @property
    def payload(self) -> DownloadOptions | SubtitleOptions | ConversionOptions | ReplacementOptions | None:
        if self.kind is TaskKind.DOWNLOAD: return self.download_options
        if self.kind is TaskKind.SUBTITLE: return self.subtitle_options
        if self.kind is TaskKind.CONVERSION: return self.conversion_options
        return self.replacement_options

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "title": self.title,
            "status": self.status.value,
            "progress": self.progress,
            "output_path": self.output_path,
            "error": self.error,
            "created_at": _timestamp_text(self.created_at),
            "updated_at": _timestamp_text(self.updated_at),
            "download_options": self.download_options.to_dict() if self.download_options else None,
            "subtitle_options": self.subtitle_options.to_dict() if self.subtitle_options else None,
            "conversion_options": self.conversion_options.to_dict() if self.conversion_options else None,
            "replacement_options": self.replacement_options.to_dict() if self.replacement_options else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | Any) -> Self:
        values = _mapping(data)
        download_data = values.get("download_options", values.get("download"))
        subtitle_data = values.get("subtitle_options", values.get("subtitle"))
        conversion_data = values.get("conversion_options", values.get("conversion"))
        replacement_data = values.get("replacement_options", values.get("replacement"))
        kind = _enum_member(TaskKind, values.get("kind"), TaskKind.DOWNLOAD)
        payload = values.get("payload")
        if isinstance(payload, Mapping):
            if kind is TaskKind.DOWNLOAD and not isinstance(download_data, Mapping): download_data = payload
            if kind is TaskKind.SUBTITLE and not isinstance(subtitle_data, Mapping): subtitle_data = payload
            if kind is TaskKind.CONVERSION and not isinstance(conversion_data, Mapping): conversion_data = payload
            if kind is TaskKind.REPLACEMENT and not isinstance(replacement_data, Mapping): replacement_data = payload
        task_id = _text(values.get("id"))
        return cls(
            id=task_id or str(uuid4()),
            kind=kind,
            title=_text(values.get("title")),
            status=_enum_member(TaskStatus, values.get("status"), TaskStatus.PENDING),
            progress=_task_progress(values.get("progress", 0.0)),
            output_path=_text(values.get("output_path")),
            error=_text(values.get("error")),
            created_at=_timestamp(values.get("created_at")),
            updated_at=_timestamp(values.get("updated_at")),
            download_options=DownloadOptions.from_dict(download_data) if isinstance(download_data, Mapping) else None,
            subtitle_options=SubtitleOptions.from_dict(subtitle_data) if isinstance(subtitle_data, Mapping) else None,
            conversion_options=ConversionOptions.from_dict(conversion_data) if isinstance(conversion_data, Mapping) else None,
            replacement_options=ReplacementOptions.from_dict(replacement_data)
            if isinstance(replacement_data, Mapping) else None,
        )
