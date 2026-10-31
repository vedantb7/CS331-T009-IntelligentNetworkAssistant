"""Shared constants and small data types for the policy package.

Everything here is plain data, with no I/O, so the engine, the guard, the
audit log and the tests can all import it without circular imports.

The tool names are the eight canonical MCP tools in ``docs/tools.md`` and must
stay in sync with ``assistant/contract.py`` (ToolName) and
``mcp_server/server.py``; a unit test checks that they match.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

ALLOW = "ALLOW"
DENY = "DENY"

# --- The eight MCP tools (docs/tools.md, section 2) -------------------------
ACTION_BLOCK = "block_client"
ACTION_UNBLOCK = "unblock_client"
ACTION_LIMIT = "limit_bandwidth"
ACTION_UNLIMIT = "remove_bandwidth_limit"
ACTION_STATUS = "get_status"
ACTION_KICK = "kick_client"
ACTION_BAN = "ban_client"
ACTION_UNBAN = "unban_client"

TOOL_ACTIONS: Tuple[str, ...] = (
    ACTION_BLOCK, ACTION_UNBLOCK, ACTION_LIMIT, ACTION_UNLIMIT,
    ACTION_STATUS, ACTION_KICK, ACTION_BAN, ACTION_UNBAN,
)
READ_ONLY_ACTIONS: Tuple[str, ...] = (ACTION_STATUS,)
MUTATING_ACTIONS: Tuple[str, ...] = tuple(a for a in TOOL_ACTIONS if a not in READ_ONLY_ACTIONS)

# Tools that accept a MAC address as the client (docs/tools.md: kick/ban/unban
# use CLIENT_OR_MAC_REGEX; the others accept a name or an IPv4 address only).
MAC_CLIENT_ACTIONS: Tuple[str, ...] = (ACTION_KICK, ACTION_BAN, ACTION_UNBAN)

# Tools that take a ``target`` (another client, or a scope keyword).
TARGET_ACTIONS: Tuple[str, ...] = (ACTION_BLOCK, ACTION_UNBLOCK)

# Admin-only action. It is NOT an MCP tool: it exists so the engine can deny an
# operator who asks to change the rules ("denied because of the role").
ACTION_UPDATE_POLICY = "update_policy"
POLICY_ACTIONS: Tuple[str, ...] = (ACTION_UPDATE_POLICY,)
ALL_ACTIONS: Tuple[str, ...] = TOOL_ACTIONS + POLICY_ACTIONS

# Earlier drafts of the contract (still accepted by assistant/contract.py and
# the assistant's MockTransport). They are mapped onto the canonical tools.
LEGACY_LIST = "list_devices"      # -> get_status
LEGACY_BLOCK = "block_device"     # -> block_client, target "all"
LEGACY_RESTORE = "restore_device"  # -> unblock_client(all) + remove_bandwidth_limit
LEGACY_ALIASES: Dict[str, str] = {
    LEGACY_LIST: ACTION_STATUS,
    LEGACY_BLOCK: ACTION_BLOCK,
    LEGACY_RESTORE: ACTION_UNBLOCK,
}

# Device classes an admin can assign in devices.yaml. Devices that are not in
# devices.yaml but are addressed by IP or MAC are treated as "unknown".
DEVICE_CLASSES: Tuple[str, ...] = ("protected", "staff", "guest", "unknown")
UNKNOWN_CLASS = "unknown"

# --- Rule IDs ----------------------------------------------------------------
# Deny IDs shared with the assistant's MockTransport (assistant/transport.py),
# so the assistant's evaluation numbers stay valid against the real engine.
R_TOKEN = "R-token"
R_UNKNOWN_DEVICE = "R-unknown-device"
R_PROTECTED = "R-protected"
R_ROLE_CLASS = "R-role-class"
R_BAD_RATE = "R-bad-rate"
R_RATE_RANGE = "R-rate-range"
R_UNKNOWN_TOOL = "R-unknown-tool"
R_BAN_DURATION = "R-ban-duration"
# Other deny IDs
R_ROLE = "R-role-unknown"
R_ROLE_ACTION = "R-role-action"
R_BAD_ARGUMENTS = "R-bad-arguments"
R_BAD_DEVICE = "R-bad-device"
R_AMBIGUOUS_DEVICE = "R-ambiguous-device"
R_IDENTITY_CONFLICT = "R-identity-conflict"
R_BAD_TARGET = "R-bad-target"
R_SELF_TARGET = "R-self-target"
R_BAD_REASON = "R-bad-reason"
R_UNBAN_SCOPE = "R-unban-scope"
R_AUDIT = "R-audit-unavailable"
R_INTERNAL = "R-internal-error"
# Allow IDs
R_STATUS = "R-status"
R_BLOCK = "R-block"
R_UNBLOCK = "R-unblock"
R_LIMIT = "R-limit"
R_UNLIMIT = "R-unlimit"
R_KICK = "R-kick"
R_BAN = "R-ban"
R_UNBAN = "R-unban"
R_RESTORE = "R-restore"
R_POLICY_ADMIN = "R-policy-admin"


@dataclass(frozen=True)
class Device:
    """A device the policy reasons about.

    Registered devices come from the admin-owned ``devices.yaml``. A device
    addressed only by IP or MAC that is not registered gets class "unknown".
    """

    name: str
    mac: Optional[str]
    cls: str
    ip: Optional[str] = None
    registered: bool = True


@dataclass(frozen=True)
class PolicyDecision:
    """The engine's answer. ``to_dict`` matches ``assistant.contract.Decision``."""

    decision: str
    reason: str
    rule: str
    candidates: Tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "decision": self.decision,
            "reason": self.reason,
            "rule": self.rule,
        }
        if self.candidates:
            out["candidates"] = list(self.candidates)
        return out


def allow(rule: str, reason: str) -> PolicyDecision:
    return PolicyDecision(ALLOW, reason, rule)


def deny(rule: str, reason: str, candidates: Tuple[str, ...] = ()) -> PolicyDecision:
    return PolicyDecision(DENY, reason, rule, candidates)
