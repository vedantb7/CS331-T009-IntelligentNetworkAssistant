"""Network access control module for INA v2.

Enforces device admission, client quarantine, and firewall filtering rules
targeting the Raspberry Pi 5 network infrastructure host ('ina-router').

Architectural Alignment:
- Enforces packet filtering via 'nftables' using the dedicated 'inet ina_filter' table.
- Does NOT disconnect or kick the client off the Wi-Fi network; 802.11 association
  and ARP/neighbor state remain preserved.
- Core router services (DHCP on UDP 67, DNS on UDP/TCP 53) remain accessible in the
  'input' chain so the client retains local IP configuration.
"""

from __future__ import annotations

import ipaddress
import os
import re
from typing import Callable, Dict, List, Optional, Set, Tuple

from inav2.mcp_server.schemas import (
    CLIENT_REGEX,
    TARGET_REGEX,
    BlockResult,
    UnblockResult,
)
from inav2.network.verification import (
    verify_block_applied,
    verify_block_removed,
    verify_client_association,
)

# Command runner type alias matching device_discovery.py
CommandRunner = Callable[[List[str], float], Tuple[int, str, str]]


def _default_runner(args: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
    from inav2.network.device_discovery import default_command_runner
    return default_command_runner(args, timeout)


# Constants for the INA nftables filtering infrastructure
TABLE_FAMILY = "inet"
TABLE_NAME = "ina_filter"
SET_BLOCKED_PAIRS = "blocked_pairs"
SET_BLOCKED_INTERNET = "blocked_internet"
SET_BLOCKED_GATEWAY = "blocked_gateway"
SET_ISOLATED = "isolated"
SET_MAC_IP_BIND = "mac_ip_bind"
SET_MANAGED_IPS = "managed_ips"

ROUTER_IP = "192.168.50.1"
PROTECTED_IPS: Set[str] = {ROUTER_IP}
DEFAULT_LEASES_FILE = "/var/lib/misc/dnsmasq.leases"

SCOPE_TARGETS = {"internet", "gateway", "all"}

# Full nftables base configuration template matching INA research specification
NFTABLES_INIT_RULES = f"""
table {TABLE_FAMILY} {TABLE_NAME} {{
\tset {SET_MAC_IP_BIND} {{
\t\ttype ether_addr . ipv4_addr
\t}}
\tset {SET_MANAGED_IPS} {{
\t\ttype ipv4_addr
\t}}
\tset {SET_BLOCKED_PAIRS} {{
\t\ttype ipv4_addr . ipv4_addr
\t}}
\tset {SET_BLOCKED_INTERNET} {{
\t\ttype ipv4_addr
\t}}
\tset {SET_BLOCKED_GATEWAY} {{
\t\ttype ipv4_addr
\t}}
\tset {SET_ISOLATED} {{
\t\ttype ipv4_addr
\t}}

\tchain prerouting {{
\t\ttype filter hook prerouting priority raw; policy accept;
\t\tiifname "ap0" ip saddr @{SET_MANAGED_IPS} ether saddr . ip saddr != @{SET_MAC_IP_BIND} counter drop
\t}}

\tchain forward {{
\t\ttype filter hook forward priority filter; policy accept;
\t\tiifname "ap0" oifname "ap0" ip saddr . ip daddr @{SET_BLOCKED_PAIRS} counter reject with icmpx admin-prohibited
\t\tiifname "ap0" oifname "ap0" ip saddr @{SET_ISOLATED} counter reject with icmpx admin-prohibited
\t\tiifname "ap0" oifname "ap0" ip daddr @{SET_ISOLATED} counter reject with icmpx admin-prohibited
\t\tiifname "ap0" oifname "wlan0" ip saddr @{SET_BLOCKED_INTERNET} counter reject with icmpx admin-prohibited
\t\tiifname "ap0" oifname "wlan0" ip saddr @{SET_ISOLATED} counter reject with icmpx admin-prohibited
\t\tiifname "wlan0" oifname "ap0" ip daddr @{SET_BLOCKED_INTERNET} counter drop
\t\tiifname "wlan0" oifname "ap0" ip daddr @{SET_ISOLATED} counter drop
\t\tiifname "ap0" meta nfproto ipv6 counter drop
\t}}

\tchain input {{
\t\ttype filter hook input priority filter; policy accept;
\t\tiifname "ap0" udp dport {{ 53, 67 }} accept
\t\tiifname "ap0" tcp dport 53 accept
\t\tiifname "ap0" ip saddr @{SET_BLOCKED_GATEWAY} counter reject with icmpx admin-prohibited
\t\tiifname "ap0" ip saddr @{SET_ISOLATED} counter reject with icmpx admin-prohibited
\t}}
}}
"""


# ==============================================================================
# Identifier Validation and Resolution
# ==============================================================================

def _load_leases_map(leases_file: Optional[str] = None) -> Dict[str, str]:
    """Read dnsmasq.leases file and map hostnames to IP addresses."""
    path = leases_file or DEFAULT_LEASES_FILE
    mapping: Dict[str, str] = {}
    if not os.path.isfile(path):
        return mapping

    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 4:
                    ip = parts[2]
                    hostname = parts[3].lower()
                    if hostname != "*":
                        mapping[hostname] = ip
    except OSError:
        pass

    return mapping


def validate_client_identifier(
    client: str,
    leases_file: Optional[str] = None,
    leases_map: Optional[Dict[str, str]] = None,
) -> str:
    """Validate client identifier and resolve to an active IPv4 address."""
    client_clean = client.strip().lower()
    if not re.match(CLIENT_REGEX, client_clean):
        raise ValueError(
            f"Invalid client identifier: '{client}'. Must be a valid hostname or 192.168.50.0/24 address."
        )

    try:
        ip_obj = ipaddress.IPv4Address(client_clean)
        net = ipaddress.IPv4Network("192.168.50.0/24")
        if ip_obj not in net or ip_obj == net.network_address or ip_obj == net.broadcast_address:
            raise ValueError(f"Client IP '{client}' is outside managed subnet range (192.168.50.2-254).")
        return str(ip_obj)
    except ipaddress.AddressValueError:
        pass

    mapping = leases_map if leases_map is not None else _load_leases_map(leases_file)
    if client_clean in mapping:
        return mapping[client_clean]

    known = ", ".join(sorted(mapping.keys())) if mapping else "none"
    raise ValueError(f"Unknown client '{client}'. Unable to resolve to an active IP. Known clients: {known}.")


def validate_target_identifier(
    target: str,
    client_ip: str,
    leases_file: Optional[str] = None,
    leases_map: Optional[Dict[str, str]] = None,
) -> Tuple[str, str]:
    """Validate target identifier and resolve into (target_type, target_val)."""
    target_clean = target.strip().lower()
    if not re.match(TARGET_REGEX, target_clean):
        raise ValueError(
            f"Invalid target identifier: '{target}'. Must be 'internet', 'gateway', 'all', or a valid client."
        )

    if target_clean in SCOPE_TARGETS:
        return target_clean, target_clean

    target_ip = validate_client_identifier(target_clean, leases_file=leases_file, leases_map=leases_map)
    if target_ip == client_ip:
        raise ValueError("client and target must differ; a client cannot be blocked from itself.")

    return "client", target_ip


# ==============================================================================
# nftables Rule Application and Management
# ==============================================================================

def ensure_ina_filter_table(runner: Optional[CommandRunner] = None) -> bool:
    """Ensure the 'inet ina_filter' table and its required sets/chains exist."""
    run = runner or _default_runner
    code, stdout, _ = run(["nft", "list", "table", TABLE_FAMILY, TABLE_NAME], 5.0)
    if code == 0:
        return True

    # Initialize table and sets
    run(["nft", "add", "table", TABLE_FAMILY, TABLE_NAME], 5.0)
    run(["nft", "add", "set", TABLE_FAMILY, TABLE_NAME, SET_BLOCKED_PAIRS, "{ type ipv4_addr . ipv4_addr; }"], 5.0)
    run(["nft", "add", "set", TABLE_FAMILY, TABLE_NAME, SET_BLOCKED_INTERNET, "{ type ipv4_addr; }"], 5.0)
    run(["nft", "add", "set", TABLE_FAMILY, TABLE_NAME, SET_BLOCKED_GATEWAY, "{ type ipv4_addr; }"], 5.0)
    run(["nft", "add", "set", TABLE_FAMILY, TABLE_NAME, SET_ISOLATED, "{ type ipv4_addr; }"], 5.0)
    return True


def apply_block_rule(
    client_ip: str,
    target_type: str,
    target_val: str,
    runner: Optional[CommandRunner] = None,
) -> Tuple[bool, str]:
    """Add the blocking rule element(s) to the appropriate nftables set."""
    run = runner or _default_runner

    if target_type == "client":
        elem = f"{{ {client_ip} . {target_val}, {target_val} . {client_ip} }}"
        cmd = ["nft", "add", "element", TABLE_FAMILY, TABLE_NAME, SET_BLOCKED_PAIRS, elem]
    elif target_type == "internet":
        elem = f"{{ {client_ip} }}"
        cmd = ["nft", "add", "element", TABLE_FAMILY, TABLE_NAME, SET_BLOCKED_INTERNET, elem]
    elif target_type == "gateway":
        elem = f"{{ {client_ip} }}"
        cmd = ["nft", "add", "element", TABLE_FAMILY, TABLE_NAME, SET_BLOCKED_GATEWAY, elem]
    elif target_type == "all":
        elem = f"{{ {client_ip} }}"
        cmd = ["nft", "add", "element", TABLE_FAMILY, TABLE_NAME, SET_ISOLATED, elem]
    else:
        return False, f"Unknown target type: {target_type}"

    code, stdout, stderr = run(cmd, 5.0)
    if code != 0:
        return False, stderr.strip() or stdout.strip() or f"nft exited with code {code}"

    return True, ""


def apply_unblock_rule(
    client_ip: str,
    target_type: str,
    target_val: str,
    runner: Optional[CommandRunner] = None,
) -> Tuple[bool, str]:
    """Remove rule element(s) using 'nft destroy element' (idempotent delete)."""
    run = runner or _default_runner

    if target_type == "client":
        elem = f"{{ {client_ip} . {target_val}, {target_val} . {client_ip} }}"
        cmd = ["nft", "destroy", "element", TABLE_FAMILY, TABLE_NAME, SET_BLOCKED_PAIRS, elem]
    elif target_type == "internet":
        elem = f"{{ {client_ip} }}"
        cmd = ["nft", "destroy", "element", TABLE_FAMILY, TABLE_NAME, SET_BLOCKED_INTERNET, elem]
    elif target_type == "gateway":
        elem = f"{{ {client_ip} }}"
        cmd = ["nft", "destroy", "element", TABLE_FAMILY, TABLE_NAME, SET_BLOCKED_GATEWAY, elem]
    elif target_type == "all":
        elem = f"{{ {client_ip} }}"
        cmd = ["nft", "destroy", "element", TABLE_FAMILY, TABLE_NAME, SET_ISOLATED, elem]
    else:
        return False, f"Unknown target type: {target_type}"

    code, stdout, stderr = run(cmd, 5.0)
    if code != 0:
        # Fallback to 'delete' if 'destroy' keyword is unsupported on older nftables
        del_cmd = list(cmd)
        del_cmd[1] = "delete"
        code, stdout, stderr = run(del_cmd, 5.0)
        if code != 0 and "No such file or directory" not in stderr:
            return False, stderr.strip() or stdout.strip() or f"nft exited with code {code}"

    return True, ""


# ==============================================================================
# Public block_client & unblock_client Interfaces
# ==============================================================================

def block_client(
    client: str,
    target: str,
    runner: Optional[CommandRunner] = None,
    leases_file: Optional[str] = None,
    leases_map: Optional[Dict[str, str]] = None,
) -> BlockResult:
    """Block network communication from a client to a target while preserving association."""
    run = runner or _default_runner

    client_ip = validate_client_identifier(client, leases_file=leases_file, leases_map=leases_map)
    target_type, target_val = validate_target_identifier(
        target, client_ip, leases_file=leases_file, leases_map=leases_map
    )

    if client_ip in PROTECTED_IPS:
        raise PermissionError(f"Action blocked by policy: Client '{client_ip}' is protected infrastructure.")
    if target_val in PROTECTED_IPS:
        raise PermissionError(f"Action blocked by policy: Target '{target_val}' is protected infrastructure.")

    ensure_ina_filter_table(run)

    # Check idempotency
    if verify_block_applied(client_ip, target_type, target_val, runner=run):
        return BlockResult(
            client=client,
            client_ip=client_ip,
            target=target,
            blocked=True,
            changed=False,
            verified=True,
            message=f"Block rule between '{client}' ({client_ip}) and '{target}' is already active.",
        )

    applied, err = apply_block_rule(client_ip, target_type, target_val, runner=run)
    if not applied:
        raise RuntimeError(f"Failed to apply nftables block rule: {err}")

    verified = verify_block_applied(client_ip, target_type, target_val, runner=run)
    if not verified:
        apply_unblock_rule(client_ip, target_type, target_val, runner=run)
        raise RuntimeError(
            f"Verification failed: Rule for '{client}' ({client_ip}) -> '{target}' "
            "was not found in kernel state after application; rule was rolled back."
        )

    return BlockResult(
        client=client,
        client_ip=client_ip,
        target=target,
        blocked=True,
        changed=True,
        verified=True,
        message=f"Successfully blocked traffic between '{client}' ({client_ip}) and '{target}'. Client association preserved.",
    )


def unblock_client(
    client: str,
    target: str,
    runner: Optional[CommandRunner] = None,
    leases_file: Optional[str] = None,
    leases_map: Optional[Dict[str, str]] = None,
) -> UnblockResult:
    """Remove a previously applied block rule between client and target.

    Bandwidth limits are NEVER touched by unblock operations.
    """
    run = runner or _default_runner

    client_ip = validate_client_identifier(client, leases_file=leases_file, leases_map=leases_map)
    target_type, target_val = validate_target_identifier(
        target, client_ip, leases_file=leases_file, leases_map=leases_map
    )

    ensure_ina_filter_table(run)

    # Idempotency check: if already unblocked, return changed=False
    if not verify_block_applied(client_ip, target_type, target_val, runner=run):
        return UnblockResult(
            client=client,
            client_ip=client_ip,
            target=target,
            unblocked=True,
            changed=False,
            verified=True,
            message=f"Rule between '{client}' ({client_ip}) and '{target}' is already absent.",
        )

    removed, err = apply_unblock_rule(client_ip, target_type, target_val, runner=run)
    if not removed:
        raise RuntimeError(f"Failed to remove nftables block rule: {err}")

    verified = verify_block_removed(client_ip, target_type, target_val, runner=run)
    if not verified:
        raise RuntimeError(f"Verification failed: Rule for '{client}' was still found in kernel set.")

    return UnblockResult(
        client=client,
        client_ip=client_ip,
        target=target,
        unblocked=True,
        changed=True,
        verified=True,
        message=f"Successfully unblocked traffic between '{client}' ({client_ip}) and '{target}'.",
    )
