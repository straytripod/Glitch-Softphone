"""Call states and helpers, kept free of PJSIP so they can be unit tested."""

import re
from dataclasses import dataclass
from enum import Enum


class CallState(Enum):
    DIALING = "dialing"          # INVITE sent, no answer yet
    RINGING_OUT = "ringing_out"  # far end is ringing (180/183)
    RINGING_IN = "ringing_in"    # incoming call waiting for us to answer
    CONNECTING = "connecting"    # answered, waiting for the final ACK
    ACTIVE = "active"            # call is up, audio flowing
    ENDED = "ended"


class MediaState(Enum):
    NONE = "none"                # no audio (not connected yet, or failed)
    ACTIVE = "active"            # audio flowing both ways
    LOCAL_HOLD = "local_hold"    # we put the call on hold
    REMOTE_HOLD = "remote_hold"  # the other side put us on hold


DTMF_DIGITS = "0123456789*#"

# The log writer passes every PJSIP line through redact_secrets first:
# - keypad digits ("dialing DTMF 1234#") can be an agent PIN;
# - SRTP keys appear in the SDP of a SIP trace ("a=crypto:1 ... inline:KEY")
#   and in PJSIP's SRTP debug lines ("TX: AES_256_CM_HMAC_SHA1_80 key=KEY").
#   With a key, a recording of the call's packets could be decrypted.
_DTMF_LOG_RE = re.compile(r"(DTMF(?: digit id)?) [0-9A-D*#]+")
_SRTP_KEY_LOG_RE = re.compile(r"(inline:|\bkey=)[A-Za-z0-9+/=]+")


def redact_dtmf(line: str) -> str:
    return _DTMF_LOG_RE.sub(r"\1 [hidden]", line)


def redact_secrets(line: str) -> str:
    return _SRTP_KEY_LOG_RE.sub(r"\1[hidden]", redact_dtmf(line))

_SIP_USER_RE = re.compile(r"sips?:([^@;>]+)")


@dataclass(frozen=True)
class Remote:
    """The other party of a call."""
    name: str     # display name, may be empty
    number: str   # extension or number: what to dial to call back

    def __str__(self):
        if self.name and self.name != self.number:
            return f"{self.name} ({self.number})"
        return self.number


def refer_target(number: str, server: str) -> str:
    """The Refer-To of a blind transfer: "<sip:*5901@pbx>", in angle brackets
    and without ;transport. FusionPBX's park dialplan takes the parking lot's
    domain from Refer-To with a pattern that needs the closing ">" and stops
    only at a port; without them the call was parked in a lot named "park@",
    where dialing *5901 never finds it."""
    return f"<sip:{number}@{server}>"


def parse_remote(uri: str) -> Remote:
    """Split a SIP URI into display name and number.

    '"Front Desk" <sip:102@192.0.2.10>'  ->  Remote('Front Desk', '102')
    '<sip:102@192.0.2.10>'               ->  Remote('', '102')
    """
    name, bracket, rest = uri.rpartition("<")
    if not bracket:
        name, rest = "", uri
    name = name.strip().strip('"').strip()
    match = _SIP_USER_RE.search(rest)
    number = match.group(1) if match else rest.strip("<> ")
    return Remote(name, number)


def format_remote(uri: str) -> str:
    """'"Front Desk" <sip:102@192.0.2.10>'  ->  'Front Desk (102)'"""
    return str(parse_remote(uri))
