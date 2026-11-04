"""Minimal, safe YAML-subset reader used when PyYAML is not installed.

The project promises that core modules and unit tests run on the standard
library alone (README, section 2). The policy files only use a small part of
YAML, so this module reads exactly that part and rejects everything else:

    key: value                 block mappings (2-space indentation)
    - item                     block sequences
    [a, b, "c"]                flow sequences of scalars
    {k: v, k2: [a, b]}         flow mappings (one level, values may be flow lists)
    "str" / 'str' / plain      strings
    1, 2.5, true, false, null  scalars
    # comment                  comments

Anchors, tags, multi-line strings and multiple documents are not supported and
raise ``ValueError``. PyYAML is always preferred when it is available.
"""

from __future__ import annotations

import re
from typing import Any, List, Tuple

_INT_RE = re.compile(r"^[-+]?[0-9]+$")
_FLOAT_RE = re.compile(r"^[-+]?([0-9]+\.[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?$")


def _strip_comment(line: str) -> str:
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _split_top(text: str, sep: str = ",") -> List[str]:
    """Split on ``sep`` outside quotes and brackets."""
    parts, depth, quote, cur = [], 0, None, []
    for ch in text:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if quote or depth:
        raise ValueError(f"unbalanced quotes or brackets in {text!r}")
    tail = "".join(cur).strip()
    if tail or parts:
        parts.append(tail)
    return parts


def _split_key(text: str) -> Tuple[str, str]:
    """Split ``key: value`` at the first colon outside quotes."""
    quote = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == ":" and (i + 1 == len(text) or text[i + 1] == " "):
            key = text[:i].strip()
            if not key:
                raise ValueError(f"empty key in {text!r}")
            if len(key) >= 2 and key[0] == key[-1] and key[0] in "'\"":
                key = key[1:-1]
            return key, text[i + 1:].strip()
    raise ValueError(f"expected 'key: value', got {text!r}")


def _scalar(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    if text[0] in "'\"":
        if len(text) < 2 or text[-1] != text[0]:
            raise ValueError(f"unterminated string {text!r}")
        return text[1:-1]
    if text.startswith("["):
        if not text.endswith("]"):
            raise ValueError(f"unterminated list {text!r}")
        inner = text[1:-1].strip()
        return [_scalar(p) for p in _split_top(inner)] if inner else []
    if text.startswith("{"):
        if not text.endswith("}"):
            raise ValueError(f"unterminated mapping {text!r}")
        inner = text[1:-1].strip()
        out = {}
        for part in (_split_top(inner) if inner else []):
            k, v = _split_key(part)
            out[k] = _scalar(v)
        return out
    if text[0] in "&*!|>%@`:,?-]}" and text not in ("-",) and not _INT_RE.match(text) \
            and not _FLOAT_RE.match(text):
        raise ValueError(f"unsupported YAML feature in {text!r}")
    if ": " in text or text.endswith(":"):
        raise ValueError(f"unexpected ':' in plain value {text!r}")
    low = text.lower()
    if low in ("null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if _INT_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text):
        return float(text)
    return text


def safe_load(content: str) -> Any:
    lines: List[Tuple[int, str]] = []
    for raw in content.splitlines():
        if raw.strip() in ("---", "..."):
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ValueError("tabs are not allowed for indentation")
        text = _strip_comment(raw)
        if text.strip():
            lines.append((len(text) - len(text.lstrip(" ")), text.strip()))
    if not lines:
        return None
    value, pos = _block(lines, 0, lines[0][0])
    if pos != len(lines):
        raise ValueError(f"unexpected content: {lines[pos][1]!r}")
    return value


def _block(lines: List[Tuple[int, str]], pos: int, indent: int) -> Tuple[Any, int]:
    if lines[pos][1].startswith("- ") or lines[pos][1] == "-":
        return _sequence(lines, pos, indent)
    return _mapping(lines, pos, indent)


def _child(lines, pos, indent, rest):
    """Value after 'key:' or '- ': inline scalar, or a nested block below."""
    if rest:
        return _scalar(rest), pos
    if pos < len(lines) and lines[pos][0] > indent:
        return _block(lines, pos, lines[pos][0])
    # A sequence may sit at the same indent as its parent key.
    if pos < len(lines) and lines[pos][0] == indent and lines[pos][1].startswith("-"):
        return _sequence(lines, pos, indent)
    return None, pos


def _mapping(lines, pos, indent):
    out = {}
    while pos < len(lines) and lines[pos][0] == indent and not lines[pos][1].startswith("- "):
        key, rest = _split_key(lines[pos][1])
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key], pos = _child(lines, pos + 1, indent, rest)
    if pos < len(lines) and lines[pos][0] > indent:
        raise ValueError(f"bad indentation at {lines[pos][1]!r}")
    return out, pos


def _sequence(lines, pos, indent):
    out = []
    while pos < len(lines) and lines[pos][0] == indent and (
            lines[pos][1].startswith("- ") or lines[pos][1] == "-"):
        rest = lines[pos][1][1:].strip()
        if rest and not rest.startswith(("{", "[", "'", '"')) and re.match(r"^[^:]+:( |$)", rest):
            raise ValueError("block mappings inside list items are not supported; use {k: v}")
        value, pos = _child(lines, pos + 1, indent, rest)
        out.append(value)
    return out, pos
