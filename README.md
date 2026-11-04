# Intelligent Network Configuration Assistant v2 (INA v2)

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP 2.3.0](https://img.shields.io/badge/mcp-2.3.0-green.svg)](https://modelcontextprotocol.io/)
[![Tests Passing](https://img.shields.io/badge/tests-339%20passed-success.svg)](code/tests/)
[![Security Defended](https://img.shields.io/badge/eval%20attacks-65%2F65%20defended-brightgreen.svg)](code/eval/)
[![Architecture](https://img.shields.io/badge/architecture-Control%2FData%20Plane%20Split-orange.svg)](code/docs/architecture.md)

> **Academic Project:** B.Tech Computer Science and Engineering (Semester 5) — CS 331 (Computer Networks)  
> **Repository Root Structure:** Submission-compliant layout (`AI_Used`, `code`, `ppt`, `report`, `README.md`).

---

## 1. Problem Statement

Configuring and administering modern Linux-based computer networks requires deep expertise in fragmented, low-level subsystem tooling:
- **Packet Filtering & Firewalls:** `nftables` / `iptables` table, set, and rule syntax.
- **Traffic Shaping:** `iproute2` traffic control (`tc`), Hierarchical Token Bucket (`htb`), and Intermediate Functional Block (`ifb`) pseudodevices.
- **Wireless Layer 2 Management:** `hostapd` control interfaces, MAC denial ACLs, and 802.11 deauthentication frames.
- **Addressing & Name Resolution:** `dnsmasq` lease parsing, DHCP host reservations, and ARP/neighbor tables.

Human administrators frequently make catastrophic errors—such as unintended broad IP bans, traffic blackholing, subnet collisions, and accidental administrative lockout.

While Large Language Models (LLMs) can parse human intent into operational actions, **giving an LLM unrestricted shell access (`sudo bash`) is extraordinarily dangerous**. An LLM can hallucinate destructive shell commands, fall victim to prompt injection attacks embedded in user requests or device hostnames, or execute unverified configuration mutations without rollback capability.

---

## 2. Project Objectives

**INA v2** bridges the gap between natural language administration and production Linux network infrastructure through a secure, robust, and verifiable architecture:

1. **Natural Language Network Management:** Enable administrators and operators to query network status, block/unblock clients, impose bandwidth constraints, and kick or ban Wi-Fi clients using conversational prompts (e.g., *"limit guest-phone to 5 Mbps"*).
2. **Zero Unrestricted Shell Execution:** The AI assistant never receives shell access. It interacts exclusively with strictly-typed Model Context Protocol (MCP) tool schemas.
3. **Independent Data-Plane Policy Enforcement:** Every mutation request is authoritatively inspected and authorized by a deterministic, rule-based policy engine running on the router, completely independent of the LLM's claims.
4. **Hardware-Verified Linux Router:** Deploy the complete data plane on a physical **Raspberry Pi 5** router running Raspberry Pi OS Lite (64-bit), providing real Wi-Fi access point services, NAT routing, DHCP, and traffic control.
5. **Privilege Boundary & Cryptographic Auditing:** Confine all kernel-modifying operations to a dedicated privileged helper (`ina-netctl`) governed by strict `sudoers` rules, with hash-chained JSONL audit trails tying user intent to executed kernel primitives.

---

## 3. Key Features

- **Multi-Role Access Control (RBAC):** Three distinct roles (`admin`, `operator`, `viewer`) granted per-person and cryptographically bound to physical hardware devices.
- **Protected Infrastructure Immunity:** Router interfaces (`pi-gateway`), administrative laptops (`admin-laptop`), and management probes are permanently protected from mutation, blocking, or rate-limiting.
- **Automated Verification & Atomic Rollback:** Kernel state is immediately queried after mutation; if the intended rule or traffic condition fails verification, the transaction is automatically rolled back.
- **Dual-Plane Split Architecture:** Lightweight operator CLI running on an administrator laptop communicating over Streamable HTTP (TLS) to the MCP server on the Raspberry Pi 5.
- **Multi-Provider LLM Integration with Deterministic Fallback:** Built-in adapters for Anthropic Claude, Google Gemini, OpenRouter, and local Ollama instances, backed by a zero-dependency rule-based parser that guarantees 100% offline functionality.
- **Subnet & Route Preflight Safety:** Automated detection of IP route collisions, interface conflicts, and rogue default route overrides.

---

## 4. System Architecture

INA v2 enforces a strict separation between the **Control Plane** (Operator Laptop) and the **Data Plane** (Raspberry Pi 5 Router):

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               OPERATOR LAPTOP (Control Plane)                          │
│                                                                                        │
│   Administrator Prompt (NL) ──► Assistant Orchestrator ──► LLM (Gemini / Claude / etc)  │
│                                           │                                            │
│                                    Tool Call Decision                                  │
│                                           │                                            │
│                                     MCP Client (SDK)                                   │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
                                            │  Streamable HTTP over TLS (HTTPS)
                                            │  Endpoint: https://192.168.50.1:8443/mcp
                                            │  Header: Authorization: Bearer <Role-Token>
                                            │
┌───────────────────────────────────────────▼────────────────────────────────────────────┐
│                             RASPBERRY PI 5 (Data Plane)                                │
│                                                                                        │
│   ┌────────────────────────────────────────────────────────────────────────────────┐   │
│   │  Unprivileged MCP Server (user: 'ina')                                         │   │
│   │    • FastMCP / Official MCP Python SDK (2.3.0)                                 │   │
│   │    • Pydantic Schema Validation & Sanitization                                 │   │
│   │    • Authoritative Policy Guard (Token -> Role -> Device Binding)              │   │
│   │    • Policy Audit Logger (policy_audit.jsonl)                                  │   │
│   └───────────────────────────────────────┬────────────────────────────────────────┘   │
│                                           │                                            │
│                               Strict sudoers boundary                                  │
│                               sudo -n /usr/local/sbin/ina-netctl <args>                │
│                                           │                                            │
│   ┌───────────────────────────────────────▼────────────────────────────────────────┐   │
│   │  Privileged Network Service (root:root)                                        │   │
│   │    • Command Log & Hash-Chained Trace (commands.jsonl)                         │   │
│   │    • Device Registry & Anti-Spoofing (MAC <-> IP Binding)                      │   │
│   │    • Linux Network Enforcement Primitives:                                     │   │
│   │        ├── nftables      ──► Packet filter tables & sets (ina_filter)          │   │
│   │        ├── tc / htb / ifb ──► Egress & ingress rate limiting classes          │   │
│   │        └── hostapd_cli   ──► Station disassociation & MAC denial lists         │   │
│   │    • Verification Engine (ping reachability, tc filter stats, station check)   │   │
│   │    • Atomic Rollback on Command Failure                                        │   │
│   └────────────────────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Hardware Requirements

| Hardware Item | Specification | Role in System |
|---|---|---|
| **Raspberry Pi 5** | 4GB or 8GB LPDDR4X-4267 | Core network router, AP, and MCP server host |
| **Power Supply** | Official Raspberry Pi 27W USB-C (5.1V / 5.0A) | High-current power to prevent brownouts under Wi-Fi load |
| **Storage** | 32GB+ Class 10 MicroSD or NVMe SSD | OS root filesystem, logs, and INA codebase |
| **Onboard Wi-Fi** | Broadcom BCM43455 (802.11ac, dual-band) | Virtual Access Point interface (`ap0`) and upstream (`wlan0`) |
| **Operator Laptop** | Any Linux, macOS, or Windows WSL2 system | Control plane CLI, LLM interaction, administrative terminal |
| **Client Devices** | Smartphones (Android / iOS), test laptops | Real station clients connecting to SSID `INA-Router` |

---

## 6. Software Requirements

### Raspberry Pi 5 (Data Plane)
- **OS:** Raspberry Pi OS Lite 64-bit (Debian GNU/Linux 13 Trixie / Bookworm compatible).
- **Kernel:** Linux 6.6+ with modules: `sch_htb`, `sch_sfq`, `cls_flower`, `act_mirred`, `ifb`.
- **System Services:** `hostapd` (v2.10+), `dnsmasq` (v2.90+), `nftables` (v1.0.9+), `iproute2`.
- **Python Environment:** Python 3.11+ with official Model Context Protocol SDK (`mcp==2.3.0`) and `pydantic`.

### Operator Laptop (Control Plane)
- **Python:** Python 3.11+
- **Dependencies:** `mcp==2.3.0` (standard library only for core modules and offline tests).
- **Optional LLM SDKs:** `anthropic>=0.18.0` (for Claude). Gemini, OpenAI, and OpenRouter work out-of-the-box via standard library HTTP clients.

---

## 7. Network Topology

```text
       UPSTREAM INTERNET (Mobile Hotspot / Campus Wi-Fi)
                             │
                             │ DHCP (e.g. 10.7.0.0/18 or 192.168.1.0/24)
                             ▼
                    ┌─────────────────┐
                    │  wlan0 (Client) │
                    │                 │
                    │ Raspberry Pi 5  │  Forwarding: net.ipv4.ip_forward = 1
                    │   ina-router    │  NAT: table ip ina_nat { chain postrouting { masquerade } }
                    │                 │
                    │   ap0 (AP mode) │  SSID: "INA-Router", WPA2-Personal, Channel 11
                    └────────┬────────┘  IP: 192.168.50.1/24 (Static)
                             │
             ┌───────────────┴───────────────┐
             │                               │
             ▼                               ▼
    Protected Devices                 Standard Clients
    Fixed IPs (.2 - .9)               DHCP Pool (.10 - .100)
    • admin-laptop: 192.168.50.2      • guest-phone: 192.168.50.34
    • probe-laptop: 192.168.50.3      • lab-laptop-3: 192.168.50.35
```

- **Client Isolation:** `ap_isolate=1` in `hostapd.conf` blocks direct peer-to-peer Wi-Fi frames.
- **Hairpin Routing:** `proxy_arp_pvlan=1` on `ap0` forces inter-client communication through the Pi's `nftables` routing chains, enabling deterministic peer blocking.
- **Anti-Spoofing:** Static hardware MAC-to-IP binding in `table inet ina_filter set mac_ip_bind` drops forged packets.

---

## 8. Repository Structure

The repository root strictly adheres to the required five-entry submission structure:

```text
INA-v2/
├── AI_Used/         # Completely EMPTY (submission requirement)
├── code/            # Complete implementation, configuration, tests, and technical documentation
├── ppt/             # Completely EMPTY (submission requirement)
├── report/          # Completely EMPTY (submission requirement)
└── README.md        # Main project documentation (this document)
```

### Detailed Breakdown of `code/`:

```text
code/
├── assistant/              # Control-plane AI assistant & operator CLI
│   ├── client.py           # Orchestration loop, clarification handling, result presentation
│   ├── config.py           # Environment and .env file loading
│   ├── contract.py         # Data classes: Intent, ToolCall, ExecutionResult, Decision
│   ├── intent.py           # Deterministic rule-based Natural Language parser
│   ├── llm.py              # LLM adapters (Claude, Gemini, OpenAI-compatible, Ollama)
│   ├── prompts.py          # System prompt with tool definitions and security instructions
│   ├── result_view.py      # Structured result formatting for human and LLM consumption
│   └── transport.py        # Streamable HTTP MCP client, Stdio transport, and mock transport
├── config/                 # Router service configurations and deployment templates
│   ├── dnsmasq/            # DHCP and DNS configuration (ina-ap.conf)
│   ├── hostapd/            # Wi-Fi Access Point template (hostapd.conf.template)
│   ├── modules-load.d/     # Kernel module loader configuration (ifb, sch_htb)
│   ├── NetworkManager/     # Drop-in configuration to keep ap0 unmanaged
│   ├── nftables/           # Base NAT masquerade rules and filter definitions
│   ├── scripts/            # Helper scripts (ina-ap-interface.sh, ina-netctl, ina-mcp)
│   ├── sudoers.d/          # Privilege boundary sudoers configuration for user 'ina'
│   └── systemd/            # Systemd service units (ina-mcp, ina-reconcile, ina-expire)
├── docs/                   # Consolidated technical documentation and runbooks
│   ├── AI_Used/            # Historical prompt engineering and tool records
│   ├── architecture.md     # Detailed 14-step request pipeline, topology, and threat boundaries
│   ├── implementation.md   # MCP server, 8 canonical tools, network modules, and policy engine
│   ├── security.md         # Privilege boundary (ina-netctl), RBAC, anti-spoofing, and audit logging
│   ├── setup_and_deployment.md # Complete Pi and laptop installation runbooks & config reference
│   ├── testing_and_evaluation.md # Hardware test records, lab runs, unit tests, and eval benchmark
│   └── troubleshooting.md  # 25 concrete diagnostic cases and remediation procedures
├── eval/                   # Automated evaluation suite
│   ├── cases.py            # Test cases: 16 functional, 10 clarification, 13 security attacks
│   ├── results/            # Evaluator output CSVs (functional.csv, clarification.csv, attacks.csv)
│   └── run_eval.py         # Evaluation runner comparing LLM vs rule-based parser
├── inav2/                  # Package compatibility shim
│   └── __init__.py         # Dynamic aliasing finder ensuring backwards compatibility
├── logs/                   # Directory for runtime logs (.gitkeep)
├── mcp_server/             # Official Model Context Protocol server
│   ├── auth.py             # Bearer token verifier and grant validator
│   ├── schemas.py          # Pydantic schemas for all tool inputs and outputs
│   ├── server.py           # FastMCP server, TLS configuration, and Streamable HTTP endpoint
│   └── tools.py            # Canonical tool registrations and policy guard hooks
├── network/                # Linux network enforcement implementation
│   ├── access_control.py   # nftables set manipulations (block_client, unblock_client)
│   ├── access_point.py     # Router preflight safety checker and status inspector
│   ├── audit.py            # Audit log integrity verification
│   ├── bandwidth.py        # Traffic Control (tc/HTB) bandwidth rate limiter
│   ├── command_log.py      # Cryptographic hash-chained command logging
│   ├── device_discovery.py # ARP, lease, and route discovery engine
│   ├── devices.py          # Device registry, reservations, and anti-spoofing bindings
│   ├── netctl.py           # Privileged helper implementation (CLI entry point for root)
│   ├── privileged.py       # IPC client for invoking ina-netctl over sudo
│   ├── service.py          # NetworkService orchestrator (validate, lock, apply, verify, rollback)
│   ├── verification.py     # Kernel state verifier (ping, tc filter query, hostapd check)
│   └── wireless.py         # hostapd_cli wrapper (kick, ban, unban station)
├── policy/                 # Deterministic rule-based authorization engine
│   ├── audit.py            # Policy decision audit trail logger
│   ├── authorization.py    # Token authenticator and role mapper
│   ├── config.py           # YAML configuration loader
│   ├── devices.yaml        # Device registry (names, MAC addresses, device classes)
│   ├── engine.py           # Core PolicyEngine evaluating requests against rules
│   ├── grants.py           # Device-bound per-person role grant manager
│   ├── guard.py            # PolicyGuard intercepting MCP tool executions
│   ├── roles.yaml          # Role hierarchy definitions and permitted actions
│   └── rules.yaml          # Rate boundaries, duration limits, and protected devices
├── tests/                  # Complete test suite (339 tests)
│   ├── conftest.py         # Pytest configuration
│   ├── lab/                # Real-kernel network namespace lab (ina_block_lab.sh)
│   ├── run_all_tests.py    # Test runner executing all unit and integration tests
│   └── unit/               # 20 unit test suites testing all components in isolation
├── pyproject.toml          # PEP 518/621 project configuration
├── requirements.txt        # Core dependencies (mcp==2.3.0)
└── .env.example            # Environment configuration template
```

---

## 9. Installation Instructions

### Operator Laptop Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/vedantb7/inav2.git
   cd inav2/code
   ```

2. **Create and activate a Python virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. **Configure Environment Variables:**
   Copy `.env.example` to `.env` in `code/`:
   ```bash
   cp .env.example .env
   ```
   Configure your chosen LLM provider (or leave blank to use the deterministic offline parser).

---

## 10. Raspberry Pi 5 Setup

For a full step-by-step walkthrough from a fresh SD card, refer to the complete runbook: [code/docs/setup_and_deployment.md](code/docs/setup_and_deployment.md).

### Quick Summary of Commands on the Pi:

```bash
# 1. Install prerequisites
sudo apt update && sudo apt install -y hostapd dnsmasq nftables iw iperf3 ethtool git python3-venv openssl curl
sudo systemctl unmask hostapd

# 2. Clone repository to /opt/ina/inav2
sudo install -d -o root -g root -m 0755 /opt/ina
sudo git clone https://github.com/vedantb7/inav2.git /opt/ina/inav2
cd /opt/ina/inav2/code

# 3. Setup virtual environment
sudo chown -R root:root /opt/ina
sudo python3 -m venv /opt/ina/venv
sudo /opt/ina/venv/bin/pip install -r requirements.txt

# 4. Install network configurations
sudo install -m 0600 config/hostapd/hostapd.conf.template /etc/hostapd/hostapd.conf
sudo install -m 0644 config/dnsmasq/ina-ap.conf /etc/dnsmasq.d/ina-ap.conf
sudo install -m 0644 config/NetworkManager/90-ina-unmanaged.conf /etc/NetworkManager/conf.d/
sudo install -m 0755 config/scripts/ina-ap-interface.sh /usr/local/sbin/
sudo install -m 0644 config/nftables/nftables.conf /etc/nftables.conf
sudo install -m 0644 config/modules-load.d/ina.conf /etc/modules-load.d/ina.conf

# 5. Setup privilege boundary and system services
sudo useradd --system --create-home --shell /bin/sh ina
sudo install -o root -g root -m 0755 config/scripts/ina-netctl /usr/local/sbin/ina-netctl
sudo install -o root -g root -m 0755 config/scripts/ina-mcp    /usr/local/bin/ina-mcp
sudo install -o root -g root -m 0440 config/sudoers.d/ina     /etc/sudoers.d/ina
sudo install -m 0644 config/tmpfiles.d/ina.conf               /etc/tmpfiles.d/ina.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/ina.conf

# 6. Enable systemd units
sudo install -m 0644 config/systemd/*.service config/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ina-ap-interface.service hostapd.service dnsmasq.service nftables.service
sudo systemctl enable --now ina-reconcile.service ina-expire.timer ina-mcp.service
```

---

## 11. MCP Communication Architecture

INA v2 uses the official Model Context Protocol (MCP) Python SDK over **Streamable HTTP with TLS**:

1. **Protocol:** Streamable HTTP (`/mcp` endpoint) on port `8443`.
2. **Encryption:** TLS (HTTPS) with an elliptic curve certificate (`P-256`) generated on the Pi. The operator laptop trusts the Pi's public certificate via `INA_MCP_CA`.
3. **Authentication:** Every HTTP request carries `Authorization: Bearer <token>`.
4. **Device Binding:** The MCP server extracts `SSH_CLIENT` or the HTTP client IP address and validates that the role token is cryptographically assigned to that specific registered device.
5. **CORS & Host Protection:** The server strictly checks HTTP `Host` and `Origin` headers against allowed identities (`ina-router`, `ina-router.local`, `192.168.50.1`), preventing DNS rebinding attacks.

---

## 12. How to Run the Project

### A. Run Against the Built-in Mock Network (Offline / Local Dev)

No Raspberry Pi hardware required. The assistant simulates the network and policy state:

```bash
cd code
python3 -m assistant "list devices"
python3 -m assistant "limit guest-phone to 5 Mbps"
python3 -m assistant "block guest-phone"
python3 -m assistant "restore guest-phone"
```

### B. Interactive Mode

```bash
cd code
python3 -m assistant
```
```text
ina> list devices
ina> block guest-phone
ina> exit
```

### C. Run with a Live LLM Provider

Set your credentials in `code/.env`:
```bash
# Example using Google Gemini
LLM_PROVIDER=gemini
LLM_API_KEY=AIzaSy...
LLM_MODEL=gemini-2.5-flash
```
Run the assistant:
```bash
python3 -m assistant "limit lab-laptop-3 to 10 mbps"
```

### D. Connect to the Live Raspberry Pi Router

On your administrative laptop connected to the `INA-Router` Wi-Fi:

```bash
cd code
INA_ROLE_TOKEN="<your-admin-token>" \
INA_MCP_CA="/path/to/server.crt" \
python3 -m assistant --server mcp --mcp-url https://192.168.50.1:8443/mcp "list devices"
```

---

## 13. Available MCP Tools

The INA MCP server registers exactly **8 canonical network management tools**:

| Tool Name | Parameters | Permitted Roles | Action Performed |
|---|---|---|---|
| `get_status` | `detailed: bool = False` | `viewer`, `operator`, `admin` | Returns active leases, connected Wi-Fi stations, active blocks, and bandwidth limits. |
| `block_client` | `client: str`, `target: str` | `operator`, `admin` | Blocks client traffic to `internet`, `gateway`, `all`, or a peer client via `nftables`. |
| `unblock_client` | `client: str`, `target: str = "all"` | `operator`, `admin` | Removes active drop elements from `nftables` sets. |
| `limit_bandwidth` | `client: str`, `download_kbit: int`, `upload_kbit: int` | `operator`, `admin` | Imposes bandwidth shaping via `tc` HTB classes on `ap0` and ingress `ifb0`. |
| `remove_bandwidth_limit` | `client: str` | `operator`, `admin` | Removes `tc` rate limiting filters and classes for the client. |
| `kick_client` | `client: str`, `reason: str` | `operator`, `admin` | Issues an 802.11 deauthentication frame via `hostapd_cli deauthenticate`. |
| `ban_client` | `client: str`, `duration_minutes: int`, `confirm: bool` | `operator` (<=60 min), `admin` (any) | Adds station MAC to `/etc/hostapd/hostapd.deny` and deauthenticates station. |
| `unban_client` | `client: str` | `operator` (own bans), `admin` (any) | Removes station MAC from hostapd deny ACL. |

---

## 14. Security Architecture & Threat Defense

INA v2 implements **Defense in Depth**:

```text
       Natural Language Request
                  │
                  ▼
   ┌──────────────────────────────┐
   │ 1. Assistant Prompt Guard    │ ──► Refuses raw shell syntax, checks basic syntax
   └──────────────┬───────────────┘
                  │
                  ▼
   ┌──────────────────────────────┐
   │ 2. MCP Pydantic Schema       │ ──► Type validation, regex constraints (MACs, IPs)
   └──────────────┬───────────────┘
                  │
                  ▼
   ┌──────────────────────────────┐
   │ 3. Authoritative Policy Guard│ ──► Device binding, role permissions, rate ranges,
   └──────────────┬───────────────┘     protected device immunity
                  │
                  ▼
   ┌──────────────────────────────┐
   │ 4. Privilege Boundary        │ ──► Unprivileged 'ina' calls strict sudo 'ina-netctl'
   └──────────────┬───────────────┘     Isolated Python (-I), whitelisted subcommands
                  │
                  ▼
   ┌──────────────────────────────┐
   │ 5. Linux Kernel Enforcement  │ ──► nftables sets, tc HTB classes, hostapd deny list
   └──────────────────────────────┘
```

1. **Prompt Injection Immunity:** User text is never concatenated into shell commands. The assistant only extracts arguments into structured tool parameters.
2. **Protected Device Shield:** Router IP (`192.168.50.1`), gateway MAC, and admin laptops are hardcoded as `protected` in `devices.yaml`. Any mutation attempting to modify them is rejected at the policy layer.
3. **Hardware Anti-Spoofing:** Static DHCP allocations are mirrored to an `nftables` hardware set. Packets with spoofed source IPs are dropped in the prerouting chain.
4. **Rate Range Enforcement:** Operators cannot set arbitrary bandwidth. Limits must fall within configured bounds (e.g., 2 Mbps to 20 Mbps).
5. **Cryptographic Audit Chains:** All decisions and executions are logged to `/var/log/ina/commands.jsonl` with SHA-256 hash-chaining to detect tampering.

---

## 15. Testing & Evaluation

### Test Suite Execution

The automated test suite contains **339 tests**:
```bash
cd code
python3 tests/run_all_tests.py
```
**Results:**
- **Ran:** 339 tests cleanly
- **Passed:** 314 tests
- **Failures:** 0
- **Errors:** 0
- **Skipped:** 25 (live-kernel hardware tests that cleanly skip when run on non-root development machines)

### Evaluation Benchmark

An evaluation harness runs **16 functional cases**, **10 clarification cases**, and **13 security attack cases** (each attack evaluated across 5 trials = 65 runs):

```bash
cd code
python3 -m eval.run_eval
```

| Evaluation Category | Test Count | Pass Rate | Result Summary |
|---|---|---|---|
| **[E2] Functional Correctness** | 16 test cases | **100.0% (16/16)** | Proper tool calls generated and executed for all clear requests. |
| **[E8] Clarification Handling** | 10 test cases | **100.0% (10/10)** | Assistant asked clarifying questions instead of hallucinating parameters. |
| **[E3] Adversarial Attacks** | 65 attack runs | **100.0% (65/65 Defended)** | **0 unsafe executions.** 30 injection attempts generated tool calls, but 100% were blocked by the router's policy engine. |

### Real-Kernel Lab Testing (No Root Required)

Using Linux network namespaces and `unshare`:
```bash
cd code
INA_RUN_LAB=1 python3 -m unittest inav2.tests.unit.test_router_config
```
Validates real packet drops and throughput constraints using Linux virtual ethernet (`veth`) pairs and bridge port isolation.

---

## 16. Known Limitations

1. **Hardware Verification Scope:** While the router infrastructure, MCP server, role grants, and Wi-Fi kick/ban operations have been verified on physical Raspberry Pi 5 hardware, exact `iperf3` throughput measurements under active `tc` shaping remain to be benchmarked in formal test records.
2. **MAC Randomization on Mobile Devices:** Modern smartphones randomize their Wi-Fi MAC address by default for new networks. If a phone reconnects with a randomized MAC, it is treated as a new client device until registered in `policy/devices.yaml`.
3. **Public LLM API Quotas:** When using free-tier LLM endpoints (e.g. Gemini Free), API rate limits (HTTP 429) can occasionally trigger fallback to the local rule-based parser.

---

## 17. Academic Project & Team Information

- **Institution:** B.Tech in Computer Science and Engineering
- **Course:** CS 331 — Computer Networks
- **Project Title:** Intelligent Network Configuration Assistant using MCP and Rule-Based Automation (INA v2)
- **Role & Contribution Summary:**
  - **Network Architecture & Kernel Primitives:** Raspberry Pi 5 access point setup, `ap0` virtual interface engineering, `nftables` filter and NAT tables, `tc` traffic shaping, and `hostapd` wireless station management.
  - **Model Context Protocol & Privilege Boundary:** FastMCP server implementation, Streamable HTTP over TLS, token-based RBAC, and the hardened `ina-netctl` sudoers privilege separation boundary.
  - **AI Assistant & Natural Language Processing:** Multi-provider LLM integration, rule-based fallback parser, prompt engineering, adversarial injection defense, and automated evaluation framework.
  - **Quality Assurance & Verification:** Comprehensive 339-test suite, namespace-isolated kernel testing, and hash-chained audit logging.

---

*For detailed technical deep-dives, consult the comprehensive guides in [`code/docs/`](code/docs/).*
