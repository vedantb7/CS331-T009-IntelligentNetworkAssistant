# INA v2 Implementation & Integration Status Matrix

This document provides an objective, code- and evidence-backed assessment of all components in the **INA v2** repository.

Every component is strictly classified into one of five verified lifecycle states:
- **Implemented & Verified:** Deployed on hardware and validated via live tests.
- **Implemented but Not Fully Verified:** Source code is complete, but edge-case or multi-client validation is ongoing.
- **Partially Implemented:** Functional module exists, but features are unintegrated or awaiting future milestones.
- **Planned / Not Integrated:** Architecture defined; files exist as minimal skeletons or docstrings.
- **Obsolete & Removed:** Legacy components tied to obsolete architectures (e.g., laptop-as-router) cleaned up.

---

## 1. System Component Status Matrix

| Component | Target Location / Module | Verification Host | Classification | Ground Truth Evidence & Test References |
| :--- | :--- | :--- | :--- | :--- |
| **1. Raspberry Pi 5 AP** | `hostapd`, `/etc/hostapd/hostapd.conf` | Raspberry Pi 5 | **Implemented & Verified** | Broadcasts SSID `INA-Router` on Channel 11 via virtual interface `ap0`. Tested with Fedora client association. |
| **2. Upstream Wi-Fi Uplink** | `NetworkManager`, `wlan0` | Raspberry Pi 5 | **Implemented & Verified** | Connects `wlan0` in STA mode to phone hotspot (`1+ Four`). Verified via `ip route show default` and external ping. |
| **3. DHCP & DNS Server** | `dnsmasq`, `/etc/dnsmasq.d/ina-ap.conf` | Raspberry Pi 5 | **Implemented & Verified** | Dynamically leases `192.168.50.10–100` (12h lease). Verified lease entry `192.168.50.46` in `dnsmasq.leases`. |
| **4. IPv4 Forwarding & NAT** | Kernel sysctl, `nftables` | Raspberry Pi 5 | **Implemented & Verified** | Persistent forwarding (`net.ipv4.ip_forward = 1`); `table ip ina_nat` masquerades `192.168.50.0/24` out `wlan0`. |
| **5. Reboot Persistence** | Systemd units & overrides | Raspberry Pi 5 | **Implemented & Verified** | All 4 units (`ina-ap-interface`, `hostapd`, `dnsmasq`, `nftables`) active post-reboot. AP reappears automatically. |
| **6. Device Discovery** | `network/device_discovery.py` | Pi & Workstation | **Implemented & Verified** | Discovers host NICs, routes, Wi-Fi driver AP modes, and parses client ARP neighbors and DHCP leases. Unit tested. |
| **7. Router State Inspector** | `network/access_point.py` | Pi & Workstation | **Implemented & Verified** | Models `APConfig`, validates settings, and inspects live systemd/nftables state. 27 unit tests pass. |
| **8. Access Control & Blocking** | `network/access_control.py` | Raspberry Pi 5 | **Planned / Not Integrated** | Minimal skeleton docstring. Live firewall dropping / client quarantine not implemented or tested. |
| **9. Bandwidth Management** | `network/bandwidth.py` | Raspberry Pi 5 | **Planned / Not Integrated** | Minimal skeleton docstring. Live QoS / token-bucket traffic shaping not implemented or tested. |
| **10. Post-Action Verification**| `network/verification.py` | Raspberry Pi 5 | **Planned / Not Integrated** | Minimal skeleton docstring. Automated rule-verification logic pending future milestones. |
| **11. FastMCP Server** | `mcp_server/server.py`, `tools.py` | Python Runtime | **Planned / Not Integrated** | Minimal skeleton modules. Tool registration, Pydantic schemas, and FastMCP runtime pending. |
| **12. Policy Engine & Auth** | `policy/engine.py`, `rules.yaml` | Python Runtime | **Planned / Not Integrated** | Minimal skeleton modules. Declarative RBAC evaluation and safety rules pending. |
| **13. AI Assistant Client** | `assistant/client.py`, `prompts.py` | Python Runtime | **Planned / Not Integrated** | Minimal skeleton modules. LLM provider integration and conversational workflows pending. |
| **14. Automated Unit Tests** | `tests/unit/` | Local Workstation | **Implemented & Verified** | 27 passing tests in `test_access_point.py` and `test_device_discovery.py`. Mocked subprocesses; no root needed. |
| **15. End-to-End Pipeline** | Full System Stack | Integrated Environment | **Planned / Not Integrated** | Natural language prompt → MCP tool → Policy engine → Pi router execution has NOT been executed or tested. |
| **16. Laptop-as-Router AP** | Legacy `nmcli` AP manager | Fedora Workstation | **Obsolete & Removed** | Obsolete `nmcli` hotspot profile creation/activation code removed from `network/access_point.py`. |

---

## 2. Infrastructure Layer Breakdown

The physical and system-level routing layer is fully established and operational:

- **Host Platform:** Raspberry Pi 5 running Raspberry Pi OS Lite 64-bit (Debian Trixie base), hostname `ina-router`, admin user `team009`.
- **Interface Architecture:** Virtual interface `ap0` (`type __ap`) dynamically layered onto physical interface `wlan0`.
- **Addressing & Subnet:** `192.168.50.1/24` (Pi router gateway), client range `192.168.50.10`–`192.168.50.100`.
- **Verified Benchmarks:**
  - Local Wi-Fi Ping: 0% loss, 34.672 ms average latency.
  - External Internet Ping: 15% loss, 210.680 ms average latency (cellular uplink hop).
  - Local TCP Throughput: 25.2 Mbps receiver throughput over Wi-Fi (`iperf3`).
  - Reboot Recovery: Verified persistent across reboot.

---

## 3. Application & Control Layer Breakdown

The application layer components represent the core software to be developed in subsequent project milestones:

### Network Control Modules (`network/`)
- `device_discovery.py`: **Functional.** Discovers Linux network interfaces, routing tables, and Wi-Fi driver capabilities. Correlates ARP neighbor table (`ip neigh`) and DHCP leases (`/var/lib/misc/dnsmasq.leases`) to discover connected clients. Covered by unit tests.
- `access_point.py`: **Functional.** Models AP configuration, validates parameter schemas (SSID, PSK, channel, subnet), and provides read-only inspection of host system services (`hostapd`, `dnsmasq`, `ina-ap-interface`, `nftables`) and kernel forwarding. Legacy laptop `nmcli` hotspot creation has been purged. Covered by unit tests.
- `access_control.py`: **Skeleton.** Intended to manage packet filtering rules and client quarantine via `nftables` on the Pi.
- `bandwidth.py`: **Skeleton.** Intended to configure Linux traffic control (`tc`) rate limits and QoS priorities on `ap0`.
- `verification.py`: **Skeleton.** Intended to run post-action checks to confirm requested rule changes applied cleanly.

### Model Context Protocol Server (`mcp_server/`)
- `server.py`, `tools.py`, `schemas.py`: **Skeleton.** Intended to expose typed tools to the LLM assistant via FastMCP.

### Policy & Authorization Engine (`policy/`)
- `engine.py`, `authorization.py`, `rules.yaml`, `roles.yaml`: **Skeleton.** Intended to enforce role-based access control and declarative network safety rules.

### Assistant Client (`assistant/`)
- `client.py`, `prompts.py`: **Skeleton.** Intended to connect the operator's natural language requests to the FastMCP server.

---

## 4. Obsolete Code Removal Summary

During repository synchronization, the following obsolete implementations were audited and purged:

1. **NetworkManager Hotspot Profile Management:** Removed `create_ap_profile`, `activate_ap_profile`, `deactivate_ap_profile`, and `remove_ap_profile` from `network/access_point.py`. These functions attempted to configure the developer's laptop as a router using `nmcli connection add type wifi ... ipv4.method shared`, which disrupted active Wi-Fi uplinks on Fedora.
2. **Obsolete Laptop Interface & Subnet Defaults:** Purged hardcoded references to laptop interfaces (`wlp8s0`) and arbitrary subnets (`192.168.10.1/24`) from `config/settings.yaml`, `.env.example`, and test suites. Replaced with verified Raspberry Pi parameters (`ap0`, `wlan0`, `192.168.50.1/24`).
3. **Flawed `flush ruleset` Directive:** Verified and documented that `flush ruleset` is excluded from `/etc/nftables.conf` to avoid flushing unrelated firewall rules.

