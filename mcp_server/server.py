"""MCPServer initialization and entrypoint for INA v2.

Compliant with Model Context Protocol specification (revision 2026-07-28).
Exposes the five canonical network management tools over stdio transport.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Annotated, Optional

try:
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations
    MCP_SDK_AVAILABLE = True
except ImportError:
    MCP_SDK_AVAILABLE = False

    class ToolAnnotations:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class MCPServer:  # type: ignore[no-redef]
        def __init__(self, name: str, instructions: str = ""):
            self.name = name
            self.instructions = instructions
            self.tools = {}

        def tool(self, **kwargs):
            def decorator(fn):
                self.tools[fn.__name__] = fn
                return fn
            return decorator

        def run(self):
            print(f"MCPServer '{self.name}' fallback run (SDK not installed)")

from inav2.mcp_server import tools
from inav2.mcp_server.schemas import (
    BanResult,
    BlockResult,
    ClientStatus,
    KickResult,
    LimitResult,
    RemoveLimitResult,
    StatusResult,
    UnbanResult,
    UnblockResult,
)

SERVER_INSTRUCTIONS = (
    "Contain misbehaving Wi-Fi clients and manage rate limits on the INA lab LAN (192.168.50.0/24). "
    "Clients remain associated on Wi-Fi and retain DHCP/DNS access when blocked. "
    "Blocks and bandwidth limits are independent."
)

mcp = MCPServer("ina-network", instructions=SERVER_INSTRUCTIONS)


# ------------------------------------------------------------------------------
# Register Canonical Tools
# ------------------------------------------------------------------------------

@mcp.tool(
    title="Block client traffic",
    annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True, open_world_hint=False),
)
def block_client(client: str, target: str) -> BlockResult:
    """Block IPv4 traffic between a client and a target without disconnecting the client.

    target = another client (both directions), 'gateway' (Pi services except DHCP/DNS),
    'internet' (forwarding to uplink), or 'all' (all three). Bandwidth limits are untouched.
    """
    return tools.block_client(client, target)


@mcp.tool(
    title="Unblock client traffic",
    annotations=ToolAnnotations(destructive_hint=False, idempotent_hint=True, open_world_hint=False),
)
def unblock_client(client: str, target: str) -> UnblockResult:
    """Remove a block rule created by block_client. Bandwidth limits are untouched."""
    return tools.unblock_client(client, target)


@mcp.tool(
    title="Limit client bandwidth",
    annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True, open_world_hint=False),
)
def limit_bandwidth(client: str, download_kbit: int, upload_kbit: int) -> LimitResult:
    """Cap a client's aggregate bandwidth in kbit/s across all destinations.

    download_kbit and upload_kbit must be integers between 256 and 20000 (1000 = 1 Mbit/s).
    """
    return tools.limit_bandwidth(client, download_kbit, upload_kbit)


@mcp.tool(
    title="Remove bandwidth limit",
    annotations=ToolAnnotations(destructive_hint=False, idempotent_hint=True, open_world_hint=False),
)
def remove_bandwidth_limit(client: str) -> RemoveLimitResult:
    """Remove bandwidth limits for a client, restoring unconstrained line rate."""
    return tools.remove_bandwidth_limit(client)


@mcp.tool(
    title="Network status",
    annotations=ToolAnnotations(destructive_hint=False, idempotent_hint=True, open_world_hint=False, read_only_hint=True),
)
def get_status(client: Optional[str] = None) -> StatusResult:
    """Query network status, client associations, active blocks, and bandwidth limits."""
    return tools.get_status(client)


@mcp.tool(
    title="Disconnect Wi-Fi client",
    annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=False, open_world_hint=False),
)
def kick_client(client: str) -> KickResult:
    """Deauthenticate a client from the INA Wi-Fi once. It will normally reconnect within seconds.

    Use ban_client to keep a device off the network. IP blocks and bandwidth limits are untouched.
    """
    return tools.kick_client(client)


@mcp.tool(
    title="Ban Wi-Fi client",
    annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True, open_world_hint=False),
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
    """
    return tools.ban_client(client, duration_minutes, reason, confirm)


@mcp.tool(
    title="Unban Wi-Fi client",
    annotations=ToolAnnotations(destructive_hint=False, idempotent_hint=True, open_world_hint=False),
)
def unban_client(client: str) -> UnbanResult:
    """Remove a ban created by ban_client so the device may associate again."""
    return tools.unban_client(client)


def get_tools_for_role(role: str = "operator") -> list[str]:
    """Return tool names visible to a given role according to authorization."""
    r = role.strip().lower()
    if r == "viewer":
        return ["get_status"]
    if r in ("operator", "admin"):
        return list(mcp.tools.keys())
    return ["get_status"]



def main() -> None:
    """Entrypoint for starting the MCP server over stdio."""
    parser = argparse.ArgumentParser(description="INA MCP Network Enforcement Server")
    parser.add_argument(
        "--role",
        default="operator",
        choices=["admin", "operator", "viewer"],
        help="Caller role token for RBAC enforcement (default: operator)",
    )
    args = parser.parse_args()

    os.environ["INA_ROLE"] = args.role
    mcp.run()


if __name__ == "__main__":
    main()
