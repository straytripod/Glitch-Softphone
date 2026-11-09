"""Settings > BLF extensions…: add, name, remove and reorder lamps. Lamps
set on the PBX (FusionPBX Devices -> Keys) are listed above, read-only."""

from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from PySide6.QtCore import Qt

from app.config import BlfEntry

EXTENSION_COL, NAME_COL = 0, 1


def entries_from_rows(rows: list[tuple[str, str]]) -> list[BlfEntry]:
    """Table rows -> entries, dropping blank and repeated extensions."""
    entries, seen = [], set()
    for extension, label in rows:
        extension, label = extension.strip(), label.strip()
        if extension and extension not in seen:
            seen.add(extension)
            entries.append(BlfEntry(extension, label))
    return entries


def pbx_keys_text(entries: list[BlfEntry]) -> str:
    """The read-only line for the PBX's lamps, e.g. "103 Ann, 104"."""
    # A no-break space keeps each "103 Ann" together when the label wraps.
    lamps = ", ".join(f"{e.extension}\u00a0{e.label}".strip() for e in entries)
    return f"From the PBX (Devices → Keys in FusionPBX): {lamps}"


def directory_note(status: str, mac: str) -> str:
    """The hint under the table: where names come from, and what the PBX
    admin needs to know to turn that on."""
    text = "Name: leave it blank to use the name from the PBX's phone directory."
    if status:
        text += f"\nPBX directory: {status}."
    if mac:
        text += f"\nThis computer's MAC address, for its device in FusionPBX: {mac}"
    return text


class BlfEditorDialog(QDialog):
    def __init__(self, entries: list[BlfEntry], note: str = "", parent=None,
                 pbx_entries: list[BlfEntry] = ()):
        super().__init__(parent)
        self.setWindowTitle("BLF extensions")
        self.resize(360, 380)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Extension", "Name"])
        self._table.horizontalHeader().setSectionResizeMode(NAME_COL, QHeaderView.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        for entry in entries:
            self._append(entry.extension, entry.label)

        add = QPushButton("Add")
        remove = QPushButton("Remove")
        up = QPushButton("Up")
        down = QPushButton("Down")
        add.clicked.connect(self._add)
        remove.clicked.connect(self._remove)
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        side = QVBoxLayout()
        for button in (add, remove, up, down):
            side.addWidget(button)
        side.addStretch()

        body = QHBoxLayout()
        body.addWidget(self._table)
        body.addLayout(side)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        if pbx_entries:
            pbx = QLabel(pbx_keys_text(pbx_entries))
            pbx.setWordWrap(True)
            layout.addWidget(pbx)
            layout.addWidget(QLabel("Your own:"))
        layout.addLayout(body)
        if note:
            hint = QLabel(note)
            hint.setWordWrap(True)
            hint.setTextInteractionFlags(Qt.TextSelectableByMouse)   # copy the MAC
            layout.addWidget(hint)
        layout.addWidget(buttons)

    def entries(self) -> list[BlfEntry]:
        rows = []
        for row in range(self._table.rowCount()):
            rows.append((self._cell(row, EXTENSION_COL), self._cell(row, NAME_COL)))
        return entries_from_rows(rows)

    def _cell(self, row: int, col: int) -> str:
        item = self._table.item(row, col)
        return item.text() if item is not None else ""

    def _append(self, extension: str, label: str) -> int:
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, EXTENSION_COL, QTableWidgetItem(extension))
        self._table.setItem(row, NAME_COL, QTableWidgetItem(label))
        return row

    def _add(self):
        row = self._append("", "")
        self._table.setCurrentCell(row, EXTENSION_COL)
        self._table.editItem(self._table.item(row, EXTENSION_COL))  # start typing at once

    def _remove(self):
        row = self._table.currentRow()
        if row >= 0:
            self._table.removeRow(row)

    def _move(self, step: int):
        row = self._table.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self._table.rowCount():
            return
        values = [(self._cell(r, EXTENSION_COL), self._cell(r, NAME_COL)) for r in (row, target)]
        for r, (extension, label) in zip((target, row), values):
            self._table.item(r, EXTENSION_COL).setText(extension)
            self._table.item(r, NAME_COL).setText(label)
        self._table.setCurrentCell(target, self._table.currentColumn())
