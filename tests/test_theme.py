from __future__ import annotations

import re
import tomllib

import pytest

import theme
from theme import ThemeError, apply_theme, load_theme_stylesheet, mix_hsl


PALETTE = """
[palette]
background = "#000000"
chrome = "#101010"
surface = "#202020"
accent = "#3366ff"
accent_aux = "#6699ff"
text = "#ffffff"
warning = "#ddaa33"
error = "#ff3366"
success = "#33aa66"
[variation]
strength = 0.5
"""


def test_starlit_night_resolves_tokens_and_contains_core_styles():
    values = tomllib.loads(theme._read_resource_text("assets", "themes", "starlit_night.toml"))
    palette, strength = values["palette"], float(values["variation"]["strength"])
    stylesheet = load_theme_stylesheet()
    group_title_color = mix_hsl(
        palette["background"], palette["surface"], min(1, 0.36 * strength / 0.5)
    )
    assert "{{" not in stylesheet
    assert palette["background"] in stylesheet
    assert palette["accent"] in stylesheet
    assert theme.theme_color("warning") == palette["warning"]
    assert re.search(
        rf'QMainWindow\[customTitleBar="true"\]\s*\{{[^}}]*background-color:\s*{palette["background"]}',
        stylesheet,
        re.DOTALL,
    )
    assert re.search(rf"QDialog\s*\{{[^}}]*background-color:\s*{palette['background']}", stylesheet, re.DOTALL)
    assert re.search(
        r'QWidget#appRoot\[customTitleBar="true"\]\s*\{[^}]*border-radius:\s*8px', stylesheet, re.DOTALL
    )
    assert re.search(rf'QFrame\[role="topBar"\]\s*\{{[^}}]*background-color:\s*{palette["chrome"]}', stylesheet, re.DOTALL)
    assert re.search(rf'QFrame\[role="statusBar"\]\s*\{{[^}}]*background-color:\s*{palette["chrome"]}', stylesheet, re.DOTALL)
    assert 'QPushButton[role="primary"]:hover' in stylesheet
    assert 'QPushButton[role="navigation"]:checked' in stylesheet
    assert 'QFrame[role="topBar"]' in stylesheet
    assert 'QLabel[role="mediaPreview"]' in stylesheet
    assert "QTableView::item:selected" in stylesheet
    assert "QScrollBar::handle:hover" in stylesheet
    assert "QComboBox::down-arrow" in stylesheet
    assert re.search(rf"QProgressBar\s*\{{[^}}]*qproperty-chunkColor:\s*{palette['accent']}", stylesheet, re.DOTALL)
    assert "QComboBox:editable QLineEdit" in stylesheet
    assert 'font-family: "Segoe UI"' not in stylesheet
    assert 'QComboBox[role="formatSelector"]' in stylesheet
    assert "chevron-down.svg" in stylesheet
    assert "chevron-up.svg" in stylesheet
    assert "QDoubleSpinBox::up-arrow" in stylesheet
    assert "QSplitter::handle:horizontal" in stylesheet
    assert 'QListWidget[dragActive="true"]' in stylesheet
    assert 'QCheckBox[role="inlineOption"]' in stylesheet
    assert re.search(r"QSpinBox::up-button, QDoubleSpinBox::up-button,[^{]*\{[^}]*width:\s*18px", stylesheet, re.DOTALL)
    assert re.search(r"QComboBox\s*\{[^}]*padding-left:\s*12px", stylesheet, re.DOTALL)
    assert re.search(r"QComboBox:editable\s*\{[^}]*padding-left:\s*8px", stylesheet, re.DOTALL)
    assert re.search(r"QHeaderView::section\s*\{[^}]*padding:\s*4px 10px", stylesheet, re.DOTALL)
    assert re.search(
        rf"QGroupBox::title\s*\{{[^}}]*background-color:\s*{group_title_color}[^}}]*border-radius:\s*5px",
        stylesheet,
        re.DOTALL,
    )
    assert re.search(
        r'QStackedWidget, QWidget\[role="panel"\], QScrollArea,[^{]*\{[^}]*background-color:\s*transparent',
        stylesheet,
        re.DOTALL,
    )
    assert re.search(r"QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox\s*\{[^}]*padding:\s*0 10px", stylesheet, re.DOTALL)
    assert re.search(r"QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox\s*\{[^}]*max-height:\s*28px", stylesheet, re.DOTALL)
    assert re.search(
        r"QComboBox\s*\{[^}]*padding-top:\s*-1px[^}]*padding-bottom:\s*1px", stylesheet, re.DOTALL
    )
    assert re.search(r"QComboBox\s*\{[^}]*combobox-popup:\s*0", stylesheet, re.DOTALL)
    assert re.search(r"QComboBox::drop-down\s*\{[^}]*subcontrol-origin:\s*border[^}]*width:\s*18px", stylesheet, re.DOTALL)
    assert re.search(r"QComboBox QAbstractItemView::item\s*\{[^}]*padding:\s*0 7px", stylesheet, re.DOTALL)
    assert re.search(
        r"QComboBox QAbstractItemView::item:hover\s*\{[^}]*color:[^}]*background-color:", stylesheet, re.DOTALL,
    )
    assert re.search(
        r'QTableView QComboBox\[role="tableCell"\]\s*\{[^}]*padding-top:\s*0[^}]*padding-bottom:\s*0',
        stylesheet, re.DOTALL,
    )
    assert "16777215px" not in stylesheet


def test_apply_theme_sets_application_stylesheet():
    class FakeApplication:
        stylesheet = ""

        def setStyleSheet(self, stylesheet: str) -> None:
            self.stylesheet = stylesheet

    app = FakeApplication()
    stylesheet = apply_theme(app)  # type: ignore[arg-type]
    assert app.stylesheet == stylesheet
    assert stylesheet


def test_macos_typography_is_larger_without_changing_other_platforms():
    windows_stylesheet = load_theme_stylesheet(platform_name="win32")
    linux_stylesheet = load_theme_stylesheet(platform_name="linux")
    macos_stylesheet = load_theme_stylesheet(platform_name="darwin")

    assert re.search(r"QWidget\s*\{[^}]*font-size:\s*10pt", windows_stylesheet, re.DOTALL)
    assert re.search(r"QWidget\s*\{[^}]*font-size:\s*10pt", linux_stylesheet, re.DOTALL)
    assert re.search(r"QWidget\s*\{[^}]*font-size:\s*13pt", macos_stylesheet, re.DOTALL)
    assert re.search(r'QLabel\[role="pageTitle"\]\s*\{[^}]*font-size:\s*21pt', macos_stylesheet, re.DOTALL)
    assert re.search(
        r'QLabel\[role="pageSubtitle"\],[^{]*\{[^}]*font-size:\s*13pt', macos_stylesheet, re.DOTALL
    )
    assert re.search(r'QLabel\[role="brandTitle"\]\s*\{[^}]*font-size:\s*17pt', macos_stylesheet, re.DOTALL)


def test_cute_light_theme_and_hsl_mix_are_deterministic():
    values = tomllib.loads(theme._read_resource_text("assets", "themes", "cute_light.toml"))
    palette = values["palette"]
    stylesheet = load_theme_stylesheet("cute_light")
    assert "{{" not in stylesheet
    assert "#fff8f6" in stylesheet
    assert "#f17986" in stylesheet
    assert re.search(
        rf"QMainWindow,[^{{]*\{{[^}}]*background-color:\s*{palette['background']}", stylesheet, re.DOTALL
    )
    assert re.search(r'QFrame\[role="topBar"\]\s*\{[^}]*background-color:\s*#fff8f6', stylesheet, re.DOTALL)
    assert re.search(r'QFrame\[role="statusBar"\]\s*\{[^}]*background-color:\s*#fff8f6', stylesheet, re.DOTALL)
    assert re.search(r"QCheckBox::indicator:checked,[^{]*\{[^}]*background-color:\s*#f17986", stylesheet, re.DOTALL)
    assert "QTableView::indicator:checked" in stylesheet
    assert "QCheckBox::indicator:checked:disabled" in stylesheet
    assert re.search(r"QProgressBar::chunk\s*\{[^}]*background-color:\s*#f17986", stylesheet, re.DOTALL)
    assert "checkmark.svg" in stylesheet
    assert "window-minimize-on-light.svg" in stylesheet
    assert "window-close-on-light.svg" in stylesheet
    assert "window-close-on-dark.svg" in load_theme_stylesheet("starlit_night")
    assert mix_hsl("#123456", "#abcdef", 0) == "#123456"
    assert mix_hsl("#123456", "#abcdef", 1) == "#abcdef"


def test_theme_rejects_invalid_variation_strength(monkeypatch):
    def fake_resource(*parts: str) -> str:
        if parts[-1] == "main.qss": return "QWidget { color: {{colors.text_primary}}; }"
        return PALETTE.replace("strength = 0.5", "strength = 1.5")

    monkeypatch.setattr(theme, "_read_resource_text", fake_resource)
    with pytest.raises(ThemeError, match="strength must be from 0 to 1"):
        load_theme_stylesheet()


def test_missing_qss_token_has_clear_error(monkeypatch):
    def fake_resource(*parts: str) -> str:
        if parts[-1] == "main.qss": return "QWidget { color: {{colors.missing}}; }"
        return PALETTE

    monkeypatch.setattr(theme, "_read_resource_text", fake_resource)
    with pytest.raises(ThemeError, match=r"missing QSS tokens: colors\.missing"):
        load_theme_stylesheet()


def test_unresolved_token_has_clear_error(monkeypatch):
    def fake_resource(*parts: str) -> str:
        if parts[-1] == "main.qss": return "QWidget { font-size: {{typography.font_size}}; }"
        return PALETTE + '\n[typography]\nfont_size = "{{colors.unresolved}}"\n'

    monkeypatch.setattr(theme, "_read_resource_text", fake_resource)
    with pytest.raises(ThemeError, match=r"left unresolved QSS tokens: colors\.unresolved"):
        load_theme_stylesheet()


def test_theme_name_rejects_resource_traversal():
    with pytest.raises(ThemeError, match="Invalid theme name"):
        load_theme_stylesheet("../starlit_night")
