# Report 01: Network Architecture & Infrastructure Deep Dive

---

## 1. Executive Summary

This report provides a comprehensive technical reference for the network architecture and container infrastructure of the **Intelligent Network Configuration Assistant (INA)** project.

INA manages a containerized virtual network lab built on Docker Compose, featuring a dedicated bridge network, virtual Ethernet (`veth`) pair topologies, Linux network namespaces (`netns`), and dynamic container IP resolution powered by the **Docker SDK for Python** ([`code/network/discovery.py`](file:///home/dhruv/Documents/ina/code/network/discovery.py)).

---

## 2. Docker Network Lab Architecture

### 2.1 Topology Overview

The containerized lab consists of four core nodes attached to a isolated Docker bridge network (`network_project-net`):

```mermaid
graph TD
    subgraph Host Network Stack
        NC[network-controller<br/>IP: Host Stack / netns<br/>Mode: network_mode: host<br/>Privileged: true]
    end

    subgraph Docker L2 Bridge: network_project-net (172.20.0.0/24, Gateway: 172.20.0.1)
        Bridge[Linux Bridge: network_project-net]
        
        C1[client1<br/>Container IP: 172.20.0.2<br/>veth: eth0]
        C2[client2<br/>Container IP: 172.20.0.4<br/>veth: eth0]
        SVR[server<br/>Container IP: 172.20.0.3<br/>veth: eth0<br/>Services: HTTP :80, iperf3 :5201]
    end

    NC == L2 nftables Bridge Hook ==> Bridge
    C1 <== veth pair ==> Bridge
    C2 <== veth pair ==> Bridge
    SVR <== veth pair ==> Bridge
```

### 2.2 Container Roles & Configuration Matrix

| Container Name | Role in Architecture | Network Mode | IP Allocation | Required Capabilities & Privileges | Installed Base Tools |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`network-controller`** | Central Network Firewall Controller | `host` | Host Stack (`172.20.0.1` gateway) | `privileged: true`, `CAP_SYS_ADMIN`, `CAP_NET_ADMIN` | `nftables`, `iptables`, `iproute2`, `modprobe` |
| **`client1`** | Target Network Client 1 | `bridge` | Dynamic/Static (`172.20.0.2`) | `CAP_NET_ADMIN` | `iproute2` (`tc`), `iperf3`, `ping`, `curl` |
| **`client2`** | Target Network Client 2 | `bridge` | Dynamic/Static (`172.20.0.4`) | `CAP_NET_ADMIN` | `iproute2` (`tc`), `iperf3`, `ping`, `curl` |
| **`server`** | Reference Target & Measurement Node | `bridge` | Dynamic/Static (`172.20.0.3`) | Standard | `nginx` (HTTP :80), `iperf3` daemon (:5201) |

---

## 3. Virtual Ethernet (`veth`) & Namespace Topologies

### 3.1 `veth` Pair Mechanics

Each client container (`client1`, `client2`, `server`) executes inside its own isolated Linux **network namespace (`netns`)**. 

Communication between the container namespace and the host bridge occurs across a **virtual Ethernet (`veth`) pair**, which functions as a virtual bidirectional patch cable:
1. **Container Peer (`eth0`)**: Positioned inside the container's network namespace. Assigned the container's IPv4 address (e.g., `172.20.0.2/24`).
2. **Host Peer (`vethXXXXXXX`)**: Bound to the host network namespace and attached directly to the slave interface list of the Linux bridge (`network_project-net`).

```text
[ Container netns: client1 ]                     [ Host Root netns ]
+---------------------------+                   +---------------------------------------+
|  eth0 (172.20.0.2/24)     | <=== veth pair == | vethA1B2C3D --> [ Bridge: project-net ]|
+---------------------------+                   +---------------------------------------+
```

### 3.2 Dynamic Docker Engine API Container Discovery

Previous iterations relied on static hardcoded IP mappings (`CLIENT_IPS`). The implementation in [`code/network/discovery.py`](file:///home/dhruv/Documents/ina/code/network/discovery.py) dynamically resolves container IP addresses at runtime using the **Docker SDK for Python** (`docker.from_env()`):

```python
def get_container_ip(container_name: str, preferred_network: str = "project-net") -> str:
    """
    Query Docker Engine API to dynamically resolve container IPv4 address on preferred_network.
    """
    info = get_container_info(container_name)
    networks = info.get("networks", {})
    for net_name, net_config in networks.items():
        if preferred_network in net_name:
            ip = net_config.get("IPAddress", "").strip()
            if ip:
                return ip
    raise ValueError(f"Container '{container_name}' has no assigned IP on '{preferred_network}'.")
```

---

## 4. Layer-2 Switching vs. Layer-3 Routing Paths

### 4.1 Intra-Subnet Layer-2 Switching (`client1` $\leftrightarrow$ `client2`)

Traffic between `client1` (`172.20.0.2`) and `client2` (`172.20.0.4`) belongs to the same `/24` subnet (`172.20.0.0/24`):
1. `client1` performs an ARP request for `172.20.0.4`.
2. Ethernet frames pass out of `client1`'s `eth0`, across the `veth` pair, and directly into the Layer-2 forward hook of the `network_project-net` bridge.
3. The bridge switches the Ethernet frame to `client2`'s `veth` interface **without escalating packets to the host Layer-3 IP routing stack**.

> **Key Architectural Implication**: Standard `iptables` rules in the host's `FORWARD` or `DOCKER-USER` chains operate at Layer 3. Because intra-bridge traffic switches at Layer 2, standard `iptables` rules are bypassed unless `br_netfilter` kernel hooks or native `nftables` bridge family tables (`table bridge network_filter`) are used.

### 4.2 Host & Gateway Traversal

* **Gateway IP (`172.20.0.1`)**: Serves as the default gateway for client containers seeking external routing.
* **`network-controller` Container**: Runs with `network_mode: host`, placing its process directly inside the host root network namespace. This enables it to load kernel modules (`br_netfilter`) and manage host-level `nftables` bridge tables.

---

## 5. Kernel Prerequisites & Privileges

To support Layer-2 firewall filtering and traffic shaping, the host environment must satisfy:

1. **Kernel Modules Loaded**:
   * `br_netfilter`: Enables Netfilter hooks on Linux bridge devices (`sysctl net.bridge.bridge-nf-call-iptables=1`).
   * `ifb`: Enables Intermediate Functional Block pseudo-devices for ingress traffic shaping (`modprobe ifb`).
   * `sch_tbf` & `act_mirred`: Enables Token Bucket Filter qdiscs and packet redirection filters.
2. **Container Capabilities**:
   * `network-controller`: `privileged: true` granting `CAP_SYS_ADMIN` and `CAP_NET_ADMIN`.
   * Client containers: `CAP_NET_ADMIN` to manage internal `tc` qdiscs and `ifb0` interfaces.
