# Network Architecture

This document describes the network topology, IP assignments, interface configurations, and container services defined in the Intelligent Network Assistant (INA) project.

---

## 🗺️ Topology Diagram

The project establishes a containerized local area network (LAN) inside a bridge network, alongside a privileged controller operating on the host network stack.

```mermaid
graph TD
    subgraph Host Network Mode
        NC[network-controller]
    end

    subgraph Custom Bridge Network: project-net (172.20.0.0/24)
        GW[Gateway: 172.20.0.1]
        C1[client1: 172.20.0.2]
        SRV[server: 172.20.0.3]
        C2[client2: 172.20.0.4]
        
        GW --- C1
        GW --- SRV
        GW --- C2
        
        C1 <--> SRV
        C2 <--> SRV
        C1 <--> C2
    end
```

---

## 🎛️ Network Configuration

The network configuration details are defined in `network/docker-compose.yml`:

* **Network Name**: `network_project-net` (resolved automatically by Docker Compose)
* **Driver**: `bridge`
* **Subnet**: `172.20.0.0/24`
* **Gateway**: `172.20.0.1`

---

## 📦 Container Services & Assignments

| Container | Hostname | IP Address | Network Mode | Privileged | Default Command / Process |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`client1`** | `client1` | `172.20.0.2` | `project-net` | No | `sleep infinity` |
| **`server`** | `server` | `172.20.0.3` | `project-net` | No | `python3 -m http.server 5000` |
| **`client2`** | `client2` | `172.20.0.4` | `project-net` | No | `sleep infinity` |
| **`network-controller`** | `network-controller` | *Host IP* | `host` | Yes | `sleep infinity` |

### 🛠️ Container Capabilities & Utilities
All containers are built from the local `Dockerfile` (based on `python:3.12-slim`) and include the following network administration utilities pre-installed:
* **`iproute2`**: Providing the `ip` tool suite (e.g. `ip addr`, `ip route`, `ip link`).
* **`iputils-ping`**: Providing the `ping` utility for connectivity checks.
* **`net-tools`**: Providing traditional tools like `ifconfig` and `route`.

---

## 🔌 Interface & Routing Details

### Client Node Interfaces
Inside the client containers (e.g. `client1` or `client2`), the standard network interfaces configured are:
1. **`lo` (Loopback)**: `127.0.0.1/8` - for local loopback communications.
2. **`eth0` (Ethernet)**: Connected to the `project-net` bridge network with the assigned static IP (`172.20.0.2` or `172.20.0.4`).

### Routing Table (Client)
By default, the routing table within each client container is configured as follows:
* **Default route**: Traffic destined outside the local subnet goes through the gateway `172.20.0.1` on device `eth0`.
* **Local Subnet Route**: Traffic for `172.20.0.0/24` is routed directly through device `eth0` with source IP set to the container's static IP.

Example `ip route` output from `client1`:
```text
default via 172.20.0.1 dev eth0 
172.20.0.0/24 dev eth0 proto kernel scope link src 172.20.0.2 
```

---

## 🛡️ Special Service: Network Controller

The `network-controller` service is uniquely configured:
* **`network_mode: host`**: Bypasses Docker's network namespaces, attaching the container directly to the host's network interfaces.
* **`privileged: true`**: Grants root-level access to kernel subsystems. This enables the controller to:
  * Manipulate routing tables on the host machine.
  * Control network interfaces, create virtual bridges, or manipulate namespaces.
  * Utilize packet-filtering utilities (`iptables` / `nftables`) and traffic shaping (`tc`) to simulate latency, packet loss, or firewall rules between containers.
