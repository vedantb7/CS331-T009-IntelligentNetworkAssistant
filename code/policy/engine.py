"""Independent policy engine.

``PolicyEngine.evaluate(role, tool, arguments, context)`` returns an allow/deny
decision with a reason and a rule ID for one of the eight MCP tools in
``docs/implementation.md``. It is a pure function of the loaded rulebook and device list:
no network calls, no clock, no side effects, so it is easy to test.

The AI never reaches this code directly and never supplies the role: the guard
derives the role from the caller's token first (see authorization.py, guard.py).

``arguments`` are the tool arguments exactly as the AI proposed them (untrusted).
``context`` is optional, trusted data from the server, never from the AI:

    client_ip, client_mac   what NetworkService resolved the client to
    target_ip, target_mac   the same for a peer target of block/unblock
    banned_by_role          for unban_client: role that created the ban
                            (None if the device is not banned)

Check order (first deny wins; anything not explicitly allowed is denied):

    role known -> tool known -> role may use tool -> arguments well-formed
      -> (get_status / update_policy end here)
      -> client valid -> client resolves to exactly one device (else unknown /
         ambiguous / conflicting identity) -> not protected
      -> role may act on this device class
      -> tool-specific checks:
           block/unblock  target is a scope keyword or a valid, non-protected peer
           limit          download/upload are whole kbit/s inside the role's range
           ban            duration inside the role's limit; reason well-formed
           unban          role may lift this ban (unban_scope)
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from .config import PolicyConfig, RoleSpec, load_policy_config
from .models import (
    ACTION_BAN, ACTION_BLOCK, ACTION_KICK, ACTION_LIMIT, ACTION_STATUS,
    ACTION_UNBAN, ACTION_UNBLOCK, ACTION_UNLIMIT, ACTION_UPDATE_POLICY,
    ALL_ACTIONS, LEGACY_ALIASES, LEGACY_BLOCK, LEGACY_RESTORE, MAC_CLIENT_ACTIONS,
    TOOL_ACTIONS, UNKNOWN_CLASS, Device, PolicyDecision, allow, deny,
    R_AMBIGUOUS_DEVICE, R_BAD_ARGUMENTS, R_BAD_DEVICE, R_BAD_RATE, R_BAD_REASON,
    R_BAD_TARGET, R_BAN, R_BAN_DURATION, R_BLOCK, R_IDENTITY_CONFLICT, R_KICK,
    R_LIMIT, R_POLICY_ADMIN, R_PROTECTED, R_RATE_RANGE, R_RESTORE, R_ROLE,
    R_ROLE_ACTION, R_ROLE_CLASS, R_SELF_TARGET, R_STATUS, R_UNBAN, R_UNBAN_SCOPE,
    R_UNBLOCK, R_UNKNOWN_DEVICE, R_UNKNOWN_TOOL, R_UNLIMIT,
)
from .registry import DeviceRegistry
from .validation import (
    KIND_IP, KIND_MAC, classify_identifier, clean_for_log, mbit_to_kbit,
    parse_kbit, parse_mbit, parse_minutes,
)

DEFAULT_POLICY_DIR = Path(__file__).resolve().parent

# Argument names each tool accepts (mcp_server/schemas.py, extra="forbid").
# "device", "mbit" and "minutes" are legacy spellings still used by the assistant.
_ARGS: Dict[str, frozenset] = {
    ACTION_BLOCK: frozenset({"client", "target"}),
    ACTION_UNBLOCK: frozenset({"client", "target"}),
    ACTION_LIMIT: frozenset({"client", "download_kbit", "upload_kbit", "mbit"}),
    ACTION_UNLIMIT: frozenset({"client"}),
    ACTION_STATUS: frozenset({"client"}),
    ACTION_KICK: frozenset({"client"}),
    ACTION_BAN: frozenset({"client", "duration_minutes", "reason", "confirm"}),
    ACTION_UNBAN: frozenset({"client"}),
    ACTION_UPDATE_POLICY: frozenset(),
}
_RENAMES = {"device": "client", "minutes": "duration_minutes"}


@dataclass(frozen=True)
class Evaluation:
    """The decision plus the *validated* values the executor should use."""

    decision: PolicyDecision
    tool: Optional[str] = None               # canonical tool name
    device: Optional[Device] = None          # the client, resolved
    target: Optional[str] = None             # "gateway"/"internet"/"all" or the peer's name/IP
    target_device: Optional[Device] = None   # the peer, when target is another client
    params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision.allowed


class PolicyEngine:
    def __init__(self, config: PolicyConfig, registry: DeviceRegistry) -> None:
        self.config = config
        self.registry = registry

    @classmethod
    def from_files(cls, policy_dir: Union[str, Path, None] = None) -> "PolicyEngine":
        base = Path(policy_dir) if policy_dir is not None else DEFAULT_POLICY_DIR
        config = load_policy_config(base / "roles.yaml", base / "rules.yaml")
        registry = DeviceRegistry.from_file(base / "devices.yaml", config.name_pattern)
        return cls(config, registry)

    # ------------------------------------------------------------------ API
    def tools_for_role(self, role: str) -> List[str]:
        """The MCP tools this role may call, in docs/implementation.md order."""
        spec = self.config.roles.get(role)
        return [t for t in TOOL_ACTIONS if spec and t in spec.actions]

    def is_protected(self, device: Device) -> bool:
        cfg = self.config
        return (device.cls in cfg.protected_classes
                or device.name in cfg.protected_devices
                or (device.mac is not None and device.mac in cfg.protected_macs)
                or (device.ip is not None and device.ip in cfg.protected_ips))

    def evaluate(self, role: Any, tool: Any, arguments: Any = None,
                 context: Optional[Mapping[str, Any]] = None) -> Evaluation:
        cfg = self.config

        if not isinstance(role, str) or role not in cfg.roles:
            return Evaluation(deny(R_ROLE, "No valid role for this request."))
        spec = cfg.roles[role]

        if not isinstance(tool, str):
            return Evaluation(deny(R_UNKNOWN_TOOL, "That action is not one of the allowed tools."))
        canonical = LEGACY_ALIASES.get(tool, tool)
        if canonical not in ALL_ACTIONS:
            return Evaluation(deny(R_UNKNOWN_TOOL, "That action is not one of the allowed tools."))
        needed = {canonical} | ({ACTION_UNLIMIT} if tool == LEGACY_RESTORE else set())
        if not needed <= spec.actions:
            return Evaluation(deny(R_ROLE_ACTION,
                                   f"Role '{role}' is not permitted to use '{tool}'."), canonical)

        args, problem = self._normalise_arguments(tool, canonical, arguments)
        if problem is not None:
            return Evaluation(problem, canonical)
        ctx: Mapping[str, Any] = context if isinstance(context, Mapping) else {}

        if canonical == ACTION_UPDATE_POLICY:
            return Evaluation(allow(R_POLICY_ADMIN, "Admin may manage policy."), canonical)
        if canonical == ACTION_STATUS:
            return self._status(role, args, canonical)

        # ---- client-targeted actions -------------------------------------
        device, problem = self._resolve(args.get("client"), canonical in MAC_CLIENT_ACTIONS,
                                        ctx.get("client_ip"), ctx.get("client_mac"))
        if problem is not None:
            return Evaluation(problem, canonical)
        problem = self._may_act_on(role, spec, device)
        if problem is not None:
            return Evaluation(problem, canonical, device)

        if canonical in (ACTION_BLOCK, ACTION_UNBLOCK):
            return self._block(role, spec, tool, canonical, device, args, ctx)
        if canonical == ACTION_LIMIT:
            return self._limit(role, spec, canonical, device, args)
        if canonical == ACTION_UNLIMIT:
            return Evaluation(allow(R_UNLIMIT, f"Removing the limit on '{device.name}' is permitted."),
                              canonical, device)
        if canonical == ACTION_KICK:
            return Evaluation(allow(R_KICK, f"Disconnecting '{device.name}' is permitted."),
                              canonical, device)
        if canonical == ACTION_BAN:
            return self._ban(role, spec, canonical, device, args)
        if canonical == ACTION_UNBAN:
            return self._unban(role, spec, canonical, device, ctx)

        # Unreachable while every action is handled above; default stays deny.
        return Evaluation(deny(R_UNKNOWN_TOOL, "Unhandled action."), canonical)

    # -------------------------------------------------------------- helpers
    def _normalise_arguments(self, tool: str, canonical: str,
                             arguments: Any) -> Tuple[Dict[str, Any], Optional[PolicyDecision]]:
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping):
            return {}, deny(R_BAD_ARGUMENTS, "Tool arguments must be a mapping of names to values.")
        args: Dict[str, Any] = {}
        for key, value in arguments.items():
            if not isinstance(key, str):
                return {}, deny(R_BAD_ARGUMENTS, "Argument names must be text.")
            name = _RENAMES.get(key, key)
            if name in args and args[name] != value:
                return {}, deny(R_BAD_ARGUMENTS, f"Conflicting values given for '{name}'.")
            args[name] = value
        allowed = _ARGS[canonical]
        if tool in (LEGACY_BLOCK, LEGACY_RESTORE):
            allowed = frozenset({"client"})
        extra = sorted(set(args) - allowed)
        if extra:
            shown = ", ".join(clean_for_log(k, 24) for k in extra[:5])
            return {}, deny(R_BAD_ARGUMENTS, f"Unexpected argument(s) for '{tool}': {shown}.")
        if tool in (LEGACY_BLOCK, LEGACY_RESTORE):
            args["target"] = "all"
        return args, None

    def _status(self, role: str, args: Mapping[str, Any], canonical: str) -> Evaluation:
        raw = args.get("client")
        if raw is None:
            return Evaluation(allow(R_STATUS, f"Status is read-only and permitted for role '{role}'."),
                              canonical)
        ident = classify_identifier(raw, self.config.name_pattern)
        if ident is None:
            return Evaluation(deny(R_BAD_DEVICE, "The status filter is not a valid name, "
                                                 "IPv4 address or MAC address."), canonical)
        kind, value = ident
        if kind == KIND_MAC:
            return Evaluation(deny(R_BAD_DEVICE, "get_status filters by name or IPv4 address, "
                                                 "not by MAC address."), canonical)
        device = (self.registry.by_ip(value) if kind == KIND_IP else self.registry.resolve(value))
        return Evaluation(allow(R_STATUS, f"Status is read-only and permitted for role '{role}'."),
                          canonical, device)

    def _resolve(self, raw: Any, allow_mac: bool, ctx_ip: Any = None, ctx_mac: Any = None,
                 what: str = "device") -> Tuple[Optional[Device], Optional[PolicyDecision]]:
        """Turn an untrusted identifier (+ trusted server context) into one Device."""
        cfg, reg = self.config, self.registry
        ident = classify_identifier(raw, cfg.name_pattern)
        if ident is None:
            # Deliberately do not echo the raw text back.
            return None, deny(R_BAD_DEVICE,
                              f"The {what} is missing or not a valid name (lower-case letters, "
                              f"digits, '-'), IPv4 address or MAC address.")
        kind, value = ident
        if kind == KIND_MAC and not allow_mac:
            return None, deny(R_BAD_DEVICE, "A MAC address is only accepted by kick_client, "
                                            "ban_client and unban_client; use a name or IP.")

        # Trusted context from the server (what it resolved the identifier to).
        c_ip = classify_identifier(ctx_ip, cfg.name_pattern) if ctx_ip else None
        c_mac = classify_identifier(ctx_mac, cfg.name_pattern) if ctx_mac else None
        c_ip = c_ip[1] if c_ip and c_ip[0] == KIND_IP else None
        c_mac = c_mac[1] if c_mac and c_mac[0] == KIND_MAC else None

        ip = value if kind == KIND_IP else c_ip
        mac = value if kind == KIND_MAC else c_mac
        if ip is not None and ip not in cfg.protected_ips:
            addr = ipaddress.IPv4Address(ip)
            net = cfg.managed_subnet
            if addr not in net or addr in (net.network_address, net.broadcast_address):
                return None, deny(R_BAD_DEVICE, f"{ip} is not a client address in {net}.")

        # Every registered device the identifier or the context points at.
        matches: Dict[str, Device] = {}
        if kind == KIND_IP and reg.by_ip(value):
            matches[reg.by_ip(value).name] = reg.by_ip(value)
        elif kind == KIND_MAC and reg.by_mac(value):
            matches[reg.by_mac(value).name] = reg.by_mac(value)
        elif kind not in (KIND_IP, KIND_MAC) and reg.resolve(value):
            matches[value] = reg.resolve(value)
        for found in (reg.by_ip(c_ip) if c_ip else None, reg.by_mac(c_mac) if c_mac else None):
            if found is not None:
                matches[found.name] = found

        conflict = deny(R_IDENTITY_CONFLICT,
                        f"The {what}'s name, IP and MAC address do not agree with the device "
                        f"list; refusing to act on a possibly spoofed identity.")
        if len(matches) > 1:
            return None, conflict
        if matches:
            device = next(iter(matches.values()))
            if (mac and device.mac and mac != device.mac) or (ip and device.ip and ip != device.ip):
                return None, conflict
            return replace(device, ip=device.ip or ip), None

        if kind not in (KIND_IP, KIND_MAC) and not (c_ip or c_mac):
            # An unregistered name that the server has not resolved: never guess.
            near = reg.candidates(value)
            if near:
                return None, deny(R_AMBIGUOUS_DEVICE,
                                  f"'{value}' does not exactly match a device. Did you mean: "
                                  f"{', '.join(near)}?", tuple(near))
            return None, deny(R_UNKNOWN_DEVICE, f"'{value}' is not a recognized device.")
        return Device(value, mac, UNKNOWN_CLASS, ip, registered=False), None

    def _may_act_on(self, role: str, spec: RoleSpec, device: Device,
                    what: str = "") -> Optional[PolicyDecision]:
        if self.is_protected(device):
            return deny(R_PROTECTED,
                        f"{what}'{device.name}' is a protected device and cannot be changed.")
        if device.cls not in spec.device_classes:
            return deny(R_ROLE_CLASS, f"Role '{role}' may not act on {device.cls} devices.")
        return None

    def _block(self, role: str, spec: RoleSpec, tool: str, canonical: str, device: Device,
               args: Mapping[str, Any], ctx: Mapping[str, Any]) -> Evaluation:
        raw = args.get("target")
        if not isinstance(raw, str) or not raw.strip():
            return Evaluation(deny(R_BAD_TARGET, "A target is required: another client, "
                                                 "or 'gateway', 'internet' or 'all'."),
                              canonical, device)
        keyword = raw.strip().lower()
        verb = "Blocking" if canonical == ACTION_BLOCK else "Unblocking"
        if keyword in self.config.block_targets:
            if tool == LEGACY_RESTORE:
                return Evaluation(allow(R_RESTORE, f"Restoring '{device.name}' is permitted."),
                                  canonical, device, keyword)
            rule = R_BLOCK if canonical == ACTION_BLOCK else R_UNBLOCK
            return Evaluation(allow(rule, f"{verb} '{device.name}' from {keyword} is permitted."),
                              canonical, device, keyword)

        peer, problem = self._resolve(raw, False, ctx.get("target_ip"), ctx.get("target_mac"),
                                      what="target")
        if problem is not None:
            if problem.rule == R_BAD_DEVICE:
                problem = deny(R_BAD_TARGET, "The target must be another client (name or IP) "
                                             "or one of 'gateway', 'internet', 'all'.")
            return Evaluation(problem, canonical, device)
        if (peer.name == device.name or (peer.ip and peer.ip == device.ip)
                or (peer.mac and peer.mac == device.mac)):
            return Evaluation(deny(R_SELF_TARGET, "A client cannot be blocked from itself."),
                              canonical, device)
        # A pair rule changes the peer's connectivity too, so the peer must also
        # be a device this role may act on.
        problem = self._may_act_on(role, spec, peer, what="target ")
        if problem is not None:
            return Evaluation(problem, canonical, device, peer.name, peer)
        rule = R_BLOCK if canonical == ACTION_BLOCK else R_UNBLOCK
        return Evaluation(allow(rule, f"{verb} traffic between '{device.name}' and "
                                      f"'{peer.name}' is permitted."),
                          canonical, device, peer.ip or peer.name, peer)

    def _limit(self, role: str, spec: RoleSpec, canonical: str, device: Device,
               args: Mapping[str, Any]) -> Evaluation:
        cfg = self.config
        lo = max(spec.min_kbit or 0, cfg.hard_min_kbit)
        hi = min(spec.max_kbit or 0, cfg.hard_max_kbit)

        if "mbit" in args:  # legacy single rate in Mbit/s, applied both ways
            if "download_kbit" in args or "upload_kbit" in args:
                return Evaluation(deny(R_BAD_ARGUMENTS, "Give either mbit or download_kbit/"
                                                        "upload_kbit, not both."), canonical, device)
            mbit = parse_mbit(args["mbit"])
            if mbit is None:
                return Evaluation(deny(R_BAD_RATE, "No valid speed given."), canonical, device)
            kbit = mbit_to_kbit(mbit)
            if not lo <= kbit <= hi:
                return Evaluation(deny(R_RATE_RANGE,
                                       f"{mbit:g} Mbit/s is outside the allowed range "
                                       f"({lo / 1000:g}-{hi / 1000:g} Mbit/s) for role '{role}'."),
                                  canonical, device)
            down = up = kbit
        else:
            down, up = parse_kbit(args.get("download_kbit")), parse_kbit(args.get("upload_kbit"))
            if down is None or up is None:
                return Evaluation(deny(R_BAD_RATE, "download_kbit and upload_kbit must both be "
                                                   "whole numbers of kbit/s."), canonical, device)
            for label, value in (("download", down), ("upload", up)):
                if not lo <= value <= hi:
                    return Evaluation(deny(R_RATE_RANGE,
                                           f"{label} {value} kbit/s is outside the allowed range "
                                           f"({lo}-{hi} kbit/s) for role '{role}'."),
                                      canonical, device)
        return Evaluation(allow(R_LIMIT, f"Within allowed range ({lo}-{hi} kbit/s)."),
                          canonical, device, params={"download_kbit": down, "upload_kbit": up})

    def _ban(self, role: str, spec: RoleSpec, canonical: str, device: Device,
             args: Mapping[str, Any]) -> Evaluation:
        ban = spec.ban
        limit = min(ban.max_minutes, self.config.hard_max_ban_minutes)
        raw = args.get("duration_minutes")
        if raw is None:
            if not ban.allow_permanent:
                return Evaluation(deny(R_BAN_DURATION,
                                       f"Role '{role}' must give duration_minutes (max {limit})."),
                                  canonical, device)
            minutes = None
        else:
            minutes = parse_minutes(raw)
            if minutes is None or minutes < 1:
                return Evaluation(deny(R_BAN_DURATION, "duration_minutes must be a whole number "
                                                       "of minutes, at least 1."), canonical, device)
            if minutes > limit:
                return Evaluation(deny(R_BAN_DURATION,
                                       f"Role '{role}' cannot ban for more than {limit} minutes."),
                                  canonical, device)

        reason = args.get("reason")
        if reason is not None and (not isinstance(reason, str)
                                   or not self.config.reason_pattern.fullmatch(reason)):
            return Evaluation(deny(R_BAD_REASON, "The ban reason may only use letters, digits, "
                                                 "spaces and . , _ - (max 120 characters)."),
                              canonical, device)
        confirm = args.get("confirm", False)
        if not isinstance(confirm, bool):
            return Evaluation(deny(R_BAD_ARGUMENTS, "confirm must be true or false."),
                              canonical, device)

        how_long = f"{minutes} minute(s)" if minutes else "permanently"
        return Evaluation(allow(R_BAN, f"Banning '{device.name}' {how_long} is within the "
                                       f"limits for role '{role}'."),
                          canonical, device,
                          params={"duration_minutes": minutes, "reason": reason, "confirm": confirm})

    def _unban(self, role: str, spec: RoleSpec, canonical: str, device: Device,
               ctx: Mapping[str, Any]) -> Evaluation:
        if spec.ban.unban_scope == "own_role":
            if "banned_by_role" not in ctx:
                return Evaluation(deny(R_UNBAN_SCOPE,
                                       f"Cannot confirm who created this ban; role '{role}' may "
                                       f"only lift bans made by role '{role}'."), canonical, device)
            by = ctx["banned_by_role"]
            if by is not None and by != role:
                return Evaluation(deny(R_UNBAN_SCOPE,
                                       f"Role '{role}' cannot unban a device banned by role "
                                       f"'{clean_for_log(by, 20)}'."), canonical, device)
        return Evaluation(allow(R_UNBAN, f"Lifting the ban on '{device.name}' is permitted."),
                          canonical, device)
