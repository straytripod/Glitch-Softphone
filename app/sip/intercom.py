"""Recognise intercom and paging calls from an incoming INVITE. No pjsua2 here.

PBXs ask a phone to answer by itself with one of these headers:

  Call-Info: <sip:pbx>;answer-after=0       (FusionPBX page.lua, many others)
  Alert-Info: Auto Answer / Ring Answer      (FusionPBX, Polycom style)
  Alert-Info: <http://x>;info=alert-autoanswer  (Cisco/Grandstream style)
  Answer-Mode: Auto                          (RFC 5373)

FusionPBX sends the same headers for one-way pages and two-way intercom
calls, so we use a convention set on the PBX: the word "intercom" in Call-Info
or Alert-Info marks an intercom call; every other auto-answer request is a
page. FreeSWITCH drops an Alert-Info header that page.lua tries to add, so on
FusionPBX the marker goes in Call-Info:

  Call-Info: <sip:pbx>;answer-after=0;intercom

See docs/FUSIONPBX.md for the dialplan change.
"""

import re
from dataclasses import dataclass
from enum import Enum

_ANSWER_AFTER_RE = re.compile(r"answer-after\s*=\s*(\d+)", re.IGNORECASE)
_AUTO_ALERTS = ("auto answer", "auto-answer", "autoanswer", "ring answer", "intercom")


class AutoKind(Enum):
    INTERCOM = "intercom"   # one handset, two-way: mic on
    PAGE = "page"           # one-way announcement: mic muted, on the ringer


@dataclass(frozen=True)
class AutoAnswer:
    kind: AutoKind
    delay_s: int = 0        # the PBX's answer-after, in seconds


def _headers(whole_msg: str) -> list[tuple[str, str]]:
    """(lower-case name, value) pairs from a raw SIP message's header part."""
    head = whole_msg.split("\r\n\r\n", 1)[0]
    pairs = []
    for line in head.split("\r\n")[1:]:   # skip the request line
        name, sep, value = line.partition(":")
        if sep:
            pairs.append((name.strip().lower(), value.strip()))
    return pairs


def classify_auto_answer(whole_msg: str) -> AutoAnswer | None:
    """AutoAnswer if the INVITE asks to be answered automatically, else None."""
    markers, delay, auto = [], 0, False   # markers: header values that may say "intercom"
    for name, value in _headers(whole_msg):
        if name == "call-info":
            match = _ANSWER_AFTER_RE.search(value)
            if match:
                auto, delay = True, int(match.group(1))
                markers.append(value.lower())
        elif name == "alert-info":
            text = value.lower()
            markers.append(text)
            if "alert-autoanswer" in text or any(word in text for word in _AUTO_ALERTS):
                auto = True
        elif name == "answer-mode" and value.lower().startswith("auto"):
            auto = True
    if not auto:
        return None
    kind = AutoKind.INTERCOM if any("intercom" in m for m in markers) else AutoKind.PAGE
    return AutoAnswer(kind, delay)
