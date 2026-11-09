"""Parked calls side panel: one button per occupied park slot; click to pick up."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout

from app.ui.blf_panel import LAMP_SIZE, _lamp_icon
from app.ui.theme import LAMP_AMBER

if TYPE_CHECKING:  # type hint only; keeps this module importable without pjsua2
    from app.services.phone_service import PhoneService


def slot_text(slot: str, who: str) -> str:
    return f"{slot}  {who}\nclick to pick up"


class ParkPanel(QFrame):
    retrieve_requested = Signal(str)   # slot clicked

    def __init__(self, phone: PhoneService, parent=None):
        super().__init__(parent)
        self._phone = phone
        self.setProperty("role", "card")   # rounded panel (app/ui/theme.py)
        self._icon = _lamp_icon(QColor(LAMP_AMBER))

        title = QLabel("Parked calls")
        title.setProperty("role", "subheading")
        self._empty = QLabel("No one is waiting.")
        self._empty.setProperty("role", "muted")
        self._slots = QVBoxLayout()
        self._slots.setSpacing(6)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.addWidget(title)
        layout.addWidget(self._empty)
        layout.addLayout(self._slots)

        phone.park_changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        # Never show() while parentless (see AgentPanel.refresh).
        enabled = self._phone.park_enabled
        if self.parentWidget() is not None or not enabled:
            self.setVisible(enabled)
        while self._slots.count():
            self._slots.takeAt(0).widget().deleteLater()
        occupied = self._phone.parked_calls()
        for slot, who in occupied:
            button = QPushButton(slot_text(slot, who))
            button.setProperty("role", "lamp")
            button.setIcon(self._icon)
            button.setIconSize(QSize(LAMP_SIZE, LAMP_SIZE))
            button.setToolTip(f"Pick up the call parked on {slot}")
            button.clicked.connect(lambda _=False, s=slot: self.retrieve_requested.emit(s))
            self._slots.addWidget(button)
        self._empty.setVisible(not occupied)
