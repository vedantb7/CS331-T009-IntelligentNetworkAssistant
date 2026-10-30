"""Tool definitions exposed through the MCP server in INA v2.

Implements the five canonical MCP tools:
- block_client(client, target)
- unblock_client(client, target)
- limit_bandwidth(client, download_kbit, upload_kbit)
- remove_bandwidth_limit(client)
- get_status(client?)

Delegates all execution to the authoritative service layer ('network.service').
Converts service errors into ToolError exceptions so MCP transmits them as
actionable 'isError: true' results per specification.
"""

from __future__ import annotations

import os
from typing import Optional

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
from inav2.network.service import (
    INAError,
    default_service,
)


def get_current_role() -> str:
    """Retrieve the caller role from the execution environment."""
    return os.environ.get("INA_ROLE", "operator").strip().lower()


def block_client(client: str, target: str) -> BlockResult:
    """Block IPv4 traffic between a client and a target without disconnecting the client.

    Args:
        client: Client reservation name (e.g. 'laptop-a') or IPv4 in 192.168.50.0/24.
        target: Another client (name or IP), or scope keyword: 'gateway', 'internet', or 'all'.

    Returns:
        BlockResult containing post-action state and verification confirmation.
    """
    try:
        role = get_current_role()
        return default_service.block(client=client, target=target, role=role)
    except INAError as err:
        raise ToolError(str(err)) from err


def unblock_client(client: str, target: str) -> UnblockResult:
    """Remove a block rule between a client and a target. Bandwidth limits are not touched.

    Args:
        client: Client reservation name or IPv4 in 192.168.50.0/24.
        target: Target client name/IP, or scope keyword ('gateway', 'internet', 'all').

    Returns:
        UnblockResult confirming removal and verification.
    """
    try:
        role = get_current_role()
        return default_service.unblock(client=client, target=target, role=role)
    except INAError as err:
        raise ToolError(str(err)) from err


def limit_bandwidth(client: str, download_kbit: int, upload_kbit: int) -> LimitResult:
    """Cap a client's aggregate bandwidth in kbit/s across all destinations.

    Args:
        client: Client reservation name or IPv4 in 192.168.50.0/24.
        download_kbit: Egress download ceiling in kbit/s (256 to 20000; 1000 = 1 Mbit/s).
        upload_kbit: Ingress upload ceiling in kbit/s (256 to 20000; 1000 = 1 Mbit/s).

    Returns:
        LimitResult containing applied rates and verification state.
    """
    try:
        role = get_current_role()
        return default_service.limit(
            client=client, download_kbit=download_kbit, upload_kbit=upload_kbit, role=role
        )
    except INAError as err:
        raise ToolError(str(err)) from err


def remove_bandwidth_limit(client: str) -> RemoveLimitResult:
    """Remove bandwidth limits for a client, returning it to unconstrained line rate.

    Args:
        client: Client reservation name or IPv4 in 192.168.50.0/24.

    Returns:
        RemoveLimitResult confirming limit removal.
    """
    try:
        role = get_current_role()
        return default_service.remove_limit(client=client, role=role)
    except INAError as err:
        raise ToolError(str(err)) from err


def get_status(client: Optional[str] = None) -> StatusResult:
    """Query network status, client associations, active blocks, and bandwidth limits.

    Args:
        client: Optional client name or IP to filter status.

    Returns:
        StatusResult containing client list and synchronization telemetry.
    """
    try:
        role = get_current_role()
        return default_service.status(client=client, role=role)
    except INAError as err:
        raise ToolError(str(err)) from err


def kick_client(client: str) -> KickResult:
    """Deauthenticate a client from the INA Wi-Fi once (it will normally reconnect within seconds).

    Args:
        client: Client reservation name (e.g. 'phone-b'), IPv4 in 192.168.50.0/24, or MAC address.

    Returns:
        KickResult containing deauthentication outcome and association state.
    """
    try:
        role = get_current_role()
        return default_service.kick(client=client, role=role)
    except INAError as err:
        raise ToolError(str(err)) from err


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
    try:
        role = get_current_role()
        return default_service.ban(
            client=client,
            duration_minutes=duration_minutes,
            reason=reason,
            confirm=confirm,
            role=role,
        )
    except INAError as err:
        raise ToolError(str(err)) from err


def unban_client(client: str) -> UnbanResult:
    """Remove a ban created by ban_client so the device may associate again.

    Args:
        client: Client reservation name, IPv4 in 192.168.50.0/24, or MAC address.

    Returns:
        UnbanResult confirming ban removal from runtime ACL and deny file.
    """
    try:
        role = get_current_role()
        return default_service.unban(client=client, role=role)
    except INAError as err:
        raise ToolError(str(err)) from err

