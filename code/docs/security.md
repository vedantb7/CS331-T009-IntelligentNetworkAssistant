# Security Architecture & Governance

---

## 1. Executive Summary & Security Goals

The **Intelligent Network Configuration Assistant (INA)** operates as an automated control plane for Linux container networking. Because it accepts natural-language requests and translates them into kernel-level modifications, security is a first-class architectural concern.

### Core Security Objectives
1. **Zero-Trust AI Execution**: Never allow Large Language Models (LLMs) to execute system commands directly. LLMs are treated as untrusted parsers whose outputs must be validated and authorized by deterministic code.
2. **Deterministic Authorization**: Guarantee that all actions adhere to declarative, immutable policies before any system state is altered.
3. **Protection of Critical Assets**: Prevent operational disruption to critical nodes (such as `server`) through hardcoded and policy-level immutability.
4. **Command & Parameter Safety**: Eliminate command injection, argument injection, and unauthorized interface targeting.
5. **Fail-Safe Operation**: Ensure that any system failure, timeout, or invalid parameter aborts cleanly without leaving half-configured or conflicting network states.
6. **Non-Repudiation & Traceability**: Maintain an append-only audit trail recording every proposed, approved, denied, executed, and failed action.

---

## 2. Security Boundaries & Threat Model

### The Security Boundary
INA enforces five sequential defense-in-depth layers between the user and the Linux kernel:

```text
[Operator / Untrusted Input]
             |
             v
+-------------------------------------------------------------+
| Layer 1: Input Sanitization & Classification                |
| * Regex client verification (alphanumeric only)             |
| * Denial of leading dashes/flags (e.g. --user=0, -v)        |
| * Disallowed control targets (network-controller, host)     |
| * Strict rate syntax and unit normalization                 |
+-------------------------------------------------------------+
             |
             v
+-------------------------------------------------------------+
| Layer 2: Declarative Policy Gate (policy/policy_engine.py)  |
| * Verifies rules.yaml action whitelist                      |
| * Verifies known_clients whitelist                          |
| * Protects protected_clients (server is immutable)          |
| * Enforces bandwidth boundary thresholds (1 - 20 Mbps)      |
+-------------------------------------------------------------+
             |
             +----------------------------+
             | [Allowed: False]           | [Allowed: True]
             v                            v
   +-------------------+        +-----------------------------------+
   | Halt Pipeline &   |        | Layer 3: Independent MCP Check    |
   | Log "DENIED"      |        | * Re-checks check_policy()        |
   +-------------------+        | * Defense against direct MCP calls|
                                +-----------------------------------+
                                                  |
                                                  v
                                +-----------------------------------+
                                | Layer 4: Safe Subprocess Runner   |
                                | * Explicit argument lists         |
                                | * NO shell=True                   |
                                | * 10-second defensive timeouts    |
                                +-----------------------------------+
                                                  |
                                                  v
                                +-----------------------------------+
                                | Layer 5: Kernel Enforcement       |
                                | * nftables bridge set isolation   |
                                | * tc/ifb atomic rollback on error |
                                +-----------------------------------+
```

### Threat Considerations & Mitigations

| Threat Vector | Potential Impact | Implemented Mitigation |
| :--- | :--- | :--- |
| **Command / Shell Injection** | Arbitrary host command execution via metacharacters (`;`, `&&`, `\|`, `` ` ``). | Subprocesses run with structured argument lists (`subprocess.run([...])`) without `shell=True`. Client inputs are regex-validated (`^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$`). |
| **CLI Argument Injection** | Subverting Docker CLI flags by passing `-` options (e.g. `--privileged`). | Validator explicitly rejects client names starting with `-` (`cleaned.startswith("-")`). |
| **Unauthorized Action Execution** | Misclassified LLM executing arbitrary tools or actions. | Whitelist enforcement in `policy/policy_engine.py` (`allowed_actions`). Unrecognized actions reject with `action-not-allowed`. |
| **Server / Gateway DoS** | Operator or LLM accidentally blocks or throttles the core server. | `protected_clients` whitelist in `rules.yaml`. The engine rejects blocking or rate-changing operations targeting `server`. |
| **Control-Plane Subversion** | Targeting `network-controller`, `host`, or Docker daemon. | `_DISALLOWED_TARGETS = {"network-controller", "host", "root", "docker", "bridge"}` blocks access to infrastructure nodes. |
| **Extreme Bandwidth Throttling** | Freezing containers by setting 0 Mbps or causing traffic bursts. | Rate parsing requires positive finite numbers and enforces strict boundaries: `1mbit <= rate <= 20mbit`. |
| **Conflicting / Orphaned Rules** | Failed `tc` commands leaving orphan `ifb0` devices or broken filters. | Atomic rollback: `_cleanup_tc_qdiscs()` clears old qdiscs prior to configuration and automatically rolls back partial state if any step fails. |
| **Subprocess Hanging / Resource Exhaustion** | Unresponsive Docker daemon or network call blocking the system. | Strict timeouts (`DEFAULT_TIMEOUT = 10`s) on all subprocess executions. |

---

## 3. Input Validation & Sanitization

Input validation is enforced consistently across `mcp_server/tools.py`, `policy/policy_engine.py`, and `network/discovery.py`:

### 3.1 Client Name Validation (`_validate_client`)
```python
_CLIENT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
_DISALLOWED_TARGETS = {"network-controller", "host", "root", "docker", "bridge"}

def _validate_client(client: str) -> str:
    cleaned = client.strip()
    if not cleaned or not _CLIENT_NAME_RE.match(cleaned) or cleaned.startswith("-"):
        raise ValueError(f"Invalid client identifier: '{client}'.")
    if cleaned.lower() in _DISALLOWED_TARGETS:
        raise ValueError(f"Targeting '{cleaned}' is not permitted.")
    return cleaned
```

### 3.2 IPv4 Address Validation (`_validate_ipv4`)
All IP addresses resolved dynamically via Docker API are verified using Python's built-in `ipaddress` library before being passed to kernel commands:
```python
def _validate_ipv4(ip: str) -> str:
    cleaned = ip.strip()
    ipaddress.IPv4Address(cleaned) # Raises ValueError if invalid
    return cleaned
```

### 3.3 Rate Value Validation (`_validate_rate`)
Rate values must strictly match valid bandwidth patterns:
```python
_RATE_RE = re.compile(r"^\d+(\.\d+)?\s*(kbit|mbit|gbit|kbps|mbps|mb/s|gbps)$", re.IGNORECASE)
```
Trailing parameters or injection attempts (e.g. `"10mbit burst 128kbit"`, `"10mbit; cat /etc/passwd"`) fail regex matching immediately and are rejected.

---

## 4. Policy Engine Security Role

### Pre-Execution Authorization
The Policy Engine evaluates requests **before** any network-altering code runs:
- If a request is rejected by policy, the Assistant immediately halts the pipeline.
- No subprocesses are spawned, no Docker exec commands are sent, and no kernel tables are modified.
- An audit entry is appended to `policy/audit_log.jsonl` with status `DENIED` and the rule violation reason.

### Dual-Layer Defense
To ensure security is not solely dependent on the Assistant, every tool in [`mcp_server/tools.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/mcp_server/tools.py) independently invokes `check_policy()`:
```python
# Enforced inside block_client, unblock_client, limit_bandwidth, and get_status
if check_policy is not None:
    decision = check_policy("block_client", {"client": client})
    if not decision.allowed:
        _log_audit("block_client", {"client": client}, "DENIED", decision.reason)
        return {
            "status": "failure",
            "action": "block_client",
            "client": client,
            "message": f"Policy denied: {decision.reason}",
        }
```
This guarantees that even if an operator or automated script invokes FastMCP tools directly over stdio/HTTP without going through the Assistant, the Policy Engine rules cannot be bypassed.

---

## 5. Subprocess & Command Execution Safety

All system utilities (`docker`, `nft`, `tc`, `ip`, `ping`, `iperf3`) are executed using strict subprocess isolation:

1. **No Shell Interpolation**: All commands use `subprocess.run(["docker", "exec", ...], shell=False)`. Shell metacharacters are treated as literal characters and cannot trigger secondary shell execution.
2. **Defensive Subprocess Timeouts**: Every subprocess execution specifies `timeout=DEFAULT_TIMEOUT` (10 seconds) or `timeout=BANDWIDTH_TEST_DURATION + 10`. If a command hangs due to kernel locks or socket freezes, `subprocess.TimeoutExpired` is caught, avoiding thread deadlocks.
3. **Suppressed Exception Leakage**: Internal stack traces, system paths, and host environment details are caught and translated into concise, sanitized error responses without leaking sensitive system data.

---

## 6. Privilege Model & Isolation

INA follows containerized privilege encapsulation to minimize host attack surface:

```text
+-------------------------------------------------------------------------+
| Host Environment                                                        |
| * Assistant process: Runs as standard unprivileged user                 |
| * Permissions: Docker socket access (/var/run/docker.sock)              |
| * Interactive Sudo: NEVER USED OR PROMPTED                              |
+-------------------------------------------------------------------------+
                                    |
            +-----------------------+-----------------------+
            |                                               |
            v                                               v
+---------------------------------------+  +------------------------------+
| network-controller Container          |  | Client Containers            |
| * network_mode: host                  |  | * network_mode: project-net  |
| * privileged: true                    |  | * cap_add: NET_ADMIN         |
| * Encapsulates host netfilter/nft     |  | * Restricted to container    |
|   bridge table operations             |    internal interfaces (eth0)   |
+---------------------------------------+  +------------------------------+
```

### Why Interactive `sudo` is Banned
Earlier prototypes often required running `sudo iptables` on the host machine. INA removes interactive `sudo` completely:
- Prevents password prompting during automated or unattended agent operations.
- Avoids granting full host root access to Python scripts or LLM processes.
- Encapsulates necessary kernel capabilities inside targeted Docker containers.

---

## 7. Firewall & Bandwidth Control Safety

### 7.1 Idempotency & Conflict Avoidance
- **Firewall Blocking**: Adding an IP to the `nftables` bridge set `@blocked_clients` checks whether the element exists. If `"File exists"` is returned, the operation succeeds gracefully without duplicating rules.
- **Firewall Unblocking**: Deleting an absent element succeeds gracefully without throwing errors.
- **Bandwidth Teardown**: `_cleanup_tc_qdiscs()` explicitly deletes queuing disciplines and removes `ifb0` before applying new rates, preventing conflicting filter chains.

### 7.2 Atomic Rollback
During bandwidth throttling, configuration requires a 6-step sequence across `eth0` and `ifb0`. If any intermediate step fails (e.g. invalid burst or latency parameters), INA triggers immediate rollback:
```python
# Configuration failed: rollback cleanly to prevent conflicting/half-configured tc rules
_cleanup_tc_qdiscs(client)
```
This guarantees that interfaces are never left in a partially-configured or broken networking state.

---

## 8. Audit Logging & Non-Repudiation

All security-relevant actions are recorded in [`policy/audit_log.jsonl`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/policy/audit_log.jsonl) using structured, single-line JSON records:

### Logged States:
* **`ALLOWED`**: High-level policy approved the operation.
* **`DENIED`**: High-level policy rejected the operation (includes rule violation reason).
* **`APPLIED`**: MCP Server successfully executed kernel configuration commands.
* **`FAILED`**: Subprocess error, missing container, or execution failure occurred.

### Accountability Support:
The audit log allows administrators to answer:
1. *What commands were attempted and when?*
2. *Why was a specific request permitted or refused?*
3. *Did any operations fail at the kernel level?*

---

## 9. Current Security Limitations & Residual Risks

While INA implements rigorous defense-in-depth, certain residual risks remain inherent to its environment:

1. **Docker Socket Attack Surface**:
   - The host Python process and `network-controller` mount `/var/run/docker.sock`. Access to the Docker socket is equivalent to root access on the host system.
   - *Residual Risk*: If an attacker achieves arbitrary code execution within the host Python environment, they could manipulate the Docker API to spawn root containers.
2. **`network-controller` Privilege Scope**:
   - `network-controller` runs with `privileged: true` and `network_mode: host`.
   - *Residual Risk*: If an attacker gains an interactive shell inside `network-controller`, they possess elevated privileges on host network interfaces and kernel modules.
3. **Shared Bridge Layer-2 Weakness**:
   - All client containers share the `network_project-net` software bridge.
   - *Residual Risk*: The bridge does not implement private VLANs (PVLANs) or ARP spoofing protection. While `nftables` drops routed frames matching `@blocked_clients`, malicious code inside `client1` could theoretically attempt local ARP poisoning against `client2`.
4. **Lack of User Authentication / Multi-Tenancy**:
   - The interactive Assistant operates in a single-tenant model without user authentication, API tokens, or role-based access control (RBAC).

---

## 10. Practical Hardening Recommendations

For production or hardened testing deployments:

1. **Protect the Docker Socket**:
   - Restrict access to `/var/run/docker.sock` to a dedicated service account.
   - Consider using a Docker socket proxy (e.g., `tecnativa/docker-socket-proxy`) to expose only container inspection and exec endpoints, blocking container creation or host mounts.
2. **Run Rootless Docker**:
   - Deploy the Docker daemon in rootless mode to eliminate root-level host exposure in the event of a container breakout.
3. **Environment Variable Security**:
   - Ensure `.env` is never committed to source control (maintained in `.gitignore`).
   - Store API keys with restricted permissions (`chmod 600 .env`).
4. **Audit Log Protection**:
   - In production environments, stream `policy/audit_log.jsonl` to an immutable, append-only centralized logging server (e.g. Syslog or SIEM) to prevent local tampering.
