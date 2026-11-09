"""Glue between the SIP engine and the Qt UI.

The UI calls methods on PhoneService and hears back only through Qt signals.
It never touches PJSIP, so the SIP side can change without breaking the UI.

Several calls can exist at once: one you're talking on, others on hold, one
ringing in. The rule is that at most one call is *live* (connected and not
on hold): dialing, answering or resuming a call first puts the live call on
hold, so two parties never hear each other by accident.
"""

import logging
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal

from app.agent import AgentAction, AgentState, AgentTracker
from app.presence import STATUS_NAMES, Presence, activity_for
from app.config import (
    AccountConfig, AgentConfig, AppConfig, AudioConfig, BlfEntry, PageGroup, save_settings,
)
from app.contacts import (
    PERSONAL, Contact, ContactBook, colleagues, company_contacts, name_for, sort_contacts,
)
from app.directory import (
    DirectoryError, device_config_url, directory_url, local_mac, parse_blf_keys, parse_directory,
    parse_entries,
)
from app import routing
from app.park import dial_code, free_slot, is_occupied, lamp_uri, slot_numbers
from app.history import CallHistory, CallRecord, CallTracker, Direction
from app.sip.audio import AudioDevice, list_devices
from app.sip.blf import BlfState, BlfStatus
from app.sip.calls import DTMF_DIGITS, CallState, MediaState, Remote
from app.sip.errors import SipError
from app.sip.intercom import AutoAnswer, AutoKind
from app.sip.mwi import Mailbox
from app.sip.registration import RegState

log = logging.getLogger(__name__)

POLL_INTERVAL_MS = 10
AGENT_PIN_DELAY_MS = 1000     # let the PBX start its "enter password" prompt
AGENT_CALL_TIMEOUT_MS = 15000  # hang up a code call the PBX didn't end itself
BUSY_HERE = 486
MAX_CALLS = 4   # PJSIP's default limit on simultaneous calls
DIRECTORY_REFRESH_MS = 60 * 60 * 1000   # names change rarely: re-read hourly


@dataclass(frozen=True)
class CallView:
    """Read-only snapshot of one call, for the UI."""
    call_id: int
    remote: Remote
    incoming: bool
    state: CallState
    media: MediaState
    muted: bool
    transfer_text: str        # transfer progress or failure; "" if none
    transferring: bool        # a transfer of this call is in progress
    auto_kind: AutoKind | None  # intercom/page being auto-answered, else None
    encrypted: bool = False   # call audio is SRTP

    @property
    def on_hold(self) -> bool:
        return self.media is MediaState.LOCAL_HOLD

    @property
    def live(self) -> bool:
        return self.state is CallState.ACTIVE and not self.on_hold


@dataclass
class _Call:
    """Everything the service tracks about one call."""
    call_id: int
    remote: Remote
    incoming: bool
    state: CallState
    tracker: CallTracker      # builds the history entry
    media: MediaState = MediaState.NONE
    encrypted: bool = False
    muted: bool = False
    transfer_text: str = ""
    transfer_target: str = ""         # who we're transferring to, while in progress
    transfer_with: int | None = None  # the other call, for an attended transfer
    auto_kind: AutoKind | None = None # intercom/page we are auto-answering
    park_slot: str = ""               # being parked on this slot
    pickup_slot: str = ""             # this call picks up the call parked here
    keep_remote: bool = False         # shown as set by intercom()/page(), not as dialed
    quiet: bool = False               # feature-code call: not listed, not logged
    dtmf_after_answer: str = ""       # keyed in once the PBX answers (agent PIN)
    answered: bool = False            # reached ACTIVE at some point

    def view(self) -> CallView:
        return CallView(self.call_id, self.remote, self.incoming, self.state,
                        self.media, self.muted, self.transfer_text,
                        bool(self.transfer_target), self.auto_kind, self.encrypted)

    @property
    def live(self) -> bool:
        return self.view().live


class PhoneService(QObject):
    # Alert tone length before an intercom/page connects, in milliseconds.
    ALERT_MS = 500

    # (RegState, detail text such as "403 Forbidden")
    registration_changed = Signal(object, str)
    # A call appeared, changed or went away: re-read calls()
    calls_changed = Signal()
    # (CallView) a new incoming call is ringing
    incoming_call = Signal(object)
    # (Remote, reason) a call ended; reason is "" for a normal hang-up
    call_ended = Signal(object, str)
    # (CallRecord) a finished call was added to the history
    call_logged = Signal(object)
    # (extension) its BLF lamp or presence changed: re-read blf_status(extension)
    # and presence(extension)
    blf_changed = Signal(str)
    # the list of watched extensions was edited: re-read blf_entries()
    blf_list_changed = Signal()
    # agent status or "code call in progress" changed: re-read agent_state
    agent_changed = Signal()
    # a park slot filled or emptied: re-read park_slots()
    park_changed = Signal()
    # the account was changed in Settings > Account…: re-read account_config
    account_changed = Signal()
    # the voicemail count changed: re-read mailbox
    voicemail_changed = Signal()
    # call routing applied, pending or failed: re-read routing_text
    routing_changed = Signal()
    # names from the PBX directory, or directory_status, changed
    directory_changed = Signal()
    # the contact list changed: re-read contacts()
    contacts_changed = Signal()
    # (fingerprint, changed) the PBX web certificate isn't trusted; changed =
    # it differs from the one trusted before. Answer with trust_web_cert().
    web_cert_untrusted = Signal(str, bool)

    def __init__(self, config: AppConfig, history: CallHistory, engine=None,
                 fetcher=None, contact_book: ContactBook | None = None, parent=None):
        super().__init__(parent)
        if engine is None:
            from app.sip.engine import SipEngine  # loads PJSIP; unit tests pass a fake
            engine = SipEngine(config.logging, config.audio)
        self._config = config
        self._history = history
        self._engine = engine
        self._engine.on_incoming_call = self._on_incoming_call
        self._engine.on_call_state = self._on_call_state
        self._engine.on_media_state = self._on_media_state
        self._engine.on_transfer_status = self._on_transfer_status
        self._engine.on_blf = self._on_blf
        self._engine.on_presence = self._on_presence
        self._engine.on_mwi = self._on_mwi
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._engine.poll)
        self.reg_state = RegState.UNREGISTERED
        self._started = False                # PJSIP is up (start() found an account)
        self._calls: dict[int, _Call] = {}   # in the order the calls began
        self._dialing = None                 # (quiet, dtmf) while dial() runs
        self._tone = None                    # "ring"/"waiting" we started, if any
        self._previewing = False             # a ringtone preview is playing
        self._blf: dict[str, BlfStatus] = {}  # extension -> latest lamp state
        self._presence: dict[str, Presence] = {}  # extension -> latest presence
        self.mailbox = Mailbox()             # my voicemail, from the PBX's MWI
        self._agent = AgentTracker()
        self._park: dict[str, BlfStatus] = {}   # slot -> its lamp, in slot order
        self._parked_here: dict[str, Remote] = {}  # slot -> who we parked there
        self._routing_call: tuple[int, str] | None = None  # (call id, route) being set
        self._routing_failed = ""   # route whose code the PBX didn't answer; not retried
        # The PBX directory, via a WebFetcher (None = no names, e.g. in tests).
        self._fetcher = fetcher
        self._names: dict[str, str] = {}   # extension -> name from the PBX
        self._company: list[Contact] = []  # the PBX's Contacts app
        self._pbx_blf: list[BlfEntry] = [] # BLF keys of our device on the PBX
        self._fetching = ""                # which directory download is running
        self._book = contact_book or ContactBook(":memory:")
        self._mac = ""                     # found once, by device_mac
        self._declined: set[str] = set()   # certificates the user said no to
        self.directory_status = ""
        self._directory_timer = QTimer(self)
        self._directory_timer.setInterval(DIRECTORY_REFRESH_MS)
        self._directory_timer.timeout.connect(self.refresh_directory)
        if fetcher is not None:
            fetcher.setParent(self)   # deleted with the service, before Qt shuts down
            fetcher.loaded.connect(self._directory_loaded)
            fetcher.failed.connect(self._directory_failed)
            fetcher.untrusted.connect(self._directory_untrusted)

    def start(self):
        account = self._config.account
        if not account.server or not account.extension:
            log.warning("No account configured; not registering")
            return
        self.refresh_directory()
        self._directory_timer.start()
        try:
            self._engine.start(account)
            self._started = True
            self._poll_timer.start()
            self._engine.register(account, self._set_reg_state)
            self._publish_status()
            self._watch_blf()
        except SipError as e:
            log.error("SIP start failed: %s", e)
            self._set_reg_state(RegState.FAILED, str(e))

    # --- the account (Settings > Account…) ------------------------------------

    @property
    def account_config(self) -> AccountConfig:
        return self._config.account

    @property
    def has_account(self) -> bool:
        return bool(self._config.account.server and self._config.account.extension)

    def can_change_account(self) -> bool:
        """Only between calls: changing it unregisters this line."""
        return not self._calls

    def set_account(self, account: AccountConfig):
        """Use a new account (or new settings) now: unregister, forget what
        the PBX told us about the old one, register again. Saved to
        settings.toml; raises OSError if saving fails (it still applies)."""
        if not self.can_change_account():
            raise RuntimeError("can't change the account during a call")
        old = self._config.account
        self._config.account = account
        self._config.account_in_settings = True
        log.info("Account changed: %s@%s (%s)", account.extension, account.server, account.transport)
        if (old.server, old.extension) != (account.server, account.extension):
            # A different line: the PBX's routing for it is unknown, assume ringing.
            self._config.routing.applied = routing.RING
            self._names.clear()
            self._company = []
            self._pbx_blf = []
            self._mac = ""
        self._blf.clear()
        self._presence.clear()
        self._park.clear()
        self._agent = AgentTracker()
        self.mailbox = Mailbox()
        if self._started:
            try:
                self._engine.change_account(account, self._set_reg_state)
                self._publish_status()
                self._watch_blf()
            except SipError as e:
                log.error("Account change failed: %s", e)
                self._set_reg_state(RegState.FAILED, str(e))
            self.refresh_directory()
        else:
            self.start()
        for signal in (self.account_changed, self.blf_list_changed, self.park_changed,
                       self.agent_changed, self.voicemail_changed, self.directory_changed,
                       self.contacts_changed):
            signal.emit()
        save_settings(self._config)

    def stop(self):
        self._poll_timer.stop()
        self._engine.shutdown()  # hangs up live calls, which still get logged
        self._history.close()
        self._book.close()

    # --- state for the UI ---------------------------------------------------

    def calls(self) -> list[CallView]:
        """The calls to show; quiet feature-code calls are left out."""
        return [call.view() for call in self._calls.values() if not call.quiet]

    def can_dial(self) -> bool:
        """Room for another call, and no outgoing call still being set up."""
        setting_up = any(c.state in (CallState.DIALING, CallState.RINGING_OUT)
                         for c in self._calls.values())
        return len(self._calls) < MAX_CALLS and not setting_up

    # --- actions called by the UI -----------------------------------------

    def dial(self, number: str, quiet: bool = False, dtmf: str = "") -> int | None:
        """Call `number`, holding any live call first. Returns the new call's
        id, or None if the call didn't start. A quiet call (feature code) isn't
        listed or logged; `dtmf` is keyed in once it's answered."""
        number = number.strip()
        if not number or not self.can_dial():
            return None
        if self.reg_state is not RegState.REGISTERED:
            self.call_ended.emit(Remote("", number), "Not registered")
            return None
        self._hold_live_calls()
        # PJSIP reports the new call's first states from *inside* make_call,
        # before it returns the id; _dialing lets _on_call_state adopt it.
        self._dialing = (quiet, dtmf)
        try:
            call_id = self._engine.make_call(number)
        except SipError as e:
            log.error("Dial %s failed: %s", number, e)
            self.call_ended.emit(Remote("", number), str(e))
            return None
        finally:
            self._dialing = None
        return call_id if call_id in self._calls else None

    def intercom(self, number: str) -> int | None:
        """Intercom call to one extension: the PBX's intercom code + number;
        the other phone answers by itself."""
        number = number.strip()
        if not number:
            return None
        who = self._names.get(number) or number
        return self._dial_as(self._config.phone.intercom_prefix + number,
                             Remote(f"Intercom: {who}", number), "Intercom")

    @property
    def page_groups(self) -> list[PageGroup]:
        return list(self._config.page_groups)

    @property
    def intercom_prefix(self) -> str:
        return self._config.phone.intercom_prefix

    def set_paging(self, intercom_prefix: str, groups: list[PageGroup]):
        """Settings > Paging and intercom…. Raises OSError if saving fails
        (the settings still apply)."""
        self._config.phone.intercom_prefix = intercom_prefix.strip()
        self._config.page_groups = list(groups)
        save_settings(self._config)

    def page(self, group: PageGroup) -> int | None:
        """Announce to a page group: an ordinary call to its number."""
        return self._dial_as(group.number, Remote(f"Page: {group.name or group.number}",
                                                  group.number), "Page")

    def _dial_as(self, number: str, remote: Remote, note: str) -> int | None:
        """dial(), showing and logging the call as `remote`, not the code dialed."""
        call_id = self.dial(number)
        call = self._calls.get(call_id)
        if call is not None:
            call.keep_remote = True
            call.remote = call.tracker.remote = remote
            call.tracker.note = note
            self.calls_changed.emit()
        return call_id

    def answer(self, call_id: int):
        call = self._calls.get(call_id)
        if call is None or call.state is not CallState.RINGING_IN:
            return
        self._hold_live_calls(except_id=call_id)
        self._engine.stop_tone()
        self._tone = None
        self._engine.answer(call_id)

    def hangup(self, call_id: int):
        call = self._calls.get(call_id)
        if call is None:
            return
        if call.state is CallState.RINGING_IN:
            call.tracker.decline()
        self._engine.hangup(call_id)

    def toggle_hold(self, call_id: int):
        call = self._calls.get(call_id)
        if call is None or call.state is not CallState.ACTIVE:
            return
        if call.media is MediaState.LOCAL_HOLD:
            self._hold_live_calls(except_id=call_id)
            self._engine.resume(call_id)
        else:
            self._engine.hold(call_id)

    def set_mute(self, call_id: int, muted: bool):
        call = self._calls.get(call_id)
        if call is None or call.muted == muted:
            return
        call.muted = muted
        self._engine.set_mute(call_id, muted)
        log.info("Call %s microphone %s", call_id, "muted" if muted else "unmuted")
        self.calls_changed.emit()

    def send_dtmf(self, call_id: int, digit: str):
        call = self._calls.get(call_id)
        if call is not None and call.live and digit in DTMF_DIGITS:
            self._engine.send_dtmf(call_id, digit)
            self._engine.play_key_tone(digit)   # hear the key, as on a desk phone

    def play_key_tone(self, digit: str):
        """Beep a key typed while dialing (send_dtmf beeps on a call)."""
        if digit in DTMF_DIGITS:
            self._engine.play_key_tone(digit)

    def transfer(self, call_id: int, number: str):
        """Blind transfer: hand the call to `number` without talking to them."""
        call = self._calls.get(call_id)
        number = number.strip()
        if not number or not self._may_transfer(call):
            return
        self._begin_transfer(call, number, None, f"Transferring to {number}…")
        try:
            self._engine.transfer(call_id, number)
        except SipError as e:
            self._transfer_failed(call, str(e))

    def connect_calls(self, call_id: int, other_id: int):
        """Attended transfer: put the far ends of two of our calls through to
        each other, and drop out. Typically `call_id` is the caller on hold
        and `other_id` the person we just consulted."""
        call, other = self._calls.get(call_id), self._calls.get(other_id)
        if call_id == other_id or not self._may_transfer(call) or not self._may_transfer(other):
            return
        self._begin_transfer(call, str(other.remote), other_id, f"Connecting to {other.remote}…")
        try:
            self._engine.transfer_replaces(call_id, other_id)
        except SipError as e:
            self._transfer_failed(call, str(e))

    # --- call park -------------------------------------------------------------

    @property
    def park_enabled(self) -> bool:
        return self._config.park.enabled

    def park_slots(self) -> list[tuple[str, BlfStatus]]:
        return list(self._park.items())

    def parked_calls(self) -> list[tuple[str, str]]:
        """(slot, who) for each call waiting in a slot. A slot we're picking up
        is left out: FreeSWITCH keeps its lamp lit for as long as the pickup
        call lasts. The lamp itself doesn't say who's waiting, so that's only
        known for calls parked from here."""
        return [(slot, str(self._parked_here.get(slot, "Parked call")))
                for slot, status in self._park.items()
                if is_occupied(status) and not self._picking_up(slot)]

    def _picking_up(self, slot: str) -> bool:
        return any(call.pickup_slot == slot for call in self._calls.values())

    def park(self, call_id: int):
        """Park a call on the first free slot (a blind transfer to *59xx)."""
        call = self._calls.get(call_id)
        if not self.park_enabled or not self._may_transfer(call):
            return
        slot = free_slot(self._park)
        if slot is None:
            call.transfer_text = "Every park slot is in use"
            self.calls_changed.emit()
            return
        call.park_slot = slot
        self._begin_transfer(call, dial_code(slot), None, f"Parking on {slot}…")
        try:
            self._engine.transfer(call_id, dial_code(slot))
        except SipError as e:
            call.park_slot = ""
            self._transfer_failed(call, str(e))

    def retrieve(self, slot: str) -> int | None:
        """Pick up the call parked on a slot. Only while one is waiting there:
        dialing an empty slot would park *us* in it."""
        if slot not in dict(self.parked_calls()):
            log.info("Not picking up %s: no call waiting there", slot)
            return None
        log.info("Picking up the call parked on %s", slot)
        call_id = self.dial(dial_code(slot))
        call = self._calls.get(call_id)
        if call is not None:
            call.pickup_slot = slot
            who = self._parked_here.pop(slot, None)
            if who is not None:   # show and log the caller, not "*5901"
                call.remote = call.tracker.remote = who
            self.calls_changed.emit()
            self.park_changed.emit()
        return call_id

    def _park_lamps(self) -> dict[str, str]:
        """lamp URI user part -> slot"""
        park = self._config.park
        return {lamp_uri(slot): slot for slot in slot_numbers(park.first_slot, park.slots)}

    def call_or_pickup(self, extension: str) -> int | None:
        """What clicking a BLF lamp does: pick up the call ringing there,
        otherwise call the extension."""
        if self.blf_status(extension).state is BlfState.RINGING:
            log.info("Picking up call ringing at %s", extension)
            return self.dial(self._config.phone.pickup_prefix + extension)
        return self.dial(extension)

    # --- call-center agent ---------------------------------------------------

    @property
    def agent_enabled(self) -> bool:
        return self._config.agent.enabled

    @property
    def agent_id(self) -> str:
        return self._config.agent.agent_id or self._config.account.extension

    @property
    def agent_state(self) -> AgentState:
        return self._agent.state

    @property
    def agent_config(self) -> AgentConfig:
        return self._config.agent

    def set_agent_config(self, agent: AgentConfig):
        """Use new agent settings now and save them to settings.toml (owner-only:
        it holds the PIN). Raises OSError if saving fails (they still apply)."""
        old_lamp = self._agent_lamp() if self.agent_enabled else None
        self._config.agent = agent
        new_lamp = self._agent_lamp() if self.agent_enabled else None
        if old_lamp != new_lamp:
            if old_lamp:
                self._engine.unwatch(old_lamp)
            self._agent = AgentTracker()   # a different agent: state unknown again
            if new_lamp:
                self._engine.watch(new_lamp, presence=False)
        log.info("Agent settings changed: enabled=%s id=%s", agent.enabled, self.agent_id)
        self.agent_changed.emit()
        save_settings(self._config)

    def agent_can_act(self) -> bool:
        """Codes are only dialed between calls, so nobody gets put on hold."""
        return self.agent_enabled and not self._calls and self.reg_state is RegState.REGISTERED

    def agent_action(self, action: AgentAction):
        """Dial the login or break code quietly and key in the agent PIN."""
        if not self.agent_can_act():
            return
        agent = self._config.agent
        code = agent.break_code if action is AgentAction.BREAK else agent.login_code
        pin = agent.password + "#" if agent.password else ""
        log.info("Agent %s: dialing %s (%s)", self.agent_id, code, action.value)
        self._agent.requested(action)
        self.dial(code, quiet=True, dtmf=pin)
        self.agent_changed.emit()

    def _agent_lamp(self) -> str:
        # FusionPBX lights "agent+<id>" while the agent is Available.
        return f"agent+{self.agent_id}"

    def _send_digits(self, call_id: int, digits: str):
        call = self._calls.get(call_id)
        if call is not None and call.state is CallState.ACTIVE:
            self._engine.send_dtmf(call_id, digits)   # the PIN isn't logged

    # --- busy lamp field -----------------------------------------------------

    def blf_entries(self) -> list[BlfEntry]:
        """Every lamp: the PBX's BLF keys first, then our own."""
        pbx = {e.extension for e in self._pbx_blf}
        return self._pbx_blf + [e for e in self._config.blf if e.extension not in pbx]

    def own_blf_entries(self) -> list[BlfEntry]:
        """The lamps added in Settings > BLF extensions… (settings.toml)."""
        return list(self._config.blf)

    def pbx_blf_entries(self) -> list[BlfEntry]:
        """The lamps set on the PBX (FusionPBX Devices -> Keys); read-only here."""
        return list(self._pbx_blf)

    def blf_status(self, extension: str) -> BlfStatus:
        return self._blf.get(extension, BlfStatus())

    def presence(self, extension: str) -> Presence:
        return self._presence.get(extension, Presence())

    def set_blf_entries(self, entries: list[BlfEntry]):
        """Watch a new list of our own extensions and save it to settings.toml.
        Raises OSError if saving fails (the new list still applies)."""
        self._config.blf = list(entries)
        self._rewatch_blf()
        save_settings(self._config)

    def _rewatch_blf(self):
        self._engine.unwatch_all()
        self._blf.clear()
        self._presence.clear()
        self._park.clear()
        self._watch_blf()
        self.blf_list_changed.emit()

    def _watch_blf(self):
        for entry in self.blf_entries():
            self._engine.watch(entry.extension)
        if self.agent_enabled:
            self._engine.watch(self._agent_lamp(), presence=False)
        if self.park_enabled:
            for lamp, slot in self._park_lamps().items():
                self._park[slot] = BlfStatus()
                self._engine.watch(lamp, presence=False)

    # --- names from the PBX directory -----------------------------------------

    def directory_name(self, extension: str) -> str:
        return self._names.get(extension, "")

    @property
    def device_mac(self) -> str:
        """The MAC address the PBX knows this phone by ("" if unknown)."""
        if self._config.directory.mac:
            return self._config.directory.mac
        if not self._mac and self._config.account.server:
            self._mac = local_mac(self._config.account.server)
        return self._mac

    def refresh_directory(self):
        directory = self._config.directory
        server = self._config.account.server
        if self._fetcher is None or not directory.enabled or not server:
            return
        mac = self.device_mac
        if not mac:
            self._set_directory_status("can't find this computer's MAC address")
            return
        # Two downloads, one after the other: colleagues' names, then (in
        # _directory_loaded) the whole directory for the company contacts.
        self._fetching = "extensions"
        self._fetcher.get(directory_url(server, mac), directory.web_cert)

    def trust_web_cert(self, fingerprint: str):
        """Trust this PBX web certificate from now on, and fetch the names.
        Raises OSError if saving fails (the trust still applies until exit)."""
        log.info("PBX web certificate trusted: %s", fingerprint)
        self._config.directory.web_cert = fingerprint
        self.refresh_directory()
        save_settings(self._config)

    def decline_web_cert(self, fingerprint: str):
        """Don't ask about this certificate again while the app runs."""
        self._declined.add(fingerprint)

    def _directory_loaded(self, text: str):
        if self._fetching == "keys":
            self._blf_keys_loaded(text)
            return
        if self._fetching == "contacts":
            self._contacts_loaded(text)
            return
        try:
            names = parse_directory(text)
        except DirectoryError as e:
            log.warning("PBX directory: %s", e)
            self._set_directory_status("the PBX sent something that isn't a directory")
            return
        log.info("PBX directory: %d names", len(names))
        self._names = names
        self._set_directory_status(f"{len(names)} name{'s' * (len(names) != 1)} loaded"
                                   if names else "empty")
        self.contacts_changed.emit()
        self._fetching = "contacts"
        self._fetcher.get(directory_url(self._config.account.server, self.device_mac, "all"),
                          self._config.directory.web_cert)

    def _contacts_loaded(self, text: str):
        try:
            entries = parse_entries(text)
        except DirectoryError as e:
            log.warning("PBX contacts: %s", e)
        else:
            self._company = company_contacts(entries, set(self._names))
            log.info("PBX contacts: %d", len(self._company))
            self.contacts_changed.emit()
        self._fetch_blf_keys()

    def _fetch_blf_keys(self):
        """Third download: our device's config file, for its BLF keys."""
        self._fetching = "keys"
        self._fetcher.get(device_config_url(self._config.account.server, self.device_mac),
                          self._config.directory.web_cert)

    def _blf_keys_loaded(self, text: str):
        self._fetching = ""
        # The file also holds our SIP password: keep the BLF keys, drop the rest.
        keys = [BlfEntry(extension, label) for extension, label in parse_blf_keys(text)]
        log.info("PBX BLF keys: %d", len(keys))
        if keys != self._pbx_blf:
            self._pbx_blf = keys
            self._rewatch_blf()

    def _directory_failed(self, status: int, message: str):
        if self._fetching == "contacts":   # names arrived; keep them
            log.info("PBX contacts unavailable: %s", message)
            self._fetch_blf_keys()
            return
        if self._fetching == "keys":       # keep the keys we had
            self._fetching = ""
            log.info("PBX BLF keys unavailable: %s", message)
            return
        self._fetching = ""
        # FusionPBX answers 404 for every refusal: provisioning off, no
        # device with our MAC, or our address outside its allowed range.
        log.info("PBX directory unavailable: %s", message)
        if status == 404:
            self._set_directory_status("not set up on the PBX")
        else:
            self._set_directory_status(f"PBX unreachable ({message})")

    # --- contacts ---------------------------------------------------------------

    def contacts(self) -> list[Contact]:
        """Colleagues, company directory and personal contacts, by name."""
        others = {n: name for n, name in self._names.items() if n != self._config.account.extension}
        return sort_contacts(colleagues(others) + self._company + self._book.all())

    def save_contact(self, contact: Contact) -> Contact:
        """Add or update a personal contact."""
        if contact.source != PERSONAL:
            raise ValueError("only personal contacts can be edited")
        saved = self._book.save(contact)
        self.contacts_changed.emit()
        return saved

    def delete_contact(self, contact: Contact):
        if contact.source == PERSONAL and contact.id is not None:
            self._book.delete(contact.id)
            self.contacts_changed.emit()

    def _named(self, remote: Remote) -> Remote:
        """Fill in a caller's name from the contacts when the PBX sent none
        (or just repeated the number, as many trunks do)."""
        if remote.name and remote.name != remote.number:
            return remote
        name = name_for(self.contacts(), remote.number)
        return Remote(name, remote.number) if name else remote

    def _directory_untrusted(self, fingerprint: str):
        self._set_directory_status("PBX web certificate not trusted")
        if fingerprint not in self._declined:
            self.web_cert_untrusted.emit(fingerprint, bool(self._config.directory.web_cert))

    def _set_directory_status(self, text: str):
        self.directory_status = text
        self.directory_changed.emit()

    # --- my presence ---------------------------------------------------------

    @property
    def my_status(self) -> str:
        return self._config.presence.status

    def set_my_status(self, status: str):
        """Publish a new status and save it. Raises OSError if saving fails
        (the status still applies)."""
        if status not in STATUS_NAMES:
            raise ValueError(f"unknown status {status!r}")
        self._config.presence.status = status
        self._publish_status()
        self._routing_failed = ""
        self._apply_routing()
        save_settings(self._config)

    # --- voicemail -------------------------------------------------------------

    def check_voicemail(self) -> int | None:
        """Call my own mailbox (FusionPBX *97: it asks for the voicemail PIN)."""
        return self.dial(self._config.phone.voicemail_code)

    def _on_mwi(self, mailbox: Mailbox):
        if mailbox != self.mailbox:
            self.mailbox = mailbox
            self.voicemail_changed.emit()

    # --- call routing by status (app/routing.py) -------------------------------

    def routing_rules(self) -> dict[str, str]:
        return dict(self._config.routing.rules)

    def set_routing_rules(self, rules: dict[str, str]):
        """Use new rules now (the PBX is updated if the current status's
        route changed) and save them. Raises OSError if saving fails."""
        for status, route in rules.items():
            if status not in STATUS_NAMES or not routing.is_valid(route):
                raise ValueError(f"bad routing rule {status!r}: {route!r}")
        self._config.routing.rules = {s: r for s, r in rules.items() if r != routing.RING}
        log.info("Call routing rules: %s", self._config.routing.rules or "ring for every status")
        self._routing_failed = ""
        self._apply_routing()
        save_settings(self._config)

    @property
    def routing_text(self) -> str:
        """Where calls go, for the main window; "" while routing isn't used."""
        target = routing.route_for(self._config.routing.rules, self.my_status)
        applied = self._config.routing.applied
        if self._routing_call is not None:
            return "Updating call routing…"
        if self._routing_failed and self._routing_failed == target:
            return "Couldn't change call routing on the PBX"
        if target != applied:
            if self.reg_state is not RegState.REGISTERED:
                return "Call routing will update once connected"
            return "Call routing will update after this call"
        if applied == routing.RING and not self._config.routing.rules:
            return ""
        return routing.route_text(applied)

    def _apply_routing(self):
        """Dial the code that sets the current status's route on the PBX, if
        it isn't set yet. Codes wait until there's no call and we're registered;
        whatever ends a call or registers calls this again."""
        self.routing_changed.emit()
        if self._routing_call is not None:
            return   # one at a time; this runs again when it ends
        target = routing.route_for(self._config.routing.rules, self.my_status)
        code = routing.code_for(target, self._config.routing.applied)
        if code is None or target == self._routing_failed:
            return
        if self._calls or self.reg_state is not RegState.REGISTERED:
            log.info("Call routing to %s waits until the line is free", target)
            return
        log.info("Call routing: %s -> %s, dialing %s",
                 self._config.routing.applied, target, code)
        call_id = self.dial(code, quiet=True)
        if call_id is None:
            log.warning("Call routing: %s didn't start", code)
            self._routing_failed = target
        else:
            self._routing_call = (call_id, target)
        self.routing_changed.emit()

    def _routing_call_ended(self, call: _Call):
        target = self._routing_call[1]
        self._routing_call = None
        if call.answered:
            # FusionPBX's scripts answer, update the extension, play a prompt
            # and hang up; answered means the code was recognised.
            self._config.routing.applied = target
            log.info("Call routing now %s", target)
            try:
                save_settings(self._config)
            except OSError as e:
                log.error("Saving call routing failed: %s", e)
        else:
            log.warning("Call routing: the PBX didn't answer the code for %s", target)
            self._routing_failed = target
        QTimer.singleShot(0, self, self._apply_routing)   # the status may have changed meanwhile

    def _publish_status(self):
        status = self._config.presence.status
        self._engine.set_presence(status, activity_for(status))

    # --- settings ----------------------------------------------------------

    def audio_devices(self) -> list[AudioDevice]:
        """The speakers and microphones PipeWire has right now."""
        return list_devices()

    @property
    def audio_config(self) -> AudioConfig:
        return self._config.audio

    def set_audio_config(self, audio: AudioConfig):
        """Use new audio devices now and save them to settings.toml.
        Raises OSError if saving fails (the new devices still apply)."""
        self._config.audio = audio
        self._engine.set_audio_devices(audio)
        log.info("Audio settings changed: %s", audio)
        save_settings(self._config)

    def can_preview_ringtone(self) -> bool:
        """Previews only between calls: ringing switches the sound device."""
        return not self._calls

    def preview_ringtone(self, ringtone: str, ring_file: str = ""):
        """Play a ringtone (not saved yet) on the ringer, until stop_preview()."""
        if self.can_preview_ringtone():
            self._engine.play_tone("ring", ringtone=ringtone, ring_file=ring_file)
            self._previewing = True

    def stop_preview(self):
        # A call that came in meanwhile owns the tone now (_update_tones).
        if self._previewing and not self._calls:
            self._engine.stop_tone()
        self._previewing = False

    @property
    def call_waiting(self) -> bool:
        return self._config.phone.call_waiting

    def set_call_waiting(self, enabled: bool):
        """Raises OSError if saving fails (the setting still applies)."""
        self._config.phone.call_waiting = enabled
        log.info("Call waiting %s", "on" if enabled else "off")
        save_settings(self._config)

    @property
    def ui_config(self):
        return self._config.ui

    def set_ui_option(self, name: str, value):
        """Change a [ui] setting and save it. Raises OSError if saving fails."""
        setattr(self._config.ui, name, value)
        save_settings(self._config)

    @property
    def auto_answer(self) -> bool:
        return self._config.phone.auto_answer

    def set_auto_answer(self, enabled: bool):
        """Raises OSError if saving fails (the setting still applies)."""
        self._config.phone.auto_answer = enabled
        log.info("Auto-answer for intercom/paging %s", "on" if enabled else "off")
        save_settings(self._config)

    # --- history -------------------------------------------------------------

    def recent_calls(self) -> list[CallRecord]:
        return self._history.recent()

    def delete_call(self, record_id: int):
        self._history.delete(record_id)

    def clear_history(self):
        self._history.clear()

    # --- callbacks from the engine (run on the Qt main thread via poll) ----

    def _on_incoming_call(self, call_id: int, remote: Remote, auto: AutoAnswer | None = None):
        remote = self._named(remote)
        if self._calls and (not self.call_waiting or len(self._calls) >= MAX_CALLS):
            log.info("Busy; rejecting incoming call from %s", remote)
            self._engine.hangup(call_id, BUSY_HERE)
            self._log_call(CallTracker(Direction.INCOMING, remote).finish("Busy on another call"))
            return
        call = _Call(call_id, remote, True, CallState.RINGING_IN,
                     CallTracker(Direction.INCOMING, remote))
        self._calls[call_id] = call
        self._engine.ring(call_id)
        # Intercom/page: answer by itself, but only when nothing else is going
        # on; during a call it arrives as an ordinary waiting call.
        if auto is not None and self.auto_answer and len(self._calls) == 1:
            self._start_auto_answer(call, auto)
        else:
            self._update_tones()
        self.calls_changed.emit()
        self.incoming_call.emit(call.view())

    def _start_auto_answer(self, call: _Call, auto: AutoAnswer):
        call.auto_kind = auto.kind
        call.tracker.note = "Intercom" if auto.kind is AutoKind.INTERCOM else "Page"
        page = auto.kind is AutoKind.PAGE
        log.info("Auto-answering %s from %s", auto.kind.value, call.remote)
        self._engine.play_tone("alert", on_ringer=page)
        delay_ms = max(self.ALERT_MS, auto.delay_s * 1000)
        QTimer.singleShot(delay_ms, lambda: self._finish_auto_answer(call.call_id))

    def _finish_auto_answer(self, call_id: int):
        call = self._calls.get(call_id)
        if call is None or call.state is not CallState.RINGING_IN:
            return  # the caller gave up during the alert tone
        if len(self._calls) > 1:
            # Something else started meanwhile: let this one ring normally.
            call.auto_kind = None
            self._update_tones()
            self.calls_changed.emit()
            return
        if call.auto_kind is AutoKind.PAGE:
            # One-way: never send our mic, and play it on the ringer.
            call.muted = True
            self._engine.set_mute(call_id, True)
            self._engine.answer(call_id, on_ringer=True)
        else:
            self._engine.answer(call_id)

    def _on_call_state(self, call_id: int, state: CallState, remote: Remote, reason: str):
        call = self._calls.get(call_id)
        if call is None:
            if not self._dialing:
                return  # e.g. a call we rejected as busy
            quiet, dtmf = self._dialing
            call = _Call(call_id, remote, False, state, CallTracker(Direction.OUTGOING, remote),
                         quiet=quiet, dtmf_after_answer=dtmf)
            self._calls[call_id] = call
            if quiet:
                QTimer.singleShot(AGENT_CALL_TIMEOUT_MS, lambda: self.hangup(call_id))
        call.state = state
        if call.pickup_slot or call.keep_remote:
            remote = Remote("", "")   # keep the parked caller's name over "*5901"
        if remote.number:
            remote = self._named(remote)
            call.remote = remote
        call.tracker.on_state(state, remote)
        if state is CallState.ACTIVE:
            call.answered = True
            # E.g. an outgoing call answered while we'd picked up another one.
            self._hold_live_calls(except_id=call_id)
            if call.dtmf_after_answer:
                digits, call.dtmf_after_answer = call.dtmf_after_answer, ""
                QTimer.singleShot(AGENT_PIN_DELAY_MS,
                                  lambda: self._send_digits(call_id, digits))
        if state is CallState.ENDED:
            del self._calls[call_id]
            if call.park_slot and call.transfer_target and not reason:
                reason = f"parked on {call.park_slot}"
            elif call.transfer_target and not reason:
                reason = f"transferred to {call.transfer_target}"
            if call.pickup_slot:
                self.park_changed.emit()    # its slot may be listed again
            if self._routing_call and self._routing_call[0] == call_id:
                self._routing_call_ended(call)
            elif not self._calls:
                QTimer.singleShot(0, self, self._apply_routing)   # it may have been waiting
            if call.quiet:
                self.agent_changed.emit()   # buttons usable again
            else:
                self._log_call(call.tracker.finish(reason))
                self.call_ended.emit(call.remote, reason)
        self._update_tones()
        self.calls_changed.emit()

    def _on_media_state(self, call_id: int, state: MediaState, encrypted: bool = False):
        call = self._calls.get(call_id)
        if call is not None:
            call.media = state
            call.encrypted = encrypted
            self._update_tones()   # early media replaces our ringback
            self.calls_changed.emit()

    def _on_transfer_status(self, call_id: int, code: int, reason: str, final: bool):
        call = self._calls.get(call_id)
        if call is None or not call.transfer_target:
            return
        target = call.transfer_target
        if not final:
            if code == 180:
                call.transfer_text = f"Transferring to {target}: ringing…"
                self.calls_changed.emit()
            return
        if not 200 <= code < 300:
            self._transfer_failed(call, f"{code} {reason}")
            return
        # The far end is now connected to the target; our legs are done.
        call.tracker.note = f"Parked on {call.park_slot}" if call.park_slot else f"Transferred to {target}"
        if call.park_slot:
            self._parked_here[call.park_slot] = call.remote
        other = self._calls.get(call.transfer_with) if call.transfer_with is not None else None
        if other is not None:
            other.tracker.note = f"Connected to {call.remote}"
            self._engine.hangup(other.call_id)
        self._engine.hangup(call_id)

    def _on_blf(self, extension: str, status: BlfStatus):
        if self.agent_enabled and extension == self._agent_lamp():
            previous = self._agent.state
            if self._agent.on_lamp(status.state) is not previous:
                log.info("Agent %s: %s", self.agent_id, self._agent.state.value)
                self.agent_changed.emit()
            return
        slot = self._park_lamps().get(extension) if self.park_enabled else None
        if slot is not None:
            if self._park.get(slot) != status:
                self._park[slot] = status
                if not is_occupied(status):
                    self._parked_here.pop(slot, None)   # picked up elsewhere, or timed out
                self.park_changed.emit()
            return
        if self._blf.get(extension) != status:
            self._blf[extension] = status
            self.blf_changed.emit(extension)

    def _on_presence(self, extension: str, presence: Presence):
        if self._presence.get(extension) != presence:
            self._presence[extension] = presence
            self.blf_changed.emit(extension)

    # --- helpers -------------------------------------------------------------

    def _hold_live_calls(self, except_id: int | None = None):
        for call in self._calls.values():
            if call.call_id != except_id and call.live:
                self._engine.hold(call.call_id)

    def _update_tones(self):
        """Ring when a call comes in while idle; beep (call waiting) when
        it comes in while another call is in progress. Otherwise play
        ringback while a call we dialed rings without audio from the PBX."""
        # A call being auto-answered has its own one-shot alert tone instead.
        calls = [c for c in self._calls.values()
                 if not (c.auto_kind is not None and c.state is CallState.RINGING_IN)]
        ringing = any(c.state is CallState.RINGING_IN for c in calls)
        busy = any(c.state is not CallState.RINGING_IN for c in self._calls.values())
        if ringing:
            tone, on_ringer = ("waiting" if busy else "ring"), None
        elif any(self._needs_ringback(c) for c in self._calls.values()):
            tone, on_ringer = "ringback", False   # in the earpiece/headset, like a call
        else:
            if self._tone is not None:   # leave a running alert tone alone
                self._engine.stop_tone()
                self._tone = None
            return
        self._tone = tone
        self._engine.play_tone(tone, on_ringer=on_ringer)

    @staticmethod
    def _needs_ringback(call: _Call) -> bool:
        # 180 Ringing carries no audio, so the phone makes the ringback
        # itself. A 183 with SDP ("early media") brings the PBX's own
        # ringback or announcement instead: then media is no longer NONE.
        return (call.state is CallState.RINGING_OUT and call.media is MediaState.NONE
                and not call.quiet)

    @staticmethod
    def _may_transfer(call: _Call | None) -> bool:
        return call is not None and call.state is CallState.ACTIVE and not call.transfer_target

    def _begin_transfer(self, call: _Call, target: str, other_id: int | None, text: str):
        call.transfer_target = target
        call.transfer_with = other_id
        call.transfer_text = text
        self.calls_changed.emit()

    def _transfer_failed(self, call: _Call, detail: str):
        log.error("Transfer of call %s to %s failed: %s", call.call_id, call.transfer_target, detail)
        what = f"Parking on {call.park_slot}" if call.park_slot else f"Transfer to {call.transfer_target}"
        call.transfer_text = f"{what} failed ({detail})"
        call.park_slot = ""
        call.transfer_target = ""
        call.transfer_with = None
        self.calls_changed.emit()

    def _log_call(self, record: CallRecord):
        try:
            self._history.add(record)
        except Exception:  # never let a history problem break calling
            log.exception("Saving call to history failed")
            return
        log.info("Call logged: %s %s %s", record.direction.value, record.remote, record.result.value)
        self.call_logged.emit(record)

    def _set_reg_state(self, state: RegState, detail: str):
        self.reg_state = state
        self.registration_changed.emit(state, detail)
        if state is RegState.REGISTERED:
            QTimer.singleShot(0, self, self._apply_routing)
