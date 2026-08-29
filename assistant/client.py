"""
assistant/client.py
====================
Intelligent Network Assistant — Orchestration & Integration layer.
Owner: Ananya (AI Assistant, Intent Parsing, Orchestration & Integration)

WHAT THIS FILE DOES
--------------------
1. Accepts a natural-language network command from a human ("Block client1",
   "Limit client1 to 5 Mbps", "Unblock client2", "Block management_server").
2. Parses it into a structured Intent (action / target / params) using an
   LLM (via LiteLLM, Anthropic, or OpenAI SDKs — whichever is installed and
   configured) with a deterministic regex-based parser as a guaranteed
   fallback.
3. Sends the Intent to Khushi's Policy Engine (`policy/policy_engine.py`)
   for an ALLOW/DENY decision.
4. If ALLOWed, executes the action against Vedant's MCP server
   (`mcp_server/tools.py` / `mcp_server/server.py`).
5. Triggers Dhruv's validation layer (`validation/monitor.py`) to confirm
   the network actually changed state (ping/iperf3-style check).
6. Prints a clean, professional report to the terminal using `rich`.

STANDALONE / MOCK MODE
-----------------------
Every teammate's module is imported defensively. If a module isn't on the
PYTHONPATH yet (because Khushi/Vedant/Dhruv haven't pushed their code, or
you're running this file in isolation), this script transparently falls
back to a built-in, in-memory mock so you (Ananya) can develop and demo the
full end-to-end flow right now. A small banner at startup tells you which
mode each subsystem is running in.

USAGE
-----
    # Interactive conversational loop
    python -m assistant.client

    # Single command, non-interactive (good for scripting / CI demos)
    python -m assistant.client "Block client1"
    python -m assistant.client "Limit client1 to 5 Mbps"

See the "Learning & Reference Guide" delivered alongside this file for a
deep dive into MCP client calling, intent extraction, and integration
points.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import random
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# 0. TERMINAL OUTPUT — rich with a graceful plain-text fallback
# ---------------------------------------------------------------------------
# We don't want the assistant to crash just because `rich` isn't installed
# on someone's machine, so we build a tiny shim with the same surface we
# use (console.print, console.rule, Table, Panel) that degrades to plain
# print() calls when rich is unavailable.
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import Prompt
    from rich.text import Text
    from rich import box

    RICH_AVAILABLE = True
    console = Console()
except ImportError:  # pragma: no cover - exercised only without rich installed
    RICH_AVAILABLE = False

    class _PlainConsole:
        """Minimal drop-in replacement for rich.console.Console."""

        def print(self, *args, **kwargs):
            # Strip rich markup like [bold red]...[/] for plain terminals.
            text = " ".join(str(a) for a in args)
            text = re.sub(r"\[/?[a-zA-Z0-9 _#]*\]", "", text)
            print(text)

        def rule(self, title: str = ""):
            print("-" * 10 + f" {title} " + "-" * 10)

    class Panel:  # type: ignore
        @staticmethod
        def fit(text, **kwargs):
            title = kwargs.get("title", "")
            return f"\n[{title}]\n{text}\n"

    class Table:  # type: ignore
        def __init__(self, *args, **kwargs):
            self.rows = []
            self.cols = []

        def add_column(self, name, **kwargs):
            self.cols.append(name)

        def add_row(self, *vals):
            self.rows.append(vals)

        def __str__(self):
            out = [" | ".join(self.cols)]
            for r in self.rows:
                out.append(" | ".join(str(v) for v in r))
            return "\n".join(out)

    class Prompt:  # type: ignore
        @staticmethod
        def ask(msg, default=None):
            resp = input(f"{re.sub(r'[][]', '', msg)}: ")
            return resp or default

    console = _PlainConsole()


# ---------------------------------------------------------------------------
# 1. STRUCTURED INTENT SCHEMA
# ---------------------------------------------------------------------------
class ActionType(str, Enum):
    """The set of actions this assistant understands. Keep in sync with
    Khushi's rules.yaml action names and Vedant's tool function names."""

    BLOCK_CLIENT = "block_client"
    UNBLOCK_CLIENT = "unblock_client"
    LIMIT_BANDWIDTH = "limit_bandwidth"
    GET_STATUS = "get_status"
    UNKNOWN = "unknown"


@dataclass
class Intent:
    """The structured object every downstream module (policy engine, MCP
    tools, validator) operates on. This is the contract between Ananya's
    parsing layer and the rest of the team's code."""

    action: ActionType
    target: str
    params: Dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    confidence: float = 1.0
    parser_used: str = "regex"  # "llm" or "regex", useful for debugging

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "target": self.target,
            "params": self.params,
            "raw_text": self.raw_text,
            "confidence": self.confidence,
            "parser_used": self.parser_used,
        }


@dataclass
class PolicyDecision:
    """Returned by Khushi's Policy Engine (or the mock)."""

    allowed: bool
    reason: str
    rule_id: Optional[str] = None


@dataclass
class ExecutionResult:
    """Returned by Vedant's MCP tool layer (or the mock)."""

    success: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationResult:
    """Returned by Dhruv's validation monitor (or the mock)."""

    success: bool
    summary: str
    metrics: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 2. INTEGRATION LOADERS — try the real teammate modules, else fall back
# ---------------------------------------------------------------------------
# Each of these blocks tries to import a real module from the team's
# codebase. If it's missing (ModuleNotFoundError is a subclass of
# ImportError, so `except ImportError` catches both), we record that we're
# in mock mode for that subsystem and instantiate our built-in stand-in.
#
# This is the single place to update once teammates publish real modules —
# nothing else in this file needs to change as long as the real modules
# expose an equivalent interface (see the mock classes below for the
# expected method signatures).

INTEGRATION_STATUS: Dict[str, str] = {}  # subsystem -> "real" | "mock"


# --- 2a. Khushi's Policy Engine -------------------------------------------
# Khushi's real policy/policy_engine.py exposes a module-level function
# `check_policy(action: str, params: dict) -> PolicyResult` (with
# `.allowed` / `.reason` attributes) rather than a `PolicyEngine` class.
# We probe for both shapes so this keeps working automatically whichever
# one ends up being the final interface.
try:
    from policy.policy_engine import PolicyEngine as _RealPolicyEngineClass  # type: ignore
except ImportError:
    _RealPolicyEngineClass = None

try:
    from policy.policy_engine import check_policy as _real_check_policy_fn  # type: ignore
except ImportError:
    _real_check_policy_fn = None

if _RealPolicyEngineClass is not None:
    INTEGRATION_STATUS["policy_engine"] = "real (class)"
elif _real_check_policy_fn is not None:
    INTEGRATION_STATUS["policy_engine"] = "real (function adapter)"
else:
    INTEGRATION_STATUS["policy_engine"] = "mock"


class MockPolicyEngine:
    """
    Stand-in for Khushi's policy/policy_engine.py.

    Expected real interface (please confirm with Khushi):
        class PolicyEngine:
            def evaluate(self, intent: dict) -> PolicyDecision-like object
                with `.allowed: bool` and `.reason: str`

    This mock implements a few sensible guardrails so the demo behaves
    realistically:
      - Protected infrastructure targets can never be blocked.
      - Bandwidth limits must be within a sane 1-1000 Mbps range.
      - Everything else is allowed.
    """

    PROTECTED_TARGETS = {"management_server", "gateway", "router", "dns_server"}

    def evaluate(self, intent: Intent) -> PolicyDecision:
        target = intent.target.lower()

        if intent.action == ActionType.BLOCK_CLIENT and target in self.PROTECTED_TARGETS:
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"'{intent.target}' is a protected infrastructure host and "
                    "cannot be blocked. (mock-rule: protect-critical-infra)"
                ),
                rule_id="protect-critical-infra",
            )

        if intent.action == ActionType.LIMIT_BANDWIDTH:
            rate = intent.params.get("rate_mbps")
            if rate is not None and not (1 <= rate <= 1000):
                return PolicyDecision(
                    allowed=False,
                    reason=(
                        f"Requested rate {rate} Mbps is outside the allowed "
                        "1-1000 Mbps range. (mock-rule: bandwidth-bounds)"
                    ),
                    rule_id="bandwidth-bounds",
                )

        if intent.action == ActionType.UNKNOWN:
            return PolicyDecision(
                allowed=False,
                reason="Could not confidently determine an action; refusing to act on an unknown intent.",
                rule_id="reject-unknown-intent",
            )

        return PolicyDecision(allowed=True, reason="No policy violation detected.")


class PolicyEngineFunctionAdapter:
    """
    Adapts Khushi's function-based `check_policy(action, params)` to the
    `.evaluate(intent) -> PolicyDecision` interface the orchestrator uses.

    Two translation details matter here:
      1. Khushi's `params` dict is keyed by `"client"` (and `"rate"` for
         bandwidth requests), while our Intent uses `.target` / `.params`.
      2. Her `PolicyResult` already has `.allowed` / `.reason`, so we just
         copy those into our own `PolicyDecision` dataclass.
    """

    def __init__(self, check_policy_fn) -> None:
        self._check_policy = check_policy_fn

    def evaluate(self, intent: Intent) -> PolicyDecision:
        params: Dict[str, Any] = {"client": intent.target}
        if intent.action == ActionType.LIMIT_BANDWIDTH:
            params["rate"] = intent.params.get(
                "rate", f"{intent.params.get('rate_mbps', 0):g}mbit"
            )
        result = self._check_policy(intent.action.value, params)
        return PolicyDecision(
            allowed=bool(getattr(result, "allowed", False)),
            reason=str(getattr(result, "reason", "No reason provided.")),
        )


# --- 2b. Vedant's MCP Server / Tools ---------------------------------------
try:
    from mcp_server import tools as _real_mcp_tools_module  # type: ignore

    INTEGRATION_STATUS["mcp_tools"] = "real"
except ImportError:
    _real_mcp_tools_module = None
    INTEGRATION_STATUS["mcp_tools"] = "mock"

# Also probe for the official MCP Python SDK, used if Vedant's server is
# exposed as an actual MCP server process (stdio or SSE transport) rather
# than something we can import in-process. See the Learning Guide for how
# this would be wired up for real.
try:
    import mcp  # type: ignore
    from mcp import ClientSession  # type: ignore

    MCP_SDK_AVAILABLE = True
except ImportError:
    MCP_SDK_AVAILABLE = False


class MockMCPTools:
    """
    Stand-in for Vedant's mcp_server/tools.py.

    Expected real interface (please confirm with Vedant), one async
    function per action, e.g.:
        async def block_client(target: str) -> dict
        async def unblock_client(target: str) -> dict
        async def limit_bandwidth(target: str, rate: str) -> dict
        async def get_status(target: str) -> dict

    This mock simulates the underlying Linux commands (iptables/tc) Vedant
    would actually run inside the Dockerized network namespace, and keeps
    an in-memory state table so repeated commands behave consistently
    within one process run.
    """

    def __init__(self) -> None:
        self._state: Dict[str, Dict[str, Any]] = {}

    async def block_client(self, target: str) -> Dict[str, Any]:
        await asyncio.sleep(0.3)  # simulate command latency
        cmd = f"iptables -A FORWARD -s {target} -j DROP"
        self._state[target] = {"blocked": True, "rate_limit_mbps": None}
        return {"success": True, "command": cmd, "message": f"{target} blocked at firewall."}

    async def unblock_client(self, target: str) -> Dict[str, Any]:
        await asyncio.sleep(0.3)
        cmd = f"iptables -D FORWARD -s {target} -j DROP"
        state = self._state.setdefault(target, {"blocked": False, "rate_limit_mbps": None})
        state["blocked"] = False
        return {"success": True, "command": cmd, "message": f"{target} unblocked."}

    async def limit_bandwidth(self, target: str, rate: str, rate_mbps: float) -> Dict[str, Any]:
        await asyncio.sleep(0.3)
        cmd = f"tc qdisc add dev veth-{target} root tbf rate {rate} burst 32kbit latency 400ms"
        state = self._state.setdefault(target, {"blocked": False, "rate_limit_mbps": None})
        state["rate_limit_mbps"] = rate_mbps
        return {
            "success": True,
            "command": cmd,
            "message": f"{target} bandwidth limited to {rate}.",
        }

    async def get_status(self, target: str) -> Dict[str, Any]:
        await asyncio.sleep(0.1)
        state = self._state.get(target, {"blocked": False, "rate_limit_mbps": None})
        return {"success": True, "command": f"tc -s qdisc show dev veth-{target}", "state": state}


class MCPToolsAdapter:
    """
    Adapts Vedant's real mcp_server/tools.py to the interface the
    orchestrator expects. Two gaps to bridge:

      1. His functions signal outcome with `{"status": "success"/"failure"}`
         rather than a boolean `{"success": ...}` key. Without this adapter,
         `_execute()`'s generic dict-normalization would default a missing
         "success" key to True — silently reporting failed iptables/tc
         commands as successful. We translate the key here so that never
         happens.
      2. He hasn't implemented `get_status()` yet. Rather than crash with
         an AttributeError (or worse, silently pretend to succeed), we
         return a clear, honest failure explaining the feature is pending.

    His functions are plain sync functions (not async), which the
    orchestrator already supports via `_maybe_await`.
    """

    def __init__(self, tools_module) -> None:
        self._tools = tools_module

    @staticmethod
    def _normalize(raw: Dict[str, Any]) -> Dict[str, Any]:
        success = raw.get("status") == "success"
        return {**raw, "success": success}

    def block_client(self, target: str) -> Dict[str, Any]:
        return self._normalize(self._tools.block_client(target))

    def unblock_client(self, target: str) -> Dict[str, Any]:
        return self._normalize(self._tools.unblock_client(target))

    def limit_bandwidth(self, target: str, rate: str, rate_mbps: Optional[float] = None) -> Dict[str, Any]:
        # Vedant's limit_bandwidth only takes (client, rate); rate_mbps is
        # accepted here purely so `_execute()`'s signature probing can call
        # this adapter the same way it would call a richer implementation.
        return self._normalize(self._tools.limit_bandwidth(target, rate))

    def get_status(self, target: str) -> Dict[str, Any]:
        if hasattr(self._tools, "get_status"):
            return self._normalize(self._tools.get_status(target))
        return {
            "success": False,
            "status": "failure",
            "message": (
                f"get_status for '{target}' is not implemented yet in "
                "mcp_server/tools.py — ask Vedant to add it."
            ),
        }


# --- 2c. Dhruv's Validation Monitor ----------------------------------------
# Dhruv's real validation/monitor.py exposes free functions
# (check_block / check_unblock / check_bandwidth) rather than a
# ValidationMonitor class, so — same as the policy engine — we probe for
# both shapes.
try:
    from validation.monitor import ValidationMonitor as _RealValidationMonitorClass  # type: ignore
except ImportError:
    _RealValidationMonitorClass = None

try:
    from validation import monitor as _real_monitor_module  # type: ignore

    _has_function_validation = hasattr(_real_monitor_module, "check_block") and hasattr(
        _real_monitor_module, "check_unblock"
    )
except ImportError:
    _real_monitor_module = None
    _has_function_validation = False

if _RealValidationMonitorClass is not None:
    INTEGRATION_STATUS["validation_monitor"] = "real (class)"
elif _has_function_validation:
    INTEGRATION_STATUS["validation_monitor"] = "real (function adapter)"
else:
    INTEGRATION_STATUS["validation_monitor"] = "mock"


class MockValidationMonitor:
    """
    Stand-in for Dhruv's validation/monitor.py.

    Expected real interface (please confirm with Dhruv):
        class ValidationMonitor:
            async def validate(self, target: str, action: str) -> dict
                (would actually shell out to ping / iperf3 inside the
                Docker network namespaces set up in network/)

    This mock produces plausible ping-style metrics that agree with the
    action just taken (e.g. a blocked client shows 100% packet loss).
    """

    async def validate(
        self, target: str, action: ActionType, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        await asyncio.sleep(0.4)  # simulate ping round-trip time
        if action == ActionType.BLOCK_CLIENT:
            return {
                "success": True,  # validation *ran* successfully...
                "reachable": False,  # ...and confirms the client is unreachable, as expected
                "packet_loss_pct": 100.0,
                "avg_latency_ms": None,
                "summary": f"{target} is unreachable (expected after block).",
            }
        if action == ActionType.UNBLOCK_CLIENT:
            latency = round(random.uniform(0.4, 4.0), 2)
            return {
                "success": True,
                "reachable": True,
                "packet_loss_pct": 0.0,
                "avg_latency_ms": latency,
                "summary": f"{target} is reachable again (avg {latency} ms).",
            }
        if action == ActionType.LIMIT_BANDWIDTH:
            latency = round(random.uniform(1.0, 6.0), 2)
            return {
                "success": True,
                "reachable": True,
                "packet_loss_pct": 0.0,
                "avg_latency_ms": latency,
                "summary": f"{target} reachable; rate limit applied (avg {latency} ms).",
            }
        # get_status doesn't need a network validation pass
        return {"success": True, "reachable": None, "summary": "No validation required for status queries."}


class ValidationMonitorAdapter:
    """
    Adapts Dhruv's real validation/monitor.py (check_block / check_unblock /
    check_bandwidth, keyed by `"passed"` + `"message"`) to the orchestrator's
    `.validate(target, action, params) -> dict` interface (keyed by
    `"success"` + `"summary"`).

    `check_bandwidth` needs the requested rate, which isn't part of the
    (target, action) pair alone — that's why `_validate()` now passes
    `intent.params` through as a third argument, and why this adapter's
    `validate()` accepts it.
    """

    def __init__(self, monitor_module) -> None:
        self._monitor = monitor_module

    def validate(
        self, target: str, action: ActionType, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        params = params or {}
        try:
            if action == ActionType.BLOCK_CLIENT:
                raw = self._monitor.check_block(target)
            elif action == ActionType.UNBLOCK_CLIENT:
                raw = self._monitor.check_unblock(target)
            elif action == ActionType.LIMIT_BANDWIDTH:
                rate = params.get("rate", f"{params.get('rate_mbps', 0):g}mbit")
                raw = self._monitor.check_bandwidth(target, rate)
            else:
                return {"success": True, "summary": "No validation required for status queries."}
        except (ValueError, RuntimeError) as exc:
            # Dhruv's functions raise ValueError for unknown clients and
            # RuntimeError for iperf3/parsing failures — turn both into a
            # clean, non-crashing validation failure instead of propagating.
            return {"success": False, "summary": f"Validation error: {exc}"}

        return {
            "success": bool(raw.get("passed", False)),
            "summary": str(raw.get("message", "No summary provided.")),
            **{k: v for k, v in raw.items() if k not in ("passed", "message")},
        }


# ---------------------------------------------------------------------------
# 3. INTENT PARSER — LLM-backed with a regex fallback that always works
# ---------------------------------------------------------------------------
LLM_SYSTEM_PROMPT = """You are an intent-extraction engine for a network \
automation assistant. Given a single natural-language instruction, output \
ONLY a JSON object (no prose, no markdown fences) with this exact shape:

{
  "action": "block_client" | "unblock_client" | "limit_bandwidth" | "get_status" | "unknown",
  "target": "<hostname or client identifier, snake_case, e.g. client1 or management_server>",
  "params": { "rate_mbps": <number, only for limit_bandwidth, omit otherwise> }
}

Rules:
- If the instruction doesn't clearly map to one of the four actions, use "unknown".
- Always normalize the target to lowercase snake_case (e.g. "Management Server" -> "management_server").
- For limit_bandwidth, extract the numeric Mbps value into params.rate_mbps.
- Output valid JSON and nothing else.
"""


def _try_litellm(user_text: str) -> Optional[str]:
    """Attempt structured parsing via LiteLLM (provider-agnostic)."""
    try:
        import litellm  # type: ignore
    except ImportError:
        return None
    model = os.environ.get("LITELLM_MODEL")
    if not model:
        return None
    try:
        resp = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            max_tokens=300,
            temperature=0,
        )
        return resp["choices"][0]["message"]["content"]
    except Exception:
        return None


def _try_anthropic(user_text: str) -> Optional[str]:
    """Attempt structured parsing via the Anthropic SDK."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic  # type: ignore
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key)
        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        resp = client.messages.create(
            model=model,
            max_tokens=300,
            system=LLM_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_text}],
        )
        # Concatenate any text blocks in the response.
        return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
    except Exception:
        return None


def _try_openai(user_text: str) -> Optional[str]:
    """Attempt structured parsing via the OpenAI SDK."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        return None
    try:
        client = OpenAI(api_key=api_key)
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=300,
            messages=[
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
        )
        return resp.choices[0].message.content
    except Exception:
        return None


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """LLMs sometimes wrap JSON in markdown fences despite instructions;
    strip those defensively before parsing."""
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Last resort: grab the first {...} block in the text.
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _normalize_target(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")


def llm_parse(text: str) -> Optional[Intent]:
    """Try each configured LLM backend in turn; return the first success."""
    for backend in (_try_litellm, _try_anthropic, _try_openai):
        raw = backend(text)
        if raw is None:
            continue
        data = _extract_json(raw)
        if not data:
            continue
        try:
            action = ActionType(data.get("action", "unknown"))
        except ValueError:
            action = ActionType.UNKNOWN
        target = _normalize_target(str(data.get("target", "")))
        params = data.get("params") or {}
        if "rate_mbps" in params and "rate" not in params:
            params["rate"] = f"{params['rate_mbps']}mbit"
        return Intent(
            action=action,
            target=target,
            params=params,
            raw_text=text,
            confidence=0.9,
            parser_used="llm",
        )
    return None


# --- Regex fallback: deterministic, dependency-free, always available -----
_BLOCK_RE = re.compile(r"\bblock\b\s+(?P<target>[a-zA-Z0-9_\- ]+)", re.IGNORECASE)
_UNBLOCK_RE = re.compile(r"\bunblock\b\s+(?P<target>[a-zA-Z0-9_\- ]+)", re.IGNORECASE)
_LIMIT_RE = re.compile(
    r"\blimit\b\s+(?P<target>[a-zA-Z0-9_\- ]+?)\s+to\s+(?P<rate>\d+(\.\d+)?)\s*mbps",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(
    r"\b(status|check)\b(\s+of)?\s+(?P<target>[a-zA-Z0-9_\- ]+)", re.IGNORECASE
)


def regex_parse(text: str) -> Intent:
    """
    Deterministic rule-based parser used when no LLM backend is available
    (or when the LLM call fails / returns malformed JSON). This guarantees
    the assistant is always usable, even fully offline.
    """
    text = text.strip()

    m = _LIMIT_RE.search(text)
    if m:
        target = _normalize_target(m.group("target"))
        rate_mbps = float(m.group("rate"))
        return Intent(
            action=ActionType.LIMIT_BANDWIDTH,
            target=target,
            params={"rate": f"{rate_mbps:g}mbit", "rate_mbps": rate_mbps},
            raw_text=text,
            confidence=0.75,
            parser_used="regex",
        )

    m = _BLOCK_RE.search(text)
    if m and "unblock" not in text.lower():
        target = _normalize_target(m.group("target"))
        return Intent(
            action=ActionType.BLOCK_CLIENT,
            target=target,
            raw_text=text,
            confidence=0.8,
            parser_used="regex",
        )

    m = _UNBLOCK_RE.search(text)
    if m:
        target = _normalize_target(m.group("target"))
        return Intent(
            action=ActionType.UNBLOCK_CLIENT,
            target=target,
            raw_text=text,
            confidence=0.8,
            parser_used="regex",
        )

    m = _STATUS_RE.search(text)
    if m:
        target = _normalize_target(m.group("target"))
        return Intent(
            action=ActionType.GET_STATUS,
            target=target,
            raw_text=text,
            confidence=0.7,
            parser_used="regex",
        )

    return Intent(
        action=ActionType.UNKNOWN,
        target="",
        raw_text=text,
        confidence=0.0,
        parser_used="regex",
    )


class IntentParser:
    """Public entry point: tries the LLM path first (if configured), and
    always has the regex parser as a safety net."""

    def parse(self, text: str) -> Intent:
        intent = llm_parse(text)
        if intent is not None and intent.action != ActionType.UNKNOWN:
            return intent
        # Either no LLM was configured, it failed, or it returned "unknown" —
        # give the regex parser a chance before giving up.
        return regex_parse(text)


# ---------------------------------------------------------------------------
# 4. ORCHESTRATOR — ties parsing -> policy -> execution -> validation
# ---------------------------------------------------------------------------
class NetworkAssistant:
    """
    The main orchestration class. This is what `main()` drives, and it's
    the piece of code that "wires together" all four teammates' modules
    (or their mocks) behind a single, stable interface.
    """

    def __init__(self) -> None:
        self.parser = IntentParser()

        # Policy engine: prefer a real PolicyEngine class if one ever ships,
        # otherwise wrap Khushi's real check_policy() function, otherwise
        # fall back to the mock. Every branch ends up exposing the same
        # `.evaluate(intent) -> PolicyDecision` interface.
        if _RealPolicyEngineClass is not None:
            self.policy_engine = _RealPolicyEngineClass()
        elif _real_check_policy_fn is not None:
            self.policy_engine = PolicyEngineFunctionAdapter(_real_check_policy_fn)
        else:
            self.policy_engine = MockPolicyEngine()

        # MCP tools: wrap Vedant's real module (normalizing its status-key
        # and missing get_status()) if importable, else mock.
        # (A real MCP-SDK network client would be constructed here too —
        # see the Learning Guide for that variant.)
        self.mcp_tools = MCPToolsAdapter(_real_mcp_tools_module) if _real_mcp_tools_module else MockMCPTools()

        # Validation monitor: prefer a real ValidationMonitor class if one
        # ever ships, otherwise wrap Dhruv's real check_block/check_unblock/
        # check_bandwidth functions, otherwise fall back to the mock.
        if _RealValidationMonitorClass is not None:
            self.validator = _RealValidationMonitorClass()
        elif _has_function_validation:
            self.validator = ValidationMonitorAdapter(_real_monitor_module)
        else:
            self.validator = MockValidationMonitor()

    # -- helpers -------------------------------------------------------
    @staticmethod
    async def _maybe_await(value):
        """Some teammates' functions may be sync, some async — support both
        transparently so we don't force an implementation detail on them."""
        if inspect.isawaitable(value):
            return await value
        return value

    async def _evaluate_policy(self, intent: Intent) -> PolicyDecision:
        # Every policy_engine variant (real class, function adapter, or
        # mock) exposes the same `.evaluate(intent) -> PolicyDecision-like`
        # interface, so we always call it the same way here.
        result = self.policy_engine.evaluate(intent)
        result = await self._maybe_await(result)
        # Normalize whatever shape the real engine returns into our
        # PolicyDecision dataclass, in case Khushi's return type differs
        # slightly (e.g. a plain dict instead of an object).
        if isinstance(result, PolicyDecision):
            return result
        if isinstance(result, dict):
            return PolicyDecision(
                allowed=bool(result.get("allowed", result.get("allow", False))),
                reason=str(result.get("reason", "No reason provided.")),
                rule_id=result.get("rule_id"),
            )
        # Fallback: assume any object with .allowed/.reason attributes.
        return PolicyDecision(
            allowed=bool(getattr(result, "allowed", False)),
            reason=str(getattr(result, "reason", "No reason provided.")),
            rule_id=getattr(result, "rule_id", None),
        )

    async def _execute(self, intent: Intent) -> ExecutionResult:
        tools = self.mcp_tools
        try:
            if intent.action == ActionType.BLOCK_CLIENT:
                raw = await self._maybe_await(tools.block_client(intent.target))
            elif intent.action == ActionType.UNBLOCK_CLIENT:
                raw = await self._maybe_await(tools.unblock_client(intent.target))
            elif intent.action == ActionType.LIMIT_BANDWIDTH:
                rate = intent.params.get("rate", f"{intent.params.get('rate_mbps', 0):g}mbit")
                rate_mbps = intent.params.get("rate_mbps")
                # Support real implementations that only take `rate`.
                sig = inspect.signature(tools.limit_bandwidth)
                if "rate_mbps" in sig.parameters:
                    raw = await self._maybe_await(tools.limit_bandwidth(intent.target, rate, rate_mbps))
                else:
                    raw = await self._maybe_await(tools.limit_bandwidth(intent.target, rate))
            elif intent.action == ActionType.GET_STATUS:
                raw = await self._maybe_await(tools.get_status(intent.target))
            else:
                return ExecutionResult(success=False, message="No executable action for unknown intent.")
        except Exception as exc:  # network tool failures shouldn't crash the CLI
            return ExecutionResult(success=False, message=f"MCP execution error: {exc}")

        if isinstance(raw, ExecutionResult):
            return raw
        if isinstance(raw, dict):
            # Prefer an explicit "success" boolean; fall back to a
            # "status" string ("success"/"failure"/"ok") if that's what a
            # teammate's module returns instead. Only default to True when
            # neither indicator is present at all — this defensive fallback
            # is what would have caught the mcp_server/tools.py status-key
            # mismatch even without the dedicated MCPToolsAdapter above.
            if "success" in raw:
                success = bool(raw["success"])
            elif "status" in raw:
                success = str(raw["status"]).strip().lower() in ("success", "ok", "true", "passed")
            else:
                success = True
            return ExecutionResult(
                success=success,
                message=str(raw.get("message", "Action completed.")),
                details={k: v for k, v in raw.items() if k not in ("success", "message")},
            )
        return ExecutionResult(success=True, message=str(raw))

    async def _validate(self, intent: Intent) -> ValidationResult:
        try:
            try:
                # Adapters (and any future real class) may want the
                # requested rate for bandwidth checks, so pass params too.
                raw = await self._maybe_await(
                    self.validator.validate(intent.target, intent.action, intent.params)
                )
            except TypeError:
                # Older/simpler validators that only accept (target, action)
                # — retry without params rather than failing the request.
                raw = await self._maybe_await(self.validator.validate(intent.target, intent.action))
        except Exception as exc:
            return ValidationResult(success=False, summary=f"Validation error: {exc}")
        if isinstance(raw, ValidationResult):
            return raw
        if isinstance(raw, dict):
            return ValidationResult(
                success=bool(raw.get("success", False)),
                summary=str(raw.get("summary", "No summary provided.")),
                metrics={k: v for k, v in raw.items() if k not in ("success", "summary")},
            )
        return ValidationResult(success=True, summary=str(raw))

    # -- the main pipeline ----------------------------------------------
    async def process_command(self, text: str) -> Dict[str, Any]:
        """Runs the full parse -> policy -> execute -> validate pipeline
        for one natural-language command, returning a report dict that the
        CLI layer renders with rich."""

        request_id = uuid.uuid4().hex[:8]
        started = time.time()

        intent = self.parser.parse(text)

        report: Dict[str, Any] = {
            "request_id": request_id,
            "raw_text": text,
            "intent": intent.to_dict(),
            "policy": None,
            "execution": None,
            "validation": None,
            "halted_at": None,
        }

        if intent.action == ActionType.UNKNOWN:
            report["halted_at"] = "parsing"
            report["error"] = (
                "Could not understand the command. Try phrasing like "
                "'Block client1', 'Unblock client2', 'Limit client1 to 5 Mbps', "
                "or 'Get status of client1'."
            )
            report["elapsed_sec"] = round(time.time() - started, 3)
            return report

        decision = await self._evaluate_policy(intent)
        report["policy"] = {"allowed": decision.allowed, "reason": decision.reason, "rule_id": decision.rule_id}

        if not decision.allowed:
            report["halted_at"] = "policy"
            report["elapsed_sec"] = round(time.time() - started, 3)
            return report

        execution = await self._execute(intent)
        report["execution"] = {
            "success": execution.success,
            "message": execution.message,
            "details": execution.details,
        }

        if not execution.success:
            report["halted_at"] = "execution"
            report["elapsed_sec"] = round(time.time() - started, 3)
            return report

        validation = await self._validate(intent)
        report["validation"] = {
            "success": validation.success,
            "summary": validation.summary,
            "metrics": validation.metrics,
        }

        report["elapsed_sec"] = round(time.time() - started, 3)
        return report


# ---------------------------------------------------------------------------
# 5. TERMINAL REPORTING
# ---------------------------------------------------------------------------
def print_startup_banner() -> None:
    mcp_note = "  [mcp SDK detected]" if MCP_SDK_AVAILABLE else ""
    if INTEGRATION_STATUS["mcp_tools"].startswith("real") and not hasattr(
        _real_mcp_tools_module, "get_status"
    ):
        mcp_note += "  [get_status pending from Vedant]"
    lines = [
        f"Policy Engine (Khushi):      {INTEGRATION_STATUS['policy_engine'].upper()}",
        f"MCP Tools (Vedant):          {INTEGRATION_STATUS['mcp_tools'].upper()}{mcp_note}",
        f"Validation Monitor (Dhruv):  {INTEGRATION_STATUS['validation_monitor'].upper()}",
    ]
    body = "\n".join(lines)
    if RICH_AVAILABLE:
        console.print(
            Panel.fit(
                body,
                title="[bold cyan]Intelligent Network Assistant — Startup[/bold cyan]",
                border_style="cyan",
            )
        )
    else:
        console.print("=== Intelligent Network Assistant — Startup ===")
        console.print(body)


def print_report(report: Dict[str, Any]) -> None:
    """Renders the full pipeline report as a readable, professional
    terminal summary."""
    intent = report["intent"]

    if RICH_AVAILABLE:
        console.rule(f"[bold]Request {report['request_id']}[/bold]")
        console.print(f"[dim]Input:[/dim] \"{report['raw_text']}\"")

        table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold magenta")
        table.add_column("Stage")
        table.add_column("Result")

        table.add_row(
            "Intent Parsing",
            f"action=[cyan]{intent['action']}[/cyan] target=[cyan]{intent['target'] or '-'}[/cyan] "
            f"params={intent['params']} (via {intent['parser_used']}, conf={intent['confidence']})",
        )

        if report.get("error"):
            table.add_row("[red]Error[/red]", report["error"])
            console.print(table)
            console.print("[bold red]HALTED[/bold red]: could not parse a valid intent.\n")
            return

        policy = report["policy"]
        policy_color = "green" if policy["allowed"] else "red"
        table.add_row(
            "Policy Check (Khushi)",
            f"[{policy_color}]{'ALLOW' if policy['allowed'] else 'DENY'}[/{policy_color}] — {policy['reason']}",
        )

        if not policy["allowed"]:
            console.print(table)
            console.print(
                Panel.fit(
                    policy["reason"],
                    title="[bold red]Blocked by Policy Engine[/bold red]",
                    border_style="red",
                )
            )
            return

        execution = report["execution"]
        exec_color = "green" if execution["success"] else "red"
        table.add_row(
            "Execution (Vedant / MCP)",
            f"[{exec_color}]{'OK' if execution['success'] else 'FAILED'}[/{exec_color}] — {execution['message']}",
        )

        if not execution["success"]:
            console.print(table)
            console.print("[bold red]HALTED[/bold red]: MCP execution failed.\n")
            return

        validation = report["validation"]
        val_color = "green" if validation["success"] else "yellow"
        table.add_row(
            "Validation (Dhruv)",
            f"[{val_color}]{'CONFIRMED' if validation['success'] else 'UNCONFIRMED'}[/{val_color}] — {validation['summary']}",
        )

        console.print(table)
        console.print(f"[dim]Completed in {report['elapsed_sec']}s[/dim]\n")
    else:
        # Plain-text fallback for environments without rich installed.
        print(f"\n--- Request {report['request_id']} ---")
        print(f'Input: "{report["raw_text"]}"')
        print(f"Intent: {intent}")
        if report.get("error"):
            print(f"ERROR: {report['error']}")
            return
        policy = report["policy"]
        print(f"Policy: {'ALLOW' if policy['allowed'] else 'DENY'} - {policy['reason']}")
        if not policy["allowed"]:
            return
        execution = report["execution"]
        print(f"Execution: {'OK' if execution['success'] else 'FAILED'} - {execution['message']}")
        if not execution["success"]:
            return
        validation = report["validation"]
        print(f"Validation: {'CONFIRMED' if validation['success'] else 'UNCONFIRMED'} - {validation['summary']}")
        print(f"Completed in {report['elapsed_sec']}s")


# ---------------------------------------------------------------------------
# 6. CLI ENTRY POINTS
# ---------------------------------------------------------------------------
async def run_single_command(assistant: NetworkAssistant, text: str) -> None:
    report = await assistant.process_command(text)
    print_report(report)


async def run_interactive(assistant: NetworkAssistant) -> None:
    console.print(
        "[bold green]Interactive mode.[/bold green] Type a command, or 'exit' / 'quit' to leave.\n"
        if RICH_AVAILABLE
        else "Interactive mode. Type a command, or 'exit' / 'quit' to leave.\n"
    )
    while True:
        try:
            text = Prompt.ask("[bold blue]network-assistant>[/bold blue]" if RICH_AVAILABLE else "network-assistant>")
        except (EOFError, KeyboardInterrupt):
            console.print("\nGoodbye.")
            break
        if text is None:
            continue
        stripped = text.strip().lower()
        if stripped in ("exit", "quit", "q"):
            console.print("Goodbye.")
            break
        if not stripped:
            continue
        report = await assistant.process_command(text)
        print_report(report)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assistant.client",
        description="Intelligent Network Assistant — natural language network configuration.",
    )
    parser.add_argument(
        "command",
        nargs="*",
        help='Optional single command to run non-interactively, e.g. "Block client1". '
        "If omitted, starts an interactive session.",
    )
    return parser


async def _amain() -> None:
    args = build_arg_parser().parse_args()
    assistant = NetworkAssistant()
    print_startup_banner()

    if args.command:
        text = " ".join(args.command)
        await run_single_command(assistant, text)
    else:
        await run_interactive(assistant)


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        console.print("\nInterrupted. Goodbye.")
        sys.exit(0)


if __name__ == "__main__":
    main()