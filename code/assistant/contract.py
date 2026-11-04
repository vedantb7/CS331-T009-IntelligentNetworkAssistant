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

    # Canonical 2026-07-28 MCP Tools
    BLOCK_CLIENT = "block_client"
    UNBLOCK_CLIENT = "unblock_client"
    LIMIT_BANDWIDTH = "limit_bandwidth"
    REMOVE_BANDWIDTH_LIMIT = "remove_bandwidth_limit"
    GET_STATUS = "get_status"
    KICK_CLIENT = "kick_client"
    BAN_CLIENT = "ban_client"
    UNBAN_CLIENT = "unban_client"

    # Aliases for backwards compatibility with earlier drafts
    LIST_DEVICES = "list_devices"
    BLOCK_DEVICE = "block_device"
    RESTORE_DEVICE = "restore_device"


# Human-readable descriptions, reused in the system prompt and in --help output.
TOOL_DESCRIPTIONS: Dict[str, str] = {
    ToolName.BLOCK_CLIENT.value: (
        "Block a client's IPv4 traffic to another client, gateway, internet, or all."
    ),
    ToolName.UNBLOCK_CLIENT.value: (
        "Unblock a client's traffic to a previously blocked target."
    ),
    ToolName.LIMIT_BANDWIDTH.value: (
        "Limit a device's download and upload bandwidth ceiling in kbit/s or Mbit/s."
    ),
    ToolName.REMOVE_BANDWIDTH_LIMIT.value: (
        "Remove any active bandwidth rate ceiling on a client."
    ),
    ToolName.GET_STATUS.value: (
        "Query client device status, associations, active blocks, and bandwidth limits."
    ),
    ToolName.KICK_CLIENT.value: (
        "Disconnect a device from Wi-Fi once; it will usually reconnect by itself."
    ),
    ToolName.BAN_CLIENT.value: (
        "Stop a device from connecting to the Wi-Fi at all (optionally for N minutes)."
    ),
    ToolName.UNBAN_CLIENT.value: (
        "Allow a previously banned device to connect to the Wi-Fi again."
    ),
    ToolName.LIST_DEVICES.value: (
        "List the devices currently on the managed network, by their "
        "admin-assigned names, with their class and current state."
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


# ---------------------------------------------------------------------------
# Tool definitions for the LLM when no MCP server is attached
# ---------------------------------------------------------------------------
# With a live server (McpTransport) the LLM is given the server's own tools/list
# schemas instead; these mirror them for the built-in mock network only.

_CLIENT = {"type": "string", "description": "Client name (e.g. 'guest-phone') or IPv4 in 192.168.50.0/24."}
_CLIENT_OR_MAC = {"type": "string", "description": "Client name, IPv4 in 192.168.50.0/24, or MAC address."}
_TARGET = {"type": "string", "description": "Another client, or 'gateway', 'internet' or 'all'."}
_KBIT = {"type": "integer", "minimum": 256, "maximum": 20000,
         "description": "Rate in kbit/s (1000 = 1 Mbit/s)."}


def _tool(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties, "required": required}}


FALLBACK_TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    _tool("block_client", "Block a client's IPv4 traffic to a target without disconnecting it.",
          {"client": _CLIENT, "target": _TARGET}, ["client", "target"]),
    _tool("unblock_client", "Remove a block created by block_client.",
          {"client": _CLIENT, "target": _TARGET}, ["client", "target"]),
    _tool("limit_bandwidth", "Cap a client's bandwidth in kbit/s.",
          {"client": _CLIENT, "download_kbit": _KBIT, "upload_kbit": _KBIT},
          ["client", "download_kbit", "upload_kbit"]),
    _tool("remove_bandwidth_limit", "Remove a client's bandwidth limit.", {"client": _CLIENT}, ["client"]),
    _tool("get_status", "Network status: clients, blocks, limits, bans.", {"client": _CLIENT}, []),
    _tool("kick_client", "Disconnect a client from Wi-Fi once; it usually reconnects.",
          {"client": _CLIENT_OR_MAC}, ["client"]),
    _tool("ban_client", "Keep a device off the Wi-Fi (returns a preview until confirmed).",
          {"client": _CLIENT_OR_MAC,
           "duration_minutes": {"type": "integer", "minimum": 1, "maximum": 10080,
                                "description": "Minutes; omit for a permanent ban."},
           "reason": {"type": "string", "description": "Short reason for the audit log."}},
          ["client"]),
    _tool("unban_client", "Allow a banned device to join the Wi-Fi again.",
          {"client": _CLIENT_OR_MAC}, ["client"]),
]
