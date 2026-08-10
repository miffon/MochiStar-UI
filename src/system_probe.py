from __future__ import annotations

import json
import logging
import os
import platform
import ssl
import sys
import traceback
from typing import Any

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import qVersion
from yt_dlp.version import __version__ as yt_dlp_version

from media_service import YtDlpService
from update_service import QtGitHubReleaseProvider, current_platform_key


DEFAULT_MEDIA_TEST_URL = "https://www.nicovideo.jp/watch/sm29822304"

_PERMISSION_ERRORS = (
    "401", "403", "429", "age-restricted", "cookies", "forbidden", "geo-restricted", "login required",
    "members-only", "not available in your country", "private video", "sign in", "too many requests",
)
_NETWORK_ERRORS = (
    "connection", "dns", "network", "proxy", "ssl", "temporary failure", "timed out", "timeout", "tls",
)
_URL_CONTENT_ERRORS = (
    "404", "content isn't available", "deleted", "no accessible playlist", "no longer available", "not found",
    "removed", "unsupported url", "url is required", "video unavailable",
)


class _YtDlpErrorCapture(logging.Handler):
    """保存 system probe 收到的 yt-dlp error"""

    def __init__(self):
        super().__init__(logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def classify_media_failure(reason: str) -> str:
    """將 media analysis 失敗原因分成 Actions 可讀分類"""
    text = reason.lower()
    if any(pattern in text for pattern in _PERMISSION_ERRORS): return "permission"
    if any(pattern in text for pattern in _NETWORK_ERRORS): return "network"
    if any(pattern in text for pattern in _URL_CONTENT_ERRORS): return "url_content"
    return "unknown"


def _environment_report() -> dict[str, Any]:
    """建立不含 secret 的 packaged application 執行環境摘要"""
    return {
        "architecture": platform.machine(),
        "macos": platform.mac_ver()[0],
        "python": platform.python_version(),
        "pyside": pyside_version,
        "qt": qVersion(),
        "yt_dlp": yt_dlp_version,
        "openssl": ssl.OPENSSL_VERSION,
        "path": os.environ.get("PATH", ""),
    }


def run_system_probe(mode: str, url: str = "") -> int:
    """執行 packaged application system probe 並輸出 JSON"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    report: dict[str, Any] = {"mode": mode, "environment": _environment_report(), "success": False}
    try:
        if mode == "update":
            release = QtGitHubReleaseProvider().check_latest(current_platform_key())
            report["release"] = str(release.version) if release else None
        elif mode == "media-analysis":
            service = YtDlpService()
            report["runtimes"] = service.detect_js_runtimes()
            capture = _YtDlpErrorCapture()
            logging.getLogger("yt_dlp").addHandler(capture)
            try:
                media = service.analyze(url or DEFAULT_MEDIA_TEST_URL)
            except Exception as error:
                reason = capture.messages[-1] if capture.messages else str(error)
                report["failure"] = {"category": classify_media_failure(reason), "reason": reason}
                raise
            finally:
                logging.getLogger("yt_dlp").removeHandler(capture)
            report["media"] = {
                "id": media.media_id, "title": media.title, "extractor": media.site,
                "format_count": len(media.formats), "subtitle_count": len(media.subtitles),
            }
            if not media.media_id or not media.title or not media.site or not media.formats:
                raise RuntimeError("Media analysis returned incomplete metadata")
        else:
            raise ValueError(f"Unknown system test: {mode}")
        report["success"] = True
    except Exception as error:
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
        logging.getLogger(__name__).exception("System probe failed")
    print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stdout, flush=True)
    return 0 if report["success"] else 1
