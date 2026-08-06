from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal


ToolState = Literal["available", "missing", "outdated", "invalid"]


@dataclass(frozen=True, slots=True)
class ToolStatus:
    """保存外部工具的路徑、版本與可用狀態"""

    key: str
    name: str
    executable: str
    path: str = ""
    version: str = ""
    minimum_version: str = ""
    state: ToolState = "missing"
    detail: str = ""

    @property
    def available(self) -> bool:
        return self.state == "available"


@dataclass(frozen=True, slots=True)
class DependencyReport:
    """彙整 FFmpeg 與 JavaScript runtime 的檢查結果"""

    ffmpeg: ToolStatus
    ffprobe: ToolStatus
    runtimes: tuple[ToolStatus, ...]

    @property
    def ffmpeg_available(self) -> bool:
        return self.ffmpeg.available and self.ffprobe.available

    @property
    def valid_runtimes(self) -> dict[str, str]:
        return {status.key: status.path for status in self.runtimes if status.available}

    @property
    def js_runtime_available(self) -> bool:
        return bool(self.valid_runtimes)

    @property
    def missing_dependency_ids(self) -> set[str]:
        missing: set[str] = set()
        if not self.ffmpeg_available: missing.add("ffmpeg")
        if not self.js_runtime_available: missing.add("js_runtime")
        return missing


@dataclass(frozen=True, slots=True)
class InstallGuide:
    """保存單一依賴的安裝命令與官方說明"""

    command: str
    url: str
    requires_admin: bool = False
    package_manager_url: str = ""


class ExternalToolInspector:
    """從自訂目錄或 PATH 偵測並驗證 MochiStar 外部依賴"""

    def __init__(
        self,
        which: Callable[..., str | None] = shutil.which,
        run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout: float = 2.0,
    ):
        self.which = which
        self.run_command = run_command
        self.timeout = timeout

    def inspect(self, ffmpeg_directory: str = "", js_runtime_directory: str = "") -> DependencyReport:
        """檢查目前設定實際會使用的 FFmpeg 與 JavaScript runtime"""
        ffmpeg = self._inspect_program("ffmpeg", "FFmpeg", "ffmpeg", ("-version",), ffmpeg_directory)
        ffprobe = self._inspect_program("ffprobe", "FFprobe", "ffprobe", ("-version",), ffmpeg_directory)
        runtimes = (
            self._inspect_runtime("deno", "Deno", "deno", ("--version",), js_runtime_directory, "2.3.0"),
            self._inspect_runtime("node", "Node", "node", ("--version",), js_runtime_directory, "22.0.0"),
            self._inspect_quickjs(js_runtime_directory),
            self._inspect_runtime("bun", "Bun", "bun", ("--version",), js_runtime_directory, "1.2.11", "1.3.14"),
        )
        return DependencyReport(ffmpeg, ffprobe, runtimes)

    def _find(self, executable: str, directory: str) -> str:
        try:
            return str(self.which(executable, path=directory) or "") if directory else str(self.which(executable) or "")
        except TypeError:
            return str(self.which(executable) or "")

    def _inspect_program(
        self, key: str, name: str, executable: str, arguments: tuple[str, ...], directory: str
    ) -> ToolStatus:
        path = self._find(executable, directory)
        if not path: return ToolStatus(key, name, executable)
        result, error = self._run(path, arguments)
        if error: return ToolStatus(key, name, executable, path=path, state="invalid", detail=error)
        output = f"{result.stdout}\n{result.stderr}".strip()
        version = self._semantic_version(output)
        return ToolStatus(key, name, executable, path=path, version=version, state="available")

    def _inspect_runtime(
        self,
        key: str,
        name: str,
        executable: str,
        arguments: tuple[str, ...],
        directory: str,
        minimum: str,
        maximum: str = "",
    ) -> ToolStatus:
        path = self._find(executable, directory)
        if not path: return ToolStatus(key, name, executable, minimum_version=minimum)
        result, error = self._run(path, arguments)
        if error:
            return ToolStatus(
                key, name, executable, path=path, minimum_version=minimum, state="invalid", detail=error
            )
        output = f"{result.stdout}\n{result.stderr}".strip()
        version = self._semantic_version(output)
        if not version:
            return ToolStatus(
                key, name, executable, path=path, minimum_version=minimum, state="invalid",
                detail="Unable to determine version",
            )
        current = self._version_tuple(version)
        if current < self._version_tuple(minimum) or maximum and current > self._version_tuple(maximum):
            supported = f"{minimum}-{maximum}" if maximum else minimum
            return ToolStatus(
                key, name, executable, path=path, version=version, minimum_version=supported, state="outdated"
            )
        return ToolStatus(
            key, name, executable, path=path, version=version, minimum_version=minimum, state="available"
        )

    def _inspect_quickjs(self, directory: str) -> ToolStatus:
        path = self._find("qjs", directory)
        if not path: return ToolStatus("quickjs", "QuickJS", "qjs", minimum_version="2023-12-9")
        result, error = self._run(path, ("--help",), ignore_return_code=True)
        if error:
            return ToolStatus(
                "quickjs", "QuickJS", "qjs", path=path, minimum_version="2023-12-9",
                state="invalid", detail=error,
            )
        output = f"{result.stdout}\n{result.stderr}".strip()
        version = self._quickjs_version(output)
        if "quickjs-ng" in output.casefold() or "quickjs ng" in output.casefold():
            return ToolStatus("quickjs", "QuickJS-NG", "qjs", path=path, version=version, state="available")
        if not version:
            return ToolStatus(
                "quickjs", "QuickJS", "qjs", path=path, minimum_version="2023-12-9",
                state="invalid", detail="Unable to determine version",
            )
        state: ToolState = "available" if self._date_tuple(version) >= (2023, 12, 9) else "outdated"
        return ToolStatus(
            "quickjs", "QuickJS", "qjs", path=path, version=version,
            minimum_version="2023-12-9", state=state,
        )

    def _run(
        self, path: str, arguments: tuple[str, ...], ignore_return_code: bool = False
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        try:
            result = self.run_command(
                [path, *arguments], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=self.timeout, check=False, **self._hidden_process_options(),
            )
        except (OSError, subprocess.SubprocessError) as error:
            return subprocess.CompletedProcess([], 1, "", ""), str(error)
        if result.returncode != 0 and not ignore_return_code:
            detail = (result.stderr or result.stdout or f"Exited with code {result.returncode}").strip()
            return result, detail
        return result, ""

    @staticmethod
    def _semantic_version(output: str) -> str:
        match = re.search(r"(?<!\d)[vV]?(\d+\.\d+(?:\.\d+)?)", output)
        return match.group(1) if match else ""

    @staticmethod
    def _quickjs_version(output: str) -> str:
        date_match = re.search(r"(?<!\d)(20\d{2})[-.]([01]?\d)[-.]([0-3]?\d)(?!\d)", output)
        if date_match: return "-".join(str(int(value)) for value in date_match.groups())
        return ExternalToolInspector._semantic_version(output)

    @staticmethod
    def _version_tuple(version: str) -> tuple[int, int, int]:
        values = [int(value) for value in version.split(".")[:3]]
        return tuple((values + [0, 0, 0])[:3])

    @staticmethod
    def _date_tuple(version: str) -> tuple[int, int, int]:
        try:
            return tuple(int(value) for value in version.split("-")[:3])
        except ValueError:
            return (0, 0, 0)

    @staticmethod
    def _hidden_process_options() -> dict[str, object]:
        if sys.platform != "win32": return {}
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        return {"startupinfo": startupinfo, "creationflags": subprocess.CREATE_NO_WINDOW}


def installation_guides(
    platform: str = sys.platform,
    which: Callable[[str], str | None] = shutil.which,
    os_release_path: Path = Path("/etc/os-release"),
) -> dict[str, InstallGuide]:
    """依平台建立只供顯示與複製的安裝指引"""
    ffmpeg_url = "https://ffmpeg.org/download.html"
    deno_url = "https://docs.deno.com/runtime/getting_started/installation/"
    if platform == "win32":
        winget = bool(which("winget"))
        return {
            "ffmpeg": InstallGuide(
                "winget install --exact --id Gyan.FFmpeg --source winget" if winget else "",
                "https://www.gyan.dev/ffmpeg/builds/",
            ),
            "js_runtime": InstallGuide("winget install --exact --id DenoLand.Deno --source winget" if winget else "", deno_url),
        }
    if platform == "darwin":
        brew = bool(which("brew"))
        return {
            "ffmpeg": InstallGuide(
                "brew install ffmpeg" if brew else "", ffmpeg_url,
                package_manager_url="" if brew else "https://brew.sh/",
            ),
            "js_runtime": InstallGuide(
                "brew install deno" if brew else "", deno_url,
                package_manager_url="" if brew else "https://brew.sh/",
            ),
        }

    distro = _linux_distribution(os_release_path)
    commands = {
        "debian": ("apt", "sudo apt update && sudo apt install ffmpeg"),
        "ubuntu": ("apt", "sudo apt update && sudo apt install ffmpeg"),
        "fedora": ("dnf", "sudo dnf install ffmpeg-free"),
        "arch": ("pacman", "sudo pacman -S ffmpeg"),
        "manjaro": ("pacman", "sudo pacman -S ffmpeg"),
        "opensuse": ("zypper", "sudo zypper install ffmpeg"),
        "opensuse-leap": ("zypper", "sudo zypper install ffmpeg"),
        "opensuse-tumbleweed": ("zypper", "sudo zypper install ffmpeg"),
    }
    manager, command = commands.get(distro, ("", ""))
    command = command if manager and which(manager) else ""
    return {
        "ffmpeg": InstallGuide(command, ffmpeg_url, requires_admin=bool(command)),
        "js_runtime": InstallGuide("curl -fsSL https://deno.land/install.sh | sh", deno_url),
    }


def _linux_distribution(path: Path) -> str:
    """從 os-release 讀取 Linux distribution ID"""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r"^ID=[\"']?([^\"'\n]+)", content, re.MULTILINE)
    return match.group(1).strip().casefold() if match else ""
