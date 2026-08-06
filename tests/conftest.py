import os
import shutil
from collections.abc import Callable
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

SampleMediaFactory = Callable[..., tuple[Path, ...]]


@pytest.fixture(scope="session")
def app() -> QApplication:
    """建立測試共用的 Qt application"""
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="session")
def sample_media(tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest) -> Iterator[SampleMediaFactory]:
    """依測試需求在共用資料夾建立假 media files"""
    directory = tmp_path_factory.getbasetemp() / "sample-media"

    def create(*names: str) -> tuple[Path, ...]:
        directory.mkdir(exist_ok=True)
        paths = tuple(directory / name for name in names)
        for path in paths: path.touch(exist_ok=True)
        return paths

    yield create

    retention_policy = request.config.getini("tmp_path_retention_policy")
    if retention_policy == "none" or retention_policy == "failed" and not request.session.testsfailed:
        shutil.rmtree(directory, ignore_errors=True)
