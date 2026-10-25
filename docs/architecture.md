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

## Current State

The repository is currently initialized as an architectural skeleton. Application logic and network integrations have not yet been implemented.

