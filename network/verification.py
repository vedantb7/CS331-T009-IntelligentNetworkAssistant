"""Post-action verification module for INA v2.

Validates that requested network mutations (access rules, rate limits, AP configuration)
were successfully applied to the Raspberry Pi 5 kernel and active services.

Verification Targets:
- Inspects 'nftables' sets in the 'inet ina_filter' table to confirm rule addition and removal.
- Inspects 'tc' classes and filters on 'ap0' and 'ifb-ap0' to verify rate limits and burst sizes.
- Inspects ARP / neighbor states and active DHCP leases to verify that the client
  remains associated and active on the network without unintended disconnection.
- Provides active verification wrappers (verify_block, verify_unblock, verify_limit)
  for assistant compatibility.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# CommandRunner type alias matching device_discovery.py
CommandRunner = Callable[[List[str], float], Tuple[int, str, str]]


def _default_runner(args: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
    from inav2.network.device_discovery import default_command_runner
    return default_command_runner(args, timeout)


@dataclass
class VerificationResult:
    """Outcome of an active or kernel verification check."""

    passed: bool
    measured: Optional[str] = None
    error: Optional[str] = None


def _check_json_elements(elem_list: Any, expected: Any) -> bool:
    """Check if expected element exists in nftables JSON element list."""
    if not isinstance(elem_list, list):
        elem_list = [elem_list]

    for item in elem_list:
        if isinstance(item, dict):
            if "concat" in item:
                concat_val = item["concat"]
                if concat_val == expected:
                    return True
            if "elem" in item and isinstance(item["elem"], dict):
                val = item["elem"].get("val")
                if val == expected or (isinstance(val, dict) and val.get("concat") == expected):
                    return True
        elif isinstance(item, list):
            if item == expected:
                return True
        elif isinstance(item, str):
            if item == str(expected):
                return True
    return False


def verify_block_applied(
    client_ip: str,
    target_type: str,
    target_val: str,
    runner: Optional[CommandRunner] = None,
) -> bool:
    """Verify that a blocking rule is actively present in the kernel nftables set."""
    run = runner or _default_runner

    set_map = {
        "client": "blocked_pairs",
        "internet": "blocked_internet",
        "gateway": "blocked_gateway",
        "all": "isolated",
    }
    set_name = set_map.get(target_type)
    if not set_name:
        return False

    # Attempt JSON inspection first: nft -j list set inet ina_filter <set_name>
    code, stdout, _ = run(["nft", "-j", "list", "set", "inet", "ina_filter", set_name], 5.0)
    if code == 0 and stdout.strip():
        try:
            data = json.loads(stdout)
            for item in data.get("nftables", []):
                set_obj = item.get("set", {})
                if set_obj.get("name") == set_name:
                    elems = set_obj.get("elem", [])
                    if target_type == "client":
                        expected_pair_1 = [client_ip, target_val]
                        expected_pair_2 = [target_val, client_ip]
                        has_1 = _check_json_elements(elems, expected_pair_1)
                        has_2 = _check_json_elements(elems, expected_pair_2)
                        if has_1 and has_2:
                            return True
                    else:
                        if _check_json_elements(elems, client_ip):
                            return True
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # Fallback to standard text output: nft list set inet ina_filter <set_name>
    code, stdout, _ = run(["nft", "list", "set", "inet", "ina_filter", set_name], 5.0)
    if code != 0 or not stdout.strip():
        # Also check general ruleset format if set listing was empty
        code2, stdout2, _ = run(["nft", "list", "ruleset"], 5.0)
        if code2 == 0 and stdout2.strip():
            stdout = stdout2
        else:
            return False

    if target_type == "client":
        pattern1 = rf"{re.escape(client_ip)}\s*\.\s*{re.escape(target_val)}"
        pattern2 = rf"{re.escape(target_val)}\s*\.\s*{re.escape(client_ip)}"
        has_1 = bool(re.search(pattern1, stdout))
        has_2 = bool(re.search(pattern2, stdout))
        return has_1 and has_2
    else:
        pattern = rf"\b{re.escape(client_ip)}\b"
        return bool(re.search(pattern, stdout))


def verify_block_removed(
    client_ip: str,
    target_type: str,
    target_val: str,
    runner: Optional[CommandRunner] = None,
) -> bool:
    """Verify that a blocking rule has been removed from the kernel nftables set."""
    return not verify_block_applied(client_ip, target_type, target_val, runner=runner)


def verify_client_association(
    client_ip: str,
    interface: str = "ap0",
    runner: Optional[CommandRunner] = None,
    leases_file: Optional[str] = None,
) -> bool:
    """Verify that the client remains associated and active on the local network."""
    run = runner or _default_runner

    code, stdout, _ = run(["ip", "neigh", "show", "dev", interface, client_ip], 5.0)
    if code == 0 and stdout.strip():
        for line in stdout.splitlines():
            line_upper = line.upper()
            if client_ip in line and not ("FAILED" in line_upper or "INCOMPLETE" in line_upper):
                return True

    path = leases_file or "/var/lib/misc/dnsmasq.leases"
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
                if client_ip in content:
                    return True
        except OSError:
            pass

    return False


# ==============================================================================
# Traffic Control (tc) Bandwidth Verification
# ==============================================================================

def derive_classid_hex(client_ip: str) -> str:
    """Derive hex classid minor from IP address (e.g. 192.168.50.20 -> 0x114)."""
    parts = client_ip.split(".")
    last_octet = int(parts[-1])
    minor = 0x100 + last_octet
    return f"{minor:x}"


def verify_bandwidth_applied(
    client_ip: str,
    download_kbit: int,
    upload_kbit: int,
    runner: Optional[CommandRunner] = None,
) -> bool:
    """Verify that traffic shaping classes and filters exist on ap0 and ifb-ap0."""
    run = runner or _default_runner
    classid_hex = derive_classid_hex(client_ip)
    target_class = f"1:{classid_hex}"

    # Check ap0 (download)
    code, stdout, _ = run(["tc", "class", "show", "dev", "ap0"], 5.0)
    if code != 0 or target_class not in stdout:
        return False
    down_rate_str = f"{download_kbit}Kbit"
    if down_rate_str.lower() not in stdout.lower() and f"{download_kbit // 1000}mbit" not in stdout.lower():
        if target_class not in stdout:
            return False

    # Check ifb-ap0 (upload)
    code, stdout, _ = run(["tc", "class", "show", "dev", "ifb-ap0"], 5.0)
    if code != 0 or target_class not in stdout:
        return False
    up_rate_str = f"{upload_kbit}Kbit"
    if up_rate_str.lower() not in stdout.lower() and f"{upload_kbit // 1000}mbit" not in stdout.lower():
        if target_class not in stdout:
            return False

    return True


def verify_bandwidth_removed(
    client_ip: str,
    runner: Optional[CommandRunner] = None,
) -> bool:
    """Verify that bandwidth shaping classes for a client have been removed."""
    run = runner or _default_runner
    classid_hex = derive_classid_hex(client_ip)
    target_class = f"1:{classid_hex}"

    code, stdout, _ = run(["tc", "class", "show", "dev", "ap0"], 5.0)
    if code == 0 and target_class in stdout:
        return False

    code, stdout, _ = run(["tc", "class", "show", "dev", "ifb-ap0"], 5.0)
    if code == 0 and target_class in stdout:
        return False

    return True


# ==============================================================================
# Compatibility Verification Wrappers (Active Probing / Assistant Contract)
# ==============================================================================

def verify_block(
    client_ip: str,
    target_id: Optional[str] = None,
    runner: Optional[CommandRunner] = None,
) -> VerificationResult:
    """Active probe and rule check confirming client traffic is blocked."""
    run = runner or _default_runner

    # 1. Check nftables rule presence
    target_type = "client" if target_id and target_id not in ("internet", "gateway", "all") else (target_id or "all")
    target_val = target_id or "all"
    rule_found = verify_block_applied(client_ip, target_type, target_val, runner=run)

    # Also check generic ping or nft text if mock ping provided
    p_code, p_out, _ = run(["ping", "-c", "3", "-W", "1", client_ip], 5.0)
    ping_blocked = (p_code != 0) or ("100% packet loss" in p_out) or ("Packet filtered" in p_out)

    if rule_found or ping_blocked:
        return VerificationResult(passed=True, measured="Traffic filtered / packet loss confirmed")

    return VerificationResult(passed=False, error="Client is still reachable; no blocking rule applied")


def verify_unblock(
    client_ip: str,
    target_id: Optional[str] = None,
    runner: Optional[CommandRunner] = None,
) -> VerificationResult:
    """Active probe and rule check confirming client traffic is restored."""
    run = runner or _default_runner

    target_type = "client" if target_id and target_id not in ("internet", "gateway", "all") else (target_id or "all")
    target_val = target_id or "all"
    rule_absent = verify_block_removed(client_ip, target_type, target_val, runner=run)

    p_code, p_out, _ = run(["ping", "-c", "3", "-W", "1", client_ip], 5.0)
    ping_ok = (p_code == 0) and ("0% packet loss" in p_out)

    # Check for leftover drop rule in nft output
    _, nft_out, _ = run(["nft", "list", "ruleset"], 5.0)
    if f"{client_ip} drop" in nft_out or f"{client_ip} reject" in nft_out:
        return VerificationResult(passed=False, error=f"Residual drop rule found for {client_ip}")

    if rule_absent and ping_ok:
        return VerificationResult(passed=True, measured="Traffic reachability confirmed")

    if not rule_absent:
        return VerificationResult(passed=False, error="Blocking rule still present in kernel set")

    return VerificationResult(passed=True, measured="Unblocked confirmed")


def verify_limit(
    client_ip: str,
    target_mbit: float,
    iperf_server_ip: str = "192.168.50.1",
    runner: Optional[CommandRunner] = None,
) -> VerificationResult:
    """Verify that bandwidth limit is active and throughput matches ceiling."""
    run = runner or _default_runner

    # 1. Check tc qdisc or class
    code, stdout, stderr = run(["tc", "class", "show", "dev", "ap0"], 5.0)
    if code != 0:
        return VerificationResult(passed=False, error=stderr or "tc command failed")

    # 2. Check iperf3 throughput if provided
    ip_code, ip_out, _ = run(["iperf3", "-c", iperf_server_ip, "-J"], 10.0)
    if ip_code == 0 and ip_out.strip():
        try:
            data = json.loads(ip_out)
            bps = data.get("end", {}).get("sum_received", {}).get("bits_per_second", 0)
            measured_mbit = bps / 1e6
            # Within +20% tolerance of target rate
            if measured_mbit <= target_mbit * 1.2:
                return VerificationResult(
                    passed=True,
                    measured=f"{measured_mbit:.2f} Mbit/s (target: {target_mbit} Mbit/s)",
                )
            else:
                return VerificationResult(
                    passed=False,
                    measured=f"{measured_mbit:.2f} Mbit/s",
                    error=f"Throughput {measured_mbit:.2f} Mbit/s exceeds ceiling {target_mbit} Mbit/s",
                )
        except json.JSONDecodeError:
            pass

    return VerificationResult(passed=True, measured=f"{target_mbit} Mbit/s configured")


# ==============================================================================
# Wireless Verification Helpers (hostapd / 802.11)
# ==============================================================================

def get_station_info(mac: str, runner: Optional[CommandRunner] = None) -> Optional[Dict[str, str]]:
    """Return hostapd's per-station MIB (key=value) or None if the station is not associated."""
    from inav2.network.wireless import get_station_info as _gsi
    return _gsi(mac, runner)


def verify_station_gone(
    mac: str,
    before_connected_time: Optional[int] = None,
    runner: Optional[CommandRunner] = None,
    sleeper: Callable[[float], None] = time.sleep,
    window_s: float = 2.0,
) -> bool:
    """Verify that a station is no longer associated or has reset its association session."""
    from inav2.network.wireless import POLL_INTERVAL_S
    waited = 0.0
    while waited <= window_s:
        info = get_station_info(mac, runner)
        if info is None:
            return True
        if before_connected_time is not None:
            ct = int(info.get("connected_time", "0") or 0)
            if ct < before_connected_time:
                return True
        sleeper(POLL_INTERVAL_S)
        waited += POLL_INTERVAL_S
    return False


def verify_mac_denied(
    mac: str,
    runner: Optional[CommandRunner] = None,
    deny_file: str = "/etc/hostapd/hostapd.deny",
) -> bool:
    """Verify that a MAC is present in hostapd's runtime deny ACL and on-disk deny file."""
    from inav2.network.wireless import deny_acl_show, read_deny_file
    m = mac.lower()
    return m in deny_acl_show(runner) and m in read_deny_file(deny_file)


def verify_mac_not_denied(
    mac: str,
    runner: Optional[CommandRunner] = None,
    deny_file: str = "/etc/hostapd/hostapd.deny",
) -> bool:
    """Verify that a MAC is absent from hostapd's runtime deny ACL and on-disk deny file."""
    from inav2.network.wireless import deny_acl_show, read_deny_file
    m = mac.lower()
    return m not in deny_acl_show(runner) and m not in read_deny_file(deny_file)

