"""Colours and the application style sheet. Follows the Windows light/dark setting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from ..assets import asset_path


@dataclass(frozen=True)
class Colors:
    dark: bool
    window: str
    surface: str
    surface_alt: str
    border: str
    border_strong: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    on_accent: str
    success: str
    warning: str
    error: str
    selection: str
    hover: str

    def q(self, name: str, alpha: float = 1.0) -> QColor:
        color = QColor(getattr(self, name))
        color.setAlphaF(alpha)
        return color


LIGHT = Colors(
    dark=False,
    window="#F3F5FA",
    surface="#FFFFFF",
    surface_alt="#F6F8FC",
    border="#E3E7F0",
    border_strong="#CBD3E1",
    text="#18202F",
    muted="#667085",
    accent="#3B6CF4",
    accent_hover="#2E5CDC",
    accent_pressed="#254DBA",
    accent_soft="#EAF0FF",
    on_accent="#FFFFFF",
    success="#16803C",
    warning="#B45309",
    error="#D92D20",
    selection="#E1E9FF",
    hover="#F5F7FD",
)

DARK = Colors(
    dark=True,
    window="#14161B",
    surface="#1D2027",
    surface_alt="#242832",
    border="#2D323D",
    border_strong="#3E4554",
    text="#E7EAF1",
    muted="#9AA4B6",
    accent="#5B8CFF",
    accent_hover="#7AA2FF",
    accent_pressed="#4876E8",
    accent_soft="#202C48",
    on_accent="#FFFFFF",
    success="#4ADE80",
    warning="#FBBF24",
    error="#F87171",
    selection="#273556",
    hover="#232733",
)


def _url(name: str) -> str:
    return 'url("' + Path(asset_path(name)).as_posix() + '")'


def _style_sheet(c: Colors) -> str:
    check = _url("check.png")
    chevron = _url("chevron-dark.png" if c.dark else "chevron-light.png")
    return f"""
    QWidget {{ color: {c.text}; }}
    QMainWindow, QWidget#root {{ background: {c.window}; }}
    QLabel {{ background: transparent; }}
    QLabel[muted="true"] {{ color: {c.muted}; }}
    QLabel#title {{ font-size: 20px; font-weight: 700; }}
    QLabel#sectionLabel {{ color: {c.muted}; font-weight: 600; }}
    QLabel#dropTitle {{ font-size: 18px; font-weight: 600; }}
    QLabel#summary {{ font-weight: 600; }}

    QFrame#card {{
        background: {c.surface};
        border: 1px solid {c.border};
        border-radius: 12px;
    }}

    QPushButton {{
        background: {c.surface};
        border: 1px solid {c.border_strong};
        border-radius: 8px;
        padding: 7px 14px;
    }}
    QPushButton:hover {{ background: {c.hover}; border-color: {c.accent}; }}
    QPushButton:pressed {{ background: {c.accent_soft}; }}
    QPushButton:disabled {{ color: {c.muted}; border-color: {c.border}; background: {c.surface_alt}; }}
    QPushButton:focus {{ outline: none; }}

    QPushButton#primary {{
        background: {c.accent};
        color: {c.on_accent};
        border: 1px solid {c.accent};
        font-size: 15px;
        font-weight: 600;
        padding: 10px 28px;
        border-radius: 10px;
    }}
    QPushButton#primary:hover {{ background: {c.accent_hover}; border-color: {c.accent_hover}; }}
    QPushButton#primary:pressed {{ background: {c.accent_pressed}; }}
    QPushButton#primary:disabled {{ background: {c.border_strong}; border-color: {c.border_strong}; color: {c.surface}; }}

    QPushButton#secondaryLarge {{
        font-size: 15px;
        font-weight: 600;
        padding: 10px 24px;
        border-radius: 10px;
    }}

    QPushButton#stop {{
        background: {c.surface};
        color: {c.error};
        border: 1px solid {c.error};
        font-size: 15px;
        font-weight: 600;
        padding: 10px 28px;
        border-radius: 10px;
    }}
    QPushButton#stop:hover {{ background: {c.hover}; }}

    QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: {c.accent}; }}
    QPushButton#ghost:hover {{ background: {c.accent_soft}; }}
    QPushButton#ghost:disabled {{ color: {c.muted}; background: transparent; }}

    QTableView {{
        background: {c.surface};
        border: none;
        outline: 0;
        selection-background-color: {c.selection};
        selection-color: {c.text};
    }}
    QHeaderView {{ background: {c.surface}; border: none; }}
    QHeaderView::section {{
        background: {c.surface};
        color: {c.muted};
        border: none;
        border-bottom: 1px solid {c.border};
        padding: 8px 12px;
        font-weight: 600;
    }}

    QProgressBar {{
        background: {c.border};
        border: none;
        border-radius: 3px;
        max-height: 6px;
        min-height: 6px;
    }}
    QProgressBar::chunk {{ background: {c.accent}; border-radius: 3px; }}

    QSlider::groove:horizontal {{ height: 6px; background: {c.border}; border-radius: 3px; }}
    QSlider::sub-page:horizontal {{ background: {c.accent}; border-radius: 3px; }}
    QSlider::handle:horizontal {{
        background: {c.surface};
        border: 2px solid {c.accent};
        width: 14px;
        height: 14px;
        margin: -6px 0;
        border-radius: 9px;
    }}
    QSlider::handle:horizontal:hover {{ background: {c.accent_soft}; }}
    QSlider::groove:horizontal:disabled, QSlider::sub-page:horizontal:disabled {{ background: {c.border}; }}
    QSlider::handle:horizontal:disabled {{ border-color: {c.border_strong}; }}

    QComboBox {{
        background: {c.surface};
        border: 1px solid {c.border_strong};
        border-radius: 8px;
        padding: 6px 10px;
        min-height: 20px;
    }}
    QComboBox:hover {{ border-color: {c.accent}; }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: center right;
        width: 26px;
        border: none;
    }}
    QComboBox::down-arrow {{ image: {chevron}; width: 14px; height: 14px; }}
    QComboBox:disabled {{ color: {c.muted}; border-color: {c.border}; }}
    QComboBox QAbstractItemView {{
        background: {c.surface};
        border: 1px solid {c.border_strong};
        selection-background-color: {c.selection};
        selection-color: {c.text};
        outline: 0;
        padding: 4px;
    }}

    QLineEdit {{
        background: {c.surface_alt};
        border: 1px solid {c.border_strong};
        border-radius: 8px;
        padding: 6px 10px;
    }}
    QLineEdit:disabled {{ color: {c.muted}; border-color: {c.border}; }}

    QCheckBox, QRadioButton {{ spacing: 9px; background: transparent; }}
    QCheckBox:disabled, QRadioButton:disabled {{ color: {c.muted}; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 16px;
        height: 16px;
        background: {c.surface};
        border: 1px solid {c.border_strong};
    }}
    QCheckBox::indicator {{ border-radius: 5px; }}
    QRadioButton::indicator {{ border-radius: 9px; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {c.accent}; }}
    QCheckBox::indicator:checked {{ background: {c.accent}; border-color: {c.accent}; image: {check}; }}
    QRadioButton::indicator:checked {{
        border-color: {c.accent};
        background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.5, fx: 0.5, fy: 0.5,
            stop: 0 {c.surface}, stop: 0.42 {c.surface}, stop: 0.5 {c.accent}, stop: 1 {c.accent});
    }}
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
        background: {c.surface_alt};
        border-color: {c.border};
    }}
    QCheckBox::indicator:checked:disabled {{ background: {c.border_strong}; border-color: {c.border_strong}; }}

    QToolTip {{
        background: {c.surface};
        color: {c.text};
        border: 1px solid {c.border_strong};
        padding: 6px 8px;
    }}

    QMenu {{ background: {c.surface}; border: 1px solid {c.border_strong}; padding: 4px; }}
    QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {c.selection}; }}
    QMenu::separator {{ height: 1px; background: {c.border}; margin: 4px 8px; }}

    QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {c.border_strong}; border-radius: 4px; min-height: 32px; margin: 0 2px; }}
    QScrollBar::handle:vertical:hover {{ background: {c.muted}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ height: 0; }}
    """


def _palette(c: Colors) -> QPalette:
    p = QPalette()
    role = QPalette.ColorRole
    p.setColor(role.Window, QColor(c.window))
    p.setColor(role.WindowText, QColor(c.text))
    p.setColor(role.Base, QColor(c.surface))
    p.setColor(role.AlternateBase, QColor(c.surface_alt))
    p.setColor(role.Text, QColor(c.text))
    p.setColor(role.Button, QColor(c.surface))
    p.setColor(role.ButtonText, QColor(c.text))
    p.setColor(role.Highlight, QColor(c.accent))
    p.setColor(role.HighlightedText, QColor(c.on_accent))
    p.setColor(role.ToolTipBase, QColor(c.surface))
    p.setColor(role.ToolTipText, QColor(c.text))
    p.setColor(role.PlaceholderText, QColor(c.muted))
    p.setColor(role.Link, QColor(c.accent))
    p.setColor(role.Mid, QColor(c.border_strong))
    p.setColor(role.Midlight, QColor(c.border))
    p.setColor(role.Light, QColor(c.surface))
    p.setColor(role.Dark, QColor(c.border_strong))
    for r in (role.WindowText, role.Text, role.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, r, QColor(c.muted))
    return p


class ThemeManager(QObject):
    """Applies the light or dark theme and re-applies it when Windows switches."""

    changed = Signal(object)  # Colors

    def __init__(self, app: QApplication, force_dark: bool | None = None):
        super().__init__(app)
        self._app = app
        self._force_dark = force_dark
        self.colors = LIGHT
        app.setStyle("Fusion")
        self.apply()
        if force_dark is None:
            QGuiApplication.styleHints().colorSchemeChanged.connect(lambda _scheme: self.apply())

    def _system_is_dark(self) -> bool:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark

    def apply(self) -> None:
        dark = self._system_is_dark() if self._force_dark is None else self._force_dark
        self.colors = DARK if dark else LIGHT
        self._app.setPalette(_palette(self.colors))
        self._app.setStyleSheet(_style_sheet(self.colors))
        self.changed.emit(self.colors)
