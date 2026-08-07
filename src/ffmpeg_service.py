from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from executable_finder import find_executable
from media_service import LogCallback, ProgressCallback, ServiceCancelled, _notify_progress
from models import ConversionOptions, ReplacementOptions, TaskRecord


class FFmpegError(RuntimeError):
    """FFmpeg 或 FFprobe 執行失敗"""


def _number(value: Any) -> float | None:
    """將 ffprobe 數值安全轉成 float"""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class FFmpegService:
    """封裝 FFmpeg 偵測、命令生成與轉檔流程"""

    VIDEO_FORMATS = {"mp4", "mov", "mkv", "webm"}
    AUDIO_FORMATS = {"mp3", "m4a", "opus", "flac", "wav"}
    SUBTITLE_FORMATS = {"srt", "vtt", "ass"}
    SUBTITLE_CODECS = {"srt": "subrip", "vtt": "webvtt", "ass": "ass"}
    STREAM_COPY_CODECS = {
        "mp4": {
            "video": {"h264", "hevc", "mpeg4", "av1"},
            "audio": {"aac", "mp3", "ac3", "eac3", "alac"},
        },
        "mov": {
            "video": {"h264", "hevc", "mpeg4", "av1", "prores"},
            "audio": {"aac", "mp3", "ac3", "eac3", "alac", "pcm_s16le", "pcm_s24le", "pcm_s32le"},
        },
        "mkv": {"video": None, "audio": None},
        "webm": {"video": {"vp8", "vp9", "av1"}, "audio": {"vorbis", "opus"}},
        "mp3": {"audio": {"mp3"}},
        "m4a": {"audio": {"aac", "alac"}},
        "opus": {"audio": {"opus"}},
        "flac": {"audio": {"flac"}},
        "wav": {"audio": {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le"}},
    }
    SOFTWARE_CODECS = {
        "mp4": ("libx264", "aac"),
        "mov": ("libx264", "aac"),
        "mkv": ("libx264", "aac"),
        "webm": ("libvpx-vp9", "libopus"),
        "mp3": ("", "libmp3lame"),
        "m4a": ("", "aac"),
        "opus": ("", "libopus"),
        "flac": ("", "flac"),
        "wav": ("", "pcm_s16le"),
    }
    HARDWARE_ENCODERS = {"nvidia": "h264_nvenc", "amd": "h264_amf", "intel": "h264_qsv"}
    HARDWARE_PRIORITY = ("nvidia", "amd", "intel")
    PRORES_PROFILES = {"proxy": "0", "lt": "1", "422": "2", "hq": "3"}
    MISSING_STREAM_ERRORS = {
        "video": "The input does not contain a video stream",
        "audio": "The input does not contain an audio stream",
        "subtitle": "The input does not contain a subtitle stream",
    }

    def __init__(
        self,
        which: Callable[[str], str | None] | None = None,
        run_command: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        popen_factory: Callable[..., subprocess.Popen[str]] | None = None,
        tool_directory: str = "",
    ):
        self.which = which or shutil.which
        self.run_command = run_command or subprocess.run
        self.popen_factory = popen_factory or subprocess.Popen
        self.tool_directory = tool_directory
        self.tool_paths: dict[str, str | None] = {}
        self._hardware_backends: list[str] | None = None
        self.detect_tools()

    def configure_tools(self, tool_directory: str = "") -> dict[str, str | None]:
        """更新 FFmpeg 私有搜尋目錄並重新偵測"""
        self.tool_directory = tool_directory
        self._hardware_backends = None
        return self.detect_tools()

    def set_validated_tools(self, ffmpeg_path: str = "", ffprobe_path: str = "") -> None:
        """套用已通過執行檢查的 FFmpeg 與 FFprobe 路徑"""
        self.tool_paths = {"ffmpeg": ffmpeg_path or None, "ffprobe": ffprobe_path or None}
        self._hardware_backends = None

    def detect_tools(self) -> dict[str, str | None]:
        """重新偵測 PATH 內的外部工具並回傳實際路徑"""
        self.tool_paths = {
            name: find_executable(name, self.tool_directory, self.which)
            for name in ("ffmpeg", "ffprobe")
        }
        return dict(self.tool_paths)

    @property
    def availability(self) -> dict[str, bool]:
        return {name: bool(path) for name, path in self.tool_paths.items()}

    @property
    def ffmpeg_path(self) -> str | None:
        return self.tool_paths.get("ffmpeg")

    @property
    def ffprobe_path(self) -> str | None:
        return self.tool_paths.get("ffprobe")

    @property
    def ffmpeg_available(self) -> bool:
        return bool(self.ffmpeg_path)

    @property
    def ffprobe_available(self) -> bool:
        return bool(self.ffprobe_path)

    def list_hardware_encoders(self) -> list[str]:
        """列出 FFmpeg 實際提供的 NVIDIA、AMD、Intel video encoder"""
        if not self.ffmpeg_path: return []
        result = self.run_command(
            [self.ffmpeg_path, "-hide_banner", "-encoders"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
            **self._window_flags(),
        )
        output = f"{result.stdout or ''}\n{result.stderr or ''}"
        encoders = []
        for line in output.splitlines():
            match = re.match(r"^\s*[VAS\.FSXBD]{6}\s+(\S+)", line)
            if not match: continue
            name = match.group(1)
            if name.endswith(("_nvenc", "_amf", "_qsv")): encoders.append(name)
        return sorted(set(encoders))

    get_available_hardware_encoders = list_hardware_encoders

    def list_hardware_backends(self) -> list[str]:
        """實際初始化 H.264 encoder, 只回傳本機可用的硬體品牌"""
        if self._hardware_backends is not None: return list(self._hardware_backends)
        compiled = set(self.list_hardware_encoders())
        available = []
        for backend in self.HARDWARE_PRIORITY:
            encoder = self.HARDWARE_ENCODERS[backend]
            if encoder not in compiled: continue
            result = self.run_command(
                [
                    self.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=size=256x256:rate=30", "-frames:v", "1", "-c:v", encoder,
                    "-f", "null", os.devnull,
                ],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                errors="replace", check=False, timeout=10, **self._window_flags(),
            )
            if result.returncode == 0: available.append(backend)
        self._hardware_backends = available
        return list(available)

    def probe(self, input_path: str | Path) -> dict[str, Any]:
        """讀取 duration 與 stream codec 資訊"""
        if not self.ffprobe_path: raise FileNotFoundError("ffprobe was not found in PATH")
        command = [
            self.ffprobe_path,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            str(input_path),
        ]
        try:
            result = self.run_command(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
                **self._window_flags(),
            )
            data = json.loads(result.stdout or "{}")
        except (subprocess.CalledProcessError, json.JSONDecodeError) as error:
            detail = getattr(error, "stderr", "") or str(error)
            raise FFmpegError(f"FFprobe failed: {detail}") from error
        data["duration"] = self._probe_duration(data)
        return data

    probe_media = probe

    def analyze_file(self, input_path: str | Path) -> dict[str, Any]:
        """讀取媒體資訊並掃描第一條影片串流的 GOP 間隔"""
        return self.analyze_gop(input_path, self.probe(input_path))

    def analyze_gop(self, input_path: str | Path, probe: dict[str, Any] | None = None) -> dict[str, Any]:
        """在既有 metadata 上補充第一條影片串流的 GOP 分析"""
        data = dict(probe) if probe is not None else self.probe(input_path)
        if not self.has_media_stream(data, "video"): return data
        command = [
            self.ffprobe_path, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "frame=key_frame", "-of", "csv=p=0", str(input_path),
        ]
        try:
            result = self.run_command(
                command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                encoding="utf-8", errors="replace", check=True, **self._window_flags(),
            )
            flags = []
            for line in (result.stdout or "").splitlines():
                value = line.strip().split(",", 1)[0]
                if value in {"0", "1"}: flags.append(int(value))
            keyframes = [index for index, flag in enumerate(flags) if flag == 1]
            intervals = [right - left for left, right in zip(keyframes, keyframes[1:])]
            if intervals:
                data["gop_analysis"] = {
                    "value": Counter(intervals).most_common(1)[0][0],
                    "average": sum(intervals) / len(intervals),
                    "minimum": min(intervals), "maximum": max(intervals),
                    "keyframes": len(keyframes), "frames_scanned": len(flags),
                }
            else:
                data["gop_analysis"] = {
                    "value": None, "keyframes": len(keyframes), "frames_scanned": len(flags),
                }
        except (subprocess.CalledProcessError, OSError) as error:
            data["gop_analysis"] = {"value": None, "error": getattr(error, "stderr", "") or str(error)}
        return data

    def probe_duration(self, input_path: str | Path) -> float | None:
        return self.probe(input_path).get("duration")

    def probe_streams(self, input_path: str | Path) -> list[dict[str, Any]]:
        return list(self.probe(input_path).get("streams") or [])

    @staticmethod
    def has_media_stream(probe: dict[str, Any], media_type: str) -> bool:
        """檢查必要 stream, 影片封面圖不視為實際畫面"""
        for stream in probe.get("streams") or []:
            if stream.get("codec_type") != media_type: continue
            attached = (stream.get("disposition") or {}).get("attached_pic") in {1, True, "1"}
            if media_type != "video" or not attached: return True
        return False

    def required_stream_type(self, target_format: str) -> str:
        """依輸出格式取得必要的來源 stream 類型"""
        target = target_format.lower().lstrip(".")
        if target in self.VIDEO_FORMATS: return "video"
        if target in self.AUDIO_FORMATS: return "audio"
        if target in self.SUBTITLE_FORMATS: return "subtitle"
        return ""

    def validate_stream_copy(
        self,
        input_path_or_probe: str | Path | dict[str, Any],
        target_format: str,
    ) -> tuple[bool, str]:
        """檢查輸入 stream codec 是否能直接封裝到指定 container"""
        target = target_format.lower().lstrip(".")
        if target not in self.STREAM_COPY_CODECS: return False, f"Unsupported output format: {target}"
        probe = input_path_or_probe if isinstance(input_path_or_probe, dict) else self.probe(input_path_or_probe)
        streams = probe.get("streams") or []
        video_codecs = [str(item.get("codec_name") or "") for item in streams if item.get("codec_type") == "video"]
        audio_codecs = [str(item.get("codec_name") or "") for item in streams if item.get("codec_type") == "audio"]
        if target in self.AUDIO_FORMATS:
            if not audio_codecs: return False, "The input does not contain an audio stream"
            allowed = self.STREAM_COPY_CODECS[target]["audio"]
            if audio_codecs[0] not in allowed:
                return False, f"{audio_codecs[0] or 'Unknown'} audio is not compatible with {target.upper()}"
            return True, ""

        if not video_codecs and not audio_codecs: return False, "The input does not contain media streams"
        rules = self.STREAM_COPY_CODECS[target]
        if rules["video"] is not None:
            incompatible = [codec for codec in video_codecs if codec not in rules["video"]]
            if incompatible: return False, f"{incompatible[0] or 'Unknown'} video is not compatible with {target.upper()}"
        if rules["audio"] is not None:
            incompatible = [codec for codec in audio_codecs if codec not in rules["audio"]]
            if incompatible: return False, f"{incompatible[0] or 'Unknown'} audio is not compatible with {target.upper()}"
        return True, ""

    def can_stream_copy(self, input_path_or_probe: str | Path | dict[str, Any], target_format: str) -> bool:
        return self.validate_stream_copy(input_path_or_probe, target_format)[0]

    def ensure_stream_copy_compatible(
        self,
        input_path_or_probe: str | Path | dict[str, Any],
        target_format: str,
    ) -> None:
        compatible, reason = self.validate_stream_copy(input_path_or_probe, target_format)
        if not compatible: raise FFmpegError(reason)

    def collision_safe_output(
        self,
        input_path: str | Path,
        output_dir: str | Path,
        target_format: str,
    ) -> Path:
        """產生不覆寫既有檔案的輸出路徑"""
        source = Path(input_path).expanduser()
        directory = Path(output_dir).expanduser() if str(output_dir) else source.parent
        extension = target_format.lower().lstrip(".")
        candidate = directory / f"{source.stem}.{extension}"
        try:
            same_as_source = candidate.resolve() == source.resolve()
        except OSError:
            same_as_source = candidate.absolute() == source.absolute()
        if not candidate.exists() and not same_as_source: return candidate
        number = 1
        while True:
            candidate = directory / f"{source.stem} ({number}).{extension}"
            if not candidate.exists(): return candidate
            number += 1

    def build_command(
        self,
        options: ConversionOptions,
        output_path: str | Path | None = None,
        pass_number: int | None = None,
        passlog_path: str | Path | None = None,
    ) -> list[str]:
        """建立跨平台 FFmpeg command, 不執行 subprocess"""
        if not self.ffmpeg_path: raise FileNotFoundError("ffmpeg was not found in PATH")
        target = options.target_format.lower().lstrip(".")
        if target not in self.VIDEO_FORMATS | self.AUDIO_FORMATS | self.SUBTITLE_FORMATS:
            raise ValueError(f"Unsupported output format: {target}")
        validation_error = self.validate_options(options)
        if validation_error: raise ValueError(validation_error)
        output = Path(output_path) if output_path else self.collision_safe_output(
            options.input_path,
            options.output_dir,
            target,
        )
        command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-nostdin",
            "-i", options.input_path,
            "-progress", "pipe:1",
            "-nostats",
        ]
        audio_only = target in self.AUDIO_FORMATS
        subtitle_only = target in self.SUBTITLE_FORMATS
        if subtitle_only: command += ["-map", "0:s:0", "-c:s", self.SUBTITLE_CODECS[target]]
        elif audio_only: command += ["-map", "0:a:0", "-vn"]
        elif pass_number == 1: command += ["-map", "0:v:0"]
        else: command += ["-map", "0:v:0", "-map", "0:a?"]

        if subtitle_only:
            pass
        elif options.stream_copy:
            command += ["-c", "copy"] if not audio_only else ["-c:a", "copy"]
        elif audio_only:
            command += ["-c:a", self.SOFTWARE_CODECS[target][1]]
            if options.audio_bitrate is not None: command += ["-b:a", f"{options.audio_bitrate}k"]
            if options.audio_sample_rate is not None: command += ["-ar", str(options.audio_sample_rate)]
        else:
            video_codec, _reason = self.resolve_video_encoder(options)
            command += ["-c:v", video_codec]
            command += self._video_arguments(options, video_codec)
            command += self._video_filters(options)
            if pass_number == 1: command += ["-an"]
            else: command += self._audio_arguments(options, target, video_codec)
        if pass_number is not None:
            command += ["-pass", str(pass_number), "-passlogfile", str(passlog_path)]
        if pass_number == 1:
            command += ["-f", "null", "-y", os.devnull]
            return command
        command += ["-n", str(output)]
        return command

    def validate_options(self, options: ConversionOptions) -> str:
        """驗證專業轉檔欄位與 container 相容性"""
        target, codec = options.target_format.lower().lstrip("."), options.video_codec.lower()
        if options.stream_copy or target in self.SUBTITLE_FORMATS: return ""
        if options.audio_sample_rate not in {None, 44100, 48000, 96000, 192000}:
            return "Unsupported audio sample rate"
        if target in self.AUDIO_FORMATS:
            if options.audio_bitrate is not None and options.audio_bitrate <= 0:
                return "Audio bitrate must be greater than zero"
            if target in {"flac", "wav"} and options.audio_bitrate is not None:
                return "Audio bitrate is unavailable for lossless output"
            if target == "mp3" and options.audio_bitrate is not None and options.audio_bitrate > 320:
                return "MP3 bitrate must not exceed 320 kbps"
            if target == "opus" and options.audio_bitrate is not None and options.audio_bitrate > 512:
                return "Opus bitrate must not exceed 512 kbps"
            return ""
        if codec == "prores" and target != "mov": return "ProRes output requires MOV"
        if codec == "prores" and options.prores_profile not in self.PRORES_PROFILES: return "Unsupported ProRes profile"
        if codec == "h264" and target not in {"mp4", "mov", "mkv"}: return f"H.264 is not compatible with {target.upper()}"
        if options.resolution_height is not None and (
            options.resolution_height < 2 or options.resolution_height > 8192 or options.resolution_height % 2
        ):
            return "Resolution height must be an even number from 2 to 8192"
        if options.fps != "source":
            try:
                numerator, separator, denominator = options.fps.partition("/")
                fps = float(numerator) / float(denominator) if separator else float(numerator)
            except (TypeError, ValueError, ZeroDivisionError):
                return "FPS must be between 1 and 240"
            if not 1 <= fps <= 240: return "FPS must be between 1 and 240"
        if options.quality_mode not in {"cbr", "crf", "vbr", "vbr_2pass"}: return "Unsupported quality mode"
        if options.quality_mode == "crf" and (options.quality_value is None or not 0 <= options.quality_value <= 51):
            return "CRF must be between 0 and 51"
        if options.quality_mode in {"cbr", "vbr", "vbr_2pass"} and (
            options.quality_value is None or options.quality_value <= 0
        ):
            return "Video bitrate must be greater than zero"
        if options.quality_mode == "vbr_2pass" and (
            options.maximum_bitrate is None or options.maximum_bitrate <= 0
        ):
            return "Maximum bitrate must be greater than zero"
        if options.quality_mode == "vbr_2pass" and options.maximum_bitrate < options.quality_value:
            return "Maximum bitrate must not be lower than the average bitrate"
        if options.gop not in {None, 1, 30, 60, 120}: return "Unsupported GOP value"
        if options.h264_profile not in {"auto", "baseline", "main", "high"}: return "Unsupported H.264 profile"
        if options.pixel_format not in {"auto", "yuv420p"}: return "Unsupported pixel format"
        if options.audio_codec not in {"auto", "copy", "aac", "pcm_s16le", "pcm_s24le", "none"}:
            return "Unsupported audio codec"
        if options.audio_codec.startswith("pcm_") and target != "mov": return "PCM audio requires MOV output"
        if options.audio_bitrate is not None and options.audio_bitrate <= 0: return "Audio bitrate must be greater than zero"
        return ""

    def resolve_video_encoder(self, options: ConversionOptions) -> tuple[str, str]:
        """依 codec 與硬體偏好選擇實際 encoder"""
        target = options.target_format.lower().lstrip(".")
        if options.video_codec == "prores": return "prores_ks", "ProRes uses CPU encoder"
        if options.video_codec == "auto" and target == "webm": return "libvpx-vp9", ""
        if options.video_codec == "auto" and target not in {"mp4", "mov", "mkv"}:
            return self.SOFTWARE_CODECS[target][0], ""
        legacy_encoder = self._normalize_encoder(options.encoder)
        if legacy_encoder: return legacy_encoder, ""
        preference = options.acceleration.lower()
        if preference == "cpu": return "libx264", ""
        available = self.list_hardware_backends()
        candidates = list(self.HARDWARE_PRIORITY) if preference == "auto" else [preference]
        for backend in candidates:
            if backend == "cpu": break
            if backend not in available: continue
            if options.quality_mode == "vbr_2pass": continue
            if options.gop not in {None, 1} and backend in {"amd", "intel"}: continue
            return self.HARDWARE_ENCODERS[backend], ""
        reason = "CPU fallback: preferred hardware is unavailable or does not support the selected settings"
        return "libx264", reason if preference != "cpu" else ""

    def _video_arguments(self, options: ConversionOptions, encoder: str) -> list[str]:
        """建立 codec、品質、profile 與 GOP 參數"""
        if encoder == "prores_ks":
            return ["-profile:v", self.PRORES_PROFILES.get(options.prores_profile, "0"), "-pix_fmt", "yuv422p10le"]
        if encoder == "libvpx-vp9": return ["-crf", "30", "-b:v", "0"]
        arguments: list[str] = []
        if options.h264_profile != "auto": arguments += ["-profile:v", options.h264_profile]
        if options.pixel_format != "auto": arguments += ["-pix_fmt", options.pixel_format]
        if options.quality_mode == "crf":
            value = f"{options.quality_value:g}"
            if encoder == "libx264": arguments += ["-preset", "medium", "-crf", value]
            elif encoder == "h264_nvenc": arguments += ["-rc", "vbr", "-cq", value, "-b:v", "0"]
            elif encoder == "h264_amf": arguments += ["-rc", "cqp", "-qp_i", value, "-qp_p", value, "-qp_b", value]
            elif encoder == "h264_qsv": arguments += ["-global_quality", value]
        elif options.quality_mode == "cbr":
            value = f"{options.quality_value:g}M"
            arguments += ["-b:v", value, "-minrate", value, "-maxrate", value, "-bufsize", f"{options.quality_value * 2:g}M"]
            if encoder == "h264_nvenc": arguments += ["-rc", "cbr"]
            elif encoder == "h264_amf": arguments += ["-rc", "cbr"]
        elif options.quality_mode in {"vbr", "vbr_2pass"}:
            arguments += ["-b:v", f"{options.quality_value:g}M"]
            if options.quality_mode == "vbr_2pass": arguments += [
                "-maxrate", f"{options.maximum_bitrate:g}M", "-bufsize", f"{options.maximum_bitrate * 2:g}M",
            ]
            if encoder == "h264_nvenc": arguments += ["-rc", "vbr"]
            elif encoder == "h264_amf": arguments += ["-rc", "vbr_peak"]
        elif encoder == "libx264": arguments += ["-preset", "medium", "-crf", "20"]
        if options.gop is not None:
            arguments += ["-g", str(options.gop)]
            if encoder == "libx264": arguments += ["-keyint_min", str(options.gop), "-sc_threshold", "0"]
            elif encoder == "h264_nvenc" and options.gop > 1: arguments += ["-no-scenecut", "1"]
        return arguments

    @staticmethod
    def _video_filters(options: ConversionOptions) -> list[str]:
        filters = []
        if options.resolution_height is not None:
            height = str(options.resolution_height) if options.allow_upscale else f"min(ih\\,{options.resolution_height})"
            filters.append(f"scale=-2:{height}")
        if options.fps != "source": filters.append(f"fps={options.fps}")
        return ["-vf", ",".join(filters)] if filters else []

    def _audio_arguments(self, options: ConversionOptions, target: str, video_encoder: str) -> list[str]:
        codec = options.audio_codec
        if codec == "none": return ["-an"]
        if codec == "copy": return ["-c:a", "copy"]
        if codec == "auto": codec = "pcm_s24le" if video_encoder == "prores_ks" else self.SOFTWARE_CODECS[target][1]
        arguments = ["-c:a", codec]
        if codec == "aac" and options.audio_bitrate is not None: arguments += ["-b:a", f"{options.audio_bitrate}k"]
        if options.audio_sample_rate is not None: arguments += ["-ar", str(options.audio_sample_rate)]
        return arguments

    @staticmethod
    def _first_stream(probe: dict[str, Any], media_type: str) -> dict[str, Any]:
        """取得第一條指定類型的有效 stream"""
        for stream in probe.get("streams") or []:
            if stream.get("codec_type") != media_type: continue
            if media_type == "video" and (stream.get("disposition") or {}).get("attached_pic") in {1, True, "1"}:
                continue
            return stream
        return {}

    @staticmethod
    def _is_still_image(path: str, probe: dict[str, Any]) -> bool:
        """辨識沒有時間軸的一般圖片, GIF 不視為靜態圖片"""
        if Path(path).suffix.lower() == ".gif": return False
        extension = Path(path).suffix.lower()
        if extension in {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".avif"}: return True
        stream = FFmpegService._first_stream(probe, "video")
        return not probe.get("duration") and int(stream.get("nb_frames") or 1) <= 1

    @classmethod
    def _stream_duration(cls, probe: dict[str, Any], media_type: str) -> float | None:
        """優先使用指定 stream 長度, 缺少時才使用容器長度"""
        duration = _number(cls._first_stream(probe, media_type).get("duration"))
        return duration if duration is not None else _number(probe.get("duration"))

    def _replacement_duration(
        self, options: ReplacementOptions, visual_probe: dict[str, Any], audio_probe: dict[str, Any],
    ) -> tuple[float, float]:
        """計算合成時間軸與切頭尾後的輸出長度"""
        still = self._is_still_image(options.visual_path, visual_probe)
        durations = []
        if not still:
            visual_duration = self._stream_duration(visual_probe, "video")
            if visual_duration is not None:
                durations.append(max(0.0, visual_duration + options.visual_delay))
        audio_duration = self._stream_duration(audio_probe, "audio")
        if audio_duration is not None: durations.append(max(0.0, audio_duration + options.audio_delay))
        if options.duration_mode == "custom": base_duration = options.custom_duration or 0.0
        elif not durations: base_duration = 0.0
        elif options.duration_mode == "shortest": base_duration = min(durations)
        else: base_duration = max(durations)
        return base_duration, base_duration - options.trim_start - options.trim_end

    def _replacement_copy_compatible(self, probe: dict[str, Any], target: str, media_type: str) -> bool:
        stream = self._first_stream(probe, media_type)
        rules = self.STREAM_COPY_CODECS.get(target, {}).get(media_type)
        return bool(stream and (rules is None or str(stream.get("codec_name") or "") in rules))

    def replacement_actions(
        self, options: ReplacementOptions, visual_probe: dict[str, Any], audio_probe: dict[str, Any],
    ) -> tuple[bool, bool, str]:
        """回傳畫面、音訊是否可 copy 與可顯示的處理摘要"""
        conversion, target = options.conversion, options.conversion.target_format.lower().lstrip(".")
        base_duration, output_duration = self._replacement_duration(options, visual_probe, audio_probe)
        visual_duration = self._stream_duration(visual_probe, "video")
        audio_duration = self._stream_duration(audio_probe, "audio")
        still = self._is_still_image(options.visual_path, visual_probe)
        video_unchanged = (
            not still and not options.force_reencode and conversion.video_codec == "auto"
            and conversion.resolution_height is None and conversion.fps == "source"
            and conversion.quality_mode == "vbr" and conversion.quality_value == 7.5
            and conversion.maximum_bitrate is None and conversion.gop is None
            and conversion.h264_profile == "auto"
            and options.aspect_ratio == "source" and not options.visual_loop
            and options.visual_delay == 0 and options.trim_start == 0 and options.trim_end == 0
            and visual_duration is not None and abs(output_duration - visual_duration) < 0.02
        )
        audio_unchanged = (
            not options.force_reencode and conversion.audio_codec in {"auto", "copy"}
            and conversion.audio_sample_rate is None and conversion.audio_bitrate is None and not options.audio_loop
            and options.audio_delay == 0 and options.trim_start == 0 and options.trim_end == 0
            and audio_duration is not None and abs(output_duration - audio_duration) < 0.02
        )
        copy_video = video_unchanged and self._replacement_copy_compatible(visual_probe, target, "video")
        copy_audio = audio_unchanged and self._replacement_copy_compatible(audio_probe, target, "audio")
        encoder = "copy" if copy_video else self.resolve_video_encoder(conversion)[0]
        audio_encoder = conversion.audio_codec
        if audio_encoder == "auto":
            audio_encoder = "pcm_s24le" if encoder == "prores_ks" else self.SOFTWARE_CODECS[target][1]
        summary = (
            f"Video: {'Stream Copy' if copy_video else encoder}; "
            f"Audio: {'Stream Copy' if copy_audio else audio_encoder}; Duration: {output_duration:.3f}s"
        )
        return copy_video, copy_audio, summary

    def validate_replacement(
        self, options: ReplacementOptions, visual_probe: dict[str, Any], audio_probe: dict[str, Any],
    ) -> str:
        """驗證替換素材、時間軸與輸出設定"""
        conversion = options.conversion
        if not options.visual_path or not options.audio_path: return "Choose both a visual source and an audio source"
        if not self.has_media_stream(visual_probe, "video"): return "The visual source does not contain a video stream"
        if not self.has_media_stream(audio_probe, "audio"): return "The audio source does not contain an audio stream"
        if options.duration_mode not in {"longest", "shortest", "custom"}: return "Unsupported duration mode"
        if options.duration_mode == "custom" and (options.custom_duration is None or options.custom_duration <= 0):
            return "Custom duration must be greater than zero"
        if options.aspect_ratio not in {"source", "16:9", "9:16", "1:1"}: return "Unsupported aspect ratio"
        if options.fit_mode not in {"contain", "cover"}: return "Unsupported image fit mode"
        error = self.validate_options(conversion)
        if error: return error
        _base_duration, output_duration = self._replacement_duration(options, visual_probe, audio_probe)
        if output_duration <= 0: return "The head and tail cuts remove the entire output"
        copy_video, copy_audio, _summary = self.replacement_actions(options, visual_probe, audio_probe)
        if conversion.audio_codec == "copy" and not copy_audio:
            return "Audio Stream Copy cannot be used when the audio timeline or format must be changed"
        return ""

    def _replacement_video_filter(
        self, options: ReplacementOptions, probe: dict[str, Any], base_duration: float, output_duration: float,
    ) -> str:
        conversion, filters = options.conversion, []
        if options.visual_delay < 0:
            filters += [f"trim=start={-options.visual_delay:g}", "setpts=PTS-STARTPTS"]
        elif options.visual_delay > 0:
            filters += [f"tpad=start_duration={options.visual_delay:g}:start_mode=add:color=black"]
        visual_duration = self._stream_duration(probe, "video")
        if not options.visual_loop and not self._is_still_image(options.visual_path, probe) and visual_duration is not None:
            endpoint = max(0.0, visual_duration + options.visual_delay)
            if base_duration > endpoint: filters += [f"tpad=stop_mode=clone:stop_duration={base_duration - endpoint:g}"]
        height = conversion.resolution_height
        if options.aspect_ratio == "source":
            if height is not None:
                height_expr = str(height) if conversion.allow_upscale else f"min(ih\\,{height})"
                filters += [f"scale=-2:{height_expr}"]
        else:
            ratio_width, ratio_height = (int(value) for value in options.aspect_ratio.split(":"))
            height_expr = str(height) if height is not None else "trunc(ih/2)*2"
            if height is not None and not conversion.allow_upscale: height_expr = f"trunc(min(ih\\,{height})/2)*2"
            width_expr = f"trunc(({height_expr})*{ratio_width}/{ratio_height}/2)*2"
            behavior = "decrease" if options.fit_mode == "contain" else "increase"
            filters += [f"scale={width_expr}:{height_expr}:force_original_aspect_ratio={behavior}"]
            if options.fit_mode == "contain": filters += [f"pad={width_expr}:{height_expr}:(ow-iw)/2:(oh-ih)/2:black"]
            else: filters += [f"crop={width_expr}:{height_expr}"]
        fps = conversion.fps
        if fps != "source": filters += [f"fps={fps}"]
        elif self._is_still_image(options.visual_path, probe): filters += ["fps=30"]
        filters += [f"trim=start={options.trim_start:g}:duration={output_duration:g}", "setpts=PTS-STARTPTS"]
        return ",".join(filters)

    def _replacement_audio_filter(
        self, options: ReplacementOptions, probe: dict[str, Any], base_duration: float, output_duration: float,
    ) -> str:
        filters = []
        if options.audio_delay < 0:
            filters += [f"atrim=start={-options.audio_delay:g}", "asetpts=PTS-STARTPTS"]
        elif options.audio_delay > 0:
            filters += [f"adelay={round(options.audio_delay * 1000)}:all=1"]
        audio_duration = self._stream_duration(probe, "audio")
        if not options.audio_loop and audio_duration is not None:
            endpoint = max(0.0, audio_duration + options.audio_delay)
            if base_duration > endpoint: filters += [f"apad=pad_dur={base_duration - endpoint:g}"]
        filters += [f"atrim=start={options.trim_start:g}:duration={output_duration:g}", "asetpts=PTS-STARTPTS"]
        return ",".join(filters)

    def build_replacement_command(
        self, options: ReplacementOptions, visual_probe: dict[str, Any] | None = None,
        audio_probe: dict[str, Any] | None = None, output_path: str | Path | None = None,
        pass_number: int | None = None, passlog_path: str | Path | None = None,
    ) -> list[str]:
        """建立替換音訊或圖片加音訊的 FFmpeg command"""
        if not self.ffmpeg_path: raise FileNotFoundError("ffmpeg was not found in PATH")
        visual_probe = visual_probe or self.probe(options.visual_path)
        audio_probe = audio_probe or self.probe(options.audio_path)
        error = self.validate_replacement(options, visual_probe, audio_probe)
        if error: raise ValueError(error)
        conversion, target = options.conversion, options.conversion.target_format.lower().lstrip(".")
        output = Path(output_path) if output_path else self.collision_safe_output(
            options.visual_path, conversion.output_dir, target,
        )
        base_duration, output_duration = self._replacement_duration(options, visual_probe, audio_probe)
        copy_video, copy_audio, _summary = self.replacement_actions(options, visual_probe, audio_probe)
        command = [self.ffmpeg_path, "-hide_banner", "-nostdin"]
        if self._is_still_image(options.visual_path, visual_probe): command += ["-loop", "1"]
        elif Path(options.visual_path).suffix.lower() == ".gif":
            if options.visual_loop: command += ["-stream_loop", "-1"]
            command += ["-ignore_loop", "1"]
        elif options.visual_loop: command += ["-stream_loop", "-1"]
        command += ["-i", options.visual_path]
        if options.audio_loop: command += ["-stream_loop", "-1"]
        command += ["-i", options.audio_path, "-progress", "pipe:1", "-nostats"]
        filters, maps = [], []
        if pass_number == 1 or not copy_video:
            filters.append(f"[0:v:0]{self._replacement_video_filter(options, visual_probe, base_duration, output_duration)}[v]")
            maps += ["-map", "[v]"]
        else:
            maps += ["-map", "0:v:0"]
        if pass_number != 1:
            if copy_audio:
                maps += ["-map", "1:a:0"]
            else:
                filters.append(f"[1:a:0]{self._replacement_audio_filter(options, audio_probe, base_duration, output_duration)}[a]")
                maps += ["-map", "[a]"]
        if filters: command += ["-filter_complex", ";".join(filters)]
        command += maps
        if copy_video and pass_number != 1:
            command += ["-c:v", "copy"]
        else:
            encoder, _reason = self.resolve_video_encoder(conversion)
            command += ["-c:v", encoder] + self._video_arguments(conversion, encoder)
            if encoder in {"libx264", *self.HARDWARE_ENCODERS.values()} and conversion.pixel_format == "auto":
                command += ["-pix_fmt", "yuv420p"]
        if pass_number == 1:
            command += ["-an"]
        elif copy_audio:
            command += ["-c:a", "copy"]
        else:
            command += self._audio_arguments(conversion, target, self.resolve_video_encoder(conversion)[0])
        command += ["-t", f"{output_duration:g}"]
        if pass_number is not None: command += ["-pass", str(pass_number), "-passlogfile", str(passlog_path)]
        if pass_number == 1: return command + ["-f", "null", "-y", os.devnull]
        return command + ["-n", str(output)]

    def execute_replacement(
        self, task: TaskRecord, progress_cb: ProgressCallback, log_cb: LogCallback,
        cancel_event: threading.Event,
    ) -> str:
        """執行替換任務並沿用 FFmpeg progress 與取消流程"""
        options = task.replacement_options
        if options is None: raise ValueError("Replacement task is missing replacement_options")
        if cancel_event.is_set(): raise ServiceCancelled("Replacement cancelled")
        visual_probe, audio_probe = self.probe(options.visual_path), self.probe(options.audio_path)
        error = self.validate_replacement(options, visual_probe, audio_probe)
        if error: raise FFmpegError(error)
        _base_duration, output_duration = self._replacement_duration(options, visual_probe, audio_probe)
        output_path = self.collision_safe_output(
            options.visual_path, options.conversion.output_dir, options.conversion.target_format,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        copy_video, copy_audio, summary = self.replacement_actions(options, visual_probe, audio_probe)
        log_cb(summary)
        if not copy_video:
            encoder, reason = self.resolve_video_encoder(options.conversion)
            log_cb(f"Video encoder: {encoder}")
            if reason: log_cb(reason)
        two_pass = options.conversion.quality_mode == "vbr_2pass" and not copy_video
        if two_pass:
            passlog_path = output_path.parent / f".{output_path.stem}-{task.id}-passlog"
            try:
                for pass_number in (1, 2):
                    self._execute_command(
                        self.build_replacement_command(
                            options, visual_probe, audio_probe, output_path, pass_number, passlog_path,
                        ), output_duration,
                        lambda value, detail, number=pass_number: _notify_progress(
                            progress_cb, ((number - 1) + value) * 0.5 if value >= 0 else value,
                            f"Pass {number}/2: {detail}",
                        ), log_cb, cancel_event,
                    )
            finally:
                self._remove_passlog_files(passlog_path)
        else:
            self._execute_command(
                self.build_replacement_command(options, visual_probe, audio_probe, output_path),
                output_duration, progress_cb, log_cb, cancel_event,
            )
        if cancel_event.is_set(): raise ServiceCancelled("Replacement cancelled")
        _notify_progress(progress_cb, 1.0, "Replacement completed")
        return str(output_path)

    build_conversion_command = build_command

    def execute_conversion(
        self,
        task: TaskRecord,
        progress_cb: ProgressCallback,
        log_cb: LogCallback,
        cancel_event: threading.Event,
    ) -> str:
        """執行 FFmpeg 並解析 -progress 輸出"""
        options = task.conversion_options
        if options is None: raise ValueError("Conversion task is missing conversion_options")
        if cancel_event.is_set(): raise ServiceCancelled("Conversion cancelled")
        output_path = self.collision_safe_output(options.input_path, options.output_dir, options.target_format)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        probe = self.probe(options.input_path)
        required_stream = self.required_stream_type(options.target_format)
        if required_stream and not self.has_media_stream(probe, required_stream):
            raise FFmpegError(self.MISSING_STREAM_ERRORS[required_stream])
        if options.stream_copy:
            compatible, reason = self.validate_stream_copy(probe, options.target_format)
            if not compatible: raise FFmpegError(reason)
        if options.audio_codec == "copy" and not options.stream_copy:
            compatible, reason = self._validate_audio_copy(probe, options.target_format)
            if not compatible: raise FFmpegError(reason)
        target = options.target_format.lower().lstrip(".")
        if target in self.VIDEO_FORMATS and not options.stream_copy:
            encoder, fallback_reason = self.resolve_video_encoder(options)
            log_cb(f"Video encoder: {encoder}")
            if fallback_reason: log_cb(fallback_reason)
        if not probe.get("duration"): _notify_progress(progress_cb, -1.0, "Duration unavailable")
        if options.quality_mode == "vbr_2pass" and target in self.VIDEO_FORMATS and not options.stream_copy:
            passlog_path = output_path.parent / f".{output_path.stem}-{task.id}-passlog"
            try:
                first_command = self.build_command(options, output_path, 1, passlog_path)
                self._execute_command(
                    first_command, probe.get("duration"),
                    lambda value, detail: _notify_progress(
                        progress_cb, value * 0.5 if value >= 0 else value, f"Pass 1/2: {detail}"
                    ),
                    log_cb, cancel_event,
                )
                second_command = self.build_command(options, output_path, 2, passlog_path)
                self._execute_command(
                    second_command, probe.get("duration"),
                    lambda value, detail: _notify_progress(
                        progress_cb, 0.5 + value * 0.5 if value >= 0 else value, f"Pass 2/2: {detail}"
                    ),
                    log_cb, cancel_event,
                )
            finally:
                self._remove_passlog_files(passlog_path)
        else:
            self._execute_command(
                self.build_command(options, output_path), probe.get("duration"),
                progress_cb, log_cb, cancel_event,
            )
        if cancel_event.is_set(): raise ServiceCancelled("Conversion cancelled")
        _notify_progress(progress_cb, 1.0, "Conversion completed")
        return str(output_path)

    @staticmethod
    def _remove_passlog_files(passlog_path: Path) -> None:
        """清除 FFmpeg 二階段編碼產生的 passlog 檔案"""
        for pass_file in passlog_path.parent.iterdir():
            if pass_file.is_file() and pass_file.name.startswith(passlog_path.name):
                pass_file.unlink(missing_ok=True)

    def _execute_command(
        self,
        command: list[str],
        duration: float | None,
        progress_cb: ProgressCallback,
        log_cb: LogCallback,
        cancel_event: threading.Event,
    ) -> None:
        """執行單次 FFmpeg command 並回報進度"""
        log_cb(" ".join(f'"{part}"' if " " in part else part for part in command))
        process = self.popen_factory(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **self._window_flags(),
        )
        messages: queue.Queue[tuple[str, str | None]] = queue.Queue()
        readers = [
            threading.Thread(target=self._read_pipe, args=(process.stdout, "stdout", messages), daemon=True),
            threading.Thread(target=self._read_pipe, args=(process.stderr, "stderr", messages), daemon=True),
        ]
        for reader in readers: reader.start()

        completed_pipes: set[str] = set()
        errors: list[str] = []
        try:
            while len(completed_pipes) < 2:
                if cancel_event.is_set():
                    self._stop_process(process)
                    raise ServiceCancelled("Conversion cancelled")
                try:
                    source, line = messages.get(timeout=0.1)
                except queue.Empty:
                    if process.poll() is not None and all(not reader.is_alive() for reader in readers): break
                    continue
                if line is None:
                    completed_pipes.add(source)
                    continue
                text = line.rstrip()
                if source == "stderr":
                    if text:
                        log_cb(text)
                        errors.append(text)
                        if len(errors) > 80: errors.pop(0)
                    continue
                progress = self._parse_progress(text, duration)
                if progress is not None: _notify_progress(progress_cb, progress, text)
            return_code = process.wait()
        except ServiceCancelled:
            raise
        except Exception:
            if process.poll() is None: self._stop_process(process)
            raise
        if return_code != 0:
            detail = "\n".join(errors[-12:]) or f"FFmpeg exited with code {return_code}"
            raise FFmpegError(detail)

    def _validate_audio_copy(self, probe: dict[str, Any], target: str) -> tuple[bool, str]:
        """只檢查 audio stream 是否能直接封裝到輸出 container"""
        streams = [item for item in probe.get("streams") or [] if item.get("codec_type") == "audio"]
        if not streams: return True, ""
        rules = self.STREAM_COPY_CODECS.get(target, {})
        allowed = rules.get("audio")
        codec = str(streams[0].get("codec_name") or "")
        if allowed is not None and codec not in allowed:
            return False, f"{codec or 'Unknown'} audio is not compatible with {target.upper()}"
        return True, ""

    validate_audio_copy = _validate_audio_copy

    @staticmethod
    def _read_pipe(pipe: Any, source: str, messages: queue.Queue[tuple[str, str | None]]) -> None:
        if pipe is None:
            messages.put((source, None))
            return
        try:
            for line in iter(pipe.readline, ""):
                messages.put((source, line))
        finally:
            messages.put((source, None))

    @staticmethod
    def _parse_progress(line: str, duration: float | None) -> float | None:
        if line == "progress=end": return 1.0
        if not duration or "=" not in line: return None
        key, value = line.split("=", 1)
        seconds: float | None = None
        try:
            if key in {"out_time_us", "out_time_ms"}: seconds = float(value) / 1_000_000
            elif key == "out_time":
                hours, minutes, raw_seconds = value.split(":")
                seconds = int(hours) * 3600 + int(minutes) * 60 + float(raw_seconds)
        except (TypeError, ValueError):
            return None
        if seconds is None: return None
        return max(0.0, min(1.0, seconds / float(duration)))

    @staticmethod
    def _probe_duration(probe: dict[str, Any]) -> float | None:
        values = [probe.get("format", {}).get("duration")]
        values += [stream.get("duration") for stream in probe.get("streams") or []]
        durations = []
        for value in values:
            try:
                if value is not None: durations.append(float(value))
            except (TypeError, ValueError):
                continue
        return max(durations) if durations else None

    @staticmethod
    def _normalize_encoder(encoder: str | None) -> str:
        value = (encoder or "").strip().lower()
        aliases = {"nvidia": "h264_nvenc", "amd": "h264_amf", "intel": "h264_qsv"}
        return "" if value in {"", "software", "cpu"} else aliases.get(value, value)

    @staticmethod
    def _validate_encoder_container(encoder: str, target: str) -> None:
        if target == "webm" and not encoder.startswith(("vp8", "vp9", "av1")):
            raise ValueError(f"{encoder} is not compatible with WebM")

    @staticmethod
    def _stop_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None: return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    @staticmethod
    def _window_flags() -> dict[str, Any]:
        if os.name != "nt": return {}
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        return {"startupinfo": startupinfo, "creationflags": subprocess.CREATE_NO_WINDOW}
