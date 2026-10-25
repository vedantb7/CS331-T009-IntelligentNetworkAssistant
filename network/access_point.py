"""Access Point (AP) profile and lifecycle management using NetworkManager.

Provides safe, declarative management of a dedicated Wi-Fi access point profile
using 'nmcli' through argument-list subprocess calls.

Safety Guarantees:
- Default operation is dry-run / read-only.
- Automatic activation is strictly prohibited.
- Dynamic interface resolution with strict precedence:
    1. Explicit CLI argument (--interface)
    2. Configuration file (config/settings.yaml)
    3. Environment variable (NETWORK_INTERFACE)
    4. Unambiguous network discovery of dedicated (non-uplink) Wi-Fi interfaces
- Refuses automatic selection of active uplink interfaces or ambiguous candidates.
- Pre-flight checks inspect active connections, routing, and wireless capabilities.
- Detects incompatible simultaneous Wi-Fi uplink operation and refuses activation
  to prevent disconnecting the host's internet connection.
- Modifies and removes ONLY explicitly identified INA-managed profiles ('ina-*').
- Never overwrites or touches unrelated user or uplink connections.
- Masked credential handling in logs and reports.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from network.device_discovery import (
    CommandRunner,
    NetworkReport,
    default_command_runner,
    discover_network,
)

# Namespace prefix for all INA-managed NetworkManager connection profiles
INA_PROFILE_PREFIX = "ina-"
DEFAULT_AP_PROFILE_NAME = "ina-ap"
DEFAULT_AP_SSID = "INA-Test-Network"
DEFAULT_AP_SUBNET = "192.168.10.1/24"


# ==============================================================================
# Settings and Interface Resolution
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


def resolve_ap_interface(
    config_interface: Optional[str] = None,
    runner: Optional[CommandRunner] = None,
    settings: Optional[Dict[str, Any]] = None,
    discovery_report: Optional[NetworkReport] = None,
) -> Tuple[Optional[str], str, NetworkReport]:
    """Resolve the target Wi-Fi interface using explicit configuration or unambiguous discovery.

    Precedence order:
    1. Explicit argument / config_interface (CLI or API caller).
    2. Explicit configuration in config/settings.yaml (network.interface).
    3. Explicit environment variable (NETWORK_INTERFACE).
    4. Unambiguous discovery:
       - Runs discover_network().
       - Excludes active uplinks (carrying default routes) to protect host connectivity.
       - If exactly 1 non-uplink wireless candidate exists, selects it.
       - If 0 candidates exist (or only active uplinks exist), refuses automatic selection.
       - If > 1 candidate exists, refuses automatic selection due to ambiguity.

    Returns:
        Tuple of (resolved_interface_or_None, reason_string, discovery_report)
    """
    if runner is None:
        runner = default_command_runner

    # 1. Explicit argument
    if config_interface and config_interface.strip():
        if discovery_report is None:
            discovery_report = discover_network(runner=runner)
        return (
            config_interface.strip(),
            f"Explicitly specified by user/caller ('{config_interface.strip()}')",
            discovery_report,
        )

    # 2. Settings file
    if settings is None:
        settings = load_network_settings()
    net_settings = settings.get("network", {}) if isinstance(settings, dict) else {}
    settings_iface = net_settings.get("interface") if isinstance(net_settings, dict) else None
    if settings_iface and str(settings_iface).strip():
        if discovery_report is None:
            discovery_report = discover_network(runner=runner)
        return (
            str(settings_iface).strip(),
            f"Configured in config/settings.yaml ('{str(settings_iface).strip()}')",
            discovery_report,
        )

    # 3. Environment variable
    env_iface = os.environ.get("NETWORK_INTERFACE")
    if env_iface and env_iface.strip():
        if discovery_report is None:
            discovery_report = discover_network(runner=runner)
        return (
            env_iface.strip(),
            f"Configured via NETWORK_INTERFACE environment variable ('{env_iface.strip()}')",
            discovery_report,
        )

    # 4. Unambiguous discovery
    if discovery_report is None:
        discovery_report = discover_network(runner=runner)

    # Determine active uplink devices (carrying default routes)
    uplink_devs = {r.interface for r in discovery_report.default_routes} | {
        uc.interface for uc in discovery_report.uplink_candidates
    }

    # Find wireless interfaces
    wireless_ifaces = [i for i in discovery_report.interfaces if i.is_wireless]

    # Candidates: wireless interfaces that are NOT active uplinks
    non_uplink_candidates = [i for i in wireless_ifaces if i.name not in uplink_devs]

    if len(non_uplink_candidates) == 1:
        chosen = non_uplink_candidates[0].name
        return (
            chosen,
            f"Unambiguously discovered dedicated non-uplink wireless interface '{chosen}'",
            discovery_report,
        )

    if len(non_uplink_candidates) == 0:
        if wireless_ifaces:
            uplink_names = [i.name for i in wireless_ifaces]
            reason = (
                f"Discovered wireless interface(s) {uplink_names} currently active as default uplink. "
                "Not selected automatically to protect host connectivity. Explicit configuration required."
            )
        else:
            reason = "No wireless interfaces discovered on the host. Explicit interface configuration required."
        return None, reason, discovery_report

    # Multiple non-uplink candidates
    cand_names = [c.name for c in non_uplink_candidates]
    reason = (
        f"Multiple non-uplink wireless interface candidates discovered ({cand_names}). "
        "Selection is ambiguous; explicit interface configuration required."
    )
    return None, reason, discovery_report


# ==============================================================================
# Data Models
# ==============================================================================


@dataclass
class APConfig:
    """Configuration specification for a dedicated INA access point."""

    ssid: str = DEFAULT_AP_SSID
    password: str = ""
    interface: Optional[str] = None
    subnet: str = DEFAULT_AP_SUBNET
    channel: Optional[int] = None
    band: str = "bg"  # "bg" (2.4GHz) or "a" (5GHz)
    profile_name: str = DEFAULT_AP_PROFILE_NAME

    def __repr__(self) -> str:
        """Mask credentials in string representations."""
        pwd_display = "***" if self.password else "(empty)"
        iface_display = repr(self.interface) if self.interface else "(auto-resolve)"
        return (
            f"APConfig(ssid={self.ssid!r}, password={pwd_display!r}, "
            f"interface={iface_display}, subnet={self.subnet!r}, "
            f"channel={self.channel!r}, band={self.band!r}, "
            f"profile_name={self.profile_name!r})"
        )

    def to_dict(self, mask_password: bool = True) -> Dict[str, Any]:
        """Convert configuration to dictionary with optional password masking."""
        data = asdict(self)
        if mask_password:
            data["password"] = "***" if self.password else ""
        return data


@dataclass
class ProfileStatus:
    """Status and properties of a NetworkManager connection profile."""

    profile_name: str
    exists: bool = False
    uuid: Optional[str] = None
    interface: Optional[str] = None
    mode: Optional[str] = None
    ssid: Optional[str] = None
    is_active: bool = False
    active_device: Optional[str] = None
    is_ina_managed: bool = False
    ipv4_method: Optional[str] = None
    ipv4_addresses: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PreflightReport:
    """Pre-flight safety assessment prior to profile creation or activation."""

    can_safely_activate: bool = False
    target_interface: Optional[str] = None
    interface_resolution_reason: str = ""
    interface_status: str = "unspecified"  # "configured", "missing", "not_wireless", "unspecified"
    ap_capability_status: str = "unknown"  # "supported", "unsupported", "unknown", "not_applicable"
    uplink_status: str = "clear"  # "conflict", "active_non_uplink", "clear"
    subnet_status: str = "clear"  # "collision", "invalid", "clear"
    profile_status: str = "missing"  # "active", "inactive", "missing", "unmanaged_conflict"
    has_active_uplink_conflict: bool = False
    active_connection_on_interface: Optional[str] = None
    is_wireless: bool = False
    ap_mode_supported: Optional[bool] = None
    conflicts: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    untested_conditions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OperationResult:
    """Structured result returned by AP management actions."""

    success: bool
    dry_run: bool = False
    message: str = ""
    planned_commands: List[List[str]] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ==============================================================================
# Validation Functions
# ==============================================================================


def validate_ap_config(config: APConfig) -> List[str]:
    """Validate an AP configuration for correctness and security constraints.

    Returns:
        List of validation error strings (empty if configuration is valid).
    """
    errors: List[str] = []

    # 1. Profile Name Validation (must use INA namespace)
    if not config.profile_name:
        errors.append("Profile name cannot be empty.")
    elif not config.profile_name.startswith(INA_PROFILE_PREFIX):
        errors.append(
            f"Profile name '{config.profile_name}' must start with '{INA_PROFILE_PREFIX}' "
            "to ensure namespace isolation from host connections."
        )
    elif not re.match(r"^[a-zA-Z0-9_-]+$", config.profile_name):
        errors.append(
            f"Profile name '{config.profile_name}' contains invalid characters; "
            "use only alphanumeric characters, underscores, and hyphens."
        )

    # 2. SSID Validation (1-32 bytes, printable UTF-8)
    if not config.ssid or not config.ssid.strip():
        errors.append("SSID cannot be empty or whitespace only.")
    else:
        ssid_bytes = config.ssid.encode("utf-8")
        if len(ssid_bytes) > 32:
            errors.append(f"SSID exceeds maximum length of 32 bytes (got {len(ssid_bytes)} bytes).")
        if any(b < 32 or b == 127 for b in ssid_bytes):
            errors.append("SSID must not contain control characters or newlines.")

    # 3. Password Validation (WPA2-Personal PSK: 8-63 ASCII or 64 hex characters)
    if not config.password:
        errors.append("WPA2 password cannot be empty.")
    else:
        pwd_len = len(config.password)
        is_hex_64 = pwd_len == 64 and all(c in "0123456789abcdefABCDEF" for c in config.password)
        if not (8 <= pwd_len <= 63 or is_hex_64):
            errors.append(
                f"WPA2 password must be between 8 and 63 ASCII characters (or 64 hexadecimal characters); got {pwd_len}."
            )
        if any(ord(c) < 32 or ord(c) == 127 for c in config.password):
            errors.append("WPA2 password must not contain control characters or newlines.")

    # 4. Interface Name Validation (if specified)
    if config.interface is not None:
        if not config.interface.strip():
            errors.append("Interface name cannot be empty when provided.")
        elif not re.match(r"^[a-zA-Z0-9_-]+$", config.interface):
            errors.append(
                f"Interface name '{config.interface}' contains invalid characters; "
                "use only alphanumeric characters, underscores, and hyphens."
            )

    # 5. IPv4 Subnet Validation
    if not config.subnet:
        errors.append("Subnet cannot be empty.")
    else:
        try:
            # Validate CIDR notation
            net = ipaddress.ip_network(config.subnet, strict=False)
            if net.version != 4:
                errors.append(f"Subnet '{config.subnet}' is IPv6; an IPv4 CIDR subnet is required.")
            elif not net.is_private:
                errors.append(f"Subnet '{config.subnet}' is not a private IPv4 address space (RFC 1918).")
            elif not (16 <= net.prefixlen <= 29):
                errors.append(
                    f"Subnet prefix /{net.prefixlen} is out of recommended range for an access point (/16 to /29)."
                )
        except ValueError as err:
            errors.append(f"Invalid IPv4 subnet CIDR '{config.subnet}': {err}")

    # 6. Band and Channel Validation
    if config.band not in ("bg", "a"):
        errors.append(f"Unsupported Wi-Fi band '{config.band}'; expected 'bg' (2.4GHz) or 'a' (5GHz).")

    if config.channel is not None:
        if config.band == "bg" and not (1 <= config.channel <= 14):
            errors.append(f"Channel {config.channel} is invalid for 2.4GHz band (expected 1-14).")
        elif config.band == "a" and not (36 <= config.channel <= 165):
            errors.append(f"Channel {config.channel} is invalid for 5GHz band (expected 36-165).")

    return errors


# ==============================================================================
# Profile Inspection
# ==============================================================================


def inspect_profile(
    profile_name: str,
    runner: Optional[CommandRunner] = None,
) -> ProfileStatus:
    """Inspect whether a named NetworkManager profile exists and retrieve its properties.

    Args:
        profile_name: Name of the NetworkManager connection profile.
        runner: Optional command runner.

    Returns:
        ProfileStatus object detailing existence, active state, and configuration.
    """
    if runner is None:
        runner = default_command_runner

    # 1. Check if profile exists in connection list
    code, out, _ = runner(["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show"], 5.0)
    if code != 0:
        return ProfileStatus(profile_name=profile_name, exists=False)

    found_uuid: Optional[str] = None
    found_type: Optional[str] = None
    found_dev: Optional[str] = None

    for line in out.splitlines():
        # Handle escaped colons in nmcli output
        parts = [p.replace(r"\:", ":") for p in line.strip().split(":")]
        if len(parts) >= 2 and parts[0] == profile_name:
            found_uuid = parts[1]
            found_type = parts[2] if len(parts) > 2 else None
            found_dev = parts[3] if len(parts) > 3 and parts[3] != "--" else None
            break

    if not found_uuid:
        return ProfileStatus(profile_name=profile_name, exists=False)

    # 2. Inspect detailed settings for the profile
    code_det, out_det, _ = runner(
        [
            "nmcli",
            "-t",
            "-f",
            "connection.id,connection.uuid,connection.type,802-11-wireless.mode,802-11-wireless.ssid,ipv4.method,ipv4.addresses",
            "connection",
            "show",
            found_uuid,
        ],
        5.0,
    )

    details: Dict[str, str] = {}
    if code_det == 0:
        for line in out_det.splitlines():
            line = line.strip()
            if ":" in line:
                k, v = line.split(":", 1)
                details[k.strip()] = v.strip().replace(r"\:", ":")

    # 3. Check if active in active connections
    code_act, out_act, _ = runner(["nmcli", "-t", "-f", "NAME,UUID,DEVICE", "connection", "show", "--active"], 5.0)
    is_active = False
    active_device = found_dev

    if code_act == 0:
        for line in out_act.splitlines():
            parts = [p.replace(r"\:", ":") for p in line.strip().split(":")]
            if len(parts) >= 2 and (parts[0] == profile_name or parts[1] == found_uuid):
                is_active = True
                if len(parts) >= 3 and parts[2] and parts[2] != "--":
                    active_device = parts[2]
                break

    mode = details.get("802-11-wireless.mode")
    ssid = details.get("802-11-wireless.ssid")
    ipv4_method = details.get("ipv4.method")
    ipv4_addresses = details.get("ipv4.addresses")

    is_ina = (
        profile_name.startswith(INA_PROFILE_PREFIX)
        and (found_type in ("802-11-wireless", "wifi"))
        and (mode == "ap" or mode is None)
    )

    return ProfileStatus(
        profile_name=profile_name,
        exists=True,
        uuid=found_uuid,
        interface=active_device,
        mode=mode,
        ssid=ssid,
        is_active=is_active,
        active_device=active_device if is_active else None,
        is_ina_managed=is_ina,
        ipv4_method=ipv4_method,
        ipv4_addresses=ipv4_addresses,
    )


# ==============================================================================
# Pre-Flight Safety Checks
# ==============================================================================


def preflight_safety_check(
    config: APConfig,
    runner: Optional[CommandRunner] = None,
) -> PreflightReport:
    """Evaluate host network conditions and detect conflicts before AP operations.

    Resolves target interface, inspects wireless capabilities, checks for active
    uplink conflicts, validates subnet overlap, and categorizes untested conditions.

    Returns:
        PreflightReport detailing whether activation can proceed safely.
    """
    if runner is None:
        runner = default_command_runner

    # 1. Resolve Target Interface
    target_iface_name, resolution_reason, report = resolve_ap_interface(
        config_interface=config.interface,
        runner=runner,
    )

    conflicts: List[str] = []
    warnings: List[str] = []
    recommendations: List[str] = []

    # Standard untested operational conditions
    untested_conditions: List[str] = [
        "Client DHCP IP address assignment and DNS resolution have not been verified (requires live AP activation).",
        "Simultaneous multi-channel RF coexistence with upstream Wi-Fi has not been verified.",
        "Packet routing, iptables/nftables forwarding, and firewall isolation are pending future milestones.",
    ]

    # Inspect NetworkManager profile existence
    prof_status = inspect_profile(config.profile_name, runner=runner)
    if not prof_status.exists:
        profile_status = "missing"
    elif not prof_status.is_ina_managed:
        profile_status = "unmanaged_conflict"
        conflicts.append(
            f"Profile name '{config.profile_name}' already exists in NetworkManager and is not an INA-managed profile. "
            "Refusing to overwrite unmanaged host connection."
        )
    elif prof_status.is_active:
        profile_status = "active"
    else:
        profile_status = "inactive"

    # Case A: Interface could not be resolved
    if not target_iface_name:
        conflicts.append(
            f"Target Wi-Fi interface is not specified and could not be automatically resolved: {resolution_reason}"
        )
        recommendations.append(
            "Configure an explicit interface using '--interface <name>' or set 'network.interface' in config/settings.yaml."
        )
        return PreflightReport(
            can_safely_activate=False,
            target_interface=None,
            interface_resolution_reason=resolution_reason,
            interface_status="unspecified",
            ap_capability_status="not_applicable",
            uplink_status="clear",
            subnet_status="clear",
            profile_status=profile_status,
            conflicts=conflicts,
            warnings=warnings,
            recommendations=recommendations,
            untested_conditions=untested_conditions,
        )

    # Case B: Interface name is known; inspect on host
    iface_dict = {i.name: i for i in report.interfaces}
    target_iface = iface_dict.get(target_iface_name)

    is_wireless = False
    ap_mode_supported: Optional[bool] = None
    interface_status = "configured"
    ap_capability_status = "unknown"

    if not target_iface:
        interface_status = "missing"
        ap_capability_status = "not_applicable"
        conflicts.append(f"Configured interface '{target_iface_name}' does not exist on the host.")
        recommendations.append("Check available interfaces using 'python3 -m network.device_discovery'.")
    else:
        is_wireless = target_iface.is_wireless
        ap_mode_supported = target_iface.ap_mode_supported

        if not is_wireless:
            interface_status = "not_wireless"
            ap_capability_status = "not_applicable"
            conflicts.append(
                f"Interface '{target_iface_name}' is not a wireless interface (detected type: {target_iface.interface_type})."
            )
        elif ap_mode_supported is True:
            ap_capability_status = "supported"
        elif ap_mode_supported is False:
            ap_capability_status = "unsupported"
            conflicts.append(
                f"Wireless driver for interface '{target_iface_name}' does not advertise AP mode support in nl80211."
            )
        else:
            ap_capability_status = "unknown"
            warnings.append(
                f"Driver AP mode support could not be verified for '{target_iface_name}' (missing 'iw' or permissions)."
            )

    # Inspect active NetworkManager connections on the target interface
    code_act, out_act, _ = runner(["nmcli", "-t", "-f", "NAME,DEVICE,TYPE", "connection", "show", "--active"], 5.0)
    active_conn_on_iface: Optional[str] = None

    if code_act == 0:
        for line in out_act.splitlines():
            parts = [p.replace(r"\:", ":") for p in line.strip().split(":")]
            if len(parts) >= 2 and parts[1] == target_iface_name:
                conn_name = parts[0]
                if conn_name != config.profile_name:
                    active_conn_on_iface = conn_name
                break

    # Detect active uplink conflict & simultaneous Wi-Fi constraints
    has_uplink_conflict = False
    uplink_interfaces = {uc.interface for uc in report.uplink_candidates}
    default_route_interfaces = {r.interface for r in report.default_routes}

    if target_iface_name in uplink_interfaces or target_iface_name in default_route_interfaces:
        has_uplink_conflict = True
        uplink_status = "conflict"
        conn_str = f" connection '{active_conn_on_iface}'" if active_conn_on_iface else ""
        conflicts.append(
            f"Active uplink conflict: Interface '{target_iface_name}' currently carries an active host default route "
            f"via{conn_str}. Activating an access point on this interface will disconnect the existing uplink and "
            "sever host internet connectivity. Simultaneous AP/STA operation on a single physical radio is constrained "
            "or unsupported."
        )
        recommendations.append(
            "Use a dedicated secondary Wi-Fi adapter (e.g. USB Wi-Fi dongle) or Ethernet uplink to host the AP safely."
        )
    elif active_conn_on_iface:
        uplink_status = "active_non_uplink"
        warnings.append(
            f"Interface '{target_iface_name}' has an active connection '{active_conn_on_iface}'. "
            "Activating the AP profile will replace or interrupt this connection."
        )
    else:
        uplink_status = "clear"

    # Check subnet collision against existing routes
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
                        f"Subnet collision: Configured AP subnet '{config.subnet}' collides with existing route "
                        f"'{r.destination}' on device '{r.interface}'."
                    )
            except ValueError:
                continue
    except ValueError:
        subnet_status = "invalid"

    can_safely_activate = (
        len(conflicts) == 0
        and interface_status == "configured"
        and ap_capability_status != "unsupported"
        and not has_uplink_conflict
    )

    return PreflightReport(
        can_safely_activate=can_safely_activate,
        target_interface=target_iface_name,
        interface_resolution_reason=resolution_reason,
        interface_status=interface_status,
        ap_capability_status=ap_capability_status,
        uplink_status=uplink_status,
        subnet_status=subnet_status,
        profile_status=profile_status,
        has_active_uplink_conflict=has_uplink_conflict,
        active_connection_on_interface=active_conn_on_iface,
        is_wireless=is_wireless,
        ap_mode_supported=ap_mode_supported,
        conflicts=conflicts,
        warnings=warnings,
        recommendations=recommendations,
        untested_conditions=untested_conditions,
    )


# ==============================================================================
# AP Profile Operations
# ==============================================================================


def _mask_command_for_logging(cmd: List[str]) -> List[str]:
    """Mask Wi-Fi passwords in command lists for safe logging/reporting."""
    masked: List[str] = []
    skip_next = False
    for token in cmd:
        if skip_next:
            masked.append("***")
            skip_next = False
        elif token in ("wifi-sec.psk", "psk"):
            masked.append(token)
            skip_next = True
        else:
            masked.append(token)
    return masked


def create_ap_profile(
    config: APConfig,
    dry_run: bool = True,
    runner: Optional[CommandRunner] = None,
) -> OperationResult:
    """Create or update a dedicated INA access point profile in NetworkManager.

    Args:
        config: APConfig specification.
        dry_run: If True (default), plans commands without modifying system state.
        runner: Optional command runner.

    Returns:
        OperationResult describing the outcome and planned/executed commands.
    """
    if runner is None:
        runner = default_command_runner

    # 1. Resolve Target Interface if not explicitly provided
    target_iface, iface_reason, _ = resolve_ap_interface(
        config_interface=config.interface,
        runner=runner,
    )
    if not target_iface:
        return OperationResult(
            success=False,
            dry_run=dry_run,
            message=f"Target interface could not be resolved: {iface_reason}",
            details={"resolution_reason": iface_reason},
        )
    config.interface = target_iface

    # 2. Validate inputs
    val_errors = validate_ap_config(config)
    if val_errors:
        return OperationResult(
            success=False,
            dry_run=dry_run,
            message=f"Configuration validation failed: {'; '.join(val_errors)}",
            details={"validation_errors": val_errors},
        )

    # 3. Inspect existing profile
    status = inspect_profile(config.profile_name, runner=runner)
    if status.exists and not status.is_ina_managed:
        return OperationResult(
            success=False,
            dry_run=dry_run,
            message=(
                f"Refusing to overwrite profile '{config.profile_name}': Existing connection is not "
                "identified as an INA-managed profile."
            ),
            details=status.to_dict(),
        )

    # 4. Derive Gateway Address
    net = ipaddress.ip_network(config.subnet, strict=False)
    try:
        specified_ip = ipaddress.ip_interface(config.subnet).ip
        if specified_ip in (net.network_address, net.broadcast_address):
            gw_ip = next(net.hosts())
        else:
            gw_ip = specified_ip
    except (ValueError, StopIteration):
        gw_ip = next(net.hosts())
    gateway_cidr = f"{gw_ip}/{net.prefixlen}"

    # 5. Formulate NetworkManager commands
    planned_commands: List[List[str]] = []

    if not status.exists:
        add_cmd = [
            "nmcli",
            "connection",
            "add",
            "type",
            "wifi",
            "ifname",
            config.interface,
            "con-name",
            config.profile_name,
            "autoconnect",
            "no",
            "ssid",
            config.ssid,
            "mode",
            "ap",
        ]
        planned_commands.append(add_cmd)

    modify_cmd = [
        "nmcli",
        "connection",
        "modify",
        config.profile_name,
        "802-11-wireless.band",
        config.band,
        "wifi-sec.key-mgmt",
        "wpa-psk",
        "wifi-sec.psk",
        config.password,
        "ipv4.method",
        "shared",
        "ipv4.addresses",
        gateway_cidr,
        "ipv6.method",
        "ignore",
    ]
    if config.channel is not None:
        modify_cmd.extend(["802-11-wireless.channel", str(config.channel)])

    planned_commands.append(modify_cmd)

    safe_commands = [_mask_command_for_logging(c) for c in planned_commands]

    if dry_run:
        return OperationResult(
            success=True,
            dry_run=True,
            message=(
                f"DRY RUN: Profile '{config.profile_name}' would be configured on '{config.interface}' "
                f"with SSID '{config.ssid}'."
            ),
            planned_commands=safe_commands,
            details={
                "gateway_cidr": gateway_cidr,
                "interface_resolution": iface_reason,
                "config": config.to_dict(mask_password=True),
            },
        )

    # Execute commands
    for cmd in planned_commands:
        code, out, err = runner(cmd, 10.0)
        if code != 0:
            err_msg = err.strip() if err.strip() else f"exit code {code}"
            return OperationResult(
                success=False,
                dry_run=False,
                message=f"NetworkManager command failed while creating profile: {err_msg}",
                planned_commands=safe_commands,
                details={"failed_command": _mask_command_for_logging(cmd), "error": err_msg},
            )

    return OperationResult(
        success=True,
        dry_run=False,
        message=f"Profile '{config.profile_name}' successfully created/updated in NetworkManager.",
        planned_commands=safe_commands,
        details={
            "gateway_cidr": gateway_cidr,
            "interface_resolution": iface_reason,
            "config": config.to_dict(mask_password=True),
        },
    )


def activate_ap_profile(
    config: APConfig,
    dry_run: bool = True,
    confirm: bool = False,
    force_disconnect_uplink: bool = False,
    runner: Optional[CommandRunner] = None,
) -> OperationResult:
    """Safely activate an existing INA access point profile in NetworkManager.

    Requires:
    - Pre-flight safety check passed without un-overridden uplink conflicts.
    - Explicit confirmation (confirm=True).
    - Verification of active operational state after activation.

    Args:
        config: APConfig specification.
        dry_run: If True (default), verifies safety and plans activation without executing.
        confirm: Explicit operator confirmation required for live execution.
        force_disconnect_uplink: Override flag if user intentionally wishes to replace uplink Wi-Fi.
        runner: Optional command runner.

    Returns:
        OperationResult detailing activation outcome.
    """
    if runner is None:
        runner = default_command_runner

    # 1. Run Pre-flight safety assessment
    preflight = preflight_safety_check(config, runner=runner)

    # 2. Check for blocking conflicts
    if preflight.has_active_uplink_conflict and not force_disconnect_uplink:
        return OperationResult(
            success=False,
            dry_run=dry_run,
            message=(
                "Activation REFUSED: Active uplink conflict detected. Interface "
                f"'{preflight.target_interface}' is currently providing host internet connectivity. "
                "Activating an AP here would sever the connection. Stopping to prevent disconnection."
            ),
            details={"preflight": preflight.to_dict()},
        )

    if not preflight.can_safely_activate and not (preflight.has_active_uplink_conflict and force_disconnect_uplink):
        return OperationResult(
            success=False,
            dry_run=dry_run,
            message=f"Activation REFUSED: Safety checks failed: {'; '.join(preflight.conflicts)}",
            details={"preflight": preflight.to_dict()},
        )

    # 3. Check operator confirmation
    if not confirm:
        return OperationResult(
            success=False,
            dry_run=dry_run,
            message=(
                "Activation REFUSED: Explicit confirmation (confirm=True) is required "
                "before activating an access point profile."
            ),
            details={"preflight": preflight.to_dict()},
        )

    # 4. Ensure profile exists
    status = inspect_profile(config.profile_name, runner=runner)
    if not status.exists:
        return OperationResult(
            success=False,
            dry_run=dry_run,
            message=f"Profile '{config.profile_name}' does not exist in NetworkManager. Create it first.",
            details={"preflight": preflight.to_dict()},
        )
    if not status.is_ina_managed:
        return OperationResult(
            success=False,
            dry_run=dry_run,
            message=f"Refusing to activate profile '{config.profile_name}': Profile is not an INA-managed AP.",
            details=status.to_dict(),
        )

    up_cmd = ["nmcli", "connection", "up", config.profile_name]

    if dry_run:
        return OperationResult(
            success=True,
            dry_run=True,
            message=f"DRY RUN: AP profile '{config.profile_name}' would be activated on '{preflight.target_interface}'.",
            planned_commands=[up_cmd],
            details={"preflight": preflight.to_dict()},
        )

    # 5. Execute activation
    code, out, err = runner(up_cmd, 15.0)
    if code != 0:
        err_msg = err.strip() if err.strip() else f"exit code {code}"
        return OperationResult(
            success=False,
            dry_run=False,
            message=f"Failed to activate AP profile '{config.profile_name}': {err_msg}",
            planned_commands=[up_cmd],
            details={"error": err_msg, "preflight": preflight.to_dict()},
        )

    # 6. Post-activation verification
    post_status = inspect_profile(config.profile_name, runner=runner)
    if not post_status.is_active:
        return OperationResult(
            success=False,
            dry_run=False,
            message=(
                f"Activation command completed, but profile '{config.profile_name}' is not reporting "
                "active state in NetworkManager. AP is not verified usable."
            ),
            planned_commands=[up_cmd],
            details={"post_status": post_status.to_dict()},
        )

    return OperationResult(
        success=True,
        dry_run=False,
        message=f"Access point '{config.profile_name}' successfully activated on '{preflight.target_interface}'.",
        planned_commands=[up_cmd],
        details={"status": post_status.to_dict()},
    )


def deactivate_ap_profile(
    profile_name: str = DEFAULT_AP_PROFILE_NAME,
    dry_run: bool = True,
    runner: Optional[CommandRunner] = None,
) -> OperationResult:
    """Deactivate an active INA access point profile.

    Args:
        profile_name: Name of profile (must be INA-managed).
        dry_run: If True, plans deactivation without executing.
        runner: Optional command runner.

    Returns:
        OperationResult detailing deactivation outcome.
    """
    if runner is None:
        runner = default_command_runner

    # Strict namespace check
    if not profile_name.startswith(INA_PROFILE_PREFIX):
        return OperationResult(
            success=False,
            dry_run=dry_run,
            message=(
                f"PROTECTION VIOLATION: Refusing to deactivate profile '{profile_name}'. "
                f"Only profiles starting with '{INA_PROFILE_PREFIX}' are managed by INA."
            ),
        )

    status = inspect_profile(profile_name, runner=runner)
    if not status.exists:
        return OperationResult(
            success=False,
            dry_run=dry_run,
            message=f"Profile '{profile_name}' does not exist in NetworkManager.",
        )

    if not status.is_ina_managed:
        return OperationResult(
            success=False,
            dry_run=dry_run,
            message=f"Refusing to deactivate profile '{profile_name}': Profile is not an INA-managed AP.",
            details=status.to_dict(),
        )

    if not status.is_active:
        return OperationResult(
            success=True,
            dry_run=dry_run,
            message=f"Profile '{profile_name}' is already inactive.",
            details=status.to_dict(),
        )

    down_cmd = ["nmcli", "connection", "down", profile_name]

    if dry_run:
        return OperationResult(
            success=True,
            dry_run=True,
            message=f"DRY RUN: Profile '{profile_name}' would be deactivated.",
            planned_commands=[down_cmd],
            details=status.to_dict(),
        )

    code, out, err = runner(down_cmd, 10.0)
    if code != 0:
        err_msg = err.strip() if err.strip() else f"exit code {code}"
        return OperationResult(
            success=False,
            dry_run=False,
            message=f"Failed to deactivate profile '{profile_name}': {err_msg}",
            planned_commands=[down_cmd],
        )

    return OperationResult(
        success=True,
        dry_run=False,
        message=f"Profile '{profile_name}' successfully deactivated.",
        planned_commands=[down_cmd],
    )


def remove_ap_profile(
    profile_name: str = DEFAULT_AP_PROFILE_NAME,
    dry_run: bool = True,
    runner: Optional[CommandRunner] = None,
) -> OperationResult:
    """Deactivate and delete only an INA-managed access point profile.

    Strictly refuses to delete or touch any profile that does not match the
    INA namespace prefix ('ina-') or is not an AP connection.

    Args:
        profile_name: Name of profile to remove.
        dry_run: If True, plans removal without deleting.
        runner: Optional command runner.

    Returns:
        OperationResult describing removal outcome.
    """
    if runner is None:
        runner = default_command_runner

    # Strict namespace check
    if not profile_name.startswith(INA_PROFILE_PREFIX):
        return OperationResult(
            success=False,
            dry_run=dry_run,
            message=(
                f"PROTECTION VIOLATION: Refusing to delete profile '{profile_name}'. "
                f"Only profiles starting with '{INA_PROFILE_PREFIX}' are managed by INA."
            ),
        )

    status = inspect_profile(profile_name, runner=runner)
    if not status.exists:
        return OperationResult(
            success=True,
            dry_run=dry_run,
            message=f"Profile '{profile_name}' does not exist (nothing to remove).",
        )

    if not status.is_ina_managed:
        return OperationResult(
            success=False,
            dry_run=dry_run,
            message=(
                f"PROTECTION VIOLATION: Profile '{profile_name}' is not identified as an "
                "INA-managed AP profile. Refusing deletion."
            ),
            details=status.to_dict(),
        )

    planned: List[List[str]] = []
    if status.is_active:
        planned.append(["nmcli", "connection", "down", profile_name])
    planned.append(["nmcli", "connection", "delete", profile_name])

    if dry_run:
        return OperationResult(
            success=True,
            dry_run=True,
            message=f"DRY RUN: Profile '{profile_name}' would be safely deleted.",
            planned_commands=planned,
            details=status.to_dict(),
        )

    # Deactivate first if active
    if status.is_active:
        deact_res = deactivate_ap_profile(profile_name, dry_run=False, runner=runner)
        if not deact_res.success:
            return OperationResult(
                success=False,
                dry_run=False,
                message=f"Failed to bring down active profile '{profile_name}' before deletion: {deact_res.message}",
            )

    code, out, err = runner(["nmcli", "connection", "delete", profile_name], 10.0)
    if code != 0:
        err_msg = err.strip() if err.strip() else f"exit code {code}"
        return OperationResult(
            success=False,
            dry_run=False,
            message=f"Failed to delete profile '{profile_name}': {err_msg}",
            planned_commands=planned,
        )

    return OperationResult(
        success=True,
        dry_run=False,
        message=f"Profile '{profile_name}' successfully removed from NetworkManager.",
        planned_commands=planned,
    )


# ==============================================================================
# CLI Entry Point
# ==============================================================================


def main() -> None:
    """CLI entry point for AP management operations."""
    parser = argparse.ArgumentParser(
        prog="python -m network.access_point",
        description="INA v2 — Safe NetworkManager Access Point Profile Management.",
    )
    subparsers = parser.add_subparsers(dest="action", help="Action to perform")

    # Common options
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "--no-dry-run",
        action="store_true",
        dest="execute",
        help="Execute changes live (defaults to safe dry-run mode).",
    )
    parent_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output result as JSON.",
    )

    # 1. status subcommand
    status_p = subparsers.add_parser("status", parents=[parent_parser], help="Inspect AP profile status and safety.")
    status_p.add_argument("--profile", default=DEFAULT_AP_PROFILE_NAME, help="Profile name (default: ina-ap).")
    status_p.add_argument("--interface", default=None, help="Wi-Fi interface (auto-discovered if omitted).")

    # 2. create subcommand
    create_p = subparsers.add_parser("create", parents=[parent_parser], help="Create/update AP profile.")
    create_p.add_argument("--profile", default=DEFAULT_AP_PROFILE_NAME, help="Profile name (default: ina-ap).")
    create_p.add_argument("--ssid", default=DEFAULT_AP_SSID, help="SSID for AP.")
    create_p.add_argument("--password", required=True, help="WPA2 passphrase.")
    create_p.add_argument("--interface", default=None, help="Wi-Fi interface (auto-discovered if omitted).")
    create_p.add_argument("--subnet", default=DEFAULT_AP_SUBNET, help="IPv4 subnet CIDR.")
    create_p.add_argument("--channel", type=int, default=None, help="Wi-Fi channel.")
    create_p.add_argument("--band", default="bg", choices=["bg", "a"], help="Wi-Fi band.")

    # 3. activate subcommand
    act_p = subparsers.add_parser("activate", parents=[parent_parser], help="Activate AP profile.")
    act_p.add_argument("--profile", default=DEFAULT_AP_PROFILE_NAME, help="Profile name.")
    act_p.add_argument("--interface", default=None, help="Wi-Fi interface (auto-discovered if omitted).")
    act_p.add_argument(
        "--confirm",
        action="store_true",
        dest="confirm",
        help="Explicit confirmation required to activate.",
    )
    act_p.add_argument(
        "--force-disconnect-uplink",
        action="store_true",
        dest="force_uplink",
        help="Force activation even if it disconnects the existing uplink connection.",
    )

    # 4. deactivate subcommand
    deact_p = subparsers.add_parser("deactivate", parents=[parent_parser], help="Deactivate AP profile.")
    deact_p.add_argument("--profile", default=DEFAULT_AP_PROFILE_NAME, help="Profile name.")

    # 5. delete subcommand
    del_p = subparsers.add_parser("delete", parents=[parent_parser], help="Delete INA AP profile.")
    del_p.add_argument("--profile", default=DEFAULT_AP_PROFILE_NAME, help="Profile name.")

    args = parser.parse_args()

    if not args.action:
        parser.print_help()
        sys.exit(0)

    dry_run = not getattr(args, "execute", False)

    if args.action == "status":
        cfg = APConfig(profile_name=args.profile, interface=args.interface, password="placeholder")
        prof = inspect_profile(args.profile)
        preflight = preflight_safety_check(cfg)
        result_data = {
            "profile_status": prof.to_dict(),
            "preflight_safety": preflight.to_dict(),
        }
        if args.json_output:
            print(json.dumps(result_data, indent=2))
        else:
            sep = "=" * 74
            subsep = "-" * 74
            print(sep)
            print("INA v2 — Access Point Status & Pre-flight Diagnostics")
            print(sep)

            # Profile Info
            prof_state = "ACTIVE" if prof.is_active else ("INACTIVE" if prof.exists else "MISSING")
            print("Profile Information:")
            print(f"  Profile Name:      {prof.profile_name}")
            print(f"  Profile Status:    {prof_state}")
            print(f"  INA-Managed:       {'Yes' if prof.is_ina_managed else 'No'}")
            if prof.is_active and prof.active_device:
                print(f"  Active Device:     {prof.active_device}")

            # Interface Resolution
            target_display = preflight.target_interface if preflight.target_interface else "(Unspecified)"
            print(f"\nInterface Resolution:")
            print(f"  Target Interface:  {target_display}")
            print(f"  Resolution Source: {preflight.interface_resolution_reason}")
            print(f"  Interface Status:  {preflight.interface_status.upper()}")
            print(f"  AP Driver Support: {preflight.ap_capability_status.upper()}")

            # Assessment
            can_act_str = "YES" if preflight.can_safely_activate else "NO (Safety Conflicts Present)"
            print(f"\nPre-Flight Safety Assessment:")
            print(f"  Can Activate:      {can_act_str}")
            print(f"  Uplink Status:     {preflight.uplink_status.upper()}")
            print(f"  Subnet Status:     {preflight.subnet_status.upper()} ({cfg.subnet})")

            # Conflicts
            if preflight.conflicts:
                print(f"\nConflicts & Blocking Issues:")
                for c in preflight.conflicts:
                    print(f"  ! {c}")

            # Warnings
            if preflight.warnings:
                print(f"\nWarnings:")
                for w in preflight.warnings:
                    print(f"  * {w}")

            # Untested conditions
            if preflight.untested_conditions:
                print(f"\nUntested Conditions:")
                for u in preflight.untested_conditions:
                    print(f"  ? {u}")

            # Recommendations
            if preflight.recommendations:
                print(f"\nRecommendations:")
                for r in preflight.recommendations:
                    print(f"  -> {r}")

            print(sep)

    elif args.action == "create":
        cfg = APConfig(
            ssid=args.ssid,
            password=args.password,
            interface=args.interface,
            subnet=args.subnet,
            channel=args.channel,
            band=args.band,
            profile_name=args.profile,
        )
        res = create_ap_profile(cfg, dry_run=dry_run)
        if args.json_output:
            print(json.dumps(res.to_dict(), indent=2))
        else:
            print(f"Result: {'SUCCESS' if res.success else 'FAILED'}")
            print(f"Dry Run: {res.dry_run}")
            print(f"Message: {res.message}")
            if res.planned_commands:
                print("Commands:")
                for c in res.planned_commands:
                    print(f"  $ {' '.join(c)}")

    elif args.action == "activate":
        cfg = APConfig(profile_name=args.profile, interface=args.interface, password="dummy_password_for_check")
        res = activate_ap_profile(
            cfg,
            dry_run=dry_run,
            confirm=args.confirm,
            force_disconnect_uplink=args.force_uplink,
        )
        if args.json_output:
            print(json.dumps(res.to_dict(), indent=2))
        else:
            print(f"Result: {'SUCCESS' if res.success else 'FAILED'}")
            print(f"Dry Run: {res.dry_run}")
            print(f"Message: {res.message}")
            if res.planned_commands:
                print("Commands:")
                for c in res.planned_commands:
                    print(f"  $ {' '.join(c)}")

    elif args.action == "deactivate":
        res = deactivate_ap_profile(profile_name=args.profile, dry_run=dry_run)
        if args.json_output:
            print(json.dumps(res.to_dict(), indent=2))
        else:
            print(f"Result: {'SUCCESS' if res.success else 'FAILED'}")
            print(f"Dry Run: {res.dry_run}")
            print(f"Message: {res.message}")

    elif args.action == "delete":
        res = remove_ap_profile(profile_name=args.profile, dry_run=dry_run)
        if args.json_output:
            print(json.dumps(res.to_dict(), indent=2))
        else:
            print(f"Result: {'SUCCESS' if res.success else 'FAILED'}")
            print(f"Dry Run: {res.dry_run}")
            print(f"Message: {res.message}")


if __name__ == "__main__":
    main()
