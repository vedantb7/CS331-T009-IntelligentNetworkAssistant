"""Role tokens and authentication.

Nobody has a role by default. Roles come from per-person grants the admin
creates on the Pi (``sudo ina-netctl roles grant <person> <role> --device
<device>``, see policy/grants.py): each grant has its own secret token and is
bound to one registered device. The MCP server calls ``authenticate`` on the
token that arrives with a request, and the *server* learns the person, role and
bound device from it. No tool takes a ``role`` argument, so the AI cannot
choose its own role.

Only SHA-256 hashes of tokens are stored, never the tokens. Tokens are long
random strings, so a plain hash is adequate for this project; the MCP
specification's OAuth 2.1 flow is future work.

Also accepted, for compatibility and local development:
- one shared hash per role in INA_<ROLE>_TOKEN_SHA256 (not person- or
  device-bound; prefer grants);
- the fixed demo tokens with INA_DEMO_TOKENS=1 (never on the Pi).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .grants import Grant, GrantError, grants_path, load_grants

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
    token_id: str                  # first 8 hex chars of the token hash; safe to log
    name: Optional[str] = None     # person, for a grant
    device: Optional[str] = None   # registered device the grant is bound to

    @property
    def label(self) -> str:
        return f"{self.name}:{self.role}" if self.name else f"{self.role}:{self.token_id}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenAuthenticator:
    def __init__(self, role_hashes: Mapping[str, str], demo: bool = False,
                 grants_file: Optional[Path] = None) -> None:
        if not role_hashes and grants_file is None:
            raise AuthConfigError("no role tokens configured")
        self._grants_file = grants_file
        self._grants_stamp: Optional[Tuple[int, int]] = None
        self._grants: List[Grant] = []
        cleaned: Dict[str, str] = {}
        for role, digest in role_hashes.items():
            digest = digest.strip().lower()
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise AuthConfigError(f"token hash for role '{role}' is not a SHA-256 hex digest")
            cleaned[role] = digest
        self._hashes = cleaned
        self.demo = demo
        self._refresh_grants()

    def _refresh_grants(self) -> None:
        """Re-read the grants file when it changed (so a revoke applies at once)."""
        if self._grants_file is None:
            return
        try:
            st = self._grants_file.stat()
            stamp: Optional[Tuple[int, int]] = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        if stamp == self._grants_stamp:
            return
        try:
            self._grants = load_grants(str(self._grants_file)) if stamp else []
        except GrantError:
            self._grants = []  # unreadable file: fail closed (no grant matches)
        self._grants_stamp = stamp

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
        path = grants_path(env.get("INA_GRANTS_FILE") or None)
        if not hashes and not path.exists():
            raise AuthConfigError(
                f"no role grants yet: run 'sudo ina-netctl roles grant <person> <role> "
                f"--device <device>' (or {ENV_DEMO_FLAG}=1 for a local demo)")
        # Always watched: grants added (or revoked) later are picked up per request.
        return cls(hashes, demo=demo, grants_file=path)

    def authenticate(self, token: Any) -> Optional[Principal]:
        """Return the Principal for a valid token, else None. Never raises."""
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LEN:
            return None
        self._refresh_grants()
        digest = hash_token(token)
        match: Optional[Principal] = None
        # Compare against every grant and role (no early exit) in constant time.
        for g in self._grants:
            if hmac.compare_digest(digest, g.token_sha256) and match is None:
                match = Principal(g.role, digest[:8], name=g.name, device=g.device)
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
