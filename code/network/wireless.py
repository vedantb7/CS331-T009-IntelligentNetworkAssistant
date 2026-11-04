"""Wireless station control module for INA v2.

Disconnects (kick) and denies (ban) Wi-Fi stations at the 802.11 layer through
hostapd's control interface on the Raspberry Pi 5 ('ina-router').

Architectural Alignment:
- Talks to hostapd only via 'hostapd_cli -i ap0' (root-only control socket),
  executed inside the 'ina-netctl' privilege boundary.
- kick = one-shot deauthentication; the client may re-associate immediately.
- ban  = runtime deny ACL + persisted deny_mac_file; re-association is refused.
- Never touches nftables blocks or tc limits.
"""

from __future__ import annotations

import ipaddress
import os
import re
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from inav2.mcp_server.schemas import MAC_REGEX, KickResult
from inav2.network.access_control import DEFAULT_LEASES_FILE, PROTECTED_IPS
from inav2.network.device_discovery import DHCPLease, parse_dnsmasq_leases

CommandRunner = Callable[[List[str], float], Tuple[int, str, str]]

AP_INTERFACE = "ap0"
DENY_MAC_FILE = "/etc/hostapd/hostapd.deny"
VERIFY_WINDOW_S = 2.0
POLL_INTERVAL_S = 0.2

PROTECTED_MACS: Set[str] = {
    "aa:bb:cc:dd:ee:00",  # pi-gateway / ap0
    "aa:bb:cc:dd:ee:01",  # admin-laptop
    "aa:bb:cc:dd:ee:02",  # probe-laptop
}


def _default_runner(args: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
    from inav2.network.device_discovery import default_command_runner
    return default_command_runner(args, timeout)


def _hostapd(run: CommandRunner, *args: str) -> Tuple[int, str, str]:
    return run(["hostapd_cli", "-i", AP_INTERFACE, *args], 5.0)


# ==============================================================================
# Identity Resolution (name / IPv4 / MAC -> station)
# ==============================================================================

@dataclass
class Station:
    name: str
    ip: Optional[str]
    mac: str


def normalize_mac(mac: str) -> str:
    """Lower-case and validate a MAC; reject broadcast, multicast, zero and the AP's own MAC."""
    m = mac.strip().lower()
    if not re.match(MAC_REGEX, m):
        raise ValueError(f"Invalid MAC address: '{mac}'.")
    if int(m[:2], 16) & 0x01:
        raise ValueError(f"MAC '{m}' is multicast/broadcast and cannot be a station.")
    if m == "00:00:00:00:00:00":
        raise ValueError("MAC 00:00:00:00:00:00 is not a valid station.")
    try:
        with open(f"/sys/class/net/{AP_INTERFACE}/address", "r", encoding="utf-8") as f:
            if f.read().strip().lower() == m:
                raise ValueError("Refusing to act on the access point's own MAC address.")
    except OSError:
        pass
    return m


def _load_leases(leases_file: Optional[str]) -> List[DHCPLease]:
    path = leases_file or DEFAULT_LEASES_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            return parse_dnsmasq_leases(f.read())
    except OSError:
        return []


def resolve_station(
    identifier: str,
    runner: Optional[CommandRunner] = None,
    leases: Optional[List[DHCPLease]] = None,
    leases_file: Optional[str] = None,
    state_bans: Optional[List[Dict[str, Any]]] = None,
    leases_map: Optional[Dict[str, str]] = None,
) -> Station:
    """Resolve a reservation name, managed IPv4 or MAC into a Station (MAC is mandatory)."""
    run = runner or _default_runner
    ident = identifier.strip().lower()

    # Check state bans first (unban often references a device with no active lease)
    if state_bans:
        for b in state_bans:
            b_name = (b.get("client") or "").lower()
            b_ip = (b.get("client_ip") or "").lower()
            b_mac = (b.get("mac") or "").lower()
            if ident in (b_name, b_ip, b_mac):
                return Station(
                    name=b.get("client") or b_mac,
                    ip=b.get("client_ip"),
                    mac=normalize_mac(b_mac),
                )

    records = leases if leases is not None else _load_leases(leases_file)
    if not records and leases_map:
        records = [
            DHCPLease(
                expiry_epoch=0,
                mac_address=_mac_from_neighbor(ip, run) or f"02:00:00:00:00:{int(ip.split('.')[-1]):02x}",
                ip_address=ip,
                hostname=name,
            )
            for name, ip in leases_map.items()
        ]

    if re.match(MAC_REGEX, ident):
        mac = normalize_mac(ident)
        lease = next((l for l in records if (l.mac_address or "").lower() == mac), None)
        return Station(
            name=(lease.hostname if lease and lease.hostname else mac),
            ip=lease.ip_address if lease else None,
            mac=mac,
        )

    try:
        ip = str(ipaddress.IPv4Address(ident))
        if ip in PROTECTED_IPS:
            return Station(name=ip, ip=ip, mac=normalize_mac("aa:bb:cc:dd:ee:00"))
        lease = next((l for l in records if l.ip_address == ip), None)
        mac = lease.mac_address if lease else _mac_from_neighbor(ip, run)
        if not mac:
            raise ValueError(f"No MAC known for {ip} (no lease and no neighbor entry).")
        return Station(
            name=(lease.hostname if lease and lease.hostname else ip),
            ip=ip,
            mac=normalize_mac(mac),
        )
    except ipaddress.AddressValueError:
        pass

    lease = next((l for l in records if (l.hostname or "").lower() == ident), None)
    if lease is None and leases is None and not leases_map:
        # Registered in devices.yaml: identify by its MAC, even if the lease shows
        # another hostname or the device is offline (it can still be banned).
        from inav2.network.devices import device_mac

        mac = device_mac(ident)
        if mac:
            by_mac = next((l for l in records if (l.mac_address or "").lower() == mac.lower()), None)
            return Station(name=ident, ip=by_mac.ip_address if by_mac else None, mac=normalize_mac(mac))
    if lease is None:
        known = ", ".join(sorted({l.hostname for l in records if l.hostname})) or "none"
        raise ValueError(f"Unknown client '{identifier}'. Known clients: {known}.")
    return Station(name=ident, ip=lease.ip_address, mac=normalize_mac(lease.mac_address))


def _mac_from_neighbor(ip: str, run: CommandRunner) -> Optional[str]:
    code, out, _ = run(["ip", "neigh", "show", "dev", AP_INTERFACE, ip], 5.0)
    m = re.search(r"lladdr\s+([0-9a-f:]{17})", out.lower()) if code == 0 else None
    return m.group(1) if m else None


def load_protected_macs(rules_path: Optional[str] = None) -> Set[str]:
    """Load protected MAC addresses from rules.yaml or built-in defaults."""
    macs = set(PROTECTED_MACS)
    path = rules_path or os.path.join(os.path.dirname(os.path.dirname(__file__)), "policy", "rules.yaml")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            # Simple line parsing if yaml library is not present, or yaml.safe_load
            try:
                import yaml
                data = yaml.safe_load(content)
                if isinstance(data, dict):
                    for m in data.get("protected_macs", []):
                        macs.add(m.strip().lower())
            except ImportError:
                for line in content.splitlines():
                    m = re.search(r'["\']([0-9a-fA-F:]{17})["\']', line)
                    if m:
                        macs.add(m.group(1).lower())
        except OSError:
            pass
    return macs


def ensure_not_protected(station: Station, protected_macs: Set[str]) -> None:
    if (station.ip and station.ip in PROTECTED_IPS) or station.mac in protected_macs:
        raise PermissionError(
            f"Router infrastructure / protected device '{station.name}' cannot be kicked or banned."
        )


# ==============================================================================
# hostapd Control Operations
# ==============================================================================

def get_station_info(mac: str, runner: Optional[CommandRunner] = None) -> Optional[Dict[str, str]]:
    """Return hostapd's per-station MIB (key=value) or None if the station is not associated."""
    run = runner or _default_runner
    code, out, err = _hostapd(run, "sta", mac)
    if code != 0:
        raise RuntimeError("hostapd control interface not available on ap0 (is ctrl_interface set?).")
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    if not lines or lines[0].upper() == "FAIL" or lines[0].lower() != mac.lower():
        return None
    info: Dict[str, str] = {}
    for line in lines[1:]:
        if "=" in line:
            k, v = line.split("=", 1)
            info[k] = v
    return info


def deauthenticate(mac: str, runner: Optional[CommandRunner] = None) -> Tuple[bool, str]:
    run = runner or _default_runner
    code, out, err = _hostapd(run, "deauthenticate", mac)
    if code != 0 or out.strip() != "OK":
        return False, (err or out).strip() or f"hostapd_cli exited with code {code}"
    return True, ""


def deny_acl_show(runner: Optional[CommandRunner] = None) -> Set[str]:
    """Parse 'deny_acl SHOW' (lines like 'aa:bb:cc:dd:ee:ff VLAN_ID=0')."""
    run = runner or _default_runner
    code, out, _ = _hostapd(run, "deny_acl", "SHOW")
    if code != 0:
        raise RuntimeError("hostapd control interface not available on ap0 (is ctrl_interface set?).")
    return {l.split()[0].lower() for l in out.splitlines() if l.strip() and ":" in l.split()[0]}


def deny_acl_add(mac: str, runner: Optional[CommandRunner] = None) -> Tuple[bool, str]:
    run = runner or _default_runner
    code, out, err = _hostapd(run, "deny_acl", "ADD_MAC", mac)
    return (code == 0 and out.strip() == "OK"), (err or out).strip()


def deny_acl_del(mac: str, runner: Optional[CommandRunner] = None) -> Tuple[bool, str]:
    run = runner or _default_runner
    code, out, err = _hostapd(run, "deny_acl", "DEL_MAC", mac)
    return (code == 0 and out.strip() == "OK"), (err or out).strip()


# ==============================================================================
# Persistent Deny File (atomic rewrite, same approach as save_state)
# ==============================================================================

def read_deny_file(path: str = DENY_MAC_FILE) -> Set[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {l.split()[0].lower() for l in f if l.strip() and not l.lstrip().startswith("#")}
    except OSError:
        return set()


def write_deny_file(macs: Set[str], path: str = DENY_MAC_FILE) -> None:
    dir_name = os.path.dirname(os.path.abspath(path))
    os.makedirs(dir_name, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
        tf.write("# Managed by INA (ina-netctl). Manual edits are overwritten.\n")
        for mac in sorted(macs):
            tf.write(f"{mac}\n")
        tf.flush()
        os.fsync(tf.fileno())
        temp_name = tf.name
    try:
        os.chmod(temp_name, 0o600)
    except OSError:
        pass
    os.replace(temp_name, path)


def _wait_until(
    pred: Callable[[], bool],
    sleeper: Callable[[float], None] = time.sleep,
    window_s: float = VERIFY_WINDOW_S,
    poll_s: float = POLL_INTERVAL_S,
) -> bool:
    waited = 0.0
    while waited <= window_s:
        if pred():
            return True
        sleeper(poll_s)
        waited += poll_s
    return False


# ==============================================================================
# Kick and Ban Operations
# ==============================================================================

def kick_station(
    station: Station,
    runner: Optional[CommandRunner] = None,
    sleeper: Callable[[float], None] = time.sleep,
    window_s: float = VERIFY_WINDOW_S,
) -> KickResult:
    """Deauthenticate once and verify the old association is gone (fresh one allowed)."""
    run = runner or _default_runner

    before = get_station_info(station.mac, run)
    if before is None:
        return KickResult(
            client=station.name,
            client_ip=station.ip,
            mac=station.mac,
            was_associated=False,
            kicked=False,
            reconnected=None,
            changed=False,
            verified=True,
            message=f"'{station.name}' is not currently associated with ap0; nothing to disconnect.",
        )
    before_ct = int(before.get("connected_time", "0") or 0)

    ok, err = deauthenticate(station.mac, run)
    if not ok:
        raise RuntimeError(f"hostapd rejected deauthentication of {station.mac}: {err}")

    # Verified = station absent, OR present with a smaller connected_time (a new session).
    waited = 0.0
    reconnected = None
    verified = False
    while waited <= window_s:
        info = get_station_info(station.mac, run)
        if info is None:
            verified, reconnected = True, False
        elif int(info.get("connected_time", "0") or 0) < before_ct:
            verified, reconnected = True, True
            break
        sleeper(POLL_INTERVAL_S)
        waited += POLL_INTERVAL_S

    if not verified:
        raise RuntimeError(
            f"Verification failed: {station.mac} still shows the same association after deauthentication."
        )

    msg = f"Deauthenticated '{station.name}' ({station.mac})."
    if reconnected:
        msg += " It re-associated; use ban_client to keep it off."

    return KickResult(
        client=station.name,
        client_ip=station.ip,
        mac=station.mac,
        was_associated=True,
        kicked=True,
        reconnected=reconnected,
        changed=True,
        verified=True,
        message=msg,
    )


def ban_station(
    station: Station,
    runner: Optional[CommandRunner] = None,
    deny_file: str = DENY_MAC_FILE,
    sleeper: Callable[[float], None] = time.sleep,
) -> Tuple[bool, bool]:
    """Apply a ban. Returns (changed, verified). Rolls back on any failure."""
    run = runner or _default_runner

    runtime = deny_acl_show(run)
    on_disk = read_deny_file(deny_file)
    if station.mac in runtime and station.mac in on_disk and get_station_info(station.mac, run) is None:
        return False, True  # idempotent no-op

    ok, err = deny_acl_add(station.mac, run)  # 1. runtime ACL (hostapd also disassociates)
    if not ok:
        raise RuntimeError(f"hostapd deny_acl ADD_MAC failed: {err}")
    try:
        write_deny_file(on_disk | {station.mac}, deny_file)  # 2. persistence
    except OSError as e:
        deny_acl_del(station.mac, run)  # rollback 1
        raise RuntimeError(f"Could not write {deny_file}: {e}; runtime ban rolled back.") from e

    if get_station_info(station.mac, run) is not None:  # 3. belt-and-braces deauth
        deauthenticate(station.mac, run)

    verified = (
        _wait_until(lambda: get_station_info(station.mac, run) is None, sleeper)
        and station.mac in deny_acl_show(run)
        and station.mac in read_deny_file(deny_file)
    )
    if not verified:  # 4. full rollback
        deny_acl_del(station.mac, run)
        try:
            write_deny_file(read_deny_file(deny_file) - {station.mac}, deny_file)
        except OSError:
            pass
        raise RuntimeError(f"Verification failed for ban of {station.mac}; ban rolled back.")
    return True, True


def unban_station(
    mac: str,
    runner: Optional[CommandRunner] = None,
    deny_file: str = DENY_MAC_FILE,
) -> Tuple[bool, bool]:
    """Remove a ban. Returns (changed, verified). Idempotent."""
    run = runner or _default_runner
    m = mac.lower()
    present = m in deny_acl_show(run) or m in read_deny_file(deny_file)
    if not present:
        return False, True
    deny_acl_del(m, run)  # 'FAIL' when already absent at runtime is fine
    write_deny_file(read_deny_file(deny_file) - {m}, deny_file)
    verified = m not in deny_acl_show(run) and m not in read_deny_file(deny_file)
    if not verified:
        raise RuntimeError(f"Verification failed: {m} is still denied.")
    return True, True
