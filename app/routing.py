"""Call routing by status: where the PBX sends my calls for each presence status.

No Qt or PJSIP here. The routing lives on the PBX, so it works for every
phone on the extension and keeps working while this app is closed. The app
sets it with FusionPBX's standard feature codes, dialed quietly:
  *78        Do Not Disturb on: callers go to voicemail (also turns forwarding off)
  *79        Do Not Disturb off
  *72<n>     forward every call to n (also turns DND off)
  *73        forwarding off
Each code moves straight from one route to another, so a status change
needs at most one code, as long as we remember what we set last.

A route is a short string, as saved in settings.toml:
  "ring"               ring this extension as normal
  "voicemail"          straight to voicemail
  "forward:5551234"    forward to that number
"""

import re

RING = "ring"
VOICEMAIL = "voicemail"
FORWARD_PREFIX = "forward:"

DND_ON = "*78"
DND_OFF = "*79"
FORWARD_ON = "*72"
FORWARD_OFF = "*73"

# FusionPBX's call-forward dialplan only matches *72 followed by 2-20 digits.
_FORWARD_NUMBER = re.compile(r"\d{2,20}")


def forward(number: str) -> str:
    return FORWARD_PREFIX + number


def forward_number(route: str) -> str:
    """The number of a forward route, else ""."""
    return route[len(FORWARD_PREFIX):] if route.startswith(FORWARD_PREFIX) else ""


def is_valid_forward_number(number: str) -> bool:
    return _FORWARD_NUMBER.fullmatch(number) is not None


def is_valid(route: str) -> bool:
    if route in (RING, VOICEMAIL):
        return True
    return route.startswith(FORWARD_PREFIX) and is_valid_forward_number(forward_number(route))


def route_for(rules: dict[str, str], status: str) -> str:
    """The route a status asks for; statuses without a rule ring as normal."""
    return rules.get(status, RING)


def code_for(target: str, applied: str) -> str | None:
    """The feature code that changes the PBX from `applied` to `target`, or
    None if it's already there."""
    if target == applied:
        return None
    if target == VOICEMAIL:
        return DND_ON
    if target.startswith(FORWARD_PREFIX):
        return FORWARD_ON + forward_number(target)
    # Back to ringing: undo whichever was on.
    return DND_OFF if applied == VOICEMAIL else FORWARD_OFF


def route_text(route: str) -> str:
    """A line for the main window: where calls go now."""
    if route == VOICEMAIL:
        return "Calls go to voicemail"
    if route.startswith(FORWARD_PREFIX):
        return f"Calls forward to {forward_number(route)}"
    return "Calls ring here"
