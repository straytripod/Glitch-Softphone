"""Registration states, kept free of PJSIP so they can be unit tested."""

from enum import Enum


class RegState(Enum):
    UNREGISTERED = "Not registered"
    REGISTERING = "Registering…"
    REGISTERED = "Registered"
    FAILED = "Registration failed"


def reg_state_from_response(code: int, expiration: int) -> RegState:
    """Map the result of a REGISTER request to a RegState.

    code is the SIP status code (200 OK, 403 Forbidden, ...). PJSIP answers
    the 401 digest challenge itself, so a 401 reaching us means the
    credentials were rejected. PJSIP reports 408 when the registrar never
    answered and 503 when the request couldn't be sent at all.

    expiration is the lifetime the registrar granted, in seconds. A 2xx with
    expiration 0 is the answer to an un-REGISTER (logging out).
    """
    if 100 <= code < 200:
        return RegState.REGISTERING
    if 200 <= code < 300:
        return RegState.REGISTERED if expiration > 0 else RegState.UNREGISTERED
    return RegState.FAILED
