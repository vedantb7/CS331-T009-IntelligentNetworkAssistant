# AI Tools Used

---

## 1. Executive Summary

During the design, development, hardening, and documentation of the **Intelligent Network Configuration Assistant (INA)**, the project team collectively leveraged artificial intelligence tools to support the engineering lifecycle. 

Rather than delegating autonomous decision-making or full system design to AI, the team utilized AI tools as **collaborative development assistants, pair programmers, and technical sounding boards**. All architectural decisions, component interfaces, security boundaries, and final code implementations were planned, reviewed, tested, and approved by the team members.

### Primary Purposes of AI Usage
Across all project phases, AI was primarily used for:
1. **Understanding the Problem Statement**: Deciphering the intersection of container networking namespaces, bridge packet flows, and LLM-driven automation.
2. **Scoping Project Capacity**: Identifying baseline requirements and establishing the practical boundaries of an educational networking prototype.
3. **Exploring Architectural Extensions**: Investigating how far the system could be extended (e.g. bi-directional ingress traffic shaping using Intermediate Functional Block devices, dynamic container discovery via Docker SDK, and FastMCP integration).
4. **Evaluating Design & Implementation Choices**: Comparing technical trade-offs, such as Layer-2 `nftables` bridge set filtering versus host-level `iptables` rules, and synchronous versus asynchronous pipeline execution.
5. **Development & Implementation Support**: Generating boilerplate code, writing parameterized unit and integration tests, and diagnosing edge-case failures.
6. **Technical Documentation**: Structuring comprehensive architectural specifications, setup guides, and academic reports.

---

## 2. Documented AI Tools

The team utilized the following AI tools throughout the project:

| AI Tool | Tool Category | Primary Role in INA Project |
| :--- | :--- | :--- |
| **Antigravity** | Agentic AI IDE & Pair Programmer | Codebase-wide refactoring, test suite generation (`tests/test_ina_focused.py`), documentation authoring (`docs/`, `reports/`), and automated verification execution. |
| **ChatGPT** (OpenAI) | Conversational LLM & Reasoning Engine | Conceptual design exploration, Linux networking syntax assistance (`tc`, `iproute2`), regex construction, and initial code scaffolding. |
| **Claude** (Anthropic) | Advanced Reasoning & Architecture Assistant | System prompt design for the Assistant, deep analysis of network edge cases (e.g. bridge forwarding vs host ping bypass), and technical report structuring. |
| **Cursor** | AI-Integrated Code Editor | Context-aware code editing, inline refactoring, navigating complex integration points, and accelerating component implementations. |
| **Gemini** (Google) | Multimodal & Technical Research Assistant | Exploring OpenRouter API configurations, reviewing zero-trust policy mechanisms, and sanity-checking security threat models. |
| **GitHub Copilot** | Inline Code Completion Assistant | Real-time code completions, standard library boilerplate (e.g. `subprocess.run` calls, Pydantic field validators), and repetitive test assertions. |

---

## 3. Tool-Specific Contributions & Utilization

### 3.1 Antigravity
* **Role**: Primary agentic IDE and pair-programming environment for system-wide auditing, test construction, and technical documentation.
* **Activities & Evidence**:
  - **Comprehensive Test Suite Generation**: Synthesized [`tests/test_ina_focused.py`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/tests/test_ina_focused.py), delivering 35 deterministic unit and asynchronous pipeline tests across all 10 project areas.
  - **System Documentation**: Authored structured documentation in [`docs/`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/docs/) (`architecture.md`, `INA_SETUP.md`, `mcpserver.md`, `network.md`, `policy.md`, `validation.md`, `security.md`, `testing.md`, `logging.md`) and maintained [`changes.md`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/changes.md).
  - **Automated Verification**: Executed the test runner (`pytest`), diagnosed failed assertions in asynchronous mocks, and confirmed full suite passing (128 tests).

### 3.2 ChatGPT (OpenAI)
* **Role**: Conceptual sounding board and reference generator for Linux networking commands and Python typing.
* **Activities**:
  - Assisted in exploring Linux Traffic Control (`tc`) Token Bucket Filter (`tbf`) parameters (`rate`, `burst`, `latency`) suitable for low-latency container environments.
  - Assisted in generating initial Python regular expressions for client hostname sanitization (`_CLIENT_NAME_RE`) and bandwidth rate formatting (`_RATE_RE`).
  - Provided syntax examples for Pydantic v2 field validators and dataclass definitions.

### 3.3 Claude (Anthropic)
* **Role**: High-level reasoning assistant for architectural design, edge-case analysis, and structured prompt engineering.
* **Activities**:
  - Assisted in designing the system prompt and few-shot classification examples used by `assistant/client.py` (`LLM_SYSTEM_PROMPT` and `RESPONSE_SYSTEM_PROMPT`).
  - Aided in analyzing the root cause of the "Host Ping Bypass" edge case, clarifying why packets issued from host namespaces do not traverse bridge forward netfilter hooks.
  - Supported structuring technical academic reports in [`reports/`](file:///mnt/DISK/Studies/SEM%205/CS%20331/Project/ina/reports/).

### 3.4 Cursor
* **Role**: AI-augmented coding environment used during active component development and manual testing.
* **Activities**:
  - Provided context-aware code suggestions while writing `mcp_server/tools.py` and `policy/policy_engine.py`.
  - Assisted in rapidly writing error handling blocks (`try...except`) and mapping status dictionary responses across teammate adapters.
  - Accelerated interactive terminal debugging and iterative code edits.

### 3.5 Gemini (Google)
* **Role**: Technical research assistant for API integrations and security evaluation.
* **Activities**:
  - Assisted in evaluating OpenRouter and OpenAI-compatible client configurations for model-agnostic LLM inference.
  - Aided in cross-checking the project's zero-trust security gate concept against standard defense-in-depth principles.
  - Assisted in reviewing YAML schema design for declarative policy rules (`policy/rules.yaml`).

### 3.6 GitHub Copilot
* **Role**: Inline code completion assistant inside IDEs for routine development acceleration.
* **Activities**:
  - Autocompleted repetitive test assertions across `tests/test_policy.py`, `tests/test_mcp.py`, and `tests/test_validation.py`.
  - Generated standard boilerplate for `argparse` CLI handling in `validation/monitor.py`.
  - Suggested routine logging formatting and dictionary unpacking idioms.

---

## 4. Collaborative Authorship Notice

All team members actively and collectively used these AI tools to accelerate development, improve test coverage, and ensure high-quality documentation. However, **all intellectual decisions, architecture definitions, code reviews, and physical verifications remained strictly under human ownership and responsibility**.
