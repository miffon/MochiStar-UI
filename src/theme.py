from __future__ import annotations

import colorsys
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QApplication


class ThemeError(ValueError):
    """表示 theme 資源或 token 設定無法使用"""


_THEME_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_TOKEN_PATTERN = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")
_HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
_COLOR_RECIPES = {
    "panel_background": ("background", "surface", 0.18),
    "panel_elevated": ("background", "surface", 0.36),
    "card_background": ("background", "surface", 0.28),
    "card_hover": ("background", "surface", 0.43),
    "input_background": ("background", "surface", 0.10),
    "input_hover": ("background", "surface", 0.20),
    "table_background": ("background", "surface", 0.16),
    "table_alternate": ("background", "surface", 0.30),
    "table_header": ("background", "surface", 0.42),
    "button_background": ("background", "surface", 0.48),
    "button_hover": ("background", "surface", 0.64),
    "button_pressed": ("background", "surface", 0.35),
    "button_disabled": ("background", "surface", 0.30),
    "accent_hover": ("accent", "accent_aux", 0.65),
    "accent_pressed": ("accent", "background", 0.18),
    "accent_soft": ("background", "accent", 0.28),
    "selection": ("background", "accent", 0.44),
    "border": ("background", "text", 0.16),
    "border_hover": ("background", "text", 0.27),
    "text_muted": ("text", "background", 0.45),
    "text_disabled": ("text", "background", 0.62),
    "error_hover": ("error", "text", 0.18),
    "error_pressed": ("error", "background", 0.18),
    "scroll_handle": ("surface", "text", 0.16),
    "scroll_hover": ("surface", "text", 0.28),
    "progress_track": ("background", "surface", 0.38),
}
_CURRENT_THEME_COLORS = {
    "success": "#50c878", "warning": "#e5b567", "warning_soft": "#30291d",
    "error": "#f36b7f", "error_soft": "#302025",
}


def _read_resource_text(*parts: str) -> str:
    """讀取 src 內的文字資源"""
    resource = Path(__file__).resolve().parent.joinpath(*parts)
    try:
        return resource.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, OSError) as error:
        path = "/".join(parts)
        raise ThemeError(f"Unable to read theme resource: {path}") from error


def _resource_path(*parts: str) -> str:
    """取得 QSS 可使用的 resource 路徑"""
    resource = Path(__file__).resolve().parent.joinpath(*parts)
    if not resource.is_file():
        path = "/".join(parts)
        raise ThemeError(f"Unable to locate theme resource: {path}")
    return str(resource).replace("\\", "/")


def _flatten_tokens(values: Mapping[str, Any], prefix: str = "") -> dict[str, str]:
    """將巢狀 TOML table 展平成 QSS token"""
    tokens: dict[str, str] = {}
    for key, value in values.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Mapping):
            tokens.update(_flatten_tokens(value, name))
        elif isinstance(value, bool):
            tokens[name] = str(value).lower()
        elif isinstance(value, (str, int, float)):
            tokens[name] = str(value)
        else:
            raise ThemeError(f"Unsupported theme token value: {name}")
    return tokens


def mix_hsl(source: str, target: str, amount: float) -> str:
    """使用最短 hue 路徑混合兩個 hex color"""
    if not _HEX_COLOR_PATTERN.fullmatch(source) or not _HEX_COLOR_PATTERN.fullmatch(target):
        raise ThemeError(f"Invalid hex color: {source!r} or {target!r}")
    amount = max(0.0, min(1.0, float(amount)))
    source_rgb = tuple(int(source[index:index + 2], 16) / 255 for index in (1, 3, 5))
    target_rgb = tuple(int(target[index:index + 2], 16) / 255 for index in (1, 3, 5))
    source_h, source_l, source_s = colorsys.rgb_to_hls(*source_rgb)
    target_h, target_l, target_s = colorsys.rgb_to_hls(*target_rgb)
    hue_delta = (target_h - source_h + 0.5) % 1.0 - 0.5
    hue = (source_h + hue_delta * amount) % 1.0
    lightness = source_l + (target_l - source_l) * amount
    saturation = source_s + (target_s - source_s) * amount
    rgb = colorsys.hls_to_rgb(hue, lightness, saturation)
    return "#" + "".join(f"{round(channel * 255):02x}" for channel in rgb)


def _relative_luminance(color: str) -> float:
    """計算 WCAG relative luminance"""
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(first: str, second: str) -> float:
    high, low = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _derive_colors(theme_values: Mapping[str, Any]) -> dict[str, str]:
    """從 palette anchors 產生 QSS semantic colors"""
    palette = theme_values.get("palette")
    variation = theme_values.get("variation")
    if not isinstance(palette, Mapping): raise ThemeError("Theme is missing [palette]")
    required = {"background", "chrome", "surface", "accent", "accent_aux", "text", "warning", "error", "success"}
    missing = sorted(required - palette.keys())
    if missing: raise ThemeError(f"Theme palette is missing: {', '.join(missing)}")
    anchors = {name: str(palette[name]) for name in required}
    for name, color in anchors.items():
        if not _HEX_COLOR_PATTERN.fullmatch(color): raise ThemeError(f"Invalid palette color {name}: {color!r}")
    strength_value = variation.get("strength", 0.5) if isinstance(variation, Mapping) else 0.5
    try:
        strength = float(strength_value)
    except (TypeError, ValueError) as error:
        raise ThemeError("Theme variation strength must be a number from 0 to 1") from error
    if not 0 <= strength <= 1: raise ThemeError("Theme variation strength must be from 0 to 1")
    scale = strength / 0.5
    colors = {
        "app_background": anchors["background"],
        "topbar_background": anchors["chrome"],
        "status_background": anchors["chrome"],
        "accent": anchors["accent"],
        "border_focus": anchors["accent"],
        "text_primary": anchors["text"],
        "warning": anchors["warning"],
        "error": anchors["error"],
        "success": anchors["success"],
        "transparent": "transparent",
    }
    for name, (source, target, weight) in _COLOR_RECIPES.items():
        colors[name] = mix_hsl(anchors[source], anchors[target], min(1.0, weight * scale))
    colors["error_soft"] = mix_hsl(anchors["background"], anchors["error"], min(1.0, 0.24 * scale))
    colors["warning_soft"] = mix_hsl(anchors["background"], anchors["warning"], min(1.0, 0.20 * scale))
    colors["success_soft"] = mix_hsl(anchors["background"], anchors["success"], min(1.0, 0.22 * scale))
    colors["tooltip_background"] = colors["button_background"]
    colors["text_on_accent"] = max(("#ffffff", "#171117"), key=lambda color: _contrast_ratio(color, anchors["accent"]))
    return colors


def load_theme_stylesheet(theme_name: str = "starlit_night") -> str:
    """載入 TOML theme 並將 token 套用到共用 QSS"""
    if not _THEME_NAME_PATTERN.fullmatch(theme_name):
        raise ThemeError(f"Invalid theme name: {theme_name!r}")

    try:
        theme_values = tomllib.loads(_read_resource_text("assets", "themes", f"{theme_name}.toml"))
    except tomllib.TOMLDecodeError as error:
        raise ThemeError(f"Invalid TOML in theme '{theme_name}': {error}") from error
    tokens = _flatten_tokens({
        key: value for key, value in theme_values.items() if key not in {"palette", "variation"}
    })
    colors = _derive_colors(theme_values)
    _CURRENT_THEME_COLORS.update(colors)
    tokens.update({f"colors.{name}": value for name, value in colors.items()})
    tokens["assets.chevron_down"] = _resource_path("assets", "chevron-down.svg")
    tokens["assets.chevron_up"] = _resource_path("assets", "chevron-up.svg")
    tokens["assets.checkmark"] = _resource_path("assets", "checkmark.svg")
    icon_variant = "on-dark" if _relative_luminance(colors["text_primary"]) > 0.5 else "on-light"
    for name in ("minimize", "maximize", "restore", "close"):
        tokens[f"assets.window_{name}"] = _resource_path("assets", f"window-{name}-{icon_variant}.svg")
    tokens["assets.window_close_hover"] = _resource_path("assets", "window-close-on-dark.svg")
    template = _read_resource_text("assets", "styles", "main.qss")

    referenced = set(_TOKEN_PATTERN.findall(template))
    missing = sorted(referenced - tokens.keys())
    if missing:
        raise ThemeError(f"Theme '{theme_name}' is missing QSS tokens: {', '.join(missing)}")

    stylesheet = _TOKEN_PATTERN.sub(lambda match: tokens[match.group(1)], template)
    unresolved = sorted(set(_TOKEN_PATTERN.findall(stylesheet)))
    if unresolved:
        raise ThemeError(f"Theme '{theme_name}' left unresolved QSS tokens: {', '.join(unresolved)}")
    return stylesheet


def theme_color(name: str, fallback: str = "#000000") -> str:
    """取得目前已載入主題的 semantic color"""
    return _CURRENT_THEME_COLORS.get(name, fallback)


def apply_theme(app: QApplication, theme_name: str = "starlit_night") -> str:
    """載入並套用 application theme"""
    stylesheet = load_theme_stylesheet(theme_name)
    app.setStyleSheet(stylesheet)
    return stylesheet
