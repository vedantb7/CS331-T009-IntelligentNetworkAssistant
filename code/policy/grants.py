"""Per-person role grants, each bound to one registered device.

Nobody has a role by default: a device on the Wi-Fi is just a client. The
admin grants a role to a named person, on the Pi:

    sudo ina-netctl roles grant dhruv admin --device admin-laptop
    sudo ina-netctl roles list
    sudo ina-netctl roles rotate dhruv        # new token, old one stops working
    sudo ina-netctl roles revoke dhruv

A grant stores only the SHA-256 hash of a random token, printed once at grant
time. The token works only from the bound device: requests must come from that
device's fixed IP in devices.yaml, which nftables anti-spoofing ties to its MAC
(``ina-netctl sync-devices``). The grants file (``/etc/ina/grants.json``,
root:ina 0640; override with INA_GRANTS_FILE) is re-read when it changes, so a
revoke takes effect on the next request.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

DEFAULT_GRANTS_FILE = "/etc/ina/grants.json"
ENV_GRANTS_FILE = "INA_GRANTS_FILE"
SERVICE_GROUP = "ina"
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


class GrantError(Exception):
    """A grant request is invalid, or the grants file cannot be used."""


@dataclass(frozen=True)
class Grant:
    name: str
    role: str
    device: str
    token_sha256: str
    granted_at: str

    @property
    def token_id(self) -> str:
        return self.token_sha256[:8]

    def public(self) -> dict:
        """Everything except the hash, for listings."""
        return {"name": self.name, "role": self.role, "device": self.device,
                "token_id": self.token_id, "granted_at": self.granted_at}


def grants_path(path: Optional[str] = None) -> Path:
    return Path(path or os.environ.get(ENV_GRANTS_FILE) or DEFAULT_GRANTS_FILE)


def load_grants(path: Optional[str] = None) -> List[Grant]:
    """Grants from the file; an absent file means no grants."""
    p = grants_path(path)
    if not p.is_file():
        return []
    try:
        text = p.read_text(encoding="utf-8")
        if not text.strip():
            return []  # created empty at install time
        doc = json.loads(text)
        return [Grant(**{k: g[k] for k in ("name", "role", "device", "token_sha256", "granted_at")})
                for g in doc.get("grants", [])]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise GrantError(f"grants file {p} is unreadable or malformed: {e}") from e


def save_grants(grants: List[Grant], path: Optional[str] = None) -> Path:
    """Atomically rewrite the grants file, readable by the MCP service group only."""
    p = grants_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"version": 1, "grants": [asdict(g) for g in grants]}
    with tempfile.NamedTemporaryFile("w", dir=p.parent, delete=False, encoding="utf-8") as tf:
        json.dump(doc, tf, indent=2)
        tf.write("\n")
        tf.flush()
        os.fsync(tf.fileno())
        temp_name = tf.name
    os.chmod(temp_name, 0o640)
    if os.geteuid() == 0:
        try:
            import grp
            os.chown(temp_name, 0, grp.getgrnam(SERVICE_GROUP).gr_gid)
        except KeyError:
            pass  # no 'ina' group yet (not installed); stays root:root
    os.replace(temp_name, p)
    return p


def _hash(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validate(name: str, role: str, device: str, policy_dir: Optional[str]) -> None:
    from .engine import PolicyEngine

    if not NAME_RE.match(name or ""):
        raise GrantError("person name must be lower-case letters, digits and '-' (max 32), "
                         "starting with a letter")
    engine = PolicyEngine.from_files(policy_dir)
    if role not in engine.config.roles:
        raise GrantError(f"unknown role '{role}' (roles.yaml has: {', '.join(sorted(engine.config.roles))})")
    dev = engine.registry.resolve(device)
    if dev is None:
        raise GrantError(f"device '{device}' is not in devices.yaml; register it first")
    if not dev.ip:
        raise GrantError(f"device '{device}' needs a fixed 'ip' in devices.yaml (then run "
                         "'ina-netctl sync-devices'): the role is bound to that address")


def grant(name: str, role: str, device: str, path: Optional[str] = None,
          policy_dir: Optional[str] = None) -> Tuple[Grant, str]:
    """Create a grant. Returns (grant, plain token); the token is never stored."""
    _validate(name, role, device, policy_dir)
    grants = load_grants(path)
    if any(g.name == name for g in grants):
        raise GrantError(f"'{name}' already has a role; revoke it first, or rotate the token")
    token = secrets.token_urlsafe(32)
    new = Grant(name=name, role=role, device=device, token_sha256=_hash(token),
                granted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    save_grants(grants + [new], path)
    return new, token


def revoke(name: str, path: Optional[str] = None) -> Optional[Grant]:
    """Remove a person's grant. Returns the removed grant, or None if there was none."""
    grants = load_grants(path)
    removed = next((g for g in grants if g.name == name), None)
    if removed is not None:
        save_grants([g for g in grants if g.name != name], path)
    return removed


def rotate(name: str, path: Optional[str] = None) -> Tuple[Grant, str]:
    """Replace a person's token (same role and device); the old token stops working."""
    grants = load_grants(path)
    old = next((g for g in grants if g.name == name), None)
    if old is None:
        raise GrantError(f"'{name}' has no role to rotate")
    token = secrets.token_urlsafe(32)
    new = Grant(name=old.name, role=old.role, device=old.device, token_sha256=_hash(token),
                granted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    save_grants([new if g.name == name else g for g in grants], path)
    return new, token
