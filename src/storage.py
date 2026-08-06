from __future__ import annotations

import base64
import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QIODevice, QSaveFile, QStandardPaths

from models import ConversionPreset, CookieConfig, TaskRecord, TaskStatus, _legacy_acceleration

SCHEMA_VERSION = 1


def _default_output_dir() -> str:
    """取得作業系統預設下載目錄"""
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    return location or str(Path.home() / "Downloads")


def _text(value: Any, default: str = "") -> str:
    """安全讀取 JSON 文字"""
    return value if isinstance(value, str) else default


def _worker_count(value: Any) -> int:
    """限制 worker 數量為 1 到 4"""
    try:
        return max(1, min(4, int(value)))
    except (TypeError, ValueError):
        return 1


def _theme_name(value: Any) -> str:
    """讀取 theme ID 並轉換舊名稱"""
    name = _text(value, "cute_light")
    return "starlit_night" if name == "modern_dark" else name


def _table_column_widths(value: Any, count: int, minimum: int) -> list[int]:
    """讀取指定數量的 table 欄寬並限制合理範圍"""
    if not isinstance(value, list) or len(value) != count: return []
    widths: list[int] = []
    for item in value:
        if isinstance(item, bool): return []
        try:
            widths.append(max(minimum, min(2000, int(item))))
        except (TypeError, ValueError):
            return []
    return widths


def _splitter_sizes(value: Any) -> list[int]:
    """讀取左右轉檔面板寬度"""
    if not isinstance(value, list) or len(value) != 2: return []
    sizes = []
    for item in value:
        if isinstance(item, bool): return []
        try:
            sizes.append(max(100, min(5000, int(item))))
        except (TypeError, ValueError):
            return []
    return sizes


def _conversion_presets(value: Any) -> list[ConversionPreset]:
    """讀取名稱不重複的自訂影片轉檔 preset"""
    if not isinstance(value, list): return []
    presets: list[ConversionPreset] = []
    names: set[str] = set()
    for item in value:
        preset = ConversionPreset.from_dict(item)
        key = preset.name.casefold()
        if preset.media_type != "video" or not preset.name or key == "default" or key in names: continue
        presets.append(preset)
        names.add(key)
    return presets


def _conversion_preset_id(value: Any, legacy_value: Any = "default:video") -> str:
    """讀取影片轉檔 preset ID, 並相容舊的分類格式"""
    values = value if isinstance(value, Mapping) else {}
    preset_id = _text(legacy_value, _text(values.get("video"), "default:video"))
    return "default:video" if preset_id == "default" else preset_id


def _ignored_dependencies(value: Any) -> list[str]:
    """讀取可永久略過的外部依賴提示"""
    if not isinstance(value, list): return []
    return [name for name in ("ffmpeg", "js_runtime") if name in value]


def _encode_bytes(value: bytes) -> str:
    """使用 Base64 保存 Qt geometry 或 state"""
    return base64.b64encode(value).decode("ascii") if value else ""


def _decode_bytes(value: Any) -> bytes:
    """安全讀取 Base64 encoded bytes"""
    if not isinstance(value, str) or not value: return b""
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError):
        return b""


@dataclass(slots=True)
class Settings:
    """應用程式持久化設定"""

    output_dir: str = field(default_factory=_default_output_dir)
    cookie: CookieConfig = field(default_factory=CookieConfig)
    last_preset: str = "best_video_audio"
    last_resolution: str = "best"
    last_video_container: str = "auto"
    last_audio_output: str = "original"
    include_automatic_subtitles: bool = True
    last_conversion_output_dir: str = ""
    last_conversion_format: str = "mp4"
    last_conversion_mode: str = "encode"
    last_conversion_encoder: str = "software"
    last_conversion_preset_id: str = "default:video"
    last_conversion_type: str = "video"
    last_conversion_acceleration: str = "auto"
    conversion_presets: list[ConversionPreset] = field(default_factory=list)
    replacement_settings: dict[str, Any] = field(default_factory=dict)
    replacement_splitter_sizes: list[int] = field(default_factory=list)
    theme_name: str = "cute_light"
    experimental_custom_title_bar: bool = False
    language: str = "zh_TW"
    manual_ffmpeg_enabled: bool = False
    ffmpeg_bin_dir: str = ""
    manual_js_runtime_enabled: bool = False
    js_runtime_bin_dir: str = ""
    ignored_missing_dependencies: list[str] = field(default_factory=list)
    auto_check_updates: bool = True
    last_update_check_at: str = ""
    worker_count: int = 1
    download_column_widths: list[int] = field(default_factory=list)
    subtitle_column_widths: list[int] = field(default_factory=list)
    queue_column_widths: list[int] = field(default_factory=list)
    conversion_splitter_sizes: list[int] = field(default_factory=list)
    geometry: bytes = b""
    window_state: bytes = b""

    def __post_init__(self) -> None:
        self.worker_count = _worker_count(self.worker_count)
        self.ignored_missing_dependencies = _ignored_dependencies(self.ignored_missing_dependencies)
        self.download_column_widths = _table_column_widths(self.download_column_widths, 4, 48)
        self.subtitle_column_widths = _table_column_widths(self.subtitle_column_widths, 6, 48)
        self.queue_column_widths = _table_column_widths(self.queue_column_widths, 7, 60)
        self.conversion_splitter_sizes = _splitter_sizes(self.conversion_splitter_sizes)
        self.replacement_splitter_sizes = _splitter_sizes(self.replacement_splitter_sizes)

    @property
    def concurrency(self) -> int:
        return self.worker_count

    @concurrency.setter
    def concurrency(self, value: int) -> None:
        self.worker_count = _worker_count(value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_dir": self.output_dir,
            "cookie": self.cookie.to_dict(),
            "last_preset": self.last_preset,
            "last_resolution": self.last_resolution,
            "last_video_container": self.last_video_container,
            "last_audio_output": self.last_audio_output,
            "include_automatic_subtitles": self.include_automatic_subtitles,
            "last_conversion_output_dir": self.last_conversion_output_dir,
            "last_conversion_format": self.last_conversion_format,
            "last_conversion_mode": self.last_conversion_mode,
            "last_conversion_encoder": self.last_conversion_encoder,
            "last_conversion_preset_id": self.last_conversion_preset_id,
            "last_conversion_type": self.last_conversion_type,
            "last_conversion_acceleration": self.last_conversion_acceleration,
            "conversion_presets": [preset.to_dict() for preset in self.conversion_presets],
            "replacement_settings": dict(self.replacement_settings),
            "replacement_splitter_sizes": list(self.replacement_splitter_sizes),
            "theme_name": self.theme_name,
            "experimental_custom_title_bar": self.experimental_custom_title_bar,
            "language": self.language,
            "manual_ffmpeg_enabled": self.manual_ffmpeg_enabled,
            "ffmpeg_bin_dir": self.ffmpeg_bin_dir,
            "manual_js_runtime_enabled": self.manual_js_runtime_enabled,
            "js_runtime_bin_dir": self.js_runtime_bin_dir,
            "ignored_missing_dependencies": list(self.ignored_missing_dependencies),
            "auto_check_updates": self.auto_check_updates,
            "last_update_check_at": self.last_update_check_at,
            "worker_count": self.worker_count,
            "download_column_widths": list(self.download_column_widths),
            "subtitle_column_widths": list(self.subtitle_column_widths),
            "queue_column_widths": list(self.queue_column_widths),
            "conversion_splitter_sizes": list(self.conversion_splitter_sizes),
            "geometry": _encode_bytes(self.geometry),
            "window_state": _encode_bytes(self.window_state),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | Any) -> Self:
        values = data if isinstance(data, Mapping) else {}
        return cls(
            output_dir=_text(values.get("output_dir"), _text(values.get("subtitle_output_dir"), _default_output_dir())),
            cookie=CookieConfig.from_dict(values.get("cookie", values.get("subtitle_cookie"))),
            last_preset=_text(values.get("last_preset"), "best_video_audio"),
            last_resolution=_text(values.get("last_resolution"), "best"),
            last_video_container=_text(values.get("last_video_container"), "auto"),
            last_audio_output=_text(values.get("last_audio_output"), "original"),
            include_automatic_subtitles=values.get("include_automatic_subtitles") is not False,
            last_conversion_output_dir=_text(values.get("last_conversion_output_dir")),
            last_conversion_format=_text(values.get("last_conversion_format"), "mp4"),
            last_conversion_mode=_text(values.get("last_conversion_mode"), "encode"),
            last_conversion_encoder=_text(values.get("last_conversion_encoder"), "software"),
            last_conversion_preset_id=_conversion_preset_id(
                values.get("last_conversion_preset_ids"), values.get("last_conversion_preset_id")
            ),
            last_conversion_type=_text(values.get("last_conversion_type"), "video")
            if _text(values.get("last_conversion_type"), "video") in {"video", "audio", "subtitle"} else "video",
            last_conversion_acceleration=_legacy_acceleration(values.get(
                "last_conversion_acceleration", values.get("last_conversion_encoder", "auto")
            )),
            conversion_presets=_conversion_presets(values.get("conversion_presets")),
            replacement_settings=dict(values.get("replacement_settings"))
            if isinstance(values.get("replacement_settings"), Mapping) else {},
            replacement_splitter_sizes=_splitter_sizes(values.get("replacement_splitter_sizes")),
            theme_name=_theme_name(values.get("theme_name")),
            experimental_custom_title_bar=(
                values.get("experimental_custom_title_bar") is True
                or values.get("experimental_extended_title_bar") is True
            ),
            language=_text(values.get("language"), "zh_TW")
            if _text(values.get("language"), "zh_TW") in {"en", "zh_TW"} else "zh_TW",
            manual_ffmpeg_enabled=values.get("manual_ffmpeg_enabled") is True,
            ffmpeg_bin_dir=_text(values.get("ffmpeg_bin_dir")),
            manual_js_runtime_enabled=values.get("manual_js_runtime_enabled") is True,
            js_runtime_bin_dir=_text(values.get("js_runtime_bin_dir")),
            ignored_missing_dependencies=_ignored_dependencies(values.get("ignored_missing_dependencies")),
            auto_check_updates=values.get("auto_check_updates") is not False,
            last_update_check_at=_text(values.get("last_update_check_at")),
            worker_count=_worker_count(values.get("worker_count", values.get("concurrency", 1))),
            download_column_widths=_table_column_widths(values.get("download_column_widths"), 4, 48),
            subtitle_column_widths=_table_column_widths(values.get("subtitle_column_widths"), 6, 48),
            queue_column_widths=_table_column_widths(values.get("queue_column_widths"), 7, 60),
            conversion_splitter_sizes=_splitter_sizes(values.get("conversion_splitter_sizes")),
            geometry=_decode_bytes(values.get("geometry")),
            window_state=_decode_bytes(values.get("window_state")),
        )


class AppStorage:
    """以 schema-versioned JSON 保存設定與未完成任務"""

    SCHEMA_VERSION = SCHEMA_VERSION
    SETTINGS_FILENAME = "settings.json"
    TASKS_FILENAME = "tasks.json"

    def __init__(self, app_dir: Path | str | None = None, logger: logging.Logger | None = None):
        if app_dir is None:
            location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
            app_dir = Path(location) if location else Path.home() / ".mochistar"
        self._app_dir = Path(app_dir)
        self.logger = logger or logging.getLogger(__name__)

    @property
    def app_dir(self) -> Path:
        return self._app_dir

    def load_settings(self) -> Settings:
        """讀取設定, 缺失或損壞時使用預設值"""
        document = self._load_document(self.SETTINGS_FILENAME)
        if document is None: return Settings()
        settings = Settings.from_dict(document.get("settings"))
        if not settings.output_dir or not Path(settings.output_dir).expanduser().is_dir():
            if settings.output_dir:
                self.logger.warning("Configured output directory is unavailable: %s", settings.output_dir)
            settings.output_dir = _default_output_dir()
        conversion_dir = Path(settings.last_conversion_output_dir).expanduser()
        if settings.last_conversion_output_dir and not conversion_dir.is_dir():
            settings.last_conversion_output_dir = settings.output_dir
        return settings

    def save_settings(self, settings: Settings) -> bool:
        """原子寫入應用程式設定"""
        return self._save_document(self.SETTINGS_FILENAME, {"settings": settings.to_dict()})

    def load_tasks(self) -> list[TaskRecord]:
        """讀取未完成任務並把中斷的 running 任務改為 paused"""
        document = self._load_document(self.TASKS_FILENAME)
        if document is None: return []
        raw_tasks = document.get("tasks")
        if not isinstance(raw_tasks, list):
            self.logger.warning("Invalid tasks payload, using an empty queue")
            return []
        tasks: list[TaskRecord] = []
        for raw_task in raw_tasks:
            if not isinstance(raw_task, Mapping): continue
            task = TaskRecord.from_dict(raw_task)
            if task.status is TaskStatus.COMPLETED: continue
            if task.status is TaskStatus.RUNNING:
                task.status = TaskStatus.PAUSED
                if not task.error: task.error = "Interrupted before application restart"
            tasks.append(task)
        return tasks

    def save_tasks(self, tasks: Iterable[TaskRecord]) -> bool:
        """只保存未完成 queue tasks"""
        pending = [task.to_dict() for task in tasks if task.status is not TaskStatus.COMPLETED]
        return self._save_document(self.TASKS_FILENAME, {"tasks": pending})

    def _load_document(self, filename: str) -> dict[str, Any] | None:
        path = self.app_dir / filename
        if not path.is_file(): return None
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            self.logger.warning("Unable to read %s, using defaults: %s", filename, error)
            return None
        if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
            self.logger.warning("Unsupported or missing schema version in %s, using defaults", filename)
            return None
        return document

    def _save_document(self, filename: str, values: Mapping[str, Any]) -> bool:
        document = {"schema_version": SCHEMA_VERSION, **values}
        try:
            payload = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
            self.app_dir.mkdir(parents=True, exist_ok=True)
            save_file = QSaveFile(str(self.app_dir / filename))
            if not save_file.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(save_file.errorString())
            if save_file.write(payload) != len(payload):
                error = save_file.errorString()
                save_file.cancelWriting()
                raise OSError(error)
            if not save_file.commit(): raise OSError(save_file.errorString())
            return True
        except (OSError, TypeError, ValueError) as error:
            self.logger.error("Unable to save %s: %s", filename, error)
            return False
