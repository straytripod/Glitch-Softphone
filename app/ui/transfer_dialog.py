"""Ask where to transfer a call, and whether to talk to them first."""

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

BLIND = "blind"       # hand the call over straight away
CONSULT = "consult"   # hold the caller and call the target first


class TransferDialog(QDialog):
    def __init__(self, remote: str, can_consult: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transfer")
        self.choice = None

        self._number = QLineEdit()
        self._number.setPlaceholderText("Number or extension")
        now = QPushButton("Transfer now")
        now.setToolTip("Send the call straight to this number")
        first = QPushButton("Talk first")
        first.setToolTip("Put the caller on hold and call this number,\n"
                         "then press Complete Transfer when ready")
        first.setEnabled(can_consult)
        # Enter = the safe choice: nothing leaves your hands until you confirm.
        first.setDefault(can_consult)
        cancel = QPushButton("Cancel")

        buttons = QHBoxLayout()
        buttons.addWidget(now)
        buttons.addWidget(first)
        buttons.addStretch()
        buttons.addWidget(cancel)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Transfer {remote} to:"))
        layout.addWidget(self._number)
        layout.addLayout(buttons)

        now.clicked.connect(lambda: self._choose(BLIND))
        first.clicked.connect(lambda: self._choose(CONSULT))
        cancel.clicked.connect(self.reject)

    @property
    def number(self) -> str:
        return self._number.text().strip()

    def _choose(self, choice: str):
        if self.number:
            self.choice = choice
            self.accept()
