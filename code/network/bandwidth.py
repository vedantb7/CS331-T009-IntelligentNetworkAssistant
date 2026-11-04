"""Bandwidth management and traffic shaping module for INA v2.

Enforces per-client bandwidth limits using Linux Traffic Control ('tc') with
Hierarchical Token Bucket (HTB) and Intermediate Functional Block (IFB) ingress redirection.

Architectural Design:
- Egress on 'ap0' shapes client download.
- Ingress on 'ap0' is mirrored to 'ifb-ap0' via 'clsact' + 'mirred' to shape client upload.
- Class IDs are deterministically derived: classid 1:(0x100 + last_octet), e.g. .20 -> 1:114.
- Burst is sized dynamically: max(15000, int(rate_kbit * 1000 / 800)) to prevent buffer starvation.
- Per-client leaf qdiscs use 'sfq perturb 60' for intra-client flow fairness.
- Bandwidth operations are completely independent of nftables packet filtering rules.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Tuple

from inav2.mcp_server.schemas import (
    CLIENT_REGEX,
    MAX_BANDWIDTH_KBIT,
    MIN_BANDWIDTH_KBIT,
    LimitResult,
    RemoveLimitResult,
)
from inav2.network.access_control import validate_client_identifier
from inav2.network.verification import (
    derive_classid_hex,
    verify_bandwidth_applied,
    verify_bandwidth_removed,
)

# Command runner type alias matching device_discovery.py
CommandRunner = Callable[[List[str], float], Tuple[int, str, str]]


def _default_runner(args: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
    from inav2.network.device_discovery import default_command_runner
    return default_command_runner(args, timeout)


AP_INTERFACE = "ap0"
IFB_INTERFACE = "ifb-ap0"
DEFAULT_RATE = "1gbit"
DEFAULT_QUANTUM = 60000


def calculate_burst_bytes(rate_kbit: int) -> int:
    """Calculate token buffer burst in bytes: max(15000, rate_kbit * 1000 / 800)."""
    rate_bits = rate_kbit * 1000
    calculated = int(rate_bits / 800)
    return max(15000, calculated)


def derive_client_tc_ids(client_ip: str) -> Tuple[str, str, str]:
    """Derive deterministic classid, handle, and hex handle for a client IPv4.

    Args:
        client_ip: IPv4 address string (e.g. '192.168.50.20').

    Returns:
        Tuple of (classid: '1:114', qdisc_handle: '114:', hex_filter_handle: '0x114').
    """
    classid_hex = derive_classid_hex(client_ip)
    return f"1:{classid_hex}", f"{classid_hex}:", f"0x{classid_hex}"


def ensure_tc_infrastructure(
    ap_iface: str = AP_INTERFACE,
    ifb_iface: str = IFB_INTERFACE,
    runner: Optional[CommandRunner] = None,
) -> bool:
    """Initialize root HTB trees and IFB redirect if not already present.

    Args:
        ap_iface: Wireless AP interface (default 'ap0').
        ifb_iface: Virtual IFB interface (default 'ifb-ap0').
        runner: Command runner function.

    Returns:
        True if infrastructure is ready, False otherwise.
    """
    run = runner or _default_runner

    # 1. Bring up IFB interface
    run(["ip", "link", "add", ifb_iface, "type", "ifb"], 5.0)
    run(["ip", "link", "set", ifb_iface, "txqueuelen", "1000", "up"], 5.0)

    # 2. Setup root qdisc and default classes on both interfaces
    for dev in (ap_iface, ifb_iface):
        run(["tc", "qdisc", "replace", "dev", dev, "root", "handle", "1:", "htb", "default", "1fff"], 5.0)
        run(
            [
                "tc", "class", "replace", "dev", dev, "parent", "1:",
                "classid", "1:1", "htb", "rate", DEFAULT_RATE, "quantum", str(DEFAULT_QUANTUM),
            ],
            5.0,
        )
        run(
            [
                "tc", "class", "replace", "dev", dev, "parent", "1:1",
                "classid", "1:1fff", "htb", "rate", DEFAULT_RATE, "quantum", str(DEFAULT_QUANTUM),
            ],
            5.0,
        )
        run(["tc", "qdisc", "replace", "dev", dev, "parent", "1:1fff", "handle", "1fff:", "fq_codel"], 5.0)

    # 3. Setup clsact and redirect on AP interface
    run(["tc", "qdisc", "replace", "dev", ap_iface, "clsact"], 5.0)
    run(
        [
            "tc", "filter", "replace", "dev", ap_iface, "ingress", "protocol", "all",
            "pref", "1", "handle", "1", "matchall", "action", "mirred", "egress", "redirect", "dev", ifb_iface,
        ],
        5.0,
    )
    return True


def apply_bandwidth_limit(
    client_ip: str,
    download_kbit: int,
    upload_kbit: int,
    ap_iface: str = AP_INTERFACE,
    ifb_iface: str = IFB_INTERFACE,
    runner: Optional[CommandRunner] = None,
) -> Tuple[bool, str]:
    """Configure HTB classes, SFQ leaf qdiscs, and flower filters for client download/upload."""
    run = runner or _default_runner
    classid, handle, hex_handle = derive_client_tc_ids(client_ip)

    down_burst = str(calculate_burst_bytes(download_kbit))
    up_burst = str(calculate_burst_bytes(upload_kbit))

    # 1. Download shaping on ap0 (egress)
    c1, _, e1 = run(
        [
            "tc", "class", "replace", "dev", ap_iface, "parent", "1:1",
            "classid", classid, "htb", "rate", f"{download_kbit}kbit",
            "ceil", f"{download_kbit}kbit", "burst", down_burst, "cburst", down_burst,
        ],
        5.0,
    )
    if c1 != 0:
        return False, f"Failed to create download class on {ap_iface}: {e1}"

    # Add sfq leaf qdisc (sfq doesn't support 'replace', so add if absent)
    run(["tc", "qdisc", "add", "dev", ap_iface, "parent", classid, "handle", handle, "sfq", "perturb", "60"], 5.0)

    # Flower filter matching destination IP
    c2, _, e2 = run(
        [
            "tc", "filter", "replace", "dev", ap_iface, "parent", "1:", "protocol", "ip",
            "pref", "10", "handle", hex_handle, "flower", "dst_ip", client_ip, "classid", classid,
        ],
        5.0,
    )
    if c2 != 0:
        return False, f"Failed to create download filter on {ap_iface}: {e2}"

    # 2. Upload shaping on ifb-ap0 (ingress redirected)
    c3, _, e3 = run(
        [
            "tc", "class", "replace", "dev", ifb_iface, "parent", "1:1",
            "classid", classid, "htb", "rate", f"{upload_kbit}kbit",
            "ceil", f"{upload_kbit}kbit", "burst", up_burst, "cburst", up_burst,
        ],
        5.0,
    )
    if c3 != 0:
        return False, f"Failed to create upload class on {ifb_iface}: {e3}"

    run(["tc", "qdisc", "add", "dev", ifb_iface, "parent", classid, "handle", handle, "sfq", "perturb", "60"], 5.0)

    # Flower filter matching source IP
    c4, _, e4 = run(
        [
            "tc", "filter", "replace", "dev", ifb_iface, "parent", "1:", "protocol", "ip",
            "pref", "10", "handle", hex_handle, "flower", "src_ip", client_ip, "classid", classid,
        ],
        5.0,
    )
    if c4 != 0:
        return False, f"Failed to create upload filter on {ifb_iface}: {e4}"

    return True, ""


def remove_bandwidth_limit_rules(
    client_ip: str,
    ap_iface: str = AP_INTERFACE,
    ifb_iface: str = IFB_INTERFACE,
    runner: Optional[CommandRunner] = None,
) -> Tuple[bool, str]:
    """Remove flower filters and HTB classes for a client from ap0 and ifb-ap0."""
    run = runner or _default_runner
    classid, _, hex_handle = derive_client_tc_ids(client_ip)

    for dev in (ap_iface, ifb_iface):
        run(["tc", "filter", "del", "dev", dev, "parent", "1:", "protocol", "ip", "pref", "10", "handle", hex_handle, "flower"], 5.0)
        run(["tc", "class", "del", "dev", dev, "classid", classid], 5.0)

    return True, ""


# ==============================================================================
# Public limit_bandwidth & remove_bandwidth_limit Interfaces
# ==============================================================================

def limit_bandwidth(
    client: str,
    download_kbit: int,
    upload_kbit: int,
    runner: Optional[CommandRunner] = None,
    leases_file: Optional[str] = None,
    leases_map: Optional[Dict[str, str]] = None,
) -> LimitResult:
    """Limit download and upload bandwidth for a client.

    Args:
        client: Client reservation name or IPv4 address.
        download_kbit: Download ceiling in kbit/s (256 - 20000).
        upload_kbit: Upload ceiling in kbit/s (256 - 20000).
        runner: Command runner function.
        leases_file: Path to dnsmasq.leases.
        leases_map: Pre-loaded leases dictionary.

    Returns:
        LimitResult containing applied rates and verification state.
    """
    run = runner or _default_runner

    # Validate rates
    if not (MIN_BANDWIDTH_KBIT <= download_kbit <= MAX_BANDWIDTH_KBIT):
        raise ValueError(f"download_kbit must be between {MIN_BANDWIDTH_KBIT} and {MAX_BANDWIDTH_KBIT} kbit/s.")
    if not (MIN_BANDWIDTH_KBIT <= upload_kbit <= MAX_BANDWIDTH_KBIT):
        raise ValueError(f"upload_kbit must be between {MIN_BANDWIDTH_KBIT} and {MAX_BANDWIDTH_KBIT} kbit/s.")

    client_ip = validate_client_identifier(client, leases_file=leases_file, leases_map=leases_map)

    # Protect router gateway
    if client_ip == "192.168.50.1":
        raise PermissionError("Router infrastructure (192.168.50.1) cannot be rate-limited.")

    ensure_tc_infrastructure(runner=run)

    # Check idempotency
    if verify_bandwidth_applied(client_ip, download_kbit, upload_kbit, runner=run):
        return LimitResult(
            client=client,
            client_ip=client_ip,
            download_kbit=download_kbit,
            upload_kbit=upload_kbit,
            changed=False,
            verified=True,
            message=f"Bandwidth limit for '{client}' ({client_ip}) already configured at {download_kbit}k/{upload_kbit}k.",
        )

    applied, err = apply_bandwidth_limit(client_ip, download_kbit, upload_kbit, runner=run)
    if not applied:
        raise RuntimeError(f"Failed to apply bandwidth shaping rules: {err}")

    verified = verify_bandwidth_applied(client_ip, download_kbit, upload_kbit, runner=run)
    if not verified:
        remove_bandwidth_limit_rules(client_ip, runner=run)
        raise RuntimeError(
            f"Verification failed: Bandwidth class for '{client}' ({client_ip}) "
            "was not found or mismatched in kernel state; changes rolled back."
        )

    return LimitResult(
        client=client,
        client_ip=client_ip,
        download_kbit=download_kbit,
        upload_kbit=upload_kbit,
        changed=True,
        verified=True,
        message=f"Successfully applied bandwidth limits to '{client}' ({client_ip}): {download_kbit} kbit/s down, {upload_kbit} kbit/s up.",
    )


def remove_bandwidth_limit(
    client: str,
    runner: Optional[CommandRunner] = None,
    leases_file: Optional[str] = None,
    leases_map: Optional[Dict[str, str]] = None,
) -> RemoveLimitResult:
    """Remove bandwidth limits for a client, restoring unconstrained line rate."""
    run = runner or _default_runner
    client_ip = validate_client_identifier(client, leases_file=leases_file, leases_map=leases_map)

    # Check if already absent (idempotency)
    if verify_bandwidth_removed(client_ip, runner=run):
        return RemoveLimitResult(
            client=client,
            client_ip=client_ip,
            changed=False,
            verified=True,
            message=f"No bandwidth limit was active for '{client}' ({client_ip}).",
        )

    remove_bandwidth_limit_rules(client_ip, runner=run)

    verified = verify_bandwidth_removed(client_ip, runner=run)
    if not verified:
        raise RuntimeError(f"Verification failed: Bandwidth limit for '{client}' was still present in kernel state.")

    return RemoveLimitResult(
        client=client,
        client_ip=client_ip,
        changed=True,
        verified=True,
        message=f"Successfully removed bandwidth limits for '{client}' ({client_ip}); returned to unconstrained rate.",
    )
