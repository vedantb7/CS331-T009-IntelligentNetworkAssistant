# Intelligent Network Configuration Assistant v2 (INA v2)

INA v2 is an intelligent network configuration and policy management system built as a Computer Networks project. It enables administrators to manage, monitor, and secure a dedicated test network using natural-language instructions through the Model Context Protocol (MCP).

Initially deployed on a Fedora laptop test environment, the system is designed with portability in mind for potential future migration to edge devices like a Raspberry Pi.

> **Project Status:** Initial Repository Skeleton. Application logic, network drivers, and runtime dependencies have not yet been implemented.

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
│   ├── device_discovery.py  # Host and device discovery on managed subnet
│   ├── access_control.py    # Firewall, MAC filtering, and quarantine controls
│   ├── bandwidth.py         # Bandwidth limits and QoS traffic shaping
│   └── verification.py      # Post-execution state verification
├── config/                  # Configuration files
│   └── settings.yaml        # System and environment settings (placeholder)
├── tests/                   # Automated test suites
│   ├── unit/                # Unit tests for isolated components
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

