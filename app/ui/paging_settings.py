"""Settings > Paging and intercom…: the intercom code and the page groups
on the Page button."""

from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from app.config import PageGroup

NAME_COL, NUMBER_COL = 0, 1

HINT = ("Intercom code: dialed in front of the extension when you click "
        "Intercom. FusionPBX uses *8, so Intercom to 101 dials *8101.\n"
        "Page groups: copy each group's name and extension from FusionPBX "
        "(Apps → Paging). They are listed on the Page button.")


def groups_from_rows(rows: list[tuple[str, str]]) -> list[PageGroup]:
    """Table rows (name, number) -> groups, dropping blank and repeated numbers."""
    groups, seen = [], set()
    for name, number in rows:
        name, number = name.strip(), number.strip()
        if number and number not in seen:
            seen.add(number)
            groups.append(PageGroup(number, name))
    return groups


class PagingDialog(QDialog):
    def __init__(self, intercom_prefix: str, groups: list[PageGroup], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Paging and intercom")
        self.resize(380, 360)

        self._prefix = QLineEdit(intercom_prefix)
        self._prefix.setPlaceholderText("*8")
        form = QFormLayout()
        form.addRow("Intercom code:", self._prefix)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Page group", "Number"])
        self._table.horizontalHeader().setSectionResizeMode(NAME_COL, QHeaderView.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        for group in groups:
            self._append(group.name, group.number)

        add = QPushButton("Add")
        remove = QPushButton("Remove")
        add.clicked.connect(self._add)
        remove.clicked.connect(self._remove)
        side = QVBoxLayout()
        side.addWidget(add)
        side.addWidget(remove)
        side.addStretch()
        body = QHBoxLayout()
        body.addWidget(self._table)
        body.addLayout(side)

        hint = QLabel(HINT)
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(body)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def intercom_prefix(self) -> str:
        return self._prefix.text().strip()

    def groups(self) -> list[PageGroup]:
        rows = [(self._cell(row, NAME_COL), self._cell(row, NUMBER_COL))
                for row in range(self._table.rowCount())]
        return groups_from_rows(rows)

    def _cell(self, row: int, col: int) -> str:
        item = self._table.item(row, col)
        return item.text() if item is not None else ""

    def _append(self, name: str, number: str) -> int:
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, NAME_COL, QTableWidgetItem(name))
        self._table.setItem(row, NUMBER_COL, QTableWidgetItem(number))
        return row

    def _add(self):
        row = self._append("", "")
        self._table.setCurrentCell(row, NAME_COL)
        self._table.editItem(self._table.item(row, NAME_COL))

    def _remove(self):
        row = self._table.currentRow()
        if row >= 0:
            self._table.removeRow(row)
