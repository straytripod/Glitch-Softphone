"""The main application window."""

import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, QStringListModel, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QComboBox, QCompleter, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QApplication, QMainWindow, QMenu, QMessageBox, QPushButton, QStatusBar, QSystemTrayIcon,
    QTabWidget, QVBoxLayout, QWidget,
)

from app import APP_ID, APP_NAME
from app.config import AppConfig
from app.history import Result
from app.presence import STATUS_NAMES
from app.services.phone_service import CallView, PhoneService
from app.sip.calls import DTMF_DIGITS, CallState, MediaState
from app.sip.intercom import AutoKind
from app.sip.registration import RegState
from app.ui.agent_panel import AgentPanel
from app.ui.account_settings import AccountDialog
from app.ui.agent_settings import AgentSettingsDialog
from app.ui.audio_settings import AudioSettingsDialog
from app.ui.blf_editor import BlfEditorDialog, directory_note
from app.ui.blf_panel import LAMP_SIZE, BlfPanel, _lamp_icon
from app.ui.contacts_tab import ContactsTab
from app.ui.history_tab import HistoryTab
from app.ui.paging_settings import PagingDialog
from app.ui.notifier import URGENCY_CRITICAL, Notifier
from app.ui.park_panel import ParkPanel
from app.ui.routing_settings import RoutingDialog
from app.ui.theme import LAMP_GREY, LAMP_RED, THEMES, apply_theme
from app.ui.transfer_dialog import BLIND, CONSULT, TransferDialog
from app.ui.tray import Tray, app_icon, save_icon_png

KEYPAD_ROWS = ("123", "456", "789", "*0#")
THEME_NAMES = {"system": "Match &system", "light": "&Light", "dark": "&Dark"}


def styled(widget, role: str):
    """Give a widget a stylesheet role (see app/ui/theme.py); returns it."""
    widget.setProperty("role", role)
    return widget


def card(*widgets) -> QFrame:
    """A rounded panel holding the given widgets, stacked."""
    frame = styled(QFrame(), "card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    for widget in widgets:
        layout.addWidget(widget)
    return frame


LOCK = "\N{LOCK} "   # in front of a call whose audio is encrypted (SRTP)


def header_text(account) -> tuple[str, str]:
    """(title, subtitle) for the top of the window."""
    if not (account.server and account.extension):
        return APP_NAME, "No account yet: Settings → Account…"
    return (account.display_name or f"Extension {account.extension}",
            f"Extension {account.extension} · {account.server}")


def voicemail_text(mailbox) -> str:
    """The Voicemail button's label."""
    return f"Voicemail · {mailbox.new} new" if mailbox.waiting else "Voicemail"


def call_status_text(call: CallView) -> str:
    """One line describing a call, e.g. "On hold: Bob (102)", with a lock in
    front once its audio is encrypted."""
    text = _call_text(call)
    return LOCK + text if call.encrypted and call.state is CallState.ACTIVE else text


def _call_text(call: CallView) -> str:
    remote = call.remote
    if call.transfer_text:
        return f"{remote}: {call.transfer_text}"
    if call.state is CallState.DIALING:
        return f"Calling {remote}…"
    if call.state is CallState.RINGING_OUT:
        return f"Ringing {remote}…"
    if call.state is CallState.RINGING_IN and call.auto_kind is AutoKind.PAGE:
        return f"Page from {remote}…"
    if call.state is CallState.RINGING_IN and call.auto_kind is AutoKind.INTERCOM:
        return f"Intercom from {remote}…"
    if call.state is CallState.RINGING_IN:
        return f"Incoming call from {remote}"
    if call.state is CallState.CONNECTING:
        return f"Connecting to {remote}…"
    if call.on_hold:
        return f"On hold: {remote}"
    if call.media is MediaState.REMOTE_HOLD:
        return f"{remote} put you on hold"
    if call.auto_kind is AutoKind.PAGE:
        return f"Page from {remote} (listen only)"
    if call.auto_kind is AutoKind.INTERCOM:
        return f"Intercom: {remote}"
    text = f"Connected to {remote}"
    return f"{text} (muted)" if call.muted else text


TRUST_DETAILS = (
    "For the phone system administrator: this is the SHA-256 fingerprint of "
    "the PBX web server's certificate. On FusionPBX, compare it with:\n\n"
    "openssl x509 -in /etc/ssl/certs/nginx.crt -noout -fingerprint -sha256"
)


def trust_question(server: str, fingerprint: str, changed: bool) -> str:
    """The "trust this phone system?" question, in plain words."""
    if changed:
        text = (f"<b>The security certificate of your phone system ({server}) has changed.</b>"
                "<br><br>That's expected if your administrator recently updated the "
                "phone system. If not, choose <b>Not now</b> and tell your administrator: "
                "something may be pretending to be your phone system.")
    else:
        text = (f"{APP_NAME} loads your colleagues' names from your phone system ({server})."
                "<br><br>The phone system uses its own security certificate, which this "
                "computer can't check by itself. That's normal for many office phone "
                "systems. If you're unsure, ask your administrator to confirm this code:")
    return (f"{text}<br><br><tt>{fingerprint}</tt><br><br>"
            "Calls work either way; this only affects the names.")


SUGGESTION_SEP = "  ·  "


def suggestion_text(name: str, number: str) -> str:
    """One dial-by-name suggestion, e.g. "Dana Lee  ·  5551234"."""
    return f"{name}{SUGGESTION_SEP}{number}"


def suggestion_number(text: str) -> str:
    return text.rpartition(SUGGESTION_SEP)[2]


def ended_text(remote, reason: str) -> str:
    return f"Call with {remote} ended ({reason})" if reason else f"Call with {remote} ended"


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig, phone: PhoneService):
        super().__init__()
        self._phone = phone
        self._selected_id = None   # the call the buttons act on
        self._last_ended = ""      # shown when no calls are left
        self._consults = {}        # consult call id -> id of the caller waiting on hold
        self._unseen_missed = 0
        self._ringing_notes = {}   # notification id -> incoming call id
        self._missed_notes = set() # notification ids for missed calls
        self._quitting = False
        self._tray_hint_shown = False
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(720, 640)


        self._number = styled(QLineEdit(), "dial")
        self._number.setPlaceholderText("Name or number")
        # Dial by name: typing part of a name suggests matching contacts.
        # Only once the text has a letter: a dropdown over plain digits
        # adds nothing, and it would swallow the next keypad click.
        self._all_suggestions = []
        self._suggestions = QStringListModel(self)
        completer = QCompleter(self._suggestions, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.activated.connect(self._suggestion_chosen)
        self._number.setCompleter(completer)
        self._number.textChanged.connect(self._filter_suggestions)
        self._number.installEventFilter(self)   # keypad beeps for typed digits
        self._call_button = styled(QPushButton("Call"), "primary")
        self._intercom_button = QPushButton("Intercom")
        self._intercom_button.setToolTip("Intercom: the extension answers by itself")
        # Page: a menu of page groups, filled each time it opens.
        self._page_button = QPushButton("Page")
        self._page_button.setToolTip("Announce to a page group")
        self._page_menu = QMenu(self._page_button)
        self._page_menu.aboutToShow.connect(self._fill_page_menu)
        self._page_button.setMenu(self._page_menu)
        self._call_list = QListWidget()   # every current call; the selected one is acted on
        self._call_list.setMaximumHeight(110)
        self._call_status = styled(QLabel(""), "call")
        self._call_status.setWordWrap(True)
        self._answer_button = styled(QPushButton("Answer"), "success")
        self._hangup_button = styled(QPushButton("Hang up"), "danger")
        self._hold_button = QPushButton("Hold")
        self._mute_button = QPushButton("Mute")
        self._mute_button.setCheckable(True)
        self._transfer_button = QPushButton("Transfer")  # becomes "Complete Transfer"
        self._park_button = QPushButton("Park")
        self._park_button.setToolTip("Put the call in a park slot; anyone can pick it up from there")

        dial_row = QHBoxLayout()
        dial_row.addWidget(self._number)
        dial_row.addWidget(self._call_button)
        dial_row.addWidget(self._intercom_button)
        dial_row.addWidget(self._page_button)
        # One size for the three, as tall as the number box: the styles differ
        # (Call has no border, Page has a menu arrow) and would size them apart.
        height = self._number.sizeHint().height()
        # Snug around the longest label, leaving the number box room to read.
        width = self._intercom_button.fontMetrics().horizontalAdvance("Intercom") + 20
        for button in (self._call_button, self._intercom_button, self._page_button):
            button.setStyleSheet("padding: 7px 8px;")   # the theme's 14px sides don't fit
            button.setFixedSize(width, height)
        call_row = QHBoxLayout()
        call_row.addWidget(self._answer_button)
        call_row.addWidget(self._hangup_button)
        in_call_row = QHBoxLayout()
        in_call_row.addWidget(self._hold_button)
        in_call_row.addWidget(self._mute_button)
        in_call_row.addWidget(self._transfer_button)
        in_call_row.addWidget(self._park_button)

        keypad = QGridLayout()
        keypad.setSpacing(10)
        for row, keys in enumerate(KEYPAD_ROWS):
            for col, key in enumerate(keys):
                button = styled(QPushButton(key), "key")
                button.setFocusPolicy(Qt.NoFocus)  # keep typing focus in the number box
                button.clicked.connect(lambda _=False, k=key: self._keypad_pressed(k))
                keypad.addWidget(button, row, col)

        # My presence status, top left as in Allworx Interact.
        self._my_status = QComboBox()
        self._my_status.addItems(STATUS_NAMES)
        self._my_status.setCurrentText(phone.my_status)
        self._my_status.setToolTip("Your status, as colleagues see it")
        self._my_status.currentTextChanged.connect(self._status_chosen)
        names = QVBoxLayout()
        names.setSpacing(0)
        self._title = styled(QLabel(""), "heading")
        self._subtitle = styled(QLabel(""), "muted")
        names.addWidget(self._title)
        names.addWidget(self._subtitle)
        # Where calls go for this status (Settings > Call routing…).
        self._routing = styled(QLabel(""), "muted")
        self._routing.setAlignment(Qt.AlignRight)
        # Voicemail lamp: red while new messages wait; click to listen (*97).
        self._voicemail = QPushButton("Voicemail")
        self._voicemail.setIconSize(QSize(LAMP_SIZE, LAMP_SIZE))
        self._voicemail_icons = {True: _lamp_icon(QColor(LAMP_RED)),
                                 False: _lamp_icon(QColor(LAMP_GREY))}
        status_row = QHBoxLayout()
        status_row.addWidget(self._voicemail)
        status_row.addSpacing(8)
        status_row.addWidget(styled(QLabel("Status"), "muted"))
        status_row.addWidget(self._my_status)
        status_column = QVBoxLayout()
        status_column.setSpacing(4)
        status_column.addLayout(status_row)
        status_column.addWidget(self._routing)
        top_row = QHBoxLayout()
        top_row.addLayout(names)
        top_row.addStretch()
        top_row.addLayout(status_column)

        phone_page = QWidget()
        layout = QVBoxLayout(phone_page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)
        layout.addLayout(dial_row)
        layout.addWidget(self._call_list)
        layout.addWidget(self._call_status)
        layout.addLayout(call_row)
        layout.addLayout(in_call_row)
        layout.addSpacing(6)
        layout.addLayout(keypad)
        layout.addStretch()

        self._history_tab = HistoryTab(phone)
        self._contacts_tab = ContactsTab(phone)
        self._tabs = QTabWidget()
        self._tabs.addTab(phone_page, "Phone")
        self._tabs.addTab(self._contacts_tab, "Contacts")
        self._tabs.addTab(self._history_tab, "History")
        self._agent_panel = AgentPanel(phone, self)   # parent: see AgentPanel.refresh
        self._blf_panel = BlfPanel(phone)
        self._park_panel = ParkPanel(phone, self)   # parent: see AgentPanel.refresh
        side = QVBoxLayout()
        side.setSpacing(12)
        side.addWidget(self._agent_panel)
        side.addWidget(self._park_panel)
        side.addWidget(card(self._blf_panel), stretch=1)

        main_column = QVBoxLayout()
        main_column.setSpacing(14)
        main_column.addLayout(top_row)
        main_column.addWidget(self._tabs, stretch=1)
        central = QWidget()
        columns = QHBoxLayout(central)
        columns.setContentsMargins(20, 16, 20, 12)
        columns.setSpacing(20)
        columns.addLayout(main_column, stretch=1)
        columns.addLayout(side)
        self.setCentralWidget(central)

        self._status = QStatusBar()
        self.setStatusBar(self._status)

        phone_menu = self.menuBar().addMenu("&Phone")
        quit_action = phone_menu.addAction("&Quit", self._quit)
        quit_action.setShortcut("Ctrl+Q")
        settings_menu = self.menuBar().addMenu("&Settings")
        settings_menu.addAction("A&ccount…", self.open_account_settings)
        settings_menu.addAction("&Audio and ringtone…", self._open_audio_settings)
        settings_menu.addAction("&BLF extensions…", self._open_blf_editor)
        settings_menu.addAction("&Queue agent…", self._open_agent_settings)
        settings_menu.addAction("Call &routing…", self._open_routing_settings)
        settings_menu.addAction("&Paging and intercom…", self._open_paging_settings)
        waiting = QAction("Call &waiting", self, checkable=True, checked=phone.call_waiting)
        waiting.toggled.connect(self._set_call_waiting)
        settings_menu.addAction(waiting)
        auto = QAction("Auto-answer &intercom/paging", self, checkable=True,
                       checked=phone.auto_answer)
        auto.toggled.connect(self._set_auto_answer)
        settings_menu.addAction(auto)
        self._setup_desktop(settings_menu)
        self._setup_appearance(settings_menu)

        self._call_button.clicked.connect(self._dial)
        self._intercom_button.clicked.connect(lambda: self._intercom(self._number.text()))
        self._number.returnPressed.connect(self._dial)
        self._call_list.currentItemChanged.connect(self._selection_changed)
        self._answer_button.clicked.connect(lambda: self._act(phone.answer))
        self._hangup_button.clicked.connect(lambda: self._act(phone.hangup))
        self._hold_button.clicked.connect(lambda: self._act(phone.toggle_hold))
        self._mute_button.toggled.connect(lambda muted: self._act(phone.set_mute, muted))
        self._transfer_button.clicked.connect(self._transfer_clicked)
        self._park_button.clicked.connect(lambda: self._act(phone.park))
        self._park_panel.retrieve_requested.connect(self._retrieve)
        phone.registration_changed.connect(self._show_registration)
        phone.calls_changed.connect(self._refresh)
        phone.incoming_call.connect(self._show_incoming)
        phone.call_ended.connect(self._show_ended)
        phone.call_logged.connect(self._count_missed)
        phone.web_cert_untrusted.connect(self._ask_trust)
        phone.routing_changed.connect(self._show_routing)
        phone.account_changed.connect(self._show_account)
        self._show_account()
        self._voicemail.clicked.connect(self._check_voicemail)
        phone.voicemail_changed.connect(self._show_voicemail)
        self._voicemail_new = 0      # new messages already announced
        self._voicemail_new_known = False
        self._show_voicemail()
        phone.registration_changed.connect(lambda *_: self._show_routing())
        self._show_routing()
        self._history_tab.call_requested.connect(self._call_back)
        self._contacts_tab.call_requested.connect(self._call_back)
        phone.contacts_changed.connect(self._update_suggestions)
        self._update_suggestions()
        self._blf_panel.call_requested.connect(self._lamp_clicked)
        self._blf_panel.intercom_requested.connect(self._intercom)
        self._blf_panel.edit_requested.connect(self._open_blf_editor)
        self._tabs.currentChanged.connect(self._tab_changed)

        self._show_registration(phone.reg_state, "")
        self._refresh()
        self._number.setFocus()   # ready to type a number

    # --- the selected call ----------------------------------------------------

    def _selected(self) -> CallView | None:
        return next((c for c in self._phone.calls() if c.call_id == self._selected_id), None)

    def _select(self, call_id: int | None):
        self._selected_id = call_id
        self._refresh()

    def _act(self, action, *args):
        """Run a PhoneService action on the selected call."""
        if self._selected_id is not None:
            action(self._selected_id, *args)

    def _selection_changed(self, item, _previous):
        # Not _refresh(): rebuilding the list inside its own signal would
        # delete the item Qt is still telling us about.
        if item is not None:
            self._selected_id = item.data(Qt.UserRole)
            self._show_selected()

    # --- actions ---------------------------------------------------------------

    def _dial(self):
        call_id = self._phone.dial(self._number.text())
        if call_id is not None:
            self._select(call_id)

    def _keypad_pressed(self, key: str):
        """On a live call the keypad sends DTMF; otherwise it types the number."""
        call = self._selected()
        if call is not None and call.live:
            self._phone.send_dtmf(call.call_id, key)
        elif self._number.isEnabled():
            self._number.insert(key)
            self._phone.play_key_tone(key)

    def eventFilter(self, watched, event):
        if (watched is self._number and event.type() == QEvent.KeyPress
                and not event.isAutoRepeat() and event.text() and event.text() in DTMF_DIGITS):
            self._phone.play_key_tone(event.text())
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        # The number box is disabled on a live call, so digit keys land here.
        call = self._selected()
        if call is not None and call.live and event.text() and event.text() in DTMF_DIGITS:
            self._phone.send_dtmf(call.call_id, event.text())
        else:
            super().keyPressEvent(event)

    def _pending_transfer(self) -> tuple[int, int] | None:
        """(caller, target) when the selected call is one half of a consult
        that is ready to complete: both calls still connected."""
        calls = {c.call_id: c for c in self._phone.calls()}
        for target_id, caller_id in self._consults.items():
            if self._selected_id not in (target_id, caller_id):
                continue
            caller, target = calls.get(caller_id), calls.get(target_id)
            if (caller and target and caller.state is CallState.ACTIVE
                    and target.state is CallState.ACTIVE
                    and not caller.transferring and not target.transferring):
                return caller_id, target_id
        return None

    def _transfer_clicked(self):
        pending = self._pending_transfer()
        if pending is not None:
            self._phone.connect_calls(*pending)   # Complete Transfer
            return
        call = self._selected()
        if call is None:
            return
        dialog = TransferDialog(str(call.remote), self._phone.can_dial(), self)
        if not dialog.exec():
            return
        if dialog.choice == BLIND:
            self._phone.transfer(call.call_id, dialog.number)
        elif dialog.choice == CONSULT:
            self._consult(call.call_id, dialog.number)

    def _consult(self, caller_id: int, number: str):
        """Attended transfer, step 1: hold the caller and call the target.
        Step 2 is Complete Transfer, once they've agreed."""
        target_id = self._phone.dial(number)  # dial() holds the caller
        if target_id is not None:
            self._consults[target_id] = caller_id
            self._select(target_id)

    def _update_suggestions(self):
        self._all_suggestions = [suggestion_text(c.name, number)
                                 for c in self._phone.contacts()
                                 for _, number in c.numbers]
        self._filter_suggestions(self._number.text())

    def _filter_suggestions(self, text: str):
        wanted = self._all_suggestions if any(ch.isalpha() for ch in text) else []
        if self._suggestions.stringList() != wanted:
            self._suggestions.setStringList(wanted)

    def _suggestion_chosen(self, text: str):
        # After the completer finishes: it writes the whole suggestion into
        # the box itself, which would overwrite the number.
        number = suggestion_number(text)
        QTimer.singleShot(0, self, lambda: self._number.setText(number))   # ready to call

    def _call_back(self, number: str):
        """Call from the History or Contacts tab."""
        if not self._phone.can_dial():
            return
        self._tabs.setCurrentIndex(0)
        self._number.setText(number)
        self._dial()

    def _retrieve(self, slot: str):
        call_id = self._phone.retrieve(slot)
        if call_id is not None:
            self._tabs.setCurrentIndex(0)
            self._select(call_id)

    def _intercom(self, number: str):
        self._show_new_call(self._phone.intercom(number))

    def _fill_page_menu(self):
        self._page_menu.clear()
        for group in self._phone.page_groups:
            label = f"{group.name}  ({group.number})" if group.name else group.number
            self._page_menu.addAction(label, lambda g=group: self._show_new_call(self._phone.page(g)))
        if self._phone.page_groups:
            self._page_menu.addSeparator()
        self._page_menu.addAction("Set up page groups…", self._open_paging_settings)

    def _show_new_call(self, call_id: int | None):
        if call_id is not None:
            self._tabs.setCurrentIndex(0)
            self._select(call_id)

    def _open_paging_settings(self):
        dialog = PagingDialog(self._phone.intercom_prefix, self._phone.page_groups, self)
        if not dialog.exec():
            return
        try:
            self._phone.set_paging(dialog.intercom_prefix(), dialog.groups())
        except OSError as e:
            QMessageBox.warning(self, "Paging and intercom",
                                f"The settings apply, but saving them failed:\n{e}")

    def _lamp_clicked(self, extension: str):
        """Call the extension, or pick up the call ringing there."""
        call_id = self._phone.call_or_pickup(extension)
        if call_id is not None:
            self._tabs.setCurrentIndex(0)
            self._select(call_id)

    def _open_blf_editor(self):
        note = directory_note(self._phone.directory_status, self._phone.device_mac)
        dialog = BlfEditorDialog(self._phone.own_blf_entries(), note, self,
                                 pbx_entries=self._phone.pbx_blf_entries())
        if not dialog.exec():
            return
        try:
            self._phone.set_blf_entries(dialog.entries())
        except OSError as e:
            QMessageBox.warning(self, "BLF extensions",
                                f"The new list is in use, but saving it failed:\n{e}")

    def _ask_trust(self, fingerprint: str, changed: bool):
        box = QMessageBox(QMessageBox.Warning if changed else QMessageBox.Question,
                          "Trust your phone system?",
                          trust_question(self._phone.account_config.server, fingerprint, changed), parent=self)
        box.setDetailedText(f"{fingerprint}\n\n{TRUST_DETAILS}")
        trust = box.addButton("Trust", QMessageBox.AcceptRole)
        box.setDefaultButton(box.addButton("Not now", QMessageBox.RejectRole))
        box.exec()
        if box.clickedButton() is not trust:
            self._phone.decline_web_cert(fingerprint)
            return
        try:
            self._phone.trust_web_cert(fingerprint)
        except OSError as e:
            QMessageBox.warning(self, "PBX certificate",
                                f"Trusted for now, but saving that failed:\n{e}")

    def _open_agent_settings(self):
        dialog = AgentSettingsDialog(self._phone.agent_config, self._phone.account_config.extension, self)
        if not dialog.exec():
            return
        try:
            self._phone.set_agent_config(dialog.agent_config())
        except OSError as e:
            QMessageBox.warning(self, "Queue agent",
                                f"The settings are in use, but saving them failed:\n{e}")

    def open_account_settings(self):
        if not self._phone.can_change_account():
            QMessageBox.information(self, "Account", "Finish your call first: changing the "
                                    "account disconnects this phone from the PBX for a moment.")
            return
        dialog = AccountDialog(self._phone.account_config, self)
        if not dialog.exec():
            return
        try:
            self._phone.set_account(dialog.account())
        except OSError as e:
            QMessageBox.warning(self, "Account", f"The account is in use, but saving it failed:\n{e}")

    def _show_account(self):
        title, subtitle = header_text(self._phone.account_config)
        self._title.setText(title)
        self._subtitle.setText(subtitle)

    def _open_routing_settings(self):
        dialog = RoutingDialog(self._phone.routing_rules(), self)
        if not dialog.exec():
            return
        try:
            self._phone.set_routing_rules(dialog.rules())
        except OSError as e:
            QMessageBox.warning(self, "Call routing",
                                f"The routing applies, but saving it failed:\n{e}")

    def _check_voicemail(self):
        self._phone.check_voicemail()
        self._tabs.setCurrentIndex(0)   # the Phone tab, with the call and keypad

    def _show_voicemail(self):
        mailbox = self._phone.mailbox
        self._voicemail.setText(voicemail_text(mailbox))
        self._voicemail.setIcon(self._voicemail_icons[mailbox.waiting] if mailbox.known else QIcon())
        self._voicemail.setToolTip(
            f"{mailbox.new} new, {mailbox.old} saved. Click to listen." if mailbox.known
            else "Call your voicemail")
        if mailbox.new > self._voicemail_new and self._voicemail_new_known:
            plural = "s" if mailbox.new != 1 else ""
            self._notify(
                "New voicemail", f"{mailbox.new} new message{plural}", [("voicemail", "Listen")])
        self._voicemail_new = mailbox.new
        # The first report (at start-up) isn't news; later increases are.
        self._voicemail_new_known = mailbox.known

    def _show_routing(self):
        text = self._phone.routing_text
        self._routing.setText(text)
        self._routing.setVisible(bool(text))

    def _open_audio_settings(self):
        AudioSettingsDialog(self._phone, self).exec()

    def _status_chosen(self, status: str):
        try:
            self._phone.set_my_status(status)
        except OSError as e:
            QMessageBox.warning(self, "Status", f"The status applies, but saving it failed:\n{e}")

    def _set_call_waiting(self, enabled: bool):
        try:
            self._phone.set_call_waiting(enabled)
        except OSError as e:
            QMessageBox.warning(self, "Call waiting", f"The setting applies, but saving it failed:\n{e}")

    # --- tray and notifications -------------------------------------------------

    def _setup_desktop(self, settings_menu):
        """Tray icon and desktop notifications. Both stay off when Qt runs
        offscreen (unit tests, test scripts), so nothing pops up there."""
        real_desktop = QGuiApplication.platformName() != "offscreen"
        icon_dir = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / APP_ID
        self._notifier = None
        if real_desktop:
            self._notifier = Notifier(APP_NAME, save_icon_png(icon_dir / "icon.png"), self)
            self._notifier.action_invoked.connect(self._notification_action)
        self._tray = None
        if real_desktop and QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = Tray(self)
            self._tray.show_requested.connect(self._show_window)
            self._tray.quit_requested.connect(self._quit)
            self._tray.show()
            # Closing the window mustn't end the app while the tray keeps it.
            QApplication.instance().setQuitOnLastWindowClosed(False)

        ui = self._phone.ui_config
        keep = QAction("Keep running in &tray when closed", self, checkable=True,
                       checked=ui.close_to_tray)
        keep.setEnabled(self._tray is not None)
        keep.toggled.connect(lambda on: self._set_ui_option("close_to_tray", on))
        settings_menu.addAction(keep)
        notes = QAction("Desktop &notifications", self, checkable=True, checked=ui.notifications)
        notes.setEnabled(self._notifier is not None and self._notifier.available)
        notes.toggled.connect(lambda on: self._set_ui_option("notifications", on))
        settings_menu.addAction(notes)

    def _setup_appearance(self, settings_menu):
        menu = settings_menu.addMenu("A&ppearance")
        group = QActionGroup(self)   # exclusive: one theme at a time
        for theme in THEMES:
            action = QAction(THEME_NAMES[theme], self, checkable=True,
                             checked=self._phone.ui_config.theme == theme)
            action.triggered.connect(lambda _=False, t=theme: self._set_theme(t))
            group.addAction(action)
            menu.addAction(action)
        # Follow the desktop switching light/dark while we run.
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._desktop_scheme_changed)

    def _set_theme(self, theme: str):
        apply_theme(QApplication.instance(), theme)
        self._set_ui_option("theme", theme)

    def _desktop_scheme_changed(self, _scheme):
        if self._phone.ui_config.theme == "system":
            apply_theme(QApplication.instance(), "system")

    def _set_ui_option(self, name: str, value):
        try:
            self._phone.set_ui_option(name, value)
        except OSError as e:
            QMessageBox.warning(self, "Settings", f"The setting applies, but saving it failed:\n{e}")

    def _notify(self, summary: str, body: str, actions=(), urgency=1, timeout_ms=-1):
        if self._notifier is None or not self._phone.ui_config.notifications:
            return None
        return self._notifier.notify(summary, body, actions, urgency, timeout_ms)

    def _notification_action(self, notification_id: int, action: str):
        call_id = self._ringing_notes.pop(notification_id, None)
        if call_id is not None and action == "answer":
            self._select(call_id)
            self._phone.answer(call_id)
        elif call_id is not None and action == "decline":
            self._phone.hangup(call_id)
        elif action == "voicemail":
            self._show_window()
            self._check_voicemail()
        elif notification_id in self._missed_notes:
            self._missed_notes.discard(notification_id)
            self._show_window()
            self._tabs.setCurrentWidget(self._history_tab)
        elif action == "default":
            self._show_window()

    def _close_stale_notifications(self):
        """Take down "incoming call" notifications once the call stops ringing."""
        ringing = {c.call_id for c in self._phone.calls() if c.state is CallState.RINGING_IN}
        for notification_id, call_id in list(self._ringing_notes.items()):
            if call_id not in ringing:
                del self._ringing_notes[notification_id]
                if self._notifier is not None:
                    self._notifier.close(notification_id)

    def _show_window(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def _quit(self):
        self._quitting = True
        QApplication.instance().quit()

    def closeEvent(self, event):
        if self._tray is not None and self._phone.ui_config.close_to_tray and not self._quitting:
            # Keep the phone running: calls still ring, and the tray brings it back.
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self._notify(f"{APP_NAME} is still running",
                             "Calls will still ring. Use the tray icon to open or quit it.",
                             timeout_ms=5000)
            return
        event.accept()
        if self._tray is not None:
            QApplication.instance().quit()   # quitOnLastWindowClosed is off with a tray

    def _set_auto_answer(self, enabled: bool):
        try:
            self._phone.set_auto_answer(enabled)
        except OSError as e:
            QMessageBox.warning(self, "Auto-answer", f"The setting applies, but saving it failed:\n{e}")

    # --- showing state ---------------------------------------------------------

    def _show_registration(self, state: RegState, detail: str):
        text = state.value
        if state is RegState.REGISTERED and self._phone.account_config.transport == "tls":
            text += " (TLS)"
        if state is RegState.FAILED and detail:
            text += f": {detail}"
        self._status.showMessage(text)
        if self._tray is not None:
            self._tray.setToolTip(f"{APP_NAME}: {text}")

    def _show_incoming(self, call: CallView):
        self._select(call.call_id)
        self._tabs.setCurrentIndex(0)  # the Answer button is on the Phone tab
        if self.isVisible():
            # Bring the window to the front so the call isn't missed. When it's
            # hidden in the tray, the notification below is enough.
            self._show_window()
        if call.auto_kind is None and not self.isActiveWindow():
            notification_id = self._notify(
                "Incoming call", str(call.remote),
                [("default", "Show"), ("answer", "Answer"), ("decline", "Decline")],
                URGENCY_CRITICAL, 0)
            if notification_id is not None:
                self._ringing_notes[notification_id] = call.call_id

    def _show_ended(self, remote, reason: str):
        self._last_ended = ended_text(remote, reason)
        self._refresh()

    def _refresh(self):
        """Redraw the call list and buttons from the service's current calls."""
        calls = self._phone.calls()
        ids = [c.call_id for c in calls]
        self._consults = {t: c for t, c in self._consults.items() if t in ids and c in ids}
        if self._selected_id not in ids:
            # The selected call ended: pick the live call, else the newest.
            live = [c.call_id for c in calls if c.live]
            self._selected_id = (live or ids or [None])[-1]

        self._call_list.blockSignals(True)  # rebuilding mustn't look like a click
        self._call_list.clear()
        for call in calls:
            item = QListWidgetItem(call_status_text(call))
            item.setData(Qt.UserRole, call.call_id)
            self._call_list.addItem(item)
            if call.call_id == self._selected_id:
                self._call_list.setCurrentItem(item)
        self._call_list.blockSignals(False)
        self._call_list.setVisible(len(calls) > 1)
        self._show_selected()
        self._close_stale_notifications()

    def _show_selected(self):
        call = self._selected()
        if call is not None:
            self._call_status.setText(call_status_text(call))
        else:
            self._call_status.setText(self._last_ended)
        self._set_buttons(call)

    def _set_buttons(self, call: CallView | None):
        live = call is not None and call.live
        active = call is not None and call.state is CallState.ACTIVE
        can_dial = self._phone.can_dial() and not live
        self._call_button.setEnabled(can_dial)
        self._intercom_button.setEnabled(can_dial)
        self._page_button.setEnabled(can_dial)
        self._number.setEnabled(can_dial)
        self._answer_button.setEnabled(call is not None and call.state is CallState.RINGING_IN)
        self._hangup_button.setEnabled(call is not None)
        # A call still ringing in is declined (603; the PBX usually sends it to voicemail).
        self._hangup_button.setText(
            "Decline" if call is not None and call.state is CallState.RINGING_IN else "Hang up")
        self._hold_button.setEnabled(active and not call.transferring)
        self._hold_button.setText("Resume" if call is not None and call.on_hold else "Hold")
        # A page is one-way: the mic stays off (and isn't even open).
        page = call is not None and call.auto_kind is AutoKind.PAGE
        self._mute_button.setEnabled(live and not page)
        self._mute_button.blockSignals(True)  # don't loop back into set_mute
        self._mute_button.setChecked(call is not None and call.muted)
        self._mute_button.blockSignals(False)
        self._mute_button.setText("Unmute" if call is not None and call.muted else "Mute")
        pending = self._pending_transfer()
        self._transfer_button.setText("Complete Transfer" if pending else "Transfer")
        self._transfer_button.setEnabled(bool(pending) or (active and not call.transferring))
        self._park_button.setVisible(self._phone.park_enabled)
        self._park_button.setEnabled(active and not call.transferring)

    # --- History tab badge ------------------------------------------------------

    def _count_missed(self, record):
        if record.result is not Result.MISSED:
            return
        if self._tabs.currentWidget() is not self._history_tab:
            self._unseen_missed += 1
            self._update_history_title()
        if not self.isActiveWindow():
            notification_id = self._notify("Missed call", str(record.remote), [("default", "Show")])
            if notification_id is not None:
                self._missed_notes.add(notification_id)

    def _tab_changed(self, _index: int):
        if self._tabs.currentWidget() is self._history_tab:
            self._unseen_missed = 0
            self._update_history_title()

    def _update_history_title(self):
        title = f"History ({self._unseen_missed})" if self._unseen_missed else "History"
        self._tabs.setTabText(self._tabs.indexOf(self._history_tab), title)
