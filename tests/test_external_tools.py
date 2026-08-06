from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtWidgets import QApplication, QLabel

from dependency_dialog import DependencyDialog
from external_tools import DependencyReport, ExternalToolInspector, ToolStatus, installation_guides
from i18n import set_language

def fake_inspector(outputs: dict[str, str], return_codes: dict[str, int] | None = None) -> ExternalToolInspector:
    paths = {name: f"C:/tools/{name}.exe" for name in outputs}
    return_codes = return_codes or {}

    def which(name: str, path: str | None = None) -> str | None:
        return paths.get(name)

    def run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        name = Path(command[0]).stem
        return subprocess.CompletedProcess(command, return_codes.get(name, 0), outputs.get(name, ""), "")

    return ExternalToolInspector(which=which, run_command=run)


def test_inspector_accepts_supported_tools_and_maps_qjs_to_quickjs() -> None:
    inspector = fake_inspector({
        "ffmpeg": "ffmpeg version 8.0",
        "ffprobe": "ffprobe version 8.0",
        "deno": "deno 2.3.1",
        "node": "v22.2.0",
        "qjs": "QuickJS version 2025-04-26",
        "bun": "1.3.14",
    })

    report = inspector.inspect()

    assert report.ffmpeg_available
    assert report.valid_runtimes == {
        "deno": "C:/tools/deno.exe",
        "node": "C:/tools/node.exe",
        "quickjs": "C:/tools/qjs.exe",
        "bun": "C:/tools/bun.exe",
    }
    assert report.missing_dependency_ids == set()


def test_inspector_rejects_old_and_unreadable_runtimes() -> None:
    inspector = fake_inspector({
        "ffmpeg": "ffmpeg version 7.1",
        "ffprobe": "ffprobe version 7.1",
        "deno": "deno 2.2.9",
        "node": "v21.9.0",
        "qjs": "QuickJS version 2023-12-08",
        "bun": "1.3.15",
    })

    report = inspector.inspect()

    assert not report.js_runtime_available
    assert {status.state for status in report.runtimes} == {"outdated"}
    assert report.missing_dependency_ids == {"js_runtime"}


def test_inspector_accepts_quickjs_ng_without_a_minimum_version() -> None:
    inspector = fake_inspector({"qjs": "QuickJS-ng version 0.10.1"}, {"qjs": 1})

    report = inspector.inspect()

    quickjs = next(status for status in report.runtimes if status.key == "quickjs")
    assert quickjs.available
    assert quickjs.name == "QuickJS-NG"


def test_inspector_marks_failed_executables_invalid() -> None:
    inspector = fake_inspector({"deno": "permission denied"}, {"deno": 1})

    report = inspector.inspect()

    deno = report.runtimes[0]
    assert deno.state == "invalid"
    assert not report.js_runtime_available


def test_installation_guides_only_return_commands_for_available_package_managers(tmp_path: Path) -> None:
    windows = installation_guides("win32", which=lambda name: "winget.exe" if name == "winget" else None)
    macos = installation_guides("darwin", which=lambda _name: None)
    os_release = tmp_path / "os-release"
    os_release.write_text('NAME="Ubuntu"\nID=ubuntu\n', encoding="utf-8")
    linux = installation_guides("linux", which=lambda name: "/usr/bin/apt" if name == "apt" else None, os_release_path=os_release)

    assert windows["ffmpeg"].command == "winget install --exact --id Gyan.FFmpeg --source winget"
    assert windows["ffmpeg"].url == "https://www.gyan.dev/ffmpeg/builds/"
    assert windows["js_runtime"].command.endswith("DenoLand.Deno --source winget")
    assert macos["ffmpeg"].command == ""
    assert linux["ffmpeg"].command == "sudo apt update && sudo apt install ffmpeg"
    assert linux["ffmpeg"].requires_admin
    assert "deno.land/install.sh" in linux["js_runtime"].command


def test_dependency_dialog_copies_commands_and_emits_ignored_items(app) -> None:
    set_language("en")
    missing = ToolStatus("ffmpeg", "FFmpeg", "ffmpeg")
    report = DependencyReport(missing, ToolStatus("ffprobe", "FFprobe", "ffprobe"), (
        ToolStatus("deno", "Deno", "deno", minimum_version="2.3.0"),
    ))
    guides = installation_guides("win32", which=lambda _name: "winget.exe")
    dialog = DependencyDialog(report, {"ffmpeg", "js_runtime"}, guides)
    ignored = []
    dialog.ignored_requested.connect(ignored.append)

    labels = [label.text() for label in dialog.findChildren(QLabel)]
    assert "The following dependencies were not detected and may affect the full experience:" in labels

    dialog._copy_command(guides["js_runtime"].command)
    assert QApplication.clipboard().text() == guides["js_runtime"].command
    dialog.ignore_checkbox.setChecked(True)
    dialog.accept()

    assert ignored == [["ffmpeg", "js_runtime"]]
