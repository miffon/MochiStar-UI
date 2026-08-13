"""PySide6 and yt-dlp media downloader"""

from release_config import IS_TEST_BUILD

__version__ = "1.0.2"


def display_version(version: str = __version__, is_test_build: bool = IS_TEST_BUILD) -> str:
    """測試 build 在核心版本後加上 test 標記"""
    return f"{version}-test" if is_test_build else version


DISPLAY_VERSION = display_version()
