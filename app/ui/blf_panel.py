"""Busy lamp field side panel: one button per watched extension."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from app.config import BlfEntry
from app.ui.theme import LAMP_AMBER, LAMP_GREEN, LAMP_GREY, LAMP_RED
from app.presence import Presence, presence_text
from app.sip.blf import BlfState, BlfStatus

if TYPE_CHECKING:  # type hint only; keeps this module importable without pjsua2
    from app.services.phone_service import PhoneService

LAMP_COLORS = {
    BlfState.IDLE: QColor(LAMP_GREEN),
    BlfState.RINGING: QColor(LAMP_AMBER),  # flashes
    BlfState.BUSY: QColor(LAMP_RED),
    BlfState.UNKNOWN: QColor(LAMP_GREY),
}
FLASH_MS = 500
LAMP_SIZE = 14


def lamp_state(status: BlfStatus, presence: Presence = Presence()) -> BlfState:
    """The lamp to show. An extension with no phone registered has no calls,
    so the PBX reports it idle; grey (not green) says nobody will answer."""
    if status.state is BlfState.IDLE and presence.online is False:
        return BlfState.UNKNOWN
    return status.state


def lamp_text(entry: BlfEntry, status: BlfStatus, presence: Presence = Presence(),
              pbx_name: str = "") -> str:
    """Who the lamp is, what they're doing, and their status if they set one
    (e.g. "At a Meeting"). The name typed into the BLF list wins over the
    PBX directory's."""
    label = entry.label or pbx_name
    name = f"{entry.extension}  {label}" if label else entry.extension
    if status.state is BlfState.IDLE and presence.online is False:
        return f"{name}\nunavailable"
    text = _lamp_lines(name, status)
    extra = presence_text(presence)
    return f"{text}\n{extra}" if extra else text


def _lamp_lines(name: str, status: BlfStatus) -> str:
    if status.state is BlfState.RINGING:
        detail = f"ringing ({status.remote})" if status.remote else "ringing"
        return f"{name}\n{detail}\nclick to pick up"   # own line: buttons don't wrap
    if status.state is BlfState.BUSY:
        return f"{name}\nbusy ({status.remote})" if status.remote else f"{name}\nbusy"
    if status.state is BlfState.IDLE:
        return f"{name}\nidle"
    return f"{name}\nunknown"


def _lamp_icon(color: QColor | None) -> QIcon:
    pixmap = QPixmap(LAMP_SIZE, LAMP_SIZE)
    pixmap.fill(Qt.transparent)
    if color is not None:
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(color)
        painter.setPen(color.darker(130))
        painter.drawEllipse(1, 1, LAMP_SIZE - 2, LAMP_SIZE - 2)
        painter.end()
    return QIcon(pixmap)


class BlfPanel(QWidget):
    call_requested = Signal(str)   # extension clicked
    intercom_requested = Signal(str)   # "Intercom" from a lamp's right-click menu
    edit_requested = Signal()

    def __init__(self, phone: PhoneService, parent=None):
        super().__init__(parent)
        self._phone = phone
        self._buttons: dict[str, QPushButton] = {}
        self._flash_on = True
        self._icons = {state: _lamp_icon(color) for state, color in LAMP_COLORS.items()}
        self._dark = _lamp_icon(None)

        header = QHBoxLayout()
        title = QLabel("Colleagues")
        title.setProperty("role", "subheading")
        header.addWidget(title)
        header.addStretch()
        edit = QPushButton("Edit…")
        edit.clicked.connect(self.edit_requested)
        header.addWidget(edit)
        self._lamps = QVBoxLayout()
        self._lamps.setSpacing(6)
        self._empty = QLabel("No one here yet.\nClick Edit… to add\nthe extensions you call most.")
        self._empty.setProperty("role", "muted")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header)
        layout.addWidget(self._empty)
        layout.addLayout(self._lamps)
        layout.addStretch()
        self.setMinimumWidth(210)

        self._flash = QTimer(self)
        self._flash.setInterval(FLASH_MS)
        self._flash.timeout.connect(self._toggle_flash)
        self._flash.start()

        phone.blf_changed.connect(self._update)
        phone.blf_list_changed.connect(self.rebuild)
        phone.directory_changed.connect(self._update_all)
        self.rebuild()

    def rebuild(self):
        for button in self._buttons.values():
            button.deleteLater()
        self._buttons.clear()
        for entry in self._phone.blf_entries():
            button = QPushButton()
            button.setIconSize(QSize(LAMP_SIZE, LAMP_SIZE))
            button.setProperty("role", "lamp")
            button.clicked.connect(lambda _=False, ext=entry.extension: self.call_requested.emit(ext))
            button.setContextMenuPolicy(Qt.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda pos, b=button, ext=entry.extension: self._lamp_menu(b, ext).popup(b.mapToGlobal(pos)))
            self._lamps.addWidget(button)
            self._buttons[entry.extension] = button
            self._update(entry.extension)
        self._empty.setVisible(not self._buttons)

    def _lamp_menu(self, button: QPushButton, extension: str) -> QMenu:
        menu = QMenu(button)
        ringing = self._phone.blf_status(extension).state is BlfState.RINGING
        menu.addAction("Pick up" if ringing else "Call", lambda: self.call_requested.emit(extension))
        intercom = menu.addAction("Intercom", lambda: self.intercom_requested.emit(extension))
        intercom.setEnabled(self._phone.can_dial())
        return menu

    def _update(self, extension: str):
        button = self._buttons.get(extension)
        entry = next((e for e in self._phone.blf_entries() if e.extension == extension), None)
        if button is None or entry is None:
            return
        status = self._phone.blf_status(extension)
        presence = self._phone.presence(extension)
        text = lamp_text(entry, status, presence, self._phone.directory_name(extension))
        button.setText(text)
        # The whole text again on hover, in case a long name is cut off.
        action = "Click to pick up" if status.state is BlfState.RINGING else f"Click to call {extension}"
        button.setToolTip(f"{text}\n\n{action}")
        self._paint(button, lamp_state(status, presence))

    def _update_all(self):
        for extension in self._buttons:
            self._update(extension)

    def _paint(self, button: QPushButton, state: BlfState):
        dark = state is BlfState.RINGING and not self._flash_on
        button.setIcon(self._dark if dark else self._icons[state])

    def _toggle_flash(self):
        self._flash_on = not self._flash_on
        for extension, button in self._buttons.items():
            state = self._phone.blf_status(extension).state
            if state is BlfState.RINGING:
                self._paint(button, state)
