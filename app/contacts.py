"""Contacts: colleagues, the company directory, and my own address book.

No Qt or PJSIP here. Three sources make one list:
  colleague  every extension, from the PBX directory (app/directory.py)
  company    the PBX's Contacts app (FusionPBX Apps > Contacts), read-only
  personal   my own contacts, kept in a small SQLite file on this computer
"""

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.paths import DATA_DIR

DEFAULT_CONTACTS_PATH = DATA_DIR / "contacts.db"
COLLEAGUE, COMPANY, PERSONAL = "colleague", "company", "personal"
SOURCE_NAMES = {COLLEAGUE: "Colleague", COMPANY: "Company directory", PERSONAL: "Personal"}
PHONE_LABELS = ("work", "mobile", "home")   # a personal contact's numbers


@dataclass(frozen=True)
class Contact:
    name: str
    numbers: tuple[tuple[str, str], ...] = ()   # (label, number); label may be ""
    company: str = ""
    source: str = PERSONAL
    id: int | None = None                       # database row of a personal contact

    @property
    def number(self) -> str:
        """The number a plain click calls."""
        return self.numbers[0][1] if self.numbers else ""


def digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def matches(contact: Contact, query: str) -> bool:
    """Search: every word must appear in the name or company, or the digits
    typed must appear in one of the numbers ("555 12" finds 555-1234)."""
    query = query.strip()
    if not query:
        return True
    text = f"{contact.name} {contact.company}".casefold()
    if all(word in text for word in query.casefold().split()):
        return True
    wanted = digits(query)
    return bool(wanted) and wanted == re.sub(r"[\s\-().+]", "", query) and any(
        wanted in digits(number) for _, number in contact.numbers)


def company_contacts(entries: list[tuple[str, list[str]]], extensions: set[str]) -> list[Contact]:
    """The PBX directory with contacts=all lists extensions too; keep the rest."""
    contacts = []
    for entry_name, numbers in entries:
        if set(numbers) <= extensions:
            continue
        name, company = split_company(entry_name)
        contacts.append(Contact(name, tuple(("", n) for n in numbers), company, COMPANY))
    return contacts


def split_company(entry_name: str) -> tuple[str, str]:
    """FusionPBX's phone template names a contact with both an organization
    and a person "Acme Supply, Dana Lee"; it only adds the comma in that case.
    -> ("Dana Lee", "Acme Supply")."""
    company, comma, person = entry_name.partition(", ")
    if comma and company and person:
        return person, company
    return entry_name, ""


def colleagues(names: dict[str, str]) -> list[Contact]:
    return [Contact(name, (("", number),), source=COLLEAGUE) for number, name in names.items()]


def sort_contacts(contacts: list[Contact]) -> list[Contact]:
    return sorted(contacts, key=lambda c: (c.name.casefold(), c.source))


def name_for(contacts: list[Contact], number: str) -> str:
    """The contact name for a caller's number, or "". Matches on digits, so
    "+1 (555) 123-4567" finds a contact saved as 5551234567 (and the other
    way round, by the last 10 digits)."""
    wanted = digits(number)
    if not wanted:
        return ""
    for contact in contacts:
        for _, saved in contact.numbers:
            have = digits(saved)
            if have and (have == wanted or (len(have) >= 10 and have[-10:] == wanted[-10:])):
                return contact.name
    return ""


class ContactBook:
    """My personal contacts on disk (SQLite, like the call history)."""

    def __init__(self, path: Path = DEFAULT_CONTACTS_PATH):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id      INTEGER PRIMARY KEY,
                name    TEXT NOT NULL,
                company TEXT NOT NULL,
                work    TEXT NOT NULL,
                mobile  TEXT NOT NULL,
                home    TEXT NOT NULL
            )""")
        self._db.commit()

    def all(self) -> list[Contact]:
        rows = self._db.execute("SELECT id, name, company, work, mobile, home FROM contacts")
        return [self._contact(*row) for row in rows]

    def save(self, contact: Contact) -> Contact:
        """Add a contact (id None) or update it. Numbers are stored by label."""
        by_label = {label: number for label, number in contact.numbers}
        values = (contact.name, contact.company, *(by_label.get(l, "") for l in PHONE_LABELS))
        with self._db:
            if contact.id is None:
                cursor = self._db.execute(
                    "INSERT INTO contacts (name, company, work, mobile, home) VALUES (?, ?, ?, ?, ?)",
                    values)
                row_id = cursor.lastrowid
            else:
                self._db.execute(
                    "UPDATE contacts SET name=?, company=?, work=?, mobile=?, home=? WHERE id=?",
                    (*values, contact.id))
                row_id = contact.id
        return self._contact(row_id, *values)

    def delete(self, contact_id: int):
        with self._db:
            self._db.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))

    def close(self):
        self._db.close()

    @staticmethod
    def _contact(row_id, name, company, *numbers) -> Contact:
        pairs = tuple((label, n) for label, n in zip(PHONE_LABELS, numbers) if n)
        return Contact(name, pairs, company, PERSONAL, row_id)
