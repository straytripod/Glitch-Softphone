"""Queue agent box: status lamp plus Join queue / Break / Leave queue buttons."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.agent import AgentState, actions_for
from app.ui.blf_panel import LAMP_SIZE, _lamp_icon
from app.ui.theme import LAMP_AMBER, LAMP_GREEN, LAMP_GREY, LAMP_RED

if TYPE_CHECKING:  # type hint only; keeps this module importable without pjsua2
    from app.services.phone_service import PhoneService

UNKNOWN_TIP = ("The PBX hasn't reported your status yet. This button switches it:\n"
               "it joins if you're out and leaves if you're in. The PBX's prompt says which.")

AGENT_COLORS = {
    AgentState.AVAILABLE: QColor(LAMP_GREEN),
    AgentState.ON_BREAK: QColor(LAMP_AMBER),
    AgentState.LOGGED_OUT: QColor(LAMP_RED),
    AgentState.UNKNOWN: QColor(LAMP_GREY),
}


class AgentPanel(QFrame):
    def __init__(self, phone: PhoneService, parent=None):
        super().__init__(parent)
        self._phone = phone
        self.setProperty("role", "card")   # rounded panel (app/ui/theme.py)
        self._icons = {state: _lamp_icon(color) for state, color in AGENT_COLORS.items()}

        self._lamp = QLabel()
        self._state = QLabel()
        status = QHBoxLayout()
        status.addWidget(self._lamp)
        status.addWidget(self._state, stretch=1)
        self._buttons = QHBoxLayout()

        self._title = QLabel()
        self._title.setProperty("role", "subheading")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.addWidget(self._title)
        layout.addLayout(status)
        layout.addLayout(self._buttons)

        phone.agent_changed.connect(self.refresh)
        phone.calls_changed.connect(self.refresh)        # buttons only between calls
        phone.registration_changed.connect(lambda *_: self.refresh())
        self.refresh()

    def refresh(self):
        # Settings > Queue agent… can turn the box on/off or change the ID.
        # Never show() while parentless: Qt would open the box as its own
        # window, and GNOME then spins a busy pointer for ~15 s waiting for it.
        enabled = self._phone.agent_enabled
        if self.parentWidget() is not None or not enabled:
            self.setVisible(enabled)
        self._title.setText(f"Queue · {self._phone.agent_id}")
        state = self._phone.agent_state
        self._lamp.setPixmap(self._icons[state].pixmap(QSize(LAMP_SIZE, LAMP_SIZE)))
        self._state.setText(state.value)
        while self._buttons.count():
            self._buttons.takeAt(0).widget().deleteLater()
        enabled = self._phone.agent_can_act()
        for label, action in actions_for(state):
            button = QPushButton(label)
            button.setEnabled(enabled)
            if not enabled:
                button.setToolTip("Only between calls")
            elif state is AgentState.UNKNOWN:
                button.setToolTip(UNKNOWN_TIP)
            button.clicked.connect(lambda _=False, a=action: self._phone.agent_action(a))
            self._buttons.addWidget(button)
