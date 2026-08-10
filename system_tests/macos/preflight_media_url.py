from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from probe_runner import ProbeFailure, run_probe


ROOT = Path(__file__).resolve().parents[2]


def _append_summary(lines: list[str]) -> None:
    """有 GitHub summary path 時附加 URL 預檢結果"""
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the external media URL before macOS system tests")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        report = run_probe(
            [sys.executable, str(ROOT / "src" / "main.py")], arguments.output,
            "media-url-preflight", "media-analysis", arguments.url,
        )
    except ProbeFailure as error:
        print(f"::error::{error}")
        _append_summary(["## Media URL preflight", "", f"Failed: {error}"])
        return 1

    media = report["media"]
    _append_summary([
        "## Media URL preflight", "", "Passed",
        f"- Extractor: {media['extractor']}", f"- Title: {media['title']}", f"- Formats: {media['format_count']}",
    ])
    return 0


if __name__ == "__main__": raise SystemExit(main())
