# Docker & Linux Network Implementation

---

## 1. Purpose of the Docker Network in INA

The **Intelligent Network Configuration Assistant (INA)** uses a virtualized Docker network to provide an isolated, deterministic, and safe local area network (LAN) environment. 

### Why a Containerized Network?
- **Realistic Linux Networking Primitives**: Containers run in distinct Linux network namespaces (`netns`), allowing real Linux kernel packet filtering (`nftables`) and traffic shaping (`tc`/IFB) to be applied and measured without modifying the host machine's physical network adapters.
- **Safety & Isolation**: Automated network policy changes (such as blocking clients or throttling bandwidth) are confined to the virtual Docker bridge, preventing accidental network disruption on the operator's machine.
- **Deterministic Testbed**: Provides reproducible IP addressing, hostnames, and connectivity baselines for automated integration testing and validation.

---

## 2. Network Topology & Architecture

The network consists of a custom Docker bridge network hosting managed client and server nodes, alongside a privileged network controller running with host networking to enforce bridge-level packet filtering.

```text
+--------------------------------------------------------------------------------+
|                             Host Network Namespace                             |
|                                                                                |
|  +--------------------------------------------------------------------------+  |
|  |                 network-controller (network_mode: host)                  |  |
|  |  * Role: Enforcement proxy for L2 bridge firewalling                     |  |
|  |  * Privileged container (privileged: true)                               |  |
|  |  * Volumes: /var/run/docker.sock, /lib/modules:ro                        |  |
|  |  * Controls nftables bridge table 'network_filter'                       |  |
|  +--------------------------------------------------------------------------+  |
|                                       |                                        |
|                                       | Enforces bridge drop rules             |
|                                       v                                        |
|  +--------------------------------------------------------------------------+  |
|  |            Docker Bridge Network: project-net (172.20.0.0/24)            |  |
|  |            Bridge Gateway: 172.20.0.1 (Interface on host)                |  |
|  |                                                                          |  |
|  |  +--------------------+  +--------------------+  +--------------------+  |  |
|  |  |      client1       |  |      client2       |  |       server       |  |  |
|  |  |    172.20.0.2      |  |    172.20.0.4      |  |    172.20.0.3      |  |  |
|  |  |   CAP_NET_ADMIN    |  |   CAP_NET_ADMIN    |  |   CAP_NET_ADMIN    |  |  |
|  |  |  (Managed Client)  |  |  (Validation Peer) |  | (Protected Server) |  |  |
|  |  |  tc eth0 + ifb0    |  |  ping / iperf3 src |  | HTTP :5000         |  |  |
|  |  |                    |  |                    |  | iperf3 :5201       |  |  |
|  |  +--------------------+  +--------------------+  +--------------------+  |  |
|  |            ^                      ^                        ^             |  |
|  |            |                      |                        |             |  |
|  |            +----------------------+------------------------+             |  |
|  |                   Inter-Container Bridged L2 Forwarding                  |  |
|  +--------------------------------------------------------------------------+  |
+--------------------------------------------------------------------------------+
```

### Subnet & Addressing Configuration
Defined in [`network/docker-compose.yml`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/network/docker-compose.yml):
* **Network Name**: `project-net` (Docker Compose creates this as `network_project-net`).
* **Driver**: `bridge` (Linux virtual bridge).
* **IPv4 Subnet**: `172.20.0.0/24` (254 assignable addresses).
* **Default Gateway**: `172.20.0.1` (Assigned to the host-side virtual bridge interface).

---

## 3. Container Services & Node Roles

> **Important**: The containers on `project-net` are **managed targets and workloads**, not administrators. They do not configure policies or control each other. The MCP Server on the host orchestrates configuration changes on these targets via the Docker Engine API.

| Container Name | Assigned Static IP | Network Mode | Privileges & Capabilities | Primary Workload / Daemon | Role in INA Architecture |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`client1`** | `172.20.0.2` | `project-net` | `cap_add: NET_ADMIN` | `sleep infinity` | **Managed Client Node**: Primary target for firewall blocking, unblocking, and bi-directional bandwidth throttling tests. |
| **`client2`** | `172.20.0.4` | `project-net` | `cap_add: NET_ADMIN` | `sleep infinity` | **Secondary Client Node**: Acts as the unblocked peer/source container for validation pings and cross-bridge connectivity tests. |
| **`server`** | `172.20.0.3` | `project-net` | `cap_add: NET_ADMIN` | `iperf3 -s -D && python3 -m http.server 5000` | **Protected Service Node**: Designated as an immutable asset in `policy/rules.yaml`. Runs an HTTP service (port 5000) and an `iperf3` daemon (port 5201) for bandwidth benchmarks. |
| **`network-controller`**| *Host IP* | `host` | `privileged: true` | `sleep infinity` | **Enforcement Controller**: Execution proxy running with host netfilter privileges. Mounts `/var/run/docker.sock` and host kernel modules to apply `nftables` bridge filtering. |

---

## 4. Key Files in `network/`

### 4.1 `network/docker-compose.yml`
Declarative multi-container configuration defining:
- The custom bridge network `project-net` and subnet `172.20.0.0/24`.
- Static IP address assignments for `client1`, `client2`, and `server`.
- Linux capabilities (`cap_add: NET_ADMIN`) for traffic control.
- Host-mode networking and `/var/run/docker.sock` volume mounting for `network-controller`.

### 4.2 `network/Dockerfile`
Base container image definition derived from `python:3.12-slim`. Pre-installs essential Linux networking and inspection utilities:
- `iproute2`: Provides `ip` and `tc` (Traffic Control).
- `iputils-ping`: Provides `ping` for ICMP diagnostics.
- `iperf3`: Network throughput benchmark utility.
- `net-tools`: Traditional tools (`ifconfig`, `netstat`).
- `nftables` & `iptables`: Packet filtering utilities.
- `kmod`: Linux kernel module loading tools.
- `procps` & `docker.io`: Process monitoring and Docker CLI client.

### 4.3 `network/setup.sh`
An automated bash bootstrap and verification script that performs:
1. Docker daemon availability checks.
2. Clean teardown of existing project containers and networks.
3. Building and launching the containers via `docker compose up -d --build`.
4. Verification that all 4 containers are running.
5. Automated TCP port checks (`client1`/`client2` -> `server:5000`).
6. DNS resolution verification (`client1` -> `client2`).
7. ICMP ping checks to establish baseline reachability.

### 4.4 `network/discovery.py`
Dynamic runtime discovery module utilizing the Docker SDK for Python (`docker.from_env()`):
- Replaces static IP assumptions in application code with dynamic lookups.
- `get_container_ip(container_name)`: Queries Docker API for a container's current IPv4 address on `project-net`.
- `list_known_clients()`: Discovers all currently running containers on the project bridge and returns a `{name: ip}` mapping.
- Validates container names and IPv4 syntax to prevent injection vulnerabilities.

---

## 5. Client Discovery: Static vs. Dynamic

The INA network uses a hybrid architecture combining static container assignment with dynamic runtime discovery:

1. **Static Compose Configuration**:
   - Containers are assigned fixed IPs in `docker-compose.yml` (`client1`: `172.20.0.2`, `server`: `172.20.0.3`, `client2`: `172.20.0.4`).
   - This ensures predictable IP assignments across rebuilds and aligns with policy whitelists.
2. **Dynamic Application Discovery**:
   - The application layer (Assistant, Policy Engine, MCP Server, and Validation Monitor) **does not hardcode IP strings**.
   - Whenever an operation is requested for `"client1"`, `network.discovery.get_container_ip("client1")` queries the Docker daemon at runtime.
   - If containers are reassigned, restarted, or dynamically added, the system automatically resolves the correct IP without code changes.

---

## 6. Network Capabilities & Permissions

Containers are granted specific Linux capabilities to adhere to the principle of least privilege while enabling low-level network operations:

### 1. `cap_add: NET_ADMIN` (on `client1`, `client2`, `server`)
- **Why Required**: Linux Traffic Control (`tc`) and interface creation (`ip link add type ifb`) require the `CAP_NET_ADMIN` capability inside the container's network namespace.
- **Scope**: Confined to each container's private network namespace (`netns`). A container with `CAP_NET_ADMIN` cannot modify the host's interfaces or sibling containers.

### 2. `privileged: true` & `network_mode: host` (on `network-controller`)
- **Why Required**: Filtering packets on a software bridge requires interaction with the host Linux kernel's bridge netfilter hooks (`nftables` bridge family).
- **Scope**: Allows `network-controller` to execute `nft add element bridge ...` commands that intercept Layer-2 forwarded frames passing between containers across `network_project-net`.

### 3. Volume Mounts on `network-controller`
- `/var/run/docker.sock`: Allows container management and inspection via Docker API.
- `/lib/modules:/lib/modules:ro`: Grants access to host kernel modules (e.g. `br_netfilter`, `ifb`) if on-demand module loading is required.

---

## 7. How INA & MCP Interact with the Network

The MCP Server (`mcp_server/tools.py`) executes network modifications via containerized subprocess calls:

### 7.1 Client Blocking & Unblocking (`nftables`)
Executed via `docker exec network-controller nft ...`:
1. **Rule Initialization**: The MCP server initializes a bridge-level table, forward chain, and IP set:
   ```bash
   docker exec network-controller nft add table bridge network_filter
   docker exec network-controller nft add chain bridge network_filter forward '{ type filter hook forward priority 0; policy accept; }'
   docker exec network-controller nft add set bridge network_filter blocked_clients '{ type ipv4_addr; }'
   docker exec network-controller nft add rule bridge network_filter forward ip saddr @blocked_clients drop
   docker exec network-controller nft add rule bridge network_filter forward ip daddr @blocked_clients drop
   ```
2. **Block**: Resolves client IP via `discovery.py` and adds the IP to `@blocked_clients`:
   ```bash
   docker exec network-controller nft add element bridge network_filter blocked_clients '{ 172.20.0.2 }'
   ```
3. **Unblock**: Removes the IP from `@blocked_clients` and clears any `tc` rules:
   ```bash
   docker exec network-controller nft delete element bridge network_filter blocked_clients '{ 172.20.0.2 }'
   ```

### 7.2 Bandwidth Limiting (`tc` + `ifb`)
Executed via `docker exec <client> ...`:
1. **Ingress Shaping**: Because Linux `tc` normally shapes only egress traffic, an Intermediate Functional Block (`ifb0`) device is created inside the client container:
   ```bash
   docker exec client1 ip link add name ifb0 type ifb
   docker exec client1 ip link set dev ifb0 up
   docker exec client1 tc qdisc add dev eth0 handle ffff: ingress
   docker exec client1 tc filter add dev eth0 parent ffff: protocol ip u32 match u32 0 0 action mirred egress redirect dev ifb0
   docker exec client1 tc qdisc replace dev ifb0 root tbf rate 10mbit burst 32kbit latency 400ms
   ```
2. **Egress Shaping**: Direct Token Bucket Filter (TBF) applied to `eth0`:
   ```bash
   docker exec client1 tc qdisc replace dev eth0 root tbf rate 10mbit burst 32kbit latency 400ms
   ```
3. **Rollback**: If any command fails, `_cleanup_tc_qdiscs(client)` tears down `ifb0` and resets `eth0` qdiscs.

### 7.3 Status & Discovery
1. Resolves all active containers on `project-net` using Docker Engine SDK.
2. Queries the live `nftables` bridge set (`nft list set bridge network_filter blocked_clients`) to identify blocked IPs.
3. Queries `tc qdisc show dev eth0` inside each container to report active bandwidth limits.
4. Pings containers from `server` to check Layer-3 reachability.

---

## 8. Network Operations & Troubleshooting Commands

### 8.1 Starting and Stopping the Network
```bash
# Automated startup, build, and health check:
./network/setup.sh

# Manual startup via Docker Compose:
docker compose -f network/docker-compose.yml up -d --build

# View running container status:
docker compose -f network/docker-compose.yml ps

# Stop and remove containers and bridge network:
docker compose -f network/docker-compose.yml down
```

### 8.2 Inspecting Network State
```bash
# Inspect Docker bridge subnet, gateway, and connected containers:
docker network inspect network_project-net

# Inspect container IP configuration:
docker exec client1 ip addr show dev eth0

# Inspect routing table inside client:
docker exec client1 ip route show
```

### 8.3 Inspecting Active Kernel Policies
```bash
# List active nftables bridge filter rules and blocked clients set:
docker exec network-controller nft list ruleset

# Inspect active traffic control queuing disciplines on client1:
docker exec client1 tc qdisc show dev eth0
docker exec client1 tc qdisc show dev ifb0
```

### 8.4 Troubleshooting Common Issues

| Issue | Root Cause | Solution |
| :--- | :--- | :--- |
| **Docker daemon not running** | Docker service is stopped. | Run `sudo systemctl start docker`. |
| **`check_block` reports client reachable** | Ping was issued from host OS instead of a peer container. Host pings bypass L2 bridge forwarding hooks. | Use `validation.monitor` or ping from sibling container: `docker exec client2 ping 172.20.0.2`. |
| **`check_bandwidth` fails / timeout** | `iperf3` server daemon exited on `server`. | Restart listener: `docker exec -d server iperf3 -s`. |
| **Permission denied on Docker commands** | Current user lacks Docker socket permissions. | Add user to `docker` group: `sudo usermod -aG docker $USER`. |
| **Subnet address conflict** | `172.20.0.0/24` is used by another local bridge. | Remove conflicting networks with `docker network prune` or adjust subnet in `docker-compose.yml`. |

---

## 9. Baseline Network Verification Procedures

Before applying policy modifications, the network environment must satisfy the following baseline measurements:

### 1. ICMP Ping Baseline (Zero Loss)
```bash
# Test reachability between clients and server
docker exec client1 ping -c 3 172.20.0.3
docker exec client2 ping -c 3 172.20.0.3
docker exec client1 ping -c 3 172.20.0.4
```
* **Expected Result**: 0% packet loss, round-trip time (RTT) < 0.1 ms across the local bridge.

### 2. TCP Service Reachability
```bash
# Verify HTTP server availability
docker exec client1 python3 -c "import socket; socket.create_connection(('server', 5000), 5)"
```
* **Expected Result**: Immediate socket connection without timeout or rejection.

### 3. Unthrottled Bandwidth Benchmark
```bash
# Measure raw inter-container throughput before applying tc limits
docker exec client1 iperf3 -c 172.20.0.3 -t 5
```
* **Expected Result**: Throughput between 10 Gbps and 30+ Gbps (typical for in-memory Linux virtual bridge communication).
* **Validation Contrast**: Once throttled to `10mbit`, the same `iperf3` command will measure **~9.2 – 10.5 Mbps**.
