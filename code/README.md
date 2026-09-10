# Intelligent Network Configuration Assistant (INA)

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Docker Compose](https://img.shields.io/badge/docker--compose-v2%2B-blue.svg)](https://docs.docker.com/compose/)
[![FastMCP](https://img.shields.io/badge/FastMCP-v3.4.7-green.svg)](https://github.com/jlowin/fastmcp)
[![Tests](https://img.shields.io/badge/tests-86%20passed-brightgreen.svg)](#-testing--verification)

The **Intelligent Network Configuration Assistant (INA)** is an AI-agentic orchestration system that translates natural-language operator requests into verified, low-level Linux kernel network modifications across Docker container networks.

---

## 🏗️ System Architecture & Multi-Layer Pipeline

INA implements a modular, defense-in-depth pipeline connecting human intent to kernel enforcement:

```text
[ Natural Language Request ]
            │
            ▼
┌─────────────────────────────────────────────────────────┐
│ 1. Intent Parsing Layer (assistant/client.py)           │
│    - Cloud LLM (OpenRouter / Anthropic / OpenAI / LiteLLM) │
│    - Deterministic Regex Fallback (0% Cloud Dependency) │
└───────────────────────────┬─────────────────────────────┘
                            │ Structured Intent
                            ▼
┌─────────────────────────────────────────────────────────┐
│ 2. Zero-Trust Policy Engine (policy/policy_engine.py)   │
│    - Rule Evaluation (policy/rules.yaml)                │
│    - Governance & Audit Logging (policy/audit_log.jsonl) │
└───────────────────────────┬─────────────────────────────┘
                            │ ALLOW / DENY
                            ▼
┌─────────────────────────────────────────────────────────┐
│ 3. FastMCP Kernel Enforcement (mcp_server/tools.py)    │
│    - Layer-2 nftables Bridge Set Filtering              │
│    - Bi-Directional tc tbf Bandwidth Shaping via IFB    │
│    - Dynamic Docker SDK Container Discovery             │
└───────────────────────────┬─────────────────────────────┘
                            │ Linux Netns Modifications
                            ▼
┌─────────────────────────────────────────────────────────┐
│ 4. Empirical Validation Engine (validation/monitor.py)   │
│    - Sibling Container ICMP Loss Verification           │
│    - Dual-Direction iperf3 Throughput Benchmarking      │
└─────────────────────────────────────────────────────────┘
```

---

## ⚡ Key Technical Innovations

1. **Dynamic Docker SDK Container Discovery**: Replaced hardcoded static IP mappings with real-time Docker Engine API queries (`docker.from_env()`), resolving client hostnames and active interfaces dynamically.
2. **Layer-2 `nftables` Bridge Set Filtering**: Executes $O(1)$ set lookups (`@blocked_clients`) hooked directly into L2 bridge forward hooks (`priority 0`), bypassing host L3 bypass limitations.
3. **Bi-Directional Traffic Control (`tc` + IFB)**: Shapes **both egress and ingress** traffic by creating Intermediate Functional Block (`ifb0`) pseudo-devices inside container netns and redirecting ingress frames via `tc filter mirred egress redirect`.
4. **Empirical Validation Convergence**: Actively benchmarks post-execution network state using ICMP packet loss and `iperf3` / `iperf3 -R` (reverse mode) JSON throughput validation before reporting success.

---

## 🚀 Quick Start Guide

### 1. Environment Setup
```bash
# Clone repository and navigate to root
cd /path/to/ina

# Create and activate Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure API Credentials
Copy the example environment template and add your OpenRouter (or Anthropic/OpenAI) API key:

```bash
cp .env.example .env
```

Edit `.env`:
```env
OPENROUTER_API_KEY=sk-or-v1-your-actual-api-key-here
```
*(Note: If no API key is set, INA automatically falls back to its deterministic regex intent parser.)*

### 3. Start Docker Lab Network Infrastructure
Spin up the custom Docker bridge network (`network_project-net`) containing `client1`, `client2`, `server`, and `network-controller`:

```bash
docker compose -f network/docker-compose.yml up -d --build
```

Verify running containers:
```bash
docker compose -f network/docker-compose.yml ps
```

### 4. Launch the Assistant CLI
```bash
python3 -m assistant.client
```

---

## 💬 Usage Examples

Run the interactive shell or pass direct natural-language commands:

```text
network-assistant>: client1 is causing trouble, kick them off the network
network-assistant>: unblock client1
network-assistant>: limit client1 bandwidth to 5 Mbps
network-assistant>: check status of client1
```

---

## 🧪 Testing & Verification

Execute the full suite of **86 automated unit and integration tests**:

```bash
source .venv/bin/activate
pytest -v
```

### Live Diagnostic Commands
```bash
# Inspect Layer-2 nftables bridge rules and blocked set elements
docker exec network-controller nft list ruleset

# Inspect active bi-directional tc qdiscs on client1
docker exec client1 tc qdisc show dev eth0
docker exec client1 tc qdisc show dev ifb0

# Measure egress throughput (client1 -> server)
docker exec client1 iperf3 -c 172.20.0.3 -t 5

# Measure ingress throughput (server -> client1 via reverse mode)
docker exec client1 iperf3 -c 172.20.0.3 -t 5 -R
```

---

## 📚 Technical Documentation & Reports

Detailed architectural documentation and viva examination guides are available in the repository:

* **[Setup & Operating Guide](docs/INA_SETUP.md)**: Full installation, configuration, and troubleshooting manual.
* **[MCP Tools & Enforcement Spec](docs/mcpserver.md)**: FastMCP server primitives and parameter schemas.
* **[Policy & Governance Guide](docs/policy.md)**: Zero-trust rules engine and JSONL audit logging.
* **[Report 01: Network Architecture](reports/01_network_architecture_and_infrastructure.md)**: Topology, Docker veth pairs, and bridge networking.
* **[Report 02: MCP & Kernel Enforcement](reports/02_mcp_server_and_kernel_enforcement.md)**: Deep dive into `nftables` L2 bridge sets and IFB `tc` shaping.
* **[Report 03: Policy Engine](reports/03_policy_engine_and_security_governance.md)**: Security policy evaluation and compliance logging.
* **[Report 04: Validation Layer](reports/04_network_validation_and_monitoring.md)**: Active ICMP and `iperf3` probing.
* **[Report 05: Edge Cases & Future Work](reports/05_edge_cases_limitations_and_future_improvements.md)**: eBPF/XDP filtering, IFB ingress solutions, and Docker SDK integration.


---

## 🧹 Cleanup

To stop and remove containers and virtual bridges:

```bash
docker compose -f network/docker-compose.yml down
```
