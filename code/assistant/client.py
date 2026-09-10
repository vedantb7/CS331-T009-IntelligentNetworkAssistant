"""
assistant/client.py
====================
Intelligent Network Assistant — Orchestration & Integration layer.

WHAT THIS FILE DOES
--------------------
1. Accepts user network intent commands in natural language or direct CLI syntax.
2. Parses it into a structured Intent (action / target / params) using an
   LLM (via LiteLLM, Anthropic, or OpenAI SDKs — whichever is installed and
   configured) with a deterministic regex-based parser as a guaranteed
   fallback.
3. Sends the Intent to the Policy Engine (`policy/policy_engine.py`)
   for an ALLOW/DENY decision.
4. If ALLOWed, executes the action against the MCP server
   (`mcp_server/tools.py` / `mcp_server/server.py`).
5. Triggers the validation layer (`validation/monitor.py`) to confirm
   the network actually changed state (ping/iperf3-style check).
6. Prints a clean, professional report to the terminal using `rich`.

STANDALONE / MOCK MODE
-----------------------
Every subsystem module is imported defensively. If a module is unavailable,
this script transparently falls back to a built-in mock.
A banner at startup displays the operational mode of each subsystem.

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
try:
    # pyrefly: ignore [missing-import]
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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
    CONVERSATION = "conversation"
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

        if intent.action in (ActionType.UNKNOWN, ActionType.CONVERSATION):
            return PolicyDecision(
                allowed=False,
                reason="Conversational or unknown intent; refusing to act on network.",
                rule_id="reject-non-network-intent",
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

    async def get_status(self, target: str = "") -> Dict[str, Any]:
        await asyncio.sleep(0.1)
        target = (target or "").strip().lower()
        clients_state = {}
        for c in ("client1", "client2", "server"):
            st = self._state.get(c, {"blocked": False, "rate_limit_mbps": None})
            limit_str = f"{st['rate_limit_mbps']:g}mbit" if st.get("rate_limit_mbps") else "none"
            fw_str = "blocked" if st.get("blocked") else ("protected" if c == "server" else "unblocked")
            clients_state[c] = {
                "ip": {"client1": "172.20.0.2", "client2": "172.20.0.4", "server": "172.20.0.3"}.get(c, "172.20.0.x"),
                "status": "running",
                "reachable": not st.get("blocked", False),
                "firewall": fw_str,
                "bandwidth_limit": limit_str,
            }

        if target and target not in ("all", "network", "*", "status"):
            c_info = clients_state.get(target, {
                "ip": "unknown", "status": "unknown", "reachable": False, "firewall": "unknown", "bandwidth_limit": "none"
            })
            return {
                "success": True,
                "status": "success",
                "action": "get_status",
                "client": target,
                "network": {"name": "network_project-net", "available": True, "subnet": "172.20.0.0/24", "gateway": "172.20.0.1"},
                "server": {"name": "server", "ip": "172.20.0.3", "status": "running", "reachable": True},
                "clients": {target: c_info},
                "message": f"Client '{target}' is {'reachable' if c_info['reachable'] else 'unreachable'}. Firewall: {c_info['firewall']}. Limit: {c_info['bandwidth_limit']}.",
            }

        return {
            "success": True,
            "status": "success",
            "action": "get_status",
            "client": "all",
            "network": {"name": "network_project-net", "available": True, "subnet": "172.20.0.0/24", "gateway": "172.20.0.1"},
            "server": {"name": "server", "ip": "172.20.0.3", "status": "running", "reachable": True},
            "clients": clients_state,
            "message": f"Network 'network_project-net' active. All {len(clients_state)} hosts operational.",
        }


class MCPToolsAdapter:
    """
    Adapts mcp_server/tools.py to the interface the orchestrator expects.
    Normalizes status keys and execution results.
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
        return self._normalize(self._tools.limit_bandwidth(target, rate))

    def get_status(self, target: str = "") -> Dict[str, Any]:
        if hasattr(self._tools, "get_status"):
            return self._normalize(self._tools.get_status(target))
        return {
            "success": False,
            "status": "failure",
            "message": (
                f"get_status for '{target}' is not implemented yet in "
                "mcp_server/tools.py."
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


# --- 2d. Khushi's Audit Log -------------------------------------------
# Not strictly part of the original pipeline diagram, but Khushi built a
# small, self-contained audit trail (policy/audit_log.py) that was sitting
# unused. It has no interface mismatch to adapt — log_action() already
# takes exactly the shape the orchestrator has on hand — so we just wire
# it straight in. Entirely optional: if it's missing, the pipeline runs
# exactly as before, just without a persisted history of decisions.
try:
    from policy.audit_log import log_action as _real_log_action  # type: ignore

    INTEGRATION_STATUS["audit_log"] = "real"
except ImportError:
    _real_log_action = None
    INTEGRATION_STATUS["audit_log"] = "not connected"


def _audit(action: ActionType, params: Dict[str, Any], status: str, reason: str = "") -> None:
    """Best-effort audit logging — never let a logging problem take down
    the actual pipeline, so any failure here is swallowed silently."""
    if _real_log_action is None:
        return
    try:
        _real_log_action(action.value, params, status, reason)
    except Exception:
        pass


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

        # Dhruv's functions now return a Pydantic `ValidationResult` model
        # (attribute access: .passed, .message, ...) rather than a plain
        # dict. Normalize both shapes into a dict here so the rest of this
        # method doesn't care which one it got.
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump()  # Pydantic v2
        elif hasattr(raw, "dict") and callable(raw.dict):
            raw = raw.dict()  # Pydantic v1 fallback
        elif not isinstance(raw, dict):
            raw = vars(raw)

        return {
            "success": bool(raw.get("passed", False)),
            "summary": str(raw.get("message", "No summary provided.")),
            **{k: v for k, v in raw.items() if k not in ("passed", "message")},
        }


# ---------------------------------------------------------------------------
# 3. INTENT PARSER — LLM-backed with a regex fallback that always works
# ---------------------------------------------------------------------------
LLM_SYSTEM_PROMPT = """You are an intelligent assistant for a network automation system called INA.
You handle BOTH conversational requests and network-management requests.
Given the user's natural-language input, output ONLY a JSON object (no prose, no markdown, no code fences) with this exact shape:

{
  "action": "block_client" | "unblock_client" | "limit_bandwidth" | "get_status" | "conversation" | "unknown",
  "target": "<hostname or client identifier, snake_case, e.g. client1 or empty string>",
  "params": {
    "rate_mbps": <number, only for limit_bandwidth, omit otherwise>
  },
  "reply": "<natural conversational reply or clarification; required for conversation and unknown, optional for network actions>"
}

Classification Rules:
1. "conversation":
   - Greetings (e.g. "hello", "hi", "hey"): friendly greeting in "reply".
   - Gratitude/Praise (e.g. "thanks", "thank you", "great work", "good job"): warm, helpful acknowledgment in "reply".
   - Capabilities (e.g. "what can you do?", "who are you?", "help me"): explain available capabilities (blocking/unblocking clients, limiting bandwidth, viewing live network status, and running help) in "reply".
   - Farewells (e.g. "bye", "goodbye", "see you"): friendly goodbye in "reply".
   - For conversational requests, set "target": "" and write the natural response in "reply".

2. Network management actions:
   - "block_client": Isolating or blocking network access for a client (e.g. "block client1", "cut client1 off", "kick client1 off wifi"). Set "target" to the client name (lowercase snake_case).
   - "unblock_client": Restoring network access (e.g. "unblock client2", "let client2 back on", "restore client2's internet"). Set "target" to the client name.
   - "limit_bandwidth": Throttling/bandwidth capping (e.g. "limit client1 to 5mbps", "throttle client1 to 5"). Set "target" to client name and params.rate_mbps to the numeric rate.
   - "get_status": Checking network status or client reachability (e.g. "status", "show network", "how is client1", "is client2 online"). Set "target" to the client or empty string for all clients.

3. "unknown":
   - If the input is ambiguous or does not map clearly to any known action, set "action": "unknown" and provide a polite, natural clarification in "reply" explaining how to rephrase or what commands are supported.

Few-shot Examples:
- "hello" -> {"action": "conversation", "target": "", "params": {}, "reply": "Hello! I am your Intelligent Network Assistant. How can I help you manage your network today?"}
- "thanks" -> {"action": "conversation", "target": "", "params": {}, "reply": "You're welcome! Let me know if you need anything else."}
- "what can you do?" -> {"action": "conversation", "target": "", "params": {}, "reply": "I can help you monitor and manage your Docker network. I can block or unblock client access, apply bandwidth rate limits, and display live network and firewall status."}
- "bye" -> {"action": "conversation", "target": "", "params": {}, "reply": "Goodbye! Have a great day."}
- "block client1" -> {"action": "block_client", "target": "client1", "params": {}, "reply": ""}
- "banana" -> {"action": "unknown", "target": "", "params": {}, "reply": "I'm not sure how to help with that. You can ask me to block or unblock clients, limit bandwidth, check network status, or type 'help' for command reference."}
"""

RESPONSE_SYSTEM_PROMPT = """You are the natural-language voice of a network \
automation assistant called INA. You are given a JSON "pipeline report" \
describing one request a user made in plain English, and what the system \
did about it (an intent-parsing stage, a policy check, an execution step \
against the network, and a validation/confirmation step).

Write a short reply (2-4 sentences, plain text, no markdown, no JSON) as if \
you were the assistant talking directly to the user. It must:
- Restate, in your own words, what the user asked for.
- Clearly say what actually happened: was it allowed or blocked by policy?
  If executed, did it succeed? Was it confirmed by validation?
- If anything failed or was denied, say why in plain terms (not raw errors).
- Avoid jargon (iptables, tc, MCP, JSON) unless there's no simpler way to say it.
- Be direct and concise — this is a status update, not a chat.

Output ONLY the reply text, nothing else.
"""


def _try_litellm(system_prompt: str, user_text: str, max_tokens: int = 300) -> Optional[str]:
    """Attempt a structured completion via LiteLLM (provider-agnostic)."""
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
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            max_tokens=max_tokens,
            temperature=0,
        )
        return resp["choices"][0]["message"]["content"]
    except Exception:
        return None


def _try_anthropic(system_prompt: str, user_text: str, max_tokens: int = 300) -> Optional[str]:
    """Attempt a structured completion via the Anthropic SDK."""
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
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_text}],
        )
        # Concatenate any text blocks in the response.
        return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
    except Exception:
        return None


def _try_openai(system_prompt: str, user_text: str, max_tokens: int = 300) -> Optional[str]:
    """Call an OpenRouter model using the OpenAI-compatible SDK."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key or "your-actual-api-key" in api_key:
        return None

    try:
        # pyrefly: ignore [missing-import]
        from openai import OpenAI
    except ImportError:
        return None

    try:
        client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )

        model = os.environ.get(
            "OPENROUTER_MODEL",
            "google/gemini-2.5-flash",
        )

        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
        )

        return resp.choices[0].message.content

    except Exception as exc:
        print(f"[OpenRouter error] {exc}", file=sys.stderr)
        return None


def _call_llm(system_prompt: str, user_text: str, max_tokens: int = 300) -> Optional[str]:
    """Try each configured LLM backend in turn for a generic completion;
    return the first non-empty response, or None if nothing is configured
    / everything failed. Shared by intent parsing and response synthesis."""
    for backend in (_try_litellm, _try_anthropic, _try_openai):
        raw = backend(system_prompt, user_text, max_tokens)
        if raw:
            return raw
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
    """Ask the configured LLM backend to translate free-form, layman
    language into a structured Intent. Returns None if no backend is
    configured, or if the response couldn't be parsed as valid JSON —
    in either case the caller falls back to the regex parser."""
    raw = _call_llm(LLM_SYSTEM_PROMPT, text, max_tokens=300)
    if raw is None:
        return None
    data = _extract_json(raw)
    if not data:
        return None
    try:
        action = ActionType(data.get("action", "unknown"))
    except ValueError:
        action = ActionType.UNKNOWN
    target = _normalize_target(str(data.get("target", "")))
    params = data.get("params") or {}
    if "rate_mbps" in params and "rate" not in params:
        params["rate"] = f"{params['rate_mbps']}mbit"
    reply = data.get("reply")
    if reply:
        params["reply"] = str(reply).strip()
    return Intent(
        action=action,
        target=target,
        params=params,
        raw_text=text,
        confidence=0.95 if action == ActionType.CONVERSATION else 0.9,
        parser_used="llm",
    )


def synthesize_natural_response(report: Dict[str, Any]) -> str:
    """The 'AI agent' voice at the END of the pipeline: turns the technical
    report (intent parsed, policy decision, execution result, validation
    result) into a short, plain-English status update for the user.

    Falls back to a deterministic, template-built summary if no LLM
    backend is configured (or the call fails), so the CLI always produces
    a readable final answer — never a bare technical dump."""
    intent = report.get("intent") or {}
    params = intent.get("params") or {}
    action = intent.get("action")

    # Conversational messages and unknown clarifications carry an explicit natural reply
    if action == ActionType.CONVERSATION.value or report.get("halted_at") == "conversation":
        if params.get("reply"):
            return str(params["reply"]).strip()
        return "Hello! How can I assist you with your network today?"

    if report.get("halted_at") == "parsing":
        if params.get("reply"):
            return str(params["reply"]).strip()
        return _template_response(report)

    payload = json.dumps(report, default=str)
    reply = _call_llm(RESPONSE_SYSTEM_PROMPT, payload, max_tokens=220)
    if reply:
        return reply.strip()
    return _template_response(report)


def _template_response(report: Dict[str, Any]) -> str:
    """Deterministic fallback used when no AI backend is available."""
    intent = report["intent"]
    raw_target = (intent.get("target") or "").strip()
    target = raw_target or "the target you mentioned"
    action = intent.get("action", "unknown")

    if report.get("halted_at") == "conversation":
        if intent.get("params", {}).get("reply"):
            return intent["params"]["reply"]
        return "Hello! How can I help you manage your network today?"

    if report.get("halted_at") == "parsing":
        if intent.get("params", {}).get("reply"):
            return intent["params"]["reply"]
        return (
            f"I wasn't quite sure how to handle \"{report['raw_text']}\". "
            "You can ask me to block or unblock clients, limit bandwidth, check network status, or type 'help' for available commands."
        )

    policy = report.get("policy") or {}
    if report.get("halted_at") == "policy":
        return (
            f"I understood you want to {action.replace('_', ' ')} {target}, but policy "
            f"blocked it: {policy.get('reason', 'no reason given')}."
        )

    execution = report.get("execution") or {}
    if report.get("halted_at") == "execution":
        return (
            f"Policy approved {action.replace('_', ' ')} for {target}, but the operation "
            f"failed: {execution.get('message', 'unknown error')}."
        )

    if action in (ActionType.GET_STATUS.value, "get_status"):
        target_desc = f"for '{raw_target}'" if raw_target and raw_target not in ("all", "network", "*") else "for the managed network"
        if execution.get("success"):
            return f"Status {target_desc}: {execution.get('message', 'operational')}"
        return f"Unable to retrieve status {target_desc}: {execution.get('message', 'unknown error')}."

    validation = report.get("validation") or {}
    if validation:
        confirmed = "and I've confirmed it on the network" if validation.get("success") else \
            "but I couldn't fully confirm it on the network yet"
        return (
            f"Done — {action.replace('_', ' ')} for {target} was applied "
            f"({execution.get('message', 'no details')}), {confirmed}: "
            f"{validation.get('summary', '')}"
        )
    return f"Done — {action.replace('_', ' ')} for {target} was applied: {execution.get('message', '')}"


# --- Regex fallback: deterministic, dependency-free, always available -----
_GREETING_RE = re.compile(
    r"^(?:hi|hello|hey|greetings|good\s+(?:morning|afternoon|evening))\b",
    re.IGNORECASE,
)
_THANKS_RE = re.compile(
    r"\b(?:thanks|thank\s+you|great\s+work|good\s+job|awesome|nice\s+work)\b",
    re.IGNORECASE,
)
_CAPABILITIES_RE = re.compile(
    r"\b(?:what\s+can\s+you\s+do|who\s+are\s+you|capabilities|what\s+do\s+you\s+do|how\s+can\s+you\s+help)\b",
    re.IGNORECASE,
)
_GOODBYE_RE = re.compile(
    r"^(?:bye|goodbye|cya|see\s+you(?:\s+later)?|farewell)\b",
    re.IGNORECASE,
)
_BLOCK_RE = re.compile(
    r"\b(?:block|kick|isolate)\b\s+(?P<target>[a-zA-Z0-9_\-]+)",
    re.IGNORECASE,
)
_UNBLOCK_RE = re.compile(
    r"\b(?:unblock|restore|unlimit|un-limit|reset)\b.*?(?P<target>client\d+|server|network-controller|[a-zA-Z0-9_\-]+)",
    re.IGNORECASE,
)
_LIMIT_RE = re.compile(
    r"\b(?:limit|throttle|cap)\b\s+(?P<target>[a-zA-Z0-9_\-]+)"
    r"(?:\s+(?:bandwidth|speed))?\s+to\s+(?P<rate>\d+(\.\d+)?)\s*(?:mbit|mbps|mb/s)?",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(
    r"\b(?:status|check|netstat|overview)\b(?:\s+of)?(?:\s+(?P<target>[a-zA-Z0-9_\-]+))?",
    re.IGNORECASE,
)


def regex_parse(text: str) -> Intent:
    """
    Deterministic rule-based parser used when no LLM backend is available
    (or when the LLM call fails / returns malformed JSON). This guarantees
    the assistant is always usable, even fully offline.
    """
    text = text.strip()

    if _GREETING_RE.search(text):
        return Intent(
            action=ActionType.CONVERSATION,
            target="",
            params={"reply": "Hello! I am your Intelligent Network Assistant. How can I help you manage your network today?"},
            raw_text=text,
            confidence=0.9,
            parser_used="regex",
        )

    if _THANKS_RE.search(text):
        return Intent(
            action=ActionType.CONVERSATION,
            target="",
            params={"reply": "You're welcome! Let me know if you need any other network changes or status checks."},
            raw_text=text,
            confidence=0.9,
            parser_used="regex",
        )

    if _CAPABILITIES_RE.search(text):
        return Intent(
            action=ActionType.CONVERSATION,
            target="",
            params={
                "reply": "I can help you manage and inspect your network! You can ask me to block or unblock client access, apply bandwidth rate limits (e.g., 'limit client1 to 5 Mbps'), or check live network and firewall status (e.g., 'status')."
            },
            raw_text=text,
            confidence=0.9,
            parser_used="regex",
        )

    if _GOODBYE_RE.search(text):
        return Intent(
            action=ActionType.CONVERSATION,
            target="",
            params={"reply": "Goodbye! Feel free to reach out whenever you need network assistance."},
            raw_text=text,
            confidence=0.9,
            parser_used="regex",
        )

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
        target_raw = m.group("target") or ""
        target = _normalize_target(target_raw)
        if target in ("network", "all", "net", "docker", "hosts", "clients", "system"):
            target = ""
        return Intent(
            action=ActionType.GET_STATUS,
            target=target,
            raw_text=text,
            confidence=0.85 if target == "" else 0.75,
            parser_used="regex",
        )

    return Intent(
        action=ActionType.UNKNOWN,
        target="",
        params={
            "reply": "I'm not quite sure how to help with that. You can ask me to block or unblock clients, limit bandwidth, check network status, or type 'help' to see what's available."
        },
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
        regex_intent = regex_parse(text)
        if regex_intent.action != ActionType.UNKNOWN:
            return regex_intent
        # If both are unknown, preserve the LLM's clarification reply if available
        if intent is not None and intent.params.get("reply"):
            return intent
        return regex_intent


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

        if intent.action == ActionType.CONVERSATION:
            report["halted_at"] = "conversation"
            report["elapsed_sec"] = round(time.time() - started, 3)
            report["natural_response"] = synthesize_natural_response(report)
            return report

        if intent.action == ActionType.UNKNOWN:
            report["halted_at"] = "parsing"
            report["error"] = intent.params.get("reply") or (
                "Could not understand the command. Try phrasing like "
                "'Block client1', 'Unblock client2', 'Limit client1 to 5 Mbps', "
                "or 'status'."
            )
            report["elapsed_sec"] = round(time.time() - started, 3)
            report["natural_response"] = synthesize_natural_response(report)
            return report

        decision = await self._evaluate_policy(intent)
        report["policy"] = {"allowed": decision.allowed, "reason": decision.reason, "rule_id": decision.rule_id}
        _audit(
            intent.action,
            {"client": intent.target, **intent.params},
            "ALLOWED" if decision.allowed else "DENIED",
            decision.reason,
        )

        if not decision.allowed:
            report["halted_at"] = "policy"
            report["elapsed_sec"] = round(time.time() - started, 3)
            report["natural_response"] = synthesize_natural_response(report)
            return report

        execution = await self._execute(intent)
        report["execution"] = {
            "success": execution.success,
            "message": execution.message,
            "details": execution.details,
        }
        if execution.success:
            _audit(
                intent.action,
                {"client": intent.target, **intent.params},
                "APPLIED",
                execution.message,
            )

        if not execution.success:
            _audit(
                intent.action,
                {"client": intent.target, **intent.params},
                "FAILED",
                execution.message,
            )
            report["halted_at"] = "execution"
            report["elapsed_sec"] = round(time.time() - started, 3)
            report["natural_response"] = synthesize_natural_response(report)
            return report

        validation = await self._validate(intent)
        report["validation"] = {
            "success": validation.success,
            "summary": validation.summary,
            "metrics": validation.metrics,
        }

        report["elapsed_sec"] = round(time.time() - started, 3)
        report["natural_response"] = synthesize_natural_response(report)
        return report


# ---------------------------------------------------------------------------
# 5. TERMINAL REPORTING
# ---------------------------------------------------------------------------
def _detect_ai_backend() -> str:
    """Report whether an LLM backend or the deterministic regex fallback is active."""
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    is_openrouter_set = bool(openrouter_key and "your-actual-api-key" not in openrouter_key)

    if os.environ.get("LITELLM_MODEL"):
        return "REAL (LITELLM)"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "REAL (ANTHROPIC)"
    if is_openrouter_set:
        return "REAL (OPENROUTER)"
    return "FALLBACK (REGEX)"


def print_startup_banner() -> None:
    mcp_note = "  [MCP SDK DETECTED]" if MCP_SDK_AVAILABLE else ""
    lines = [
        f"AI Agent:            {_detect_ai_backend().upper()}",
        f"Policy Engine:       {INTEGRATION_STATUS['policy_engine'].upper()}",
        f"MCP Tools:           {INTEGRATION_STATUS['mcp_tools'].upper()}{mcp_note}",
        f"Validation Monitor:  {INTEGRATION_STATUS['validation_monitor'].upper()}",
        f"Audit Logging:       {INTEGRATION_STATUS['audit_log'].upper()}",
    ]
    body = "\n".join(lines)
    if RICH_AVAILABLE:
        console.print(
            Panel.fit(
                body,
                title="[bold cyan]Intelligent Network Assistant — Subsystem Status[/bold cyan]",
                border_style="cyan",
            )
        )
    else:
        console.print("=== Intelligent Network Assistant — Subsystem Status ===")
        console.print(body)


def print_help() -> None:
    """Display concise help showing available user commands."""
    if RICH_AVAILABLE:
        table = Table(title="Available Commands", box=box.SIMPLE_HEAVY, show_header=True, header_style="bold magenta")
        table.add_column("Command", style="bold cyan")
        table.add_column("Description")
        table.add_column("Example", style="dim")
        table.add_row("block <client>", "Block client network access at firewall", "block client1")
        table.add_row("unblock <client>", "Restore client network access", "unblock client1")
        table.add_row("limit <client> <rate>", "Set bandwidth rate limit on client", "limit client1 5mbit")
        table.add_row("status", "Show live Docker network, client, firewall, and bandwidth status", "status")
        table.add_row("help", "Display available commands and usage reference", "help")
        console.print(table)
        console.print()
    else:
        print("\n=== Available Commands ===")
        print("  block <client>        Block client network access at firewall (e.g. block client1)")
        print("  unblock <client>      Restore client network access (e.g. unblock client1)")
        print("  limit <client> <rate> Set bandwidth rate limit on client (e.g. limit client1 5mbit)")
        print("  status                Show live Docker network, client, firewall, and bandwidth status (e.g. status)")
        print("  help                  Display available commands and usage reference\n")


def print_status_table(details: Dict[str, Any]) -> None:
    """Render a concise, human-readable status table for network and client states."""
    network = details.get("network", {})
    clients = details.get("clients", {})

    net_name = network.get("name", "network_project-net")
    subnet = network.get("subnet", "N/A")
    gateway = network.get("gateway", "N/A")
    net_status = "Available" if network.get("available", True) else "Unavailable"
    net_title = f"Network: {net_name} ({subnet}, Gateway: {gateway}) — {net_status}"

    if RICH_AVAILABLE:
        table = Table(title=net_title, box=box.SIMPLE_HEAVY, show_header=True, header_style="bold magenta")
        table.add_column("Target", style="bold")
        table.add_column("IP Address", style="cyan")
        table.add_column("State")
        table.add_column("Reachability")
        table.add_column("Firewall")
        table.add_column("Bandwidth Limit")

        for name, data in sorted(clients.items()):
            state = data.get("status", "unknown")
            reachable = data.get("reachable", False)
            reach_str = "[green]Reachable[/green]" if reachable else "[red]Unreachable[/red]"

            fw = data.get("firewall", "unknown")
            if fw == "blocked":
                fw_str = "[bold red]Blocked[/bold red]"
            elif fw == "protected":
                fw_str = "[bold cyan]Protected[/bold cyan]"
            else:
                fw_str = "[green]Unblocked[/green]"

            bw = data.get("bandwidth_limit", "none")
            bw_str = f"[yellow]{bw}[/yellow]" if bw != "none" else "[dim]None[/dim]"

            table.add_row(name, data.get("ip", "N/A"), state, reach_str, fw_str, bw_str)

        console.print(table)
        console.print()
    else:
        print(f"\n=== {net_title} ===")
        header = f"{'Target':<14} {'IP Address':<16} {'State':<10} {'Reachability':<14} {'Firewall':<12} {'Bandwidth Limit':<15}"
        print(header)
        print("-" * len(header))
        for name, data in sorted(clients.items()):
            reach = "Reachable" if data.get("reachable") else "Unreachable"
            print(f"{name:<14} {data.get('ip', 'N/A'):<16} {data.get('status', 'unknown'):<10} {reach:<14} {data.get('firewall', 'unknown'):<12} {data.get('bandwidth_limit', 'none'):<15}")
        print()


def print_report(report: Dict[str, Any]) -> None:
    """Renders the full pipeline report as a readable, professional
    terminal summary."""
    intent = report["intent"]

    halted_at = report.get("halted_at")

    if halted_at == "conversation":
        reply_color = "cyan"
    elif halted_at == "parsing":
        reply_color = "yellow"
    else:
        overall_ok = (
            halted_at is None
            and (report.get("validation") or {}).get("success", True)
        )
        reply_color = "green" if overall_ok else "red"

    if RICH_AVAILABLE:
        console.rule(f"[bold]Request {report['request_id']}[/bold]")
        console.print(f"[dim]You said:[/dim] \"{report['raw_text']}\"")

        # --- The AI agent's natural-language reply, shown first --------
        console.print(
            Panel.fit(
                report.get("natural_response", "(no response generated)"),
                title="[bold]INA[/bold]",
                border_style=reply_color,
            )
        )

        # Conversational and unknown queries do not run the network pipeline
        if halted_at == "conversation":
            console.print(f"[dim]Completed in {report.get('elapsed_sec', 0.0)}s[/dim]\n")
            return

        if halted_at == "parsing":
            console.print(f"[dim]Tip: Type 'help' to see available network commands. ({report.get('elapsed_sec', 0.0)}s)[/dim]\n")
            return

        # --- Technical trace of the pipeline, for debugging/demo -------
        table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold magenta")
        table.add_column("Stage")
        table.add_column("Result")

        table.add_row(
            "Intent Parsing",
            f"action=[cyan]{intent['action']}[/cyan] target=[cyan]{intent['target'] or '-'}[/cyan] "
            f"params={intent['params']} (via {intent['parser_used']}, conf={intent['confidence']})",
        )

        policy = report.get("policy") or {}
        policy_color = "green" if policy.get("allowed") else "red"
        table.add_row(
            "Policy Engine",
            f"[{policy_color}]{'ALLOW' if policy.get('allowed') else 'DENY'}[/{policy_color}] — {policy.get('reason', '')}",
        )

        if not policy.get("allowed"):
            console.print(table)
            console.print()
            return

        execution = report.get("execution") or {}
        exec_color = "green" if execution.get("success") else "red"
        table.add_row(
            "MCP Tools",
            f"[{exec_color}]{'OK' if execution.get('success') else 'FAILED'}[/{exec_color}] — {execution.get('message', '')}",
        )

        if not execution.get("success"):
            console.print(table)
            console.print()
            return

        validation = report.get("validation") or {}
        val_color = "green" if validation.get("success") else "yellow"
        table.add_row(
            "Validation Monitor",
            f"[{val_color}]{'CONFIRMED' if validation.get('success') else 'UNCONFIRMED'}[/{val_color}] — {validation.get('summary', '')}",
        )

        console.print(table)
        console.print(f"[dim]Completed in {report['elapsed_sec']}s[/dim]\n")

        # If this was a status query and was successful, render the status table
        if intent["action"] in (ActionType.GET_STATUS.value, "get_status") and execution.get("details", {}).get("clients"):
            print_status_table(execution["details"])
    else:
        # Plain-text fallback for environments without rich installed.
        print(f"\n--- Request {report['request_id']} ---")
        print(f'You said: "{report["raw_text"]}"')
        print(f"INA: {report.get('natural_response', '(no response generated)')}")
        if halted_at == "conversation":
            print(f"Completed in {report.get('elapsed_sec', 0.0)}s\n")
            return
        if halted_at == "parsing":
            print(f"Tip: Type 'help' to see available network commands. ({report.get('elapsed_sec', 0.0)}s)\n")
            return
        print(f"[trace] Intent: {intent}")
        policy = report.get("policy") or {}
        print(f"[trace] Policy Engine: {'ALLOW' if policy.get('allowed') else 'DENY'} - {policy.get('reason')}")
        if not policy.get("allowed"):
            return
        execution = report.get("execution") or {}
        print(f"[trace] MCP Tools: {'OK' if execution.get('success') else 'FAILED'} - {execution.get('message')}")
        if not execution.get("success"):
            return
        validation = report.get("validation") or {}
        print(f"[trace] Validation Monitor: {'CONFIRMED' if validation.get('success') else 'UNCONFIRMED'} - {validation.get('summary')}")
        print(f"Completed in {report['elapsed_sec']}s")
        if intent["action"] in (ActionType.GET_STATUS.value, "get_status") and execution.get("details", {}).get("clients"):
            print_status_table(execution["details"])


# ---------------------------------------------------------------------------
# 6. CLI ENTRY POINTS
# ---------------------------------------------------------------------------
async def run_single_command(assistant: NetworkAssistant, text: str) -> None:
    report = await assistant.process_command(text)
    print_report(report)


async def run_interactive(assistant: NetworkAssistant) -> None:
    console.print(
        "[bold green]Interactive mode.[/bold green] Type a command, 'help' for command list, or 'exit' / 'quit' to leave.\n"
        if RICH_AVAILABLE
        else "Interactive mode. Type a command, 'help' for command list, or 'exit' / 'quit' to leave.\n"
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
        if stripped in ("help", "?"):
            print_help()
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
        help='Optional single command to run non-interactively, e.g. "Block client1", "status", "help". '
        "If omitted, starts an interactive session.",
    )
    return parser


async def _amain() -> None:
    args = build_arg_parser().parse_args()
    assistant = NetworkAssistant()
    print_startup_banner()

    if args.command:
        if len(args.command) == 1 and args.command[0].strip().lower() in ("help", "?"):
            print_help()
            return
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

