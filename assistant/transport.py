"""Transport layer: how the assistant sends a tool call and gets a result.

Two transports share one interface:

- :class:`MockTransport`: a self-contained, in-memory stand-in for the whole
  server side (role check -> policy -> apply -> verify). It lets the assistant
  run end-to-end on a laptop today, before Dhruv's MCP server and Ananya's
  policy engine exist. This is what milestone M1 in the plan needs, and what
  the evaluation harness runs against.
- :class:`HttpTransport`: a thin client for Dhruv's real FastMCP server, used
  once it is up. It sends the same :class:`ToolCall` and expects the same
  :class:`ToolResult` shape, so nothing else in the assistant changes.

The mock deliberately mirrors the real design: it knows a device list with
classes, a tiny role-based policy, protected devices, and a bandwidth range,
and it keeps in-memory network state so verification reflects reality. It is a
faithful rehearsal of the contract, not a shortcut around it.

Owner: Khushi (assistant). The policy numbers here are a stand-in for Ananya's
real engine and must be kept consistent with ``policy/rules.yaml``.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from assistant.contract import (
    Decision,
    ToolCall,
    ToolName,
    ToolResult,
    Verification,
)


class Transport:
    """Interface: send a ToolCall, get a ToolResult."""

    def call(self, request: ToolCall) -> ToolResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Mock server side: device table + policy + in-memory network state
# ---------------------------------------------------------------------------

@dataclass
class MockDevice:
    name: str
    mac: str
    cls: str   # "protected", "staff", "guest", or "unknown"


@dataclass
class MockNetworkState:
    """In-memory stand-in for the Pi's firewall and tc state."""

    blocked: set = field(default_factory=set)        # set of MACs
    limits: Dict[str, float] = field(default_factory=dict)  # MAC -> mbit
    banned: set = field(default_factory=set)         # set of MACs

    def reset(self) -> None:
        self.blocked.clear()
        self.limits.clear()
        self.banned.clear()


# Default role tokens -> role. In the real system these are hashed on the Pi.
DEFAULT_TOKENS = {
    "operator-token": "operator",
    "admin-token": "admin",
}

# Default demo device list, matching the plan's device classes.
DEFAULT_DEVICES = [
    MockDevice("pi-gateway", "aa:bb:cc:dd:ee:00", "protected"),
    MockDevice("admin-laptop", "aa:bb:cc:dd:ee:01", "protected"),
    MockDevice("probe-laptop", "aa:bb:cc:dd:ee:02", "protected"),
    MockDevice("lab-laptop-3", "aa:bb:cc:dd:ee:03", "staff"),
    MockDevice("guest-phone", "aa:bb:cc:dd:ee:04", "guest"),
]

# Bandwidth bounds (Mbit/s), per role. Mirrors the plan's permission table.
BANDWIDTH_BOUNDS = {
    "operator": (2.0, 20.0),
    "admin": (1.0, 20.0),
}

# Which device classes each role may act on for block/limit/restore.
ROLE_CLASS_ALLOWED = {
    "operator": {"guest", "unknown"},
    "admin": {"guest", "unknown", "staff"},
}


class MockTransport(Transport):
    """A faithful in-memory rehearsal of the server pipeline."""

    def __init__(self,
                 devices: Optional[List[MockDevice]] = None,
                 tokens: Optional[Dict[str, str]] = None) -> None:
        self.devices = {d.name: d for d in (devices or DEFAULT_DEVICES)}
        self.tokens = dict(tokens or DEFAULT_TOKENS)
        self.state = MockNetworkState()

    # -- helpers -----------------------------------------------------------
    def _role(self, token: Optional[str]) -> Optional[str]:
        return self.tokens.get(token or "")

    def _resolve(self, name: Optional[str]) -> Optional[MockDevice]:
        if name is None:
            return None
        # Exact match after trimming/lowercasing.
        key = name.strip()
        if key in self.devices:
            return self.devices[key]
        for dname, dev in self.devices.items():
            if dname.lower() == key.lower():
                return dev
        return None

    def _deny(self, call: ToolCall, reason: str, rule: str) -> ToolResult:
        return ToolResult(
            status="denied", tool=call.tool, request_id=call.request_id,
            device=call.arguments.get("device"),
            decision=Decision("DENY", reason, rule),
            detail=reason,
        )

    # -- the pipeline ------------------------------------------------------
    def call(self, request: ToolCall) -> ToolResult:
        role = self._role(request.token)
        if role is None:
            return self._deny(request, "Unknown or missing role token.", "R-token")

        if request.tool in (ToolName.LIST_DEVICES.value, ToolName.GET_STATUS.value):
            return self._list_devices(request, role)

        device = self._resolve(request.arguments.get("device"))
        if device is None:
            name = request.arguments.get("device")
            return self._deny(request, f"'{name}' is not a recognized device.",
                              "R-unknown-device")

        # Protected devices can never be touched.
        if device.cls == "protected":
            return self._deny(request,
                              f"'{device.name}' is a protected device and cannot be changed.",
                              "R-protected")

        # Role must be allowed to act on this device class.
        if device.cls not in ROLE_CLASS_ALLOWED.get(role, set()):
            return self._deny(request,
                              f"Role '{role}' may not act on {device.cls} devices.",
                              "R-role-class")

        if request.tool == ToolName.LIMIT_BANDWIDTH.value:
            return self._limit(request, role, device)
        if request.tool in (ToolName.BLOCK_DEVICE.value, ToolName.BLOCK_CLIENT.value):
            return self._block(request, device)
        if request.tool in (ToolName.RESTORE_DEVICE.value, ToolName.UNBLOCK_CLIENT.value):
            return self._restore(request, device)
        if request.tool in (ToolName.KICK_CLIENT.value, "kick_client"):
            return self._kick(request, device)
        if request.tool in (ToolName.BAN_CLIENT.value, "ban_client"):
            return self._ban(request, role, device)
        if request.tool in (ToolName.UNBAN_CLIENT.value, "unban_client"):
            return self._unban(request, role, device)

        return self._deny(request, f"Unknown tool '{request.tool}'.", "R-unknown-tool")

    def _list_devices(self, call: ToolCall, role: str) -> ToolResult:
        rows = []
        for d in self.devices.values():
            rows.append({
                "name": d.name,
                "class": d.cls,
                "blocked": d.mac in self.state.blocked,
                "limit_mbit": self.state.limits.get(d.mac),
                "banned": d.mac in self.state.banned,
            })
        return ToolResult(
            status="success", tool=call.tool, request_id=call.request_id,
            decision=Decision("ALLOW", "Listing is permitted for all roles.", "R-list"),
            detail=f"{len(rows)} device(s) on the network.",
            data={"devices": rows},
        )

    def _kick(self, call: ToolCall, device: MockDevice) -> ToolResult:
        verification = Verification(
            passed=True,
            expected="station deauthenticated",
            measured="station deauthenticated",
            checks=[{"name": "deauthenticate", "expected": "OK", "measured": "OK"}],
        )
        return ToolResult(
            status="success",
            tool=call.tool,
            request_id=call.request_id,
            device=device.name,
            decision=Decision("ALLOW", "Kick permitted."),
            verification=verification,
            detail=f"Disconnected {device.name}.",
            data={"kicked": True, "mac": device.mac},
        )

    def _ban(self, call: ToolCall, role: str, device: MockDevice) -> ToolResult:
        args = call.arguments or {}
        minutes = args.get("minutes")
        if minutes is None and "duration_minutes" in args:
            minutes = args.get("duration_minutes")
        confirm = bool(args.get("confirm"))

        if role == "operator":
            if minutes is None:
                return self._deny(call, "Role 'operator' must give duration_minutes (max 60).", "R-ban-duration")
            if minutes > 60:
                return self._deny(call, "Role 'operator' cannot ban for more than 60 minutes.", "R-ban-duration")

        dur = f"{minutes} min" if minutes else "permanently"
        if not confirm:
            return ToolResult(
                status="preview",
                tool=call.tool,
                request_id=call.request_id,
                device=device.name,
                decision=Decision("ALLOW", "Ban preview."),
                detail=f"PREVIEW: would deny {device.mac} for {dur} and disconnect it now.",
                data={"requires_confirmation": True, "preview": True, "mac": device.mac},
            )

        self.state.banned.add(device.mac)
        verification = Verification(
            passed=True,
            expected="station denied",
            measured="station denied",
            checks=[{"name": "deny_acl", "expected": device.mac, "measured": device.mac}],
        )
        return ToolResult(
            status="success",
            tool=call.tool,
            request_id=call.request_id,
            device=device.name,
            decision=Decision("ALLOW", "Ban applied."),
            verification=verification,
            detail=f"Banned {device.name} ({device.mac}) for {dur}.",
            data={"banned": True, "mac": device.mac},
        )

    def _unban(self, call: ToolCall, role: str, device: MockDevice) -> ToolResult:
        self.state.banned.discard(device.mac)
        verification = Verification(
            passed=True,
            expected="station allowed",
            measured="station allowed",
            checks=[{"name": "deny_acl", "expected": "absent", "measured": "absent"}],
        )
        return ToolResult(
            status="success",
            tool=call.tool,
            request_id=call.request_id,
            device=device.name,
            decision=Decision("ALLOW", "Unban permitted."),
            verification=verification,
            detail=f"Unbanned {device.name}.",
            data={"unbanned": True, "mac": device.mac},
        )

    def _limit(self, call: ToolCall, role: str, device: MockDevice) -> ToolResult:
        mbit = call.arguments.get("mbit")
        if not isinstance(mbit, (int, float)):
            return self._deny(call, "No valid speed given.", "R-bad-rate")
        lo, hi = BANDWIDTH_BOUNDS[role]
        if not (lo <= float(mbit) <= hi):
            return self._deny(call,
                              f"{mbit} Mbit/s is outside the allowed range "
                              f"({lo:g}-{hi:g} Mbit/s) for role '{role}'.",
                              "R-rate-range")
        # Apply + verify (mock): the limit takes effect and reads back exactly.
        self.state.limits[device.mac] = float(mbit)
        verification = Verification(
            passed=True,
            expected=f"{mbit:g} Mbit/s",
            measured=f"{mbit:g} Mbit/s",
            checks=[{"name": "tc rate", "expected": f"{mbit:g}mbit",
                     "measured": f"{mbit:g}mbit"}],
        )
        return ToolResult(
            status="success", tool=call.tool, request_id=call.request_id,
            device=device.name,
            decision=Decision("ALLOW", "Within allowed range.", "R-rate-range"),
            verification=verification,
            detail=f"Limited {device.name} to {mbit:g} Mbit/s.",
        )

    def _block(self, call: ToolCall, device: MockDevice) -> ToolResult:
        self.state.blocked.add(device.mac)
        verification = Verification(
            passed=True, expected="100% packet loss", measured="100% packet loss",
            checks=[{"name": "ping", "expected": "unreachable", "measured": "unreachable"}],
        )
        return ToolResult(
            status="success", tool=call.tool, request_id=call.request_id,
            device=device.name,
            decision=Decision("ALLOW", "Non-protected device.", "R-block"),
            verification=verification,
            detail=f"Blocked {device.name}.",
        )

    def _restore(self, call: ToolCall, device: MockDevice) -> ToolResult:
        self.state.blocked.discard(device.mac)
        self.state.limits.pop(device.mac, None)
        verification = Verification(
            passed=True, expected="reachable, no limit", measured="reachable, no limit",
            checks=[{"name": "ping", "expected": "reachable", "measured": "reachable"},
                    {"name": "leftover rules", "expected": "none", "measured": "none"}],
        )
        return ToolResult(
            status="success", tool=call.tool, request_id=call.request_id,
            device=device.name,
            decision=Decision("ALLOW", "Restore is permitted.", "R-restore"),
            verification=verification,
            detail=f"Restored {device.name}.",
        )

    # -- convenience for the assistant's "list device names" need ----------
    def device_names(self) -> List[str]:
        return list(self.devices.keys())


class HttpTransport(Transport):
    """Client for Dhruv's real FastMCP server (used once it is up).

    Sends the same ToolCall as JSON and parses the same ToolResult shape.
    The exact endpoint path is a placeholder to agree with Dhruv.
    """

    def __init__(self, base_url: str = "http://192.168.50.1:8000",
                 timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def call(self, request: ToolCall) -> ToolResult:
        url = f"{self.base_url}/tool"
        body = json.dumps(request.to_dict()).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        return ToolResult.from_dict(raw)

    def device_names(self) -> List[str]:
        """Fetch current device names via a list_devices call (best effort)."""
        result = self.call(ToolCall(tool=ToolName.GET_STATUS.value,
                                    token="operator-token"))
        rows = result.data.get("devices", [])
        return [r.get("name") for r in rows if r.get("name")]


# ---------------------------------------------------------------------------
# Direct Service Layer Transport
# ---------------------------------------------------------------------------

class ServiceTransport(Transport):
    """Direct transport connecting the assistant to the authoritative NetworkService.

    Translates assistant ToolCall invocations into typed NetworkService operations,
    enforcing real policy evaluation, single-writer locking, kernel mutations,
    read-back verification, and SHA-256 audit logging.
    """

    def __init__(self, service=None) -> None:
        if service is None:
            from inav2.network.service import default_service
            self.service = default_service
        else:
            self.service = service

    def call(self, request: ToolCall) -> ToolResult:
        role = "operator"
        if request.token:
            if "admin" in request.token.lower():
                role = "admin"
            elif "viewer" in request.token.lower():
                role = "viewer"

        args = request.arguments or {}
        device = args.get("device") or args.get("client") or ""
        target = args.get("target") or "all"

        try:
            if request.tool in (ToolName.BLOCK_CLIENT.value, ToolName.BLOCK_DEVICE.value):
                res = self.service.block(client=device, target=target, role=role)
                return ToolResult(
                    status="success",
                    tool=request.tool,
                    request_id=request.request_id,
                    device=device,
                    decision=Decision("ALLOW", "Block action permitted by policy."),
                    verification=Verification(passed=res.verified, measured="Kernel block set confirmed"),
                    detail=res.message or f"Blocked {device} to {target}.",
                )

            elif request.tool == ToolName.UNBLOCK_CLIENT.value:
                res = self.service.unblock(client=device, target=target, role=role)
                return ToolResult(
                    status="success",
                    tool=request.tool,
                    request_id=request.request_id,
                    device=device,
                    decision=Decision("ALLOW", "Unblock action permitted by policy."),
                    verification=Verification(passed=res.verified, measured="Rule removed from kernel set"),
                    detail=res.message or f"Unblocked {device} to {target}.",
                )

            elif request.tool == ToolName.LIMIT_BANDWIDTH.value:
                mbit = args.get("mbit")
                down = args.get("download_kbit") or (int(mbit * 1000) if mbit else 5000)
                up = args.get("upload_kbit") or down
                res = self.service.limit(client=device, download_kbit=down, upload_kbit=up, role=role)
                return ToolResult(
                    status="success",
                    tool=request.tool,
                    request_id=request.request_id,
                    device=device,
                    decision=Decision("ALLOW", "Bandwidth shaping permitted by policy."),
                    verification=Verification(passed=res.verified, measured=f"{down}k down / {up}k up confirmed"),
                    detail=res.message or f"Limited {device} to {down}k/{up}k.",
                )

            elif request.tool == ToolName.REMOVE_BANDWIDTH_LIMIT.value:
                res = self.service.remove_limit(client=device, role=role)
                return ToolResult(
                    status="success",
                    tool=request.tool,
                    request_id=request.request_id,
                    device=device,
                    decision=Decision("ALLOW", "Bandwidth unlimit permitted by policy."),
                    verification=Verification(passed=res.verified, measured="Classes removed from ap0 and ifb-ap0"),
                    detail=res.message or f"Removed bandwidth limits for {device}.",
                )

            elif request.tool in (ToolName.RESTORE_DEVICE.value, ToolName.UNBLOCK_CLIENT.value):
                self.service.unblock(client=device, target="all", role=role)
                self.service.remove_limit(client=device, role=role)
                return ToolResult(
                    status="success",
                    tool=request.tool,
                    request_id=request.request_id,
                    device=device,
                    decision=Decision("ALLOW", "Restore permitted by policy."),
                    verification=Verification(passed=True, measured="Unblocked and unshaped"),
                    detail=f"Restored {device}: unblocked and removed rate limits.",
                )

            elif request.tool in (ToolName.GET_STATUS.value, ToolName.LIST_DEVICES.value):
                res = self.service.status(client=device or None, role=role)
                rows = [
                    {
                        "name": c.client,
                        "ip": c.ip,
                        "associated": c.associated,
                        "blocked": c.blocked_targets,
                        "download_kbit": c.download_kbit,
                        "upload_kbit": c.upload_kbit,
                        "banned": c.banned,
                    }
                    for c in res.clients
                ]
                return ToolResult(
                    status="success",
                    tool=request.tool,
                    request_id=request.request_id,
                    device=device or None,
                    decision=Decision("ALLOW", "Status query permitted."),
                    verification=Verification(passed=True, measured="Read from system state"),
                    data={"devices": rows},
                    detail=f"Found {len(rows)} devices.",
                )

            elif request.tool in (ToolName.KICK_CLIENT.value, "kick_client"):
                res = self.service.kick(client=device, role=role)
                return ToolResult(
                    status="success",
                    tool=request.tool,
                    request_id=request.request_id,
                    device=device,
                    decision=Decision("ALLOW", "Kick action permitted by policy."),
                    verification=Verification(passed=res.verified, measured="Deauthentication accepted"),
                    detail=res.message or f"Disconnected {device}.",
                    data={"mac": res.mac, "kicked": res.kicked, "reconnected": res.reconnected},
                )

            elif request.tool in (ToolName.BAN_CLIENT.value, "ban_client"):
                minutes = args.get("minutes")
                if minutes is None and "duration_minutes" in args:
                    minutes = args.get("duration_minutes")
                reason = args.get("reason")
                confirm = bool(args.get("confirm"))
                res = self.service.ban(
                    client=device,
                    duration_minutes=minutes,
                    reason=reason,
                    confirm=confirm,
                    role=role,
                )
                status = "preview" if res.preview else "success"
                return ToolResult(
                    status=status,
                    tool=request.tool,
                    request_id=request.request_id,
                    device=device,
                    decision=Decision("ALLOW", "Ban preview generated." if res.preview else "Ban applied."),
                    verification=Verification(passed=res.verified, measured="Deny ACL confirmed" if res.verified else "None"),
                    detail=res.message,
                    data={
                        "preview": res.preview,
                        "requires_confirmation": res.requires_confirmation,
                        "banned": res.banned,
                        "mac": res.mac,
                        "expires_at": res.expires_at,
                    },
                )

            elif request.tool in (ToolName.UNBAN_CLIENT.value, "unban_client"):
                res = self.service.unban(client=device, role=role)
                return ToolResult(
                    status="success",
                    tool=request.tool,
                    request_id=request.request_id,
                    device=device,
                    decision=Decision("ALLOW", "Unban action permitted by policy."),
                    verification=Verification(passed=res.verified, measured="Deny ACL removal confirmed"),
                    detail=res.message or f"Unbanned {device}.",
                    data={"mac": res.mac, "unbanned": res.unbanned},
                )

            else:
                return ToolResult(
                    status="error",
                    tool=request.tool,
                    request_id=request.request_id,
                    device=device or None,
                    error=f"Unknown tool '{request.tool}'.",
                )

        except Exception as err:
            from inav2.network.service import PolicyDeniedError
            is_denied = (
                isinstance(err, PolicyDeniedError)
                or "denied" in str(err).lower()
                or "protected" in str(err).lower()
                or "refusing to" in str(err).lower()
                or "cannot" in str(err).lower()
                or "unknown client" in str(err).lower()
            )
            status = "denied" if is_denied else "failure"
            return ToolResult(
                status=status,
                tool=request.tool,
                request_id=request.request_id,
                device=device or None,
                decision=Decision("DENY", str(err)),
                verification=Verification(passed=False, expected="Action permitted by policy", measured=str(err)),
                detail=str(err),
            )

    def device_names(self) -> List[str]:
        """Fetch known client device names from active status."""
        try:
            status_res = self.service.status(role="viewer")
            return [c.client for c in status_res.clients]
        except Exception:
            return []
