"""Input validation helpers.

Everything the AI (or a hostname on the network) hands us is untrusted text.
These helpers turn raw values into safe, typed values, or into ``None``.
They never raise on bad input and never echo untrusted text back unsanitised.
"""

from __future__ import annotations

import ipaddress
import math
import re
from typing import Any, Optional, Tuple

# Longest raw identifier we will even look at (before the stricter patterns).
MAX_RAW_NAME_LEN = 128

_IPV4_SHAPE = re.compile(r"^[0-9]{1,3}(\.[0-9]{1,3}){3}$")
_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")

KIND_NAME = "name"
KIND_IP = "ip"
KIND_MAC = "mac"


def normalize_device_name(raw: Any) -> Optional[str]:
    """Trim and lower-case an identifier. Return None if it is not usable text."""
    if not isinstance(raw, str):
        return None
    if len(raw) > MAX_RAW_NAME_LEN:
        return None
    name = raw.strip().lower()
    return name or None


def classify_identifier(raw: Any, name_pattern) -> Optional[Tuple[str, str]]:
    """Return ``(kind, normalised value)`` for a client identifier, or None.

    kind is "ip", "mac" or "name". Dashed MACs (aa-bb-...) are normalised to
    the colon form used everywhere else.
    """
    value = normalize_device_name(raw)
    if value is None:
        return None
    if _IPV4_SHAPE.match(value):
        try:
            return KIND_IP, str(ipaddress.IPv4Address(value))
        except ValueError:
            return None
    mac = value.replace("-", ":")
    if _MAC_RE.match(mac):
        return KIND_MAC, mac
    if name_pattern.fullmatch(value):
        return KIND_NAME, value
    return None


def parse_kbit(raw: Any) -> Optional[int]:
    """Return a whole number of kbit/s, or None.

    Rejects booleans (``True`` is an ``int`` in Python and would pass as 1),
    strings, NaN, infinity and non-integral floats.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if isinstance(raw, float):
        if math.isnan(raw) or math.isinf(raw) or raw != int(raw):
            return None
    return int(raw)


def parse_mbit(raw: Any) -> Optional[float]:
    """Return a finite float in Mbit/s (legacy ``mbit`` argument), or None."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def mbit_to_kbit(mbit: float) -> int:
    return int(round(mbit * 1000))


def parse_minutes(raw: Any) -> Optional[int]:
    """Return a whole number of minutes, or None (bools and fractions rejected)."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if isinstance(raw, float) and (math.isnan(raw) or math.isinf(raw) or raw != int(raw)):
        return None
    return int(raw)


def clean_for_log(value: Any, limit: int = 64) -> str:
    """Make any value safe to store in a log line.

    Control characters and non-ASCII become '?', and the text is truncated, so a
    hostile hostname cannot forge log lines or bloat the log.
    """
    text = str(value)
    out = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in text)
    if len(out) > limit:
        out = out[:limit] + "..."
    return out
