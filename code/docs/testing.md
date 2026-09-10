# Testing Strategy & Test Suite Guide

---

## 1. Overview & Testing Strategy

The **Intelligent Network Configuration Assistant (INA)** implements a two-tier testing methodology to guarantee correctness, safety, and operational reliability:

1. **Deterministic Unit & Pipeline Tests (Offline / Mock-Driven)**: Fast, repeatable tests using `pytest` and `unittest.mock` that verify policy decisions, input sanitization, error handling, command parameter construction, and end-to-end pipeline transitions without mutating the host kernel or requiring root privileges.
2. **Live Network Integration Tests (Containerized / Environment-Aware)**: Tests that inspect the running Docker bridge network (`network_project-net`), verify container IP allocations, and confirm physical inter-container reachability.

```text
+-------------------------------------------------------------------------+
|                        INA Test Architecture                            |
+-------------------------------------------------------------------------+
                                    |
          +-------------------------+-------------------------+
          |                                                   |
          v                                                   v
+-----------------------------------+   +---------------------------------+
| Fast Deterministic Test Tier      |   | Live Integration Test Tier      |
| (pytest + unittest.mock)          |   | (Live Docker & Bridge Net)      |
|                                   |   |                                 |
| * tests/test_ina_focused.py (35)  |   | * tests/test_network.py (20)    |
| * tests/test_policy.py (23)       |   | * tests/test_discovery.py (5)   |
| * tests/test_validation.py (32)   |   |                                 |
| * tests/test_mcp.py (13)          |   |                                 |
+-----------------------------------+   +---------------------------------+
          |                                                   |
          +-------------------------+-------------------------+
                                    |
                                    v
          +---------------------------------------------------+
          |         Total Suite: 128 Passing Tests            |
          |       Duration: ~11.5 seconds across suite        |
          +---------------------------------------------------+
```

---

## 2. Test Suite Structure & Modules

The test suite contains **6 test modules** comprising **128 automated tests**:

| Test File | Test Count | Primary Purpose & Scope |
| :--- | :---: | :--- |
| [`tests/test_ina_focused.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/tests/test_ina_focused.py) | 35 | **Focused 10-Area Test Suite**: Covers Policy, MCP commands, injection prevention, error handling, validation checks, audit logging, and full asynchronous end-to-end pipeline transitions. |
| [`tests/test_validation.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/tests/test_validation.py) | 32 | **Validation Models & Monitor**: Tests Pydantic schemas (`ValidationRequest`, `ValidationResult`), rate unit parsing, packet loss calculation, and monitor algorithms. |
| [`tests/test_policy.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/tests/test_policy.py) | 23 | **Policy Engine & Audit Log**: Tests authorization whitelists, protected client immutability, bandwidth boundary rules, status policies, and audit log generation. |
| [`tests/test_network.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/tests/test_network.py) | 20 | **Live Network & Container Integration**: Tests Docker daemon presence, container running states, static IP allocations on `network_project-net`, and ping reachability. |
| [`tests/test_mcp.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/tests/test_mcp.py) | 13 | **MCP Tool Interface**: Tests FastMCP tool wrappers in `mcp_server/server.py` (`block`, `unblock`, `limit`, `status`) and error propagation. |
| [`tests/test_discovery.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/tests/test_discovery.py) | 5 | **Dynamic Container Discovery**: Tests Docker Engine API lookup via `network/discovery.py`, verifying dynamic IP resolution, metadata inspection, and non-existent container handling. |

---

## 3. Unit Testing Breakdown

### 3.1 Policy Engine Unit Tests
* **Whitelisted Actions**: Verifies that `block_client`, `unblock_client`, `limit_bandwidth`, and `get_status` are accepted for authorized clients.
* **Unauthorized Actions**: Verifies that arbitrary actions (e.g. `delete_client`, `reboot_host`) are rejected with rule ID `action-not-allowed`.
* **Protected Clients**: Ensures `server` cannot be blocked or rate-limited under any condition.
* **Bandwidth Boundaries**: Validates that rates below 1 Mbps (e.g. `0.5mbit`) or above 20 Mbps (e.g. `25mbit`) are rejected with `bandwidth-bounds`.

### 3.2 Input Sanitization & Anti-Injection Tests
* **Shell Injection Defense**: Verifies rejection of payloads containing shell metacharacters:
  - `client1; reboot`
  - `client1 && cat /etc/passwd`
  - `client1 | id`
  - ``client1 `whoami` ``
* **CLI Flag Injection Defense**: Verifies rejection of client names starting with `-` (e.g. `--user=0`, `-v`, `-h`).
* **Control-Plane Target Isolation**: Verifies rejection of attempts to target `network-controller`, `host`, `docker`, or `bridge`.
* **Rate Syntax Verification**: Confirms malformed rate values (`"invalid_rate"`, `"10mbit burst 128kbit"`, negative numbers) fail validation without executing commands.
* **IPv4 Address Validation**: Ensures malformed, multicast, or loopback IPs are caught by `_validate_ipv4`.

### 3.3 MCP Tools & Command Parameter Construction
Tests use `unittest.mock.patch` to capture the exact argument vectors sent to `subprocess.run`:
* **`block_client`**: Asserts exact parameter structure:
  ```python
  ["docker", "exec", "network-controller", "nft", "add", "element", "bridge", "network_filter", "blocked_clients", "{", "172.20.0.2", "}"]
  ```
* **`unblock_client`**: Asserts deletion from the bridge set and verifies that `_cleanup_tc_qdiscs()` is called to tear down old traffic shaping.
* **`limit_bandwidth`**: Verifies the complete 6-step `tc`/`ifb` sequence:
  1. Teardown existing qdiscs.
  2. Create `ifb0` interface (`ip link add name ifb0 type ifb`).
  3. Enable `ifb0` (`ip link set dev ifb0 up`).
  4. Redirect `eth0` ingress to `ifb0` (`action mirred egress redirect dev ifb0`).
  5. Apply TBF qdisc on `ifb0` (ingress).
  6. Apply TBF qdisc on `eth0` (egress).
* **Idempotency**: Asserts that if an IP is already blocked (`File exists`), the tool returns `"status": "success"` without duplicating rules.

### 3.4 Error Handling & Fault Injection
Tests simulate critical system failure modes to confirm safe degradation:
* **Subprocess Timeouts**: Simulates `subprocess.TimeoutExpired` (10s) and confirms tools catch it cleanly without hanging the process.
* **Missing Docker Executable**: Simulates `FileNotFoundError` when `docker` is absent from PATH and confirms clean failure reporting.
* **Non-Existent Container**: Confirms `ValueError` is raised and caught when querying missing containers.
* **Automatic Rollback**: Simulates failure during intermediate `tc` setup and confirms `_cleanup_tc_qdiscs()` executes automatically to revert state.

### 3.5 Validation Monitor Unit Tests
* **`check_block`**: Simulates ping command outputs to verify that `100% packet loss` evaluates to `passed=True`, while `0% loss` evaluates to `passed=False`.
* **`check_unblock`**: Confirms that responsive pings evaluate to `passed=True`.
* **`check_bandwidth`**: Injects simulated JSON outputs from `iperf3` and confirms that throughput within ±20% evaluates to `passed=True`, while throughput outside the margin fails.
* **Timeouts**: Confirms that hung `ping` or `iperf3` commands return clean validation errors rather than hanging worker threads.

---

## 4. Integration & End-to-End Testing

### 4.1 Full Pipeline Asynchronous Tests (`TestEndToEndPipeline`)
Implemented in [`tests/test_ina_focused.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/tests/test_ina_focused.py) using `@pytest.mark.anyio`:
1. **Happy Path**: Natural command (`"block client1"`) → Intent Parsing → Policy Evaluation (`ALLOWED`) → MCP Execution (`APPLIED`) → Validation Monitor (`CONFIRMED`) → Response generated.
2. **Policy Denial Short-Circuit**: Command targeting `server` (`"block server"`) → Policy halts pipeline at stage `"policy"` → MCP tool is never called (`assert_not_called()`) → Validation skipped.
3. **Execution Failure Short-Circuit**: Command where kernel configuration fails → Pipeline halts at stage `"execution"` → Validation is skipped.
4. **Conversational Short-Circuit**: Greetings (`"hello"`) → Parsed as `CONVERSATION` → Immediately returns friendly greeting without contacting Policy Engine, MCP Server, or Validation.
5. **Ambiguous Command Clarification**: Unknown input (`"gibberish"`) → Parsed as `UNKNOWN` → Returns a clarification response explaining available features.

### 4.2 Live Network Integration Tests (`test_network.py`)
Executed directly against the active Docker environment:
* Confirms `server`, `client1`, `client2`, and `network-controller` are in running state.
* Verifies static IP allocations (`client1: 172.20.0.2`, `server: 172.20.0.3`, `client2: 172.20.0.4`).
* Probes active ICMP reachability across the `network_project-net` virtual bridge.

---

## 5. Mocking & Isolation Strategy

To ensure tests can run in Continuous Integration (CI) environments without requiring interactive `sudo` or Docker daemons:

| Component Under Test | Isolation / Mocking Technique | Reason for Mocking |
| :--- | :--- | :--- |
| **Kernel Commands (`nft`, `tc`)** | `unittest.mock.patch("subprocess.run")` | Prevents mutating host network interfaces and eliminates need for root privileges during unit tests. |
| **Docker Daemon API** | `unittest.mock.patch("docker.from_env")` | Allows verifying error handling (missing containers, API errors) deterministically. |
| **Audit Log File Writes** | `pytest` `tmp_path` fixture + monkeypatching `LOG_PATH` | Prevents tests from polluting the production `policy/audit_log.jsonl` file. |
| **Asynchronous Client Pipeline** | `pytest.mark.anyio` | Tests asynchronous async/await pipeline methods in `NetworkAssistant`. |

---

## 6. Running the Automated Test Suite

### 6.1 Running All Tests
From the project root inside the virtual environment:
```bash
# Run the complete test suite (128 tests):
.venv/bin/pytest -v
```

### 6.2 Running Specific Test Suites
```bash
# Run the focused 10-area test suite (35 tests):
.venv/bin/pytest tests/test_ina_focused.py -v

# Run only policy tests (23 tests):
.venv/bin/pytest tests/test_policy.py -v

# Run only MCP server tests (13 tests):
.venv/bin/pytest tests/test_mcp.py -v

# Run only validation tests (32 tests):
.venv/bin/pytest tests/test_validation.py -v

# Run only live network tests (20 tests; requires Docker containers running):
.venv/bin/pytest tests/test_network.py -v

# Run only container discovery tests (5 tests):
.venv/bin/pytest tests/test_discovery.py -v
```

---

## 7. Interpreting Test Results & Diagnosing Failures

### What a Passing Test Means
- **Unit Tests Pass**: Policy rules, input filters, command parameter lists, and error handlers behave deterministically according to project specifications.
- **Integration Tests Pass**: Docker containers are active, interfaces are configured on `172.20.0.0/24`, and cross-bridge reachability is intact.

### Investigating Test Failures
1. **`test_network.py` Failures**:
   - Symptom: `server container does not exist` or `container is not running`.
   - Diagnosis: The Docker Compose environment is down.
   - Fix: Run `docker compose -f network/docker-compose.yml up -d --build`.
2. **`test_discovery.py` Failures**:
   - Symptom: `DockerException: Docker daemon is not running`.
   - Diagnosis: Docker service is stopped or user lacks socket permissions.
   - Fix: Start Docker service or add user to `docker` group.
3. **`test_ina_focused.py` Failures**:
   - Symptom: Assertion error on command parameters or return values.
   - Diagnosis: Internal helper function or policy check contract changed.
   - Fix: Check recent changes in `mcp_server/tools.py` or `policy/policy_engine.py`.

---

## 8. Automated Tests vs. Live Network Validation

It is important to distinguish automated tests from live runtime validation:

- **Automated Tests (`pytest`)**: Run by developers or CI pipelines to verify that the code logic and mocked command generation are correct.
- **Live Network Validation (`validation/monitor.py`)**: Run dynamically at runtime inside the Assistant after a user requests a network change. It tests real network behavior on the active Docker bridge to ensure the command took effect.

---

## 9. Current Testing Limitations & Residual Gaps

1. **Hardware / Kernel Driver Coverage**: Unit tests mock `subprocess.run` calls for `tc` and `nftables`. While this ensures determinism and safety, it does not detect host-specific kernel incompatibilities (e.g. missing kernel modules like `ifb` or `br_netfilter` on non-standard Linux kernels).
2. **Multi-Client Concurrency Under Load**: Tests verify sequential operation. They do not simulate concurrent parallel traffic streams from multiple clients competing for the single `iperf3` server daemon on `server:5201`.
3. **Live External LLM Roundtrips**: End-to-end unit tests mock the intent parsing stage to ensure offline reproducibility. Live network calls to external OpenRouter API endpoints are tested manually during runtime.
