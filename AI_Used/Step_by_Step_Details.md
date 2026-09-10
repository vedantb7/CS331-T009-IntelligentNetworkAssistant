# Step-by-Step AI Contributions by Development Stage

---

## 1. Overview

This document provides a detailed, stage-by-stage breakdown of where and how AI tools contributed to the engineering of the **Intelligent Network Configuration Assistant (INA)**. 

Each section outlines:
- **Team Intent & Objectives**: What the human engineers planned to achieve.
- **AI Contribution & Assistance**: The reasoning, implementation, or documentation support provided by AI.
- **Human Review & Verification**: How the team inspected, tested, and validated the output.
- **Corrections & Iterative Fixes**: Specific adjustments made when AI suggestions contained gaps, bugs, or required refinement.

---

## 2. Development Stages & Component Contributions

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ Stage 1: Network Infrastructure & Docker Compose Topology               │
├─────────────────────────────────────────────────────────────────────────┤
│ Stage 2: Declarative Policy Engine & Audit Governance                   │
├─────────────────────────────────────────────────────────────────────────┤
│ Stage 3: FastMCP Server & Linux Kernel Enforcement (nftables & tc/IFB)   │
├─────────────────────────────────────────────────────────────────────────┤
│ Stage 4: Dynamic Container Discovery (Docker Engine SDK)                │
├─────────────────────────────────────────────────────────────────────────┤
│ Stage 5: Active Network Validation & Performance Monitoring             │
├─────────────────────────────────────────────────────────────────────────┤
│ Stage 6: Interactive Assistant CLI & LLM Intent Orchestration           │
├─────────────────────────────────────────────────────────────────────────┤
│ Stage 7: Comprehensive Security Hardening & Defense-in-Depth            │
├─────────────────────────────────────────────────────────────────────────┤
│ Stage 8: Automated Test Suite Construction (128 Tests)                  │
├─────────────────────────────────────────────────────────────────────────┤
│ Stage 9: Technical Documentation, Academic Reports & System Architecture │
└─────────────────────────────────────────────────────────────────────────┘
```

---

### Stage 1: Containerized Network Infrastructure & Lab Topology
* **Relevant Files**: [`network/docker-compose.yml`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/network/docker-compose.yml), [`network/Dockerfile`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/network/Dockerfile), [`network/setup.sh`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/network/setup.sh)
* **Team Intent**:
  Create an isolated Docker bridge network (`project-net`) hosting client and server nodes, with a privileged network controller to execute netfilter and traffic control rules without altering the host's physical network adapters.
* **AI Assistance**:
  - Suggested baseline Dockerfile package requirements (`iproute2`, `nftables`, `iperf3`, `kmod`).
  - Assisted in structuring the Compose file with static IP assignments on `172.20.0.0/24` and granular Linux capabilities (`cap_add: NET_ADMIN`).
  - Drafted the initial bash orchestration script (`setup.sh`) to automate Docker availability checks and container teardown.
* **Human Review & Verification**:
  - The team verified that unprivileged client nodes (`client1`, `client2`, `server`) only had `NET_ADMIN` in their private namespaces, preventing them from modifying host interfaces.
  - Tested container startup manually via `docker compose up -d` and confirmed reachability.
* **Corrections & Refinements**:
  - AI initially omitted the persistent `iperf3 -s` listener command on the server; the team updated the server compose command to `["sh", "-c", "iperf3 -s -D && exec python3 -m http.server 5000"]` to support continuous bandwidth testing.

---

### Stage 2: Declarative Policy Engine & Rule Governance
* **Relevant Files**: [`policy/policy_engine.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/policy/policy_engine.py), [`policy/rules.yaml`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/policy/rules.yaml), [`policy/audit_log.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/policy/audit_log.py)
* **Team Intent**:
  Build a deterministic gatekeeper that evaluates user requests against plain-YAML rules before any system command runs, recording all outcomes in an append-only audit trail.
* **AI Assistance**:
  - Suggested the `PolicyResult` dataclass pattern (`allowed`, `reason`, `rule_id`).
  - Assisted in writing the unit parser `_parse_mbit()` to normalize rates (`kbit`, `mbit`, `gbit`, `mbps`) to numeric float values.
  - Drafted the append-only JSONL logging mechanism and the `explain_action()` natural-language formatter.
* **Human Review & Verification**:
  - The team wrote unit tests in `tests/test_policy.py` asserting that protected clients (`server`) cannot be blocked or throttled, and that unauthorized verbs are rejected.
* **Corrections & Refinements**:
  - AI initially accepted any string client name; the team added regex sanitization (`_is_valid_client_name`) and blocked leading dashes (`-`) to eliminate argument injection.

---

### Stage 3: FastMCP Server & Linux Kernel Enforcement
* **Relevant Files**: [`mcp_server/server.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/mcp_server/server.py), [`mcp_server/tools.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/mcp_server/tools.py)
* **Team Intent**:
  Expose standardized Model Context Protocol tools (`block`, `unblock`, `limit`, `status`) that translate abstract requests into concrete `nftables` and `tc` commands inside target containers.
* **AI Assistance**:
  - Outlined the FastMCP decorator syntax (`@mcp.tool()`).
  - Researched the 6-step `tc` queuing discipline sequence required for bi-directional shaping using `ifb0` and `mirred` ingress redirection.
  - Provided command parameter lists for `nft add element bridge network_filter blocked_clients ...`.
* **Human Review & Verification**:
  - The team verified that all subprocess calls used explicit array arguments (`subprocess.run([...])`) without `shell=True`.
  - Tested blocking and throttling manually from the command line using `fastmcp call` and verified with `nft list ruleset` and `tc qdisc show`.
* **Corrections & Refinements**:
  - Early implementations failed when intermediate `tc` commands errored, leaving half-configured queuing disciplines; the team added the `_cleanup_tc_qdiscs()` automatic rollback mechanism.
  - AI initially omitted an independent policy check in `tools.py`; the team added a secondary `check_policy()` invocation inside every tool for defense-in-depth.

---

### Stage 4: Dynamic Container Discovery
* **Relevant Files**: [`network/discovery.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/network/discovery.py), [`tests/test_discovery.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/tests/test_discovery.py)
* **Team Intent**:
  Eliminate hardcoded static IP dictionaries from the application code by dynamically querying the Docker daemon API at runtime.
* **AI Assistance**:
  - Suggested using the official Docker SDK for Python (`docker.from_env()`).
  - Drafted `get_container_ip()` and `list_known_clients()` functions with subnet filtering.
* **Human Review & Verification**:
  - Tested dynamic discovery against running and stopped containers; verified that missing containers raise a clean `ValueError` rather than an unhandled API crash.
* **Corrections & Refinements**:
  - Ensured container names are regex-sanitized before passing them to the Docker SDK, preventing arbitrary container lookup attacks.

---

### Stage 5: Active Network Validation & Performance Monitoring
* **Relevant Files**: [`validation/monitor.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/validation/monitor.py), [`validation/models.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/validation/models.py)
* **Team Intent**:
  Empirically verify that applied network changes took physical effect on the bridge using live synthetic traffic (ICMP pings and `iperf3` throughput measurements).
* **AI Assistance**:
  - Implemented Pydantic v2 validation models (`ValidationRequest`, `ValidationResult`).
  - Aided in writing JSON parsers for `iperf3 -J` output to extract average `bits_per_second`.
  - Assisted in designing the ±20% tolerance check algorithm (`bandwidth_within_tolerance`).
* **Human Review & Verification**:
  - Discovered that pings from the host OS bypassed bridge forward rules (reporting 0% loss even when blocked). AI assisted in analyzing the Layer-2 packet path, leading the team to implement `pick_source_container()` to force pings to originate from a sibling container across the bridge.
* **Corrections & Refinements**:
  - Added `ensure_iperf_server()` to automatically detect and restart the server daemon if it was stopped, preventing false validation failures.

---

### Stage 6: Interactive Assistant CLI & Intent Orchestration
* **Relevant Files**: [`assistant/client.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/assistant/client.py)
* **Team Intent**:
  Build an interactive terminal assistant that parses natural-language operator intent using OpenRouter LLMs, orchestrates the execution pipeline, and renders Rich diagnostic reports.
* **AI Assistance**:
  - Structured the system prompt (`LLM_SYSTEM_PROMPT`) with few-shot classification examples.
  - Drafted the fallback regex parser to allow full offline operation without cloud API dependencies.
  - Implemented the Rich console table formatters for status reports and help commands.
* **Human Review & Verification**:
  - Verified that conversational greetings (`hello`, `thanks`, `bye`) short-circuit the pipeline immediately without triggering policy checks or network modifications.
  - Tested that unknown or ambiguous commands return a polite clarification prompt rather than executing unverified actions.
* **Corrections & Refinements**:
  - Refined the response parser to strictly normalize JSON output from various LLM providers, ignoring conversational preambles or markdown fences.

---

### Stage 7: Comprehensive Security Hardening
* **Relevant Files**: Across `mcp_server/tools.py`, `policy/policy_engine.py`, and `network/discovery.py`
* **Team Intent**:
  Audit the entire codebase to eliminate command injection, privilege escalation, and unauthorized infrastructure access.
* **AI Assistance**:
  - Analyzed the threat vectors associated with Docker socket mounting and container capabilities.
  - Drafted the `_DISALLOWED_TARGETS` list (`network-controller`, `host`, `docker`, `root`).
  - Added 10-second defensive timeouts to all subprocess executions.
* **Human Review & Verification**:
  - Attempted common injection payloads (`client1; reboot`, `client1 && id`, `--user=0`) in tests and confirmed immediate rejection.
* **Corrections & Refinements**:
  - Fixed an edge case where rate strings with trailing options (`"10mbit burst 128kbit"`) could bypass simple substring checks by enforcing strict regex matching (`_RATE_RE`).

---

### Stage 8: Automated Test Suite Construction
* **Relevant Files**: [`tests/test_ina_focused.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/tests/test_ina_focused.py) and all files in `tests/`
* **Team Intent**:
  Build a deterministic, comprehensive test suite covering all 10 architectural areas specified in the project requirements.
* **AI Assistance**:
  - Generated the 35 focused test cases in `tests/test_ina_focused.py` using `unittest.mock` and `pytest.mark.anyio`.
  - Set up mocking fixtures to simulate subprocess outputs for `nft`, `tc`, `ping`, and `iperf3`.
* **Human Review & Verification**:
  - Executed the full test suite (`pytest -v`) and confirmed that all 128 tests across the repository pass cleanly in under 12 seconds.
* **Corrections & Refinements**:
  - Resolved an assertion error in `test_e2e_successful_block_flow` where the validator mock targeted `check_block` instead of the adapter's `validate` method.

---

### Stage 9: Technical Documentation & Final System Reporting
* **Relevant Files**: All files in [`docs/`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/docs/) (`architecture.md`, `INA_SETUP.md`, `mcpserver.md`, `network.md`, `policy.md`, `validation.md`, `security.md`, `testing.md`, `logging.md`), [`README.md`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/README.md), and [`changes.md`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/changes.md)
* **Team Intent**:
  Author complete, accurate, and professional documentation for developers and evaluators reflecting the actual codebase.
* **AI Assistance**:
  - Drafted comprehensive subsystem specifications based on direct code inspection.
  - Created ASCII architectural diagrams and data contract matrices.
  - Recorded detailed modification histories in `changes.md`.
* **Human Review & Verification**:
  - Verified every terminal command, file path, and parameter against the actual code.
  - Eliminated stale references to obsolete `iptables` host implementations and outdated test counts.
* **Corrections & Refinements**:
  - Corrected broken markdown report links and updated badge test numbers from 86 to 128.
