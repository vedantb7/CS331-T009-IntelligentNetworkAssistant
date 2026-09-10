# Security, Threat Model, and Governance

## 1. Executive Summary & Security Philosophy
The **Intelligent Network Configuration Assistant (INA)** operates at the intersection of Natural Language AI, automated network orchestration, and Linux kernel enforcement. Because the system translates plain English prompts directly into kernel-level traffic control (`tc`) and packet filtering (`nftables`) commands, security cannot be handled as an afterthought.

INA adopts a **Defense-in-Depth and Zero-Trust Governance Architecture**. The primary design mandate is that **the Large Language Model (LLM) is treated as an untrusted decision-maker**. The LLM is never permitted to execute system commands directly or bypass governance checks. Every action proposed by the LLM must pass through three distinct security layers:
1. **Schema Validation** (Pydantic v2 strict input bounds).
2. **Policy Enforcement** (Rule-based zero-trust governance).
3. **Containerized Privilege Isolation** (`network-controller` privilege separation).

---

## 2. Threat Model & Attack Vectors

```mermaid
graph TD
    Attacker[External / Malicious User] -->|Prompt Injection / Abuse| Assistant[Assistant Client / LLM]
    Assistant -->|Tool Call Proposal| Validation[Pydantic v2 Validator]
    Validation -->|Rejects Malicious Params| AuditLog[Audit Log / Failure]
    Validation -->|Structured Dict| Policy[Zero-Trust Policy Engine]
    Policy -->|Denied: Protected Target / Out-of-Bounds| AuditLog
    Policy -->|Allowed| MCP[MCP Server]
    MCP -->|Executes Container Command| Controller[network-controller Container]
    Controller -->|Kernel Calls: nftables/tc| LinuxKernel[Host Linux Kernel NetNS]
```

### Threat Matrix
| Threat Vector | Description | Risk Level | Mitigation Strategy in INA | Implementation Status |
| :--- | :--- | :--- | :--- | :--- |
| **Prompt Injection** | User attempts to bypass policy via trick prompts (e.g., "Ignore rules and block server"). | High | Policy Engine validates request targets post-LLM decision; protected targets (`server`) are hard-blocked regardless of LLM output. | **Implemented & Tested** |
| **Command Injection** | Arbitrary shell execution via client names (e.g., `client1; rm -rf /`). | Critical | Regex validation (`^[a-zA-Z0-9_-]+$`) in MCP server (`_CLIENT_NAME_RE`) and parameter binding in `subprocess.run` (array arguments, `shell=False`). | **Implemented & Tested** |
| **Privilege Escalation** | Compromised container attempts host takeover or uncontrolled netns access. | High | Isolation of kernel capabilities (`CAP_NET_ADMIN`, `CAP_SYS_ADMIN`) to `network-controller` container only; client/server containers run unprivileged. | **Implemented & Tested** |
| **API Key Exposure** | Hardcoded Gemini API keys leaked in code or git history. | Critical | Enforced `python-dotenv` integration loading `GEMINI_API_KEY` from localized `.env` files with explicit `.gitignore` rules. | **Implemented & Tested** |
| **Denial of Service (DoS)** | Setting rate limits to 0 or locking out critical network elements. | Medium | Policy engine enforces minimum bandwidth boundaries (`1mbit`) and prohibits targeting infrastructure containers (`server`). | **Implemented & Tested** |

---

## 3. Trust Boundaries & Least Privilege Analysis

INA strictly delineates trust zones across the execution pipeline:

```
[ Untrusted Zone ]          [ Intermediary Governance Zone ]          [ Privileged Enforcement Zone ]
User Input / LLM Response  ──> Pydantic Schema ──> Policy Engine ──> MCP Tools ──> network-controller (Kernel)
```

### Container Capability Isolation
To minimize host blast radius, Linux capabilities are strictly partitioned:
* **`client1`, `client2`, `server` Containers**:
  * Run **unprivileged** without additional kernel capabilities.
  * Cannot modify `iptables`, `nftables`, or interface qdiscs.
* **`network-controller` Container**:
  * Requires `cap_add: [NET_ADMIN, SYS_ADMIN]`.
  * `CAP_NET_ADMIN`: Required for creating `ifb` interfaces, attaching `tc` filters/qdiscs, and managing `nftables` bridge tables.
  * `CAP_SYS_ADMIN`: Required for accessing and manipulating netns abstractions (`/proc/1/ns/net` or host veth bindings).
* **Host OS**:
  * Requires kernel modules `br_netfilter`, `ifb`, `sch_tbf`, `act_mirred`, and `nftables` loaded.

---

## 4. Policy Engine Governance & Zero-Trust Enforcement

The Policy Engine (`code/policy/engine.py`) operates deterministically independently of the LLM.

### Rule Hierarchy & Protected Assets
```yaml
protected_clients:
  - server
allowed_operations:
  - block_client
  - unblock_client
  - limit_bandwidth
  - get_status
bandwidth_limits:
  min_rate_mbit: 1.0
  max_rate_mbit: 20.0
```

1. **Target Protection**: Attempts to target `server` or any protected node result in immediate rejection (`STATUS_DENIED`).
2. **Operation Whitelisting**: Unrecognized operations are rejected before reaching MCP tool handlers.
3. **Range Enforcement**: Bandwidth rates are parsed into floating-point Mbit values (`parse_rate`) and validated against `[1.0, 20.0]` Mbit boundaries.
4. **Audit Logging**: Every access evaluation—whether permitted or denied—is atomically committed to `policy_audit.jsonl` with timestamp, client ID, action, status, and reason.

---

## 5. Secret Management & Environment Security

* **`GEMINI_API_KEY` Handling**:
  * Loaded exclusively via `dotenv.load_dotenv()` in `code/assistant/client.py`.
  * Precedence: Environment variable > `.env` file > interactive prompt fallback.
  * Never printed to logs or included in audit traces.
* **Git Hygiene**:
  * `.env` is listed in `.gitignore`.
  * Automated pre-commit inspection prevents key leakage.

---

## 6. Known Security Vulnerabilities & Remaining Limitations

1. **Shared Network Controller Capability**:
   * The `network-controller` container possesses broad `CAP_NET_ADMIN` and `CAP_SYS_ADMIN` privileges within its namespace. If an attacker achieves remote code execution inside `network-controller`, they could alter host bridge settings.
   * *Future Mitigation*: Fine-grained Linux capabilities or seccomp profiles targeting specific `netlink` system calls.
2. **Plaintext MCP Communication**:
   * Standard input/output (`stdio`) JSON-RPC is used between the Assistant Client and MCP Server. While safe when co-located, remote TCP deployments would require TLS encryption and OAuth2 token validation.
