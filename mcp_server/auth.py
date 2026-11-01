"""Bearer-token authentication for the INA MCP server's Streamable HTTP transport.

Every HTTP request to the MCP endpoint carries ``Authorization: Bearer <role
token>``. The MCP SDK's bearer middleware hands the token to
:class:`RoleTokenVerifier`; a missing or unknown token is answered with
``401 Unauthorized`` before any MCP message is processed. A verified token is
then available to the tools through ``get_access_token()``, and the policy
guard turns it into the caller's role (the same SHA-256 hashes,
``INA_*_TOKEN_SHA256``, see policy/authorization.py).

The tokens are pre-provisioned role tokens, not OAuth-issued ones: the MCP
Authorization spec's OAuth flow (a SHOULD for HTTP transports) is not
implemented. The metadata URLs below only describe this resource server.
"""

from __future__ import annotations

import os
from typing import Optional

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

from inav2.policy.authorization import AuthConfigError, TokenAuthenticator

# Public base URL of the server (used in the protected-resource metadata).
DEFAULT_PUBLIC_URL = "https://192.168.50.1:8443"


class RoleTokenVerifier:
    """SDK TokenVerifier that accepts exactly the configured INA role tokens."""

    def __init__(self, authenticator: Optional[TokenAuthenticator] = None) -> None:
        self._authenticator = authenticator

    def _auth(self) -> Optional[TokenAuthenticator]:
        if self._authenticator is None:
            try:
                self._authenticator = TokenAuthenticator.from_env()
            except AuthConfigError:
                return None  # no tokens configured: every request is 401
        return self._authenticator

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        auth = self._auth()
        principal = auth.authenticate(token) if auth else None
        if principal is None:
            return None
        return AccessToken(token=token, client_id=principal.label, scopes=[principal.role])


def auth_settings(public_url: Optional[str] = None) -> AuthSettings:
    base = (public_url or os.environ.get("INA_MCP_PUBLIC_URL") or DEFAULT_PUBLIC_URL).rstrip("/")
    return AuthSettings(
        issuer_url=base,
        resource_server_url=f"{base}/mcp",
        # RoleTokenVerifier checks the token itself; role tokens carry no audience.
        validate_token_resource=False,
    )
