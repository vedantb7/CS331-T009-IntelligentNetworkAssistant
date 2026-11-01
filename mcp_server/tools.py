"""Tool definitions exposed through the MCP server in INA v2.

Implements the eight canonical MCP tools:
- block_client(client, target)
- unblock_client(client, target)
- limit_bandwidth(client, download_kbit, upload_kbit)
- remove_bandwidth_limit(client)
- get_status(client?)
- kick_client(client)
- ban_client(client, duration_minutes?, reason?, confirm?)
- unban_client(client)

Every call passes through the same server-side pipeline before anything runs:
1. The MCP SDK has already checked the arguments against the tool's inputSchema
   (patterns and ranges declared in server.py); bad input never reaches here.
2. PolicyGuard.check(): role token -> role -> policy engine -> decision written
   to the policy audit log. It fails closed. The token is a connection
   credential taken from the server's environment (INA_ROLE_TOKEN), as the MCP
   spec prescribes for stdio; the AI never supplies or chooses a role.
3. Only if allowed: the command runs, using the role the token resolved to.
   The unprivileged server never executes it itself: 'default_service' is a
   NetctlService, which hands it to the root-only 'sudo ina-netctl' boundary,
   where the authoritative service layer ('network.service') executes it.
4. The command output comes back to this server: every nft/tc/hostapd_cli
   call the request caused (argv, exit code, output) is returned in a trace,
   while the same records are written to the root-side command log as they
   run. The outcome, with a summary of that trace, is recorded in the policy
   audit log under the same request ID.

Service errors are raised as ToolError so MCP transmits them as actionable
'isError: true' results. The message starts with "Denied" when nothing was
executed and "Failed" when the change itself or its verification failed.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Mapping, Optional, TypeVar

try:
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:
    class ToolError(Exception):  # type: ignore[no-redef]
        """Fallback exception representing a tool execution error."""
        pass

from inav2.mcp_server.schemas import (
    BanResult,
    BlockResult,
    KickResult,
    LimitResult,
    RemoveLimitResult,
    StatusResult,
    UnbanResult,
    UnblockResult,
)
from inav2.network.command_log import ensure_recording, request_scope, summarize
from inav2.network.privileged import default_executor
from inav2.network.service import (
    INAError,
    KernelMutationError,
    LockTimeoutError,
    VerificationError,
    WirelessControlError,
)
from inav2.policy.audit import AuditError
from inav2.policy.authorization import AuthConfigError
from inav2.policy.config import PolicyConfigError
from inav2.policy.guard import PolicyGuard

T = TypeVar("T")

# Environment variable carrying the caller's role token (stdio credential).
TOKEN_ENV = "INA_ROLE_TOKEN"

# Errors raised after the command started; everything else means nothing ran.
_FAILED_ERRORS = (KernelMutationError, LockTimeoutError, VerificationError, WirelessControlError)

_guard: Optional[PolicyGuard] = None

# Executes allowed calls. In the deployed (unprivileged) server this is a
# NetctlService, so every command runs inside 'sudo ina-netctl'; see
# network/privileged.py. Tests replace it with an in-process NetworkService.
default_service = default_executor()


def get_guard() -> PolicyGuard:
    """Return the shared PolicyGuard, loading policy files and token hashes on first use."""
    global _guard
    if _guard is None:
        try:
            _guard = PolicyGuard.from_config()
        except (AuthConfigError, PolicyConfigError) as err:
            raise ToolError(f"Denied: the server's policy is not configured ({err}).") from err
    return _guard


def _error_text(err: INAError) -> str:
    prefix = "Failed" if isinstance(err, _FAILED_ERRORS) else "Denied"
    return f"{prefix}: {err}"


def _execute(
    tool: str,
    arguments: Dict[str, Any],
    action: Callable[[str], T],
    context: Optional[Mapping[str, Any]] = None,
) -> T:
    """Authorize one tool call with the policy guard, then run it with the resolved role."""
    guard = get_guard()
    result = guard.check(os.environ.get(TOKEN_ENV, ""), tool, arguments, context=context)
    if not result.allowed:
        decision = result.decision
        raise ToolError(f"Denied by policy [{decision.rule}]: {decision.reason}")

    # Direct (root/dev) executors record here; a NetctlService gets its records
    # back from ina-netctl. Either way they land in 'trace'.
    ensure_recording(default_service)
    with request_scope(result.request_id) as trace:
        try:
            out = action(result.role)
        except INAError as err:
            try:
                guard.record_outcome(result, "failure", str(err), verification_passed=False,
                                     commands=summarize(trace))
            except AuditError:
                pass
            raise ToolError(_error_text(err)) from err

    status = "preview" if getattr(out, "preview", False) else "success"
    try:
        guard.record_outcome(result, status, getattr(out, "message", None) or "",
                             verification_passed=getattr(out, "verified", None),
                             commands=summarize(trace))
    except AuditError:
        pass
    return out


def block_client(client: str, target: str) -> BlockResult:
    """Block IPv4 traffic between a client and a target without disconnecting the client.

    Args:
        client: Client reservation name (e.g. 'guest-phone') or IPv4 in 192.168.50.0/24.
        target: Another client (name or IP), or scope keyword: 'gateway', 'internet', or 'all'.

    Returns:
        BlockResult containing post-action state and verification confirmation.
    """
    return _execute(
        "block_client", {"client": client, "target": target},
        lambda role: default_service.block(client=client, target=target, role=role),
    )


def unblock_client(client: str, target: str) -> UnblockResult:
    """Remove a block rule between a client and a target. Bandwidth limits are not touched.

    Args:
        client: Client reservation name or IPv4 in 192.168.50.0/24.
        target: Target client name/IP, or scope keyword ('gateway', 'internet', 'all').

    Returns:
        UnblockResult confirming removal and verification.
    """
    return _execute(
        "unblock_client", {"client": client, "target": target},
        lambda role: default_service.unblock(client=client, target=target, role=role),
    )


def limit_bandwidth(client: str, download_kbit: int, upload_kbit: int) -> LimitResult:
    """Cap a client's aggregate bandwidth in kbit/s across all destinations.

    Args:
        client: Client reservation name or IPv4 in 192.168.50.0/24.
        download_kbit: Egress download ceiling in kbit/s (256 to 20000; 1000 = 1 Mbit/s).
        upload_kbit: Ingress upload ceiling in kbit/s (256 to 20000; 1000 = 1 Mbit/s).

    Returns:
        LimitResult containing applied rates and verification state.
    """
    return _execute(
        "limit_bandwidth",
        {"client": client, "download_kbit": download_kbit, "upload_kbit": upload_kbit},
        lambda role: default_service.limit(
            client=client, download_kbit=download_kbit, upload_kbit=upload_kbit, role=role
        ),
    )


def remove_bandwidth_limit(client: str) -> RemoveLimitResult:
    """Remove bandwidth limits for a client, returning it to unconstrained line rate.

    Args:
        client: Client reservation name or IPv4 in 192.168.50.0/24.

    Returns:
        RemoveLimitResult confirming limit removal.
    """
    return _execute(
        "remove_bandwidth_limit", {"client": client},
        lambda role: default_service.remove_limit(client=client, role=role),
    )


def get_status(client: Optional[str] = None) -> StatusResult:
    """Query network status, client associations, active blocks, and bandwidth limits.

    Args:
        client: Optional client name or IP to filter status.

    Returns:
        StatusResult containing client list and synchronization telemetry.
    """
    arguments = {"client": client} if client else {}
    return _execute(
        "get_status", arguments,
        lambda role: default_service.status(client=client, role=role),
    )


def kick_client(client: str) -> KickResult:
    """Deauthenticate a client from the INA Wi-Fi once (it will normally reconnect within seconds).

    Args:
        client: Client reservation name (e.g. 'guest-phone'), IPv4 in 192.168.50.0/24, or MAC address.

    Returns:
        KickResult containing deauthentication outcome and association state.
    """
    return _execute(
        "kick_client", {"client": client},
        lambda role: default_service.kick(client=client, role=role),
    )


def ban_client(
    client: str,
    duration_minutes: Optional[int] = None,
    reason: Optional[str] = None,
    confirm: bool = False,
) -> BanResult:
    """Prevent a device (by MAC) from associating with the INA Wi-Fi, and disconnect it now.

    Without confirm=true this only returns a preview of what would happen; nothing changes.
    duration_minutes = 1..10080, or omit for a permanent ban. IP blocks and limits are untouched.

    Args:
        client: Client reservation name, IPv4 in 192.168.50.0/24, or MAC address.
        duration_minutes: Ban duration in minutes (1 to 10080; default permanent).
        reason: Optional diagnostic or administrative reason string.
        confirm: Must be True to apply the ban; if False, returns a dry-run preview.

    Returns:
        BanResult containing preview or applied ban status and verification.
    """
    arguments: Dict[str, Any] = {"client": client, "duration_minutes": duration_minutes,
                                 "confirm": confirm}
    if reason is not None:
        arguments["reason"] = reason
    return _execute(
        "ban_client", arguments,
        lambda role: default_service.ban(
            client=client,
            duration_minutes=duration_minutes,
            reason=reason,
            confirm=confirm,
            role=role,
        ),
    )


def unban_client(client: str) -> UnbanResult:
    """Remove a ban created by ban_client so the device may associate again.

    Args:
        client: Client reservation name, IPv4 in 192.168.50.0/24, or MAC address.

    Returns:
        UnbanResult confirming ban removal from runtime ACL and deny file.
    """
    # Trusted server-side context: who created the ban (operators may only lift their own).
    context = {"banned_by_role": default_service.ban_owner_role(client)}
    return _execute(
        "unban_client", {"client": client},
        lambda role: default_service.unban(client=client, role=role),
        context=context,
    )
