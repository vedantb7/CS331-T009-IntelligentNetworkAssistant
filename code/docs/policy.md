# Policy Engine & Security Governance

---

## 1. Overview & Purpose

The **Policy Engine** (`policy/`) is the central deterministic authorization layer of the **Intelligent Network Configuration Assistant (INA)**. It acts as an immutable gatekeeper between the natural-language Assistant layer and the kernel execution layer (MCP Server).

### Why INA Needs a Policy Engine
Large Language Models (LLMs) are probabilistic and cannot guarantee deterministic safety boundaries on their own. In a network management system, allowing an unverified LLM intent to execute arbitrary system-level commands could lead to:
- Disconnecting critical infrastructure (e.g. accidentally isolating the primary server).
- Setting extreme bandwidth throttling values (e.g. 0 bps or 100 Gbps).
- Command injection or targeting unauthorized containers and host interfaces.

The Policy Engine solves this by enforcing a strict **zero-trust safety model**: every proposed network operation is evaluated against declarative rules in `policy/rules.yaml` before any system command can be executed.

---

## 2. Zero-Trust & Pre-Execution Safety Role

INA follows the principle that **no action is permitted by default**.

```text
+-------------+      +-------------------+      +---------------------+      +----------------+
| User Prompt | ---> | LLM Intent Parser | ---> |    Policy Engine    | ---> |   MCP Server   |
+-------------+      +-------------------+      | (check_policy)      |      | (Kernel Config)|
                                                +---------------------+      +----------------+
                                                           |
                                            [Allowed?]     |
                                            +--------------+--------------+
                                            |                             |
                                      NO (DENIED)                    YES (ALLOWED)
                                            |                             |
                                            v                             v
                                  +-------------------+         +-------------------+
                                  | Halt Pipeline &   |         | Execute Kernel    |
                                  | Record Audit Log  |         | Action via Docker |
                                  +-------------------+         +-------------------+
```

### Key Safety Guarantees
1. **Pre-Execution Denial**: Unauthorized, malformed, or unsafe requests are stopped immediately at the policy layer. No subprocesses are spawned, and no Docker or kernel configurations are touched.
2. **Dual-Layer Defense**: Even if an external caller bypasses the Assistant and attempts to call MCP tools directly, every tool in `mcp_server/tools.py` independently verifies `check_policy()` before applying changes.
3. **Immutability of Critical Nodes**: Infrastructure marked as `protected_clients` (e.g., `server`) cannot be blocked or throttled under any circumstances.

---

## 3. Policy Configuration: `policy/rules.yaml`

The rulebook is stored in [`policy/rules.yaml`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/policy/rules.yaml). It uses plain YAML so network administrators can adjust policies without modifying Python code.

```yaml
# policy/rules.yaml
# The rulebook that check_policy() reads to decide ALLOW or DENY.

# Clients that can never be blocked or bandwidth-limited.
protected_clients:
  - server

# Any bandwidth request must fall within this range.
bandwidth:
  minimum: 1mbit
  maximum: 20mbit

# The only actions the system is allowed to perform at all.
allowed_actions:
  - block_client
  - unblock_client
  - limit_bandwidth
  - get_status

# Client names that actually exist in the Docker network.
known_clients:
  - client1
  - client2
  - server
```

### Field Breakdown:
- **`protected_clients`**: List of nodes immune to disruptive actions (blocking or rate limiting).
- **`bandwidth`**: Absolute lower (`1mbit`) and upper (`20mbit`) bounds for traffic shaping.
- **`allowed_actions`**: Strict whitelist of permissible operation verbs. Any action not explicitly enumerated is rejected.
- **`known_clients`**: Whitelist of valid Docker container hostnames on the managed network.

---

## 4. Policy Engine Implementation: `policy/policy_engine.py`

The policy engine exports the core verification function:

```python
def check_policy(action: str, params: dict) -> PolicyResult:
```

### The `PolicyResult` Dataclass
Decisions are encapsulated in a structured `PolicyResult`:
```python
@dataclass
class PolicyResult:
    allowed: bool      # True if operation is permitted; False otherwise
    reason: str        # Human-readable justification of the decision
    rule_id: str = ""  # Machine-readable identifier of the rule triggered
```

### Input Validation Routines
Before evaluating business logic, `policy_engine.py` validates input structure:
1. **Client Name Validation (`_is_valid_client_name`)**:
   - Must be a string matching `^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$`.
   - Cannot start with `-` (prevents command-line argument injection).
2. **Rate Parsing & Normalization (`_parse_mbit`)**:
   - Rejects non-numeric/malformed rates via `_RATE_FORMAT_RE`.
   - Normalizes supported units (`mbit`, `mbps`, `mb/s`, `kbit`, `gbit`) to float Mbps values.
   - Requires finite, positive values (`math.isfinite(val) and val > 0`).

---

## 5. Supported Operations & Evaluation Rules

| Action | Required Params | Policy Checks | Success Condition (`allowed: True`) | Denial Conditions (`allowed: False`) |
| :--- | :--- | :--- | :--- | :--- |
| **`block_client`** | `client: str` | 1. Action Whitelist<br>2. Client Syntax<br>3. Known Client Whitelist<br>4. Protected Client Check | Target is valid, known, and **not** in `protected_clients`. | - Invalid/empty client name (`invalid-client`)<br>- Client not in `known_clients` (`known-client`)<br>- Client is in `protected_clients` (`protected-client`) |
| **`unblock_client`** | `client: str` | 1. Action Whitelist<br>2. Client Syntax<br>3. Known Client Whitelist | Target is valid and recognized in `known_clients`. | - Invalid/empty client name (`invalid-client`)<br>- Client not in `known_clients` (`known-client`) |
| **`limit_bandwidth`** | `client: str`, `rate: str\|num` | 1. Action Whitelist<br>2. Client Syntax<br>3. Known Client Whitelist<br>4. Protected Client Check<br>5. Rate Syntax<br>6. Min/Max Range | Target is valid, known, not protected, and `1mbit <= rate <= 20mbit`. | - Client in `protected_clients` (`protected-client`)<br>- Unparseable or non-positive rate (`bandwidth-format`)<br>- Rate < 1 Mbps or > 20 Mbps (`bandwidth-bounds`) |
| **`get_status`** | `client: Optional[str]` | 1. Action Whitelist<br>2. Target Scope Check | Empty, `"all"`, `"network"`, or a recognized client name. | - Specific client queried that is not in `known_clients` (`known-client`)<br>- Invalid client name string (`invalid-client`) |

---

## 6. Complete Decision Flow

```
1. Operator inputs request: "throttle client1 to 5mbps"
2. Assistant parses text into Intent(action="limit_bandwidth", target="client1", params={"rate": "5mbit"})
3. Assistant calls check_policy("limit_bandwidth", {"client": "client1", "rate": "5mbit"})
   a. "limit_bandwidth" in allowed_actions? -> YES
   b. _is_valid_client_name("client1")? -> YES
   c. "client1" in known_clients? -> YES
   d. "client1" in protected_clients? -> NO
   e. 1.0 <= 5.0 <= 20.0? -> YES
   f. Returns PolicyResult(allowed=True, reason="5.0mbit is within allowed range. Limit permitted.", rule_id="bandwidth-allowed")
4. Assistant proceeds to dispatch MCP tool: limit_bandwidth("client1", "5mbit")
5. Audit log records: {"action": "limit_bandwidth", "status": "APPLIED", ...}
```

If any condition in step 3 fails:
- Returns `PolicyResult(allowed=False, reason="...", rule_id="...")`.
- Pipeline halts immediately (`halted_at: "policy"`).
- Audit log records entry with `status: "DENIED"`.
- User receives clear, safe explanation why the request was refused.

---

## 7. Audit Logging Integration (`policy/audit_log.py`)

All policy evaluations and operational outcomes are recorded in [`policy/audit_log.jsonl`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/policy/audit_log.jsonl) to ensure full non-repudiation and traceability:

### Audit Status Transitions:
* **`ALLOWED`**: Policy approved the proposed request.
* **`DENIED`**: Policy rejected the request due to rule violation.
* **`APPLIED`**: MCP Server successfully executed the kernel configuration.
* **`FAILED`**: Operational error occurred during command execution or validation.

### Sample Audit Records:
```json
{"timestamp": "2026-09-10T04:15:30", "action": "block_client", "params": {"client": "client1"}, "status": "APPLIED", "reason": "Client client1 blocked successfully at bridge level."}
{"timestamp": "2026-09-10T04:16:10", "action": "block_client", "params": {"client": "server"}, "status": "DENIED", "reason": "'server' is a protected client and cannot be blocked."}
{"timestamp": "2026-09-10T04:17:05", "action": "limit_bandwidth", "params": {"client": "client1", "rate": "50mbit"}, "status": "DENIED", "reason": "Requested rate 50.0mbit is outside the allowed range (1.0mbit - 20.0mbit)."}
```

### Explaining Past Actions:
The `explain_action(index=-1)` function converts raw log entries into natural language:
```python
from policy.audit_log import explain_action

print(explain_action(-1))
# Output: "I denied the request to block_client with params {'client': 'server'} because: 'server' is a protected client and cannot be blocked."
```

---

## 8. Practical Examples: Allowed vs. Denied Requests

### 8.1 Allowed Requests
```python
# 1. Normal client blocking
check_policy("block_client", {"client": "client1"})
# -> PolicyResult(allowed=True, reason="'client1' is not protected. Block permitted.", rule_id="block-allowed")

# 2. Client unblocking
check_policy("unblock_client", {"client": "client1"})
# -> PolicyResult(allowed=True, reason="Unblock permitted for 'client1'.", rule_id="unblock-allowed")

# 3. Bandwidth within bounds
check_policy("limit_bandwidth", {"client": "client2", "rate": "10mbit"})
# -> PolicyResult(allowed=True, reason="10.0mbit is within allowed range. Limit permitted.", rule_id="bandwidth-allowed")

# 4. Network-wide status query
check_policy("get_status", {})
# -> PolicyResult(allowed=True, reason="Network status query permitted.", rule_id="status-query")
```

### 8.2 Denied Requests
```python
# 1. Protected client protection
check_policy("block_client", {"client": "server"})
# -> PolicyResult(allowed=False, reason="'server' is a protected client and cannot be blocked.", rule_id="protected-client")

# 2. Unknown container
check_policy("block_client", {"client": "client99"})
# -> PolicyResult(allowed=False, reason="'client99' is not a recognized client in this network.", rule_id="known-client")

# 3. Bandwidth rate above maximum (20 Mbps)
check_policy("limit_bandwidth", {"client": "client1", "rate": "50mbit"})
# -> PolicyResult(allowed=False, reason="Requested rate 50.0mbit is outside the allowed range (1.0mbit - 20.0mbit).", rule_id="bandwidth-bounds")

# 4. Bandwidth rate below minimum (1 Mbps)
check_policy("limit_bandwidth", {"client": "client1", "rate": "500kbit"})
# -> PolicyResult(allowed=False, reason="Requested rate 0.5mbit is outside the allowed range (1.0mbit - 20.0mbit).", rule_id="bandwidth-bounds")

# 5. Invalid action
check_policy("restart_container", {"client": "client1"})
# -> PolicyResult(allowed=False, reason="Action 'restart_container' is not in the list of allowed actions.", rule_id="action-not-allowed")

# 6. Injection attempt
check_policy("block_client", {"client": "client1; reboot"})
# -> PolicyResult(allowed=False, reason="A valid client name is required.", rule_id="invalid-client")
```

---

## 9. Modifying Policy Configuration Safely

Because `rules.yaml` is loaded dynamically on each policy check via `load_policy()`, administrators can modify rules without restarting the server:

1. **Changing Bandwidth Thresholds**:
   Edit `policy/rules.yaml` to adjust `bandwidth.minimum` or `bandwidth.maximum`:
   ```yaml
   bandwidth:
     minimum: 500kbit
     maximum: 50mbit
   ```
2. **Adding Known Clients**:
   When spinning up new containers (e.g. `client3`), add them to `known_clients`:
   ```yaml
   known_clients:
     - client1
     - client2
     - client3
     - server
   ```
3. **Safety Constraints**:
   - Do **not** remove `server` from `protected_clients` unless authorized.
   - Maintain valid YAML syntax (indentation and list formats).

---

## 10. Verification & Test Commands

To verify that the Policy Engine and Audit Logging subsystems are functioning correctly:

```bash
# Run the dedicated policy engine unit tests:
.venv/bin/pytest tests/test_policy.py -v

# Run the comprehensive focused test suite covering policy boundaries and injection guards:
.venv/bin/pytest tests/test_ina_focused.py -k "TestPolicyEngine or TestAuditLogging" -v
```

All tests run deterministically in-memory without requiring active Docker containers or host network modifications.
