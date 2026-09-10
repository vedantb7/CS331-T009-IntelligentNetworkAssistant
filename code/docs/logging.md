# Audit Logging & Traceability

---

## 1. Overview & Purpose

The **Audit Logging** subsystem of the **Intelligent Network Configuration Assistant (INA)** provides a tamper-evident, append-only chronological record of all security decisions and network modifications.

### Why Audit Logging is Necessary in INA
Because INA allows operators to control network isolation and bandwidth throttling through natural-language commands, maintaining an immutable ledger is essential for:
- **Accountability & Non-Repudiation**: Knowing exactly which operations were requested, what parameters were supplied, and whether the system approved or denied them.
- **Explainability**: Enabling the AI Assistant to explain its past decisions to human operators (e.g. *"Why was client1 blocked?"* or *"Why was the request to limit server denied?"*).
- **Incident Investigation & Debugging**: Providing precise timestamps and error messages when low-level kernel configurations or container commands fail.

---

## 2. Implementation Location & Subsystem Structure

> **Architectural Note**: Audit logging is implemented under the [`policy/`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/policy/) directory, **not** in a separate `logger/` directory.

The subsystem consists of two files:

```text
policy/
├── audit_log.py          # Core logging functions & natural language explanation logic
└── audit_log.jsonl       # Append-only JSON Lines ledger of all recorded actions
```

### Module Responsibilities:
* [`policy/audit_log.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/policy/audit_log.py):
  - `log_action(action, params, status, reason)`: Constructs a structured log dictionary, timestamps it, and appends it as a single JSON line to `audit_log.jsonl`.
  - `get_all_logs()`: Parses the JSONL file and returns a list of dictionaries.
  - `explain_action(index=-1)`: Converts the latest (or specified) log entry into a human-readable English sentence.
* [`policy/audit_log.jsonl`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/policy/audit_log.jsonl):
  - The physical file where entries are stored. Each line is an independent, parseable JSON object.

---

## 3. Logged Actions & Event Lifecycle

Audit records are generated at two specific checkpoints during request execution:

```text
       +-------------------------------------------------------------+
       |               User Request: "block client1"                 |
       +-------------------------------------------------------------+
                                      |
                                      v
       +-------------------------------------------------------------+
       |                  1. Policy Engine Check                     |
       |  Evaluates request against rules.yaml whitelist & bounds    |
       +-------------------------------------------------------------+
                 |                                      |
       [DENIED]  |                                      |  [ALLOWED]
                 v                                      v
       +--------------------+                 +--------------------+
       |  Log Entry: DENIED |                 | Log Entry: ALLOWED |
       |  (Pipeline Halts)  |                 +--------------------+
       +--------------------+                           |
                                                        v
                                      +-----------------------------------+
                                      |       2. MCP Server Execution     |
                                      | Applies kernel commands (nft, tc) |
                                      +-----------------------------------+
                                                |               |
                                      [SUCCESS] |               | [FAILURE]
                                                v               v
                                      +----------------+ +----------------+
                                      | Log: APPLIED   | | Log: FAILED    |
                                      +----------------+ +----------------+
                                                |
                                                v
                                      +-----------------------------------+
                                      |      3. Validation Monitor        |
                                      | Empirically verifies traffic flow |
                                      +-----------------------------------+
```

### Supported Status Transitions:
1. **`ALLOWED`**: The Policy Engine approved the proposed request against `rules.yaml`.
2. **`DENIED`**: The Policy Engine rejected the request (e.g. protected client, rate out of bounds, unknown client, or disallowed action).
3. **`APPLIED`**: The MCP Server successfully executed the kernel configuration commands via Docker.
4. **`FAILED`**: Operational error occurred during command execution (e.g. timeout, missing Docker binary, or command exit code error).

---

## 4. Log Entry Schema

Each line in `policy/audit_log.jsonl` contains exactly five standardized fields:

| Field | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| **`timestamp`** | `str` (ISO8601) | Local system timestamp recorded to second precision. | `"2026-09-10T03:56:30"` |
| **`action`** | `str` | Name of the requested operation. | `"block_client"`, `"limit_bandwidth"`, `"unblock_client"`, `"get_status"` |
| **`params`** | `dict` | Key-value dictionary of parameters supplied to the action. | `{"client": "client1"}`, `{"client": "client1", "rate": "10mbit"}` |
| **`status`** | `str` | State of the action. Must be one of `ALLOWED`, `DENIED`, `APPLIED`, or `FAILED`. | `"APPLIED"` |
| **`reason`** | `str` | Human-readable explanation of why the decision or state occurred. | `"Client client1 blocked successfully at bridge level."` |

### Actual JSONL Record Examples:

#### 1. Successful Block Operation (Allowed & Applied)
```json
{"timestamp": "2026-09-10T03:56:30", "action": "block_client", "params": {"client": "client1"}, "status": "ALLOWED", "reason": "'client1' is not protected. Block permitted."}
{"timestamp": "2026-09-10T03:56:31", "action": "block_client", "params": {"client": "client1"}, "status": "APPLIED", "reason": "Client client1 blocked successfully at bridge level."}
```

#### 2. Policy Denial (Protected Server Target)
```json
{"timestamp": "2026-09-10T00:18:01", "action": "block_client", "params": {"client": "server"}, "status": "DENIED", "reason": "'server' is a protected client and cannot be blocked."}
```

#### 3. Policy Denial (Bandwidth Out of Bounds)
```json
{"timestamp": "2026-09-08T11:20:28", "action": "limit_bandwidth", "params": {"client": "client1", "rate_mbps": 55, "rate": "55mbit"}, "status": "DENIED", "reason": "Requested rate 55.0mbit is outside the allowed range (1.0mbit - 20.0mbit)."}
```

#### 4. Successful Bandwidth Throttling (Bi-Directional)
```json
{"timestamp": "2026-09-10T00:42:32", "action": "limit_bandwidth", "params": {"client": "client2", "rate_mbps": 8, "rate": "8mbit"}, "status": "ALLOWED", "reason": "8.0mbit is within allowed range. Limit permitted."}
{"timestamp": "2026-09-10T00:42:33", "action": "limit_bandwidth", "params": {"client": "client2", "rate_mbps": 8, "rate": "8mbit"}, "status": "APPLIED", "reason": "Bi-directional (ingress & egress) bandwidth limited to 8mbit successfully."}
```

#### 5. Operational Failure
```json
{"timestamp": "2026-09-10T04:20:15", "action": "block_client", "params": {"client": "client1"}, "status": "FAILED", "reason": "Operation timed out while communicating with network-controller."}
```

---

## 5. Audit Logging vs. Network Validation

It is important not to confuse Audit Logging with Network Validation:

* **Audit Logging (`policy/audit_log.py`)**: A passive historical record of system events. It tracks what was requested, which rule approved or denied it, and whether the command succeeded. It does not send packets across interfaces.
* **Network Validation (`validation/monitor.py`)**: An active empirical test. It generates real network traffic (ICMP pings across the bridge and `iperf3` throughput streams) to verify that network packets are physically conforming to the applied policy.

---

## 6. How Logging Supports Security, Debugging, and Explainability

### 6.1 Security Auditing & Governance
- **Proof of Enforcement**: Proves that unauthorized operations (e.g. attempting to block `server`) were stopped before reaching the kernel.
- **Traceability**: If an unexpected outage occurs, administrators can examine the exact sequence of commands and timestamps leading up to the event.

### 6.2 Natural Language Explainability (`explain_action`)
INA uses the audit log to explain its actions to human operators:
```python
from policy.audit_log import explain_action

# Explain the most recent action:
print(explain_action(-1))
# Output: "I denied the request to block_client with params {'client': 'server'} because: 'server' is a protected client and cannot be blocked."
```

### 6.3 Fault Diagnosis
If an operation fails, the `FAILED` entry records the specific reason (such as a Docker daemon communication failure or a subprocess timeout) without exposing raw stack traces to end users.

---

## 7. Practical Commands for Inspecting Logs

### View Recent Log Entries (CLI)
```bash
# View the last 10 audit log entries:
tail -n 10 policy/audit_log.jsonl

# Pretty-print all log entries using jq:
jq . policy/audit_log.jsonl

# Filter for all DENIED policy events:
grep '"status": "DENIED"' policy/audit_log.jsonl | jq .

# Filter for all FAILED operational events:
grep '"status": "FAILED"' policy/audit_log.jsonl | jq .
```

### Programmatic Python Inspection
```python
from policy.audit_log import get_all_logs, explain_action

# Retrieve all entries as Python dictionaries:
logs = get_all_logs()
print(f"Total actions logged: {len(logs)}")

# Print human-readable explanations of the last 3 events:
for i in range(-3, 0):
    print(explain_action(i))
```

---

## 8. Current Implementation Limitations & Residual Risks

1. **Absence of Log Rotation**:
   - `policy/audit_log.jsonl` is opened in append mode (`"a"`). It grows indefinitely over time and does not implement automatic size-based rotation, compression, or archiving.
2. **Local Unencrypted File Storage**:
   - Logs are stored on the local filesystem. They are not cryptographically signed, hashed into an immutable merkle tree, or forwarded to a remote SIEM / Syslog daemon. If a host account is compromised, the file could theoretically be edited.
3. **File Lock Concurrency**:
   - `log_action()` performs standard Python file writes without explicit OS-level file locking (`fcntl`). While atomic on Linux for small writes under `PIPE_BUF` (4096 bytes), heavy concurrent multi-process logging could theoretically experience line interleaving.
4. **Single-Tenant Scope**:
   - Log entries record *what* was requested, but do not record *which user* issued the command, as the current CLI operates in a single-operator local mode without user accounts.
