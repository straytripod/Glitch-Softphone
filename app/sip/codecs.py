"""Codec order for calls. No pjsua2 here, so it can be unit-tested.

PJSIP offers every codec it has, most at the same priority, so PCMU often
came before G.722 in our offer. FusionPBX's default ("generous" codec
negotiation) takes the first codec in the phone's offer that it also has,
and calls ended up narrowband. So we rank the codecs we want and turn the
rest off.
"""

TOP_PRIORITY = 250   # 0 turns a codec off; PJSIP treats 255 specially, so stay below


def codec_priorities(codec_ids: list[str], wanted: list[str]) -> dict[str, int]:
    """PJSIP codec ids ("G722/16000/1") -> priority, from names in order of
    preference ("G722"). Names match the part before the first "/", ignoring
    case. Every codec not wanted gets 0."""
    rank = {name.strip().lower(): i for i, name in reversed(list(enumerate(wanted)))}
    priorities = {}
    for codec_id in codec_ids:
        i = rank.get(codec_id.split("/")[0].lower())
        priorities[codec_id] = 0 if i is None else TOP_PRIORITY - i
    return priorities
