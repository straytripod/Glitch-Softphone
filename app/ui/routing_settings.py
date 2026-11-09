"""Settings > Call routing…: for each status, ring me / voicemail / forward."""

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QGridLayout, QLabel, QLineEdit, QVBoxLayout,
)

from app import routing
from app.presence import STATUS_NAMES

CHOICES = (("Ring me", routing.RING), ("Voicemail", routing.VOICEMAIL),
           ("Forward to", routing.FORWARD_PREFIX))

INTRO = ("When you change your status, the PBX sends your calls here. It keeps "
         "doing so while this app is closed, and for every phone on your extension.")
CAVEAT = ("Voicemail uses the PBX's Do Not Disturb, which also signs you out of "
          "call-center queues.")


class RoutingDialog(QDialog):
    def __init__(self, rules: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Call routing")
        self._rows: dict[str, tuple[QComboBox, QLineEdit]] = {}

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        for row, status in enumerate(STATUS_NAMES):
            route = routing.route_for(rules, status)
            choice = QComboBox()
            for label, value in CHOICES:
                choice.addItem(label, value)
            number = QLineEdit(routing.forward_number(route))
            number.setPlaceholderText("Number")
            number.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,20}")))
            kind = routing.FORWARD_PREFIX if routing.forward_number(route) else route
            choice.setCurrentIndex(choice.findData(kind))
            choice.currentIndexChanged.connect(self._check)
            number.textChanged.connect(self._check)
            grid.addWidget(QLabel(status), row, 0)
            grid.addWidget(choice, row, 1)
            grid.addWidget(number, row, 2)
            self._rows[status] = (choice, number)

        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        caveat = QLabel(CAVEAT)
        caveat.setWordWrap(True)
        caveat.setProperty("role", "muted")
        self._buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(grid)
        layout.addWidget(caveat)
        layout.addWidget(self._buttons)
        self._check()

    def rules(self) -> dict[str, str]:
        """status -> route; statuses that ring as normal are left out."""
        rules = {}
        for status, (choice, number) in self._rows.items():
            kind = choice.currentData()
            route = routing.forward(number.text()) if kind == routing.FORWARD_PREFIX else kind
            if route != routing.RING:
                rules[status] = route
        return rules

    def _check(self):
        ok = True
        for choice, number in self._rows.values():
            forwarding = choice.currentData() == routing.FORWARD_PREFIX
            number.setEnabled(forwarding)
            if forwarding and not routing.is_valid_forward_number(number.text()):
                ok = False
        self._buttons.button(QDialogButtonBox.Save).setEnabled(ok)
