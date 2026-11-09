"""Busy lamp field: turn a dialog-event NOTIFY into idle / ringing / busy.

No pjsua2 or Qt here. The PBX sends RFC 4235 "dialog-info" XML listing the
watched extension's calls (dialogs). FreeSWITCH examples:

  idle:    <dialog-info ... entity="sip:102@pbx"></dialog-info>
  busy:    <dialog id=".." direction="initiator"><state>confirmed</state>
             <remote><identity display="Bob">sip:103@pbx</identity> ...
  ringing: <dialog id=".." direction="recipient"><state>early</state> ...
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum

from app.sip.calls import Remote, parse_remote

_NS = "{urn:ietf:params:xml:ns:dialog-info}"
# Dialog states before the call is answered (RFC 4235 section 3.7.1).
_SETTING_UP = {"trying", "proceeding", "early"}


class BlfState(Enum):
    UNKNOWN = "unknown"   # not subscribed (yet), or the PBX refused
    IDLE = "idle"
    RINGING = "ringing"   # a call is ringing *at* the extension: can be picked up
    BUSY = "busy"         # on a call, or dialing out


@dataclass(frozen=True)
class BlfStatus:
    state: BlfState = BlfState.UNKNOWN
    remote: Remote | None = None   # who they're on a call with / who's calling them


def parse_dialog_info(xml_text: str) -> BlfStatus:
    """Map a dialog-info document to a lamp state.

    Ringing wins over busy (a desk phone flashes a ringing line even if that
    person is already on another call, so you can pick it up). Terminated
    dialogs are ignored. Unparseable XML gives UNKNOWN.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return BlfStatus(BlfState.UNKNOWN)
    ringing, busy = [], []   # remotes of matching dialogs (None if unknown)
    for dialog in root.iter(f"{_NS}dialog"):
        state = (dialog.findtext(f"{_NS}state") or "").strip().lower()
        if state == "terminated" or not state:
            continue
        if state in _SETTING_UP and dialog.get("direction") == "recipient":
            ringing.append(_remote_of(dialog))
        else:
            busy.append(_remote_of(dialog))
    if ringing:
        return BlfStatus(BlfState.RINGING, ringing[0])
    if busy:
        return BlfStatus(BlfState.BUSY, busy[0])
    return BlfStatus(BlfState.IDLE)


def _remote_of(dialog) -> Remote | None:
    """The other party of a dialog, or None if the PBX didn't say."""
    identity = dialog.find(f"{_NS}remote/{_NS}identity")
    if identity is None or not (identity.text or "").strip():
        return None
    display = identity.get("display", "")
    return parse_remote(f'"{display}" <{identity.text.strip()}>')


def notify_body(whole_msg: str) -> str | None:
    """The dialog-info body of a raw NOTIFY message, or None if the message
    isn't a NOTIFY carrying dialog-info (e.g. the 202 to our SUBSCRIBE)."""
    if not whole_msg.startswith("NOTIFY "):
        return None
    head, sep, body = whole_msg.partition("\r\n\r\n")
    if not sep or "application/dialog-info+xml" not in head.lower():
        return None
    return body
