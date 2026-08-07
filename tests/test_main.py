from pathlib import Path

from main import _acquire_instance_lock, _initialize_language, _run_update_smoke_test
from storage import AppStorage, Settings


def test_instance_lock_allows_only_one_process(tmp_path: Path) -> None:
    app_dir = tmp_path / "new" / "MochiStar"
    first = _acquire_instance_lock(app_dir)
    assert first is not None
    assert app_dir.is_dir()
    try:
        assert _acquire_instance_lock(app_dir) is None
    finally:
        first.unlock()

    reopened = _acquire_instance_lock(app_dir)
    assert reopened is not None
    reopened.unlock()


def test_first_launch_asks_for_language_and_saves_selection(tmp_path: Path) -> None:
    storage = AppStorage(tmp_path)
    settings = Settings()
    choices = []

    language = _initialize_language(storage, settings, lambda: choices.append("asked") or "en")

    assert language == "en"
    assert choices == ["asked"]
    assert storage.load_settings().language == "en"


def test_existing_settings_keep_language_without_asking(tmp_path: Path) -> None:
    storage = AppStorage(tmp_path)
    settings = Settings(language="zh_TW")
    assert storage.save_settings(settings)

    language = _initialize_language(storage, storage.load_settings(), lambda: "en")

    assert language == "zh_TW"
    assert storage.load_settings().language == "zh_TW"


def test_first_launch_uses_english_for_unknown_selection(tmp_path: Path) -> None:
    storage = AppStorage(tmp_path)

    assert _initialize_language(storage, Settings(), lambda: "unknown") == "en"
    assert storage.load_settings().language == "en"


def test_update_smoke_test_checks_current_platform() -> None:
    class Provider:
        def __init__(self): self.platforms = []

        def check_latest(self, platform_key): self.platforms.append(platform_key)

    provider = Provider()

    assert _run_update_smoke_test(provider) == 0
    assert len(provider.platforms) == 1
