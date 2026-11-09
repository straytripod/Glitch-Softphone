"""Thin wrapper around PJSIP's pjsua2 API. No Qt code belongs here.

Threading: PJSIP is started with zero SIP worker threads. The owner must
call poll() regularly from one thread (the Qt main thread, via a timer).
Every PJSIP callback (registration, calls, BLF) then runs inside poll() on
that thread, so no locking or cross-thread hand-off is needed. Audio and RTP
still run on PJSIP's own media threads, so a busy UI doesn't break audio.
"""

import logging
from pathlib import Path

import pjsua2 as pj

from app import APP_VERSION
from app.config import DEFAULT_PORTS, AccountConfig, AudioConfig, LoggingConfig
from app.presence import Activity, Presence
from app.ringtones import CUSTOM, DEFAULT_RINGTONE, PRESETS
from app.sip.audio import MIC_DEV, RINGER_DEV, SPEAKER_DEV, install_alsa_config
from app.sip.blf import BlfState, BlfStatus, notify_body, parse_dialog_info
from app.sip.certs import system_ca_file
from app.sip.codecs import codec_priorities
from app.sip.calls import CallState, MediaState, parse_remote, redact_secrets, refer_target
from app.sip.errors import SipError  # noqa: F401  (re-exported for callers)
from app.sip.intercom import classify_auto_answer
from app.sip.mwi import parse_message_summary, sip_body
from app.sip.registration import RegState, reg_state_from_response

log = logging.getLogger(__name__)
pjsip_log = logging.getLogger("pjsip")

USER_AGENT = f"GlitchSoftphone/{APP_VERSION}"

_TRANSPORT_TYPES = {
    "udp": pj.PJSIP_TRANSPORT_UDP,
    "tcp": pj.PJSIP_TRANSPORT_TCP,
    "tls": pj.PJSIP_TRANSPORT_TLS,
}

_ACTIVITIES = {
    Activity.NONE: pj.PJRPID_ACTIVITY_UNKNOWN,
    Activity.AWAY: pj.PJRPID_ACTIVITY_AWAY,
    Activity.BUSY: pj.PJRPID_ACTIVITY_BUSY,
}

_SRTP_USE = {
    "off": pj.PJMEDIA_SRTP_DISABLED,
    "optional": pj.PJMEDIA_SRTP_OPTIONAL,
    "required": pj.PJMEDIA_SRTP_MANDATORY,
}


def _tls_config(account: AccountConfig) -> pj.TlsConfig:
    """Client-side TLS settings: which CAs to trust, and modern TLS only."""
    tls = pj.TlsConfig()
    # PJSIP trusts nothing unless given a CA list; without our own CA file,
    # use the system's bundle.
    tls.CaListFile = account.tls_ca_file or system_ca_file()
    tls.verifyServer = account.tls_verify
    tls.proto = pj.PJ_SSL_SOCK_PROTO_TLS1_2 | pj.PJ_SSL_SOCK_PROTO_TLS1_3
    return tls


class _PjLogWriter(pj.LogWriter):
    """Forward PJSIP's own log lines into Python logging."""

    def write(self, entry):
        if entry.level <= 1:
            level = logging.ERROR
        elif entry.level == 2:
            level = logging.WARNING
        elif entry.level == 3:
            level = logging.INFO
        else:
            level = logging.DEBUG
        pjsip_log.log(level, redact_secrets(entry.msg.rstrip()))


_CALL_STATES = {
    pj.PJSIP_INV_STATE_CALLING: CallState.DIALING,
    pj.PJSIP_INV_STATE_INCOMING: CallState.RINGING_IN,
    pj.PJSIP_INV_STATE_CONNECTING: CallState.CONNECTING,
    pj.PJSIP_INV_STATE_CONFIRMED: CallState.ACTIVE,
    pj.PJSIP_INV_STATE_DISCONNECTED: CallState.ENDED,
}

_MEDIA_STATES = {
    pj.PJSUA_CALL_MEDIA_ACTIVE: MediaState.ACTIVE,
    pj.PJSUA_CALL_MEDIA_LOCAL_HOLD: MediaState.LOCAL_HOLD,
    pj.PJSUA_CALL_MEDIA_REMOTE_HOLD: MediaState.REMOTE_HOLD,
}

# Tones as (freq1, freq2, ((on_ms, off_ms), ...), loop).
# Steps are (freq1, freq2, ms on, ms off), as in app/ringtones.py; the
# incoming-call ring itself comes from there (the user's choice).
TONES = {
    # Call waiting, heard inside the current call: two short beeps every 8 s.
    "waiting": (((440, 0, 200, 200), (440, 0, 200, 8000)), True),
    # Intercom/page about to connect: one rising double beep, played once.
    "alert": (((880, 0, 120, 60), (880, 0, 120, 0)), False),
    # Our outgoing call is ringing and the PBX sent 180 without audio:
    # North American ringback, 440+480 Hz, 2 s on, 4 s off.
    "ringback": (((440, 480, 2000, 4000),), True),
}

KEY_TONE_MS = 120   # keypad beep length, like a desk phone

# PJMEDIA_AUD_DEFAULT_*_DEV, which the Python bindings don't export.
DEFAULT_CAPTURE_DEV = -1
DEFAULT_PLAYBACK_DEV = -2


class _Account(pj.Account):
    """pjsua2 calls these methods on us; we hand them to the engine."""

    def __init__(self, engine, on_reg_state):
        super().__init__()
        self._engine = engine
        self._on_reg_state = on_reg_state

    def onRegState(self, prm):
        log.info("REGISTER result: %s %s (expires %ss)",
                 prm.code, prm.reason, prm.expiration)
        state = reg_state_from_response(prm.code, prm.expiration)
        self._on_reg_state(state, f"{prm.code} {prm.reason}")

    def onIncomingCall(self, prm):
        # The raw INVITE tells us whether the PBX wants an auto-answer.
        self._engine._incoming_call(_Call(self._engine, self, prm.callId), prm.rdata.wholeMsg)

    def onMwiInfo(self, prm):
        # A message-summary NOTIFY (subscribed, or sent unasked by the PBX).
        mailbox = parse_message_summary(sip_body(prm.rdata.wholeMsg or ""))
        if mailbox is not None:
            log.info("Voicemail: %s new, %s old", mailbox.new, mailbox.old)
            self._engine.on_mwi(mailbox)


class _Call(pj.Call):
    def __init__(self, engine, account, call_id=pj.PJSUA_INVALID_ID):
        super().__init__(account, call_id)
        self._engine = engine

    def onCallState(self, prm):
        self._engine._call_state_changed(self)

    def onCallMediaState(self, prm):
        self._engine._call_media_changed(self)

    def onCallTransferStatus(self, prm):
        self._engine._transfer_status(self, prm)


class _Buddy(pj.Buddy):
    """One subscription to an extension: dialog state (BLF lamp) or presence.

    A PJSIP buddy holds a single subscription; with both kinds requested it
    only does presence. So an extension watched for both gets two buddies
    with the same URI, which PJSIP allows."""

    def __init__(self, engine, number, presence):
        super().__init__()
        self._engine = engine
        self.number = number
        self.presence = presence   # True: Event: presence; False: Event: dialog

    def onBuddyState(self):
        if self.presence:
            self._engine._presence_event(self)

    def onBuddyEvSubDlgEventState(self, prm):
        # pjsua2 doesn't expose the parsed dialog state, but this callback
        # carries the raw NOTIFY, whose dialog-info XML we parse ourselves.
        self._engine._blf_event(self, prm.e)


class SipEngine:
    """Owns PJSIP. Reports events through plain callbacks set by the owner:

    on_incoming_call(call_id: int, remote: Remote, auto: AutoAnswer | None)
    on_call_state(call_id: int, state: CallState, remote: Remote, reason: str)
    on_media_state(call_id: int, state: MediaState, encrypted: bool)
    on_transfer_status(call_id: int, code: int, reason: str, final: bool)
    on_blf(number: str, status: BlfStatus)
    on_presence(number: str, presence: Presence)
    """

    def __init__(self, logging_config: LoggingConfig, audio_config: AudioConfig):
        self._logging_config = logging_config
        self._audio_config = audio_config
        self._ep = pj.Endpoint()
        # PJSIP keeps a raw pointer to the log writer; see start() for who owns it.
        self._log_writer = _PjLogWriter()
        self._account = None
        self._account_config = None
        self._calls = {}          # call_id -> _Call, for calls in progress
        self._ended_calls = []    # dropped at the next poll(), see _call_state_changed
        self._muted_calls = set()
        self._tone = None         # ToneGenerator or AudioMediaPlayer playing, if any
        self._tone_kind = None    # what it plays: a TONES key or "ring:<ringtone>"
        self._key_tone = None     # ToneGenerator for keypad beeps, while one plays
        self._dev_ids = {}        # our ALSA device name -> PJSIP device id
        self._ring_mode = None    # True = ringer, speaker only; False = call devices
        self._created = False
        self.on_incoming_call = lambda call_id, remote, auto: None
        self.on_call_state = lambda call_id, state, remote, reason: None
        self.on_media_state = lambda call_id, state, encrypted: None
        self.on_transfer_status = lambda call_id, code, reason, final: None
        self.on_blf = lambda number, status: None
        self.on_presence = lambda number, presence: None
        self.on_mwi = lambda mailbox: None
        self._buddies = {}        # number -> [_Buddy]: lamp, and presence if wanted
        self._transports = {}     # (transport, TLS settings) -> PJSIP transport id

    def start(self, account: AccountConfig):
        """Initialise PJSIP and open the account's SIP transport (udp/tcp/tls)."""
        transport = account.transport
        sip_trace = self._logging_config.sip_trace
        if sip_trace:
            pjsip_log.setLevel(logging.DEBUG)

        ep_cfg = pj.EpConfig()
        ep_cfg.uaConfig.threadCnt = 0           # we drive PJSIP via poll()
        ep_cfg.uaConfig.mainThreadOnly = False
        ep_cfg.uaConfig.userAgent = USER_AGENT
        # Level 5 includes full SIP messages; 3 is a normal summary.
        # PJSIP only passes lines at or below consoleLevel to our writer.
        ep_cfg.logConfig.level = 5 if sip_trace else 3
        ep_cfg.logConfig.consoleLevel = ep_cfg.logConfig.level
        ep_cfg.logConfig.msgLogging = sip_trace
        ep_cfg.logConfig.writer = self._log_writer
        # Endpoint.libDestroy() deletes the writer itself. Hand ownership to
        # C++ so Python doesn't free it a second time (that double free
        # corrupted memory and crashed the app on exit).
        self._log_writer.__disown__()
        # Python logging adds the timestamp; PJSIP only adds its source file.
        ep_cfg.logConfig.decor = pj.PJ_LOG_HAS_SENDER | pj.PJ_LOG_HAS_SPACE

        # ALSA reads this when PJSIP first opens audio, so it must come first.
        install_alsa_config(self._audio_config)

        try:
            self._ep.libCreate()
            self._created = True
            self._ep.libInit(ep_cfg)
            self._transport_for(account)
            self._ep.libStart()
            self._set_codec_priorities()
        except pj.Error as e:
            raise SipError(e.info()) from None
        log.info("PJSIP %s started (%s%s)", self._ep.libVersion().full, transport,
                 f", SRTP {account.srtp_mode}" if transport == "tls" else "")
        self._find_audio_devices()
        self._use_call_devices()

    def register(self, account: AccountConfig, on_reg_state):
        """Create the account and send REGISTER.

        on_reg_state(state: RegState, detail: str) is called from poll()
        whenever the registration result changes.
        """
        uri_params = "" if account.transport == "udp" else f";transport={account.transport}"
        acfg = pj.AccountConfig()
        acfg.idUri = f'"{account.display_name}" <sip:{account.extension}@{account.server}>'
        acfg.regConfig.registrarUri = f"sip:{account.server}:{account.sip_port}{uri_params}"
        # SDES only: the keys go in the SDP, protected by TLS. That's what
        # FusionPBX desk phones use; DTLS-SRTP is for WebRTC.
        acfg.mediaConfig.srtpUse = _SRTP_USE[account.srtp_mode]
        acfg.mediaConfig.srtpSecureSignaling = 1   # SRTP only over TLS
        acfg.mediaConfig.srtpOpt.keyings = pj.IntVector([pj.PJMEDIA_SRTP_KEYING_SDES])
        if account.transport != "udp":
            # Send every request via the PBX on our transport. Without this,
            # requests addressed to a bare sip:101@pbx URI (the presence
            # PUBLISH) look for a UDP transport we don't have, and fail.
            acfg.sipConfig.proxies.append(
                f"sip:{account.server}:{account.sip_port};transport={account.transport};lr")
        # Publish my presence status (PUBLISH) once registered.
        acfg.presConfig.publishEnabled = True
        # Subscribe to our mailbox's message-summary: voicemail waiting lamp.
        acfg.mwiConfig.enabled = True
        # Use exactly the transport made for these settings (there can be
        # several after the account was changed, e.g. UDP and TLS).
        try:
            acfg.sipConfig.transportId = self._transport_for(account)
        except pj.Error as e:
            raise SipError(e.info()) from None
        acfg.sipConfig.authCreds.append(pj.AuthCredInfo(
            "digest", "*", account.auth_username or account.extension, 0, account.password,
        ))

        on_reg_state(RegState.REGISTERING, "")
        self._account_config = account
        self._account = _Account(self, on_reg_state)
        try:
            self._account.create(acfg)
        except pj.Error as e:
            raise SipError(e.info()) from None

    def _transport_for(self, account: AccountConfig) -> int:
        """The id of a transport matching the account's settings, created if
        needed. Raises pj.Error."""
        key = (account.transport,
               (account.tls_ca_file, account.tls_verify) if account.transport == "tls" else None)
        if key not in self._transports:
            tcfg = pj.TransportConfig()
            tcfg.port = 0  # any free local port; the PBX learns it from REGISTER
            if account.transport == "tls":
                tcfg.tlsConfig = _tls_config(account)
            self._transports[key] = self._ep.transportCreate(_TRANSPORT_TYPES[account.transport], tcfg)
        return self._transports[key]

    def change_account(self, account: AccountConfig, on_reg_state):
        """Swap to a different account (or new settings for this one) without
        restarting PJSIP: the old one unregisters and drops its lamps, the new
        one registers on a transport that matches it. Only between calls."""
        self.unwatch_all()
        if self._account is not None:
            self._account.shutdown()   # sends the un-REGISTER
            self._account = None
        self.register(account, on_reg_state)   # finds or makes the transport

    # --- busy lamp field ------------------------------------------------------

    def watch(self, number: str, presence: bool = True):
        """Subscribe to an extension's dialog state (BLF) and, unless
        presence=False (e.g. the agent lamp), its presence. Results arrive
        through on_blf / on_presence whenever the PBX sends a NOTIFY."""
        if number in self._buddies or self._account is None:
            return
        buddies = [self._subscribe(number, presence=False)]
        if presence:
            buddies.append(self._subscribe(number, presence=True))
        self._buddies[number] = [b for b in buddies if b is not None]
        log.info("BLF: watching %s%s", number, " (+ presence)" if presence else "")

    def _subscribe(self, number: str, presence: bool):
        cfg = pj.BuddyConfig()
        cfg.uri = self._dial_uri(number)
        cfg.subscribe = presence
        cfg.subscribe_dlg_event = not presence
        buddy = _Buddy(self, number, presence)
        try:
            buddy.create(self._account, cfg)
        except pj.Error as e:
            kind = "presence" if presence else "BLF"
            log.warning("%s subscribe to %s failed: %s", kind, number, e.info())
            return None
        return buddy

    def unwatch(self, number: str):
        """Stop watching one extension (deleting a buddy unsubscribes it)."""
        self._buddies.pop(number, None)

    def unwatch_all(self):
        # Deleting a pj.Buddy unsubscribes it (SUBSCRIBE with Expires: 0).
        self._buddies.clear()

    def _blf_event(self, buddy, event):
        try:
            if buddy.getInfo().subState == pj.PJSIP_EVSUB_STATE_TERMINATED:
                # Refused or expired; PJSIP retries by itself later.
                self.on_blf(buddy.number, BlfStatus(BlfState.UNKNOWN))
                return
        except pj.Error:
            return
        if event.type != pj.PJSIP_EVENT_TSX_STATE:
            return
        tsx = event.body.tsxState
        if tsx.type != pj.PJSIP_EVENT_RX_MSG:
            return
        body = notify_body(tsx.src.rdata.wholeMsg)
        if body is None:
            return  # e.g. the 202 Accepted to our SUBSCRIBE
        status = parse_dialog_info(body)
        log.debug("BLF %s: %s %s", buddy.number, status.state.value, status.remote or "")
        self.on_blf(buddy.number, status)

    def _presence_event(self, buddy):
        status = buddy.getInfo().presStatus
        online = {pj.PJSUA_BUDDY_STATUS_ONLINE: True,
                  pj.PJSUA_BUDDY_STATUS_OFFLINE: False}.get(status.status)
        presence = Presence(online, status.note or status.statusText)
        log.debug("Presence %s: %s", buddy.number, presence)
        self.on_presence(buddy.number, presence)

    # --- my presence ----------------------------------------------------------

    def set_presence(self, note: str, activity: Activity):
        """Publish my status. PJSIP sends it (and re-sends it after each
        re-registration) once the account is registered, and withdraws it
        when the account shuts down."""
        if self._account is None:
            return
        status = pj.PresenceStatus()
        status.status = pj.PJSUA_BUDDY_STATUS_ONLINE
        status.activity = _ACTIVITIES[activity]
        status.note = note
        try:
            self._account.setOnlineStatus(status)
        except pj.Error as e:
            log.warning("Publishing presence failed: %s", e.info())
            return
        log.info("Presence: %s", note)

    # --- calls -------------------------------------------------------------

    def _dial_uri(self, number: str) -> str:
        account = self._account_config
        uri_params = "" if account.transport == "udp" else f";transport={account.transport}"
        port = "" if account.sip_port == DEFAULT_PORTS[account.transport] else f":{account.sip_port}"
        return f"sip:{number}@{account.server}{port}{uri_params}"

    def make_call(self, number: str) -> int:
        """Dial a number or extension on the PBX. Returns the call id."""
        self._use_call_devices()
        call = _Call(self, self._account)
        try:
            call.makeCall(self._dial_uri(number), pj.CallOpParam(True))
        except pj.Error as e:
            raise SipError(e.info()) from None
        # A call that failed instantly has already reported ENDED; don't track it.
        if call.isActive():
            self._calls[call.getId()] = call
        return call.getId()

    def ring(self, call_id: int):
        """Tell the caller we're ringing (180 Ringing)."""
        self._answer_with(call_id, 180)

    def answer(self, call_id: int, on_ringer: bool = False):
        """Answer a call. on_ringer plays it on the ringer device, speaker
        only (for one-way pages: the headset mic isn't even opened)."""
        if on_ringer:
            self._use_ring_devices()
        else:
            self._use_call_devices()
        self._answer_with(call_id, 200)

    def hangup(self, call_id: int, status_code: int = 0):
        """End or reject a call. status_code 0 lets PJSIP pick (e.g. 603 Decline
        for an unanswered incoming call); 486 means Busy Here."""
        call = self._calls.get(call_id)
        if call is None:
            return
        prm = pj.CallOpParam()
        prm.statusCode = status_code
        try:
            call.hangup(prm)
        except pj.Error as e:
            log.warning("Hangup of call %s failed: %s", call_id, e.info())

    def hold(self, call_id: int):
        """Put the call on hold (re-INVITE with a=sendonly). The PBX then
        plays music on hold to the other side."""
        call = self._calls.get(call_id)
        if call is None:
            return
        try:
            call.setHold(pj.CallOpParam(True))
        except pj.Error as e:
            log.warning("Hold of call %s failed: %s", call_id, e.info())

    def resume(self, call_id: int):
        call = self._calls.get(call_id)
        if call is None:
            return
        prm = pj.CallOpParam(True)
        prm.opt.flag = pj.PJSUA_CALL_UNHOLD
        try:
            call.reinvite(prm)
        except pj.Error as e:
            log.warning("Resume of call %s failed: %s", call_id, e.info())

    def set_mute(self, call_id: int, muted: bool):
        """Stop or restart sending our microphone into the call."""
        if muted:
            self._muted_calls.add(call_id)
        else:
            self._muted_calls.discard(call_id)
        call = self._calls.get(call_id)
        if call is not None:
            self._call_media_changed(call, report=False)

    def send_dtmf(self, call_id: int, digits: str):
        """Send keypad digits in the RTP stream (RFC 2833 telephone-events)."""
        call = self._calls.get(call_id)
        if call is None:
            return
        try:
            call.dialDtmf(digits)
        except pj.Error as e:
            log.warning("DTMF on call %s failed: %s", call_id, e.info())

    def play_key_tone(self, digit: str):
        """Beep the keypad digit in our own ear, as a desk phone does. Only we
        hear it: the PBX gets the digit from send_dtmf(). poll() disconnects
        the generator once it's quiet, so it never holds the sound device."""
        if not self._calls:
            self._use_call_devices()   # dialing: in the headset, not the ringer
        try:
            if self._key_tone is None:
                generator = pj.ToneGenerator()
                generator.createToneGenerator()
                generator.startTransmit(self._ep.audDevManager().getPlaybackDevMedia())
                self._key_tone = generator
            tone = pj.ToneDigit()
            tone.digit, tone.on_msec, tone.off_msec = digit, KEY_TONE_MS, 0
            self._key_tone.playDigits(pj.ToneDigitVector([tone]), False)
        except pj.Error as e:
            log.warning("Keypad tone failed: %s", e.info())

    def _stop_key_tone(self, only_if_quiet: bool = True):
        generator = self._key_tone
        if generator is None or (only_if_quiet and generator.isBusy()):
            return
        self._key_tone = None
        try:
            generator.stopTransmit(self._ep.audDevManager().getPlaybackDevMedia())
            generator.stop()
        except pj.Error as e:
            log.warning("Stopping the keypad tone failed: %s", e.info())

    def transfer(self, call_id: int, number: str):
        """Blind transfer: send REFER asking the far end (in practice the PBX)
        to call `number` instead of us. Progress comes back through
        on_transfer_status; our leg stays up until the caller hangs it up."""
        call = self._calls.get(call_id)
        if call is None:
            return
        try:
            call.xfer(refer_target(number, self._account_config.server), pj.CallOpParam(True))
        except pj.Error as e:
            raise SipError(e.info()) from None
        log.info("Call %s: REFER to %s sent", call_id, number)

    def transfer_replaces(self, call_id: int, other_id: int):
        """Attended transfer: connect the parties of two of our calls. Sends
        REFER on `call_id` telling its far end to call the far end of
        `other_id` with "Replaces", so that party swaps us for them.
        Progress comes back through on_transfer_status for `call_id`."""
        call, other = self._calls.get(call_id), self._calls.get(other_id)
        if call is None or other is None:
            return
        try:
            call.xferReplaces(other, pj.CallOpParam(True))
        except pj.Error as e:
            raise SipError(e.info()) from None
        log.info("Call %s: REFER with Replaces of call %s sent", call_id, other_id)

    def play_tone(self, kind: str, on_ringer: bool | None = None, ringtone: str = "",
                  ring_file: str = ""):
        """Play "ring" (the chosen ringtone, or `ringtone` / `ring_file` to preview another)
        or a tone from TONES; looped ones play until stop_tone(). "ring"
        plays on the ringer (speaker only); "waiting" into the current
        call's audio. on_ringer=True/False picks the ringer or call devices first."""
        if kind == "ring":
            ringtone = ringtone or self._audio_config.ringtone
            ring_file = ring_file or self._audio_config.ringtone_file
            playing, loop = f"ring:{ringtone}:{ring_file}", True
        else:
            playing, loop = kind, TONES[kind][1]
        if self._tone_kind == playing and loop:
            return
        self.stop_tone()
        if kind == "ring" or on_ringer:
            self._use_ring_devices()
        elif on_ringer is False:
            self._use_call_devices()
        media = None
        if kind == "ring" and ringtone == CUSTOM:
            media = self._ring_file(ring_file)
        if media is None:
            steps = TONES[kind][0] if kind != "ring" else PRESETS.get(ringtone, PRESETS[DEFAULT_RINGTONE])
            media = self._tone_generator(kind, steps, loop)
        if media is None:
            return
        try:
            media.startTransmit(self._ep.audDevManager().getPlaybackDevMedia())
        except pj.Error as e:
            log.error("Could not play %s: %s", playing, e.info())
            return
        self._tone, self._tone_kind = media, playing

    def _tone_generator(self, kind: str, steps, loop: bool):
        tones = pj.ToneDescVector()
        for freq1, freq2, on_ms, off_ms in steps:
            tone = pj.ToneDesc()
            tone.freq1, tone.freq2 = freq1, freq2
            tone.on_msec, tone.off_msec = on_ms, off_ms
            tones.append(tone)
        try:
            generator = pj.ToneGenerator()
            generator.createToneGenerator()
            generator.play(tones, loop)
        except pj.Error as e:
            log.error("Could not make the %s tone: %s", kind, e.info())
            return None
        return generator

    def _ring_file(self, path: str):
        """A looping player for the user's ringtone WAV; None (so the default
        ring plays instead) if it's missing or PJSIP can't read it."""
        if not path or not Path(path).is_file():
            log.warning("Ringtone file %s is missing; using the built-in ring", path or "(none)")
            return None
        try:
            player = pj.AudioMediaPlayer()
            player.createPlayer(path)   # options 0 = loop
        except pj.Error as e:
            log.error("Could not play ringtone %s: %s", path, e.info())
            return None
        return player

    def stop_tone(self):
        if self._tone is None:
            return
        media, self._tone, self._tone_kind = self._tone, None, None
        try:
            media.stopTransmit(self._ep.audDevManager().getPlaybackDevMedia())
            if isinstance(media, pj.ToneGenerator):
                media.stop()
        except pj.Error as e:
            log.warning("Stopping tone failed: %s", e.info())

    # --- sound devices -------------------------------------------------------
    #
    # PJSIP has one sound device pair. While a call rings we point it at the
    # ringer in speaker-only mode, so ringing never opens the microphone (that
    # would flip a Bluetooth headset into call mode). Dialing or answering
    # switches it back to the call's speaker + microphone.

    def set_audio_devices(self, audio_config: AudioConfig):
        """Switch to new speaker/mic/ringer choices. Works mid-call too:
        PJSIP reopens the sound device and the call carries on."""
        self._audio_config = audio_config
        install_alsa_config(audio_config)
        if not self._created:
            return  # start() will pick them up
        try:
            self._ep.audDevManager().refreshDevs()  # re-read our ALSA devices
        except pj.Error as e:
            log.error("Refreshing audio devices failed: %s", e.info())
        self._find_audio_devices()
        ringing, self._ring_mode = self._ring_mode, None  # force a re-apply
        if ringing:
            self._use_ring_devices()
        else:
            self._use_call_devices()
        devices = self._ep.audDevManager()
        if devices.sndIsActive():
            # PJSIP only reopens the device when its id or mode changes. Our
            # devices keep their ids (only what they point at changed), so
            # flip the mode and back to make it reopen them now.
            mode = self._snd_mode()
            try:
                devices.setSndDevMode(mode & ~pj.PJSUA_SND_DEV_NO_IMMEDIATE_OPEN)
                devices.setSndDevMode(mode)
            except pj.Error as e:
                log.error("Reopening the sound device failed: %s", e.info())

    def _set_codec_priorities(self):
        """Offer only [audio] codecs, in that order (app/sip/codecs.py)."""
        ids = [info.codecId for info in self._ep.codecEnum2()]
        priorities = codec_priorities(ids, self._audio_config.codecs)
        if not any(priorities.values()):
            log.warning("None of [audio] codecs %s exist; offering PJSIP's defaults (have: %s)",
                        self._audio_config.codecs, ", ".join(ids))
            return
        for codec_id, priority in priorities.items():
            self._ep.codecSetPriority(codec_id, priority)
        offered = sorted((p, c) for c, p in priorities.items() if p)
        log.info("Codecs offered: %s", ", ".join(c for _, c in reversed(offered)))

    def _find_audio_devices(self):
        devices = self._ep.audDevManager()
        defaults = {
            SPEAKER_DEV: DEFAULT_PLAYBACK_DEV,
            MIC_DEV: DEFAULT_CAPTURE_DEV,
            RINGER_DEV: DEFAULT_PLAYBACK_DEV,
        }
        for name, default in defaults.items():
            try:
                self._dev_ids[name] = devices.lookupDev("ALSA", name)
            except pj.Error:
                log.warning("Audio device %s not found; using the default device", name)
                self._dev_ids[name] = default
        audio = self._audio_config
        log.info("Audio devices: speaker=%s microphone=%s ringer=%s",
                 audio.speaker or "default", audio.microphone or "default",
                 audio.ringer or audio.speaker or "default")

    def _use_ring_devices(self):
        if self._ring_mode is True:
            return
        self._ring_mode = True
        devices = self._ep.audDevManager()
        try:
            # Mode first, so the microphone closes before anything reopens.
            devices.setSndDevMode(self._snd_mode())
            devices.setPlaybackDev(self._dev_ids.get(RINGER_DEV, DEFAULT_PLAYBACK_DEV))
        except pj.Error as e:
            log.error("Could not switch to the ringer device: %s", e.info())

    def _use_call_devices(self):
        if self._ring_mode is False:
            return
        self._ring_mode = False
        devices = self._ep.audDevManager()
        try:
            # Mode last: if the ringer is still open, the mic opens only once.
            devices.setPlaybackDev(self._dev_ids.get(SPEAKER_DEV, DEFAULT_PLAYBACK_DEV))
            devices.setCaptureDev(self._dev_ids.get(MIC_DEV, DEFAULT_CAPTURE_DEV))
            devices.setSndDevMode(self._snd_mode())
        except pj.Error as e:
            log.error("Could not switch to the call audio devices: %s", e.info())

    def _snd_mode(self) -> int:
        # NO_IMMEDIATE_OPEN: changing devices while idle mustn't open the mic.
        mode = pj.PJSUA_SND_DEV_NO_IMMEDIATE_OPEN
        if self._ring_mode:
            mode |= pj.PJSUA_SND_DEV_SPEAKER_ONLY
        return mode

    def _answer_with(self, call_id: int, status_code: int):
        call = self._calls.get(call_id)
        if call is None:
            return
        prm = pj.CallOpParam(True)
        prm.statusCode = status_code
        try:
            call.answer(prm)
        except pj.Error as e:
            log.warning("Answer %s on call %s failed: %s", status_code, call_id, e.info())

    def _incoming_call(self, call, invite: str):
        ci = call.getInfo()
        self._calls[ci.id] = call
        auto = classify_auto_answer(invite)
        log.info("Incoming call %s from %s%s", ci.id, ci.remoteUri,
                 f" ({auto.kind.value}, auto-answer after {auto.delay_s}s)" if auto else "")
        self.on_incoming_call(ci.id, parse_remote(ci.remoteUri), auto)

    def _call_state_changed(self, call):
        ci = call.getInfo()
        if ci.state == pj.PJSIP_INV_STATE_EARLY:
            # EARLY = provisional response (180/183): ringing, on whichever side.
            is_incoming = ci.role == pj.PJSIP_ROLE_UAS
            state = CallState.RINGING_IN if is_incoming else CallState.RINGING_OUT
        else:
            state = _CALL_STATES.get(ci.state)
        if state is None:
            return
        # lastStatusCode is the last INVITE response, so a normally ended call
        # still says 200 OK. Only codes >= 300 (busy, declined...) explain an end.
        reason = ""
        if state is CallState.ENDED and ci.lastStatusCode >= 300:
            reason = f"{ci.lastStatusCode} {ci.lastReason}"
        log.info("Call %s: %s %s", ci.id, ci.stateText, reason)
        if state is CallState.ENDED:
            # Deleting a pjsua2 Call from inside its own callback can crash,
            # so park the object and let the next poll() drop it.
            self._ended_calls.append(self._calls.pop(ci.id, call))
            self._muted_calls.discard(ci.id)
        self.on_call_state(ci.id, state, parse_remote(ci.remoteUri), reason)

    def _transfer_status(self, call, prm):
        # The PBX reports the new call's progress in NOTIFYs carrying a SIP
        # status line: 100 Trying, 180 Ringing, then 200 OK or a failure.
        # A rejected REFER (e.g. 403) arrives here as a final status too.
        final = prm.finalNotify or prm.statusCode >= 200
        log.info("Call %s transfer: %s %s%s", call.getId(), prm.statusCode, prm.reason,
                 " (final)" if final else "")
        if final:
            prm.cont = False  # no more NOTIFYs wanted
        self.on_transfer_status(call.getId(), prm.statusCode, prm.reason, final)

    def _call_media_changed(self, call, report=True):
        """Wire the call's audio to the sound device to match its state:
        call -> speaker always; microphone -> call unless muted or on hold."""
        ci = call.getInfo()
        for media in ci.media:
            if media.type != pj.PJMEDIA_TYPE_AUDIO:
                continue
            state = _MEDIA_STATES.get(media.status, MediaState.NONE)
            if state is not MediaState.NONE:
                send_mic = state is not MediaState.LOCAL_HOLD and ci.id not in self._muted_calls
                try:
                    call_audio = call.getAudioMedia(media.index)
                    devices = self._ep.audDevManager()
                    call_audio.startTransmit(devices.getPlaybackDevMedia())
                    if send_mic:
                        devices.getCaptureDevMedia().startTransmit(call_audio)
                    else:
                        devices.getCaptureDevMedia().stopTransmit(call_audio)
                except pj.Error as e:
                    log.error("Could not connect call audio: %s", e.info())
            if report:
                encrypted = state is not MediaState.NONE and self._is_encrypted(call, media.index)
                log.info("Call %s media: %s%s", ci.id, state.value, " (SRTP)" if encrypted else "")
                self.on_media_state(ci.id, state, encrypted)
            break  # one audio stream per call

    @staticmethod
    def _is_encrypted(call, media_index: int) -> bool:
        """True if the negotiated audio profile is RTP/SAVP (SRTP). With SRTP
        "optional" PJSIP can encrypt over plain RTP/AVP; that reads as False,
        which errs on the safe side for the lock icon."""
        try:
            return bool(call.getStreamInfo(media_index).proto & pj.PJMEDIA_TP_PROFILE_SRTP)
        except pj.Error:
            return False

    def poll(self):
        """Process pending SIP events. Call every few milliseconds."""
        if not self._created:
            return  # PJSIP is gone (or never started); calling it would abort
        self._ended_calls.clear()
        self._ep.libHandleEvents(0)
        self._stop_key_tone()

    def shutdown(self):
        """Hang up, unregister and tear down PJSIP. Safe to call if never started."""
        self.stop_tone()
        if self._created:
            self._stop_key_tone(only_if_quiet=False)
        if self._created and self._calls:
            self._ep.hangupAllCalls()
        # Drop our Call objects while PJSIP still exists: a Call's destructor
        # talks to PJSIP, and after libDestroy() that aborts the app. (Hanging
        # up above ends the calls at once, parking them in _ended_calls.)
        self._calls.clear()
        self._ended_calls.clear()
        self._buddies.clear()  # same reason: their destructors call PJSIP
        if self._account is not None:
            self._account.shutdown()
            self._account = None
        if self._created:
            self._ep.libDestroy()
            self._created = False
