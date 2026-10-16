"""Sampling progress shared by the engine adapters; ordinary status text stays valid."""
import re

_STEP = re.compile(r"(?:·\s*|step\s+)(\d{1,8})\s*/\s*(\d{1,8})(?:\D|$)", re.I)


def sampling_fraction(message):
    if not isinstance(message, str):
        return None
    match = _STEP.search(message[-256:])
    if not match:
        return None
    value, total = map(int, match.groups())
    return min(1.0, value / total) if 0 < total and 0 <= value <= total else None
