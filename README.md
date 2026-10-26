# Intelligent Network Configuration Assistant v2 (INA v2)

**INA v2** is an automated network management and policy enforcement framework developed as a Computer Networks engineering project. The system manages, monitors, and shapes traffic on a dedicated test network using natural-language instructions communicated via the Model Context Protocol (MCP).

---

## 1. System Architecture at a Glance

The physical and system-level routing infrastructure is hosted on a **Raspberry Pi 5 (`ina-router`)**, which operates as a dedicated wireless access point and gateway router. Client devices—such as a Fedora developer workstation—connect over Wi-Fi as standard clients.

```text
Phone Hotspot ("1+ Four") ──[Wi-Fi Uplink]──> Raspberry Pi 5 ("ina-router") ──[Wi-Fi AP: "INA-Router"]──> Client Devices (Fedora, etc.)
                                              • Uplink: wlan0                              • Subnet: 192.168.50.0/24
                                              • AP: ap0 (virtual interface)                 • Gateway: 192.168.50.1
                                              • NAT: nftables (masquerade)                  • DHCP: 192.168.50.10–100
```

For complete architectural details, service dependency graphs, and packet flows, see [docs/architecture.md](docs/architecture.md).

---

## 2. Prerequisites

### Network Infrastructure Host (Raspberry Pi 5)
- **Hardware:** Raspberry Pi 5 (4GB or 8GB), 16GB+ microSD card, 27W USB-C power supply.
- **Operating System:** Raspberry Pi OS Lite (64-bit, Debian Trixie base).
- **Core Packages:** `hostapd`, `dnsmasq`, `nftables`, `iw`, `iperf3`.

### Development & Client Environment (Fedora / Linux Workstation)
- **Operating System:** Linux (Fedora 38+, Ubuntu 22.04+, or Debian 12+).
- **Python Runtime:** Python 3.11+ (Standard library only; zero external pip dependencies required for core inspection modules and unit tests).

---

## 3. Quick Start & Python Commands

### Run Automated Unit Tests
Execute the local test suite (tests mocked system commands, configuration schemas, and parsers without requiring root or live Pi hardware):

```bash
python3 -m unittest discover tests/unit -v
```

### Inspect Local Host Network & Connected Clients
Inspect network interfaces, kernel routing tables, wireless driver AP capabilities, and active DHCP/ARP clients:

```bash
# Print human-readable host discovery report
python3 -m network.device_discovery

# Query connected client devices on the managed AP network (ARP + DHCP leases)
python3 -m network.device_discovery --clients

# Export complete discovery telemetry as structured JSON
python3 -m network.device_discovery --json
```

### Inspect Router Infrastructure Status & Validate Config
Inspect live system services (`hostapd`, `dnsmasq`, `ina-ap-interface`, `nftables`), IPv4 forwarding, and NAT rules, or validate an AP configuration:

```bash
# Inspect live router infrastructure state
python3 -m network.access_point status

# Validate AP configuration parameters
python3 -m network.access_point validate --ssid INA-Router --channel 11
```

---

## 4. Documentation Index

The `docs/` directory serves as the project's source of truth:

- **[docs/architecture.md](docs/architecture.md):** Complete Pi-based system architecture, component ownership, topology, and packet traversal sequences.
- **[docs/setup.md](docs/setup.md):** Step-by-step reproducible guide to bringing up the Raspberry Pi 5 network router from a fresh OS installation.
- **[docs/network.md](docs/network.md):** Interface roles, IP address space (`192.168.50.0/24`), DHCP lease telemetry, and modular `nftables` NAT rules.
- **[docs/testing.md](docs/testing.md):** Verifiable records of live test runs (reboot validation, local ping, external ping, `iperf3` 25.2 Mbps throughput benchmarks, and test limitations).
- **[docs/troubleshooting.md](docs/troubleshooting.md):** Diagnostic commands and safe remediation steps for 11 verified failure scenarios.
- **[docs/implementation_status.md](docs/implementation_status.md):** Objective classification matrix detailing implemented, planned, and purged components across the entire repository.

---

## 5. Important Project Notice & Scope

> [!WARNING]
> **End-to-End Control Pipeline Status:**
> While the Raspberry Pi 5 network infrastructure (AP, DHCP, DNS, forwarding, NAT, and reboot persistence) is verified and operational, the **FastMCP server (`mcp_server/`)**, **Policy Engine (`policy/`)**, **AI Assistant client (`assistant/`)**, **access control blocking (`network/access_control.py`)**, and **bandwidth traffic shaping (`network/bandwidth.py`)** remain planned components.
>
> They exist as modular architecture skeletons and have **NOT** been integrated or tested end to end. Do not claim or assume that natural-language network mutation is operational.
