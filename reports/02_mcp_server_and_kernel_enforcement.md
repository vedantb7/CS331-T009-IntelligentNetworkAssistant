# Report 02: MCP Server & Low-Level Kernel Enforcement Deep Dive

---

## 1. Executive Summary

This report delivers a deep technical dive into the **Model Context Protocol (MCP) server layer** ([`mcp_server/server.py`](file:///home/dhruv/Documents/ina/mcp_server/server.py)) and the low-level Linux kernel enforcement mechanisms ([`mcp_server/tools.py`](file:///home/dhruv/Documents/ina/mcp_server/tools.py)).

The system translates structured tool invocations from the Assistant into real-time Linux kernel modifications, utilizing:
1. **`nftables` Layer-2 Bridge Set Filtering** for instant client blocking/unblocking across virtual ethernet bridges.
2. **Linux Traffic Control (`tc`) Token Bucket Filter (`tbf`)** for egress bandwidth throttling inside target container namespaces.

---

## 2. Model Context Protocol (MCP) Architecture

The MCP Server ([`mcp_server/server.py`](file:///home/dhruv/Documents/ina/mcp_server/server.py)) uses **FastMCP** (`fastmcp==3.4.7`) to expose standardized network control primitives over stdio or JSON-RPC.

```text
[ Assistant Orchestrator ]
         │
         │ (FastMCP JSON-RPC Tool Call)
         ▼
+-------------------------------------------------------------+
| mcp_server/server.py (FastMCP Server: "Network Assistant")  |
+-------------------------------------------------------------+
  ├── @mcp.tool() block(client: str)
  ├── @mcp.tool() unblock(client: str)
  └── @mcp.tool() limit(client: str, rate: str)
         │
         │ (Internal Function Dispatch)
         ▼
+-------------------------------------------------------------+
| mcp_server/tools.py (Subprocess Network Operations)          |
+-------------------------------------------------------------+
  ├── block_client()   ──> network.discovery.get_container_ip ──> nft drop
  ├── unblock_client() ──> network.discovery.get_container_ip ──> nft delete
  └── limit_bandwidth()──> network.discovery.get_container_ip ──> tc qdisc
```

> **Note on Dynamic Client IP Resolution**: `mcp_server/tools.py` no longer uses a static `CLIENT_IPS` dictionary. All tools dynamically query the Docker Engine API via `network.discovery.get_container_ip(client)` to resolve target IPv4 addresses in real time.


### 2.1 Tool Specifications

| MCP Tool Name | Function Signature | Input Parameters | Return Dictionary Structure | Execution Target Container |
| :--- | :--- | :--- | :--- | :--- |
| `block` | `block(client: str)` | `client`: Client ID (e.g. `"client1"`) | `{"status": "success"/"failure", "action": "block_client", "client": "...", "message": "..."}` | `network-controller` |
| `unblock` | `unblock(client: str)` | `client`: Client ID (e.g. `"client1"`) | `{"status": "success"/"failure", "action": "unblock_client", "client": "...", "message": "..."}` | `network-controller` |
| `limit` | `limit(client: str, rate: str)` | `client`: Client ID, `rate`: Speed string (e.g. `"5mbit"`) | `{"status": "success"/"failure", "action": "limit_bandwidth", "client": "...", "rate": "...", "message": "..."}` | `<client>` (e.g. `client1`) |

---

## 3. Layer-2 Bridge Filtering Mechanics (`nftables`)

### 3.1 The Bridge Filtering Problem

In Docker container networking, containers on the same bridge (`172.20.0.0/24`) communicate via **Layer-2 Ethernet frame switching**. Packets traveling between `client1` (`172.20.0.2`) and `server` (`172.20.0.3`) pass directly through the bridge interface (`network_project-net`) without escalating to the Layer-3 routing engine of the host.

Standard `iptables` rules placed in the `FORWARD` or `DOCKER-USER` chains operate at **Layer 3**. Without strict `br_netfilter` kernel hooks, bridged frames bypass `iptables` entirely.

### 3.2 The `nftables` Bridge Filtering Solution

To solve this, INA implements an `nftables` Layer-2 bridge filter inside [`mcp_server/tools.py`](file:///home/dhruv/Documents/ina/mcp_server/tools.py) via `_initialize_bridge_filter()`:

```python
def _initialize_bridge_filter() -> tuple[bool, str]:
    steps = [
        ("add", "table", "bridge", "network_filter"),
        ("add", "chain", "bridge", "network_filter", "forward",
         "{ type filter hook forward priority 0; policy accept; }"),
        ("add", "set", "bridge", "network_filter", "blocked_clients",
         "{ type ipv4_addr; }"),
    ]
```

#### Kernel Hierarchy Created in `nftables`:
1. **Table**: `bridge network_filter` (Operates specifically on L2 Ethernet frames).
2. **Chain**: `forward` hooked to `hook forward priority 0` with `policy accept`.
3. **Set**: `blocked_clients` containing dynamic `type ipv4_addr` elements.
4. **Rules**:
   * `nft add rule bridge network_filter forward ip saddr @blocked_clients drop`
   * `nft add rule bridge network_filter forward ip daddr @blocked_clients drop`

```mermaid
graph LR
    Frame[Ethernet Frame from client1] --> Hook{nftables L2 Hook: priority 0}
    Hook -->|Match IP in @blocked_clients| DROP[DROP Frame]
    Hook -->|No Match| PASS[Forward Frame to server]
```

### 3.3 Block & Unblock Execution Commands

#### Blocking a Client (`block_client("client1")`):
Appends the client's static IP (`172.20.0.2`) to the `nftables` bridge set:
```bash
docker exec network-controller nft add element bridge network_filter blocked_clients { 172.20.0.2 }
```

#### Unblocking a Client (`unblock_client("client1")`):
Deletes the client's IP from the `nftables` bridge set:
```bash
docker exec network-controller nft delete element bridge network_filter blocked_clients { 172.20.0.2 }
```

---

## 4. Traffic Control (`tc`) Bandwidth Limiting

### 4.1 Token Bucket Filter (`tbf`) Qdisc

Bandwidth limiting is enforced inside the target client container's network namespace using the Linux **Traffic Control (`tc`)** utility. The system configures a **Token Bucket Filter (`tbf`) queuing discipline (qdisc)** on the egress interface `eth0`.

```bash
docker exec client1 tc qdisc replace dev eth0 root tbf rate 5mbit burst 32kbit latency 400ms
```

### 4.2 Mathematical & Technical Parameter Breakdown

```text
               +----------------------------------+
               |        Token Bucket (depth = B)  |
               |  Tokens arrive at rate R (5mbit) |
               +----------------------------------+
                                |
                                v
Packet Stream ---> [ Queue Buffer (Latency 400ms) ] ---> [ Transmit eth0 ]
                          (If tokens available)
```

1. **`rate 5mbit` ($R$)**:
   The sustained maximum transfer rate (5 Megabits per second). Tokens are accumulated in the bucket at this exact rate.
2. **`burst 32kbit` ($B$)**:
   The size of the token bucket (32 Kilobits). Determines the maximum amount of data that can be transmitted in an instantaneous burst when the bucket is full.
3. **`latency 400ms` ($L$)**:
   The maximum time a packet is permitted to wait in the TBF queue waiting for tokens before being dropped. Queue capacity is calculated as $Queue\_Bytes = R \times L$.

### 4.3 Querying Active Traffic Control Rules

To inspect the live `tc` configuration inside `client1`:
```bash
docker exec client1 tc qdisc show dev eth0
```

* **Output Output**:
  `qdisc tbf 8001: root refcnt 2 rate 5Mbit burst 4Kb lat 400.0ms`

---

## 5. Subprocess Execution & Failure Handling Matrix

In [`mcp_server/tools.py`](file:///home/dhruv/Documents/ina/mcp_server/tools.py), all CLI tools are executed using `subprocess.run` with `capture_output=True, text=True, check=False`.

| Scenario | Subprocess Exit Code | `stderr` Pattern | MCP Tool Outcome Returned |
| :--- | :--- | :--- | :--- |
| **Successful Block** | `0` | Clean | `{"status": "success", "message": "Client client1 blocked successfully..."}` |
| **Duplicate Block** | `1` | `File exists` | `{"status": "success", "message": "Client client1 is already blocked."}` |
| **Duplicate Unblock** | `1` | `No such file/element` | `{"status": "success", "message": "Client client1 is already unblocked."}` |
| **Docker Daemon Down** | N/A | `FileNotFoundError` | `{"status": "failure", "message": "Docker command not found..."}` |
| **Invalid Client** | N/A | None | `{"status": "failure", "message": "Client unknown not found..."}` |

---

## 6. Key Takeaways for Examination & Defense

1. **Why `nftables` over legacy `iptables`?**  
   `nftables` provides native, performant Layer-2 bridge table hooks (`table bridge`) and dynamic IP sets (`set blocked_clients`), allowing $O(1)$ set lookups instead of traversing linear `iptables` rule chains ($O(N)$).
2. **Why is `tc` executed inside the target container?**  
   `tc qdisc` shapes egress traffic on `eth0` at the source network namespace level. Running `tc` inside `client1` directly throttles packet transmission at its virtual NIC.
3. **What happens if a rate of `100mbit` is requested?**  
   The MCP server would execute it, but the upstream **Policy Engine** intercepts and denies rates above `20mbit` before MCP execution is triggered.
