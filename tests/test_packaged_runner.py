from pathlib import Path


RUNNER = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "run-macos-packaged-system.sh"


def test_packaged_runner_uses_ascii_bundle_path_with_spaces() -> None:
    """避免 Nuitka bootstrap 在 macOS Unicode bundle path 啟動前中止"""
    script = RUNNER.read_text(encoding="utf-8")

    assert 'installed_dir="$test_home/Applications Test"' in script
    assert 'installed_app="$installed_dir/MochiStar.app"' in script
    assert "Applications 測試" not in script
    assert "MochiStar 測試.app" not in script
