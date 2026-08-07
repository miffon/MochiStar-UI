from __future__ import annotations

import hashlib
import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from i18n import set_language, tr
from qt_network import QtDownloadResult, QtNetworkCancelled, QtNetworkClient, QtNetworkError
from update_controller import UpdateController
from update_service import (
    DownloadedUpdate,
    GitHubReleaseProvider,
    ManualUpdateInstaller,
    QtGitHubReleaseProvider,
    QtUpdateDownloader,
    ReleaseAsset,
    SemanticVersion,
    UpdateCheckStatus,
    UpdateCancelled,
    UpdateDownloader,
    UpdateError,
    UpdateNotConfigured,
    UpdateRelease,
    current_platform_key,
    manual_update_instructions,
)

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def release_payload(tag: str, asset: dict | None = None, **values) -> dict:
    """建立 GitHub release API 測試資料"""
    return {
        "tag_name": tag,
        "name": f"Release {tag}",
        "body": f"Notes for {tag}",
        "html_url": f"https://github.test/releases/{tag}",
        "published_at": "2026-07-29T00:00:00Z",
        "draft": False,
        "assets": [asset] if asset else [],
        **values,
    }


def asset_payload(
    content: bytes,
    digest: str | None = None,
    name: str = "MochiStar-Windows-portable.zip",
) -> dict:
    """建立 release asset 測試資料"""
    return {
        "name": name,
        "browser_download_url": f"https://github.test/{name}",
        "size": len(content),
        "digest": digest or f"sha256:{hashlib.sha256(content).hexdigest()}",
        "state": "uploaded",
    }


def make_release(content: bytes = b"update", version: str = "1.1.0") -> UpdateRelease:
    asset = ReleaseAsset(
        name="MochiStar-Windows-portable.zip",
        url="https://github.test/MochiStar-Windows-portable.zip",
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    return UpdateRelease(
        tag=f"v{version}",
        version=SemanticVersion.parse(version),
        title=f"Version {version}",
        notes="Changes",
        page_url=f"https://github.test/releases/v{version}",
        published_at="2026-07-29T00:00:00Z",
        asset=asset,
    )


def wait_until(app: QApplication, predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate(): return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for update controller")


def test_semantic_version_accepts_only_stable_tags_and_compares_numerically() -> None:
    assert SemanticVersion.parse("v1.10.0") > SemanticVersion.parse("1.9.0")
    assert str(SemanticVersion.parse("v2.3.4")) == "2.3.4"
    for value in ("1.0", "v1.0.0-beta.1", "release-1.0.0", "v1.0.0.1"):
        with pytest.raises(ValueError): SemanticVersion.parse(value)


def test_platform_key_maps_supported_desktop_systems() -> None:
    assert current_platform_key("Windows") == "windows"
    assert current_platform_key("Darwin") == "macos"
    assert current_platform_key("Linux") == "linux"
    with pytest.raises(UpdateError): current_platform_key("Haiku")


def test_manual_update_instructions_are_platform_specific() -> None:
    assert "ZIP" in manual_update_instructions("windows")
    assert "DMG" in manual_update_instructions("macos")
    assert "Applications" in manual_update_instructions("macos")
    assert "executable permission" in manual_update_instructions("linux")
    set_language("zh_TW")
    assert "DMG" in tr(manual_update_instructions("macos"))
    assert "應用程式" in tr(manual_update_instructions("macos"))
    assert tr("No stable release is available yet") == "目前尚無正式版本"
    assert tr("This test build is newer than the latest stable release") == "目前測試版比最新正式版更新"
    set_language("en")
    with pytest.raises(UpdateError): manual_update_instructions("haiku")


def test_manual_update_installer_only_opens_download_directory(tmp_path: Path) -> None:
    opened = []
    update = DownloadedUpdate(make_release(), tmp_path / "downloads" / "MochiStar-Windows-portable.zip")
    installer = ManualUpdateInstaller(lambda path: opened.append(path) or True)

    assert installer.install(update)
    assert opened == [update.path.parent]
    assert not update.path.exists()


@pytest.mark.parametrize(
    "platform_key,asset_name",
    [
        ("windows", "MochiStar-Windows-portable.zip"),
        ("macos", "MochiStar-macOS-installer.dmg"),
        ("linux", "MochiStar-Linux.tar.gz"),
    ],
)
def test_github_provider_uses_platform_release_asset_names(platform_key: str, asset_name: str) -> None:
    content = b"verified update"
    provider = GitHubReleaseProvider(
        "owner/repo",
        urlopen=lambda *_args, **_kwargs: FakeResponse(json.dumps([
            release_payload("v1.1.0", asset_payload(content, name=asset_name)),
        ]).encode()),
    )

    release = provider.check_latest(platform_key)

    assert release is not None
    assert release.asset is not None
    assert release.asset.name == asset_name


def test_github_provider_selects_highest_stable_non_draft_release() -> None:
    content = b"verified update"
    payload = [
        release_payload("v2.0.0", asset_payload(content), draft=True),
        release_payload("v1.9.0-beta.1", asset_payload(content)),
        release_payload("v1.9.0", asset_payload(content)),
        release_payload("v1.10.0", asset_payload(content)),
    ]
    provider = GitHubReleaseProvider(
        "owner/repo",
        urlopen=lambda *_args, **_kwargs: FakeResponse(json.dumps(payload).encode()),
    )
    release = provider.check_latest("windows")

    assert release is not None
    assert str(release.version) == "1.10.0"
    assert release.asset is not None
    assert release.asset.sha256 == hashlib.sha256(content).hexdigest()


def test_github_provider_keeps_release_link_when_verified_asset_is_missing() -> None:
    content = b"update"
    invalid_asset = asset_payload(content, digest="sha256:not-a-hash")
    provider = GitHubReleaseProvider(
        "owner/repo",
        urlopen=lambda *_args, **_kwargs: FakeResponse(json.dumps([
            release_payload("v1.1.0", invalid_asset),
        ]).encode()),
    )
    release = provider.check_latest("windows")

    assert release is not None
    assert release.asset is None
    assert release.page_url.endswith("/v1.1.0")


def test_github_provider_reports_configuration_payload_and_network_errors() -> None:
    with pytest.raises(UpdateNotConfigured): GitHubReleaseProvider("").check_latest("windows")
    malformed = GitHubReleaseProvider("owner/repo", urlopen=lambda *_args, **_kwargs: FakeResponse(b"{"))
    with pytest.raises(UpdateError): malformed.check_latest("windows")

    def timeout(*_args, **_kwargs):
        raise TimeoutError("timed out")

    with pytest.raises(UpdateError): GitHubReleaseProvider("owner/repo", urlopen=timeout).check_latest("windows")


def test_qt_github_provider_reads_and_parses_release_payload() -> None:
    content = b"verified update"

    class Client:
        def read(self, url, headers, timeout, max_size):
            assert url == "https://api.github.com/repos/owner/repo/releases?per_page=30"
            assert headers["User-Agent"] == "MochiStar-Update-Checker"
            assert timeout == 12
            assert max_size == 2 * 1024 * 1024
            return json.dumps([release_payload("v1.2.0", asset_payload(content))]).encode()

    release = QtGitHubReleaseProvider("owner/repo", Client(), 12).check_latest("windows")

    assert release is not None
    assert str(release.version) == "1.2.0"
    assert release.asset is not None


def test_qt_github_provider_translates_network_errors() -> None:
    class Client:
        def read(self, *_args, **_kwargs):
            raise QtNetworkError("connection failed")

    with pytest.raises(UpdateError, match="connection failed"):
        QtGitHubReleaseProvider("owner/repo", Client()).check_latest("macos")


def test_qt_network_client_runs_inside_controller_style_worker(app) -> None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(QtNetworkClient().read, "data:text/plain,hello", {}, 2, 100).result()

    assert result == b"hello"


def test_update_downloader_verifies_and_atomically_finishes(tmp_path: Path) -> None:
    content = b"downloaded update content"
    release = make_release(content)
    progress = []
    downloader = UpdateDownloader(urlopen=lambda *_args, **_kwargs: FakeResponse(content))
    update = downloader.download(release, tmp_path, lambda *values: progress.append(values), threading.Event())

    assert update.path.read_bytes() == content
    assert update.path.name == release.asset.name
    assert not (tmp_path / f"{release.asset.name}.part").exists()
    assert progress[-1] == (len(content), len(content))


@pytest.mark.parametrize("bad_size,bad_digest", [(True, False), (False, True)])
def test_update_downloader_removes_partial_file_after_verification_failure(
    tmp_path: Path,
    bad_size: bool,
    bad_digest: bool,
) -> None:
    content = b"update"
    release = make_release(content)
    asset = release.asset
    assert asset is not None
    release = UpdateRelease(
        tag=release.tag,
        version=release.version,
        title=release.title,
        notes=release.notes,
        page_url=release.page_url,
        published_at=release.published_at,
        asset=ReleaseAsset(
            name=asset.name,
            url=asset.url,
            size=asset.size + 1 if bad_size else asset.size,
            sha256="0" * 64 if bad_digest else asset.sha256,
        ),
    )
    downloader = UpdateDownloader(urlopen=lambda *_args, **_kwargs: FakeResponse(content))

    with pytest.raises(UpdateError):
        downloader.download(release, tmp_path, lambda *_args: None, threading.Event())
    assert not list(tmp_path.glob("*.part"))
    assert not (tmp_path / asset.name).exists()


def test_update_downloader_honors_cancellation_and_cleans_partial_file(tmp_path: Path) -> None:
    release = make_release(b"update")
    cancel_event = threading.Event()
    cancel_event.set()
    downloader = UpdateDownloader(urlopen=lambda *_args, **_kwargs: FakeResponse(b"update"))

    with pytest.raises(UpdateCancelled):
        downloader.download(release, tmp_path, lambda *_args: None, cancel_event)
    assert not list(tmp_path.glob("*.part"))


def test_qt_update_downloader_verifies_and_atomically_finishes(tmp_path: Path) -> None:
    content = b"downloaded with Qt"
    release = make_release(content)
    progress = []

    class Client:
        def download(self, url, headers, timeout, output_path, progress_cb, cancel_event):
            assert url == release.asset.url
            assert headers["User-Agent"] == "MochiStar-Update-Downloader"
            assert timeout == 25
            assert not cancel_event.is_set()
            output_path.write_bytes(content)
            progress_cb(len(content))
            return QtDownloadResult(len(content), hashlib.sha256(content).hexdigest())

    update = QtUpdateDownloader(Client(), 25).download(
        release, tmp_path, lambda *values: progress.append(values), threading.Event(),
    )

    assert update.path.read_bytes() == content
    assert progress == [(len(content), len(content))]
    assert not list(tmp_path.glob("*.part"))


def test_qt_update_downloader_translates_cancellation_and_cleans_partial_file(tmp_path: Path) -> None:
    release = make_release()

    class Client:
        def download(self, _url, _headers, _timeout, output_path, _progress, _cancel_event):
            output_path.write_bytes(b"partial")
            raise QtNetworkCancelled("cancelled")

    with pytest.raises(UpdateCancelled):
        QtUpdateDownloader(Client()).download(
            release, tmp_path, lambda *_args: None, threading.Event(),
        )
    assert not list(tmp_path.glob("*.part"))


def test_update_controller_checks_in_background_and_filters_current_version(app) -> None:
    class Provider:
        def check_latest(self, _platform_key):
            return make_release()

    controller = UpdateController(Provider(), UpdateDownloader(), "1.0.0", "windows")
    results = []
    controller.check_succeeded.connect(lambda result, manual: results.append((result, manual)))
    controller.check(True)
    wait_until(app, lambda: bool(results))
    assert results[0][0].status is UpdateCheckStatus.AVAILABLE
    assert results[0][0].release.tag == "v1.1.0"
    assert results[0][1] is True
    controller.shutdown()


@pytest.mark.parametrize(
    "current_version,is_test_build,latest_version,status,available_version",
    [
        ("1.0.0", False, None, UpdateCheckStatus.NO_STABLE_RELEASE, None),
        ("1.0.0", False, "1.0.0", UpdateCheckStatus.UP_TO_DATE, None),
        ("1.0.0", True, "1.0.0", UpdateCheckStatus.AVAILABLE, "1.0.0"),
        ("1.0.1", True, "1.0.0", UpdateCheckStatus.TEST_BUILD_AHEAD, None),
        ("1.0.0", True, "1.0.3", UpdateCheckStatus.AVAILABLE, "1.0.3"),
    ],
)
def test_update_controller_classifies_stable_and_test_builds(
    current_version: str,
    is_test_build: bool,
    latest_version: str | None,
    status: UpdateCheckStatus,
    available_version: str | None,
) -> None:
    controller = UpdateController(
        object(), UpdateDownloader(), current_version, "windows", is_test_build=is_test_build,
    )

    result = controller._classify_release(make_release(version=latest_version) if latest_version else None)

    assert result.status is status
    assert (str(result.release.version) if result.release else None) == available_version
    controller.shutdown()
