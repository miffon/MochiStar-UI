from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


MAX_TEXT_LENGTH = 30_000
ATTEMPT_SUFFIX = re.compile(r"-\d+$")


def _display_json(path: Path) -> tuple[str, dict | None]:
    """將 JSON 轉成 log 文字, UI report 省略重複 screenshot 明細"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return path.read_text(encoding="utf-8", errors="replace"), None
    display = data
    if path.name == "report.json" and isinstance(data, dict):
        display = {key: value for key, value in data.items() if key not in {"captures", "dropdowns", "dialogs"}}
        display["dropdowns"] = [
            {key: item.get(key) for key in (
                "name", "source", "path", "popup_width", "popup_height", "overlap_height", "overlaps_combo",
            )}
            for item in data.get("dropdowns", []) if isinstance(item, dict)
        ]
    return json.dumps(display, ensure_ascii=False, indent=2), data if isinstance(data, dict) else None


def _safe_image_name(path: Path, source: Path) -> str:
    """將 screenshot 相對路徑攤平成容易上傳與排序的檔名"""
    parts = path.relative_to(source).parts
    if parts and parts[0] == "ui": parts = parts[1:]
    return "-".join(parts)


def build_bundle(source: Path, output: Path, title: str) -> None:
    """建立含摘要、合併 log 和 UI 圖檔的 artifact bundle"""
    output.mkdir(parents=True, exist_ok=True)
    ui_dir = output / "ui"
    ui_dir.mkdir(exist_ok=True)
    text_paths = sorted(
        path for path in source.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".log", ".txt", ".md"}
        and not path.is_relative_to(output)
    )
    image_paths = sorted(
        path for path in source.rglob("*.png") if path.is_file() and not path.is_relative_to(output)
    )

    summaries = []
    log_sections = []
    for path in text_paths:
        relative = path.relative_to(source).as_posix()
        if path.suffix.lower() == ".json": text, data = _display_json(path)
        else: text, data = path.read_text(encoding="utf-8", errors="replace"), None
        if len(text) > MAX_TEXT_LENGTH: text = text[:MAX_TEXT_LENGTH] + "\n... truncated in artifact bundle"
        log_sections.append(f"===== {relative} =====\n{text.rstrip()}\n")
        if data is not None and not ATTEMPT_SUFFIX.search(path.stem):
            status = "passed" if data.get("success") is True else "failed" if data.get("success") is False else "reported"
            detail = data.get("failure") or data.get("error") or ""
            summaries.append(f"- `{relative}`: {status}" + (f" - {detail}" if detail else ""))

    ui_files = []
    for path in image_paths:
        destination = ui_dir / _safe_image_name(path, source)
        shutil.copy2(path, destination)
        ui_files.append(destination.name)

    readme = [
        f"# {title}", "", f"- Text diagnostics: {len(text_paths)}", f"- UI screenshots: {len(ui_files)}", "",
        "## Test results", "", *(summaries or ["- No canonical JSON report was produced"]), "",
        "## Files", "", "- `report.log`: merged textual diagnostics",
        "- `ui/`: page, scrolled content, dropdown, and dialog screenshots", "",
    ]
    if ui_files:
        readme.extend(["## UI screenshots", "", *(f"- `ui/{name}`" for name in ui_files), ""])
    (output / "README.md").write_text("\n".join(readme), encoding="utf-8")
    (output / "report.log").write_text("\n".join(log_sections), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build one readable macTest artifact bundle")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--title", required=True)
    arguments = parser.parse_args()
    build_bundle(arguments.source, arguments.output, arguments.title)
    return 0


if __name__ == "__main__": raise SystemExit(main())
