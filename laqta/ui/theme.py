"""Laqta's look, taken from the logo: violet-black surfaces, violet for selection and focus,
and record-red kept for one meaning only: recording now."""

from pathlib import Path

CHEVRON = (Path(__file__).with_name("chevron.png")).as_posix()
INK = "#0F0B1A"  # window
PANEL = "#1A1430"  # rail, inputs, raised areas
LINE = "#2E2550"  # borders
TEXT = "#ECE8F7"
MUTED = "#9A91B8"
VIOLET = "#8B5CF6"
VIOLET_DIM = "#3B2A78"  # selected row
REC = "#FF3B3B"

QSS = f"""
* {{ color: {TEXT}; font-size: 13px; }}
QDialog, QWidget#page, QStackedWidget {{ background: {INK}; }}
QLabel#heading {{ font-size: 20px; font-weight: 600; padding-bottom: 6px; }}
QLabel#hint, QLabel#muted {{ color: {MUTED}; }}
QLabel#gameName {{ font-size: 16px; font-weight: 600; }}

QListWidget {{ background: {PANEL}; border: none; outline: 0; padding: 6px; }}
QListWidget#rail {{ padding: 14px 8px; font-size: 14px; }}
QListWidget::item {{ padding: 8px 12px; border-radius: 6px; margin: 1px 0; }}
QListWidget::item:hover {{ background: {LINE}; }}
QListWidget::item:selected {{ background: {VIOLET_DIM}; color: {TEXT}; }}

QLineEdit, QComboBox, QSpinBox, QKeySequenceEdit {{
    background: {PANEL}; border: 1px solid {LINE}; border-radius: 6px; padding: 6px 10px; min-height: 20px;
    selection-background-color: {VIOLET};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QKeySequenceEdit:focus {{ border: 1px solid {VIOLET}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({CHEVRON}); width: 10px; height: 6px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {LINE}; selection-background-color: {VIOLET_DIM}; outline: 0; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

QPushButton {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 6px; padding: 7px 16px; }}
QPushButton:hover {{ border-color: {VIOLET}; }}
QPushButton:focus {{ border: 1px solid {VIOLET}; }}
QPushButton#primary {{ background: {VIOLET}; border-color: {VIOLET}; color: #FFFFFF; font-weight: 600; }}
QPushButton#primary:hover {{ background: #9D74FF; }}
QPushButton#danger:hover {{ border-color: {REC}; color: {REC}; }}

QCheckBox {{ spacing: 8px; padding: 3px 0; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {LINE}; border-radius: 4px; background: {PANEL}; }}
QCheckBox::indicator:checked {{ background: {VIOLET}; border-color: {VIOLET}; }}
QCheckBox::indicator:focus {{ border-color: {VIOLET}; }}

QFrame#rule {{ background: {LINE}; max-height: 1px; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QMenu {{ background: {PANEL}; border: 1px solid {LINE}; padding: 6px; }}
QMenu::item {{ padding: 7px 22px; border-radius: 5px; }}
QMenu::item:selected {{ background: {VIOLET_DIM}; }}
QMenu::item:disabled {{ color: {MUTED}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 8px; }}
QToolTip {{ background: {PANEL}; border: 1px solid {LINE}; padding: 4px 8px; }}
"""
