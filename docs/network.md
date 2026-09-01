# Intelligent Network Assistant (INA) - Network Documentation

Welcome to the comprehensive documentation for the **Intelligent Network Assistant (INA)** network environment. This document describes the network architecture, container services, setup and management procedures, baseline test verification measurements, and troubleshooting guides.

---

## 📂 Project Structure

Below is an overview of the key files and directories in this repository:

```text
ina/
├── docs/                             # Project documentation
│   ├── network.md                    # Main combined network architecture, setup, and baseline verification guide
│   ├── README.md                     # Documentation entry point
│   └── network-baseline.md           # Baseline verification & test results
├── network/                          # Network environment & services
│   ├── Dockerfile                    # Container definition with networking utilities
│   ├── docker-compose.yml            # Multi-container service definitions
│   ├── setup.sh                      # Shell script to automate checks, setup & tests
│   └── client.py                     # Custom TCP client/server socket script
└── README.md                         # Project landing page
```

---

## 🗺️ Network Topology & Architecture

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

### 🎛️ Network Configuration

The network configuration details are defined in `network/docker-compose.yml`:

* **Network Name**: `network_project-net` (resolved automatically by Docker Compose)
* **Driver**: `bridge`
* **Subnet**: `172.20.0.0/24`
* **Gateway**: `172.20.0.1`

---

## 📦 Container Services & Assignments

| Container | Hostname | IP Address | Network Mode | Privileged | Default Command / Process | Role |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`client1`** | `client1` | `172.20.0.2` | `project-net` | No | `sleep infinity` | Network Client Node |
| **`server`** | `server` | `172.20.0.3` | `project-net` | No | `python3 -m http.server 5000` | HTTP/TCP Server Daemon |
| **`client2`** | `client2` | `172.20.0.4` | `project-net` | No | `sleep infinity` | Network Client Node |
| **`network-controller`** | `network-controller` | *Host IP* | `host` | Yes | `sleep infinity` | Controller & Policy Enforcer |

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

---

## 📋 Prerequisites

Before setting up the environment, ensure your host machine meets the following requirements:

* **Operating System**: Linux (recommended due to direct bridge interface mapping and `host` network controller requirements).
* **Docker**: Engine version `20.10.0` or higher.
* **Docker Compose**: V2 installed (accessible via `docker compose`).
* **Python**: Python 3.x (optional on the host, but useful for running scripts locally; pre-installed inside the containers).

---

## 🚀 Installation & Setup

### ⚡ Quick Start / Automated Setup
To quickly get the environment running and perform automated connectivity verification, execute:

1. Navigate to the network directory:
   ```bash
   cd network
   ```

2. Make the setup script executable (if not already):
   ```bash
   chmod +x setup.sh
   ```

3. Run the setup script:
   ```bash
   ./setup.sh
   ```

#### What `setup.sh` Does:
1. **Environment Verifications**: Validates Docker installations and checks if the Docker daemon is active.
2. **Teardown & Clean**: Cleans up previous container runs, leftover project networks, and orphans.
3. **Build & Deploy**: Triggers the Docker Compose build process (`docker compose up -d --build`).
4. **Health Check**: Monitors container status to ensure all 4 containers are running.
5. **Connectivity Tests**: Runs a suite of connectivity checks:
   * **TCP Port Check**: client1 -> server (`server:5000`)
   * **TCP Port Check**: client2 -> server (`server:5000`)
   * **DNS Resolution**: Checks if client1 can resolve the hostname `client2` via the internal Docker DNS.
   * **ICMP Ping**: client1 -> server
   * **ICMP Ping**: client2 -> server

### 🛠️ Manual Environment Management

If you prefer to control the environment manually instead of using `setup.sh`, use the following commands:

1. **Build and Start Environment** (from `network/` directory):
   ```bash
   docker compose up -d --build
   ```

2. **View Active Containers**:
   ```bash
   docker compose ps
   ```

3. **Access Container Shell**:
   ```bash
   # Enter client1
   docker exec -it client1 bash

   # Enter client2
   docker exec -it client2 bash

   # Enter the network-controller
   docker exec -it network-controller bash
   ```

4. **Stop and Clean Environment**:
   ```bash
   # Stop containers without deleting
   docker compose stop

   # Stop, delete containers, and clean networks
   docker compose down
   ```

---

## 📡 Testing & Network Baseline Verification

This section documents manual testing execution and the verified network baseline measurements for the Intelligent Network Assistant (INA) environment.

### 🧪 Manual Connectivity Testing

You can manually execute the following validation commands to verify your setup:

#### ICMP Ping Verification
Run a ping test from `client1` to `server`:
```bash
docker exec -it client1 ping -c 4 server
```

#### TCP Socket Communication Test (Custom Client/Server)
Inside the containers, a custom Python TCP script (`client.py`) is copied to `/app/client.py`. You can use it to test custom message exchange:

1. **Start TCP server listener inside `client1`**:
   ```bash
   docker exec -it client1 python3 client.py server
   ```
   *(Starts listener on `0.0.0.0:5000`)*

2. **Trigger TCP client inside `client2` in a separate terminal**:
   ```bash
   docker exec -it client2 python3 client.py client
   ```
   *(Connects to `client1:5000`, sends handshake, prints server response, and repeats every 5 seconds)*

3. **Verify Output**:
   * **Client output**: `connecting to client1:5000`, `recieved : Hello from server`
   * **Server output**: `waiting for connection...`, `recieved message: hello form client2`

---

### 🛡️ Manual Policy Control Testing (`block`, `unblock`, `limit_bandwidth`)

You can test network policies manually from the host terminal using `docker exec` commands targeting `iptables` on `network-controller` and `tc` on client containers.

#### 0. Enable Host Bridge Netfilter (Prerequisite)
To allow host `iptables` rules on `network-controller` to filter Layer 2 container bridge traffic:
* **Terminal Command**:
  ```bash
  docker exec network-controller modprobe br_netfilter
  ```
* **Intended Result**: Loads the `br_netfilter` kernel module enabling `iptables` to process bridged Docker traffic.

---

#### 1. Testing `block` Manually (Firewall Rule)
Block all outbound traffic originating from `client1` (`172.20.0.2`):

* **Terminal Commands**:
  ```bash
  # Apply DROP rule on network-controller for client1 IP
  docker exec network-controller iptables -I DOCKER-USER -s 172.20.0.2 -j DROP

  # Inspect rule in DOCKER-USER chain
  docker exec network-controller iptables -L DOCKER-USER -n -v --line-numbers

  # Test connectivity from blocked container
  docker exec client1 ping -c 4 -W 1 172.20.0.4
  ```

* **Intended Result**:
  * The `DOCKER-USER` chain displays line 1: `DROP all -- 172.20.0.2 everywhere`.
  * `ping` from `client1` to `client2` or `server` fails with **100% packet loss**.
  * The packet/byte counter for rule 1 in `iptables -L DOCKER-USER -n -v` increases with each dropped packet attempt.

---

#### 2. Testing `unblock` Manually (Firewall Rule Removal)
Remove the DROP rule to restore full network communication for `client1`:

* **Terminal Commands**:
  ```bash
  # Delete DROP rule (rule #1) from DOCKER-USER chain
  docker exec network-controller iptables -D DOCKER-USER 1

  # Verify chain is empty / rule removed
  docker exec network-controller iptables -L DOCKER-USER -n -v --line-numbers

  # Verify connectivity from client1
  docker exec client1 ping -c 4 172.20.0.4
  ```

* **Intended Result**:
  * The `DROP` rule is deleted from the `DOCKER-USER` chain.
  * `ping` from `client1` to `client2` or `server` succeeds with **0% packet loss**.
  * Complete network connectivity is restored.

---

#### 3. Testing `limit_bandwidth` Manually (`tc` Traffic Control)
Apply a Token Bucket Filter (`tbf`) queuing discipline on interface `eth0` of `client1` to throttle outbound bandwidth to **5 Mbps**:

* **Terminal Commands**:
  ```bash
  # Start iperf3 server daemon on server node (if not already active)
  docker exec -d server iperf3 -s

  # Test baseline throughput prior to restriction
  docker exec client1 iperf3 -c 172.20.0.3 -t 10

  # Apply 5 Mbps bandwidth limit on client1 eth0
  docker exec client1 tc qdisc replace dev eth0 root tbf rate 5mbit burst 32kbit latency 400ms

  # Verify active tc queuing discipline configuration
  docker exec client1 tc qdisc show dev eth0

  # Measure throttled bandwidth
  docker exec client1 iperf3 -c 172.20.0.3 -t 10

  # Remove bandwidth limitation (reset queuing discipline)
  docker exec client1 tc qdisc del dev eth0 root
  ```

* **Intended Result**:
  * **Baseline**: Unthrottled bandwidth achieves high speed (>10 Gbps).
  * **qdisc Configuration**: `tc qdisc show` reports `qdisc tbf ... rate 5Mbit burst 4Kb lat 400ms`.
  * **Throttled Benchmark**: `iperf3` transfer rate drops from >10 Gbps down to **~4.5 – 5.5 Mbps**.
  * **Removal**: Deleting root `qdisc` restores throughput back to full unthrottled baseline speed (>10 Gbps).

---

### 📊 Baseline Network Verification & Diagnostic Outputs

#### Connectivity Matrix & Verification

##### 1. `client1` ➔ `server` (ICMP & TCP)
* **Status**: `PASS`
* **Packet Loss**: `0%`
* **Average RTT**: `0.044 ms`

**Ping Command Execution Output:**
```text
PING server (172.20.0.3) 56(84) bytes of data.
64 bytes from server.network_project-net (172.20.0.3): icmp_seq=1 ttl=64 time=0.031 ms
64 bytes from server.network_project-net (172.20.0.3): icmp_seq=2 ttl=64 time=0.053 ms
64 bytes from server.network_project-net (172.20.0.3): icmp_seq=3 ttl=64 time=0.039 ms
64 bytes from server.network_project-net (172.20.0.3): icmp_seq=4 ttl=64 time=0.053 ms

--- server ping statistics ---
4 packets transmitted, 4 received, 0% packet loss, time 3082ms
rtt min/avg/max/mdev = 0.031/0.044/0.053/0.009 ms
```

##### 2. `client2` ➔ `server` (ICMP & TCP)
* **Status**: `PASS`
* **Packet Loss**: `0%`
* **Average RTT**: `0.055 ms`

**Ping Command Execution Output:**
```text
PING server (172.20.0.3) 56(84) bytes of data.
64 bytes from server.network_project-net (172.20.0.3): icmp_seq=1 ttl=64 time=0.030 ms
64 bytes from server.network_project-net (172.20.0.3): icmp_seq=2 ttl=64 time=0.092 ms
64 bytes from server.network_project-net (172.20.0.3): icmp_seq=3 ttl=64 time=0.048 ms
64 bytes from server.network_project-net (172.20.0.3): icmp_seq=4 ttl=64 time=0.052 ms

--- server ping statistics ---
4 packets transmitted, 4 received, 0% packet loss, time 3096ms
rtt min/avg/max/mdev = 0.030/0.055/0.092/0.022 ms
```

#### Diagnostic Outputs: Interfaces & Routing Table

##### Client 1 (`client1`) Interfaces (`ip addr`)
```text
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
    inet6 ::1/128 scope host proto kernel_lo 
       valid_lft forever preferred_lft forever
2: eth0@if75: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default 
    link/ether ae:3b:f5:9e:6c:ac brd ff:ff:ff:ff:ff:ff link-netnsid 0
    inet 172.20.0.2/24 brd 172.20.0.255 scope global eth0
       valid_lft forever preferred_lft forever
```

##### Client 2 (`client2`) Interfaces (`ip addr`)
```text
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
    inet6 ::1/128 scope host proto kernel_lo 
       valid_lft forever preferred_lft forever
2: eth0@if74: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default 
    link/ether 2a:64:78:2f:15:11 brd ff:ff:ff:ff:ff:ff link-netnsid 0
    inet 172.20.0.4/24 brd 172.20.0.255 scope global eth0
       valid_lft forever preferred_lft forever
```

##### Routing Table (`ip route`)
Verified routing configuration on client nodes:
```text
default via 172.20.0.1 dev eth0 
172.20.0.0/24 dev eth0 proto kernel scope link src 172.20.0.2 
```

#### Baseline Summary Status
* **Status**: `PASS`
* **Security & Traffic Control Note**: No firewall policies, traffic shaping, or packet dropping configured. This baseline serves as the raw network speed/routing benchmark.

---

## 🔍 Troubleshooting

Here are common issues and how to resolve them:

### 1. Error: `Docker daemon is not running`
* **Cause**: Docker service is not active on the host machine.
* **Solution**: Start the service via systemd:
  ```bash
  sudo systemctl start docker
  ```

### 2. Error: `network-controller` fails to start
* **Cause**: The container runs in `host` mode with `privileged: true`. If the docker daemon doesn't have sufficient privileges or if security modules like SELinux/AppArmor are blocking it, it might fail.
* **Solution**: Check the container logs:
  ```bash
  docker logs network-controller
  ```
  Ensure your user is part of the `docker` group, or run compose with `sudo`.

### 3. Port Conflicts (Address already in use)
* **Cause**: Another service on the host is already using port `5000`.
* **Solution**: Change the mapped host ports in `docker-compose.yml` or stop the conflicting service on the host:
  ```bash
  sudo lsof -i :5000
  # Kill the conflicting PID if necessary
  ```
