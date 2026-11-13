"""Laqta's look. One accent colour (violet, from the logo, unless the user picks another)
tints every surface; record-red is fixed and means one thing only: recording now."""

from __future__ import annotations

import colorsys
import re
from pathlib import Path

CHEVRON = Path(__file__).with_name("chevron.png").as_posix()
DEFAULT_ACCENT = "#8B5CF6"
REC = "#FF3B3B"
STATUS = {"ok": "#3DD68C", "warn": "#F5A524", "bad": REC}  # health badges; each also carries a word
PRESETS = {  # name -> accent; red is left out on purpose
    "violet": "#8B5CF6",
    "blue": "#4C8DFF",
    "teal": "#2DD4BF",
    "green": "#4ADE80",
    "amber": "#F5A524",
    "rose": "#F472B6",
}


def is_color(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) is not None


def _hex(hue: float, lightness: float, saturation: float) -> str:
    r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
    return "#{:02X}{:02X}{:02X}".format(round(r * 255), round(g * 255), round(b * 255))


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two #RRGGBB colours (1 = identical, 21 = black on white)."""

    def luminance(color: str) -> float:
        channels = (int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))
        r, g, b = (v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in channels)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def palette(accent: str = DEFAULT_ACCENT) -> dict[str, str]:
    """Every colour the UI uses, derived from the accent's hue so surfaces never clash with it."""
    accent = accent if is_color(accent) else DEFAULT_ACCENT
    r, g, b = (int(accent[i : i + 2], 16) / 255 for i in (1, 3, 5))
    hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    tint = min(saturation, 0.45)  # a grey accent gives neutral surfaces
    # Primary button: choose the label colour, then nudge the fill darker or lighter until
    # the label passes WCAG AA (4.5:1). The logo violet with a white label sits just under
    # it, so its fill is darkened by a step too small to notice.
    label = "#FFFFFF" if contrast("#FFFFFF", accent) >= 3 else "#14101F"  # white unless the accent is light
    fill, step = accent.upper(), -0.02 if label == "#FFFFFF" else 0.02
    level = lightness
    while contrast(label, fill) < 4.5 and 0.05 < level < 0.95:
        level += step
        fill = _hex(hue, level, saturation)
    return {
        "ink": _hex(hue, 0.075, tint * 0.9),  # window
        "panel": _hex(hue, 0.135, tint * 0.9),  # rail, inputs, raised areas
        "line": _hex(hue, 0.235, tint * 0.8),  # borders
        "text": _hex(hue, 0.945, 0.5),
        "muted": _hex(hue, 0.645, tint * 0.4),
        "accent": accent.upper(),
        "accent_fill": fill,  # primary button background
        "accent_hover": _hex(hue, min(0.9, level + 0.05), saturation),
        "accent_dim": _hex(hue, 0.31, min(saturation, 0.5)),  # selected row
        "on_accent": label,  # text on the primary button
        "rec": REC,
    }


current = palette()  # what is on screen now; widgets that paint by hand read this


def qss(accent: str = DEFAULT_ACCENT) -> str:
    c = palette(accent)
    return f"""
* {{ color: {c['text']}; font-size: 13px; }}
QDialog, QWidget#page, QStackedWidget {{ background: {c['ink']}; }}
QLabel#heading {{ font-size: 20px; font-weight: 600; padding-bottom: 6px; }}
QLabel#hint, QLabel#muted {{ color: {c['muted']}; }}
QLabel#gameName {{ font-size: 17px; font-weight: 600; }}
QWidget#header {{ background: {c['panel']}; border-bottom: 1px solid {c['line']}; }}
QWidget#footer {{ background: {c['panel']}; border-top: 1px solid {c['line']}; }}
QWidget#header QLabel, QWidget#footer QLabel, QWidget#pill QLabel {{ background: transparent; }}
QLabel#brand {{ font-size: 20px; font-weight: 700; }}
QWidget#pill {{ background: {c['ink']}; border: 1px solid {c['line']}; border-radius: 16px; }}
QLabel#state {{ font-size: 13px; }}
QStackedWidget#detail, QStackedWidget#detail > QWidget, QWidget#detailBody, QStackedWidget#detail QScrollArea {{ background: {c['panel']}; }}
QWidget#detailBody QLabel {{ background: transparent; }}
QStackedWidget#detail {{ border-left: 1px solid {c['line']}; }}
QStackedWidget#detail QPushButton {{ background: {c['ink']}; }}
QStackedWidget#detail QPushButton#primary {{ background: {c['accent_fill']}; }}
QLabel#empty {{ color: {c['muted']}; font-size: 15px; background: transparent; }}

QListWidget {{ background: {c['panel']}; border: none; outline: 0; padding: 6px; }}
QListWidget#rail {{ padding: 4px 8px; font-size: 14px; }}
QWidget#sidebar {{ background: {c['panel']}; border-right: 1px solid {c['line']}; }}
QLabel#railTitle {{ color: {c['muted']}; font-size: 12px; font-weight: 600; padding: 10px 20px 4px 20px; background: transparent; }}
QWidget#sidebar QLabel {{ background: transparent; }}
QWidget#sidebar QPushButton {{ background: {c['ink']}; }}
QListWidget::item {{ padding: 8px 12px; border-radius: 6px; margin: 1px 0; }}
QListWidget::item:hover {{ background: {c['line']}; }}
QListWidget::item:selected {{ background: {c['accent_dim']}; color: {c['text']}; }}
QListWidget#grid {{ background: {c['ink']}; padding: 8px 12px; }}
QListWidget#grid::item, QListWidget#grid::item:hover, QListWidget#grid::item:selected {{ background: transparent; padding: 0; margin: 0; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QKeySequenceEdit {{
    background: {c['panel']}; border: 1px solid {c['line']}; border-radius: 6px; padding: 6px 10px; min-height: 20px;
    selection-background-color: {c['accent']}; selection-color: {c['on_accent']};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QKeySequenceEdit:focus {{ border: 1px solid {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({CHEVRON}); width: 10px; height: 6px; }}
QComboBox QAbstractItemView {{ background: {c['panel']}; border: 1px solid {c['line']}; selection-background-color: {c['accent_dim']}; outline: 0; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}

QPushButton {{ background: {c['panel']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 8px 16px; }}
QPushButton:hover {{ border-color: {c['accent']}; }}
QPushButton:focus {{ border: 1px solid {c['accent']}; }}
QPushButton:checked {{ background: {c['accent_dim']}; border-color: {c['accent']}; }}
QPushButton:disabled {{ color: {c['muted']}; border-color: {c['panel']}; }}
QPushButton#primary {{ background: {c['accent_fill']}; border-color: {c['accent_fill']}; color: {c['on_accent']}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {c['accent_hover']}; }}
QPushButton#primary:disabled {{ background: {c['line']}; border-color: {c['line']}; color: {c['muted']}; }}
QPushButton#danger {{ color: {c['muted']}; }}
QPushButton#danger:hover {{ border-color: {c['rec']}; color: {c['rec']}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['line']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ height: 0; background: transparent; }}
QPushButton#swatch {{ min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; border-radius: 16px; padding: 0;
    border: 2px solid {c['ink']}; }}
QPushButton#swatch:checked {{ border: 2px solid {c['text']}; }}

QCheckBox {{ spacing: 8px; padding: 3px 0; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {c['line']}; border-radius: 4px; background: {c['panel']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
QCheckBox::indicator:focus {{ border-color: {c['accent']}; }}

QLabel#badge_ok, QLabel#badge_warn, QLabel#badge_bad {{ border-radius: 6px; padding: 3px 8px; font-weight: 600; background: {c['panel']}; }}
QLabel#badge_ok {{ color: {STATUS['ok']}; }}
QLabel#badge_warn {{ color: {STATUS['warn']}; }}
QLabel#badge_bad {{ color: {STATUS['bad']}; }}
QLabel#checkTitle {{ font-weight: 600; }}
QLabel#toast {{ background: {c['panel']}; border: 1px solid {c['accent']}; padding: 12px 16px; font-size: 14px; }}
QTextBrowser {{ background: {c['panel']}; border: 1px solid {c['line']}; border-radius: 6px; padding: 8px; }}
QPushButton#tile {{ text-align: left; padding: 9px 12px; }}
QPushButton#tile:checked {{ background: {c['accent_dim']}; border: 1px solid {c['accent']}; }}
QLabel#tileTitle {{ font-weight: 600; font-size: 14px; background: transparent; }}
QLabel#tileHint {{ color: {c['muted']}; font-size: 12px; background: transparent; }}
QLabel#preview {{ background: #000000; border-radius: 6px; }}
QPushButton#playButton {{ background: {c['accent_fill']}; border-color: {c['accent_fill']}; color: {c['on_accent']}; padding: 0; font-size: 14px; }}
QPushButton#playButton:hover {{ background: {c['accent_hover']}; }}
QStackedWidget#detail QLabel#preview {{ background: transparent; }}
QProgressBar {{ background: {c['panel']}; border: none; border-radius: 4px; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
QProgressDialog {{ background: {c['ink']}; }}
QScrollArea {{ border: none; background: {c['ink']}; }}
QFrame#rule {{ background: {c['line']}; max-height: 1px; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {c['line']}; border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QMenu {{ background: {c['panel']}; border: 1px solid {c['line']}; padding: 6px; }}
QMenu::item {{ padding: 7px 22px; border-radius: 5px; }}
QMenu::item:selected {{ background: {c['accent_dim']}; }}
QMenu::item:disabled {{ color: {c['muted']}; }}
QMenu::separator {{ height: 1px; background: {c['line']}; margin: 5px 8px; }}
QToolTip {{ background: {c['panel']}; border: 1px solid {c['line']}; padding: 4px 8px; }}
"""


def apply(app, accent: str = DEFAULT_ACCENT) -> None:
    """Restyle the whole running app."""
    current.clear()
    current.update(palette(accent))
    app.setStyleSheet(qss(accent))
