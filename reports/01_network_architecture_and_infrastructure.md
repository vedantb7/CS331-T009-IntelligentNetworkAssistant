# Report 01: Network Architecture & Infrastructure Deep Dive

---

## 1. Executive Summary

This report presents an exhaustive technical analysis of the network topology, container infrastructure, Linux kernel networking subsystems, and interface configurations supporting the **Intelligent Network Configuration Assistant (INA)** project.

The INA project relies on a virtualized Local Area Network (LAN) deployed using Docker Compose V2, featuring a custom Linux bridge (`project-net`), isolated client container namespaces, and a privileged network controller container attached directly to the host network stack.

---

## 2. Docker Network Topology & Subnet Design

The project configures a custom software bridge network defined in [`network/docker-compose.yml`](file:///home/dhruv/Documents/ina/network/docker-compose.yml):

* **Network Name**: `network_project-net` (Compose scope prefix: `network_`)
* **Bridge Driver**: `bridge` (Linux virtual bridge interface)
* **IPv4 Subnet**: `172.20.0.0/24` (Class C private subnet, 254 usable host addresses)
* **Default Gateway**: `172.20.0.1` (Assigned to the host-side virtual bridge interface)

```mermaid
graph TD
    subgraph Host Network Namespace (network_mode: host)
        NC["network-controller\n(Host IP / Privileged Mode)"]
    end

    subgraph Linux Kernel Bridge: project-net (172.20.0.0/24)
        GW["Gateway Interface\n172.20.0.1"]
        C1["client1\n172.20.0.2\n(Egress tc tbf)"]
        SRV["server\n172.20.0.3\n(HTTP :5000 / iperf3 :5201)"]
        C2["client2\n172.20.0.4\n(Egress tc tbf)"]

        GW <---> C1
        GW <---> SRV
        GW <---> C2
        
        C1 <--->|Bridged Egress/Ingress| SRV
        C2 <--->|Bridged Egress/Ingress| SRV
        C1 <--->|Bridged L2 Forwarding| C2
    end
```

---

## 3. Container Services & Node Specifications

The network consists of 4 primary containerized nodes built from [`network/Dockerfile`](file:///home/dhruv/Documents/ina/network/Dockerfile) (based on `python:3.12-slim`):

| Container Name | Hostname | Assigned Static IP | Network Mode | Privileged Mode | Runtime Capabilities / Installed Packages | Primary Role & Service Process |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `client1` | `client1` | `172.20.0.2` | `project-net` | No (`CAP_NET_ADMIN`) | `iproute2`, `iputils-ping`, `iperf3`, `net-tools`, `tc` | Client node. Default cmd: `sleep infinity`. Target for block/throttle. |
| `server` | `server` | `172.20.0.3` | `project-net` | No (`CAP_NET_ADMIN`) | `iproute2`, `iperf3`, `python3` | Protected target server. Default cmd: `iperf3 -s -D && python3 -m http.server 5000`. |
| `client2` | `client2` | `172.20.0.4` | `project-net` | No (`CAP_NET_ADMIN`) | `iproute2`, `iputils-ping`, `iperf3`, `net-tools`, `tc` | Secondary client node. Default cmd: `sleep infinity`. Used as validation ping source. |
| `network-controller` | `network-controller` | Host IP (`eth0`/`wlan0`) | `host` | Yes (`privileged: true`) | `iptables`, `nftables`, `kmod`, `procps`, `docker.io` | Central Firewall & Traffic Control Enforcer. Default cmd: `sleep infinity`. |

---

## 4. Low-Level Container Interface & Routing Architecture

### 4.1 Interface Pairings (veth Pairs)

Each container connected to `project-net` receives a virtual Ethernet pair (`veth`):
1. **Peer A (Container end)**: Named `eth0` inside the container's network namespace (`netns`).
2. **Peer B (Host bridge end)**: Named `vethXXXXXXX` attached directly to the `network_project-net` bridge interface in the host's default root network namespace.

```text
[ Container NetNS: client1 ]               [ Host Root NetNS ]
+--------------------------+               +----------------------------------+
| Interface: eth0          | <---veth--->  | Interface: vethae3bf59          |
| IP: 172.20.0.2/24        |   Pair        | Attached to: network_project-net |
+--------------------------+               +----------------------------------+
```

### 4.2 Network Interfaces Inside Client Containers

Executing `ip addr` inside `client1` (`172.20.0.2`) yields:

```text
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
2: eth0@if75: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default 
    link/ether ae:3b:f5:9e:6c:ac brd ff:ff:ff:ff:ff:ff link-netnsid 0
    inet 172.20.0.2/24 brd 172.20.0.255 scope global eth0
       valid_lft forever preferred_lft forever
```

### 4.3 Kernel Routing Table Configuration

Inside each client container, the kernel routing table directs local subnet traffic directly to `eth0` and external traffic to the Docker bridge gateway (`172.20.0.1`):

```text
default via 172.20.0.1 dev eth0 
172.20.0.0/24 dev eth0 proto kernel scope link src 172.20.0.2 
```

---

## 5. Network Controller Architecture

The `network-controller` container occupies a unique architectural role:

* **Host Network Mode (`network_mode: host`)**:
  Bypasses network containerization and attaches directly to the host OS network stack. It shares the host's IP address and network interfaces (`lo`, `docker0`, `br-XXXXX`).
* **Full Privileges (`privileged: true`)**:
  Grants `CAP_SYS_ADMIN` and root access to Linux kernel capabilities. This enables:
  1. Direct kernel module loading (`modprobe br_netfilter`).
  2. Access to host Layer-2 bridge tables (`nftables bridge`).
  3. Execution of `docker.io` CLI commands via the mounted Docker socket (`/var/run/docker.sock`).
* **Volume Mounts**:
  * `/lib/modules:/lib/modules:ro`: Grants access to kernel object files (`.ko`) required for `nftables` and `br_netfilter`.
  * `/var/run/docker.sock:/var/run/docker.sock`: Allows container-to-container control execution.

---

## 6. Linux Kernel Netfilter & Bridge Filtering Setup

When frames travel between `client1` (`172.20.0.2`) and `server` (`172.20.0.3`), they are switched at **Layer 2 (Data Link Layer)** across the virtual bridge (`network_project-net`) without escalating to the Layer 3 (Network Layer) IP routing table of the host.

### 6.1 `br_netfilter` Requirement

By default, Linux bridges bypass standard `iptables` and Layer-3 firewall chains for bridged packets. To enable bridge inspection:

```bash
docker exec network-controller modprobe br_netfilter
```

This sets the sysctl flags:
* `net.bridge.bridge-nf-call-iptables = 1`
* `net.bridge.bridge-nf-call-ip6tables = 1`
* `net.bridge.bridge-nf-call-arptables = 1`

### 6.2 Setup Verification Script (`network/setup.sh`)

Automated setup is driven by [`network/setup.sh`](file:///home/dhruv/Documents/ina/network/setup.sh), which executes:
1. Docker daemon status verification (`docker info`).
2. Clean teardown of existing networks (`docker compose down --remove-orphans`).
3. Automated compilation and instantiation (`docker compose up -d --build`).
4. Container health checks across `client1`, `client2`, `server`, and `network-controller`.
5. Automated TCP port probes (`client1 -> server:5000`) and ICMP echo checks.

```bash
# Execute automated setup and connectivity verification
./network/setup.sh
```

---

## 7. Key Infrastructure Takeaways for Examination

1. **Why `host` mode for `network-controller`?**  
   Because container-scoped firewall rules cannot manipulate host bridge hooks without elevated privileges and root namespace visibility.
2. **How are container IP addresses identified at runtime?**  
   Via dynamic Docker Engine API container discovery using the Docker SDK for Python (`docker.from_env()`) in [`network/discovery.py`](file:///home/dhruv/Documents/ina/network/discovery.py), removing all hardcoded client IP resolution tables.
3. **What layer does the bridge filter operate at?**  
   Layer 2 (Data Link Layer), filtering Ethernet frames carrying IPv4 payloads.

