# Intelligent Network Configuration Assistant (INA) — Setup & Usage Guide

---

## ⚡ Quick Start

Get INA running in under two minutes:

```bash
# 1. Set up virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure API credentials (optional: if omitted, deterministic regex parser is used)
cp .env.example .env

# 3. Start the Docker network topology
docker compose -f network/docker-compose.yml up -d --build

# 4. Launch the interactive Assistant CLI
python3 -m assistant.client
```

Inside the assistant, try typing:
* `help` — displays the command reference table.
* `status` — displays live network reachability and firewall states.
* `block client1` — isolates `client1` from the bridge network.
* `limit client1 to 10 Mbps` — throttles bi-directional traffic to 10 Mbps.
* `unblock client1` — restores full network access and clears limits.

---

## 1. Prerequisites

### Required Software
- **Operating System**: Linux (recommended due to direct Linux kernel bridge netfilter hooks and `tc` interface interactions) or macOS/Windows with Docker Desktop.
- **Python**: Python 3.10, 3.11, or 3.12.
- **Docker**: Docker Engine 20.10+ or Docker Desktop.
- **Docker Compose**: Docker Compose V2 (`docker compose` command syntax).

### System Privileges
- **No interactive `sudo` required on the host**: All privileged network operations are safely encapsulated inside the Docker containers:
  - `network-controller` runs with `privileged: true` and `network_mode: host` to manage bridge `nftables`.
  - Client containers (`client1`, `client2`, `server`) are assigned `cap_add: NET_ADMIN` for local `tc` traffic control.
- Ensure your host user has permissions to communicate with the Docker daemon (i.e. is added to the local `docker` group):
  ```bash
  sudo usermod -aG docker $USER
  newgrp docker
  ```

### API Credentials (Optional)
INA supports LLM-backed intent parsing via OpenRouter (or local OpenAI/Anthropic SDKs). If an API key is not provided, the Assistant automatically degrades gracefully to a built-in deterministic regex intent parser without crashing.

---

## 2. Project Setup

### Step 1: Clone or Navigate to the Project Root
```bash
cd /path/to/ina
```

### Step 2: Create & Activate Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Step 3: Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Core dependencies installed:
- `pyyaml`: Parses policy rulebook (`policy/rules.yaml`).
- `rich`: Formats interactive CLI tables, panels, and rule dividers.
- `pydantic`: Validates network validation requests and result models.
- `python-dotenv`: Automatically loads environment variables from `.env`.
- `openai`: Client for OpenRouter API intent classification.
- `docker`: Docker SDK for Python dynamic container and IP discovery.
- `pytest`: Complete test suite runner.

### Step 4: Configure Environment Variables
Copy the template configuration:
```bash
cp .env.example .env
```
Edit `.env` to add your OpenRouter key:
```env
OPENROUTER_API_KEY=sk-or-v1-your-actual-api-key-here
OPENROUTER_MODEL=google/gemini-2.5-flash
```
*(If left unset or with default placeholder text, INA runs entirely offline using local regex parsing).*

---

## 3. Starting the Docker Network

### Option A: Standard Docker Compose (Recommended)
Build and start the container topology in detached mode:
```bash
docker compose -f network/docker-compose.yml up -d --build
```

### Option B: Automated Setup Script
Run the automated bootstrap and health-check script:
```bash
chmod +x network/setup.sh
./network/setup.sh
```

### Verify Running Containers
Verify that all four containers are running:
```bash
docker compose -f network/docker-compose.yml ps
```
Expected output:
```text
NAME                 IMAGE             STATUS         PORTS
client1              network-client1   Up (healthy)   
client2              network-client2   Up (healthy)   
network-controller   network-controller Up (healthy)   
server               network-server    Up (healthy)   
```

---

## 4. Starting the INA Assistant

Launch the interactive terminal client:
```bash
python3 -m assistant.client
```

### How the Assistant Connects
1. **Intent Parsing**: Natural language input is sent to OpenRouter (via `openai.OpenAI(base_url="https://openrouter.ai/api/v1")`). If offline or unconfigured, the Assistant uses its local regex parser.
2. **Policy Evaluation**: The Assistant passes the parsed intent directly to `policy.policy_engine.check_policy()`, verifying against `policy/rules.yaml`.
3. **MCP Tool Dispatch**: Approved actions are dispatched via the in-process `MCPToolsAdapter` directly to `mcp_server.tools` (or via FastMCP client if running over stdio).
4. **Validation**: Post-execution state is tested using `validation.monitor` before presenting the final report to the operator.

---

## 5. Basic Usage & Commands

The Assistant accepts natural-language commands as well as standard CLI keywords:

### 5.1 Conversational & Informational Commands
* `hello` or `hi` — Friendly greeting.
* `what can you do?` — Explains capabilities.
* `help` — Prints the interactive command reference table.
* `thanks` — Natural conversational acknowledgment.
* `bye` or `exit` — Exits the assistant.

### 5.2 Status & Diagnostics
* `status` or `check network status` — Synthesizes live container reachability, firewall drop states, and bandwidth limits into a Rich console table.
* `check status of client1` — Queries reachability and rules specifically for `client1`.

### 5.3 Firewall Isolation (Block / Unblock)
* `block client1` or `disconnect client1 from the network`
  - Validates `client1` is not protected.
  - Adds `client1` IP (`172.20.0.2`) to the `@blocked_clients` bridge set in `nftables`.
  - Validates with 3 cross-bridge ICMP pings (expects 100% loss).
* `unblock client1` or `restore network access for client1`
  - Removes IP from `nftables` bridge set.
  - Tears down any lingering `tc` qdiscs and `ifb0` interfaces.
  - Validates reachability (expects 0% loss).

### 5.4 Traffic Shaping (Bandwidth Limiting)
* `limit client1 to 10 Mbps` or `throttle client1 to 5mbit`
  - Verifies requested rate falls within policy boundaries (1 Mbps – 20 Mbps).
  - Creates `ifb0` interface and redirects ingress traffic.
  - Applies symmetrical Token Bucket Filters on `ifb0` and `eth0`.
  - Runs active `iperf3` validation against `server` (verifies throughput is within ±20%).

### 5.5 Policy Enforced Rejections
* `block server` — **Denied by policy** (`server` is a protected client).
* `limit client1 to 50 Mbps` — **Denied by policy** (exceeds maximum 20 Mbps limit).
* `limit client1 to 0.5 Mbps` — **Denied by policy** (below minimum 1 Mbps limit).
* `block unknown_client` — **Denied by policy** (target not in `known_clients`).

---

## 6. MCP Server Usage

In addition to running via the Assistant, the FastMCP server tools can be inspected and called directly from the command line:

### 6.1 Inspecting Available MCP Tools
```bash
fastmcp inspect mcp_server/server.py
```
Output:
```text
Available tools:
  - block(client: str) -> dict
  - unblock(client: str) -> dict
  - limit(client: str, rate: str) -> dict
  - status(client: str = '') -> dict
```

### 6.2 Directly Calling MCP Tools
```bash
# Query full network status:
fastmcp call mcp_server/server.py status

# Query specific client status:
fastmcp call mcp_server/server.py status client=client1

# Block client1:
fastmcp call mcp_server/server.py block client=client1

# Throttle client1 to 8mbit:
fastmcp call mcp_server/server.py limit client=client1 rate=8mbit

# Unblock client1:
fastmcp call mcp_server/server.py unblock client=client1
```

### 6.3 Programmatic Python Invocation
```python
from mcp_server.tools import block_client, unblock_client, limit_bandwidth, get_status

# Query status
print(get_status("client1"))

# Block client
print(block_client("client1"))
```

---

## 7. Manual Network Verification & Testing

### 7.1 Verify Container Reachability
```bash
# Ping server from client1 (baseline check: 0% loss expected)
docker exec client1 ping -c 3 172.20.0.3

# Test HTTP service
docker exec client1 python3 -c "import socket; socket.create_connection(('server', 5000), 5)"
```

### 7.2 Verify Firewall Blocking (`nftables`)
```bash
# Inspect active nftables bridge ruleset and blocked set elements:
docker exec network-controller nft list ruleset

# Verify block behavior by pinging across the bridge from client2:
docker exec client2 ping -c 3 172.20.0.2
# Expected: 100% packet loss when client1 is blocked
```

### 7.3 Verify Bandwidth Limiting (`tc` / `ifb`)
```bash
# Inspect egress qdisc on eth0:
docker exec client1 tc qdisc show dev eth0

# Inspect ingress redirection and qdisc on ifb0:
docker exec client1 tc qdisc show dev ifb0

# Measure actual egress throughput:
docker exec client1 iperf3 -c 172.20.0.3 -t 5

# Measure actual ingress throughput (reverse mode):
docker exec client1 iperf3 -c 172.20.0.3 -t 5 -R
```

### 7.4 Running the Automated Test Suite
INA includes 128 automated unit, integration, and security tests:
```bash
# Run the complete test suite:
pytest -v

# Run only focused deterministic tests:
pytest tests/test_ina_focused.py -v

# Run only policy tests:
pytest tests/test_policy.py -v
```
*(All 128 tests execute in ~11 seconds).*

---

## 8. Troubleshooting

| Problem | Root Cause | Solution |
| :--- | :--- | :--- |
| **Docker daemon not running** | Docker service is stopped or inactive. | Start Docker daemon via `sudo systemctl start docker` or launch Docker Desktop. |
| **Permission Denied accessing Docker socket** | Current user is not in the `docker` group. | Run `sudo usermod -aG docker $USER` and restart terminal session. |
| **Validation fails with "connection refused" on iperf3** | The `iperf3` daemon on `server` stopped or crashed. | Restart the listener: `docker exec -d server iperf3 -s`. |
| **Ping block shows 0% loss when tested from host** | Host-originated pings bypass Layer-2 bridge forwarding hooks. | Always test block rules from a peer container across the bridge: `docker exec client2 ping 172.20.0.2`. |
| **LLM returns "I'm not sure how to help"** | OpenRouter API key invalid, unset, or rate-limited. | Check `.env` key, or rely on deterministic syntax (e.g. `block client1`, `limit client1 to 5mbps`). |
| **Address already in use on port 5000** | A host service is bound to port 5000. | `network-controller` runs in host mode; stop conflicting host services using `sudo lsof -i :5000`. |
| **ModuleNotFoundError: No module named 'fastmcp' / 'docker'** | Python virtual environment is not activated. | Run `source .venv/bin/activate` before executing commands. |

---

## 9. Shutdown & Cleanup

When finished working with INA:

```bash
# Stop and remove containers and virtual bridge network:
docker compose -f network/docker-compose.yml down

# Clean up any orphaned Docker networks if necessary:
docker network rm network_project-net 2>/dev/null || true
```

---

## 10. Documentation Reference

For detailed technical breakdowns, refer to the component documentation:

- [System Architecture Guide](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/docs/architecture.md): Complete architecture, data contracts, and pipeline flow.
- [MCP Server Specification](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/docs/mcpserver.md): FastMCP tool specifications, parameter schemas, and rollback logic.
- [Network & Linux Kernel Implementation](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/docs/network.md): Detailed bridge networking, `nftables` tables, and `tc/ifb` traffic control.
- [Policy Engine & Security Governance](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/docs/policy.md): Declarative rules, boundaries, and zero-trust safety checks.
