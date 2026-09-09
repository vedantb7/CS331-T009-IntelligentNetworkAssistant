# Intelligent Network Assistant (INA) — Setup & Operation Guide

This guide provides step-by-step instructions to set up, run, verify, and troubleshoot the **Intelligent Network Configuration Assistant (INA)**.

---

## 1. Quick-Start & Project Setup

### Step 1: Prerequisites Check
Ensure Python 3.10+, Docker, and Docker Compose are installed:
```bash
python3 --version
docker --version
docker compose version
```

### Step 2: Clone & Open Project Directory
```bash
cd /path/to/ina
```

### Step 3: Set Up Python Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Step 4: Configure API Credentials
INA uses OpenRouter (or Anthropic/OpenAI) for LLM-based natural language intent parsing. Set your key:

```bash
# Linux / macOS
export OPENROUTER_API_KEY="sk-or-v1-..."

# Windows PowerShell
$env:OPENROUTER_API_KEY="sk-or-v1-..."
```
*(Note: If no API key is provided, INA automatically degrades gracefully to a deterministic regex parser.)*

### Step 5: Start Docker Network Infrastructure
Spin up the custom Docker bridge network (`network_project-net`) containing `client1`, `client2`, `server`, and `network-controller`:

```bash
docker compose -f network/docker-compose.yml up -d --build
```

Verify active containers:
```bash
docker compose -f network/docker-compose.yml ps
```

### Step 6: Launch the Intelligent Assistant
Run the interactive client:
```bash
python3 -m assistant.client
```

Try natural-language network commands:
* **Block client**: `block client1` or `client1 is causing trouble, kick them off`
* **Unblock client**: `unblock client1` or `restore access for client1`
* **Bandwidth limit**: `limit client1 to 5 Mbps` or `throttle client1 to 8 Mbps`
* **Check status**: `check status of client1`

---

## 2. Automated Test Suite Verification

To verify full system integrity, execute the automated PyTest suite covering container discovery, MCP tool execution, policy rules, and validation models:

```bash
source .venv/bin/activate
pytest -v
```
*(Expected Result: 86 passed)*

---

## 3. Manual Network Verification & Inspection

### 3.1 Verifying Layer-2 `nftables` Firewall Rules
Block operations insert IPv4 addresses into an `nftables` bridge set (`@blocked_clients`) on `network-controller`.

```bash
# View active L2 bridge rules and blocked set elements
docker exec network-controller nft list ruleset

# Manually test ping block from sibling container (traverses bridge hook)
docker exec client2 ping -c 3 172.20.0.2
```

### 3.2 Verifying Bi-Directional Traffic Control (`tc`) Bandwidth Throttling
Bandwidth limiting configures Token Bucket Filters (`tbf`) on `eth0` (Egress) and an `ifb0` pseudo-device (Ingress via `mirred` redirect).

```bash
# Inspect egress qdisc on eth0
docker exec client1 tc qdisc show dev eth0

# Inspect ingress redirection filter & ingress qdisc on ifb0
docker exec client1 tc filter show dev eth0 parent ffff:
docker exec client1 tc qdisc show dev eth0
docker exec client1 tc qdisc show dev ifb0

# Measure throttled egress throughput (client1 -> server)
docker exec client1 iperf3 -c 172.20.0.3 -t 5

# Measure throttled ingress throughput (server -> client1 via reverse mode)
docker exec client1 iperf3 -c 172.20.0.3 -t 5 -R
```

---

## 4. Architecture & Command Execution Locations

| Network Operation | Execution Target Container | Low-Level Kernel Mechanism | Command Executed |
| :--- | :--- | :--- | :--- |
| **Block Client** | `network-controller` | `nftables` L2 Bridge Set | `nft add element bridge network_filter blocked_clients { <ip> }` |
| **Unblock Client** | `network-controller` & Target Container | `nftables` & `tc` qdisc teardown | `nft delete element ...` & `_cleanup_tc_qdiscs()` |
| **Limit Bandwidth** | Target Client (e.g. `client1`) | `tc tbf` + `ifb0` mirred redirect | `tc qdisc replace dev eth0 root tbf ...` & IFB redirect |
| **IP Resolution** | Docker Daemon API | Docker SDK for Python | `docker.from_env().containers.get(client)` |

---

## 5. Troubleshooting Guide

| Issue / Symptom | Probable Cause | Resolution |
| :--- | :--- | :--- |
| **Validation UNCONFIRMED** | `iperf3` server daemon stopped on `server` container. | Run `docker exec -d server iperf3 -s` to restart listener. |
| **Host Ping False Positive** | Pinging from Host OS bypasses L2 bridge forward hook. | Always test ping from a sibling container (e.g. `docker exec client2 ping ...`). |
| **Docker Permission Denied** | User not added to `docker` group or Docker Desktop down. | Ensure Docker Desktop is running or run with `sudo`. |
| **LLM Intent Unknown** | Missing or invalid API key. | Set `OPENROUTER_API_KEY` or rely on deterministic regex fallback. |

---

## 6. Shutting Down the Environment

```bash
# Stop and remove containers and network bridge
docker compose -f network/docker-compose.yml down
```
