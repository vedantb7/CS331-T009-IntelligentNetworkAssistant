# Intelligent Network Assistant (INA) - Network Documentation

This document describes the network topology, IP assignments, interface configurations, and container services, as well as step-by-step instructions for installing dependencies, setting up, running, testing, and troubleshooting the network environment.

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

---

## 📋 Prerequisites

Before setting up the environment, ensure your host machine meets the following requirements:

* **Operating System**: Linux (recommended due to direct bridge interface mapping and `host` network controller requirements).
* **Docker**: Engine version `20.10.0` or higher.
* **Docker Compose**: V2 installed (accessible via `docker compose`).
* **Python**: Python 3.x (optional on the host, but useful for running scripts locally; pre-installed inside the containers).

---

## 🚀 Installation & Automated Setup

The easiest way to initialize the network environment is using the provided automation script:

1. Navigate to the network directory:
   ```bash
   cd network
   ```

2. Make the script executable (if not already):
   ```bash
   chmod +x setup.sh
   ```

3. Run the setup script:
   ```bash
   ./setup.sh
   ```

### What `setup.sh` Does:
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

---

## 🛠️ Manual Environment Management

If you prefer to control the environment manually instead of using `setup.sh`, use the following commands:

### 1. Build and Start the Environment
Run this from the `network/` directory:
```bash
docker compose up -d --build
```

### 2. View Active Containers
Check the status of running containers:
```bash
docker compose ps
```

### 3. Access a Container's Shell
To enter an interactive bash shell in any container:
```bash
# Enter client1
docker exec -it client1 bash

# Enter client2
docker exec -it client2 bash

# Enter the network-controller
docker exec -it network-controller bash
```

### 4. Stop and Clean the Environment
To stop containers without deleting them:
```bash
docker compose stop
```
To stop, delete containers, and clean networks:
```bash
docker compose down
```

---

## 📡 Testing Connectivity Manually

You can manually execute the following validation commands to verify your setup:

### ICMP Ping Verification
Run a ping test from `client1` to `server`:
```bash
docker exec -it client1 ping -c 4 server
```

### TCP Socket Communication Test (Custom Client/Server)
Inside the containers, a custom Python TCP script (`client.py`) is copied to `/app/client.py`. You can use it to test custom message exchange:

1. **Start the TCP server listener inside `client1`**:
   ```bash
   docker exec -it client1 python3 client.py server
   ```
   *(This starts a listener on `0.0.0.0:5000`)*

2. **Trigger the TCP client inside `client2` in a separate terminal**:
   ```bash
   docker exec -it client2 python3 client.py client
   ```
   *(This connects to `client1:5000`, sends a handshake message, prints the server response, and repeats every 5 seconds)*

3. **Verify Output**:
   * **Client output**: `connecting to client1:5000`, `recieved : Hello from server`
   * **Server output**: `waiting for connection...`, `recieved message: hello form client2`

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
