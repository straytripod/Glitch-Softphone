"""The look: warm light and dark colors, bundled fonts, one stylesheet.

Every color lives in a Palette, and the stylesheet is built from it, so
light and dark mode are the same rules with different colors. Widgets ask
for a style by role instead of setting colors themselves:

    button.setProperty("role", "primary")    # see ROLES below

"system" mode follows the desktop's light/dark setting, live.

Fonts ship in app/ui/fonts (SIL Open Font License): Inter for text,
Source Serif 4 for headings. Without them Qt falls back to system fonts.
"""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QPalette

FONT_DIR = Path(__file__).parent / "fonts"
ICON_DIR = Path(__file__).parent / "icons"
TEXT_FONT = "Inter"
HEADING_FONT = "Source Serif 4 Subhead"
THEMES = ("system", "light", "dark")

# Widget roles the stylesheet knows: heading, subheading, muted (labels);
# card (frames); primary, success, danger, key, lamp (buttons).


@dataclass(frozen=True)
class Palette:
    window: str        # page background
    surface: str       # cards, inputs, lists
    raised: str        # buttons
    hover: str
    border: str
    text: str
    muted: str
    accent: str        # the warm clay color: main actions, focus, selection
    accent_hover: str
    on_accent: str     # text on accent/success/danger buttons
    selection: str     # selected list row
    success: str
    danger: str
    chevron: str       # drop-down arrow image in ICON_DIR, drawn in the muted color


LIGHT = Palette(
    window="#F6F2EA", surface="#FFFDF8", raised="#FBF8F2", hover="#F0EADF",
    border="#E3DBCD", text="#2B2621", muted="#7C7368",
    accent="#C2603A", accent_hover="#A9502E", on_accent="#FFFFFF",
    selection="#F3DFD3", success="#4F8A55", danger="#B9493A",
    chevron="chevron-light.svg",
)
DARK = Palette(
    window="#1E1C19", surface="#282521", raised="#302C27", hover="#3A352F",
    border="#3D3831", text="#EEE8DE", muted="#A69D90",
    accent="#DD8158", accent_hover="#E6956F", on_accent="#1E1C19",
    selection="#4A3529", success="#73AD77", danger="#E07563",
    chevron="chevron-dark.svg",
)

# Lamp colors work on both backgrounds, so they don't change with the theme.
LAMP_GREEN = "#5E9B62"
LAMP_AMBER = "#E0A33A"
LAMP_RED = "#D05A44"
LAMP_GREY = "#A59E92"


def load_fonts():
    """Register the bundled fonts with Qt (once, after QApplication exists)."""
    for path in sorted(FONT_DIR.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(path))


def is_dark(mode: str) -> bool:
    if mode == "system":
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    return mode == "dark"


def apply_theme(app, mode: str):
    """Style the whole application for "system", "light" or "dark"."""
    palette = DARK if is_dark(mode) else LIGHT
    app.setStyle("Fusion")   # same widget shapes on every desktop; colors come from us
    app.setPalette(qt_palette(palette))
    app.setStyleSheet(stylesheet(palette))


def qt_palette(p: Palette) -> QPalette:
    """Qt's own palette, for the parts a stylesheet doesn't reach (message
    boxes, menus' shortcuts, disabled text)."""
    qp = QPalette()
    for role, color in (
        (QPalette.Window, p.window), (QPalette.WindowText, p.text),
        (QPalette.Base, p.surface), (QPalette.AlternateBase, p.raised),
        (QPalette.Text, p.text), (QPalette.Button, p.raised), (QPalette.ButtonText, p.text),
        (QPalette.Highlight, p.accent), (QPalette.HighlightedText, p.on_accent),
        (QPalette.ToolTipBase, p.surface), (QPalette.ToolTipText, p.text),
        (QPalette.PlaceholderText, p.muted), (QPalette.Link, p.accent),
        (QPalette.Mid, p.border), (QPalette.Dark, p.border),
    ):
        qp.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        qp.setColor(QPalette.Disabled, role, QColor(p.muted))
    return qp


def stylesheet(p: Palette) -> str:
    return f"""
* {{ font-family: "{TEXT_FONT}"; font-size: 10pt; }}
QLabel {{ background: transparent; }}
QLabel[role="heading"] {{ font-family: "{HEADING_FONT}"; font-size: 19pt; font-weight: 600; }}
QLabel[role="subheading"] {{ font-family: "{HEADING_FONT}"; font-size: 13pt; font-weight: 600; }}
QLabel[role="muted"] {{ color: {p.muted}; }}
QLabel[role="call"] {{ font-size: 12pt; }}

QFrame[role="card"] {{
    background: {p.surface}; border: 1px solid {p.border}; border-radius: 14px;
}}

QPushButton {{
    background: {p.raised}; color: {p.text}; border: 1px solid {p.border};
    border-radius: 10px; padding: 7px 14px;
}}
QPushButton:hover {{ background: {p.hover}; }}
QPushButton:pressed, QPushButton:checked {{ background: {p.selection}; border-color: {p.accent}; }}
QPushButton:disabled {{ color: {p.muted}; background: transparent; }}
QPushButton:focus {{ border-color: {p.accent}; }}
QPushButton[role="primary"], QPushButton[role="success"], QPushButton[role="danger"] {{
    color: {p.on_accent}; border: none; font-weight: 600;
}}
QPushButton[role="primary"] {{ background: {p.accent}; }}
QPushButton[role="primary"]:hover {{ background: {p.accent_hover}; }}
QPushButton[role="success"] {{ background: {p.success}; }}
QPushButton[role="danger"] {{ background: {p.danger}; }}
QPushButton[role="primary"]:disabled, QPushButton[role="success"]:disabled,
QPushButton[role="danger"]:disabled {{
    background: {p.hover}; color: {p.muted}; font-weight: normal;
}}
QPushButton[role="key"] {{
    font-size: 15pt; border-radius: 22px; min-height: 30px; min-width: 56px;
    background: {p.surface};
}}
QPushButton[role="lamp"] {{
    text-align: left; padding: 8px 10px; background: {p.surface}; border-radius: 12px;
}}
QPushButton[role="lamp"]:hover {{ background: {p.hover}; }}

QLineEdit, QComboBox, QSpinBox {{
    background: {p.surface}; color: {p.text}; border: 1px solid {p.border};
    border-radius: 10px; padding: 7px 10px;
    selection-background-color: {p.accent}; selection-color: {p.on_accent};
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {p.accent}; }}
QLineEdit[role="dial"] {{ font-size: 14pt; padding: 9px 14px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url("{(ICON_DIR / p.chevron).as_posix()}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {p.surface}; border: 1px solid {p.border}; border-radius: 8px;
    selection-background-color: {p.selection}; selection-color: {p.text}; padding: 4px;
}}

QListWidget, QTableWidget {{
    background: {p.surface}; alternate-background-color: {p.raised};
    border: 1px solid {p.border}; border-radius: 12px; padding: 4px; outline: none;
}}
QListWidget::item {{ padding: 8px; border-radius: 8px; }}
QListWidget::item:selected, QTableWidget::item:selected {{
    background: {p.selection}; color: {p.text};
}}
QHeaderView::section {{
    background: transparent; color: {p.muted}; border: none;
    border-bottom: 1px solid {p.border}; padding: 6px;
}}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{
    background: transparent; color: {p.muted}; padding: 8px 16px; margin-right: 4px;
    border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {p.text}; border-bottom-color: {p.accent}; }}
QTabBar::tab:hover {{ color: {p.text}; }}

QMenuBar {{ background: {p.window}; }}
QMenuBar::item {{ padding: 6px 10px; border-radius: 6px; }}
QMenuBar::item:selected {{ background: {p.hover}; }}
QMenu {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {p.selection}; color: {p.text}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 5px 8px; }}

QStatusBar {{ color: {p.muted}; }}
QToolTip {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border}; padding: 6px; }}
QCheckBox {{ spacing: 8px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""
