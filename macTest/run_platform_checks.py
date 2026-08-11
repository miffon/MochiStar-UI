from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from external_tools import ExternalToolInspector


def _write_executable(path: Path, output: str, exit_code: int = 0) -> None:
    """建立含固定輸出的測試 executable"""
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\nexit {exit_code}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def main() -> int:
    """驗證 macOS subprocess 和基本 filesystem 權限行為"""
    result_dir = Path(os.environ["MOCHISTAR_RESULT_DIR"])
    workspace = result_dir / "權限與 shell 空白路徑"
    tools = workspace / "自訂工具 bin"
    output = workspace / "輸出資料夾"
    tools.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=True)

    _write_executable(tools / "ffmpeg", "ffmpeg version 7.1")
    _write_executable(tools / "ffprobe", "ffprobe version 7.1")
    _write_executable(tools / "deno", "deno 2.3.1")
    report = ExternalToolInspector(timeout=3).inspect(str(tools), str(tools))
    if not report.ffmpeg_available or not report.js_runtime_available:
        raise RuntimeError("Custom tool directory was not detected")

    unicode_result = subprocess.run(
        [str(tools / "deno"), "--version"], capture_output=True, text=True, encoding="utf-8", check=True,
    )
    if "deno 2.3.1" not in unicode_result.stdout: raise RuntimeError("UTF-8 subprocess output was not preserved")

    failing = tools / "failing tool"
    _write_executable(failing, "expected failure", 7)
    failed_result = subprocess.run([str(failing)], capture_output=True, text=True, encoding="utf-8", check=False)
    if failed_result.returncode != 7: raise RuntimeError("Non-zero subprocess exit code was not preserved")

    slow = tools / "slow tool"
    slow.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
    slow.chmod(slow.stat().st_mode | stat.S_IXUSR)
    timeout_detected = False
    try:
        subprocess.run([str(slow)], timeout=0.1, check=False)
    except subprocess.TimeoutExpired:
        timeout_detected = True
    if not timeout_detected: raise RuntimeError("Subprocess timeout was not detected")

    deno = tools / "deno"
    deno.chmod(stat.S_IRUSR | stat.S_IWUSR)
    denied_report = ExternalToolInspector(timeout=3).inspect(str(tools), str(tools))
    deno.chmod(deno.stat().st_mode | stat.S_IXUSR)
    deno_status = next(status for status in denied_report.runtimes if status.key == "deno")
    if deno_status.available: raise RuntimeError("Executable without execute permission was accepted")

    writable_file = output / "測試 output.txt"
    writable_file.write_text("MochiStar", encoding="utf-8")
    if writable_file.read_text(encoding="utf-8") != "MochiStar": raise RuntimeError("Writable output check failed")

    read_only = workspace / "唯讀資料夾"
    read_only.mkdir(exist_ok=True)
    read_only.chmod(stat.S_IRUSR | stat.S_IXUSR)
    permission_error = ""
    try:
        (read_only / "blocked.txt").write_text("blocked", encoding="utf-8")
    except OSError as error:
        permission_error = f"{type(error).__name__}: {error}"
    finally:
        read_only.chmod(stat.S_IRWXU)
    if not permission_error: raise RuntimeError("Read-only output directory unexpectedly accepted a write")

    result = {
        "custom_tools": {"ffmpeg": report.ffmpeg.path, "ffprobe": report.ffprobe.path, "runtimes": report.valid_runtimes},
        "unicode_stdout": unicode_result.stdout.strip(), "nonzero_exit_code": failed_result.returncode,
        "timeout_detected": timeout_detected, "non_executable_state": deno_status.state,
        "permission_error": permission_error, "success": True,
    }
    (result_dir / "platform-checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__": raise SystemExit(main())
