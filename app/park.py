"""Call park: FusionPBX "valet park" slots, worked out from their lamps.

No Qt or PJSIP here. FusionPBX numbers its park slots 5901-5999:
  transfer a call to *5901   parks it in slot 5901
  dial *5901                 picks up the call parked there
  watch park+*5901           a lamp that is lit while the slot is in use
Parking onto a slot that's already taken rings the call back to whoever
parked it, so a wrong guess never loses a call.
"""

from app.sip.blf import BlfState, BlfStatus


def slot_numbers(first_slot: int, slots: int) -> list[str]:
    return [str(first_slot + i) for i in range(slots)]


def dial_code(slot: str) -> str:
    return f"*{slot}"


def lamp_uri(slot: str) -> str:
    return f"park+*{slot}"


def is_occupied(status: BlfStatus) -> bool:
    # A parked call can show as ringing or busy depending on the PBX version.
    return status.state in (BlfState.BUSY, BlfState.RINGING)


def free_slot(statuses: dict[str, BlfStatus]) -> str | None:
    """The slot to park on: the first known-free one, else the first whose
    lamp hasn't reported yet (the PBX bounces it back if taken), else None."""
    for wanted in (BlfState.IDLE, BlfState.UNKNOWN):
        for slot, status in statuses.items():
            if status.state is wanted:
                return slot
    return None
