"""The Contacts tab: one searchable list of colleagues, company contacts and
personal contacts. Double-click to call; right-click for every number."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from app.contacts import PERSONAL, PHONE_LABELS, SOURCE_NAMES, Contact, matches

if TYPE_CHECKING:  # type hint only; keeps this module importable without pjsua2
    from app.services.phone_service import PhoneService


def contact_text(contact: Contact) -> str:
    """Two lines: the name, then company / number / where it comes from."""
    details = [d for d in (contact.company, contact.number) if d]
    more = len(contact.numbers) - 1
    if more > 0:
        details[-1] += f" (+{more})"
    details.append(SOURCE_NAMES[contact.source])
    return f"{contact.name}\n" + " · ".join(details)


def number_text(label: str, number: str) -> str:
    return f"Call {label} {number}" if label else f"Call {number}"


class ContactDialog(QDialog):
    """Add or edit a personal contact."""

    def __init__(self, contact: Contact | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit contact" if contact else "New contact")
        self._id = contact.id if contact else None
        numbers = dict(contact.numbers) if contact else {}
        self._name = QLineEdit(contact.name if contact else "")
        self._company = QLineEdit(contact.company if contact else "")
        self._numbers = {label: QLineEdit(numbers.get(label, "")) for label in PHONE_LABELS}
        form = QFormLayout()
        form.addRow("Name", self._name)
        form.addRow("Company", self._company)
        for label, field in self._numbers.items():
            form.addRow(label.capitalize(), field)
        self._buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._buttons)
        self._name.textChanged.connect(self._check)
        for field in self._numbers.values():
            field.textChanged.connect(self._check)
        self._check()

    def contact(self) -> Contact:
        numbers = tuple((label, field.text().strip()) for label, field in self._numbers.items()
                        if field.text().strip())
        return Contact(self._name.text().strip(), numbers, self._company.text().strip(),
                       PERSONAL, self._id)

    def _check(self):
        contact = self.contact()
        self._buttons.button(QDialogButtonBox.Save).setEnabled(bool(contact.name and contact.numbers))


class ContactsTab(QWidget):
    call_requested = Signal(str)   # number to dial

    def __init__(self, phone: PhoneService, parent=None):
        super().__init__(parent)
        self._phone = phone

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by name, company or number")
        self._search.setClearButtonEnabled(True)
        self._list = QListWidget()
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._call_button = QPushButton("Call")
        self._call_button.setProperty("role", "primary")
        self._edit_button = QPushButton("Edit…")
        self._delete_button = QPushButton("Delete")
        add_button = QPushButton("Add contact…")

        buttons = QHBoxLayout()
        buttons.addWidget(self._call_button)
        buttons.addWidget(self._edit_button)
        buttons.addWidget(self._delete_button)
        buttons.addStretch()
        buttons.addWidget(add_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.addWidget(self._search)
        layout.addWidget(self._list)
        layout.addLayout(buttons)

        self._search.textChanged.connect(lambda _text: self.reload())
        self._search.returnPressed.connect(self._call_selected)
        self._list.itemDoubleClicked.connect(lambda _item: self._call_selected())
        self._list.currentItemChanged.connect(lambda *_: self._set_buttons())
        self._list.customContextMenuRequested.connect(self._context_menu)
        self._call_button.clicked.connect(self._call_selected)
        self._edit_button.clicked.connect(self._edit_selected)
        self._delete_button.clicked.connect(self._delete_selected)
        add_button.clicked.connect(self._add)
        phone.contacts_changed.connect(self.reload)
        self.reload()

    def reload(self):
        query = self._search.text()
        self._list.clear()
        for contact in self._phone.contacts():
            if matches(contact, query):
                item = QListWidgetItem(contact_text(contact))
                item.setData(Qt.UserRole, contact)
                self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)   # Enter in the search box calls the top match
        self._set_buttons()

    def _selected(self) -> Contact | None:
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _set_buttons(self):
        contact = self._selected()
        personal = contact is not None and contact.source == PERSONAL
        self._call_button.setEnabled(contact is not None)
        self._edit_button.setEnabled(personal)
        self._delete_button.setEnabled(personal)

    def _call_selected(self):
        contact = self._selected()
        if contact is not None and contact.number:
            self.call_requested.emit(contact.number)

    def _context_menu(self, pos):
        item = self._list.itemAt(pos)
        if item is None:
            return
        self._list.setCurrentItem(item)
        contact = item.data(Qt.UserRole)
        menu = QMenu(self)
        for label, number in contact.numbers:
            menu.addAction(number_text(label, number),
                           lambda n=number: self.call_requested.emit(n))
        if contact.source == PERSONAL:
            menu.addSeparator()
            menu.addAction("Edit…", self._edit_selected)
            menu.addAction("Delete", self._delete_selected)
        menu.exec(self._list.viewport().mapToGlobal(pos))

    def _add(self):
        # A search that looks like a number is probably the number to save.
        typed = self._search.text().strip()
        looks_like_number = re.fullmatch(r"[\d\s\-().+*#]+", typed) is not None
        start = Contact("", (("work", typed),)) if looks_like_number else None
        dialog = ContactDialog(start, self)
        if start is not None:
            dialog.setWindowTitle("New contact")
        if dialog.exec():
            self._phone.save_contact(dialog.contact())

    def _edit_selected(self):
        contact = self._selected()
        if contact is None or contact.source != PERSONAL:
            return
        dialog = ContactDialog(contact, self)
        if dialog.exec():
            self._phone.save_contact(dialog.contact())

    def _delete_selected(self):
        contact = self._selected()
        if contact is None or contact.source != PERSONAL:
            return
        answer = QMessageBox.question(self, "Delete contact", f"Delete {contact.name}?")
        if answer == QMessageBox.Yes:
            self._phone.delete_contact(contact)
