# INA v2 Architecture

## Overview

The **Intelligent Network Configuration Assistant v2 (INA v2)** is an automated network management system designed to manage a dedicated test network. Initially hosted on a Fedora laptop with planned migration to a Raspberry Pi, the system provides natural-language driven network configuration, monitoring, and policy enforcement via the Model Context Protocol (MCP).

## Planned System Components

```
┌────────────────────────────────────────────────────────┐
│               Natural Language Assistant               │
│               (Client & Prompt Engine)                 │
└───────────────────────────┬────────────────────────────┘
                            │ MCP Protocol
┌───────────────────────────▼────────────────────────────┐
│                    FastMCP Server                      │
│            (Tool Registry & Schema Models)             │
└──────┬────────────────────┬────────────────────┬───────┘
       │                    │                    │
┌──────▼──────┐      ┌──────▼──────┐      ┌──────▼──────┐
│   Policy    │      │   Network   │      │ Verification│
│ & Auth      │      │ Operations  │      │ & Telemetry │
│ Engine      │      │  (NetOps)   │      │             │
└─────────────┘      └─────────────┘      └─────────────┘
```

1. **Natural-Language Assistant and MCP Client (`assistant/`)**
   - Processes user intent and interacts with network management tools through MCP.
   - Formulates structured prompts, manages conversational context, and coordinates multi-step operational tasks.

2. **FastMCP Server (`mcp_server/`)**
   - Exposes safe, typed network administration tools adhering to the Model Context Protocol.
   - Validates input arguments and response schemas using Pydantic models.

3. **Independent Policy and Authorization Engine (`policy/`)**
   - Decouples policy verification and role-based access control from tool implementation.
   - Evaluates actions against declaratively defined rules (`rules.yaml`) and role assignments (`roles.yaml`).

4. **Network Device Discovery and Access Control (`network/`)**
   - `device_discovery.py`: Scans and maintains an active inventory of connected network clients.
   - `access_control.py`: Enforces device admission policies, quarantine isolation, and firewall filtering rules.
   - `bandwidth.py`: Configures Quality of Service (QoS), rate limits, and bandwidth shaping rules.
   - `verification.py`: Confirms state changes post-action to ensure requested operations completed successfully.

5. **Configuration and Environment (`config/`, `.env.example`)**
   - Centralized system settings (`settings.yaml`) and environmental configurations.

6. **Test Suite (`tests/`)**
   - Segregated unit (`tests/unit/`) and integration (`tests/integration/`) test suites.
   - Shared test fixtures configured in `tests/conftest.py`.

## Current State & Milestone Status

- **Milestone 1 — Read-Only Network Foundation (Implemented):**
  - Host network discovery and reporting implemented in `network/device_discovery.py`.
  - Collects interfaces, MAC addresses, operational states, IP assignments, routing tables, default routes, wireless driver AP capabilities, and tunnel classifications.
  - Generates structured data models (`NetworkReport`, `InterfaceInfo`, `RouteInfo`, candidate models) and human-readable reports.
  - Strictly read-only; no network mutations, hotspot configuration, or firewall modifications are performed.
- **Milestone 2 — Safe Access Point Management (Implemented):**
  - Access point profile lifecycle and safety management implemented in `network/access_point.py`.
  - Configures WPA2-Personal security, explicit subnets, and NetworkManager's `shared` IPv4 mode.
  - Implements pre-flight safety checks leveraging discovery data to prevent disconnecting active Wi-Fi uplinks.
  - Enforces strict namespace isolation (`ina-*`), dry-run defaults, and explicit confirmation requirements.
- **Pending Milestones:**
  - FastMCP tool exposure and schemas (`mcp_server/`).
  - Packet forwarding rules, firewall policies, and client quarantine (`network/access_control.py`).
  - Policy and authorization engine (`policy/`).
  - Bandwidth shaping and QoS controls (`network/bandwidth.py`).
  - Assistant and conversational agent (`assistant/`).

## Component Deep Dive: Network Discovery (`network/device_discovery.py`)

### Purpose & Architecture
The network discovery module inspects the host network environment on Linux (Fedora) and provides the ground truth required for later milestones to configure and host a dedicated access point safely without disrupting the host's existing internet connectivity.

### Observed Facts vs. Inferred Candidates
The discovery engine strictly separates direct observations from inferred recommendations:
- **Observed Facts:**
  - Network interfaces, operational states (`UP`, `DOWN`, `UNKNOWN`), device flags, and link types from `ip -j addr` and `ip -j -d link`.
  - Physical MAC addresses and assigned IPv4/IPv6 addresses with CIDR prefixes and scopes.
  - Active kernel routing tables, destinations, gateways, metrics, and protocols from `ip -j route` and `ip -j -6 route`.
  - Wireless device mapping (`iw dev`) and physical adapter AP mode capability advertisement (`iw list`).
  - Kernel tunnel flags and drivers (WireGuard, TUN/TAP, SIT, GRE, overlay networks).
- **Inferred Candidates:**
  - **Uplink Candidates:** Derived from default routes sorted by metric. The lowest-metric active route on an operational interface is inferred as the primary uplink.
  - **Access Point (AP) Candidates:** Wireless interfaces whose drivers advertise `AP` mode. Interfaces currently acting as the primary uplink are flagged with warnings regarding the need for virtual interfaces (VIF) or secondary Wi-Fi adapters.
  - **Tunnel Impact Analysis:** Tunnels with active routing table entries that may divert subnet traffic or alter routing policies.

### Error Handling & Degradation
- All system commands are executed via argument lists without shell interpolation (`shell=False`) and guarded by execution timeouts.
- Missing utilities (e.g. missing `iw`), permission restrictions, and corrupted output are trapped gracefully and returned as diagnostic warnings in `report.warnings`.
- Unavailable information (such as AP capability when `iw` is unavailable) is explicitly recorded as `None` to distinguish it from a verified negative (`False`).

## Component Deep Dive: Safe Access Point Management (`network/access_point.py`)

### Purpose & Relationship to Discovery
The access point management component builds upon `network/device_discovery.py` to manage a dedicated test network access point on Fedora using NetworkManager. Before executing any profile actions, the AP manager calls `discover_network()` to obtain real-time ground truth regarding active interfaces, kernel routes, and Wi-Fi capabilities.

### Separation of Responsibilities & Lifecycle Stages
To avoid assumptions regarding network state, the architecture clearly separates four operational stages:
1. **Profile Creation (Implemented):** Creates or updates an inactive NetworkManager connection profile with explicit SSID, WPA2-PSK security, and `ipv4.method shared`. Autoconnect is set to `no` so the AP profile remains dormant until explicitly invoked.
2. **AP Activation (Implemented with Safety Guards):** Invokes `nmcli connection up <profile_name>`. Guarded by pre-flight checks, confirmation requirements (`confirm=True`), and post-activation verification to ensure the interface genuinely transitions to the active state.
3. **Client Connectivity & Addressing (Handled by NetworkManager):** NetworkManager's internal `dnsmasq` instance manages DHCP address allocation on the configured AP subnet once active.
4. **Internet Forwarding & Firewall Enforcement (Pending Milestone 3+):** Forwarding between AP clients and upstream interfaces (e.g. Wi-Fi client, Ethernet, or Cloudflare WARP) and packet inspection/quarantine will be implemented in subsequent milestones.

### Safety Guards & Incompatible Uplink Detection
- **Dynamic Interface Selection Precedence:**
  1. Explicit CLI argument (`--interface <name>`).
  2. Configuration file (`config/settings.yaml` -> `network.interface`).
  3. Environment variable (`NETWORK_INTERFACE`).
  4. Unambiguous network discovery (selects a dedicated non-uplink Wi-Fi interface only when exactly one candidate exists).
  - If multiple wireless interfaces exist or the only available wireless interface is the active internet uplink, automatic selection is refused to prevent network disruption, and explicit configuration is required.
- **Single-Radio Simultaneous Operation Constraints:** Most consumer Wi-Fi cards (such as standard Intel `iwlwifi` laptop chipsets) cannot safely maintain a client uplink connection (`STA`) and an access point (`AP`) simultaneously, or are constrained to a single frequency channel. Activating an AP on an active uplink interface will cause NetworkManager to drop the uplink Wi-Fi connection (e.g. `IITGN-SSO`).
- **Active Uplink Conflict Detection:** The AP pre-flight check detects when the target interface carries an active default route or active client connection, and refuses activation by default. A dedicated secondary Wi-Fi adapter or Ethernet uplink is required for reliable simultaneous operation.
- **Strict Namespace Isolation:** All INA profiles must be prefixed with `ina-` (e.g. `ina-ap`). The module strictly refuses to inspect, modify, deactivate, or delete any NetworkManager connection outside this namespace, safeguarding existing profiles (e.g. `IITGN-SSO`, `CloudflareWARP`, `Wired connection 1`).
- **Credential Protection:** Passwords are never stored in plain text in logs or formatted command previews. String representations mask passphrases with `***`.

