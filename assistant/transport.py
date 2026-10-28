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

    def reset(self) -> None:
        self.blocked.clear()
        self.limits.clear()


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

        if request.tool == ToolName.LIST_DEVICES.value:
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
        if request.tool == ToolName.BLOCK_DEVICE.value:
            return self._block(request, device)
        if request.tool == ToolName.RESTORE_DEVICE.value:
            return self._restore(request, device)

        return self._deny(request, f"Unknown tool '{request.tool}'.", "R-unknown-tool")

    def _list_devices(self, call: ToolCall, role: str) -> ToolResult:
        rows = []
        for d in self.devices.values():
            rows.append({
                "name": d.name,
                "class": d.cls,
                "blocked": d.mac in self.state.blocked,
                "limit_mbit": self.state.limits.get(d.mac),
            })
        return ToolResult(
            status="success", tool=call.tool, request_id=call.request_id,
            decision=Decision("ALLOW", "Listing is permitted for all roles.", "R-list"),
            detail=f"{len(rows)} device(s) on the network.",
            data={"devices": rows},
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
        result = self.call(ToolCall(tool=ToolName.LIST_DEVICES.value,
                                    token="operator-token"))
        rows = result.data.get("devices", [])
        return [r.get("name") for r in rows if r.get("name")]
