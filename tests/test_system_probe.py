from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "system_tests" / "macos"))

import system_probe
from probe_runner import run_probe
from system_probe import classify_media_failure


@pytest.mark.parametrize(("reason", "category"), [
    ("ERROR: Video unavailable. This video has been deleted", "url_content"),
    ("HTTP Error 404: Not Found", "url_content"),
    ("Sign in to confirm your age, cookies are required", "permission"),
    ("HTTP Error 403: Forbidden", "permission"),
    ("HTTP Error 429: Too Many Requests", "permission"),
    ("Connection timed out while resolving DNS", "network"),
    ("Extractor failed while mapping metadata", "unknown"),
])
def test_classify_media_failure(reason: str, category: str) -> None:
    assert classify_media_failure(reason) == category


def test_media_probe_preserves_yt_dlp_failure_reason(monkeypatch, capsys) -> None:
    class FailedService:
        def detect_js_runtimes(self) -> dict[str, str]:
            return {}

        def analyze(self, _url: str):
            logging.getLogger("yt_dlp").error("ERROR: Video unavailable. This video has been removed")
            raise ValueError("yt-dlp returned no media information")

    monkeypatch.setattr(system_probe, "YtDlpService", FailedService)

    assert system_probe.run_system_probe("media-analysis", "https://example.test/missing") == 1
    report = json.loads(capsys.readouterr().out)
    assert report["failure"] == {
        "category": "url_content",
        "reason": "ERROR: Video unavailable. This video has been removed",
    }
    assert report["error"] == "yt-dlp returned no media information"


def test_probe_runner_injects_url_and_writes_canonical_report(tmp_path) -> None:
    code = (
        "import json, os; "
        "print(json.dumps({'success': True, 'url': os.environ['MOCHISTAR_SYSTEM_TEST_URL']}))"
    )

    report = run_probe(
        [sys.executable, "-c", code], tmp_path, "media-external", "media-analysis",
        "https://example.test/injected", attempts=1,
    )

    assert report["url"] == "https://example.test/injected"
    assert json.loads((tmp_path / "media-external.json").read_text(encoding="utf-8"))["url"] == report["url"]
