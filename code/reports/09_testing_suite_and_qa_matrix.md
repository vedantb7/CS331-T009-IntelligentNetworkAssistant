# Testing Suite and QA Matrix

## 1. Executive Summary & Test Suite Architecture
The **Intelligent Network Configuration Assistant (INA)** maintains a comprehensive, automated PyTest suite to verify unit logic, schema enforcement, policy governance, MCP tool integration, and network topology health.

As of the latest run, **128 unit and integration tests** execute in under 12 seconds with a **100% pass rate**.

```bash
============================= 128 passed in 11.62s =============================
```

### Test Hierarchy
```
code/tests/
├── test_discovery.py     # Docker SDK runtime container discovery unit tests
├── test_policy.py        # Zero-trust Policy Engine evaluation & audit log tests
├── test_validation.py    # Pydantic v2 validation, rate parsing & metric tolerance tests
├── test_mcp.py           # FastMCP tool wrapper handlers & error propagation tests
├── test_network.py       # Live Docker container topology & connectivity tests
└── test_ina_focused.py   # Comprehensive end-to-end integration & pipeline tests
```

---

## 2. Test Execution Matrix by Subsystem

### 2.1 Dynamic Container Discovery (`test_discovery.py`)
| Test Case Name | Purpose | Input Parameter | Expected Result | Status |
| :--- | :--- | :--- | :--- | :--- |
| `test_validate_container_name_valid` | Verify regex sanitization for valid identifiers | `"client1"` | Returns `"client1"` | **PASSED** |
| `test_validate_container_name_invalid_injection` | Reject command injection symbols | `"client1; rm -rf /"` | Raises `ValueError` | **PASSED** |
| `test_validate_container_name_leading_dash` | Reject flag injection attempts | `"-client"` | Raises `ValueError` | **PASSED** |
| `test_get_container_ip_nonexistent` | Ensure missing container handling | `"nonexistent_node"` | Raises `ValueError` | **PASSED** |

### 2.2 Policy Engine & Audit Governance (`test_policy.py`)
| Test Case Name | Purpose | Input Parameter | Expected Result | Status |
| :--- | :--- | :--- | :--- | :--- |
| `test_valid_block_operation_is_allowed` | Permit policy-compliant block request | `client1`, `block_client` | `STATUS_ALLOWED` | **PASSED** |
| `test_protected_client_cannot_be_blocked` | Enforce server protection rule | `server`, `block_client` | `STATUS_DENIED` | **PASSED** |
| `test_bandwidth_below_minimum_is_denied` | Enforce min rate boundary (1 Mbit) | `client1`, `0.5mbit` | `STATUS_DENIED` | **PASSED** |
| `test_bandwidth_above_maximum_is_denied` | Enforce max rate boundary (20 Mbit) | `client1`, `50mbit` | `STATUS_DENIED` | **PASSED** |
| `test_log_action_creates_log_entry` | Verify JSONL audit file generation | Audit event dict | JSONL line appended | **PASSED** |

### 2.3 Validation Engine (`test_validation.py`)
| Test Case Name | Purpose | Input Parameter | Expected Result | Status |
| :--- | :--- | :--- | :--- | :--- |
| `test_parse_rate_mbit` | Rate string normalization | `"5.5Mbit"` | `5.5` Mbit float | **PASSED** |
| `test_parse_rate_kbit` | Kbps rate conversion | `"500Kbit"` | `0.5` Mbit float | **PASSED** |
| `test_bandwidth_within_tolerance` | Active iperf validation matching target | Target: `5.0`, Actual: `4.8` | Returns `True` (within $\pm 20\%$) | **PASSED** |
| `test_bandwidth_outside_tolerance` | Active iperf validation out of bounds | Target: `5.0`, Actual: `1.0` | Returns `False` | **PASSED** |
| `test_pick_source_container_for_client1` | Host Ping Bypass resolution | Target: `client1` | Returns `client2` | **PASSED** |
| `test_pick_source_container_for_client2` | Host Ping Bypass resolution | Target: `client2` | Returns `client1` | **PASSED** |

### 2.4 MCP Tools Integration (`test_mcp.py`)
| Test Case Name | Purpose | Input Parameter | Expected Result | Status |
| :--- | :--- | :--- | :--- | :--- |
| `test_block_calls_block_client` | Verify FastMCP block tool invocation | `client_name="client1"` | Invokes `block_client` network handler | **PASSED** |
| `test_unblock_calls_unblock_client` | Verify FastMCP unblock tool invocation | `client_name="client1"` | Invokes `unblock_client` network handler | **PASSED** |
| `test_limit_calls_limit_bandwidth` | Verify FastMCP bandwidth tool invocation | `client1`, `5mbit` | Invokes `limit_bandwidth` network handler | **PASSED** |
| `test_block_propagates_network_failure` | Ensure clean error reporting on failure | Network exception | MCP tool returns failure payload | **PASSED** |

### 2.5 Live Network Topology (`test_network.py`)
| Test Case Name | Purpose | Input Parameter | Expected Result | Status |
| :--- | :--- | :--- | :--- | :--- |
| `test_container_is_running[server]` | Verify server lifecycle state | Docker status check | `running` | **PASSED** |
| `test_container_is_running[client1]` | Verify client1 lifecycle state | Docker status check | `running` | **PASSED** |
| `test_client1_can_ping_server` | Baseline bridge connectivity | ICMP echo | 0% packet loss | **PASSED** |
| `test_server_http_service_is_reachable` | Application layer HTTP ping | `http://172.20.0.3:5000` | HTTP 200 OK | **PASSED** |
| `test_iperf3_server_is_reachable` | Active benchmark server status | Port 5201 check | Connection established | **PASSED** |

---

## 3. Automated Test Execution Guide

To execute the test suite against the workspace environment:

```bash
# Activate virtual environment
source /home/dhruv/Documents/ina/.venv/bin/activate

# Execute all unit and integration tests
pytest -v code/tests/

# Execute only policy engine tests
pytest -v code/tests/test_policy.py

# Execute with coverage report
pytest --cov=code code/tests/
```

---

## 4. Test Limitations & Recommendations

1. **Docker Daemon Dependency**:
   * Topology tests (`test_network.py`) require active running Docker containers (`docker compose up -d`). When running in headless CI without Docker, network tests skip or fail.
2. **Mocking vs Live Execution**:
   * Unit tests for MCP (`test_mcp.py`) mock kernel `subprocess.run` calls to execute quickly. Live verification relies on integration tests (`test_ina_focused.py` and active validation monitor runs).
