from __future__ import annotations

import shutil
import sys
from collections.abc import Callable, Iterable

MACOS_EXECUTABLE_DIRECTORIES = ("/opt/homebrew/bin", "/usr/local/bin")


def configure_macos_executable_path(
    environment: dict[str, str],
    platform_name: str = sys.platform,
    macos_directories: Iterable[str] = MACOS_EXECUTABLE_DIRECTORIES,
) -> None:
    """讓 Finder 啟動的 macOS application 與 child processes 找得到 Homebrew tools"""
    if platform_name != "darwin": return
    entries = [entry for entry in environment.get("PATH", "").split(":") if entry]
    for directory in reversed(tuple(macos_directories)):
        if directory not in entries: entries.insert(0, directory)
    environment["PATH"] = ":".join(entries)


def find_executable(
    name: str,
    directory: str = "",
    which: Callable[..., str | None] = shutil.which,
    platform_name: str = sys.platform,
    macos_directories: Iterable[str] = MACOS_EXECUTABLE_DIRECTORIES,
) -> str | None:
    """從自訂目錄、PATH 與 macOS Homebrew 標準目錄尋找 executable"""
    if directory:
        try:
            return which(name, path=directory)
        except TypeError:
            return which(name)

    result = which(name)
    if result or platform_name != "darwin": return result
    search_path = ":".join(macos_directories)
    try:
        return which(name, path=search_path)
    except TypeError:
        return None
