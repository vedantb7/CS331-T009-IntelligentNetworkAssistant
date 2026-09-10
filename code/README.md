# Intelligent Network Configuration Assistant (INA)

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Docker Compose](https://img.shields.io/badge/docker--compose-v2%2B-blue.svg)](https://docs.docker.com/compose/)
[![FastMCP](https://img.shields.io/badge/FastMCP-v3.4.7-green.svg)](https://github.com/jlowin/fastmcp)
[![Tests](https://img.shields.io/badge/tests-128%20passed-brightgreen.svg)](#-testing--automated-verification)

The **Intelligent Network Configuration Assistant (INA)** is an AI-orchestrated, policy-governed network automation system designed for containerized local area networks. It translates high-level natural language operator commands (e.g., *"block client1"*, *"limit client2 to 10 Mbps"*, or *"check network status"*) into safe, deterministic Linux kernel networking primitives (`nftables`, `tc`/IFB) with active physical validation and tamper-evident audit logging.

---

## 🎯 Problem Statement & Purpose

Managing container networks traditionally requires deep manual knowledge of Linux networking subsystems (`iproute2`, `tc`, `nftables`, `iptables`), namespace isolation, and complex traffic control syntax. 

Directly connecting Large Language Models (LLMs) to terminal environments is dangerous because models can hallucinate commands, disrupt critical infrastructure, or expose systems to injection vulnerabilities.

**INA solves this by introducing a strict zero-trust boundary**:
- **LLMs as Classifiers**: The AI model is strictly an intent classifier, never an unrestricted command generator.
- **Deterministic Policy Gating**: Every proposed action is vetted against declarative rules before execution.
- **Standardized Control Bridge**: Network changes execute via the Model Context Protocol (FastMCP) with parameter sanitization and atomic rollback.
- **Physical Validation**: Post-execution state is tested using live synthetic traffic (cross-bridge ICMP pings and `iperf3` throughput measurements).

---

## ⚡ Key Features

* **AI-Assisted Natural Language Control**: Natural conversations and network configuration commands handled naturally with OpenRouter LLMs (and built-in offline regex fallback).
* **Zero-Trust Policy Engine**: Declarative safety rules in `policy/rules.yaml` preventing unauthorized actions, protecting critical nodes (`server`), and bounding rates (1–20 Mbps).
* **Model Context Protocol (FastMCP) Tools**: Standardized network management interface exposing parameterized `block`, `unblock`, `limit`, and `status` primitives.
* **Layer-2 Firewalling**: Linux `nftables` bridge filtering on forwarded container frames with $O(1)$ set lookups (`@blocked_clients`).
* **Bi-Directional Traffic Shaping**: Symmetrical ingress and egress rate limiting using Linux `tc` Token Bucket Filters (TBF) and Intermediate Functional Block (`ifb0`) redirection.
* **Empirical Network Validation**: Active verification using cross-bridge ping loss (100% loss for block, 0% for unblock) and dual-direction `iperf3` throughput tests within ±20% tolerance.
* **Immutable Audit Trail**: Append-only JSON Lines logging (`policy/audit_log.jsonl`) recording `ALLOWED`, `DENIED`, `APPLIED`, and `FAILED` events with explainability support.

---

## 🏗️ High-Level Architecture

```text
[ User / Operator ]
        │
        ▼ (Natural Language)
┌─────────────────────────────────────────────────────────┐
│ 1. Interactive Assistant CLI (assistant/client.py)      │
│    - OpenRouter LLM Intent Parser (Regex Fallback)      │
│    - Conversational Handler & Rich Terminal UI          │
└───────────────────────────┬─────────────────────────────┘
                            │ Structured Intent
                            ▼
┌─────────────────────────────────────────────────────────┐
│ 2. Policy Engine (policy/policy_engine.py)              │
│    - Rulebook Whitelisting (policy/rules.yaml)          │
│    - Rate Boundaries & Protected Client Immutability    │
└───────────────────────────┬─────────────────────────────┘
                            │ ALLOWED / DENIED
                            ▼
┌─────────────────────────────────────────────────────────┐
│ 3. FastMCP Server Bridge (mcp_server/tools.py)          │
│    - Input Sanitization & Anti-Injection Guards         │
│    - Independent Policy Re-Check                        │
│    - Audit Logging (policy/audit_log.jsonl)             │
└───────────────────────────┬─────────────────────────────┘
                            │ Parameterized Subprocess (No shell=True)
                            ▼
┌─────────────────────────────────────────────────────────┐
│ 4. Docker Network Infrastructure (network/compose)      │
│    - network-controller: nftables L2 bridge filtering   │
│    - client1 / client2: tc tbf + ifb0 shaping           │
│    - server: Protected HTTP :5000 & iperf3 :5201 daemon │
└───────────────────────────┬─────────────────────────────┘
                            │ Physical Packet Flow
                            ▼
┌─────────────────────────────────────────────────────────┐
│ 5. Validation Monitor (validation/monitor.py)           │
│    - Peer Container Cross-Bridge Ping Loss Check        │
│    - Symmetrical iperf3 Bandwidth Benchmark             │
└─────────────────────────────────────────────────────────┘
```

---

## 📁 Project Directory Structure

```text
ina/
├── assistant/
│   └── client.py              # Interactive CLI client, LLM parser & orchestrator
├── mcp_server/
│   ├── server.py              # FastMCP server tool definitions
│   └── tools.py               # Kernel execution logic, validation & rollback
├── policy/
│   ├── policy_engine.py       # Deterministic policy checking logic
│   ├── rules.yaml             # Declarative rules (protected clients, bounds)
│   ├── audit_log.py           # Audit trail logger and natural language explainer
│   └── audit_log.jsonl        # Append-only chronological audit records
├── validation/
│   ├── monitor.py             # Active validation engine (ping & iperf3 tests)
│   └── models.py              # Pydantic v2 validation data contracts
├── network/
│   ├── docker-compose.yml     # Container network topology (172.20.0.0/24)
│   ├── Dockerfile             # Base networking image (iproute2, nftables, etc.)
│   ├── discovery.py           # Dynamic Docker Engine API container discovery
│   └── setup.sh               # Automated bootstrap & baseline verification script
├── tests/
│   ├── test_ina_focused.py    # 10-area focused deterministic & E2E test suite (35 tests)
│   ├── test_policy.py         # Policy authorization and rule bounds tests (23 tests)
│   ├── test_validation.py     # Pydantic models & monitor algorithm tests (32 tests)
│   ├── test_network.py        # Live Docker bridge & container integration tests (20 tests)
│   ├── test_mcp.py            # FastMCP tool interface tests (13 tests)
│   └── test_discovery.py      # Docker SDK dynamic discovery tests (5 tests)
├── docs/                      # Comprehensive technical documentation
├── reports/                   # Academic deep-dive reports & viva guides
├── requirements.txt           # Python package dependencies
└── .env.example               # Environment variable configuration template
```

---

## 💻 Technology Stack

* **Language & Runtime**: Python 3.10+ (tested on Python 3.12).
* **Tool Abstraction**: FastMCP (Model Context Protocol).
* **Containerization**: Docker Engine & Docker Compose V2.
* **Kernel Networking**:
  - `nftables`: Layer-2 bridge table forwarding filter (`network_filter`) and sets (`@blocked_clients`).
  - `tc` (Traffic Control): Token Bucket Filter (`tbf`) queuing disciplines.
  - `ifb` (Intermediate Functional Block): Virtual device for ingress traffic redirection via `mirred`.
  - `iproute2` & `iputils-ping`: Network namespace management and ICMP diagnostics.
* **Bandwidth Benchmarking**: `iperf3` (TCP throughput measurement).
* **Data Validation & UI**: `pydantic` v2, `pyyaml`, and `rich`.
* **Testing**: `pytest` and `unittest.mock`.

---

## 🚀 Quick Start Guide

### 1. Prerequisites Check
Ensure Python 3.10+, Docker, and Docker Compose V2 are installed:
```bash
python3 --version
docker --version
docker compose version
```
*(No interactive `sudo` required; ensure user is added to the `docker` group).*

### 2. Install Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure API Credentials (Optional)
```bash
cp .env.example .env
# Add your OPENROUTER_API_KEY in .env, or skip to use built-in offline regex parsing
```

### 4. Start the Docker Lab Network
```bash
docker compose -f network/docker-compose.yml up -d --build
```
Verify 4 containers running (`server`, `client1`, `client2`, `network-controller`):
```bash
docker compose -f network/docker-compose.yml ps
```

### 5. Launch the Assistant CLI
```bash
python3 -m assistant.client
```

---

## 💬 Example Operations & Commands

The assistant processes both natural-language commands and standard keywords:

| User Input | Action Taken | Expected Result |
| :--- | :--- | :--- |
| `help` | Informational | Displays interactive command reference table. |
| `status` | Discovery / Query | Synthesizes live reachability, active block rules, and bandwidth limits. |
| `block client1` | Firewall Isolation | Adds IP to `nftables` bridge set; confirmed by 100% cross-bridge packet loss. |
| `unblock client1` | Firewall Restoration | Removes IP from bridge set and tears down lingering `tc` qdiscs; confirmed reachable. |
| `limit client1 to 10 Mbps` | Traffic Shaping | Applies symmetrical TBF on `eth0` and `ifb0`; verified by live `iperf3` benchmark. |
| `block server` | Policy Denial | **Denied**: `server` is protected by `rules.yaml`. |
| `limit client1 to 50 Mbps` | Policy Denial | **Denied**: Exceeds maximum authorized rate (20 Mbps). |

---

## 🧪 Testing & Automated Verification

INA contains **128 automated tests** covering unit logic, parameter construction, injection prevention, error handling, live container connectivity, and asynchronous pipeline flow:

```bash
# Run the complete test suite (128 passing tests):
pytest -v

# Run the 10-area focused deterministic test suite:
pytest tests/test_ina_focused.py -v
```
*(All 128 tests execute in ~11.5 seconds).*

---

## 📚 Technical Documentation Index

Comprehensive guides covering every subsystem are available in the [`docs/`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/docs/) directory:

- [Setup & Operations Guide](docs/INA_SETUP.md) — Complete environment installation, usage, direct FastMCP calling, and troubleshooting.
- [System Architecture Specification](docs/architecture.md) — Architectural responsibilities, request lifecycle, data contracts, and pipeline flow.
- [MCP Server Specification](docs/mcpserver.md) — FastMCP tool contracts, parameter schemas, kernel commands, and atomic rollback.
- [Docker & Linux Network Guide](docs/network.md) — Bridge topology (`172.20.0.0/24`), interface routing, and container privileges.
- [Policy Engine & Security Governance](docs/policy.md) — Zero-trust rules engine, declarative rulebook, and parameter bounds.
- [Validation & Monitoring Guide](docs/validation.md) — Post-execution physical validation, cross-bridge pings, and `iperf3` benchmarking.
- [Security Architecture & Governance](docs/security.md) — Threat model, defense-in-depth, anti-injection sanitization, and residual risks.
- [Testing Strategy & Test Suite Guide](docs/testing.md) — Test taxonomy, mocking techniques, edge cases, and failure investigation.
- [Audit Logging & Traceability](docs/logging.md) — Append-only JSONL event ledger schema, state transitions, and explainability.

---

## 👥 Academic Team & Subsystem Ownership

This project was built for CS 331 (Computer Networks):

* **MCP Server & Linux Kernel Enforcement**: Vedant
* **Policy Engine & Audit Governance**: Khushi
* **Network Validation & Infrastructure**: Dhruv
* **Interactive Assistant & Pipeline Orchestration**: Collaborative

---

## ⚠️ Known Limitations & Future Work

* **Docker Socket Attack Surface**: Access to `/var/run/docker.sock` provides elevated control over the host Docker daemon; future revisions will incorporate a restricted API proxy.
* **Shared Bridge Layer-2 Weakness**: The bridge does not implement private VLANs (PVLAN); containers share an unsegmented broadcast domain.
* **Log Rotation**: `policy/audit_log.jsonl` is append-only and currently lacks automatic size-based rotation.
* **iperf3 Server Concurrency**: Validation tests execute sequentially to avoid socket collisions on the single `iperf3` listener daemon.
