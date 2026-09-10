# Model Context Protocol (MCP) Server

---

## 1. Overview & Role in INA

The **MCP Server** (`mcp_server/`) serves as the standardized execution layer for the **Intelligent Network Configuration Assistant (INA)**. It exposes safe, programmatic tools over the Model Context Protocol (FastMCP) that allow the AI Assistant to interact with the underlying Docker network infrastructure.

### Core Role
- **Control Bridge**: Decouples natural-language understanding (Assistant / LLM) and access control (Policy Engine) from low-level Linux networking commands.
- **Safety & Boundary Enforcement**: Sanitizes all input parameters, enforces defensive timeouts, prevents command injection, and re-validates actions against the Policy Engine before executing system modifications.
- **Kernel Abstraction**: Translates high-level requests (`block`, `unblock`, `limit`, `status`) into deterministic Linux kernel networking primitives (`nftables` on bridge interfaces, `tc`/IFB traffic shaping on virtual network devices, and Docker Engine API inspection).

---

## 2. MCP Server Architecture

The MCP Server connects the high-level assistant orchestrator to the low-level Linux network stack:

```
+-------------------------------------------------------------------------+
|                              AI Assistant                               |
|        (LLM Intent Parser / Orchestrator in assistant/client.py)        |
+-------------------------------------------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|                            MCP Server Layer                             |
|                                                                         |
|   mcp_server/server.py                                                  |
|   * FastMCP Server Instance ("Network Assistant")                       |
|   * Tool Declarations: @mcp.tool() for block, unblock, limit, status    |
|                                                                         |
|   mcp_server/tools.py                                                   |
|   * Strict Input Validation (regex, disallowed targets, rate bounds)    |
|   * Defense-in-Depth Policy Pre-Check (check_policy)                    |
|   * Safe Subprocess Execution (argument lists, no shell=True, timeouts) |
|   * Audit Trail Integration (policy/audit_log.jsonl)                    |
|   * Dynamic IP & Container Discovery (network/discovery.py)             |
+-------------------------------------------------------------------------+
                    |                                 |
                    | (docker exec nft ...)           | (docker exec tc ...)
                    v                                 v
+------------------------------------+  +---------------------------------+
|     network-controller Container   |  |   Target Containers (client1/2) |
|  * network_mode: host              |  |  * CAP_NET_ADMIN capabilities   |
|  * privileged: true                |  |  * eth0 (ingress/egress tbf)    |
|  * nftables bridge table & set     |  |  * ifb0 (redirected ingress)    |
+------------------------------------+  +---------------------------------+
```

### Key Architectural Characteristics
1. **Out-of-Band Management**: The MCP Server runs on the host (or within the controller environment), communicating into container namespaces via the Docker Engine API and parameterized `docker exec` calls.
2. **Dual-Layer Policy Check**: Although the Assistant evaluates policies before calling MCP tools, each tool in `mcp_server/tools.py` independently verifies `check_policy()` to ensure safety even if tools are invoked directly via MCP clients.
3. **Audit Trail Synchronization**: Every tool execution attempts to record transitions (`APPLIED`, `FAILED`, `DENIED`) to `policy/audit_log.jsonl`.

---

## 3. Module & File Breakdown

The MCP subsystem consists of two primary modules:

### 3.1 `mcp_server/server.py`
Defines the FastMCP application instance and exposes the public tool interfaces:
- Instantiates `mcp = FastMCP("Network Assistant")`.
- Registers four MCP tools via the `@mcp.tool()` decorator:
  - `block(client: str) -> dict`
  - `unblock(client: str) -> dict`
  - `limit(client: str, rate: str) -> dict`
  - `status(client: str = "") -> dict`
- Serves as the entry point when running `python3 -m mcp_server.server` or `fastmcp run/dev`.

### 3.2 `mcp_server/tools.py`
Contains the business logic, security validations, and kernel interaction routines:
- **Validators**: `_validate_client()`, `_validate_ipv4()`, `_validate_rate()`.
- **Core Tool Implementations**:
  - `block_client(client: str) -> dict`
  - `unblock_client(client: str) -> dict`
  - `limit_bandwidth(client: str, rate: str) -> dict`
  - `get_status(client: str = "") -> dict`
- **Internal Kernel Helpers**:
  - `_initialize_bridge_filter()`: Configures `nftables` bridge table and drop rules.
  - `_cleanup_tc_qdiscs()`: Atomically removes `tc` qdiscs and `ifb0` interfaces.
  - `_get_blocked_ips()`: Reads the active `nftables` blocked IP set.
  - `_get_tc_rate()`: Queries current `tc` rate limits on container interfaces.
  - `_ping_container()`: Tests L3 reachability across containers.

---

## 4. Exposed MCP Tools Reference

### 4.1 `block` (`block_client`)
* **Purpose**: Completely isolate a client container from network communication across the bridge.
* **Input Parameters**:
  * `client` (*string*, required): Name of target container (e.g. `"client1"`).
* **Validation**:
  * Regex validation: `^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$`.
  * Cannot start with `-` (prevents command option injection).
  * Disallows control-plane containers (`network-controller`, `host`, `root`, `docker`, `bridge`).
  * Policy Engine approval check (`check_policy("block_client", ...)`).
  * Dynamically resolves container IP and verifies valid IPv4 format.
* **Kernel Action**:
  * Ensures `nftables` bridge table `network_filter` and set `@blocked_clients` exist on `network-controller`.
  * Adds client IPv4 address to `@blocked_clients`.
  * Forwarded bridge packets with source or destination matching `@blocked_clients` are dropped.
* **Idempotency**: If the IP is already in `@blocked_clients`, returns `"status": "success"` with message indicating already blocked.
* **Returned Result**:
  ```json
  {
    "status": "success",
    "action": "block_client",
    "client": "client1",
    "message": "Client client1 blocked successfully at bridge level."
  }
  ```

### 4.2 `unblock` (`unblock_client`)
* **Purpose**: Restore network access to a previously blocked container and remove bandwidth restrictions.
* **Input Parameters**:
  * `client` (*string*, required): Name of target container (e.g. `"client1"`).
* **Validation**:
  * Same client name regex, safety restrictions, and policy approval check as `block`.
* **Kernel Action**:
  * Calls `_cleanup_tc_qdiscs(client)` to clear any lingering `tc` rate limits or `ifb0` devices.
  * Deletes client IPv4 address from `@blocked_clients` in `nftables` bridge set.
* **Idempotency**: If the IP was not in the set, returns `"status": "success"` with message indicating already unblocked.
* **Returned Result**:
  ```json
  {
    "status": "success",
    "action": "unblock_client",
    "client": "client1",
    "message": "Client client1 unblocked successfully."
  }
  ```

### 4.3 `limit` (`limit_bandwidth`)
* **Purpose**: Enforce bi-directional (ingress and egress) bandwidth rate limits on a container.
* **Input Parameters**:
  * `client` (*string*, required): Name of target container (e.g. `"client1"`).
  * `rate` (*string*, required): Target bandwidth rate (e.g. `"10mbit"`, `"5mbps"`, or `"0mbit"` / `"del"` for removal).
* **Validation**:
  * Client name validation and disallowed target check.
  * Rate format validation against `_RATE_RE` (e.g., `kbit`, `mbit`, `gbit`, `kbps`, `mbps`).
  * Policy Engine approval check verifying bounds (e.g. 1 Mbps to 20 Mbps).
* **Kernel Action**:
  * Cleans up existing `tc` queuing disciplines on `eth0` and `ifb0`.
  * If rate is `"0mbit"`, `"none"`, or `"del"`, finishes as rate removal.
  * Creates `ifb0` device inside target container and brings it `up`.
  * Attaches `ingress` qdisc to `eth0` and redirects inbound packets to `ifb0` via `mirred`.
  * Applies Token Bucket Filter (`tbf`) on `ifb0` (Ingress Shaping).
  * Applies Token Bucket Filter (`tbf`) on `eth0` (Egress Shaping).
* **Atomic Rollback**: If any `tc` command fails, `_cleanup_tc_qdiscs()` automatically rolls back changes to prevent half-configured states.
* **Returned Result**:
  ```json
  {
    "status": "success",
    "action": "limit_bandwidth",
    "client": "client1",
    "rate": "10mbit",
    "message": "Bi-directional (ingress & egress) bandwidth limited to 10mbit successfully."
  }
  ```

### 4.4 `status` (`get_status`)
* **Purpose**: Query live reachability, firewall state, and bandwidth configurations across the network or for a specific client.
* **Input Parameters**:
  * `client` (*string*, optional): Container name to inspect, or empty/`"all"` for full network status.
* **Validation**:
  * Sanitizes client string; allows `"all"`, `"network"`, `""`, or valid client identifiers.
  * Policy authorization check (`check_policy("get_status", ...)`).
* **Kernel Action**:
  * Queries Docker Engine API for active containers and IP configurations.
  * Queries `nft list set bridge network_filter blocked_clients` on `network-controller`.
  * Queries `tc qdisc show dev eth0` inside each container.
  * Sends test ICMP ping from `server` to probe reachability.
* **Returned Result**:
  ```json
  {
    "status": "success",
    "action": "get_status",
    "client": "all",
    "network": {
      "name": "network_project-net",
      "available": true,
      "subnet": "172.20.0.0/24",
      "gateway": "172.20.0.1"
    },
    "server": {
      "name": "server",
      "ip": "172.20.0.3",
      "status": "running",
      "reachable": true
    },
    "clients": {
      "client1": {
        "ip": "172.20.0.2",
        "status": "running",
        "reachable": true,
        "firewall": "unblocked",
        "bandwidth_limit": "10mbit"
      }
    },
    "message": "Network 'network_project-net' active. Server reachable. Hosts: 3/3 reachable."
  }
  ```

---

## 5. Low-Level Kernel Mechanisms & Command Execution

The MCP implementation relies on specific Linux networking subsystems executed via Docker:

### 5.1 Layer-2 Bridge Firewalling (`nftables`)
Executed inside the `network-controller` container (`network_mode: host`, `privileged: true`):
```bash
# 1. Initialize bridge table and forward chain
docker exec network-controller nft add table bridge network_filter
docker exec network-controller nft add chain bridge network_filter forward '{ type filter hook forward priority 0; policy accept; }'
docker exec network-controller nft add set bridge network_filter blocked_clients '{ type ipv4_addr; }'

# 2. Add drop rules for blocked set
docker exec network-controller nft add rule bridge network_filter forward ip saddr @blocked_clients drop
docker exec network-controller nft add rule bridge network_filter forward ip daddr @blocked_clients drop

# 3. Add / remove client IP
docker exec network-controller nft add element bridge network_filter blocked_clients '{ 172.20.0.2 }'
docker exec network-controller nft delete element bridge network_filter blocked_clients '{ 172.20.0.2 }'
```

### 5.2 Bi-Directional Traffic Shaping (`tc` + `ifb`)
Executed directly inside the target client container (e.g. `client1`, `cap_add: NET_ADMIN`):
```bash
# 1. Teardown existing qdiscs and interfaces
docker exec client1 tc qdisc del dev eth0 root
docker exec client1 tc qdisc del dev eth0 ingress
docker exec client1 tc qdisc del dev ifb0 root
docker exec client1 ip link set dev ifb0 down
docker exec client1 ip link delete dev ifb0

# 2. Create and enable Intermediate Functional Block (IFB) device
docker exec client1 ip link add name ifb0 type ifb
docker exec client1 ip link set dev ifb0 up

# 3. Ingress redirection via mirred
docker exec client1 tc qdisc add dev eth0 handle ffff: ingress
docker exec client1 tc filter add dev eth0 parent ffff: protocol ip u32 match u32 0 0 action mirred egress redirect dev ifb0

# 4. Token Bucket Filter (TBF) traffic shaping
docker exec client1 tc qdisc replace dev ifb0 root tbf rate 10mbit burst 32kbit latency 400ms
docker exec client1 tc qdisc replace dev eth0 root tbf rate 10mbit burst 32kbit latency 400ms
```

---

## 6. Privilege Model & Safe Execution

The MCP Server avoids host-level privilege escalation (`sudo`) through containerized privilege encapsulation:

1. **Host Isolation**: The Python process hosting the MCP server runs as a standard unprivileged user. It only requires membership in the local `docker` group (or read/write access to `/var/run/docker.sock`).
2. **Encapsulated Root Privileges**:
   - `network-controller` runs with `privileged: true` and host networking, allowing it to modify kernel bridge netfilter tables without granting host sudo privileges to the assistant.
   - Client containers (`client1`, `client2`, `server`) only require `CAP_NET_ADMIN` to configure their own internal network interfaces (`eth0`, `ifb0`).
3. **Subprocess Isolation**: All subprocess calls use explicit argument vectors (`subprocess.run(["docker", "exec", ...])`), avoiding shell expansion vulnerabilities.
4. **Execution Timeout**: Subprocess calls are guarded by `DEFAULT_TIMEOUT = 10` seconds, preventing worker threads from hanging indefinitely.

---

## 7. Discovering and Invoking MCP Tools

### 7.1 Inspecting Available Tools
To verify tools exposed by the MCP Server using FastMCP CLI:
```bash
# Inspect registered tools and parameter signatures
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

### 7.2 Calling Tools via FastMCP CLI
You can invoke tools directly from the terminal for testing:
```bash
# Query network status
fastmcp call mcp_server/server.py status

# Block client1
fastmcp call mcp_server/server.py block client=client1

# Throttle client1 to 8 Mbps
fastmcp call mcp_server/server.py limit client=client1 rate=8mbit

# Restore client1
fastmcp call mcp_server/server.py unblock client=client1
```

### 7.3 Programmatic Python Invocation
The tools can also be imported directly by Python services:
```python
from mcp_server.tools import block_client, unblock_client, limit_bandwidth, get_status

# Query status
result = get_status("client1")

# Apply bandwidth limit
result = limit_bandwidth("client1", "10mbit")
```

---

## 8. Error Handling & Defensive Measures

| Failure Mode | Detection Mechanism | System Behavior |
| :--- | :--- | :--- |
| **Command Injection Attack** | Regex check in `_validate_client` | Rejects input immediately with `ValueError: Invalid client identifier`. |
| **Protected Target (`server`)** | Policy Engine check (`check_policy`) | Rejects action with `"Policy denied: 'server' is a protected client"`; logs `DENIED` in audit trail. |
| **Disallowed Container** | Membership check against `_DISALLOWED_TARGETS` | Rejects targeting `network-controller`, `host`, or `docker`. |
| **Malformed Rate Value** | Regex validation in `_validate_rate` | Returns failure without executing `tc`. |
| **Non-Existent Container** | Docker SDK lookup (`get_container_ip`) | Returns `Client '<client>' not found in Docker environment`. |
| **Subprocess Timeout** | `subprocess.TimeoutExpired` (10s) | Catches timeout cleanly, rolls back `tc` changes if limiting, and logs `FAILED`. |
| **Docker Daemon Down** | `FileNotFoundError` or `DockerException` | Returns clear operational failure message without crashing process. |

---

## 9. MCP Server in the Complete INA Pipeline

The MCP Server fits into the INA pipeline as the single gateway for state modification:

```
1. Operator submits request: "throttle client1 to 5mbps"
2. Assistant parses text into structured Intent(action=LIMIT_BANDWIDTH, target="client1", params={"rate": "5mbit"})
3. Assistant evaluates Policy Engine: rules.yaml allows client1 throttling between 1-20 Mbps
4. Assistant dispatches call to MCP tool: limit_bandwidth("client1", "5mbit")
5. MCP tool re-checks policy, sanitizes inputs, and applies tc/ifb commands via docker exec
6. MCP tool logs APPLIED to audit_log.jsonl and returns execution result dict
7. Assistant triggers Validation Monitor: executes iperf3 test to physically verify throughput
8. Assistant formats final report (Policy + Execution + Validation) in terminal UI
```
