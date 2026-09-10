# Intelligent Network Configuration Assistant (INA) — Architecture

---

## 1. Executive Summary & Purpose

The **Intelligent Network Configuration Assistant (INA)** is an automated, policy-governed network management system designed for containerized environments. It bridges the gap between high-level user intent expressed in plain natural language (e.g., *"block client1"*, *"limit client2 to 5 Mbps"*, or *"check network status"*) and low-level Linux kernel networking primitives (`nftables`, `tc`/IFB, Docker bridge network namespaces).

### Core Objectives
- **Natural Language Intent Translation**: Accurately interpret operator commands using Large Language Models (LLMs) with robust local regex fallbacks.
- **Strict Policy Governance**: Enforce deterministic, declarative safety rules (e.g., protected assets, allowed operations, rate boundaries) before any network configuration changes occur.
- **Standardized Control Bridge**: Expose kernel management actions through the Model Context Protocol (FastMCP), isolating application logic from raw system calls.
- **Active Post-Execution Validation**: Automatically verify through live synthetic traffic (ICMP pings across network bridges and `iperf3` throughput measurements) that requested network policies are physically active.
- **Tamper-Evident Audit Logging**: Maintain a structured JSONL audit trail recording all evaluations, policy denials, successful applications, and operational failures.

---

## 2. Overall Architecture & Component Responsibilities

INA is architected as a modular, pipeline-driven system where every layer has a distinct responsibility and clean interface boundaries.

```
       +-------------------------------------------------------------+
       |                         Operator                            |
       +-------------------------------------------------------------+
                                      |
                                      v
       +-------------------------------------------------------------+
       |               Interactive Assistant CLI                     |
       |  (Rich Terminal UI | LLM Intent Parser | Pipeline Runner)   |
       +-------------------------------------------------------------+
                 |                                      ^
                 v                                      |
       +--------------------+                 +---------------------+
       |   Policy Engine    |                 | Validation Monitor  |
       | (rules.yaml check) |                 | (ping & iperf3 test)|
       +--------------------+                 +---------------------+
                 |                                      ^
                 | (Allowed)                            |
                 v                                      |
       +-------------------------------------------------------------+
       |                      MCP Server                             |
       |     (FastMCP Tools | Input Sanitization | Subprocess)       |
       +-------------------------------------------------------------+
                       |                                |
                       | (nftables / tc commands)       | (Audit Entries)
                       v                                v
       +---------------------------------+    +---------------------+
       |     Docker Network Topology     |    |  Audit Log (JSONL)  |
       | (network-controller, clients,   |    | (ALLOWED, DENIED,   |
       |  server on project-net bridge)  |    |  APPLIED, FAILED)   |
       +---------------------------------+    +---------------------+
```

### 2.1 Assistant (`assistant/client.py`)
- **CLI Interface**: Interactive terminal interface with `rich` styling, tabular status reporting, and formatted diagnostic output.
- **Intent Parser**: Uses OpenRouter LLMs (or local OpenAI/Anthropic compatible endpoints) with a strict system prompt to classify inputs into structured `Intent` objects (`action`, `target`, `params`). Falls back deterministically to regular expressions if the LLM is unreachable or unconfigured.
- **Pipeline Orchestrator**: Coordinates execution sequence: `Parse -> Classify -> Policy Evaluation -> MCP Execution -> Validation -> Audit -> Response`.
- **Conversational Filter**: Immediately short-circuits general greetings and help queries without invoking network tools or policy checks.

### 2.2 Policy Engine (`policy/policy_engine.py`, `policy/rules.yaml`)
- **Deterministic Access Control**: Reads declarative policies from `rules.yaml`.
- **Action Whitelisting**: Verifies that requested operations belong to `allowed_actions` (`block_client`, `unblock_client`, `limit_bandwidth`, `get_status`).
- **Target Verification**: Ensures target nodes belong to `known_clients` and prevents operations against `protected_clients` (e.g., `server` can never be blocked or throttled).
- **Rate Boundary Enforcement**: Enforces that bandwidth throttling rates fall strictly within authorized thresholds (e.g., 1 Mbps to 20 Mbps).

### 2.3 MCP Server (`mcp_server/server.py`, `mcp_server/tools.py`)
- **Model Context Protocol Implementation**: Implements FastMCP tool definitions (`block`, `unblock`, `limit`, `status`).
- **Parameter Validation & Command Safety**: Validates container names against strict alphanumeric regular expressions, validates IPv4 address formats, rejects flags or shell metacharacters, and executes all system utilities via explicit argument lists without `shell=True`.
- **Secondary Policy Verification**: Re-checks `check_policy()` independently inside each tool to guarantee policy enforcement even if tools are invoked directly via MCP.
- **Kernel Enforcement**: Executes `nftables` commands inside `network-controller` for firewalling, and `tc`/`ip` commands inside target containers for traffic shaping.
- **Atomic Rollback**: Cleans up queuing disciplines (`_cleanup_tc_qdiscs`) if intermediate traffic control configuration steps fail.

### 2.4 Docker Network Infrastructure (`network/docker-compose.yml`, `network/discovery.py`)
- **Bridge Network**: Custom Docker bridge `network_project-net` (`172.20.0.0/24`) isolating managed nodes.
- **Managed Nodes**:
  - `client1` (`172.20.0.2`): Primary test client node.
  - `client2` (`172.20.0.4`): Secondary client node, utilized as a source node for validation pings.
  - `server` (`172.20.0.3`): Protected server running HTTP (port 5000) and `iperf3` daemon (port 5201).
- **Enforcement Controller**:
  - `network-controller`: Runs with `network_mode: host` and `privileged: true`, mounting `/var/run/docker.sock` and `/lib/modules`. Hosts the `nftables` bridge filtering hooks that intercept Layer-2 inter-container forwarded frames.
- **Dynamic Discovery**: Resolves container metadata and runtime IP addresses on-the-fly using the Docker Engine SDK for Python (`docker.from_env()`).

### 2.5 Validation Monitor (`validation/monitor.py`, `validation/models.py`)
- **Synthetic Traffic Testing**: Actively tests network behavior post-modification rather than assuming kernel commands succeeded.
- **Bridge Ping Verification**: Executes cross-container ICMP pings via `pick_source_container()` (e.g., pinging `client1` from `client2`). This guarantees traffic traverses the bridge forward hook where firewall rules reside.
- **Bandwidth Verification**: Executes `iperf3` clients against `server` inside the target container to measure actual transfer throughput, confirming it falls within a ±20% margin of the requested rate.
- **Pydantic Data Contracts**: Enforces schema validation using `ValidationRequest` and `ValidationResult` models.

### 2.6 Audit Logging (`policy/audit_log.py`, `policy/audit_log.jsonl`)
- **Structured JSONL Records**: Logs timestamped records of every significant transition:
  - `ALLOWED`: Policy approved the requested operation.
  - `DENIED`: Policy rejected an unauthorized action, protected target, or invalid rate.
  - `APPLIED`: Kernel configuration successfully executed by MCP.
  - `FAILED`: Operational failure during command execution or validation.
- **Human-Readable Explanation**: Exposes `explain_action()` to translate raw audit entries into plain-language summaries on demand.

---

## 3. End-to-End Request Flow

The diagram below illustrates the life of a request through the entire INA pipeline:

```
+------+      +-----------+      +---------------+      +------------+      +------------+      +------------+
| User |      | Assistant |      | Policy Engine |      | MCP Server |      |  Network   |      | Validation |
+------+      +-----------+      +---------------+      +------------+      +------------+      +------------+
   |                |                    |                    |                   |                   |
   | 1. Command     |                    |                    |                   |                   |
   |--------------->|                    |                    |                   |                   |
   |                | 2. Parse Intent    |                    |                   |                   |
   |                | (LLM / Regex)      |                    |                   |                   |
   |                |------------------->|                    |                   |                   |
   |                |    3. check_policy |                    |                   |                   |
   |                |<-------------------|                    |                   |                   |
   |                |    PolicyDecision  |                    |                   |                   |
   |                |                    |                    |                   |                   |
   |                |-- [If DENIED: Halt & Log Audit] ------> | (Audit Log)       |                   |
   |                |                                         |                   |                   |
   |                | 4. Invoke MCP Tool                      |                   |                   |
   |                |---------------------------------------->|                   |                   |
   |                |                                         | 5. Execute Command|                   |
   |                |                                         | (nft / tc via CLI)|                   |
   |                |                                         |------------------>|                   |
   |                |                                         |<------------------|                   |
   |                |                                         |    Subprocess Res |                   |
   |                |                                         |                   |                   |
   |                | 6. Tool Result (APPLIED / FAILED)       |                   |                   |
   |                |<----------------------------------------|                   |                   |
   |                |                                                             |                   |
   |                | 7. Validate Network State (check_block / check_bandwidth)   |                   |
   |                |-------------------------------------------------------------------------------->|
   |                |                                                             |  8. Ping / iperf3 |
   |                |                                                             |  across bridge    |
   |                |                                                             |<----------------->|
   |                |<--------------------------------------------------------------------------------|
   |                |    ValidationResult (passed=True/False)                                         |
   |                |                                                                                 |
   | 9. Formatted   |                                                                                 |
   |    Report      |                                                                                 |
   |<---------------|                                                                                 |
```

### Stage Details:
1. **User Input**: Operator submits a command (e.g., `"block client1"`).
2. **Intent Parsing**: The Assistant parses the input into an `Intent(action=BLOCK_CLIENT, target="client1")`. Conversational queries (greetings, general help) are returned directly without contacting backend subsystems.
3. **Policy Evaluation**: The Policy Engine checks `rules.yaml`. If target is protected (e.g., `server`) or the rate is out of bounds, execution halts with a policy denial message, and an audit record is stored.
4. **MCP Tool Invocation**: The Assistant invokes the appropriate MCP tool (`block_client`).
5. **Kernel Enforcement**: The MCP tool resolves `client1` to `172.20.0.2` via Docker SDK and executes the required `nftables` or `tc` command inside the appropriate container namespace.
6. **Execution Logging**: The outcome (`APPLIED` or `FAILED`) is appended to `audit_log.jsonl`. If execution fails, the pipeline halts before validation.
7. **Validation**: The Validation Monitor selects a non-target peer container (`client2`) and executes a test ping or `iperf3` test to confirm the physical network behavior.
8. **User Response**: The Assistant formats policy decisions, tool results, and validation metrics into a Rich console table and natural language summary.

---

## 4. Core Network Operations

### 4.1 Block Client (`block_client`)
- **Intent**: Completely isolate a client container from network traffic.
- **Target**: `network-controller` container.
- **Mechanism**: Linux `nftables` bridge filtering.
- **Workflow**:
  1. Resolves target IP dynamically via `network/discovery.py`.
  2. Ensures the bridge table and forward chain exist:
     ```bash
     nft add table bridge network_filter
     nft add chain bridge network_filter forward '{ type filter hook forward priority 0; policy accept; }'
     nft add set bridge network_filter blocked_clients '{ type ipv4_addr; }'
     ```
  3. Inserts drop rules matching source or destination IP against the `@blocked_clients` set:
     ```bash
     nft add rule bridge network_filter forward ip saddr @blocked_clients drop
     nft add rule bridge network_filter forward ip daddr @blocked_clients drop
     ```
  4. Adds target IP to `@blocked_clients`:
     ```bash
     nft add element bridge network_filter blocked_clients { 172.20.0.2 }
     ```
  5. **Idempotency**: If `File exists` is returned, the operation succeeds gracefully.
  6. **Validation**: `check_block()` issues 3 ICMP pings from `client2` to `172.20.0.2`. `100% packet loss` validates successful isolation.

### 4.2 Unblock Client (`unblock_client`)
- **Intent**: Restore full network access to a previously blocked container.
- **Target**: `network-controller` and target container.
- **Mechanism**: `nftables` element deletion and `tc` qdisc teardown.
- **Workflow**:
  1. Deletes client IP from the `nftables` bridge set:
     ```bash
     nft delete element bridge network_filter blocked_clients { 172.20.0.2 }
     ```
  2. Executes `_cleanup_tc_qdiscs(client)` to clear any lingering traffic control rules on `eth0` and `ifb0`.
  3. **Idempotency**: If the element is already absent, the operation succeeds gracefully.
  4. **Validation**: `check_unblock()` issues 3 ICMP pings from `client2` to `172.20.0.2`. `0% packet loss` confirms full connectivity.

### 4.3 Limit Bandwidth (`limit_bandwidth`)
- **Intent**: Enforce symmetrical (ingress and egress) bandwidth rate limits on a container.
- **Target**: Target client container (e.g., `client1`).
- **Mechanism**: Linux Traffic Control (`tc`) Token Bucket Filter (`tbf`) and Intermediate Functional Block (`ifb`) virtual devices.
- **Workflow**:
  1. Cleans existing qdiscs via `_cleanup_tc_qdiscs(client)`.
  2. Creates and activates an `ifb0` virtual interface inside the container:
     ```bash
     ip link add name ifb0 type ifb
     ip link set dev ifb0 up
     ```
  3. Adds an ingress qdisc to `eth0` and redirects all inbound packets to `ifb0` using `mirred`:
     ```bash
     tc qdisc add dev eth0 handle ffff: ingress
     tc filter add dev eth0 parent ffff: protocol ip u32 match u32 0 0 action mirred egress redirect dev ifb0
     ```
  4. Applies TBF rate shaping to `ifb0` (Ingress Throttling):
     ```bash
     tc qdisc replace dev ifb0 root tbf rate <rate> burst 32kbit latency 400ms
     ```
  5. Applies TBF rate shaping to `eth0` (Egress Throttling):
     ```bash
     tc qdisc replace dev eth0 root tbf rate <rate> burst 32kbit latency 400ms
     ```
  6. **Atomic Rollback**: If any `tc` command returns an error, `_cleanup_tc_qdiscs()` tears down `ifb0` and reverts `eth0` to prevent orphaned interfaces or corrupt qdisc trees.
  7. **Validation**: `check_bandwidth()` executes `docker exec client1 iperf3 -c 172.20.0.3 -t 5` against `server`. Measured bandwidth must fall within 20% of `<rate>`.

### 4.4 Status / Query (`get_status`)
- **Intent**: Report real-time state of the managed network and containers.
- **Workflow**:
  1. Calls Docker Engine API to inspect container status (running/exited) and dynamic IP allocations.
  2. Probes Layer-3 ICMP reachability from `server`.
  3. Queries live `nftables` bridge set on `network-controller`:
     ```bash
     nft list set bridge network_filter blocked_clients
     ```
  4. Inspects live traffic control qdiscs on container interfaces:
     ```bash
     tc qdisc show dev eth0
     ```
  5. Synthesizes reachability, firewall drop state, and active bandwidth limits into a structured status payload for display.

---

## 5. Component Communication & Data Contracts

Data passing between INA components uses strongly-typed dataclasses, Pydantic models, and standardized dictionary schemas:

| Pipeline Transition | Sender -> Receiver | Data Contract / Object | Key Fields / Schema |
| :--- | :--- | :--- | :--- |
| **Parsing** | Assistant -> Policy Engine | `Intent` (dataclass) | `action: ActionType`<br>`target: str`<br>`params: dict`<br>`raw_text: str` |
| **Authorization** | Policy Engine -> Assistant | `PolicyResult` / `PolicyDecision` | `allowed: bool`<br>`reason: str`<br>`rule_id: str` |
| **Tool Execution** | Assistant -> MCP Server | Function Arguments | `client: str`, `rate: str` |
| **Execution Result** | MCP Server -> Assistant | MCP Result Dict | `{"status": "success"\|"failure", "action": str, "client": str, "message": str, "details": dict}` |
| **Validation Request** | Assistant -> Validation Monitor | `ValidationRequest` (Pydantic) | `operation: "block"\|"unblock"\|"bandwidth"`<br>`client: str`<br>`rate: Optional[str]` |
| **Validation Result** | Validation Monitor -> Assistant | `ValidationResult` (Pydantic) | `operation: str`<br>`client: str`<br>`target_ip: str`<br>`passed: bool`<br>`message: str`<br>`ping: Optional[str]`<br>`measured_rate: Optional[float]` |
| **Audit Trail** | System -> `audit_log.jsonl` | JSON Record | `{"timestamp": ISO8601, "action": str, "params": dict, "status": "ALLOWED"\|"DENIED"\|"APPLIED"\|"FAILED", "reason": str}` |

---

## 6. Docker Network Topology & Control Bridge

### 6.1 Subnet & Container Design

```
+----------------------------------------------------------------------------+
|                          Host Operating System                             |
|                                                                            |
|  +----------------------------------------------------------------------+  |
|  |             network-controller (network_mode: host)                 |  |
|  |  * Privileged container (privileged: true)                           |  |
|  |  * Direct access to Linux kernel netfilter & host network interfaces |  |
|  |  * Executes 'nft' bridge rules on inter-container forwarded frames    |  |
|  +----------------------------------------------------------------------+  |
|                                     |                                      |
|                                     | Controls bridge filter rules         |
|                                     v                                      |
|  +----------------------------------------------------------------------+  |
|  |               Docker Bridge: project-net (172.20.0.0/24)             |  |
|  |                                                                      |  |
|  |  +------------------+  +------------------+  +--------------------+  |  |
|  |  |     client1      |  |     client2      |  |       server       |  |  |
|  |  |   172.20.0.2     |  |   172.20.0.4     |  |    172.20.0.3      |  |  |
|  |  |   CAP_NET_ADMIN  |  |   CAP_NET_ADMIN  |  |   CAP_NET_ADMIN    |  |  |
|  |  | (tc eth0 + ifb0) |  |  (ping validator)|  | (HTTP:5000, iperf3)|  |  |
|  |  +------------------+  +------------------+  +--------------------+  |  |
|  +----------------------------------------------------------------------+  |
+----------------------------------------------------------------------------+
```

### 6.2 The Role of the MCP Server as Control Bridge
The MCP Server acts as an abstraction layer between high-level Python code and low-level Linux namespaces:
1. **Separation of Concerns**: Unprivileged components (Assistant CLI, LLM parsers) never execute raw shell commands or interact with the kernel directly.
2. **Namespace Traversal**: Bridges host user-space into container namespaces via parameterized Docker exec calls (`["docker", "exec", container, ...]`).
3. **Privileged Controller Mediation**: Rather than granting full host root access to the assistant application, firewall modifications are routed specifically through the `network-controller` container, which holds the required kernel netfilter capabilities.

---

## 7. Security Boundaries & Defense-in-Depth

INA implements multi-layer defense-in-depth to protect infrastructure stability:

```
[User Input]
     |
     v
[Layer 1: Input Sanitization] ------> Rejects shell metacharacters (; && | ` $), flags (-*), 
     |                                and control-plane names (network-controller, host, root)
     v
[Layer 2: Policy Engine] -----------> Evaluates rules.yaml whitelist, protected targets (server),
     |                                and bandwidth range constraints (1-20 Mbps)
     v
[Layer 3: MCP Internal Validation] -> Independent policy & parameter check inside mcp_server/tools.py
     |                                (prevents bypass if MCP tools are called directly)
     v
[Layer 4: Safe Subprocess Execution]  Executes commands as argument lists (subprocess.run([...]))
     |                                with timeouts (10s) and NO shell=True
     v
[Layer 5: Kernel Enforcement] ------> Container-isolated tc namespaces & nftables bridge tables
```

### Why Policy Evaluation Precedes Network Modification
1. **Integrity of Protected Nodes**: Critical infrastructure (e.g., `server`) must never experience accidental outages caused by misclassified LLM intents.
2. **Prevention of Corrupted State**: Validating actions and bounds *before* executing commands prevents partial or conflicting rules (e.g., applying tc filters with invalid burst/rate settings).
3. **Auditing of Unauthorized Attempts**: Immediate pre-execution evaluation ensures every attempted policy violation is recorded in `audit_log.jsonl` with status `DENIED`.

---

## 8. Project Directory Structure

Only key project directories and files relevant to the operational architecture are listed below:

```
ina/
├── assistant/
│   ├── client.py                 # Interactive Assistant CLI, LLM parser & orchestrator
├── mcp_server/
│   ├── server.py                 # FastMCP server exposing network management tools
│   ├── tools.py                  # Core tool implementations (block, unblock, limit, status)
├── policy/
│   ├── policy_engine.py          # Policy Engine evaluation logic & rate parsers
│   ├── rules.yaml                # Declarative policy rules (protected clients, limits)
│   ├── audit_log.py              # Audit logging logic & explanation utilities
│   └── audit_log.jsonl           # Append-only JSONL audit trail
├── validation/
│   ├── monitor.py                # Active validation monitor (ping & iperf3 tests)
│   └── models.py                 # Pydantic data contracts (ValidationRequest/Result)
├── network/
│   ├── docker-compose.yml        # Multi-container network topology definition
│   ├── Dockerfile                # Image definition (iproute2, nftables, iperf3, tc)
│   └── discovery.py              # Dynamic Docker SDK container discovery
├── tests/
│   ├── test_ina_focused.py       # Focused deterministic test suite (35 tests)
│   ├── test_policy.py            # Unit tests for policy enforcement
│   ├── test_mcp.py               # Unit tests for MCP server tools
│   ├── test_network.py           # Integration tests for network operations
│   └── test_validation.py        # Unit tests for validation models & monitor
├── docs/
│   ├── architecture.md           # System architecture documentation (this document)
│   ├── INA_SETUP.md              # Detailed environment setup & operations guide
│   ├── mcpserver.md              # MCP server specifications
│   ├── network.md                # Low-level network implementation guide
│   └── policy.md                 # Policy engine & rules documentation
├── .env.example                  # Template for API credentials (OPENROUTER_API_KEY)
└── requirements.txt              # Project dependencies (fastmcp, docker, pydantic, rich, etc.)
```

---

## 9. System Startup & Quick-Start

To run the complete INA system:

### 1. Prerequisites & Environment Setup
```bash
# Create virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure OpenRouter API Key (optional; deterministic regex fallback used if unset)
cp .env.example .env
```

### 2. Start Network Infrastructure
```bash
# Launch Docker bridge network and container topology
docker compose -f network/docker-compose.yml up -d --build

# Verify container status
docker compose -f network/docker-compose.yml ps
```

### 3. Run the Assistant
```bash
# Launch interactive assistant CLI
python3 -m assistant.client
```

### 4. Run Automated Test Suite
```bash
# Run complete test suite across all subsystems
pytest -v
```

> For comprehensive deployment instructions, hardware requirements, manual kernel inspection commands, and troubleshooting procedures, see [`docs/INA_SETUP.md`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/docs/INA_SETUP.md).
