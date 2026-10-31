"""Loading and validating roles.yaml and rules.yaml.

The loader fails closed: any missing key, wrong type or unsafe combination
raises ``PolicyConfigError`` and the system refuses to start, instead of running
with a half-understood rulebook.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, Mapping, Optional, Pattern

from .models import (
    ACTION_BAN, ACTION_LIMIT, ACTION_UNBAN, ALL_ACTIONS, DEVICE_CLASSES,
    MUTATING_ACTIONS, POLICY_ACTIONS,
)

UNBAN_SCOPES = ("any", "own_role")
_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


class PolicyConfigError(Exception):
    """The policy files are missing, malformed or unsafe."""


@dataclass(frozen=True)
class BanSpec:
    max_minutes: int
    allow_permanent: bool
    unban_scope: str


@dataclass(frozen=True)
class RoleSpec:
    name: str
    description: str
    actions: FrozenSet[str]
    device_classes: FrozenSet[str]
    min_kbit: Optional[int] = None
    max_kbit: Optional[int] = None
    ban: Optional[BanSpec] = None


@dataclass(frozen=True)
class PolicyConfig:
    roles: Mapping[str, RoleSpec]
    protected_classes: FrozenSet[str]
    protected_devices: FrozenSet[str]
    protected_macs: FrozenSet[str]
    protected_ips: FrozenSet[str]
    managed_subnet: ipaddress.IPv4Network
    name_pattern: Pattern[str]
    block_targets: FrozenSet[str]
    hard_min_kbit: int
    hard_max_kbit: int
    hard_max_ban_minutes: int
    reason_pattern: Pattern[str]


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyConfigError(f"cannot read {path}: {exc}") from exc
    try:
        import yaml  # PyYAML, preferred when installed
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise PolicyConfigError(f"cannot parse {path}: {exc}") from exc
    except ImportError:
        from . import _yaml
        try:
            data = _yaml.safe_load(content)
        except ValueError as exc:
            raise PolicyConfigError(f"cannot parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyConfigError(f"{path} must contain a mapping at the top level")
    return data


def _need(mapping: Any, key: str, where: str) -> Any:
    if not isinstance(mapping, Mapping):
        raise PolicyConfigError(f"{where}: expected a mapping")
    if key not in mapping:
        raise PolicyConfigError(f"{where}: missing required key '{key}'")
    return mapping[key]


def _int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PolicyConfigError(f"{where}: expected an integer, got {value!r}")
    return value


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise PolicyConfigError(f"{where}: expected true or false, got {value!r}")
    return value


def _string_list(value: Any, where: str) -> list:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PolicyConfigError(f"{where}: expected a list of strings")
    return value


def _pattern(cfg: Any, where: str) -> Pattern[str]:
    raw = _need(cfg, "pattern", where)
    if not isinstance(raw, str):
        raise PolicyConfigError(f"{where}: pattern must be a string")
    try:
        return re.compile(raw)
    except re.error as exc:
        raise PolicyConfigError(f"{where}: bad pattern: {exc}") from exc


def load_policy_config(roles_path: Path, rules_path: Path) -> PolicyConfig:
    roles_doc = _load_yaml(Path(roles_path))
    rules_doc = _load_yaml(Path(rules_path))

    # --- rules.yaml -------------------------------------------------------
    if _need(rules_doc, "default_decision", "rules.yaml") != "deny":
        raise PolicyConfigError("rules.yaml: default_decision must be 'deny'")

    protected_classes = frozenset(_string_list(
        _need(rules_doc, "protected_classes", "rules.yaml"), "rules.yaml protected_classes"))
    for cls in protected_classes:
        if cls not in DEVICE_CLASSES:
            raise PolicyConfigError(f"rules.yaml: unknown protected class '{cls}'")

    protected_devices = frozenset(n.strip().lower() for n in _string_list(
        rules_doc.get("protected_devices", []), "rules.yaml protected_devices"))

    protected_macs = frozenset(m.strip().lower() for m in _string_list(
        rules_doc.get("protected_macs", []), "rules.yaml protected_macs"))
    for mac in protected_macs:
        if not _MAC_RE.match(mac):
            raise PolicyConfigError(f"rules.yaml: invalid protected MAC '{mac}'")

    try:
        subnet = ipaddress.IPv4Network(_need(rules_doc, "managed_subnet", "rules.yaml"))
    except (ValueError, TypeError) as exc:
        raise PolicyConfigError(f"rules.yaml: bad managed_subnet: {exc}") from exc

    protected_ips = set()
    for ip in _string_list(_need(rules_doc, "protected_ips", "rules.yaml"),
                           "rules.yaml protected_ips"):
        try:
            protected_ips.add(str(ipaddress.IPv4Address(ip.strip())))
        except ValueError as exc:
            raise PolicyConfigError(f"rules.yaml: invalid protected IP '{ip}'") from exc

    name_pattern = _pattern(_need(rules_doc, "client_name", "rules.yaml"), "rules.yaml client_name")
    reason_pattern = _pattern(_need(rules_doc, "ban_reason", "rules.yaml"), "rules.yaml ban_reason")

    block_targets = frozenset(_string_list(
        _need(rules_doc, "block_targets", "rules.yaml"), "rules.yaml block_targets"))
    if not block_targets <= {"gateway", "internet", "all"}:
        raise PolicyConfigError("rules.yaml: block_targets may only use gateway, internet, all")

    bw = _need(rules_doc, "bandwidth_kbit", "rules.yaml")
    hard_min = _int(_need(bw, "hard_min", "rules.yaml bandwidth_kbit"), "hard_min")
    hard_max = _int(_need(bw, "hard_max", "rules.yaml bandwidth_kbit"), "hard_max")
    if not 0 < hard_min <= hard_max:
        raise PolicyConfigError("rules.yaml: need 0 < hard_min <= hard_max")
    ban_hard_max = _int(_need(_need(rules_doc, "ban_minutes", "rules.yaml"), "hard_max",
                              "rules.yaml ban_minutes"), "ban_minutes hard_max")
    if ban_hard_max < 1:
        raise PolicyConfigError("rules.yaml: ban_minutes hard_max must be >= 1")

    # --- roles.yaml -------------------------------------------------------
    raw_roles = _need(roles_doc, "roles", "roles.yaml")
    if not isinstance(raw_roles, dict) or not raw_roles:
        raise PolicyConfigError("roles.yaml: 'roles' must be a non-empty mapping")
    roles: Dict[str, RoleSpec] = {}
    for name, body in raw_roles.items():
        where = f"roles.yaml role '{name}'"
        if not isinstance(name, str) or not isinstance(body, dict):
            raise PolicyConfigError(f"{where}: must be a mapping")
        actions = frozenset(_string_list(_need(body, "actions", where), f"{where} actions"))
        for action in actions:
            if action not in ALL_ACTIONS:
                raise PolicyConfigError(f"{where}: unknown action '{action}'")
        classes = frozenset(_string_list(
            _need(body, "device_classes", where), f"{where} device_classes"))
        for cls in classes:
            if cls not in DEVICE_CLASSES:
                raise PolicyConfigError(f"{where}: unknown device class '{cls}'")
            if cls in protected_classes:
                raise PolicyConfigError(f"{where}: may not be granted protected class '{cls}'")
        if actions & set(MUTATING_ACTIONS) and not classes:
            raise PolicyConfigError(f"{where}: mutating actions need device_classes")

        lo = hi = None
        if ACTION_LIMIT in actions:
            bounds = _need(body, "bandwidth_kbit", where)
            lo = _int(_need(bounds, "min", where), f"{where} bandwidth min")
            hi = _int(_need(bounds, "max", where), f"{where} bandwidth max")
            if not hard_min <= lo <= hi <= hard_max:
                raise PolicyConfigError(
                    f"{where}: bandwidth must satisfy hard_min <= min <= max <= hard_max")

        ban = None
        if actions & {ACTION_BAN, ACTION_UNBAN}:
            raw_ban = _need(body, "ban", where)
            ban = BanSpec(
                max_minutes=_int(_need(raw_ban, "max_minutes", where), f"{where} ban max_minutes"),
                allow_permanent=_bool(_need(raw_ban, "allow_permanent", where),
                                      f"{where} ban allow_permanent"),
                unban_scope=_need(raw_ban, "unban_scope", where),
            )
            if not 1 <= ban.max_minutes <= ban_hard_max:
                raise PolicyConfigError(f"{where}: ban max_minutes must be 1..{ban_hard_max}")
            if ban.unban_scope not in UNBAN_SCOPES:
                raise PolicyConfigError(f"{where}: unban_scope must be one of {UNBAN_SCOPES}")

        roles[name] = RoleSpec(name, str(body.get("description", "")),
                               actions, classes, lo, hi, ban)

    if "admin" not in roles or "operator" not in roles:
        raise PolicyConfigError("roles.yaml must define both 'admin' and 'operator'")
    # Hard invariant: only the admin may hold policy-management actions.
    for name, spec in roles.items():
        if name != "admin" and spec.actions & set(POLICY_ACTIONS):
            raise PolicyConfigError(f"role '{name}' may not hold policy-management actions")

    return PolicyConfig(
        roles=roles,
        protected_classes=protected_classes,
        protected_devices=protected_devices,
        protected_macs=protected_macs,
        protected_ips=frozenset(protected_ips),
        managed_subnet=subnet,
        name_pattern=name_pattern,
        block_targets=block_targets,
        hard_min_kbit=hard_min,
        hard_max_kbit=hard_max,
        hard_max_ban_minutes=ban_hard_max,
        reason_pattern=reason_pattern,
    )
