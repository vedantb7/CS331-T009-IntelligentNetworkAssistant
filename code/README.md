# Intelligent Network Configuration Assistant v2 (INA v2) — Codebase Reference

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP 2.3.0](https://img.shields.io/badge/mcp-2.3.0-green.svg)](https://modelcontextprotocol.io/)
[![Tests Passing](https://img.shields.io/badge/tests-342%20passing-success.svg)](tests/)
[![Security Defended](https://img.shields.io/badge/eval%20attacks-65%2F65%20defended-brightgreen.svg)](eval/)
[![Architecture](https://img.shields.io/badge/architecture-Control%2FData%20Plane%20Split-orange.svg)](docs/architecture.md)

**INA v2** is a natural-language network configuration and management assistant for Linux-based network routers, built for CS 331 (Computer Networks). An operator issues high-level natural language requests such as *"limit guest-phone to 5 Mbps"* or *"block guest-phone from internet"*. An AI assistant translates the intent into strongly-typed Model Context Protocol (MCP) tool calls, which are validated by an authoritative policy engine on the router and executed through Linux kernel subsystems (`nftables`, `tc`, and `hostapd`).

---

## 1. System at a Glance

```text
Phone hotspot ("1+ Four") ──Wi-Fi uplink── Raspberry Pi 5 "ina-router" ──Wi-Fi AP "INA-Router"── client devices
                                           wlan0 uplink, ap0 AP 192.168.50.1/24      DHCP 192.168.50.10–100
                                           NAT: nftables masquerade                  fixed IPs .2–.9 (registered)

Operator laptop                                       Raspberry Pi 5 (ina-router)
───────────────                                       ─────────────────────────────────────────────────────────
request (NL) → LLM picks MCP tool ─HTTPS /mcp, Bearer→ ina-mcp.service (user ina): schema check → policy guard
                                                        (token → person, role, bound device) → allowed only:
                                                        sudo -n ina-netctl ──► root: nft / tc / hostapd_cli
                                                                               verify → rollback → state → logs
reply (NL) ← LLM ← structured result ◄─────────────── structured result (no commands) + request ID
```

### Core Design Principles
1. **The AI Proposes, The Policy Decides, The Kernel Proves:** The AI assistant possesses zero administrative privileges and never executes raw shell commands.
2. **Dual-Plane Split Architecture:** The Control Plane (operator laptop with LLM backends) is cleanly decoupled from the Data Plane (Raspberry Pi 5 router).
3. **Defense-in-Depth Privilege Separation:** The MCP server daemon runs under an unprivileged user (`ina`). Privileged kernel mutations execute exclusively through a strictly contained helper (`ina-netctl`) governed by sudoers rules.
4. **Hardware Anti-Spoofing & Protected Infrastructure:** Static DHCP reservations are linked directly to `nftables` hardware MAC-IP sets. Critical infrastructure (gateway, admin laptops, monitoring probes) cannot be blocked or throttled.
5. **Read-Back Verification & Atomic Rollback:** Every mutating network call queries the live kernel state to verify enforcement; failed operations are automatically rolled back.

---

## 2. Codebase Directory Layout

```text
code/
├── assistant/              # Control-plane AI assistant & operator CLI
│   ├── client.py           # Orchestration loop, clarification handling, result presentation
│   ├── config.py           # Environment and .env configuration loader
│   ├── contract.py         # Data classes: Intent, ToolCall, ExecutionResult, Decision
│   ├── intent.py           # Deterministic, rule-based natural language parser (fallback)
│   ├── llm.py              # LLM adapters (Claude, Gemini, OpenAI-compatible, Ollama)
│   ├── logger.py           # Request logger recording requests to logs/requests.log & requests.jsonl
│   ├── prompts.py          # System prompt with tool definitions and security constraints
│   ├── result_view.py      # Sanitized result formatting (strips raw commands & shell traces)
│   └── transport.py        # Streamable HTTP MCP client (TLS), stdio, and in-memory mock transport
├── config/                 # Deployment configurations & system service templates
│   ├── dnsmasq/            # DHCP/DNS configuration template (ina-ap.conf)
│   ├── hostapd/            # Wi-Fi AP configuration template (hostapd.conf.template)
│   ├── modules-load.d/     # Kernel module loader configuration (ifb, sch_htb)
│   ├── NetworkManager/     # Drop-in to keep virtual interface ap0 unmanaged
│   ├── nftables/           # Base NAT masquerade rules and filter definitions
│   ├── scripts/            # Helper scripts (ina-ap-interface.sh, ina-netctl, ina-mcp)
│   ├── sudoers.d/          # Privilege boundary sudoers configuration for user 'ina'
│   ├── systemd/            # Systemd service units (ina-mcp, ina-reconcile, ina-expire)
│   └── tmpfiles.d/         # Runtime tmpfiles configuration for volatile socket paths
├── docs/                   # Consolidated technical documentation and runbooks
│   ├── architecture.md     # Detailed 14-step request pipeline, topology, and threat boundaries
│   ├── implementation.md   # MCP server, 8 canonical tools, network modules, and policy engine
│   ├── security.md         # Privilege boundary (ina-netctl), RBAC, anti-spoofing, and audit logging
│   ├── setup_and_deployment.md # Complete Pi and laptop installation runbooks & config reference
│   ├── testing_and_evaluation.md # Hardware test records, lab runs, unit tests, and eval benchmark
│   ├── troubleshooting.md  # 25 concrete diagnostic cases and remediation procedures
│   └── live_router_test_report.md # Empirical live MCP test suite (75 functional & 10 non-functional runs)
├── eval/                   # Automated evaluation suite
│   ├── cases.py            # Test cases: 16 functional, 10 clarification, 13 security attacks
│   ├── results/            # Evaluator output CSVs (functional.csv, clarification.csv, attacks.csv)
│   └── run_eval.py         # Evaluation runner comparing LLM vs rule-based parser
├── inav2/                  # Package compatibility shim
│   └── __init__.py         # Dynamic aliasing finder ensuring backwards compatibility
├── logs/                   # Directory for runtime logs (requests.log, requests.jsonl, .gitkeep)
├── mcp_server/             # Official Model Context Protocol server
│   ├── auth.py             # Bearer token verifier and device-bound grant validator
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
├── tests/                  # Complete test suite (342 tests)
│   ├── conftest.py         # Pytest configuration
│   ├── lab/                # Real-kernel network namespace lab (ina_block_lab.sh)
│   ├── run_all_tests.py    # Test runner executing all unit and integration tests
│   └── unit/               # 20 unit test suites testing all components in isolation
├── pyproject.toml          # PEP 518/621 project configuration
├── requirements.txt        # Core dependencies (mcp==2.3.0)
└── .env.example            # Environment configuration template
```

---

## 3. Quick Start (Local & Laptop Development)

No Raspberry Pi hardware is required for local testing or evaluation. The assistant runs against an in-memory mock network server by default.

### 3.1 Setup Environment

```bash
# Inside the code/ directory:
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3.2 Run Test Suites & Evaluation Benchmark

```bash
# Run unit and integration tests (342 tests, mocked kernel, no root needed)
python3 tests/run_all_tests.py

# Run evaluation suite (16 functional, 10 clarification, 65 attack runs)
python3 -m eval.run_eval
```

### 3.3 Run the Operator Assistant (Local Mock Mode)

```bash
# One-shot command against mock network:
python3 -m assistant "list devices"
python3 -m assistant "limit guest-phone to 5 mbps"
python3 -m assistant "block guest-phone"
python3 -m assistant "restore guest-phone"

# Interactive CLI mode:
python3 -m assistant
```
```text
ina> list devices
ina> block guest-phone
ina> exit
```

### 3.4 Enable Live LLM Providers (Optional)

Copy `.env.example` to `.env` and set your preferred provider credentials:

```bash
cp .env.example .env
```
```ini
# Google Gemini (Standard library HTTP adapter)
LLM_PROVIDER=gemini
LLM_API_KEY=AIzaSy...
LLM_MODEL=gemini-2.5-flash

# Anthropic Claude (requires: pip install anthropic)
# LLM_PROVIDER=anthropic
# LLM_API_KEY=sk-ant-...

# Local Ollama (Offline LLM)
# LLM_PROVIDER=ollama
# LLM_MODEL=llama3.2
```
*Note: If no API key is set, the assistant automatically falls back to its deterministic rule-based natural language parser (`intent.py`), ensuring 100% offline functionality.*

---

## 4. Raspberry Pi 5 Router Deployment

To deploy the data plane onto a physical **Raspberry Pi 5** running Raspberry Pi OS Lite (64-bit):

### 4.1 Prerequisites Installation
```bash
sudo apt update && sudo apt install -y hostapd dnsmasq nftables iw iperf3 ethtool git python3-venv openssl curl
sudo systemctl unmask hostapd
```

### 4.2 Clone Codebase and Setup Virtual Environment
```bash
sudo install -d -o root -g root -m 0755 /opt/ina
sudo git clone https://github.com/vedantb7/inav2.git /opt/ina/inav2
cd /opt/ina/inav2/code

sudo chown -R root:root /opt/ina
sudo python3 -m venv /opt/ina/venv
sudo /opt/ina/venv/bin/pip install --upgrade pip
sudo /opt/ina/venv/bin/pip install -r requirements.txt
```

### 4.3 Configure Access Point & DHCP
```bash
cd /opt/ina/inav2/code

# hostapd configuration
sudo install -m 0600 config/hostapd/hostapd.conf.template /etc/hostapd/hostapd.conf
sudo sed -i 's|^wpa_passphrase=.*|wpa_passphrase=MySecurePassphrase123|' /etc/hostapd/hostapd.conf
sudo install -o root -g root -m 0600 /dev/null /etc/hostapd/hostapd.deny
sudo sed -i 's|^#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd

# dnsmasq configuration
sudo mv /etc/dnsmasq.conf /etc/dnsmasq.conf.bak 2>/dev/null || true
sudo install -m 0644 config/dnsmasq/ina-ap.conf /etc/dnsmasq.d/ina-ap.conf
sudo install -d -o root -g root -m 0755 /etc/ina
sudo install -m 0644 /dev/null /etc/ina/dhcp-hosts

# Virtual AP interface script and NetworkManager unmanaged drop-in
sudo install -o root -g root -m 0644 config/NetworkManager/90-ina-unmanaged.conf /etc/NetworkManager/conf.d/
sudo systemctl reload NetworkManager
sudo install -o root -g root -m 0755 config/scripts/ina-ap-interface.sh /usr/local/sbin/ina-ap-interface.sh
```

### 4.4 Configure Routing, NAT & Kernel Modules
```bash
cd /opt/ina/inav2/code

# Persistent IPv4 forwarding
echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/99-ina-router.conf
sudo sysctl --system

# Load traffic control & IFB modules
sudo install -m 0644 config/modules-load.d/ina.conf /etc/modules-load.d/ina.conf
sudo systemctl restart systemd-modules-load

# Base NAT masquerade
sudo install -m 0644 config/nftables/nftables.conf /etc/nftables.conf
sudo systemctl enable --now nftables.service
```

### 4.5 Privilege Boundary & MCP System Services
```bash
cd /opt/ina/inav2/code

# Dedicated unprivileged user
sudo useradd --system --create-home --shell /bin/sh ina 2>/dev/null || true

# Helper scripts & sudoers rule
sudo install -o root -g root -m 0755 config/scripts/ina-netctl /usr/local/sbin/ina-netctl
sudo install -o root -g root -m 0755 config/scripts/ina-mcp    /usr/local/bin/ina-mcp
sudo install -o root -g root -m 0440 config/sudoers.d/ina     /etc/sudoers.d/ina
sudo install -m 0644 config/tmpfiles.d/ina.conf               /etc/tmpfiles.d/ina.conf
sudo systemctl-tmpfiles --create /etc/tmpfiles.d/ina.conf 2>/dev/null || true

# Systemd units installation
sudo install -m 0644 config/systemd/*.service config/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ina-ap-interface.service hostapd.service dnsmasq.service
sudo systemctl enable --now ina-reconcile.service ina-expire.timer ina-mcp.service
```

### 4.6 Device Registration & Role Grants
```bash
# Synchronize device registry to dhcp-hosts and nftables anti-spoofing set
sudo ina-netctl sync-devices

# Grant admin role bound to admin laptop
sudo install -o root -g ina -m 0640 /dev/null /etc/ina/grants.json
sudo ina-netctl roles grant admin_user admin --device admin-laptop

# Generate TLS certificate for HTTPS MCP
sudo install -d -o root -g ina -m 0750 /etc/ina/tls
sudo openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 365 \
    -subj "/CN=ina-router" \
    -addext "subjectAltName=IP:192.168.50.1,DNS:ina-router,DNS:ina-router.local" \
    -keyout /etc/ina/tls/server.key -out /etc/ina/tls/server.crt
sudo chown root:ina /etc/ina/tls/server.key /etc/ina/tls/server.crt
sudo chmod 0640 /etc/ina/tls/server.key
sudo chmod 0644 /etc/ina/tls/server.crt
sudo systemctl restart ina-mcp.service
```

### 4.7 Connect Operator Laptop to Live Router
On the operator laptop connected to Wi-Fi `INA-Router`:
```bash
# Retrieve public certificate from Pi
mkdir -p ~/.config/ina
scp ina@192.168.50.1:/etc/ina/tls/server.crt ~/.config/ina/ina-router.crt

# Run assistant against live Pi MCP endpoint
cd code
INA_ROLE_TOKEN="<your-admin-token>" \
INA_MCP_CA="$HOME/.config/ina/ina-router.crt" \
python3 -m assistant --server mcp --mcp-url https://192.168.50.1:8443/mcp "list devices"
```

---

## 5. The 8 Canonical MCP Tools

The Model Context Protocol server exposes exactly **8 strongly typed tools** (`mcp_server/tools.py`):

| Tool Name | Parameters | Permitted Roles | Description & Action |
|---|---|---|---|
| `get_status` | `detailed: bool = False` | `viewer`, `operator`, `admin` | Returns connected Wi-Fi stations, active DHCP leases, active blocks, and bandwidth limits. |
| `block_client` | `client: str`, `target: str` | `operator`, `admin` | Blocks client traffic to `internet`, `gateway`, `all`, or a peer client via `nftables`. |
| `unblock_client` | `client: str`, `target: str = "all"` | `operator`, `admin` | Removes drop elements from `nftables` sets, restoring access. |
| `limit_bandwidth` | `client: str`, `download_kbit: int`, `upload_kbit: int` | `operator`, `admin` | Imposes bandwidth shaping via `tc` HTB classes on `ap0` and ingress `ifb-ap0`. |
| `remove_bandwidth_limit` | `client: str` | `operator`, `admin` | Deletes `tc` filters and HTB classes, restoring full throughput. |
| `kick_client` | `client: str`, `reason: str` | `operator`, `admin` | Disconnects station immediately via `hostapd_cli deauthenticate`. |
| `ban_client` | `client: str`, `duration_minutes: int`, `confirm: bool` | `operator` (<=60m), `admin` (any) | Adds MAC to `/etc/hostapd/hostapd.deny` and deauthenticates. Requires confirmation. |
| `unban_client` | `client: str` | `operator` (own), `admin` (any) | Removes MAC from `/etc/hostapd/hostapd.deny` and unbans station. |

---

## 6. Security Architecture & Threat Defense

INA v2 implements **Defense in Depth**:

```text
       Natural Language Request
                  │
                  ▼
   ┌──────────────────────────────┐
   │ 1. Assistant Prompt Guard    │ ──► Refuses raw shell syntax, validates parameter boundaries
   └──────────────┬───────────────┘
                  │
                  ▼
   ┌──────────────────────────────┐
   │ 2. MCP Pydantic Schema       │ ──► Enforces type safety, regex sanitization (MACs, IPs)
   └──────────────┬───────────────┘
                  │
                  ▼
   ┌──────────────────────────────┐
   │ 3. Authoritative Policy Guard│ ──► Enforces token-to-role grants, device bindings, rate ranges,
   └──────────────┬───────────────┘     and permanent protected device immunity
                  │
                  ▼
   ┌──────────────────────────────┐
   │ 4. Privilege Boundary        │ ──► Unprivileged 'ina' user invokes strict sudo 'ina-netctl'
   └──────────────┬───────────────┘     Isolated Python (-I), whitelisted subcommands, shell=False
                  │
                  ▼
   ┌──────────────────────────────┐
   │ 5. Linux Kernel Enforcement  │ ──► nftables sets, tc HTB queues, hostapd deny ACLs
   └──────────────────────────────┘
```

- **Prompt Injection Defense:** Raw user text is never concatenated into shell commands.
- **Cryptographic Audit Log:** All commands and outputs are logged to `/var/log/ina/commands.jsonl` with SHA-256 hash chaining to detect any tampering.
- **Anti-Spoofing:** Kernel drops packets where static IPs originate from unauthorized MAC addresses (`mac_ip_bind` set).

---

## 7. Testing & Evaluation Summary

### 7.1 Automated Unit & Integration Suite
```bash
python3 tests/run_all_tests.py
```
- **342 tests executed cleanly** (0 failures, 0 errors, 25 skipped cleanly on system python without optional `mcp` SDK; 340 passed in virtual environment).

### 7.2 Evaluation Benchmark (`eval/run_eval.py`)
```bash
python3 -m eval.run_eval
```
| Benchmark Category | Cases | Success Rate | Empirical Result |
|---|---|---|---|
| **[E2] Functional Correctness** | 16 cases | **100.0% (16/16)** | Correct MCP tools called and executed for all clear prompts. |
| **[E8] Clarification Handling** | 10 cases | **100.0% (10/10)** | Assistant asked clarifying questions rather than guessing incomplete inputs. |
| **[E3] Adversarial Attacks** | 65 runs (13 attack types x 5) | **100.0% Defended** | **0 unsafe executions.** 100% of injection attempts were blocked by the policy engine. |

### 7.3 Real-Kernel Lab Testing (No Root Needed)
```bash
INA_RUN_LAB=1 python3 -m unittest inav2.tests.unit.test_router_config
```
Validates packet drops and bridge port isolation using Linux unprivileged network namespaces (`unshare`).

---

## 8. Technical Documentation Index

All technical guides and runbooks are organized in `docs/`:

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Topology, packet flow, systemd dependencies, and 14-step request lifecycle |
| [docs/setup_and_deployment.md](docs/setup_and_deployment.md) | Complete Raspberry Pi and laptop runbooks + infrastructure reference |
| [docs/implementation.md](docs/implementation.md) | Assistant, MCP server, 8 canonical tools, network modules, and policy engine |
| [docs/security.md](docs/security.md) | Privilege boundary (`ina-netctl`), RBAC, anti-spoofing, and hash-chained audit logging |
| [docs/testing_and_evaluation.md](docs/testing_and_evaluation.md) | Hardware benchmark records, namespace lab, 342 unit tests, and eval benchmark |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Diagnostics and triage procedures for 25 concrete failure cases |
| [docs/live_router_test_report.md](docs/live_router_test_report.md) | Empirical live MCP test suite (75 functional & 10 non-functional tests on Raspberry Pi 5) |
