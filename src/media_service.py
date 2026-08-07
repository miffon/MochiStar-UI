from __future__ import annotations

import logging
import os
import re
import shutil
from collections import defaultdict
from pathlib import Path
from threading import Event, RLock
from typing import Any, Callable

from yt_dlp import YoutubeDL
from yt_dlp.utils import remove_terminal_sequences

from executable_finder import find_executable
from models import CookieConfig, DownloadOptions, FormatInfo, MediaInfo, SubtitleOptions, SubtitleTrack, TaskRecord

ProgressCallback = Callable[..., None]
LogCallback = Callable[[str], None]


class ServiceCancelled(Exception):
    """使用者取消背景 media 工作"""


class _YtDlpLogger:
    """把 yt-dlp logger 轉送到 application Log"""

    def __init__(self, callback: LogCallback):
        self.callback = callback

    def debug(self, message: str) -> None:
        self.callback(remove_terminal_sequences(message))

    def info(self, message: str) -> None:
        self.callback(remove_terminal_sequences(message))

    def warning(self, message: str) -> None:
        message = remove_terminal_sequences(message)
        self.callback(message if message.startswith("WARNING:") else f"WARNING: {message}")

    def error(self, message: str) -> None:
        message = remove_terminal_sequences(message)
        self.callback(message if message.startswith("ERROR:") else f"ERROR: {message}")


def _notify_progress(callback: ProgressCallback, progress: float | None, detail: str = "") -> None:
    """呼叫 progress callback, 同時相容單參數 callback"""
    try:
        callback(progress, detail)
    except TypeError:
        callback(progress)


class _CollisionSafeYoutubeDL(YoutubeDL):
    """讓 yt-dlp 遇到既有輸出檔時自動加入流水號"""

    _claim_lock = RLock()
    _claimed_paths: set[str] = set()

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._resolved_paths: dict[tuple[str, str], str] = {}
        self._owned_claims: set[str] = set()

    def prepare_filename(
        self,
        info_dict: dict[str, Any],
        dir_type: str = "",
        *,
        outtmpl: str | None = None,
        warn: bool = False,
    ) -> str:
        """保留 yt-dlp 檔名清理規則, 並避免略過同名檔案"""
        filename = super().prepare_filename(info_dict, dir_type, outtmpl=outtmpl, warn=warn)
        if not filename or filename == "-": return filename
        media_key = str(info_dict.get("id") or info_dict.get("display_id") or id(info_dict))
        cache_key = (filename, media_key)
        if cache_key in self._resolved_paths: return self._resolved_paths[cache_key]

        with self._claim_lock:
            candidate = Path(filename)
            number = 0
            while candidate.exists() or self._claim_key(candidate) in self._claimed_paths:
                number += 1
                candidate = Path(filename).with_name(f"{Path(filename).stem} ({number}){Path(filename).suffix}")
            claim = self._claim_key(candidate)
            self._claimed_paths.add(claim)
            self._owned_claims.add(claim)
            self._resolved_paths[cache_key] = str(candidate)
        return str(candidate)

    def close(self) -> None:
        """關閉 yt-dlp 並釋放跨 worker 的檔名保留"""
        try:
            super().close()
        finally:
            with self._claim_lock:
                self._claimed_paths.difference_update(self._owned_claims)
                self._owned_claims.clear()

    @staticmethod
    def _claim_key(path: Path) -> str:
        """建立跨 worker 比對用的絕對路徑 key"""
        value = str(path.absolute())
        return os.path.normcase(value)


class YtDlpService:
    """封裝 yt-dlp 分析與下載流程"""

    OUTPUT_TEMPLATE = "%(title).180B.%(ext)s"

    def __init__(
        self,
        youtube_dl_factory: Callable[..., YoutubeDL] = _CollisionSafeYoutubeDL,
        which: Callable[[str], str | None] = shutil.which,
        ffmpeg_directory: str = "",
        js_runtime_directory: str = "",
    ):
        self.youtube_dl_factory = youtube_dl_factory
        self.which = which
        self.ffmpeg_directory = ffmpeg_directory
        self.js_runtime_directory = js_runtime_directory
        self._validated_runtimes: dict[str, str] | None = None

    def configure_tools(self, ffmpeg_directory: str = "", js_runtime_directory: str = "") -> None:
        """更新後續 yt-dlp 工作使用的私有工具目錄"""
        self.ffmpeg_directory = ffmpeg_directory
        self.js_runtime_directory = js_runtime_directory
        self._validated_runtimes = None

    def set_validated_runtimes(self, runtimes: dict[str, str]) -> None:
        """套用已通過版本檢查的 JavaScript runtime"""
        self._validated_runtimes = dict(runtimes)

    def cookie_options(self, cookie: CookieConfig | None) -> dict[str, Any]:
        """把 CookieConfig 轉成 yt-dlp options"""
        if cookie is None: return {}
        source = cookie.source.strip().lower()
        if source in {"file", "cookiefile", "netscape"} and cookie.file_path:
            return {"cookiefile": cookie.file_path}
        if source in {"browser", "cookiesfrombrowser"} and cookie.browser:
            browser_config: tuple[str, ...] = (cookie.browser,)
            if cookie.profile: browser_config += (cookie.profile,)
            return {"cookiesfrombrowser": browser_config}
        return {}

    def runtime_options(self) -> dict[str, Any]:
        """設定 JavaScript runtime 與官方 EJS challenge solver"""
        options: dict[str, Any] = {"remote_components": ["ejs:github"]}
        runtimes = {name: {"path": path} for name, path in self.detect_js_runtimes().items()}
        if runtimes: options["js_runtimes"] = runtimes
        elif self._validated_runtimes is not None or self.js_runtime_directory: options["js_runtimes"] = {}
        return options

    def detect_js_runtimes(self) -> dict[str, str]:
        """依 yt-dlp priority 列出可用 JavaScript runtimes"""
        if self._validated_runtimes is not None: return dict(self._validated_runtimes)
        return {
            name: path
            for name, executable in (("deno", "deno"), ("node", "node"), ("quickjs", "qjs"), ("bun", "bun"))
            if (path := self._find_executable(executable, self.js_runtime_directory))
        }

    def ffmpeg_options(self) -> dict[str, str]:
        """設定 yt-dlp 使用的 FFmpeg 目錄"""
        return {"ffmpeg_location": self.ffmpeg_directory} if self.ffmpeg_directory else {}

    def analyze(
        self,
        url: str,
        cookie: CookieConfig | None = None,
        cancel_event: Event | None = None,
        progress_cb: Callable[[int, int], None] | None = None,
        include_automatic_subtitles: bool = True,
    ) -> MediaInfo:
        """分析單一 URL 並正規化單片或 playlist metadata"""
        if not url.strip(): raise ValueError("URL is required")
        self._check_cancelled(cancel_event)

        def analysis_log(message: str) -> None:
            """轉送 yt-dlp log 並擷取 playlist item 進度"""
            self._check_cancelled(cancel_event)
            text = remove_terminal_sequences(message)
            match = re.search(r"\bDownloading item (\d+) of (\d+)\b", text)
            if match and progress_cb: progress_cb(int(match.group(1)), int(match.group(2)))
            logging.getLogger("yt_dlp").info(text)

        options = {
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "skip_download": True,
            "color": "no_color",
            "logger": _YtDlpLogger(analysis_log),
            **self.runtime_options(),
            **self.cookie_options(cookie),
        }
        try:
            with self.youtube_dl_factory(options) as ydl:
                info = ydl.extract_info(url.strip(), download=False)
        except Exception as error:
            if cancel_event and cancel_event.is_set(): raise ServiceCancelled("Analysis cancelled") from error
            raise
        self._check_cancelled(cancel_event)
        if not info: raise ValueError("yt-dlp returned no media information")
        if info.get("_type") in {"playlist", "multi_video"} and not any(info.get("entries") or []):
            raise ValueError("No accessible playlist items were found")
        return self.map_media_info(
            info, fallback_url=url.strip(), include_automatic_subtitles=include_automatic_subtitles
        )

    def map_media_info(
        self, info: dict[str, Any], fallback_url: str = "", include_automatic_subtitles: bool = True
    ) -> MediaInfo:
        """把 yt-dlp info dict 轉成 typed MediaInfo"""
        entries = [
            self.map_media_info(
                entry, fallback_url=entry.get("url", ""),
                include_automatic_subtitles=include_automatic_subtitles,
            )
            for entry in info.get("entries") or []
            if entry
        ]
        formats = [self.map_format(item) for item in info.get("formats") or [] if item]
        subtitles = self.map_subtitles(info.get("subtitles"), "manual")
        if include_automatic_subtitles:
            subtitles += self.map_subtitles(info.get("automatic_captions"), "automatic")
        return MediaInfo(
            media_id=str(info.get("id") or ""),
            title=str(info.get("title") or info.get("playlist_title") or "Untitled"),
            uploader=str(info.get("uploader") or info.get("channel") or ""),
            duration=self._number(info.get("duration")),
            site=str(info.get("extractor_key") or info.get("extractor") or ""),
            webpage_url=str(info.get("webpage_url") or info.get("original_url") or fallback_url),
            thumbnail=str(info.get("thumbnail") or ""),
            formats=formats,
            subtitles=subtitles,
            entries=entries,
        )

    @staticmethod
    def map_subtitles(values: Any, source: str) -> list[SubtitleTrack]:
        """把 yt-dlp subtitles dict 轉成可選 track"""
        if not isinstance(values, dict): return []
        tracks = []
        for language, raw_formats in values.items():
            if not isinstance(raw_formats, list): continue
            formats = []
            name = ""
            for item in raw_formats:
                if not isinstance(item, dict): continue
                extension = str(item.get("ext") or "").strip().lower()
                if extension and extension not in formats: formats.append(extension)
                if not name: name = str(item.get("name") or "")
            if formats:
                tracks.append(SubtitleTrack(str(language), name, source, formats))
        return tracks

    def map_format(self, item: dict[str, Any]) -> FormatInfo:
        """把 yt-dlp format dict 轉成 FormatInfo"""
        width = self._integer(item.get("width"))
        height = self._integer(item.get("height"))
        resolution = str(item.get("resolution") or "")
        if not resolution and width and height: resolution = f"{width}x{height}"
        if not resolution and height: resolution = f"{height}p"
        filesize = item.get("filesize") or item.get("filesize_approx")
        return FormatInfo(
            format_id=str(item.get("format_id") or ""),
            extension=str(item.get("ext") or ""),
            resolution=resolution,
            fps=self._number(item.get("fps")),
            video_codec=str(item.get("vcodec") or ""),
            audio_codec=str(item.get("acodec") or ""),
            filesize=self._integer(filesize),
            width=width,
            height=height,
        )

    def build_download_options(
        self,
        options: DownloadOptions,
        progress_hooks: list[Callable[[dict[str, Any]], None]] | None = None,
        logger: Any | None = None,
    ) -> dict[str, Any]:
        """建立可直接傳給 YoutubeDL 的下載 options"""
        output_dir = str(Path(options.output_dir or ".").expanduser())
        result: dict[str, Any] = {
            "paths": {"home": output_dir},
            "outtmpl": {"default": self.OUTPUT_TEMPLATE},
            "format": self.build_format_selector(options),
            "color": "no_color",
            "continuedl": True,
            "overwrites": False,
            "nopart": False,
            "windowsfilenames": os.name == "nt",
            **self.runtime_options(),
            **self.ffmpeg_options(),
            **self.cookie_options(options.cookie),
        }
        if progress_hooks: result["progress_hooks"] = progress_hooks
        if logger is not None: result["logger"] = logger
        if options.playlist_item_ids: result["match_filter"] = self.build_playlist_filter(options.playlist_item_ids)

        preset = options.preset.strip().lower()
        audio_output = options.audio_output.strip().lower()
        container = options.video_container.strip().lower()
        if preset in {"audio", "audio_only"} and audio_output not in {"", "original", "auto"}:
            result["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_output,
                "preferredquality": "0",
                "nopostoverwrites": True,
            }]
            result["final_ext"] = audio_output
        elif preset not in {"audio", "audio_only"} and container not in {"", "auto", "original"}:
            result["merge_output_format"] = container
            result["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": container}]
            result["final_ext"] = container
        return result

    def build_subtitle_options(
        self,
        options: SubtitleOptions,
        source: str,
        language_format: str,
        languages: list[str],
        progress_hooks: list[Callable[[dict[str, Any]], None]] | None = None,
        logger: Any | None = None,
    ) -> dict[str, Any]:
        """建立只下載 sidecar 字幕的 yt-dlp options"""
        output_dir = str(Path(options.output_dir or ".").expanduser())
        subtitle_template = "%(title).180B.%(language)s.auto.%(ext)s" if source == "automatic" else (
            "%(title).180B.%(language)s.%(ext)s"
        )
        result: dict[str, Any] = {
            "paths": {"home": output_dir},
            "outtmpl": {"default": self.OUTPUT_TEMPLATE, "subtitle": subtitle_template},
            "skip_download": True,
            "writesubtitles": source == "manual",
            "writeautomaticsub": source == "automatic",
            "subtitleslangs": languages,
            "subtitlesformat": language_format or "best",
            "overwrites": False,
            "windowsfilenames": os.name == "nt",
            "color": "no_color",
            **self.runtime_options(),
            **self.ffmpeg_options(),
            **self.cookie_options(options.cookie),
        }
        if progress_hooks: result["progress_hooks"] = progress_hooks
        if logger is not None: result["logger"] = logger
        return result

    @staticmethod
    def build_playlist_filter(media_ids: list[str]) -> Callable[..., str | None]:
        """建立依 media ID 過濾 playlist entry 的 yt-dlp match_filter"""
        selected = set(map(str, media_ids))

        def match_filter(info: dict[str, Any], *_args: Any, **_kwargs: Any) -> str | None:
            if info.get("_type") in {"playlist", "multi_video"}: return None
            media_id = str(info.get("id") or "")
            return None if not media_id or media_id in selected else "Not selected in the download queue"

        return match_filter

    def build_format_selector(self, options: DownloadOptions) -> str:
        """依 preset、解析度與 advanced format ID 建立 yt-dlp selector"""
        video_id, audio_id = options.video_format_id.strip(), options.audio_format_id.strip()
        if video_id and audio_id: return f"{video_id}+{audio_id}"
        if video_id: return video_id
        if audio_id: return audio_id

        preset = options.preset.strip().lower()
        height = self._resolution_height(options.resolution)
        height_filter = f"[height<={height}]" if height else ""
        if preset in {"video", "video_only"}: return f"bestvideo{height_filter}"
        if preset in {"audio", "audio_only"}: return "bestaudio/best"
        return f"bestvideo*{height_filter}+bestaudio/best{height_filter or ''}"

    def execute_download(
        self,
        task: TaskRecord,
        progress_cb: ProgressCallback,
        log_cb: LogCallback,
        cancel_event: Event,
    ) -> str:
        """執行下載並回傳 yt-dlp 判定的最終輸出路徑"""
        options = task.download_options
        if options is None: raise ValueError("Download task is missing download_options")
        self._check_cancelled(cancel_event)
        Path(options.output_dir or ".").expanduser().mkdir(parents=True, exist_ok=True)
        latest_path = ""

        def progress_hook(data: dict[str, Any]) -> None:
            nonlocal latest_path
            if cancel_event.is_set(): raise ServiceCancelled("Download cancelled")
            latest_path = str(data.get("filename") or data.get("info_dict", {}).get("_filename") or latest_path)
            status = str(data.get("status") or "")
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes")
            progress = min(1.0, float(downloaded) / float(total)) if downloaded is not None and total else -1.0
            detail = self._progress_detail(data)
            if status == "finished": progress = 1.0
            _notify_progress(progress_cb, progress, detail)

        ydl_options = self.build_download_options(
            options,
            progress_hooks=[progress_hook],
            logger=_YtDlpLogger(log_cb),
        )
        try:
            with self.youtube_dl_factory(ydl_options) as ydl:
                info = ydl.extract_info(options.url, download=True)
                final_path = self._download_result_path(ydl, info) or latest_path
        except ServiceCancelled:
            raise
        except Exception as error:
            if cancel_event.is_set(): raise ServiceCancelled("Download cancelled") from error
            raise
        self._check_cancelled(cancel_event)
        _notify_progress(progress_cb, 1.0, "Download completed")
        return str(final_path)

    def execute_subtitle(
        self,
        task: TaskRecord,
        progress_cb: ProgressCallback,
        log_cb: LogCallback,
        cancel_event: Event,
    ) -> str:
        """依來源與格式分組下載 sidecar 字幕"""
        options = task.subtitle_options
        if options is None: raise ValueError("Subtitle task is missing subtitle_options")
        if not options.selections: raise ValueError("Subtitle task has no selected tracks")
        self._check_cancelled(cancel_event)
        output_dir = Path(options.output_dir or ".").expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
        for selection in options.selections:
            key = (selection.source, selection.format or "best")
            if selection.language and selection.language not in grouped[key]: grouped[key].append(selection.language)
        groups = list(grouped.items())

        for group_index, ((source, subtitle_format), languages) in enumerate(groups):
            def progress_hook(data: dict[str, Any], current=group_index) -> None:
                if cancel_event.is_set(): raise ServiceCancelled("Subtitle download cancelled")
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                downloaded = data.get("downloaded_bytes")
                local_progress = float(downloaded) / float(total) if downloaded is not None and total else 0.0
                progress = min(1.0, (current + local_progress) / len(groups))
                _notify_progress(progress_cb, progress, self._progress_detail(data))

            ydl_options = self.build_subtitle_options(
                options,
                source,
                subtitle_format,
                languages,
                progress_hooks=[progress_hook],
                logger=_YtDlpLogger(log_cb),
            )
            try:
                with self.youtube_dl_factory(ydl_options) as ydl:
                    ydl.extract_info(options.url, download=True)
            except ServiceCancelled:
                raise
            except Exception as error:
                if cancel_event.is_set(): raise ServiceCancelled("Subtitle download cancelled") from error
                raise
            _notify_progress(
                progress_cb,
                (group_index + 1) / len(groups),
                f"Downloaded subtitle group {group_index + 1}/{len(groups)}",
            )
        self._check_cancelled(cancel_event)
        _notify_progress(progress_cb, 1.0, "Subtitle download completed")
        return str(output_dir.resolve())

    @staticmethod
    def _download_result_path(ydl: YoutubeDL, info: dict[str, Any] | None) -> str:
        if not info: return ""
        for key in ("filepath", "_filename", "filename"):
            if info.get(key): return str(info[key])
        requested = info.get("requested_downloads") or []
        for item in reversed(requested):
            for key in ("filepath", "_filename", "filename"):
                if item.get(key): return str(item[key])
        entries = info.get("entries") or []
        if entries: return YtDlpService._download_result_path(ydl, entries[-1])
        try:
            return str(ydl.prepare_filename(info))
        except Exception:
            return ""

    @staticmethod
    def _progress_detail(data: dict[str, Any]) -> str:
        parts = []
        speed = data.get("speed")
        eta = data.get("eta")
        if speed: parts.append(f"{float(speed) / 1024 / 1024:.1f} MiB/s")
        if eta is not None: parts.append(f"ETA {int(eta)}s")
        return " · ".join(parts) or str(data.get("status") or "")

    @staticmethod
    def _resolution_height(resolution: str) -> int | None:
        value = resolution.strip().lower()
        if value in {"", "best", "auto"}: return None
        try:
            return int(value.removesuffix("p"))
        except ValueError:
            return None

    @staticmethod
    def _check_cancelled(cancel_event: Event | None) -> None:
        if cancel_event and cancel_event.is_set(): raise ServiceCancelled("Operation cancelled")

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _integer(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _find_executable(self, name: str, directory: str = "") -> str | None:
        """從指定目錄或系統 PATH 尋找 executable"""
        if directory: return find_executable(name, directory, shutil.which)
        return find_executable(name, which=self.which)
