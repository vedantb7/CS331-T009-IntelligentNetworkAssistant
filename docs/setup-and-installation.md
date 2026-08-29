# Setup & Installation Guide

This guide provides step-by-step instructions for installing dependencies, setting up, running, testing, and troubleshooting the Intelligent Network Assistant (INA) network environment.

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
