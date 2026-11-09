"""The app icon (drawn in code, so there are no image files to ship) and the
system-tray icon with its Show / Quit menu."""

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from app import APP_NAME
from app.ui.theme import HEADING_FONT, LIGHT

ICON_COLOR = QColor(LIGHT.accent)   # warm clay


def app_pixmap(size: int = 64) -> QPixmap:
    """A clay-colored rounded square with a serif "G" in cream."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(ICON_COLOR)
    painter.drawRoundedRect(QRectF(2, 2, size - 4, size - 4), size * 0.22, size * 0.22)
    font = QFont(HEADING_FONT)   # bundled; Qt falls back to a system serif
    font.setStyleHint(QFont.Serif)
    font.setWeight(QFont.DemiBold)
    font.setPixelSize(int(size * 0.62))
    painter.setFont(font)
    painter.setPen(QColor(LIGHT.surface))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "G")
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64, 128):
        icon.addPixmap(app_pixmap(size))
    return icon


def save_icon_png(path: Path) -> str:
    """Write the icon as a PNG for the notification service; returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    app_pixmap(128).save(str(path), "PNG")
    return str(path)


class Tray(QSystemTrayIcon):
    show_requested = Signal()     # "Show" menu item, or a click on the icon
    quit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(app_icon(), parent)
        menu = QMenu()
        # .emit, not the signal itself: PySide6 can't call a signal object.
        menu.addAction(f"Show {APP_NAME}", self.show_requested.emit)
        menu.addSeparator()
        menu.addAction("Quit", self.quit_requested.emit)
        self._menu = menu   # keep a reference: Qt doesn't own a context menu
        self.setContextMenu(menu)
        self.activated.connect(self._activated)
        self.setToolTip(APP_NAME)

    def _activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:   # left click
            self.show_requested.emit()
