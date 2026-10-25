# INA v2 Setup & Operational Guide

## Overview

This document provides setup instructions, prerequisite details, and operational guidance for **Intelligent Network Configuration Assistant v2 (INA v2)**.

> **Current Implementation Status:** Milestone 2 — Safe Access Point Management Implemented.
> Read-only host network discovery and safe NetworkManager AP profile management are operational. Packet forwarding, firewall rules, and policy enforcement are pending future milestones.

---

## Prerequisites

- **Operating System:** Linux (Fedora 38+ recommended for development; Raspberry Pi OS planned for edge deployment).
- **Python:** Python 3.11+ (standard library only; no external package installations required).
- **Host Network Utilities:**
  - `iproute2` (`/usr/bin/ip`): Used for querying interfaces, links, and routing tables.
  - `iw` (`/usr/bin/iw`): Used for wireless interface detection and driver AP capability verification.
  - `NetworkManager` (`/usr/bin/nmcli`): Used for managing wireless connection profiles and shared IPv4 mode.

---

## Running the Read-Only Network Discovery Report

The network discovery module inspects the host network without making any changes to interfaces, routing, or firewall configuration.

### Text Summary (Standard CLI)

To print a human-readable diagnostic report:

```bash
python3 -m network.device_discovery
```

### JSON Export

To export the raw structured data model as JSON for integration or automated parsing:

```bash
python3 -m network.device_discovery --json
```

---

## Illustrative Output (Example)

> **Note:** The following output is illustrative. Actual output will reflect your machine's physical and virtual network adapters.

```text
==============================================================================
INA v2 — Host Network Discovery Report
Timestamp: 2026-10-25T14:14:12Z | Platform: Linux 6.11.4-200.fc40.x86_64 (x86_64)
==============================================================================

[ OBSERVED FACTS ]

1. Detected Network Interfaces:
   • lo:
       State: UNKNOWN | Type: loopback   | Link: loopback | MAC: 00:00:00:00:00:00
       Flags: LOOPBACK, UP, LOWER_UP
       IPV4: 127.0.0.1/8 (scope: host)
       IPV6: ::1/128 (scope: host)
   • enp7s0:
       State: DOWN    | Type: ethernet   | Link: ether    | MAC: 40:c2:ba:45:b3:20
       Flags: BROADCAST, MULTICAST
       IP Addresses: (none assigned)
   • wlp8s0:
       State: UP      | Type: wireless   | Link: ether    | MAC: 02:b5:eb:ed:f5:75
       Flags: BROADCAST, MULTICAST, UP, LOWER_UP
       IPV4: 192.168.1.150/24 (scope: global)
       Wi-Fi Details: Wireless device | AP Mode Advertised: Yes (Driver advertises AP mode support)
   • wg0:
       State: UNKNOWN | Type: tunnel     | Link: none     | MAC: (none)
       Flags: POINTOPOINT, UP, LOWER_UP
       IPV4: 10.8.0.2/24 (scope: global)
       Tunnel Details: wireguard (WireGuard VPN tunnel)

2. Routing Table Entries:
   Observed 3 route(s), including 1 default route(s):
   • default via 192.168.1.1 dev wlp8s0 metric 600 proto dhcp [DEFAULT]
   • 192.168.1.0/24 dev wlp8s0 proto kernel metric 600
   • 10.8.0.0/24 dev wg0 proto kernel

------------------------------------------------------------------------------
[ INFERRED CANDIDATES & ANALYSIS ]
* Note: The following items are heuristics inferred from the observed facts above.

1. Uplink Candidate(s):
   • wlp8s0 (PRIMARY): via 192.168.1.1 (metric: 600) [UP]
     Analysis: Lowest metric default route; primary candidate for host outbound connectivity.

2. Access Point (AP) Candidate(s):
   • wlp8s0 [AP Capable]:
     Analysis: Driver explicitly advertises AP mode support. Currently acting as primary uplink; configuring AP here requires virtual AP (VIF) support or a secondary Wi-Fi adapter.

3. Tunnel / Routing-Impacting Interfaces:
   • wg0 (type: wireguard):
     Analysis: WireGuard VPN tunnel. Has active routes in kernel table; may divert or encapsulate subnet traffic.

------------------------------------------------------------------------------
[ WARNINGS & DIAGNOSTICS ]
   (No warnings encountered)
==============================================================================
```

---

## Access Point Management (`network.access_point`)

The access point management module enables configuring and managing a dedicated test network Wi-Fi hotspot through NetworkManager (`nmcli`).

### Configuration Parameters

| Parameter | Default | Validation & Description |
| :--- | :--- | :--- |
| `--ssid` | `INA-Test-Network` | 1-32 bytes UTF-8 string without control characters. |
| `--password` | *(Required)* | 8-63 ASCII characters (or 64 hex characters) for WPA2-Personal PSK. |
| `--interface`| *(Auto-resolved)* | Target Wi-Fi interface name (`^[a-zA-Z0-9_-]+$`). If omitted, resolved via precedence: CLI > `config/settings.yaml` > `NETWORK_INTERFACE` env var > unambiguous non-uplink discovery. |
| `--subnet` | `192.168.10.1/24` | Private IPv4 CIDR (/16 to /29). Must not collide with host routes. |
| `--channel` | `None` (auto) | Wi-Fi channel (1-14 for 2.4GHz; 36-165 for 5GHz). |
| `--band` | `bg` (2.4GHz) | Wireless band: `bg` (2.4GHz) or `a` (5GHz). |
| `--profile` | `ina-ap` | Profile name. **Must** start with `ina-` to isolate it from host profiles. |

### Inspecting Status & Pre-Flight Safety

Run a pre-flight inspection to verify whether an INA profile exists and whether the current host network conditions permit safe activation:

```bash
python3 -m network.access_point status
```

### Creating the AP Profile

By default, all CLI operations run in **dry-run mode**, printing planned `nmcli` commands without modifying system state. Pass `--no-dry-run` to execute:

```bash
# Dry-run preview (safe, no changes)
python3 -m network.access_point create --ssid INA-Test-Network --password YourSecretPassphrase123

# Live creation
python3 -m network.access_point create --ssid INA-Test-Network --password YourSecretPassphrase123 --no-dry-run
```

### Activation Safety & Uplink Guards

Activation changes the wireless interface state. To prevent accidental disconnection of remote or SSH sessions:
- The command requires `--no-dry-run` **and** explicit `--confirm`.
- If the target interface currently hosts the active internet uplink (e.g. `wlp8s0` connected to `IITGN-SSO`), activation is **strictly refused**:

```text
Activation REFUSED: Active uplink conflict detected. Interface 'wlp8s0' currently carries
an active host default route via connection 'IITGN-SSO'. Activating an AP here would sever
host connectivity. Use a secondary Wi-Fi adapter or Ethernet uplink.
```

To activate on a verified, non-conflicting interface:

```bash
python3 -m network.access_point activate --profile ina-ap --no-dry-run --confirm
```

### Deactivation and Safe Cleanup

Cleanup operations only touch profiles in the `ina-*` namespace:

```bash
# Deactivate the AP profile
python3 -m network.access_point deactivate --profile ina-ap --no-dry-run

# Remove the profile entirely from NetworkManager
python3 -m network.access_point delete --profile ina-ap --no-dry-run
```

### Recovery Guidance

If an interface was disconnected or an uplink profile disrupted:
1. Re-activate the primary host Wi-Fi connection:
   ```bash
   nmcli connection up IITGN-SSO
   ```
2. Verify default routing:
   ```bash
   python3 -m network.device_discovery
   ```

---

## Running the Unit Tests

The test suite uses Python's built-in `unittest` module and does not require third-party packages or root privileges. All external commands and system files are mocked.

To run the full unit test suite:

```bash
python3 -m unittest discover tests/unit -v
```

### Covered Test Scenarios:
- **Device Discovery:**
  - Full network discovery and parsing of multiple interfaces (loopback, Ethernet, Wi-Fi, WireGuard, TUN/TAP, SIT).
  - Missing command handling (`ip` or `iw` not installed).
  - Corrupted or invalid JSON command output handling.
  - Host environments with no default route.
  - Interfaces without assigned IP addresses and inactive links.
  - Subprocess timeout and permission error handling.
  - Tri-state Wi-Fi AP capability validation (`True`, `False`, and `None` for unavailable).
  - Data model serialization (`to_dict()`) and text formatting.
- **Access Point Management:**
  - Comprehensive configuration validation (SSID, WPA2 passphrase, CIDR subnet, interface, channels).
  - Dry-run verification and credential masking in commands and representations.
  - Profile inspection and distinction between INA-managed and unmanaged host profiles.
  - Strict protection against modifying or deleting unrelated profiles (e.g. `IITGN-SSO`, `CloudflareWARP`).
  - Pre-flight detection of active uplink conflicts and single-radio simultaneous operation risks.
  - Refusal to activate without explicit operator confirmation (`confirm=True`).
  - Command execution failure and timeout handling.
  - Post-activation verification failure detection when NetworkManager does not transition to active.
  - Safe deactivation and cleanup restricted to `ina-*` profiles.

---

## Known Limitations & Planned Capabilities

- **Milestone 2 Scope:** Access point management handles profile declaration and activation via NetworkManager. It does **not** install custom packet filtering rules, configure client blocking, manipulate `/proc/sys/net/ipv4/ip_forward` directly, or configure DNS policies.
- **Single-Radio Hardware Constraints:** Laptops with a single physical Wi-Fi adapter (e.g. `wlp8s0`) cannot maintain an upstream client connection and an AP simultaneously across different channels. Attempting to activate an AP on the uplink Wi-Fi interface is refused by safety checks. A secondary USB Wi-Fi adapter or wired Ethernet connection is recommended.
- **Wi-Fi Detection:** Querying wireless capabilities requires the `iw` utility. If `iw` is not installed, wireless interfaces will still be detected via sysfs, but AP mode capability will be reported as `Unavailable` (`None`).
