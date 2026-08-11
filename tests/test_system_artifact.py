from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "macTest"))

from build_artifact_bundle import build_bundle


def test_build_bundle_flattens_ui_and_merges_text_diagnostics(tmp_path: Path) -> None:
    source = tmp_path / "results"
    (source / "ui" / "cute_light" / "zh_TW").mkdir(parents=True)
    (source / "ui" / "cute_light" / "zh_TW" / "settings.png").write_bytes(b"png fixture")
    (source / "ui" / "report.json").write_text(
        '{"success": true, "capture_count": 26, "captures": [{"path": "settings.png"}]}', encoding="utf-8",
    )
    (source / "probes").mkdir()
    (source / "probes" / "runner.log").write_text("probe completed", encoding="utf-8")
    (source / "probes" / "MochiStar-test.ips").write_text("native crash report", encoding="utf-8")
    output = source / "final-script-arm64"

    build_bundle(source, output, "macOS ARM64")

    assert (output / "ui" / "cute_light-zh_TW-settings.png").read_bytes() == b"png fixture"
    assert "`ui/report.json`: passed" in (output / "README.md").read_text(encoding="utf-8")
    report_log = (output / "report.log").read_text(encoding="utf-8")
    assert "probe completed" in report_log
    assert "native crash report" in report_log
    assert '"captures"' not in report_log
