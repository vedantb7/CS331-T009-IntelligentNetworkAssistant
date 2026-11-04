"""MCPServer initialization and entrypoint for INA v2.

Compliant with Model Context Protocol specification (revision 2026-07-28).
Exposes the eight canonical network management tools over stdio transport.

Tool parameters are declared with ``Annotated[..., Field(...)]`` so the SDK
publishes the patterns and ranges in each tool's inputSchema (the model sees
them in tools/list) and rejects non-conforming arguments as an ``isError``
result before the tool function runs.

Transports (the role always comes from a token, never from a flag the caller picks):

    # Deployment: Streamable HTTP on the Pi, TLS, bearer token per request
    python -m inav2.mcp_server.server --host 192.168.50.1 --port 8443 \
        --tls-cert /etc/ina/tls/server.crt --tls-key /etc/ina/tls/server.key

    # Local development: stdio subprocess, token from the environment
    INA_ROLE_TOKEN=<token> python -m inav2.mcp_server.server --transport stdio
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
            print(f"MCPServer '{self.name}' fallback run (SDK not installed)", file=sys.stderr)

try:
    from pydantic import Field
except ImportError:
    def Field(*args, **kwargs):  # type: ignore[no-redef]
        return None

from inav2.mcp_server import tools
from inav2.mcp_server.schemas import (
    CLIENT_OR_MAC_REGEX,
    CLIENT_REGEX,
    MAX_BAN_MINUTES,
    MAX_BANDWIDTH_KBIT,
    MIN_BANDWIDTH_KBIT,
    REASON_REGEX,
    TARGET_REGEX,
    BanResult,
    BlockResult,
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

if MCP_SDK_AVAILABLE:
    from inav2.mcp_server.auth import RoleTokenVerifier, auth_settings

    # Bearer-token auth applies to the HTTP transport; stdio and in-process
    # connections carry no HTTP headers and use INA_ROLE_TOKEN instead.
    mcp = MCPServer("ina-network", instructions=SERVER_INSTRUCTIONS,
                    token_verifier=RoleTokenVerifier(), auth=auth_settings())
else:
    mcp = MCPServer("ina-network", instructions=SERVER_INSTRUCTIONS)


# ------------------------------------------------------------------------------
# Argument Types (published in inputSchema, enforced by the SDK)
# ------------------------------------------------------------------------------

Client = Annotated[str, Field(
    pattern=CLIENT_REGEX,
    description="Client reservation name (e.g. 'guest-phone') or IPv4 address in 192.168.50.0/24.",
)]
ClientOrMac = Annotated[str, Field(
    pattern=CLIENT_OR_MAC_REGEX,
    description="Client reservation name (e.g. 'guest-phone'), IPv4 in 192.168.50.0/24, or MAC address.",
)]
Target = Annotated[str, Field(
    pattern=TARGET_REGEX,
    description="Another client (name or IP), or scope keyword: 'gateway', 'internet', or 'all'.",
)]
Kbit = Annotated[int, Field(
    ge=MIN_BANDWIDTH_KBIT,
    le=MAX_BANDWIDTH_KBIT,
    description=f"Rate in kbit/s ({MIN_BANDWIDTH_KBIT} to {MAX_BANDWIDTH_KBIT}; 1000 = 1 Mbit/s).",
)]
BanMinutes = Annotated[int, Field(
    ge=1,
    le=MAX_BAN_MINUTES,
    description=f"Ban duration in minutes (1 to {MAX_BAN_MINUTES}); omit for a permanent ban.",
)]
Reason = Annotated[str, Field(
    pattern=REASON_REGEX,
    description="Short reason recorded in the audit log (letters, digits, spaces and .,_-; max 120).",
)]


# ------------------------------------------------------------------------------
# Register Canonical Tools
# ------------------------------------------------------------------------------

@mcp.tool(
    title="Block client traffic",
    annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True, open_world_hint=False),
)
def block_client(client: Client, target: Target) -> BlockResult:
    """Block IPv4 traffic between a client and a target without disconnecting the client.

    target = another client (both directions), 'gateway' (Pi services except DHCP/DNS),
    'internet' (forwarding to uplink), or 'all' (all three). Bandwidth limits are untouched.
    """
    return tools.block_client(client, target)


@mcp.tool(
    title="Unblock client traffic",
    annotations=ToolAnnotations(destructive_hint=False, idempotent_hint=True, open_world_hint=False),
)
def unblock_client(client: Client, target: Target) -> UnblockResult:
    """Remove a block rule created by block_client. Bandwidth limits are untouched."""
    return tools.unblock_client(client, target)


@mcp.tool(
    title="Limit client bandwidth",
    annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True, open_world_hint=False),
)
def limit_bandwidth(client: Client, download_kbit: Kbit, upload_kbit: Kbit) -> LimitResult:
    """Cap a client's aggregate bandwidth in kbit/s across all destinations.

    download_kbit and upload_kbit must be integers between 256 and 20000 (1000 = 1 Mbit/s).
    """
    return tools.limit_bandwidth(client, download_kbit, upload_kbit)


@mcp.tool(
    title="Remove bandwidth limit",
    annotations=ToolAnnotations(destructive_hint=False, idempotent_hint=True, open_world_hint=False),
)
def remove_bandwidth_limit(client: Client) -> RemoveLimitResult:
    """Remove bandwidth limits for a client, restoring unconstrained line rate."""
    return tools.remove_bandwidth_limit(client)


@mcp.tool(
    title="Network status",
    annotations=ToolAnnotations(destructive_hint=False, idempotent_hint=True, open_world_hint=False, read_only_hint=True),
)
def get_status(client: Optional[Client] = None) -> StatusResult:
    """Query network status, client associations, active blocks, and bandwidth limits."""
    return tools.get_status(client)


@mcp.tool(
    title="Disconnect Wi-Fi client",
    annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=False, open_world_hint=False),
)
def kick_client(client: ClientOrMac) -> KickResult:
    """Deauthenticate a client from the INA Wi-Fi once. It will normally reconnect within seconds.

    Use ban_client to keep a device off the network. IP blocks and bandwidth limits are untouched.
    """
    return tools.kick_client(client)


@mcp.tool(
    title="Ban Wi-Fi client",
    annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True, open_world_hint=False),
)
def ban_client(
    client: ClientOrMac,
    duration_minutes: Optional[BanMinutes] = None,
    reason: Optional[Reason] = None,
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
def unban_client(client: ClientOrMac) -> UnbanResult:
    """Remove a ban created by ban_client so the device may associate again."""
    return tools.unban_client(client)


def get_tools_for_role(role: str = "operator") -> list[str]:
    """Return tool names a role may call, according to policy/roles.yaml."""
    from inav2.policy.integration import get_tools_for_role as policy_tools_for_role

    return [t for t in policy_tools_for_role(role) if t != "update_policy"]


_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def build_http_app(host: str, port: int, extra_hosts: Optional[list] = None):
    """The Streamable HTTP ASGI app: single /mcp endpoint, POST only.

    stateless_http + json_response: no sessions and no SSE streams, the
    lightest mode, and the shape of revision 2026-07-28 (sessions removed).
    DNS-rebinding protection: the Host header must name this server, and a
    request carrying an Origin header is refused (403); CLI clients send none.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    names = [host, *(extra_hosts or [])]
    allowed_hosts = [n for name in names for n in (name, f"{name}:{port}")]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=[],
    )
    app = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=security,
        host=host,
    )
    return _ClientAddressMiddleware(app)


class _ClientAddressMiddleware:
    """Records each HTTP request's peer IP for the policy guard (device-bound grants).

    The address is the TCP peer, never a header: uvicorn runs with
    proxy_headers=False, so X-Forwarded-For cannot fake it.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        peer = scope.get("client")
        token = tools.CLIENT_ADDRESS.set(peer[0] if peer else None)
        try:
            await self.app(scope, receive, send)
        finally:
            tools.CLIENT_ADDRESS.reset(token)


def serve_http(host: str, port: int, tls_cert: Optional[str] = None, tls_key: Optional[str] = None,
               allow_plain_http: bool = False, extra_hosts: Optional[list] = None) -> None:
    """Run the Streamable HTTP server (blocking)."""
    import uvicorn

    if (tls_cert is None) != (tls_key is None):
        raise SystemExit("--tls-cert and --tls-key must be given together")
    if tls_cert is None and host not in _LOOPBACK and not allow_plain_http:
        raise SystemExit(
            f"Refusing to serve plain HTTP on {host}: bearer tokens would cross the network "
            "unencrypted. Pass --tls-cert/--tls-key (or --allow-plain-http for a lab)."
        )
    # Over HTTP the token comes from each request; never fall back to one in the environment.
    os.environ.pop(tools.TOKEN_ENV, None)
    tools.REQUIRE_REQUEST_TOKEN = True
    uvicorn.run(build_http_app(host, port, extra_hosts), host=host, port=port,
                ssl_certfile=tls_cert, ssl_keyfile=tls_key, log_level="warning",
                proxy_headers=False)  # the peer address binds role tokens; never trust headers


def main() -> None:
    """Entrypoint: Streamable HTTP (default) or stdio."""
    parser = argparse.ArgumentParser(
        description="INA MCP Network Enforcement Server",
        epilog="HTTP: role token per request (Authorization: Bearer). "
               f"stdio: role token from ${tools.TOKEN_ENV}.",
    )
    parser.add_argument("--transport", choices=["streamable-http", "stdio"], default="streamable-http")
    parser.add_argument("--host", default="127.0.0.1", help="Address to bind (HTTP; default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (HTTP; default 8000)")
    parser.add_argument("--tls-cert", default=None, help="TLS certificate (PEM) for HTTPS")
    parser.add_argument("--tls-key", default=None, help="TLS private key (PEM) for HTTPS")
    parser.add_argument("--allowed-host", action="append", default=[],
                        help="Extra Host header name clients may use, e.g. ina-router (repeatable)")
    parser.add_argument("--allow-plain-http", action="store_true",
                        help="Allow HTTP without TLS on a non-loopback address (lab only)")
    args = parser.parse_args()

    if args.transport == "stdio":
        if not os.environ.get(tools.TOKEN_ENV):
            # Not fatal: every call will be denied with R-token, which the client can report.
            print(f"warning: {tools.TOKEN_ENV} is not set; all tool calls will be denied.",
                  file=sys.stderr)
        mcp.run()
        return
    if not MCP_SDK_AVAILABLE:
        raise SystemExit("The MCP Python SDK is required for the HTTP transport (pip install -r requirements.txt).")
    serve_http(args.host, args.port, args.tls_cert, args.tls_key, args.allow_plain_http, args.allowed_host)


if __name__ == "__main__":
    main()
