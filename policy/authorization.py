"""Role tokens and authentication.

Each role has its own secret token. The MCP server calls ``authenticate`` on
the token that arrives with a request, and the *server* learns the role from it.
No tool takes a ``role`` argument, so the AI cannot choose its own role.

Only SHA-256 hashes of tokens are stored (in environment variables, never in
the repository). Tokens are long random strings, so a plain hash is adequate
for this project; the MCP specification's OAuth 2.1 flow is future work.

Generate a token and its hash on the Pi:

    python -m policy.authorization --new operator
    python -m policy.authorization --new admin
    python -m policy.authorization --new viewer
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

ENV_HASH_VARS = {
    "admin": "INA_ADMIN_TOKEN_SHA256",
    "operator": "INA_OPERATOR_TOKEN_SHA256",
    "viewer": "INA_VIEWER_TOKEN_SHA256",
}
ENV_DEMO_FLAG = "INA_DEMO_TOKENS"
# Matches ROLE_TOKENS in assistant/client.py (plus a viewer token). Only enabled with INA_DEMO_TOKENS=1.
DEMO_TOKENS = {"operator": "operator-token", "admin": "admin-token",
               "viewer": "viewer-token"}
MAX_TOKEN_LEN = 256


class AuthConfigError(Exception):
    """No usable role tokens are configured."""


@dataclass(frozen=True)
class Principal:
    role: str
    token_id: str  # first 8 hex chars of the token hash; safe to log

    @property
    def label(self) -> str:
        return f"{self.role}:{self.token_id}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenAuthenticator:
    def __init__(self, role_hashes: Mapping[str, str], demo: bool = False) -> None:
        if not role_hashes:
            raise AuthConfigError("no role tokens configured")
        cleaned: Dict[str, str] = {}
        for role, digest in role_hashes.items():
            digest = digest.strip().lower()
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise AuthConfigError(f"token hash for role '{role}' is not a SHA-256 hex digest")
            cleaned[role] = digest
        self._hashes = cleaned
        self.demo = demo

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "TokenAuthenticator":
        env = os.environ if env is None else env
        hashes: Dict[str, str] = {}
        for role, var in ENV_HASH_VARS.items():
            value = env.get(var, "").strip()
            if value:
                hashes[role] = value
        demo = env.get(ENV_DEMO_FLAG, "") == "1"
        if demo:
            for role, token in DEMO_TOKENS.items():
                hashes.setdefault(role, hash_token(token))
        if not hashes:
            raise AuthConfigError(
                f"set {ENV_HASH_VARS['admin']} and {ENV_HASH_VARS['operator']} "
                f"(or {ENV_DEMO_FLAG}=1 for a local demo)")
        return cls(hashes, demo=demo)

    def authenticate(self, token: Any) -> Optional[Principal]:
        """Return the Principal for a valid token, else None. Never raises."""
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LEN:
            return None
        digest = hash_token(token)
        match: Optional[Principal] = None
        # Compare against every role (no early exit) in constant time.
        for role, stored in self._hashes.items():
            if hmac.compare_digest(digest, stored) and match is None:
                match = Principal(role, digest[:8])
        return match


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a role token and its hash.")
    parser.add_argument("--new", choices=sorted(ENV_HASH_VARS), required=True)
    args = parser.parse_args(argv)
    token = secrets.token_urlsafe(32)
    print(f"# Give this token to the {args.new} client (keep it secret):")
    print(f"INA_{args.new.upper()}_TOKEN={token}")
    print("# Put ONLY this hash on the Pi (e.g. in .env, never in git):")
    print(f"{ENV_HASH_VARS[args.new]}={hash_token(token)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
