"""Shared tool contract between the assistant and the MCP server.

This is the agreed interface for INA v2. The assistant (this package) turns a
natural-language request into exactly one of these tool calls, and the MCP
server (``mcp_server/``) exposes tools with these exact names, arguments and
result shape. Keeping the contract in one small module means Khushi (assistant)
and Dhruv (MCP server) build against the same definition.

Owner: Khushi (assistant). Must stay in sync with Dhruv's ``mcp_server/``.

Design rules (see the project plan, interface section):
- No tool takes a ``role`` argument. The caller attaches a role *token*; the
  server derives the role from it. The AI never chooses a role.
- The four tools are fixed. The AI cannot invent commands.
- Every tool returns the same result shape, so denials and failures are
  reported the same way as successes, never hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ToolName(str, Enum):
    """The only actions the system can perform."""

    LIST_DEVICES = "list_devices"
    LIMIT_BANDWIDTH = "limit_bandwidth"
    BLOCK_DEVICE = "block_device"
    RESTORE_DEVICE = "restore_device"


# Human-readable descriptions, reused in the system prompt and in --help output.
TOOL_DESCRIPTIONS: Dict[str, str] = {
    ToolName.LIST_DEVICES.value: (
        "List the devices currently on the managed network, by their "
        "admin-assigned names, with their class and current state."
    ),
    ToolName.LIMIT_BANDWIDTH.value: (
        "Limit a device's bandwidth to a given rate in Mbit/s."
    ),
    ToolName.BLOCK_DEVICE.value: "Block a device from the network.",
    ToolName.RESTORE_DEVICE.value: (
        "Restore a device: remove any block and any bandwidth limit."
    ),
}


@dataclass
class ToolCall:
    """One request the assistant sends to the MCP server.

    ``token`` carries the caller's role; the server, not the AI, reads it.
    ``arguments`` holds the tool-specific fields, e.g. ``{"device": "lab-laptop-3"}``
    or ``{"device": "lab-laptop-3", "mbit": 5}``.
    """

    tool: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    token: Optional[str] = None
    request_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "token": self.token,
            "request_id": self.request_id,
        }


@dataclass
class Decision:
    """The policy engine's answer (Ananya's component), carried in the result."""

    decision: str  # "ALLOW" or "DENY"
    reason: str = ""
    rule: Optional[str] = None

    @property
    def allowed(self) -> bool:
        return self.decision.upper() == "ALLOW"


@dataclass
class Verification:
    """The verification outcome (Khushi's component), carried in the result."""

    passed: bool
    expected: Optional[str] = None
    measured: Optional[str] = None
    checks: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ToolResult:
    """The uniform result shape every tool returns (contract C4 in the plan).

    ``status`` is one of:
      - "success"  : allowed, applied and verified
      - "denied"   : stopped by the policy engine; nothing changed
      - "failure"  : allowed but the change or its verification did not succeed
    """

    status: str
    tool: str
    request_id: Optional[str] = None
    device: Optional[str] = None
    decision: Optional[Decision] = None
    verification: Optional[Verification] = None
    detail: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ToolResult":
        """Parse a result dict from the server (or the mock) into a ToolResult."""
        decision = None
        if isinstance(raw.get("decision"), dict):
            d = raw["decision"]
            decision = Decision(
                decision=str(d.get("decision", "DENY")),
                reason=str(d.get("reason", "")),
                rule=d.get("rule"),
            )
        verification = None
        if isinstance(raw.get("verification"), dict):
            v = raw["verification"]
            verification = Verification(
                passed=bool(v.get("passed", False)),
                expected=v.get("expected"),
                measured=v.get("measured"),
                checks=list(v.get("checks", [])),
            )
        return cls(
            status=str(raw.get("status", "failure")),
            tool=str(raw.get("tool", "")),
            request_id=raw.get("request_id"),
            device=raw.get("device"),
            decision=decision,
            verification=verification,
            detail=str(raw.get("detail", "")),
            data={k: v for k, v in raw.items() if k not in (
                "status", "tool", "request_id", "device",
                "decision", "verification", "detail",
            )},
        )
