# Docker Infrastructure and Container Network Interface (CNI)

## 1. Overview & Architecture
The **Intelligent Network Configuration Assistant (INA)** uses Docker Compose to orchestrate a controlled multi-container network topology. The infrastructure emulates an enterprise LAN environment containing multiple client nodes, a centralized application server, and an out-of-band network controller.

```mermaid
graph TD
    subgraph Host Linux Kernel & Docker Engine
        Controller[network-controller<br>privileged, network_mode: host<br>Mounts /var/run/docker.sock]
        
        subgraph Docker Bridge: network_project-net (172.20.0.0/24)
            Bridge[Linux Bridge Interface<br>br-XXXXXXXXXXXX]
            Server[server<br>IP: 172.20.0.3<br>Port 5000 HTTP + iperf3 -s]
            Client1[client1<br>IP: 172.20.0.2<br>veth1 <--> netns1]
            Client2[client2<br>IP: 172.20.0.4<br>veth2 <--> netns2]
        end
        
        Controller -->|Docker Engine API | HostDaemon[Docker Daemon / Network State]
        Controller -->|Kernel Calls: nftables/tc| Bridge
        Bridge <--> Server
        Bridge <--> Client1
        Bridge <--> Client2
    end
```

---

## 2. Container Topology & Specs

### Compose Configuration (`docker-compose.yml`)
The network infrastructure is defined declaratively using Compose schema v3:

| Container Name | Role | Base OS | Static / Discovered IP | Capabilities / Mode | Mounts & Ports |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`server`** | Application Target | Alpine/Debian Python | `172.20.0.3` | `CAP_NET_ADMIN` | Port 5000 HTTP, `iperf3 -s` background server |
| **`client1`** | Workstation Node | Alpine/Debian Python | `172.20.0.2` | `CAP_NET_ADMIN` | Ping, curl, iperf3 client tools |
| **`client2`** | Workstation Node | Alpine/Debian Python | `172.20.0.4` | `CAP_NET_ADMIN` | Ping, curl, iperf3 client tools |
| **`network-controller`** | Enforcement Host | Debian Python 3.10 | Host IP (`network_mode: host`) | `privileged: true`, `CAP_NET_ADMIN`, `CAP_SYS_ADMIN` | `/lib/modules:ro`, `/var/run/docker.sock`, `/app` |

---

## 3. CNI Bridge Network & IPAM

### Network Configuration
* **Network Identifier**: `network_project-net` (Compose prefix + `project-net`)
* **Subnet**: `172.20.0.0/24`
* **Gateway**: `172.20.0.1` (Host Linux Bridge IP)
* **Driver**: Standard Docker `bridge` CNI driver.

### Virtual Ethernet (`veth`) Pair Architecture
Each container instantiated under `project-net` receives a dedicated virtual ethernet pair (`veth`):
1. **Interface in Container NetNS**: Named `eth0` inside the container namespace.
2. **Interface on Host Bridge**: Named `vethXXXXXXX` attached directly to the host Linux bridge interface (`br-XXXXXXXXXXXX`).

Packets routed between `client1` (`172.20.0.2`) and `client2` (`172.20.0.4`) cross the host bridge at Layer 2.

---

## 4. Dynamic Container Discovery Engine (`code/network/discovery.py`)

INA completely eliminates fragile static IP hardcoding through automated runtime container inspection via the official **Docker SDK for Python** (`docker.from_env()`).

```python
def get_container_ip(container_name: str, preferred_network: str = "project-net") -> str:
    """
    Dynamically resolve the current IPv4 address of a running container on a target network.
    Inspects Docker Engine API NetworkSettings.
    """
```

### Key Discovery Features:
1. **Sanitization**: Input validation via `_validate_container_name` (`^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$`) prevents argument injection.
2. **Network Matching**: Substring matching handles prefix variance (e.g., matching `project-net` inside `network_project-net`).
3. **State Verification**: Asserts container status is actively `running` before returning IP addresses.
4. **Dynamic Fleet Discovery**: `list_known_clients()` dynamically retrieves all running containers attached to `project-net`.

---

## 5. Host Prerequisites & Linux Kernel Requirements

To execute kernel-level `nftables` bridge filtering and `tc` shaping, the host Linux kernel must support the following modules:

```bash
# Load mandatory kernel modules
sudo modprobe br_netfilter
sudo modprobe ifb
sudo modprobe sch_tbf
sudo modprobe act_mirred
sudo modprobe nft_compat
```

### Docker Socket Binding
The `network-controller` container mounts `/var/run/docker.sock` to execute `docker exec` commands into client network namespaces (`client1`, `client2`) when manipulating namespace-local qdiscs and `ifb0` interfaces.

---

## 6. Docker Limitations & Troubleshooting

1. **Host-Ping Bypass on Host Networking**:
   * Executing `ping 172.20.0.2` directly from the `network-controller` (which runs in `network_mode: host`) uses host routing tables, bypassing host bridge forward chains.
   * *Resolution*: INA's validation monitor utilizes `pick_source_container()` to run pings from a sibling bridge node (`client2` $\rightarrow$ `client1`).
2. **`nftables` Module Availability on Custom Kernels**:
   * Systems running WSL2 or non-standard kernel builds may lack `br_netfilter` or `nftables` bridge table support. Standard Ubuntu 22.04 LTS kernel 5.15+ is required.
