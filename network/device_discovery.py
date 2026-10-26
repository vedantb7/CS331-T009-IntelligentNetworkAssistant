"""Network device discovery and read-only host inspection.

Inspects host network interfaces, IP/MAC addresses, routing tables,
wireless capabilities (AP mode support), and tunnel interfaces on Linux.
Produces structured data models and a human-readable diagnostic report.

All operations in this module are strictly read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# Type alias for a safe subprocess execution function
CommandRunner = Callable[[List[str], float], Tuple[int, str, str]]


def default_command_runner(args: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
    """Execute a system command safely without a shell.

    Args:
        args: Command and argument list.
        timeout: Maximum duration in seconds before timing out.

    Returns:
        Tuple of (exit_code, stdout, stderr).
    """
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"Command not found: '{args[0]}'"
    except PermissionError:
        return 126, "", f"Permission denied executing: '{args[0]}'"
    except subprocess.TimeoutExpired:
        return 124, "", f"Command timed out after {timeout}s: {' '.join(args)}"
    except OSError as err:
        return 1, "", f"OS error executing '{args[0]}': {err}"


# ==============================================================================
# Structured Data Models
# ==============================================================================


@dataclass
class DHCPLease:
    """Represents a DHCP lease record from dnsmasq.

    Corresponds to /var/lib/misc/dnsmasq.leases format:
    <expiry_timestamp> <mac_address> <ip_address> <hostname> [client_id]
    """

    expiry_epoch: int
    mac_address: str
    ip_address: str
    hostname: Optional[str]  # None if '*' or omitted
    client_id: Optional[str] = None


@dataclass
class NeighborEntry:
    """Represents a kernel neighbor table entry (ARP / IPv6 neighbor)."""

    ip_address: str
    mac_address: Optional[str]
    interface: str
    state: str  # REACHABLE, STALE, DELAY, PROBE, FAILED, INCOMPLETE, PERMANENT


@dataclass
class DiscoveredClient:
    """Represents a client device discovered on the managed local network."""

    ip_address: str
    mac_address: Optional[str]
    hostname: Optional[str] = None
    interface: str = "ap0"
    neighbor_state: Optional[str] = None
    lease_expiry: Optional[int] = None
    is_active: bool = False
    source: str = "unknown"  # "dhcp", "neighbor", or "both"
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert discovered client to a dictionary."""
        return asdict(self)


@dataclass
class IPAddressInfo:
    """Represents an assigned IPv4 or IPv6 address."""

    address: str
    family: str  # "ipv4" or "ipv6"
    prefixlen: int
    scope: Optional[str] = None


@dataclass
class RouteInfo:
    """Represents a routing table entry."""

    destination: str
    interface: str
    gateway: Optional[str] = None
    metric: Optional[int] = None
    protocol: Optional[str] = None
    family: str = "ipv4"
    is_default: bool = False


@dataclass
class InterfaceInfo:
    """Represents an observed network interface and its attributes."""

    name: str
    operstate: str  # "UP", "DOWN", "UNKNOWN", etc.
    flags: List[str] = field(default_factory=list)
    mac_address: Optional[str] = None
    link_type: Optional[str] = None
    interface_type: str = "unknown"  # "loopback", "ethernet", "wireless", "tunnel", "bridge", etc.
    addresses: List[IPAddressInfo] = field(default_factory=list)
    is_loopback: bool = False
    is_wireless: bool = False
    is_tunnel: bool = False
    tunnel_type: Optional[str] = None  # "wireguard", "tun", "tap", "sit", "ipip", etc.
    vpn_hint: Optional[str] = None
    ap_mode_supported: Optional[bool] = None  # None: unknown/unavailable; True: supported; False: not supported


@dataclass
class UplinkCandidate:
    """Inferred candidate for outbound internet / uplink connectivity."""

    interface: str
    gateway: Optional[str]
    metric: Optional[int]
    family: str
    operstate: str
    is_primary: bool
    notes: str


@dataclass
class APCandidate:
    """Inferred candidate for hosting a dedicated Wi-Fi access point."""

    interface: str
    ap_mode_supported: Optional[bool]
    operstate: str
    mac_address: Optional[str]
    is_current_uplink: bool
    notes: str


@dataclass
class TunnelCandidate:
    """Inferred tunnel or VPN interface that may affect routing."""

    interface: str
    tunnel_type: Optional[str]
    vpn_hint: Optional[str]
    has_active_routes: bool
    notes: str


@dataclass
class NetworkReport:
    """Structured report containing observed network facts and inferred candidates."""

    timestamp: str
    platform_info: str
    interfaces: List[InterfaceInfo] = field(default_factory=list)
    routes: List[RouteInfo] = field(default_factory=list)
    default_routes: List[RouteInfo] = field(default_factory=list)
    uplink_candidates: List[UplinkCandidate] = field(default_factory=list)
    ap_candidates: List[APCandidate] = field(default_factory=list)
    tunnel_candidates: List[TunnelCandidate] = field(default_factory=list)
    clients: List[DiscoveredClient] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert report to a JSON-serializable dictionary."""
        return asdict(self)

    def format_report(self) -> str:
        """Format the report as human-readable diagnostic text."""
        lines: List[str] = []
        sep = "=" * 78
        subsep = "-" * 78

        lines.append(sep)
        lines.append("INA v2 — Host Network Discovery Report")
        lines.append(f"Timestamp: {self.timestamp} | Platform: {self.platform_info}")
        lines.append(sep)

        # ----------------------------------------------------------------------
        # Observed Facts
        # ----------------------------------------------------------------------
        lines.append("\n[ OBSERVED FACTS ]")

        # Interfaces
        lines.append("\n1. Detected Network Interfaces:")
        if not self.interfaces:
            lines.append("   (No network interfaces detected)")
        for iface in self.interfaces:
            mac_display = iface.mac_address if iface.mac_address else "(none)"
            lines.append(f"   • {iface.name}:")
            lines.append(
                f"       State: {iface.operstate:<7} | Type: {iface.interface_type:<10} | "
                f"Link: {iface.link_type or 'unknown':<8} | MAC: {mac_display}"
            )
            if iface.flags:
                lines.append(f"       Flags: {', '.join(iface.flags)}")

            # IP Addresses
            if iface.addresses:
                for addr in iface.addresses:
                    scope_str = f" (scope: {addr.scope})" if addr.scope else ""
                    lines.append(f"       {addr.family.upper()}: {addr.address}/{addr.prefixlen}{scope_str}")
            else:
                lines.append("       IP Addresses: (none assigned)")

            # Wireless facts
            if iface.is_wireless:
                if iface.ap_mode_supported is True:
                    ap_str = "Yes (Driver advertises AP mode support)"
                elif iface.ap_mode_supported is False:
                    ap_str = "No (Driver does not list AP mode)"
                else:
                    ap_str = "Unavailable (Could not verify driver AP modes)"
                lines.append(f"       Wi-Fi Details: Wireless device | AP Mode Advertised: {ap_str}")

            # Tunnel facts
            if iface.is_tunnel:
                t_type = iface.tunnel_type or "unspecified"
                v_hint = f" ({iface.vpn_hint})" if iface.vpn_hint else ""
                lines.append(f"       Tunnel Details: {t_type}{v_hint}")

        # Routes
        lines.append("\n2. Routing Table Entries:")
        if not self.routes:
            lines.append("   (No routing entries detected)")
        else:
            default_count = len(self.default_routes)
            lines.append(f"   Observed {len(self.routes)} route(s), including {default_count} default route(s):")
            for r in self.routes:
                def_mark = " [DEFAULT]" if r.is_default else ""
                gw_str = f" via {r.gateway}" if r.gateway else ""
                metric_str = f" metric {r.metric}" if r.metric is not None else ""
                proto_str = f" proto {r.protocol}" if r.protocol else ""
                lines.append(f"   • {r.destination}{gw_str} dev {r.interface}{metric_str}{proto_str}{def_mark}")

        # ----------------------------------------------------------------------
        # Connected Clients (AP Network)
        # ----------------------------------------------------------------------
        if self.clients:
            lines.append("\n" + subsep)
            lines.append("[ CONNECTED CLIENTS (LOCAL ACCESS POINT) ]")
            for c in self.clients:
                host_str = f" ({c.hostname})" if c.hostname else " (unassigned hostname)"
                state_str = f" | State: {c.neighbor_state}" if c.neighbor_state else ""
                mac_str = c.mac_address or "(unknown MAC)"
                lines.append(f"   • {c.ip_address} [{mac_str}]{host_str}{state_str} [dev {c.interface}]")
                for note in c.notes:
                    lines.append(f"       Note: {note}")

        # ----------------------------------------------------------------------
        # Inferred Candidates
        # ----------------------------------------------------------------------
        lines.append("\n" + subsep)
        lines.append("[ INFERRED CANDIDATES & ANALYSIS ]")
        lines.append("* Note: The following items are heuristics inferred from the observed facts above.")

        # Uplink
        lines.append("\n1. Uplink Candidate(s):")
        if not self.uplink_candidates:
            lines.append("   • None detected (No active default routes found).")
        for uc in self.uplink_candidates:
            primary_tag = " (PRIMARY)" if uc.is_primary else ""
            gw_str = f" via {uc.gateway}" if uc.gateway else ""
            metric_str = f" (metric: {uc.metric})" if uc.metric is not None else ""
            lines.append(f"   • {uc.interface}{primary_tag}:{gw_str}{metric_str} [{uc.operstate}]")
            lines.append(f"     Analysis: {uc.notes}")

        # AP Candidate
        lines.append("\n2. Access Point (AP) Candidate(s):")
        if not self.ap_candidates:
            lines.append("   • None detected (No wireless interfaces identified).")
        for ac in self.ap_candidates:
            status_map = {True: "AP Capable", False: "Not AP Capable", None: "Capability Unknown"}
            lines.append(f"   • {ac.interface} [{status_map.get(ac.ap_mode_supported)}]:")
            lines.append(f"     Analysis: {ac.notes}")

        # Tunnel Candidate
        lines.append("\n3. Tunnel / Routing-Impacting Interfaces:")
        if not self.tunnel_candidates:
            lines.append("   • None detected.")
        for tc in self.tunnel_candidates:
            lines.append(f"   • {tc.interface} (type: {tc.tunnel_type or 'unknown'}):")
            lines.append(f"     Analysis: {tc.notes}")

        # ----------------------------------------------------------------------
        # Warnings & Diagnostics
        # ----------------------------------------------------------------------
        if self.warnings:
            lines.append("\n" + subsep)
            lines.append("[ WARNINGS & DIAGNOSTICS ]")
            for w in self.warnings:
                lines.append(f"   ! {w}")

        lines.append(sep)
        return "\n".join(lines)


# ==============================================================================
# Helper Discovery Functions
# ==============================================================================


def _parse_ip_addr_json(
    raw_json: str,
) -> Tuple[List[InterfaceInfo], List[str]]:
    """Parse JSON output from 'ip -j addr'."""
    interfaces: List[InterfaceInfo] = []
    warnings: List[str] = []

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as err:
        warnings.append(f"Failed to parse 'ip -j addr' JSON output: {err}")
        return interfaces, warnings

    if not isinstance(data, list):
        warnings.append(f"Unexpected data format from 'ip -j addr'; expected list, got {type(data).__name__}")
        return interfaces, warnings

    for entry in data:
        if not isinstance(entry, dict):
            continue

        name = entry.get("ifname", "")
        if not name:
            continue

        operstate = str(entry.get("operstate", "UNKNOWN")).upper()
        flags = [str(f).upper() for f in entry.get("flags", [])]
        link_type = entry.get("link_type")
        raw_mac = entry.get("address")
        mac_address = str(raw_mac).lower() if raw_mac and raw_mac != "00:00:00:00:00:00" else None

        # Determine loopback
        is_loopback = (name == "lo") or ("LOOPBACK" in flags) or (link_type == "loopback")
        if is_loopback:
            mac_address = "00:00:00:00:00:00"

        # Parse assigned IP addresses
        addresses: List[IPAddressInfo] = []
        addr_info_list = entry.get("addr_info", [])
        if isinstance(addr_info_list, list):
            for a in addr_info_list:
                if not isinstance(a, dict):
                    continue
                local_addr = a.get("local")
                family_raw = a.get("family")
                prefixlen = a.get("prefixlen", 0)
                scope = a.get("scope")

                if local_addr and family_raw:
                    family = "ipv4" if family_raw == "inet" else ("ipv6" if family_raw == "inet6" else str(family_raw))
                    addresses.append(
                        IPAddressInfo(
                            address=str(local_addr),
                            family=family,
                            prefixlen=int(prefixlen) if isinstance(prefixlen, int) else 0,
                            scope=str(scope) if scope else None,
                        )
                    )

        iface = InterfaceInfo(
            name=name,
            operstate=operstate,
            flags=flags,
            mac_address=mac_address,
            link_type=str(link_type) if link_type else None,
            addresses=addresses,
            is_loopback=is_loopback,
        )
        interfaces.append(iface)

    return interfaces, warnings


def _parse_ip_link_details(
    raw_json: str,
    iface_map: Dict[str, InterfaceInfo],
) -> List[str]:
    """Enrich InterfaceInfo objects using detailed output from 'ip -j -d link'."""
    warnings: List[str] = []
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as err:
        warnings.append(f"Failed to parse 'ip -j -d link' JSON: {err}")
        return warnings

    if not isinstance(data, list):
        return warnings

    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = entry.get("ifname", "")
        if not name or name not in iface_map:
            continue

        iface = iface_map[name]
        linkinfo = entry.get("linkinfo", {})
        info_kind = linkinfo.get("info_kind") if isinstance(linkinfo, dict) else None

        # Check for tunnel / encapsulation types
        tunnel_kinds = {
            "wireguard": ("wireguard", "WireGuard VPN tunnel"),
            "tun": ("tun", "TUN device (often used by VPNs like OpenVPN/Tailscale)"),
            "tap": ("tap", "TAP device (virtual Ethernet / L2 tunnel)"),
            "ipip": ("ipip", "IPv4-in-IPv4 encapsulation tunnel"),
            "sit": ("sit", "IPv6-in-IPv4 (SIT) encapsulation tunnel"),
            "gre": ("gre", "Generic Routing Encapsulation (GRE) tunnel"),
            "gretap": ("gretap", "GRE L2 tunnel"),
            "ip6gre": ("ip6gre", "IPv6 GRE tunnel"),
            "vti": ("vti", "Virtual Tunnel Interface (IPsec VTI)"),
            "vti6": ("vti6", "IPv6 Virtual Tunnel Interface"),
            "vxlan": ("vxlan", "Virtual eXtensible LAN overlay tunnel"),
            "geneve": ("geneve", "GENEVE overlay tunnel"),
        }

        if info_kind in tunnel_kinds:
            iface.is_tunnel = True
            iface.tunnel_type = tunnel_kinds[info_kind][0]
            iface.vpn_hint = tunnel_kinds[info_kind][1]
            iface.interface_type = "tunnel"
        elif info_kind in {"bridge", "veth", "bond", "dummy", "macvlan", "ipvlan"}:
            iface.interface_type = info_kind

    return warnings


def _parse_ip_routes_json(
    raw_json: str,
    family: str = "ipv4",
) -> Tuple[List[RouteInfo], List[str]]:
    """Parse JSON output from 'ip -j route' or 'ip -j -6 route'."""
    routes: List[RouteInfo] = []
    warnings: List[str] = []

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as err:
        warnings.append(f"Failed to parse 'ip -j route' ({family}) JSON output: {err}")
        return routes, warnings

    if not isinstance(data, list):
        return routes, warnings

    for entry in data:
        if not isinstance(entry, dict):
            continue

        dst = entry.get("dst", "")
        gateway = entry.get("gateway")
        dev = entry.get("dev", "")
        metric = entry.get("metric")
        protocol = entry.get("protocol")

        if not dev:
            continue

        is_default = (dst in ("default", "0.0.0.0/0", "::/0")) or (not dst and gateway is not None)
        destination_label = "default" if is_default else (dst if dst else "unspecified")

        routes.append(
            RouteInfo(
                destination=destination_label,
                interface=str(dev),
                gateway=str(gateway) if gateway else None,
                metric=int(metric) if isinstance(metric, int) else None,
                protocol=str(protocol) if protocol else None,
                family=family,
                is_default=is_default,
            )
        )

    return routes, warnings


def _inspect_wifi_and_ap_modes(
    runner: CommandRunner,
    iface_map: Dict[str, InterfaceInfo],
) -> List[str]:
    """Inspect wireless interfaces and check for AP mode support using 'iw'."""
    warnings: List[str] = []

    # 1. Check iw dev to map interfaces to phys
    code_dev, out_dev, err_dev = runner(["iw", "dev"], 5.0)
    if code_dev == 127:
        warnings.append("The 'iw' utility is not installed; Wi-Fi AP capability detection is unavailable.")
        # Mark all wireless interfaces with ap_mode_supported = None
        for iface in iface_map.values():
            if iface.is_wireless:
                iface.ap_mode_supported = None
        return warnings
    elif code_dev != 0:
        if err_dev.strip():
            warnings.append(f"'iw dev' returned code {code_dev}: {err_dev.strip()}")
        return warnings

    # Parse iw dev output
    # Format:
    # phy#0
    #     Interface wlan0
    #         ifindex 3
    #         wdev 0x1
    #         addr 00:11:22:33:44:55
    #         type managed
    iface_to_phy: Dict[str, str] = {}
    current_phy = ""
    for line in out_dev.splitlines():
        line = line.strip()
        if line.startswith("phy#"):
            current_phy = line.split()[0].replace("#", "")  # e.g. "phy0"
        elif line.startswith("Interface "):
            parts = line.split()
            if len(parts) >= 2:
                ifname = parts[1]
                if current_phy:
                    iface_to_phy[ifname] = current_phy
                if ifname in iface_map:
                    iface_map[ifname].is_wireless = True
                    iface_map[ifname].interface_type = "wireless"

    if not iface_to_phy:
        # No wireless interfaces reported by iw
        return warnings

    # 2. Check iw list to find supported interface modes per phy
    code_list, out_list, err_list = runner(["iw", "list"], 5.0)
    if code_list != 0:
        if err_list.strip():
            warnings.append(f"'iw list' returned code {code_list}: {err_list.strip()}")
        for ifname in iface_to_phy:
            if ifname in iface_map:
                iface_map[ifname].ap_mode_supported = None
        return warnings

    # Parse phy supported modes
    phy_supported_modes: Dict[str, set[str]] = {}
    active_phy = ""
    in_supported_modes = False

    for raw_line in out_list.splitlines():
        trimmed = raw_line.strip()
        if trimmed.startswith("Wiphy "):
            parts = trimmed.split()
            if len(parts) >= 2:
                active_phy = parts[1]
                phy_supported_modes[active_phy] = set()
            in_supported_modes = False
        elif "Supported interface modes:" in trimmed:
            in_supported_modes = True
        elif in_supported_modes:
            if trimmed.startswith("* "):
                mode_name = trimmed[2:].strip()
                if active_phy and active_phy in phy_supported_modes:
                    phy_supported_modes[active_phy].add(mode_name)
            elif not trimmed.startswith("*") and trimmed and not trimmed.startswith("software interface modes"):
                # Exited the supported modes block
                in_supported_modes = False

    # Apply detected modes to interface records
    for ifname, phy_name in iface_to_phy.items():
        if ifname in iface_map:
            modes = phy_supported_modes.get(phy_name)
            if modes is not None:
                iface_map[ifname].ap_mode_supported = "AP" in modes
            else:
                iface_map[ifname].ap_mode_supported = None

    return warnings


def _enrich_from_sysfs(iface_map: Dict[str, InterfaceInfo]) -> None:
    """Enrich or supplement interface details from /sys/class/net when available."""
    sys_net = "/sys/class/net"
    if not os.path.exists(sys_net):
        return

    for name, iface in list(iface_map.items()):
        dev_path = os.path.join(sys_net, name)
        if not os.path.isdir(dev_path):
            continue

        # Check wireless attribute
        if not iface.is_wireless:
            if os.path.exists(os.path.join(dev_path, "wireless")) or os.path.exists(os.path.join(dev_path, "phy80211")):
                iface.is_wireless = True
                if iface.interface_type == "unknown":
                    iface.interface_type = "wireless"

        # Check tun_flags
        tun_flags_path = os.path.join(dev_path, "tun_flags")
        if os.path.exists(tun_flags_path):
            iface.is_tunnel = True
            if not iface.tunnel_type:
                iface.tunnel_type = "tun/tap"
                iface.vpn_hint = "TUN/TAP device detected via sysfs"
            if iface.interface_type in ("unknown", "ethernet"):
                iface.interface_type = "tunnel"

        # Check point-to-point flag if not already identified as tunnel
        if "POINTOPOINT" in iface.flags and not iface.is_loopback:
            iface.is_tunnel = True
            if not iface.tunnel_type:
                iface.tunnel_type = "point-to-point"
                iface.vpn_hint = "Point-to-point network link"
            if iface.interface_type == "unknown":
                iface.interface_type = "tunnel"

        # Classify remaining unknown interfaces
        if iface.interface_type == "unknown":
            if iface.is_loopback:
                iface.interface_type = "loopback"
            elif iface.is_tunnel:
                iface.interface_type = "tunnel"
            elif iface.is_wireless:
                iface.interface_type = "wireless"
            elif iface.link_type == "ether":
                iface.interface_type = "ethernet"


# ==============================================================================
# Inferred Candidate Analysis
# ==============================================================================


def _analyze_candidates(
    interfaces: List[InterfaceInfo],
    default_routes: List[RouteInfo],
    all_routes: List[RouteInfo],
) -> Tuple[List[UplinkCandidate], List[APCandidate], List[TunnelCandidate]]:
    """Infer candidate roles and routing implications from observed data."""
    iface_dict = {i.name: i for i in interfaces}

    # 1. Uplink candidates
    uplink_candidates: List[UplinkCandidate] = []
    # Sort default routes: IPv4 first, then by metric (lowest metric first)
    sorted_defaults = sorted(
        default_routes,
        key=lambda r: (0 if r.family == "ipv4" else 1, r.metric if r.metric is not None else 999999),
    )

    seen_uplink_devs: set[str] = set()
    for idx, r in enumerate(sorted_defaults):
        if r.interface in seen_uplink_devs:
            continue
        seen_uplink_devs.add(r.interface)

        iface = iface_dict.get(r.interface)
        operstate = iface.operstate if iface else "UNKNOWN"
        is_primary = idx == 0

        notes_parts: List[str] = []
        if is_primary:
            notes_parts.append("Lowest metric default route; primary candidate for host outbound connectivity.")
        else:
            notes_parts.append("Secondary or metric-subordinated default route.")

        if iface and iface.is_tunnel:
            notes_parts.append(f"Interface is a tunnel ({iface.tunnel_type}); host routing may be redirected via VPN.")
        if operstate == "DOWN":
            notes_parts.append("Warning: Associated interface is reported DOWN.")

        uplink_candidates.append(
            UplinkCandidate(
                interface=r.interface,
                gateway=r.gateway,
                metric=r.metric,
                family=r.family,
                operstate=operstate,
                is_primary=is_primary,
                notes=" ".join(notes_parts),
            )
        )

    # 2. Access Point (AP) candidates
    ap_candidates: List[APCandidate] = []
    primary_uplink_dev = uplink_candidates[0].interface if uplink_candidates else ""

    for iface in interfaces:
        if iface.is_wireless:
            is_current_uplink = iface.name == primary_uplink_dev
            notes_list: List[str] = []

            if iface.ap_mode_supported is True:
                notes_list.append("Driver explicitly advertises AP mode support.")
            elif iface.ap_mode_supported is False:
                notes_list.append("Driver does not advertise AP mode support; unsuitable for standalone access point.")
            else:
                notes_list.append("Driver AP capabilities could not be verified.")

            if is_current_uplink:
                notes_list.append("Currently acting as primary uplink; configuring AP here requires virtual AP (VIF) support or a secondary Wi-Fi adapter.")
            elif iface.operstate == "UP":
                notes_list.append("Interface is operational and free of default uplink duties.")

            ap_candidates.append(
                APCandidate(
                    interface=iface.name,
                    ap_mode_supported=iface.ap_mode_supported,
                    operstate=iface.operstate,
                    mac_address=iface.mac_address,
                    is_current_uplink=is_current_uplink,
                    notes=" ".join(notes_list),
                )
            )

    # 3. Tunnel / Routing-Impacting interfaces
    tunnel_candidates: List[TunnelCandidate] = []
    routes_by_dev = {r.interface for r in all_routes}

    for iface in interfaces:
        if iface.is_tunnel:
            has_routes = iface.name in routes_by_dev
            notes_items: List[str] = []

            if iface.vpn_hint:
                notes_items.append(iface.vpn_hint + ".")
            else:
                notes_items.append(f"Encapsulation tunnel of type '{iface.tunnel_type}'.")

            if has_routes:
                notes_items.append("Has active routes in kernel table; may divert or encapsulate subnet traffic.")
            else:
                notes_items.append("No active routes associated with this tunnel interface.")

            tunnel_candidates.append(
                TunnelCandidate(
                    interface=iface.name,
                    tunnel_type=iface.tunnel_type,
                    vpn_hint=iface.vpn_hint,
                    has_active_routes=has_routes,
                    notes=" ".join(notes_items),
                )
            )

    return uplink_candidates, ap_candidates, tunnel_candidates


# ==============================================================================
# Main Discovery Entry Point
# ==============================================================================


def discover_network(
    runner: Optional[CommandRunner] = None,
    include_clients: bool = False,
    ap_interface: str = "ap0",
    leases_file: Optional[str] = "/var/lib/misc/dnsmasq.leases",
) -> NetworkReport:
    """Perform read-only network discovery and return a structured NetworkReport.

    Args:
        runner: Optional custom CommandRunner for dependency injection / mocking.
                Defaults to default_command_runner.
        include_clients: Whether to also discover connected client devices on the AP network.
        ap_interface: Target AP interface for client discovery (defaults to 'ap0').
        leases_file: Path to dnsmasq.leases file (defaults to '/var/lib/misc/dnsmasq.leases').

    Returns:
        NetworkReport containing observed facts, inferred candidates, and diagnostics.
    """
    if runner is None:
        runner = default_command_runner

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    platform_info = f"{platform.system()} {platform.release()} ({platform.machine()})"
    warnings: List[str] = []

    if sys.platform != "linux":
        warnings.append(
            f"Platform '{sys.platform}' is not Linux. Network discovery tools (iproute2/iw) are specific to Linux."
        )

    # 1. Discover interfaces via 'ip -j addr'
    code_addr, out_addr, err_addr = runner(["ip", "-j", "addr"], 5.0)
    interfaces: List[InterfaceInfo] = []
    if code_addr == 0 and out_addr.strip():
        interfaces, parse_warnings = _parse_ip_addr_json(out_addr)
        warnings.extend(parse_warnings)
    else:
        if code_addr == 127:
            warnings.append("The 'ip' command was not found. iproute2 utilities are required.")
        else:
            err_msg = err_addr.strip() if err_addr.strip() else f"return code {code_addr}"
            warnings.append(f"Unable to query network interfaces with 'ip -j addr': {err_msg}")

    iface_map = {i.name: i for i in interfaces}

    # 2. Enrich interfaces with 'ip -j -d link' (detailed linkinfo: wireguard, tun, bridge, etc.)
    code_link, out_link, _ = runner(["ip", "-j", "-d", "link"], 5.0)
    if code_link == 0 and out_link.strip():
        link_warnings = _parse_ip_link_details(out_link, iface_map)
        warnings.extend(link_warnings)

    # 3. Enrich interfaces with Wi-Fi details and AP mode capabilities via 'iw'
    wifi_warnings = _inspect_wifi_and_ap_modes(runner, iface_map)
    warnings.extend(wifi_warnings)

    # 4. Cross-reference sysfs for missing attributes or interfaces
    _enrich_from_sysfs(iface_map)

    # 5. Discover IPv4 routes via 'ip -j route'
    routes: List[RouteInfo] = []
    code_r4, out_r4, err_r4 = runner(["ip", "-j", "route"], 5.0)
    if code_r4 == 0 and out_r4.strip():
        r4_list, r4_warnings = _parse_ip_routes_json(out_r4, family="ipv4")
        routes.extend(r4_list)
        warnings.extend(r4_warnings)
    elif code_r4 != 0 and code_r4 != 127:
        if err_r4.strip():
            warnings.append(f"'ip -j route' returned code {code_r4}: {err_r4.strip()}")

    # 6. Discover IPv6 routes via 'ip -j -6 route'
    code_r6, out_r6, err_r6 = runner(["ip", "-j", "-6", "route"], 5.0)
    if code_r6 == 0 and out_r6.strip():
        r6_list, r6_warnings = _parse_ip_routes_json(out_r6, family="ipv6")
        routes.extend(r6_list)
        warnings.extend(r6_warnings)
    elif code_r6 != 0 and code_r6 != 127:
        if err_r6.strip():
            warnings.append(f"'ip -j -6 route' returned code {code_r6}: {err_r6.strip()}")

    # Extract default routes
    default_routes = [r for r in routes if r.is_default]

    # 7. Infer candidates
    uplink_cands, ap_cands, tunnel_cands = _analyze_candidates(
        interfaces=list(iface_map.values()),
        default_routes=default_routes,
        all_routes=routes,
    )

    # 8. Discover connected clients if requested
    clients: List[DiscoveredClient] = []
    if include_clients:
        try:
            clients = discover_clients(
                ap_interface=ap_interface,
                leases_file=leases_file,
                runner=runner,
            )
        except Exception as exc:
            warnings.append(f"Failed to query connected clients on {ap_interface}: {exc}")

    return NetworkReport(
        timestamp=timestamp,
        platform_info=platform_info,
        interfaces=list(iface_map.values()),
        routes=routes,
        default_routes=default_routes,
        uplink_candidates=uplink_cands,
        ap_candidates=ap_cands,
        tunnel_candidates=tunnel_cands,
        clients=clients,
        warnings=warnings,
    )


# ==============================================================================
# Client Discovery Primitives (DHCP Leases and ARP Neighbors)
# ==============================================================================


def parse_dnsmasq_leases(lease_content: str) -> List[DHCPLease]:
    """Parse dnsmasq.leases format content into structured DHCPLease objects.

    Standard line format:
    <expiry_timestamp> <mac_address> <ip_address> <hostname> [client_id]

    A hostname of '*' indicates that the client did not provide Option 12 in DHCPREQUEST.
    Note: The first field is an expiration timestamp (unix epoch), not the assignment time.
    """
    leases: List[DHCPLease] = []
    for line in lease_content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            expiry_epoch = int(parts[0])
        except ValueError:
            continue
        mac = parts[1].lower()
        ip = parts[2]
        hostname = parts[3]
        if hostname == "*":
            hostname = None
        client_id = parts[4] if len(parts) >= 5 else None
        leases.append(
            DHCPLease(
                expiry_epoch=expiry_epoch,
                mac_address=mac,
                ip_address=ip,
                hostname=hostname,
                client_id=client_id,
            )
        )
    return leases


def parse_ip_neighbors(
    output: str,
    target_interface: Optional[str] = None,
) -> List[NeighborEntry]:
    """Parse output from 'ip -j neigh' or 'ip neigh' into NeighborEntry objects.

    Supports both JSON format ('ip -j neigh') and line-based text output ('ip neigh').
    """
    entries: List[NeighborEntry] = []
    output = output.strip()
    if not output:
        return entries

    # Attempt JSON parsing first
    if output.startswith("[") and output.endswith("]"):
        try:
            data = json.loads(output)
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    dev = item.get("dev", "")
                    if target_interface and dev != target_interface:
                        continue
                    ip = item.get("dst", "")
                    lladdr = item.get("lladdr")
                    states = item.get("state", [])
                    state_str = ", ".join(states) if isinstance(states, list) else str(states)
                    if ip:
                        entries.append(
                            NeighborEntry(
                                ip_address=ip,
                                mac_address=lladdr.lower() if lladdr else None,
                                interface=dev,
                                state=state_str,
                            )
                        )
                return entries
        except json.JSONDecodeError:
            pass

    # Line-based fallback
    # Example: 192.168.50.46 dev ap0 lladdr 5e:22:f6:20:9a:e7 REACHABLE
    # Example: 192.168.50.12 dev ap0 FAILED
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        if len(tokens) < 3:
            continue
        ip = tokens[0]
        dev = ""
        lladdr = None
        state = tokens[-1]

        idx = 1
        while idx < len(tokens):
            if tokens[idx] == "dev" and idx + 1 < len(tokens):
                dev = tokens[idx + 1]
                idx += 2
            elif tokens[idx] == "lladdr" and idx + 1 < len(tokens):
                lladdr = tokens[idx + 1].lower()
                idx += 2
            else:
                idx += 1

        if target_interface and dev != target_interface:
            continue

        entries.append(
            NeighborEntry(
                ip_address=ip,
                mac_address=lladdr,
                interface=dev,
                state=state,
            )
        )

    return entries


def discover_clients(
    ap_interface: str = "ap0",
    leases_file: Optional[str] = "/var/lib/misc/dnsmasq.leases",
    runner: Optional[CommandRunner] = None,
) -> List[DiscoveredClient]:
    """Discover connected client devices by inspecting ARP neighbors and DHCP leases.

    Queries:
    1. 'ip neigh show dev <ap_interface>' (or 'ip -j neigh') for active neighbor states.
    2. dnsmasq leases file for IP assignments, expiration, and hostnames.

    Returns:
        List of DiscoveredClient objects.
    """
    if runner is None:
        runner = default_command_runner

    # 1. Query neighbor table
    code, out, _ = runner(["ip", "-j", "neigh", "show", "dev", ap_interface], 5.0)
    if code != 0 or not out.strip() or not out.strip().startswith("["):
        code, out, _ = runner(["ip", "neigh", "show", "dev", ap_interface], 5.0)

    neighbors = parse_ip_neighbors(out, target_interface=ap_interface) if code == 0 else []

    # 2. Query DHCP leases
    leases: List[DHCPLease] = []
    if leases_file and os.path.exists(leases_file):
        try:
            with open(leases_file, "r", encoding="utf-8") as f:
                leases = parse_dnsmasq_leases(f.read())
        except (OSError, PermissionError):
            pass

    client_map: Dict[str, DiscoveredClient] = {}

    for lease in leases:
        notes: List[str] = []
        if lease.hostname is None:
            notes.append("Hostname unassigned (client sent '*' in DHCP request)")
        if lease.mac_address and len(lease.mac_address) >= 2:
            try:
                first_byte = int(lease.mac_address[:2], 16)
                if first_byte & 0x02:
                    notes.append("MAC address appears randomized (locally administered bit set)")
            except ValueError:
                pass

        client = DiscoveredClient(
            ip_address=lease.ip_address,
            mac_address=lease.mac_address,
            hostname=lease.hostname,
            interface=ap_interface,
            lease_expiry=lease.expiry_epoch,
            source="dhcp",
            notes=notes,
        )
        client_map[lease.ip_address] = client

    for neigh in neighbors:
        if neigh.ip_address in client_map:
            c = client_map[neigh.ip_address]
            c.neighbor_state = neigh.state
            c.source = "both"
            if neigh.mac_address and not c.mac_address:
                c.mac_address = neigh.mac_address
            if neigh.state in ("REACHABLE", "DELAY"):
                c.is_active = True
        else:
            is_active = neigh.state in ("REACHABLE", "DELAY")
            notes: List[str] = []
            if neigh.mac_address and len(neigh.mac_address) >= 2:
                try:
                    first_byte = int(neigh.mac_address[:2], 16)
                    if first_byte & 0x02:
                        notes.append("MAC address appears randomized (locally administered bit set)")
                except ValueError:
                    pass
            client_map[neigh.ip_address] = DiscoveredClient(
                ip_address=neigh.ip_address,
                mac_address=neigh.mac_address,
                interface=neigh.interface,
                neighbor_state=neigh.state,
                is_active=is_active,
                source="neighbor",
                notes=notes,
            )

    return list(client_map.values())


# ==============================================================================
# CLI Entry Point
# ==============================================================================


def main() -> None:
    """CLI entry point for running read-only network discovery."""
    parser = argparse.ArgumentParser(
        prog="python -m network.device_discovery",
        description="INA v2 — Read-Only Host Network Discovery and Inspection.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output network discovery results as structured JSON.",
    )
    parser.add_argument(
        "--clients",
        action="store_true",
        dest="include_clients",
        help="Discover connected client devices on the local AP network via DHCP and ARP.",
    )
    parser.add_argument(
        "--interface",
        default="ap0",
        dest="ap_interface",
        help="Target AP interface for client discovery (default: 'ap0').",
    )
    parser.add_argument(
        "--leases-file",
        default="/var/lib/misc/dnsmasq.leases",
        dest="leases_file",
        help="Path to dnsmasq leases file (default: '/var/lib/misc/dnsmasq.leases').",
    )
    args = parser.parse_args()

    report = discover_network(
        include_clients=args.include_clients,
        ap_interface=args.ap_interface,
        leases_file=args.leases_file,
    )

    if args.json_output:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.format_report())


if __name__ == "__main__":
    main()
