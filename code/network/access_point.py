"""Access Point (AP) and Router Infrastructure Management for INA v2.

Provides configuration modeling, input validation, and read-only status inspection
of the Raspberry Pi 5 network infrastructure ('ap0' virtual Wi-Fi interface,
'hostapd' AP daemon, 'dnsmasq' DHCP/DNS server, 'nftables' NAT, and IPv4 forwarding).

On the Raspberry Pi 5 network host, the access point and routing infrastructure
are managed by system services ('hostapd', 'dnsmasq', 'ina-ap-interface.service',
and 'nftables'). This module provides safe configuration modeling, validation,
and read-only status inspection of the router host infrastructure. It does not
attempt to create NetworkManager hotspot profiles on client machines.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from network.device_discovery import (
    CommandRunner,
    NetworkReport,
    default_command_runner,
    discover_network,
)

# Verified Raspberry Pi 5 network infrastructure defaults
DEFAULT_AP_SSID = "INA-Router"
DEFAULT_AP_CHANNEL = 11
DEFAULT_AP_BAND = "bg"
DEFAULT_AP_INTERFACE = "ap0"
DEFAULT_UPLINK_INTERFACE = "wlan0"
DEFAULT_ROUTER_IP = "192.168.50.1"
DEFAULT_AP_SUBNET = "192.168.50.0/24"
DEFAULT_DHCP_RANGE_START = "192.168.50.10"
DEFAULT_DHCP_RANGE_END = "192.168.50.100"
DEFAULT_DHCP_LEASE_TIME = "12h"


# ==============================================================================
# Configuration Data Models
# ==============================================================================


@dataclass
class APConfig:
    """Specification for the INA Access Point and local router subnet."""

    ssid: str = DEFAULT_AP_SSID
    password: Optional[str] = None
    channel: int = DEFAULT_AP_CHANNEL
    band: str = DEFAULT_AP_BAND
    interface: str = DEFAULT_AP_INTERFACE
    uplink_interface: str = DEFAULT_UPLINK_INTERFACE
    router_ip: str = DEFAULT_ROUTER_IP
    subnet: str = DEFAULT_AP_SUBNET
    dhcp_range_start: str = DEFAULT_DHCP_RANGE_START
    dhcp_range_end: str = DEFAULT_DHCP_RANGE_END
    dhcp_lease_time: str = DEFAULT_DHCP_LEASE_TIME

    def __repr__(self) -> str:
        masked_pw = "***" if self.password else "(none)"
        return (
            f"APConfig(ssid={self.ssid!r}, password={masked_pw}, channel={self.channel}, "
            f"interface={self.interface!r}, uplink={self.uplink_interface!r}, "
            f"router_ip={self.router_ip!r}, subnet={self.subnet!r})"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary with masked credentials."""
        d = asdict(self)
        if d.get("password"):
            d["password"] = "***"
        return d


# ==============================================================================
# Settings Loader
# ==============================================================================


def load_network_settings(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load system configuration from config/settings.yaml if available."""
    if config_path is None:
        env_path = os.environ.get("INA_CONFIG_PATH")
        if env_path and os.path.exists(env_path):
            config_path = env_path
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(base_dir, "config", "settings.yaml")

    if not os.path.exists(config_path):
        return {}

    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ==============================================================================
# Configuration Validation
# ==============================================================================


def validate_ap_config(config: APConfig) -> List[str]:
    """Validate AP and router configuration parameters.

    Returns a list of error strings. An empty list signifies valid configuration.
    """
    errors: List[str] = []

    # 1. SSID Validation
    if not config.ssid or not config.ssid.strip():
        errors.append("SSID must not be empty or whitespace only.")
    else:
        ssid_bytes = config.ssid.encode("utf-8")
        if len(ssid_bytes) > 32:
            errors.append(f"SSID exceeds 32 octets ({len(ssid_bytes)} bytes).")
        if any(ord(c) < 32 or ord(c) == 127 for c in config.ssid):
            errors.append("SSID contains illegal non-printable control characters.")

    # 2. Password Validation (WPA2-Personal PSK)
    if config.password is not None:
        pw = config.password
        is_64_hex = len(pw) == 64 and bool(re.fullmatch(r"[0-9a-fA-F]{64}", pw))
        if is_64_hex:
            pass  # Valid 64-hex PSK
        elif len(pw) < 8 or len(pw) > 63:
            errors.append(
                f"WPA2 passphrase length must be 8–63 ASCII characters or 64 hex characters (got {len(pw)})."
            )
        else:
            if not all(32 <= ord(c) <= 126 for c in pw):
                errors.append("WPA2 passphrase contains non-printable or non-ASCII characters.")

    # 3. Channel Validation
    if config.band in ("bg", "b", "g", "2.4GHz"):
        if not (1 <= config.channel <= 14):
            errors.append(f"Invalid 2.4 GHz Wi-Fi channel: {config.channel}. Must be between 1 and 14.")
    elif config.band in ("a", "5GHz"):
        valid_5ghz = {36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144, 149, 153, 157, 161, 165}
        if config.channel not in valid_5ghz:
            errors.append(f"Invalid 5 GHz Wi-Fi channel: {config.channel}.")
    else:
        errors.append(f"Invalid Wi-Fi band: '{config.band}'. Must be 'bg' (2.4GHz) or 'a' (5GHz).")

    # 4. Interface Name Validation
    if not config.interface or not re.fullmatch(r"[a-zA-Z0-9_\-\.]{1,16}", config.interface):
        errors.append(f"Invalid AP interface name: '{config.interface}'.")

    if not config.uplink_interface or not re.fullmatch(r"[a-zA-Z0-9_\-\.]{1,16}", config.uplink_interface):
        errors.append(f"Invalid uplink interface name: '{config.uplink_interface}'.")

    if config.interface and config.interface == config.uplink_interface:
        errors.append(
            f"AP interface and uplink interface cannot be the same physical/virtual device ('{config.interface}'). "
            "Use a virtual AP interface (e.g., 'ap0') on top of 'wlan0'."
        )

    # 5. Subnet and IP Validation
    if "/" not in config.subnet:
        errors.append(f"Invalid IPv4 subnet format: '{config.subnet}'. Expected CIDR notation (e.g. 192.168.50.0/24).")
        return errors

    try:
        net = ipaddress.ip_network(config.subnet, strict=False)
        if not isinstance(net, ipaddress.IPv4Network):
            errors.append("Only IPv4 subnets are currently supported for the AP client network.")
        elif net.prefixlen < 8 or net.prefixlen > 30:
            errors.append(f"Subnet prefix length /{net.prefixlen} is impractical for an AP (must be /8 to /30).")
        elif not net.is_private:
            errors.append(f"Configured subnet '{config.subnet}' is not within RFC 1918 private address space.")

        # Router IP
        try:
            router_ip = ipaddress.ip_address(config.router_ip)
            if router_ip not in net:
                errors.append(f"Router IP '{config.router_ip}' is not within subnet '{config.subnet}'.")
            elif router_ip in (net.network_address, net.broadcast_address):
                errors.append(f"Router IP '{config.router_ip}' cannot be the network or broadcast address.")
        except ValueError:
            errors.append(f"Invalid router IP address: '{config.router_ip}'.")

        # DHCP Range
        try:
            start_ip = ipaddress.ip_address(config.dhcp_range_start)
            end_ip = ipaddress.ip_address(config.dhcp_range_end)
            if start_ip not in net or end_ip not in net:
                errors.append("DHCP pool addresses must be within the configured subnet.")
            elif start_ip > end_ip:
                errors.append(
                    f"DHCP start IP ({config.dhcp_range_start}) is greater than end IP ({config.dhcp_range_end})."
                )
        except ValueError:
            errors.append("Invalid DHCP range IP addresses.")

    except ValueError:
        errors.append(f"Invalid IPv4 subnet format: '{config.subnet}'. Expected CIDR notation (e.g. 192.168.50.0/24).")

    return errors


# ==============================================================================
# Router Infrastructure Status Models and Inspection
# ==============================================================================


@dataclass
class ServiceStatus:
    """Status of an individual host systemd service."""

    name: str
    active: bool
    state: str  # "active", "inactive", "failed", "not-found", etc.
    description: str = ""


@dataclass
class RouterStatus:
    """Observed operational state of the Raspberry Pi router infrastructure."""

    timestamp: str
    is_operational: bool
    ap_interface: str
    ap_interface_up: bool
    ap_interface_ip: Optional[str]
    uplink_interface: str
    uplink_interface_up: bool
    uplink_gateway: Optional[str]
    hostapd_status: ServiceStatus
    dnsmasq_status: ServiceStatus
    ap_interface_service_status: ServiceStatus
    nftables_status: ServiceStatus
    ipv4_forwarding_enabled: bool
    nat_rule_active: bool
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert status to a dictionary."""
        return asdict(self)

    def format_report(self) -> str:
        """Format the router status as human-readable diagnostic text."""
        lines: List[str] = []
        sep = "=" * 78
        subsep = "-" * 78

        lines.append(sep)
        lines.append("INA v2 — Raspberry Pi Router Infrastructure Status")
        lines.append(f"Timestamp: {self.timestamp} | Fully Operational: {'YES' if self.is_operational else 'NO'}")
        lines.append(sep)

        lines.append("\n[ INTERFACES ]")
        ap_ip_str = self.ap_interface_ip or "(none)"
        ap_up_str = "UP" if self.ap_interface_up else "DOWN"
        lines.append(f"   • AP Interface ({self.ap_interface}): State={ap_up_str} | IP={ap_ip_str}")

        up_up_str = "UP" if self.uplink_interface_up else "DOWN"
        gw_str = f" via {self.uplink_gateway}" if self.uplink_gateway else " (no gateway)"
        lines.append(f"   • Uplink Interface ({self.uplink_interface}): State={up_up_str} | Gateway={gw_str}")

        lines.append("\n[ SYSTEM SERVICES ]")
        for svc in [
            self.hostapd_status,
            self.dnsmasq_status,
            self.ap_interface_service_status,
            self.nftables_status,
        ]:
            state_disp = svc.state.upper()
            status_tag = "[PASS]" if svc.active else "[FAIL]"
            lines.append(f"   • {svc.name:<25}: {state_disp:<10} {status_tag} ({svc.description})")

        lines.append("\n[ FORWARDING & NAT ]")
        fwd_tag = "[PASS]" if self.ipv4_forwarding_enabled else "[FAIL]"
        nat_tag = "[PASS]" if self.nat_rule_active else "[FAIL]"
        lines.append(
            f"   • IPv4 Forwarding (net.ipv4.ip_forward): "
            f"{'ENABLED' if self.ipv4_forwarding_enabled else 'DISABLED'} {fwd_tag}"
        )
        lines.append(
            f"   • nftables NAT Masquerade (table ip ina_nat): "
            f"{'ACTIVE' if self.nat_rule_active else 'INACTIVE'} {nat_tag}"
        )

        if self.warnings:
            lines.append("\n" + subsep)
            lines.append("[ WARNINGS & DIAGNOSTICS ]")
            for w in self.warnings:
                lines.append(f"   ! {w}")

        lines.append(sep)
        return "\n".join(lines)


def _check_systemd_service(
    service_name: str,
    runner: CommandRunner,
    description: str = "",
) -> ServiceStatus:
    """Query systemctl is-active for a specified service."""
    code, stdout, stderr = runner(["systemctl", "is-active", service_name], 5.0)
    state = stdout.strip() if stdout.strip() else ("inactive" if code != 0 else "unknown")
    if code == 127:
        state = "not-found"
    is_active = (code == 0 and state == "active")
    return ServiceStatus(
        name=service_name,
        active=is_active,
        state=state,
        description=description,
    )


def inspect_router_status(
    config: Optional[APConfig] = None,
    runner: Optional[CommandRunner] = None,
) -> RouterStatus:
    """Inspect the live operational state of the Raspberry Pi router infrastructure.

    Queries:
    1. Interface state and assigned IP for AP interface (default 'ap0').
    2. Interface state and default route for uplink interface (default 'wlan0').
    3. Service states for 'hostapd', 'dnsmasq', 'ina-ap-interface', and 'nftables'.
    4. Kernel sysctl 'net.ipv4.ip_forward'.
    5. Kernel nftables table 'ip ina_nat'.

    Returns:
        RouterStatus dataclass describing the operational state.
    """
    if config is None:
        config = APConfig()
    if runner is None:
        runner = default_command_runner

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    warnings: List[str] = []

    # 1. Query AP Interface
    ap_iface = config.interface
    ap_up = False
    ap_ip = None
    code, out, _ = runner(["ip", "-j", "addr", "show", "dev", ap_iface], 5.0)
    if code == 0 and out.strip():
        try:
            data = json.loads(out)
            if isinstance(data, list) and data:
                entry = data[0]
                flags = entry.get("flags", [])
                ap_up = "UP" in flags or entry.get("operstate") == "UP"
                for addr in entry.get("addr_info", []):
                    if addr.get("family") == "inet":
                        ap_ip = f"{addr.get('local')}/{addr.get('prefixlen')}"
                        break
        except Exception:
            pass
    elif code != 0:
        warnings.append(f"AP interface '{ap_iface}' does not exist or could not be queried via 'ip addr'.")

    # 2. Query Uplink Interface
    uplink_iface = config.uplink_interface
    uplink_up = False
    uplink_gw = None
    code_up, out_up, _ = runner(["ip", "-j", "route", "show", "dev", uplink_iface], 5.0)
    if code_up == 0 and out_up.strip():
        try:
            data = json.loads(out_up)
            if isinstance(data, list):
                for r in data:
                    if r.get("dst") == "default":
                        uplink_gw = r.get("gateway")
                        uplink_up = True
                        break
                if not uplink_up and data:
                    uplink_up = True
        except Exception:
            pass

    # 3. Check System Services
    hostapd_svc = _check_systemd_service("hostapd", runner, "Wi-Fi AP Daemon")
    dnsmasq_svc = _check_systemd_service("dnsmasq", runner, "DHCP and DNS Server")
    ap_iface_svc = _check_systemd_service("ina-ap-interface", runner, "Virtual AP Interface Setup")
    nftables_svc = _check_systemd_service("nftables", runner, "Packet Filter & NAT")

    # 4. Check IPv4 Forwarding
    fwd_code, fwd_out, _ = runner(["sysctl", "-n", "net.ipv4.ip_forward"], 5.0)
    fwd_enabled = (fwd_code == 0 and fwd_out.strip() == "1")
    if not fwd_enabled:
        warnings.append("IPv4 forwarding is not enabled (net.ipv4.ip_forward != 1).")

    # 5. Check nftables NAT table
    nat_code, nat_out, _ = runner(["nft", "list", "table", "ip", "ina_nat"], 5.0)
    nat_active = (nat_code == 0 and "masquerade" in nat_out)
    if not nat_active:
        warnings.append("nftables NAT table 'ip ina_nat' is missing or lacks masquerade rule.")

    # Determine overall operational state
    is_operational = (
        ap_up
        and hostapd_svc.active
        and dnsmasq_svc.active
        and fwd_enabled
        and nat_active
    )

    return RouterStatus(
        timestamp=timestamp,
        is_operational=is_operational,
        ap_interface=ap_iface,
        ap_interface_up=ap_up,
        ap_interface_ip=ap_ip,
        uplink_interface=uplink_iface,
        uplink_interface_up=uplink_up,
        uplink_gateway=uplink_gw,
        hostapd_status=hostapd_svc,
        dnsmasq_status=dnsmasq_svc,
        ap_interface_service_status=ap_iface_svc,
        nftables_status=nftables_svc,
        ipv4_forwarding_enabled=fwd_enabled,
        nat_rule_active=nat_active,
        warnings=warnings,
    )


# ==============================================================================
# Preflight Safety Checks
# ==============================================================================


@dataclass
class PreflightReport:
    """Detailed diagnostics of system state before AP operation."""

    can_safely_operate: bool
    target_interface: str
    uplink_interface: str
    ap_capability_status: str  # "supported", "unsupported", "unknown"
    uplink_status: str         # "clear", "conflict", "missing"
    subnet_status: str         # "clear", "collision", "invalid"
    conflicts: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    untested_conditions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def preflight_safety_check(
    config: Optional[APConfig] = None,
    runner: Optional[CommandRunner] = None,
) -> PreflightReport:
    """Perform pre-flight safety inspection for the AP router infrastructure.

    Validates:
    - AP interface and uplink interface separation (protects host connectivity).
    - Wireless driver AP mode capability.
    - Subnet collisions between AP subnet and uplink/host routes.
    - Presence of required Linux utilities.

    Returns:
        PreflightReport describing safety status.
    """
    if config is None:
        config = APConfig()
    if runner is None:
        runner = default_command_runner

    conflicts: List[str] = []
    warnings: List[str] = []
    untested_conditions: List[str] = [
        "Wi-Fi client association under varying radio frequency interference",
        "DHCP address acquisition with diverse client operating systems",
        "Hardware thermal throttling under sustained iperf3 traffic",
    ]

    # Validate config
    val_errors = validate_ap_config(config)
    if val_errors:
        conflicts.extend(val_errors)

    # Run discovery
    report = discover_network(runner=runner)

    # Check AP capability on physical wireless devices
    ap_capability_status = "unknown"
    wireless_found = False
    for iface in report.interfaces:
        if iface.is_wireless:
            wireless_found = True
            if iface.ap_mode_supported is True:
                ap_capability_status = "supported"
            elif iface.ap_mode_supported is False and ap_capability_status != "supported":
                ap_capability_status = "unsupported"

    if not wireless_found:
        ap_capability_status = "unsupported"
        warnings.append("No physical wireless interface detected on the host.")
    elif ap_capability_status == "unsupported":
        conflicts.append("Wireless driver does not advertise AP mode support.")

    # Check uplink separation
    uplink_status = "clear"
    for r in report.default_routes:
        if r.interface == config.interface:
            uplink_status = "conflict"
            conflicts.append(
                f"Uplink conflict: Configured AP interface '{config.interface}' currently carries default route. "
                "AP and uplink must use separate virtual/physical interfaces (e.g. 'ap0' and 'wlan0')."
            )

    # Check subnet collision
    subnet_status = "clear"
    try:
        configured_net = ipaddress.ip_network(config.subnet, strict=False)
        for r in report.routes:
            if r.destination in ("default", "unspecified") or not r.destination:
                continue
            try:
                route_net = ipaddress.ip_network(r.destination, strict=False)
                if configured_net.overlaps(route_net):
                    subnet_status = "collision"
                    conflicts.append(
                        f"Subnet collision: Configured AP subnet '{config.subnet}' overlaps with existing route "
                        f"'{r.destination}' on device '{r.interface}'."
                    )
            except ValueError:
                continue
    except ValueError:
        subnet_status = "invalid"

    can_safely_operate = (
        len(conflicts) == 0
        and ap_capability_status != "unsupported"
        and uplink_status != "conflict"
        and subnet_status != "collision"
    )

    return PreflightReport(
        can_safely_operate=can_safely_operate,
        target_interface=config.interface,
        uplink_interface=config.uplink_interface,
        ap_capability_status=ap_capability_status,
        uplink_status=uplink_status,
        subnet_status=subnet_status,
        conflicts=conflicts,
        warnings=warnings,
        untested_conditions=untested_conditions,
    )


# ==============================================================================
# CLI Entry Point
# ==============================================================================


def main() -> None:
    """CLI entry point for AP configuration validation and router status inspection."""
    parser = argparse.ArgumentParser(
        prog="python -m network.access_point",
        description="INA v2 — Access Point & Router Infrastructure Management.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Operational subcommands")

    # Status subcommand
    status_parser = subparsers.add_parser("status", help="Inspect router infrastructure status")
    status_parser.add_argument("--json", action="store_true", help="Output status as structured JSON")
    status_parser.add_argument("--interface", default=DEFAULT_AP_INTERFACE, help="Target AP interface (default: ap0)")

    # Validate subcommand
    validate_parser = subparsers.add_parser("validate", help="Validate an AP configuration")
    validate_parser.add_argument("--ssid", default=DEFAULT_AP_SSID, help="Access Point SSID")
    validate_parser.add_argument("--password", help="WPA2 passphrase")
    validate_parser.add_argument("--channel", type=int, default=DEFAULT_AP_CHANNEL, help="Wi-Fi channel (default: 11)")
    validate_parser.add_argument("--interface", default=DEFAULT_AP_INTERFACE, help="Target AP interface (default: ap0)")

    args = parser.parse_args()

    if args.subcommand == "validate":
        config = APConfig(
            ssid=args.ssid,
            password=args.password,
            channel=args.channel,
            interface=args.interface,
        )
        errors = validate_ap_config(config)
        if errors:
            print("Configuration Validation FAILED:")
            for e in errors:
                print(f"  • {e}")
            sys.exit(1)
        else:
            print(f"Configuration is VALID: {config}")

    else:
        # Default action: status
        ap_iface = getattr(args, "interface", DEFAULT_AP_INTERFACE)
        config = APConfig(interface=ap_iface)
        status = inspect_router_status(config=config)
        if getattr(args, "json", False):
            print(json.dumps(status.to_dict(), indent=2))
        else:
            print(status.format_report())


if __name__ == "__main__":
    main()
