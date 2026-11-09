"""The History tab: recent calls, newest first. Double-click to call back."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QHBoxLayout, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from app.history import CallRecord, Direction, Result
from app.ui.theme import LAMP_RED

if TYPE_CHECKING:  # type hint only; keeps this module importable without pjsua2
    from app.services.phone_service import PhoneService

RESULT_TEXT = {
    Result.ANSWERED: "",
    Result.MISSED: "Missed",
    Result.DECLINED: "Declined",
    Result.NOT_ANSWERED: "Not answered",
}
MISSED_COLOR = QColor(LAMP_RED)   # readable on light and dark


def format_when(timestamp: float, now: float | None = None) -> str:
    """'Today 14:03', 'Yesterday 09:15', 'Mon 17:40' (this week), '2026-10-01 08:00'."""
    when = datetime.fromtimestamp(timestamp)
    today = datetime.fromtimestamp(time.time() if now is None else now).date()
    clock = when.strftime("%H:%M")
    if when.date() == today:
        return f"Today {clock}"
    if when.date() == today - timedelta(days=1):
        return f"Yesterday {clock}"
    if today - when.date() < timedelta(days=7):
        return f"{when.strftime('%a')} {clock}"
    return when.strftime("%Y-%m-%d %H:%M")


def format_duration(seconds: float) -> str:
    """65 -> '1:05', 3725 -> '1:02:05'."""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02}:{secs:02}"
    return f"{minutes}:{secs:02}"


def entry_text(record: CallRecord, now: float | None = None) -> str:
    """Two lines: who, then direction / result / time / duration."""
    arrow = "↙ In" if record.direction is Direction.INCOMING else "↗ Out"
    details = [arrow]
    if record.result is Result.ANSWERED:
        details.append(format_duration(record.duration))
    else:
        details.append(RESULT_TEXT[record.result])
    details.append(format_when(record.started_at, now))
    return f"{record.remote}\n" + " · ".join(details)


class HistoryTab(QWidget):
    call_requested = Signal(str)   # number to dial

    def __init__(self, phone: PhoneService, parent=None):
        super().__init__(parent)
        self._phone = phone

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._call_button = QPushButton("Call")
        self._delete_button = QPushButton("Delete")
        clear_button = QPushButton("Clear all")

        buttons = QHBoxLayout()
        buttons.addWidget(self._call_button)
        buttons.addWidget(self._delete_button)
        buttons.addStretch()
        buttons.addWidget(clear_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self._list)
        layout.addLayout(buttons)

        self._list.itemDoubleClicked.connect(lambda _item: self._call_selected())
        self._list.currentItemChanged.connect(lambda *_: self._set_buttons())
        self._call_button.clicked.connect(self._call_selected)
        self._delete_button.clicked.connect(self._delete_selected)
        clear_button.clicked.connect(self._clear)
        phone.call_logged.connect(lambda _record: self.reload())

        self.reload()

    def reload(self):
        self._list.clear()
        now = time.time()
        for record in self._phone.recent_calls():
            item = QListWidgetItem(entry_text(record, now))
            item.setData(Qt.UserRole, record)
            if record.reason:
                item.setToolTip(record.reason)  # e.g. "486 Busy Here"
            if record.result is Result.MISSED:
                item.setForeground(QBrush(MISSED_COLOR))
            self._list.addItem(item)
        self._set_buttons()

    def _selected(self) -> CallRecord | None:
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _set_buttons(self):
        has_selection = self._selected() is not None
        self._call_button.setEnabled(has_selection)
        self._delete_button.setEnabled(has_selection)

    def _call_selected(self):
        record = self._selected()
        if record is not None and record.remote.number:
            self.call_requested.emit(record.remote.number)

    def _delete_selected(self):
        record = self._selected()
        if record is not None:
            self._phone.delete_call(record.id)
            self.reload()

    def _clear(self):
        answer = QMessageBox.question(self, "Clear history", "Delete the whole call history?")
        if answer == QMessageBox.Yes:
            self._phone.clear_history()
            self.reload()
