# MCP Server

## Overview

The `mcp_server/` component provides the MCP interface for network management. It receives approved requests from the Assistant/Policy layer and performs network configuration on the Docker network.

**Responsibilities:**

* Block a client using `iptables`
* Unblock a client using `iptables`
* Limit client bandwidth using `tc`

The MCP Server is **outside the Docker network** and acts as the control bridge to the Docker containers.

---

## Files

```text
mcp_server/
├── server.py    # MCP server and tool definitions
└── tools.py     # Network operation implementations
```

### `server.py`

Exposes the following MCP tools:

| MCP Tool  | Parameters       | Purpose                |
| --------- | ---------------- | ---------------------- |
| `block`   | `client`         | Block a client         |
| `unblock` | `client`         | Unblock a client       |
| `limit`   | `client`, `rate` | Limit client bandwidth |

### `tools.py`

Contains the actual network operations:

* `block_client()` → `iptables` on the Docker host
* `unblock_client()` → `iptables` on the Docker host
* `limit_bandwidth()` → `tc` inside the target container

Dynamic Client Discovery:

Instead of relying on a hardcoded IP dictionary, the MCP tools resolve container network addresses dynamically at runtime using the **Docker SDK for Python** (`docker.from_env()`) via `network.discovery.get_container_ip(client)`:

```python
from network.discovery import get_container_ip

# Dynamically resolves target container IP address from Docker Engine API
client_ip = get_container_ip(client)
```

This ensures that container IP changes or network restarts are automatically resolved without requiring code modifications.

---

## Network Configuration

The Docker network is:

```text
Network: project-net
Subnet: 172.20.0.0/24

client1 → 172.20.0.2
server  → 172.20.0.3
client2 → 172.20.0.4
```

### Block / Unblock

`iptables` runs on the **Docker host** using the `DOCKER-USER` chain.

Example:

```bash
iptables -I DOCKER-USER -s 172.20.0.2 -j DROP
```

### Bandwidth Limit (Bi-Directional Ingress & Egress)

Bandwidth limiting is enforced inside the target container using Linux Traffic Control (`tc`) and an Intermediate Functional Block (`ifb0`) pseudo-device to shape both **ingress** and **egress** traffic:

1. Create and enable `ifb0` device: `ip link add name ifb0 type ifb && ip link set dev ifb0 up`
2. Add `ingress` qdisc on `eth0` and redirect ingress packets to `ifb0`: `tc qdisc add dev eth0 handle ffff: ingress && tc filter add dev eth0 parent ffff: protocol ip u32 match u32 0 0 action mirred egress redirect dev ifb0`
3. Apply TBF qdisc on `ifb0` for **Ingress Shaping**: `tc qdisc replace dev ifb0 root tbf rate <rate> burst 32kbit latency 400ms`
4. Apply TBF qdisc on `eth0` for **Egress Shaping**: `tc qdisc replace dev eth0 root tbf rate <rate> burst 32kbit latency 400ms`

## Setup

### 1. Create and activate virtual environment

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Python dependencies

```bash
pip install --upgrade pip
pip install fastmcp==3.4.7
```

The MCP Server uses Python's built-in `subprocess` module, so no additional Python library is required for command execution.

### 3. Verify required system tools

The following system tools are required:

```bash
docker --version
iptables --version
tc -V
```

If missing, install them on Ubuntu/Debian:

```bash
sudo apt update
sudo apt install docker.io iptables iproute2
```

### 4. Start the Docker network

```bash
cd network
docker compose up -d
cd ..
```

### 5. Verify containers

```bash
docker ps
```

The containers `client1`, `client2`, and `server` should be running.

### 6. Verify MCP Server

```bash
fastmcp list mcp_server/server.py
```

It should show the three tools:

```text
block(client: str)
unblock(client: str)
limit(client: str, rate: str)
```


## Running the MCP Server

The MCP server requires sufficient privileges for host-level `iptables` operations.

For testing:

```bash
sudo env "PATH=$PATH" fastmcp call mcp_server/server.py <tool> <arguments>
```

---

## Testing the Tools

### 1. Block

```bash
sudo env "PATH=$PATH" fastmcp call mcp_server/server.py block client=client1
```

Expected:

```json
{
  "status": "success",
  "action": "block_client",
  "client": "client1",
  "message": "Client client1 blocked successfully."
}
```

Verify:

```bash
sudo iptables -L DOCKER-USER -n --line-numbers
```

---

### 2. Unblock

```bash
sudo env "PATH=$PATH" fastmcp call mcp_server/server.py unblock client=client1
```

Expected:

```json
{
  "status": "success",
  "action": "unblock_client",
  "client": "client1",
  "message": "Client client1 unblocked successfully."
}
```

---

### 3. Limit Bandwidth

```bash
fastmcp call mcp_server/server.py limit client=client1 rate=5mbit
```

Expected:

```json
{
  "status": "success",
  "action": "limit_bandwidth",
  "client": "client1",
  "rate": "5mbit",
  "message": "Bandwidth limited to 5mbit successfully."
}
```

Verify:

```bash
docker exec client1 tc qdisc show dev eth0
```

Expected output contains:

```text
qdisc tbf ... rate 5Mbit
```

---

## Integration With Other Components

The MCP Server does **not** directly receive natural-language commands.

The overall flow is:

```text
Admin/User
    ↓
AI Assistant / MCP Client
    ↓
Policy Engine
    ↓
MCP Server
    ↓
Network Configuration
    ↓
Docker Network
```

### Interaction

1. **Assistant** interprets the user's natural-language request.
2. **Policy Engine** checks whether the requested operation is allowed.
3. If approved, the **Assistant calls the appropriate MCP tool**.
4. **MCP Server** executes the network operation.
5. The structured result is returned to the **Assistant**.
6. The Assistant reports the result to the Admin/User.

The MCP Server is therefore responsible only for **executing approved network operations and returning their results**.
