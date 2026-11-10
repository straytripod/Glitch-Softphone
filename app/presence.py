"""Presence: the status I pick, and what the PBX says about colleagues.

No Qt or PJSIP here. My status goes to the PBX as a SIP PUBLISH (PIDF): the
label travels as the note, so colleagues see exactly "At a Meeting", plus an
RPID activity (away/busy) for phones that only show an icon. FreeSWITCH
passes it on to everyone subscribed to the extension's presence. The status
is display only: calls still ring as normal.
"""

import re
from dataclasses import dataclass
from enum import Enum


class Activity(Enum):
    NONE = "none"     # plain "available"
    AWAY = "away"
    BUSY = "busy"


# The Allworx Interact list (see the user guide in the project), in its order.
STATUSES = (
    ("In Office", Activity.NONE),
    ("Busy", Activity.BUSY),
    ("Away", Activity.AWAY),
    ("At a Meeting", Activity.AWAY),
    ("On Business Trip", Activity.AWAY),
    ("On Vacation", Activity.AWAY),
    ("At Home", Activity.AWAY),
)
STATUS_NAMES = tuple(name for name, _ in STATUSES)
DEFAULT_STATUS = "In Office"


def activity_for(status: str) -> Activity:
    return dict(STATUSES).get(status, Activity.NONE)


@dataclass(frozen=True)
class Presence:
    """What the PBX last said about one extension."""
    online: bool | None = None   # None = no report yet
    note: str = ""               # e.g. "At a Meeting", "Available"


# Notes that add nothing under a lamp: FreeSWITCH's default for a registered
# phone, our own default, and call states the lamp already shows.
_PLAIN_NOTES = {"", "available", "online", "open", "in office", "on the phone"}
# FreeSWITCH's notes during a call: "Ring 103", "Call 101", "Talk 101" (the
# other party's number). Only a number may follow, so a status someone typed,
# like "Call me after 3", still shows.
_CALL_NOTE = re.compile(r"(ring|call|talk)(\s+[+*#\d]+)?", re.IGNORECASE)


def presence_text(presence: Presence) -> str:
    """The status line under a lamp; empty when there's nothing to add."""
    if presence.online is None:
        return ""
    if not presence.online:
        return "offline"
    note = presence.note.strip()
    if note.lower() in _PLAIN_NOTES or _CALL_NOTE.fullmatch(note):
        return ""
    return note
