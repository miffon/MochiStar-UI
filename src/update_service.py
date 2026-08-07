from __future__ import annotations

import hashlib
import json
import platform
import re
import threading
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from release_config import GITHUB_REPOSITORY

_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_PLATFORM_ASSETS = {
    "windows": "MochiStar-Windows-portable.zip",
    "macos": "MochiStar-macOS-installer.dmg",
    "linux": "MochiStar-Linux.tar.gz",
}

_MANUAL_UPDATE_INSTRUCTIONS = {
    "windows": (
        "Close MochiStar, extract the downloaded ZIP file, then replace the old MochiStar folder "
        "with the extracted folder. Your settings and queue are stored separately."
    ),
    "macos": (
        "Close MochiStar, open the downloaded DMG, then drag MochiStar.app to Applications and replace "
        "the old version. If macOS blocks it, use Open Anyway in Privacy & Security."
    ),
    "linux": (
        "Close MochiStar, extract the downloaded archive, then replace the old MochiStar folder. "
        "The archive preserves the executable permission."
    ),
}


class UpdateError(RuntimeError):
    """表示更新檢查或下載無法完成"""


class UpdateNotConfigured(UpdateError):
    """表示尚未設定 release repository"""


class UpdateCancelled(UpdateError):
    """表示使用者取消更新下載"""


@dataclass(frozen=True, order=True, slots=True)
class SemanticVersion:
    """只接受穩定版 MAJOR.MINOR.PATCH"""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> SemanticVersion:
        match = _VERSION_PATTERN.fullmatch(value.strip())
        if not match: raise ValueError(f"Invalid stable version: {value}")
        return cls(*(int(part) for part in match.groups()))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    """目前平台可下載的 GitHub release asset"""

    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class UpdateRelease:
    """正規化後的穩定版 release"""

    tag: str
    version: SemanticVersion
    title: str
    notes: str
    page_url: str
    published_at: str
    asset: ReleaseAsset | None


@dataclass(frozen=True, slots=True)
class DownloadedUpdate:
    """已完成完整性驗證的更新檔案"""

    release: UpdateRelease
    path: Path


class ReleaseProvider(Protocol):
    def check_latest(self, platform_key: str) -> UpdateRelease | None:
        """取得最新穩定版 release"""


class UpdateInstaller(Protocol):
    def install(self, update: DownloadedUpdate) -> bool:
        """開啟已下載更新檔的位置"""


class ManualUpdateInstaller:
    """第一階段只開啟已下載檔案所在資料夾"""

    def __init__(self, open_directory: Callable[[Path], bool]):
        self.open_directory = open_directory

    def install(self, update: DownloadedUpdate) -> bool:
        return self.open_directory(update.path.parent)


def current_platform_key(system_name: str | None = None) -> str:
    """將 platform.system 統一成 release asset key"""
    name = (system_name or platform.system()).strip().lower()
    key = {"windows": "windows", "darwin": "macos", "linux": "linux"}.get(name)
    if not key: raise UpdateError(f"Unsupported update platform: {name or 'unknown'}")
    return key


def manual_update_instructions(platform_key: str) -> str:
    """依平台取得手動替換更新檔的操作說明"""
    try:
        return _MANUAL_UPDATE_INSTRUCTIONS[platform_key]
    except KeyError as error:
        raise UpdateError(f"Unsupported update platform: {platform_key}") from error


class GitHubReleaseProvider:
    """透過 GitHub Releases REST API 取得最新穩定版"""

    def __init__(
        self,
        repository: str = GITHUB_REPOSITORY,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        timeout: float = 15.0,
    ):
        self.repository = repository.strip().strip("/")
        self.urlopen = urlopen
        self.timeout = timeout

    def check_latest(self, platform_key: str) -> UpdateRelease | None:
        if not self.repository: raise UpdateNotConfigured("GitHub repository is not configured")
        asset_name = _PLATFORM_ASSETS.get(platform_key)
        if not asset_name: raise UpdateError(f"Unsupported update platform: {platform_key}")
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.repository}/releases?per_page=30",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "MochiStar-Update-Checker",
            },
        )
        try:
            with self.urlopen(request, timeout=self.timeout) as response:
                raw_payload = response.read(2 * 1024 * 1024 + 1)
                if len(raw_payload) > 2 * 1024 * 1024: raise UpdateError("GitHub releases response is too large")
                payload = json.loads(raw_payload)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise UpdateError(f"Unable to read GitHub releases: {error}") from error
        if not isinstance(payload, list): raise UpdateError("GitHub releases response is not a list")

        releases = [
            release
            for item in payload
            if isinstance(item, Mapping)
            if item.get("draft") is not True
            if (release := self._parse_release(item, asset_name)) is not None
        ]
        return max(releases, key=lambda release: release.version) if releases else None

    @staticmethod
    def _parse_release(values: Mapping[str, Any], asset_name: str) -> UpdateRelease | None:
        tag = values.get("tag_name")
        if not isinstance(tag, str): return None
        try:
            version = SemanticVersion.parse(tag)
        except ValueError:
            return None
        assets = values.get("assets")
        asset_values = next(
            (
                item
                for item in assets
                if isinstance(item, Mapping) and item.get("name") == asset_name and item.get("state") == "uploaded"
            ),
            None,
        ) if isinstance(assets, list) else None
        asset = GitHubReleaseProvider._parse_asset(asset_values) if asset_values else None
        title = values.get("name")
        notes = values.get("body")
        page_url = values.get("html_url")
        published_at = values.get("published_at")
        return UpdateRelease(
            tag=tag,
            version=version,
            title=title if isinstance(title, str) and title.strip() else tag,
            notes=notes if isinstance(notes, str) else "",
            page_url=page_url if isinstance(page_url, str) else "",
            published_at=published_at if isinstance(published_at, str) else "",
            asset=asset,
        )

    @staticmethod
    def _parse_asset(values: Mapping[str, Any]) -> ReleaseAsset | None:
        name, url, digest = values.get("name"), values.get("browser_download_url"), values.get("digest")
        size = values.get("size")
        if not isinstance(name, str) or Path(name).name != name: return None
        if not isinstance(url, str) or not url.startswith("https://"): return None
        if not isinstance(size, int) or isinstance(size, bool) or size < 0: return None
        if not isinstance(digest, str) or not digest.startswith("sha256:"): return None
        sha256 = digest.removeprefix("sha256:").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha256): return None
        return ReleaseAsset(name=name, url=url, size=size, sha256=sha256)


class UpdateDownloader:
    """串流下載 release asset 並驗證大小與 SHA-256"""

    def __init__(self, urlopen: Callable[..., Any] = urllib.request.urlopen, timeout: float = 30.0):
        self.urlopen = urlopen
        self.timeout = timeout

    def download(
        self,
        release: UpdateRelease,
        target_dir: Path,
        progress: Callable[[int, int], None],
        cancel_event: threading.Event,
    ) -> DownloadedUpdate:
        asset = release.asset
        if asset is None: raise UpdateError("Release does not include a verified asset for this platform")
        target_dir.mkdir(parents=True, exist_ok=True)
        final_path = target_dir / asset.name
        partial_path = target_dir / f"{asset.name}.part"
        request = urllib.request.Request(asset.url, headers={"User-Agent": "MochiStar-Update-Downloader"})
        digest = hashlib.sha256()
        downloaded = 0
        try:
            with self.urlopen(request, timeout=self.timeout) as response, partial_path.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    if cancel_event.is_set(): raise UpdateCancelled("Update download was cancelled")
                    output.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    progress(downloaded, asset.size)
            if cancel_event.is_set(): raise UpdateCancelled("Update download was cancelled")
            if downloaded != asset.size:
                raise UpdateError(f"Update size mismatch: expected {asset.size}, received {downloaded}")
            if digest.hexdigest() != asset.sha256:
                raise UpdateError("Update SHA-256 verification failed")
            partial_path.replace(final_path)
            return DownloadedUpdate(release=release, path=final_path)
        except Exception:
            try:
                partial_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
