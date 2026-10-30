"""Deterministic natural-language intent parser.

Turns a plain-English request into a structured :class:`Intent` without any
external service. This is the guaranteed fallback used when no LLM is
configured, and it is also what makes the assistant fully testable and the
evaluation reproducible (same input always gives the same parse).

When an LLM backend is configured (see ``assistant/llm.py``) the client prefers
it, but the LLM is asked to return the same structured shape this parser
produces, so the rest of the pipeline does not care which one was used.

Owner: Khushi (assistant).

The parser never executes anything. It only decides *what the user wants*:
an action, a target device, parameters, and how confident it is. Deciding
whether the action is *allowed* is the policy engine's job, downstream.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from assistant.contract import ToolName


@dataclass
class Intent:
    """A structured request, before any policy check or execution."""

    action: str                      # a ToolName value, or "unknown"
    target: Optional[str] = None     # device name as the user referred to it
    params: Dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    confidence: float = 1.0
    parser: str = "rule_based"       # "rule_based" or "llm"
    needs_clarification: bool = False
    clarification: str = ""          # the question to ask, if any

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "target": self.target,
            "params": dict(self.params),
            "raw_text": self.raw_text,
            "confidence": self.confidence,
            "parser": self.parser,
            "needs_clarification": self.needs_clarification,
            "clarification": self.clarification,
        }


# Words that signal each action. Order matters: more specific first.
_BLOCK_WORDS = ("block", "cut off", "quarantine")
_UNBLOCK_WORDS = ("unblock", "restore", "allow again", "let back", "reconnect",
                  "remove the limit", "remove limit", "lift")
_LIMIT_WORDS = ("limit", "throttle", "cap", "slow down", "slow", "rate limit",
                "restrict bandwidth", "restrict the bandwidth")
_LIST_WORDS = ("list", "show", "what devices", "which devices", "who is on",
               "who's on", "devices", "status of the network", "connected")

_UNBAN_RE = re.compile(r"\b(unban|allow\s+\S+\s+back\s+on\s+(the\s+)?wi-?fi|let\s+\S+\s+connect\s+again)\b")
_BAN_RE = re.compile(r"\b(ban|blacklist|never\s+let|stop\s+\S+\s+(from\s+)?connecting|keep\s+\S+\s+off)\b")
_KICK_RE = re.compile(r"\b(kick|disconnect|deauth|boot\s+\S+\s+off|force\s+\S+\s+to\s+reconnect)\b")
_DURATION_RE = re.compile(r"\b(?:for\s+)?(\d+)\s*(minutes?|mins?|hours?|hrs?|h)\b")
_PERMANENT_RE = re.compile(r"\b(permanently|forever|for\s+good)\b")
_MAC_RE = re.compile(r"\b([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})\b")

# A speed like "5mbps", "5 mbit", "to 10 mb/s", "10 megabit". The optional
# leading sign lets us catch (and reject) a negative rate rather than silently
# dropping the minus.
_RATE_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*(?:mbit|mbps|mb/s|mbit/s|megabit|m)\b",
    re.IGNORECASE,
)

# A device reference. We capture the token after a preposition like
# "device X", "client X", "to X", "for X", or a bare known-looking name.
_DEVICE_AFTER_RE = re.compile(
    r"\b(?:device|client|host|machine)\s+([a-z0-9][a-z0-9_\-\.]*)",
    re.IGNORECASE,
)
_DEVICE_PREP_RE = re.compile(
    r"\b(?:to|for|on|of)\s+([a-z0-9][a-z0-9_\-\.]{1,})",
    re.IGNORECASE,
)
# A hostname-ish token (has a digit or a hyphen), e.g. lab-laptop-3, pixel-7.
_NAMELIKE_RE = re.compile(r"\b([a-z][a-z0-9]*(?:[-_][a-z0-9]+)+)\b", re.IGNORECASE)

# Words that are never device names even though they look name-like.
_STOPWORDS = {
    "mbit", "mbps", "mb", "the", "a", "an", "to", "for", "on", "of", "and",
    "please", "network", "bandwidth", "speed", "limit", "block", "unblock",
    "restore", "device", "devices", "client", "clients",
    "off", "permanently", "forever", "good", "wifi", "wi-fi",
}


def _contains(text: str, words) -> bool:
    return any(w in text for w in words)


def _extract_rate(text: str) -> Optional[float]:
    m = _RATE_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _extract_device(text: str) -> Optional[str]:
    """Best-effort extraction of the device the user is referring to."""
    mac_m = _MAC_RE.search(text)
    if mac_m:
        return mac_m.group(1).lower()
    for rx in (_DEVICE_AFTER_RE, _NAMELIKE_RE):
        m = rx.search(text)
        if m:
            candidate = m.group(1)
            if candidate.lower() not in _STOPWORDS:
                return candidate
    # Last resort: a token after a preposition that is not a stopword.
    for m in _DEVICE_PREP_RE.finditer(text):
        candidate = m.group(1)
        if candidate.lower() not in _STOPWORDS and not _RATE_RE.match(candidate):
            return candidate
    return None


def parse(text: str) -> Intent:
    """Parse one natural-language request into an :class:`Intent`.

    The parser is intentionally conservative: when the action is clear but the
    target or rate is missing, it returns ``needs_clarification=True`` with a
    question, rather than guessing. Guessing is exactly what we do not want a
    network tool to do.
    """
    raw = text.strip()
    low = raw.lower()

    if not low:
        return Intent(action="unknown", raw_text=raw, confidence=0.0,
                      needs_clarification=True,
                      clarification="Could you say what you'd like to do?")

    # 1. Decide the action.
    # Check order: unban -> unblock -> ban -> kick -> limit -> block -> list
    if _UNBAN_RE.search(low):
        action = ToolName.UNBAN_CLIENT.value
    elif _contains(low, _UNBLOCK_WORDS):
        action = ToolName.RESTORE_DEVICE.value
    elif _BAN_RE.search(low):
        action = ToolName.BAN_CLIENT.value
    elif _KICK_RE.search(low):
        action = ToolName.KICK_CLIENT.value
    elif _contains(low, _LIMIT_WORDS):
        action = ToolName.LIMIT_BANDWIDTH.value
    elif _contains(low, _BLOCK_WORDS):
        action = ToolName.BLOCK_DEVICE.value
    elif _contains(low, _LIST_WORDS):
        action = ToolName.LIST_DEVICES.value
    else:
        return Intent(
            action="unknown", raw_text=raw, confidence=0.2,
            needs_clarification=True,
            clarification=(
                "I didn't catch an action. Try: 'list devices', "
                "'limit lab-laptop-3 to 5 Mbps', 'block lab-laptop-3', "
                "or 'restore lab-laptop-3'."
            ),
        )

    # 2. list_devices needs nothing else.
    if action == ToolName.LIST_DEVICES.value:
        return Intent(action=action, raw_text=raw, confidence=0.95)

    # 3. Target device and parameters
    device = _extract_device(low)
    params: Dict[str, Any] = {}

    if action == ToolName.BAN_CLIENT.value:
        if device is None:
            return Intent(
                action=action, raw_text=raw, confidence=0.5,
                needs_clarification=True,
                clarification="Which device should I ban?",
            )
        dur_m = _DURATION_RE.search(low)
        perm_m = _PERMANENT_RE.search(low)
        if dur_m:
            qty = int(dur_m.group(1))
            unit = dur_m.group(2).lower()
            if unit.startswith("h"):
                qty *= 60
            params["minutes"] = qty
        elif perm_m:
            params["permanent"] = True
        else:
            return Intent(
                action=action, target=device, raw_text=raw, confidence=0.6,
                needs_clarification=True,
                clarification=f"Ban {device} permanently, or for how many minutes?",
            )
        return Intent(action=action, target=device, params=params, raw_text=raw, confidence=0.9)

    if action == ToolName.KICK_CLIENT.value:
        if device is None:
            return Intent(
                action=action, raw_text=raw, confidence=0.5,
                needs_clarification=True,
                clarification="Which device should I disconnect?",
            )
        return Intent(action=action, target=device, raw_text=raw, confidence=0.9)

    if action == ToolName.UNBAN_CLIENT.value:
        if device is None:
            return Intent(
                action=action, raw_text=raw, confidence=0.5,
                needs_clarification=True,
                clarification="Which device should I unban?",
            )
        return Intent(action=action, target=device, raw_text=raw, confidence=0.9)

    if action == ToolName.LIMIT_BANDWIDTH.value:
        rate = _extract_rate(low)
        # A zero or negative rate is not a valid limit; ask rather than apply.
        if rate is not None and rate <= 0:
            return Intent(action=action, target=device, raw_text=raw,
                          confidence=0.5, needs_clarification=True,
                          clarification="A limit must be a positive speed in Mbps. "
                          "What rate would you like?")
        if rate is not None:
            params["mbit"] = rate
        if device is None and rate is None:
            return Intent(action=action, raw_text=raw, confidence=0.4,
                          needs_clarification=True,
                          clarification="Which device, and what speed in Mbps?")
        if device is None:
            return Intent(action=action, params=params, raw_text=raw,
                          confidence=0.5, needs_clarification=True,
                          clarification="Which device should I limit?")
        if rate is None:
            return Intent(action=action, target=device, raw_text=raw,
                          confidence=0.6, needs_clarification=True,
                          clarification=f"What speed in Mbps for {device}?")
        return Intent(action=action, target=device, params=params,
                      raw_text=raw, confidence=0.9)

    # block / restore
    if device is None:
        verb = "block" if action == ToolName.BLOCK_DEVICE.value else "restore"
        return Intent(action=action, raw_text=raw, confidence=0.5,
                      needs_clarification=True,
                      clarification=f"Which device should I {verb}?")
    return Intent(action=action, target=device, raw_text=raw, confidence=0.9)


def available_actions() -> List[str]:
    """The action names the parser can produce (for prompts and help text)."""
    return [t.value for t in ToolName]
