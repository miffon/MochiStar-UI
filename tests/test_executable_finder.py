from executable_finder import configure_macos_executable_path, find_executable


def test_configure_macos_executable_path_preserves_path_and_adds_homebrew() -> None:
    environment = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin"}

    configure_macos_executable_path(environment, "darwin")

    assert environment["PATH"] == "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin"


def test_configure_macos_executable_path_ignores_other_platforms() -> None:
    environment = {"PATH": "/usr/bin"}

    configure_macos_executable_path(environment, "linux")

    assert environment["PATH"] == "/usr/bin"


def test_find_executable_uses_macos_homebrew_directories_after_path() -> None:
    calls = []

    def which(name: str, path: str | None = None) -> str | None:
        calls.append((name, path))
        return "/opt/homebrew/bin/ffmpeg" if path == "/opt/homebrew/bin:/usr/local/bin" else None

    assert find_executable("ffmpeg", which=which, platform_name="darwin") == "/opt/homebrew/bin/ffmpeg"
    assert calls == [("ffmpeg", None), ("ffmpeg", "/opt/homebrew/bin:/usr/local/bin")]


def test_find_executable_preserves_custom_directory_and_other_platform_path() -> None:
    def which(name: str, path: str | None = None) -> str | None:
        return f"{path}/{name}" if path else None

    assert find_executable("deno", "/custom/bin", which, "darwin") == "/custom/bin/deno"
    assert find_executable("deno", which=which, platform_name="linux") is None
