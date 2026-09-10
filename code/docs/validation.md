# Network Validation & Monitoring

---

## 1. Overview & Purpose

The **Validation Monitor** (`validation/`) is the post-execution verification subsystem of the **Intelligent Network Configuration Assistant (INA)**. 

### Why Validation is Essential
In network automation, a command exiting with code 0 does not prove that network traffic is actually behaving as intended. For example:
- An `nftables` or `iptables` rule might be inserted in the wrong chain priority or bypassed by host routing.
- A `tc` queuing discipline might be attached to an interface without active traffic traversing it.
- A peer container might be offline or unresponsive despite rule creation succeeding.

The Validation Monitor performs **active, empirical verification** by generating live synthetic traffic across the network bridge to confirm that the requested state has been physically realized before confirming completion to the operator.

---

## 2. Validation vs. Audit Logging

A common point of confusion is the distinction between validation and audit logging. In INA, these are separate subsystems with complementary responsibilities:

| Property | Network Validation (`validation/`) | Audit Logging (`policy/audit_log.py`) |
| :--- | :--- | :--- |
| **Nature** | **Active & Empirical**: Generates synthetic network packets. | **Passive & Historical**: Records state transitions to disk. |
| **Mechanism** | ICMP pings and `iperf3` TCP stream benchmarks. | Append-only JSONL writes (`policy/audit_log.jsonl`). |
| **Question Answered** | *"Is the network traffic physically behaving as requested?"* | *"Who requested this change, what was decided, and when?"* |
| **Output** | Quantitative metrics (`packet_loss`, `measured_rate`, `passed: bool`). | Chronological event records (`ALLOWED`, `DENIED`, `APPLIED`, `FAILED`). |
| **Execution Point** | Step 4 in the pipeline (after MCP execution). | Triggered at Policy evaluation and MCP execution. |

---

## 3. Architecture & File Breakdown

The validation subsystem consists of two core Python modules and a manual testing playbook:

```text
validation/
├── models.py             # Pydantic v2 data contracts (ValidationRequest, ValidationResult)
├── monitor.py            # Core active monitoring routines (ping, iperf3, CLI entrypoint)
└── manual_testing.md     # Step-by-step testing and verification procedures
```

### 3.1 `validation/models.py`
Defines strict schemas using Pydantic v2:
- **`ValidationRequest`**: Validates request parameters (`operation: "block" | "unblock" | "bandwidth"`, `client: str`, `rate: Optional[str]`). Normalizes rate strings and validates units (`kbit`, `mbit`, `gbit`).
- **`ValidationResult`**: Encapsulates the evaluation outcome:
  - `operation`: Operation tested (`"BLOCK"`, `"UNBLOCK"`, `"BANDWIDTH"`).
  - `client`: Target container name.
  - `target_ip`: Dynamically resolved IPv4 address.
  - `passed: bool`: Boolean determination of test success.
  - `message: str`: Human-readable explanation.
  - `ping: Optional[str]`: Packet loss summary string.
  - `expected_rate`, `measured_rate`, `tolerance`: Bandwidth metrics (for bandwidth operations).

### 3.2 `validation/monitor.py`
Contains the implementation of live synthetic network tests:
- `get_ip(client)`: Resolves container IPv4 address using `network/discovery.py`.
- `pick_source_container(target_client)`: Selects a non-target peer container (e.g. `client2`) to issue pings across the bridge.
- `run_ping(source_container, target_ip)`: Executes `ping -c 3 -W 1` inside `source_container` via `docker exec`.
- `check_block(client)`: Asserts that packet loss is **100%**.
- `check_unblock(client)`: Asserts that packet loss is **< 100%** (0% loss expected).
- `run_iperf(client, server_ip, reverse)`: Runs `iperf3 -c server -t 5 -J` (with `-R` for reverse ingress shaping).
- `check_bandwidth(client, expected_rate)`: Measures both egress and ingress throughput against `server` and asserts both fall within ±20% of `expected_rate`.
- `main()`: Command-line interface allowing manual/on-demand execution.

---

## 4. Implemented Validation Checks

### 4.1 Blocking Verification (`check_block`)
- **How It Works**:
  1. Identifies a peer container using `pick_source_container(client)` (e.g., if blocking `client1`, it selects `client2`).
  2. Executes: `docker exec client2 ping -c 3 -W 1 172.20.0.2`.
  3. Parses the stdout/stderr for packet loss percentage.
- **Pass Condition**: `packet_loss >= 100.0%`.
- **Fail Condition**: Any response packets received (`packet_loss < 100.0%`).

### 4.2 Unblocking Verification (`check_unblock`)
- **How It Works**:
  1. Issues 3 ICMP pings from the peer container across the bridge hook to the target IP.
  2. Parses packet loss percentage.
- **Pass Condition**: `packet_loss < 100.0%` (normally 0% loss on a healthy bridge).
- **Fail Condition**: `packet_loss >= 100.0%` (destination unreachable).

### 4.3 Bandwidth Limiting Verification (`check_bandwidth`)
- **How It Works**:
  1. Ensures the `iperf3` daemon is listening on `server:5201` via `ensure_iperf_server()`.
  2. **Egress Throughput Test**: Runs `docker exec client iperf3 -c 172.20.0.3 -t 5 -J`.
  3. **Ingress Throughput Test**: Runs `docker exec client iperf3 -c 172.20.0.3 -t 5 -J -R` (reverse mode).
  4. Parses JSON output (`-J`), extracts `bits_per_second`, and converts to Mbps.
  5. Computes measured rate as average: `(egress + ingress) / 2.0`.
- **Pass Condition**: Both egress and ingress rates fall within the **±20% tolerance** band:
  $$\text{expected} \times (1 - 0.20) \le \text{measured} \le \text{expected} \times (1 + 0.20)$$
- **Fail Condition**: Either egress or ingress falls outside the tolerance range.

---

## 5. End-to-End Validation Flow

Validation operates automatically as the final phase in INA's execution pipeline:

```text
       +-------------------------------------------------------------+
       |               User Command: "block client1"                 |
       +-------------------------------------------------------------+
                                      |
                                      v
       +-------------------------------------------------------------+
       |                     1. Policy Check                         |
       |       check_policy("block_client", {"client": "client1"})   |
       |       Result: ALLOWED                                       |
       +-------------------------------------------------------------+
                                      |
                                      v
       +-------------------------------------------------------------+
       |                     2. MCP Execution                        |
       |       mcp_tools.block_client("client1")                     |
       |       Kernel: nft add element bridge network_filter ...     |
       |       Result: APPLIED                                       |
       +-------------------------------------------------------------+
                                      |
                                      v
       +-------------------------------------------------------------+
       |                  3. Validation Monitor                      |
       |  a. pick_source_container("client1") -> "client2"           |
       |  b. docker exec client2 ping -c 3 -W 1 172.20.0.2           |
       |  c. parse_packet_loss() -> 100.0%                           |
       |  d. ValidationResult(passed=True, operation="BLOCK")        |
       +-------------------------------------------------------------+
                                      |
                                      v
       +-------------------------------------------------------------+
       |                 4. Assistant User Report                    |
       |  Displays Rich status table & confirmation:                 |
       |  "Client client1 is blocked and verified (100% loss)."      |
       +-------------------------------------------------------------+
```

---

## 6. Integration: Automatic vs. Manual Execution

INA supports **both** automatic and manual validation:

### 6.1 Automatic Pipeline Integration
Inside [`assistant/client.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/assistant/client.py), `NetworkAssistant` wraps `validation.monitor` via `ValidationMonitorAdapter`:
- Whenever a network action (`BLOCK_CLIENT`, `UNBLOCK_CLIENT`, `LIMIT_BANDWIDTH`) executes successfully, `self._validate(intent)` is triggered automatically.
- If MCP execution fails, validation is skipped (`halted_at: "execution"`).
- If validation fails, the CLI alerts the operator that execution succeeded but validation was `UNCONFIRMED`.

### 6.2 Manual / Standalone CLI Execution
Developers and operators can trigger validation independently using `validation/monitor.py`:

```bash
# Activate virtual environment
source .venv/bin/activate

# 1. Manually validate a blocked client:
python3 validation/monitor.py block client1

# 2. Manually validate an unblocked client:
python3 validation/monitor.py unblock client1

# 3. Manually validate a bandwidth limit:
python3 validation/monitor.py bandwidth client1 10mbit
```

---

## 7. Sample Validation Outputs

### Successful Block Validation
```text
Validation: BLOCK
Target: client1
Target IP: 172.20.0.2
Ping: 100.0% packet loss
Result: PASS
Message: client1 is successfully blocked.
```

### Failed Block Validation (Client Still Reachable)
```text
Validation: BLOCK
Target: client1
Target IP: 172.20.0.2
Ping: 0.0% packet loss
Result: FAIL
Message: client1 is still reachable. Packet loss: 0.0%.
```

### Successful Bandwidth Validation
```text
Validation: BANDWIDTH
Target: client1
Target IP: 172.20.0.2
Expected Rate: 10.00 Mbps
Measured Rate: 9.85 Mbps
Tolerance: ±20%
Result: PASS
Message: Bi-directional bandwidth is within expected range (Egress: 9.90 Mbps, Ingress: 9.80 Mbps).
```

### Failed Bandwidth Validation
```text
Validation: BANDWIDTH
Target: client1
Target IP: 172.20.0.2
Expected Rate: 10.00 Mbps
Measured Rate: 2.10 Mbps
Tolerance: ±20%
Result: FAIL
Message: Measured bandwidth outside expected range (Egress: 2.10 Mbps [passed=False], Ingress: 2.10 Mbps [passed=False]).
```

---

## 8. Critical Design Decisions & Limitations

### 8.1 The "Host Ping Bypass" Issue
- **Root Cause**: The firewall rules applied by `network-controller` reside on the Linux bridge forward hook. A ping issued directly from the host machine (or from a container with `network_mode: host`) uses Layer-3 host routing and never traverses the bridge forward hook.
- **Solution**: `pick_source_container()` forces pings to originate from a sibling container (e.g. `client2`) attached to `project-net`, ensuring traffic crosses the bridge where drop rules are actively enforced.

### 8.2 System Limitations
1. **Single-Container Networks**: If only one container exists on the bridge, `pick_source_container()` cannot find an independent peer and raises `ValueError`.
2. **iperf3 Server Concurrency**: `server` runs a single `iperf3` daemon on port 5201. Simultaneous concurrent validation runs from multiple clients will experience socket contention; validation tests must execute sequentially.
3. **TCP Ramp-Up Latency**: Bandwidth tests under 3 seconds can produce noisy measurements due to TCP slow-start; INA enforces `BANDWIDTH_TEST_DURATION = 5` seconds with a `±20%` tolerance window to accommodate window scaling.

---

## 9. Verification & Automated Tests

Automated tests for the validation models and monitor routines are located in [`tests/test_validation.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/tests/test_validation.py) and [`tests/test_ina_focused.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/tests/test_ina_focused.py):

```bash
# Run validation unit tests:
.venv/bin/pytest tests/test_validation.py -v

# Run focused validation & error handling tests:
.venv/bin/pytest tests/test_ina_focused.py -k "TestValidationMonitor" -v
```
All unit tests mock subprocess invocations, ensuring deterministic execution without requiring running Docker daemons during CI runs.
