"""Voicemail waiting (MWI): parse the PBX's message-summary notifications.

No PJSIP here. With MWI on, PJSIP subscribes to the "message-summary"
event for our own mailbox (RFC 3842), and the PBX sends a NOTIFY whenever
it changes. The body is a few text lines:

    Messages-Waiting: yes
    Message-Account: sip:101@192.0.2.10
    Voice-Message: 2/5 (0/0)

Voice-Message is new/old (urgent new/urgent old).
"""

import re
from dataclasses import dataclass

_VOICE = re.compile(r"^\s*Voice-Message\s*:\s*(\d+)\s*/\s*(\d+)", re.IGNORECASE | re.MULTILINE)
_WAITING = re.compile(r"^\s*Messages-Waiting\s*:\s*(yes|no)", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class Mailbox:
    known: bool = False   # False until the PBX has told us anything
    new: int = 0
    old: int = 0

    @property
    def waiting(self) -> bool:
        return self.new > 0


def parse_message_summary(body: str) -> Mailbox | None:
    """The mailbox state in a NOTIFY body, or None if it isn't one."""
    voice = _VOICE.search(body)
    waiting = _WAITING.search(body)
    if voice:
        new, old = int(voice.group(1)), int(voice.group(2))
        # Some PBXs say "yes" without counts in Voice-Message; trust the flag.
        if waiting and waiting.group(1).lower() == "yes" and new == 0:
            new = 1
        return Mailbox(True, new, old)
    if waiting:
        return Mailbox(True, 1 if waiting.group(1).lower() == "yes" else 0, 0)
    return None


def sip_body(message: str) -> str:
    """The body of a raw SIP message (after the blank line)."""
    for separator in ("\r\n\r\n", "\n\n"):
        if separator in message:
            return message.split(separator, 1)[1]
    return message
