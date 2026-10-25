# Intelligent Network Configuration Assistant v2 (INA v2)

INA v2 is an intelligent network configuration and policy management system built as a Computer Networks project. It enables administrators to manage, monitor, and secure a dedicated test network using natural-language instructions through the Model Context Protocol (MCP).

Initially deployed on a Fedora laptop test environment, the system is designed with portability in mind for potential future migration to edge devices like a Raspberry Pi.

> **Project Status:** Milestone 2.1 — AP Configuration & Preflight Diagnostics Refined.
> Host network discovery, dynamic interface resolution, and safe NetworkManager AP profile management are operational and covered by unit tests. Forwarding rules, firewall filtering, and policy enforcement are pending future milestones.

---

## Quick Start

### 1. Read-Only Host Network Discovery
Inspect host network interfaces, active routes, and wireless driver AP support:

```bash
# Print human-readable network discovery report
python3 -m network.device_discovery

# Export discovery data as structured JSON
python3 -m network.device_discovery --json
```

### 2. Access Point Profile Management (Safe & Dry-Run by Default)
Inspect AP profile status, run pre-flight safety checks, and preview planned `nmcli` operations. Target interface is resolved dynamically via strict precedence (CLI `--interface` > `config/settings.yaml` > `NETWORK_INTERFACE` > unambiguous discovery), and active uplink interfaces are never selected automatically:

```bash
# Inspect AP profile status and pre-flight safety diagnostics
python3 -m network.access_point status

# Inspect status targeting an explicit interface (e.g., secondary Wi-Fi dongle)
python3 -m network.access_point status --interface wlan1

# Preview AP profile creation (dry-run mode, safe)
python3 -m network.access_point create --ssid INA-Test-Network --password YourSecretPassphrase123

# Live creation (requires --no-dry-run)
python3 -m network.access_point create --ssid INA-Test-Network --password YourSecretPassphrase123 --no-dry-run
```

### 3. Run Unit Tests (No Root or External Dependencies Required)
```bash
python3 -m unittest discover tests/unit -v
```

---

## High-Level Architecture

The system consists of five primary layers:

1. **Natural-Language Assistant (`assistant/`):** MCP client layer that translates operator intentions into validated tool invocations and structures conversational workflows.
2. **FastMCP Server (`mcp_server/`):** High-performance MCP server exposing network management primitives via typed Pydantic schemas.
3. **Policy and Authorization Engine (`policy/`):** Decoupled engine enforcing role-based access control (`roles.yaml`) and declarative network safety rules (`rules.yaml`).
4. **Network Operations (`network/`):** Core network modules executing device discovery, access control, traffic shaping, and post-action verification on the managed network.
5. **Configuration and Infrastructure (`config/`, `tests/`, `scripts/`, `docs/`):** Declarative settings, test suites, utilities, and architectural documentation.

---

## Directory Structure & Responsibilities

```text
intelligent-network-assistant-v2/
├── assistant/               # Natural language assistant and MCP client interface
│   ├── __init__.py
│   ├── client.py            # MCP client connection and request dispatcher
│   └── prompts.py           # System prompts and conversational templates
├── mcp_server/              # FastMCP server and tool registry
│   ├── __init__.py
│   ├── server.py            # MCP server setup and lifecycle management
│   ├── tools.py             # Tool implementations exposed to the assistant
│   └── schemas.py           # Pydantic schemas for tool inputs and outputs
├── policy/                  # Policy enforcement and RBAC engine
│   ├── __init__.py
│   ├── engine.py            # Policy evaluation logic
│   ├── authorization.py     # Role-based authorization checks
│   ├── rules.yaml           # Security and operational policy definitions (placeholder)
│   └── roles.yaml           # User/agent roles and permissions (placeholder)
├── network/                 # Network management and execution modules
│   ├── device_discovery.py  # Host discovery & interface/routing inspection (Implemented)
│   ├── access_point.py      # NetworkManager AP profile lifecycle & safety (Implemented)
│   ├── access_control.py    # Firewall, MAC filtering, and quarantine controls
│   ├── bandwidth.py         # Bandwidth limits and QoS traffic shaping
│   └── verification.py      # Post-execution state verification
├── config/                  # Configuration files
│   └── settings.yaml        # System and environment settings (placeholder)
├── tests/                   # Automated test suites
│   ├── unit/                # Unit tests (test_device_discovery, test_access_point)
│   ├── integration/         # End-to-end and MCP integration tests
│   └── conftest.py          # Pytest fixtures and shared testing utilities
├── scripts/                 # Operational and maintenance helper scripts
├── docs/                    # Project documentation
│   ├── architecture.md      # Detailed system architecture reference
│   └── setup.md             # Development setup and environment guide
├── logs/                    # Application and runtime logs (.gitkeep tracked)
├── .env.example             # Example environment variable template
├── .gitignore               # Git ignore rules for Python, virtualenvs, logs
├── requirements.txt         # Project dependencies (comment-only skeleton)
├── pyproject.toml           # Project metadata and pytest configuration
└── README.md                # Project overview and directory map
```

---

## Development & Setup

For prerequisite details and environment configuration instructions, see [`docs/setup.md`](docs/setup.md) and [`docs/architecture.md`](docs/architecture.md).

