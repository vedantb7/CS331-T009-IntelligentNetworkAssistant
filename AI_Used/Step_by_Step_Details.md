# Step-by-Step AI Contributions — INA v2

---

## 1. Overview

This document breaks down where AI was used in the second half of the project (INA v2). For each stage it records:
- **Intent**: what we wanted to achieve.
- **AI assistance**: what the AI tool contributed.
- **Review & verification**: how the output was checked.
- **Corrections**: what had to be changed.

Stages for Khushi's component (AI assistant, verification and evaluation) are filled in below. Other members add their own stages in the same format.

```text
Stage 1: Re-scoping after feedback and roadmap
Stage 2: Literature review
Stage 3: AI assistant (assistant/)
Stage 4: Post-change verification (network/verification.py)
Stage 5: Evaluation harness (eval/)
Stage 6: Integration with the MCP server and policy engine
Stage 7: Documentation and final report
```

---

### Stage 1: Re-scoping After Feedback and Roadmap
* **Intent**: Respond to the professor's feedback with a narrower problem, defined roles, a hardware target and a realistic plan.
* **AI assistance**: Proposed candidate problem statements, a staged roadmap and a four-way work split.
* **Review & verification**: The team discussed the options and chose the final problem statement and split together.
* **Corrections**: Plan written as stages rather than week by week; extra features that did not serve the core problem were left out.

---

### Stage 2: Literature Review
* **Relevant output**: Related Work section of the final report.
* **Intent**: Position INA v2 against existing work and identify the gap it fills.
* **AI assistance**: Found and summarised candidate papers on natural-language network management, LLM-generated configurations, intent-based networking and prompt injection.
* **Review & verification**: Each reference was checked to confirm it exists and that the summary matches the paper before it was cited.

---

### Stage 3: AI Assistant
* **Relevant files**: `assistant/contract.py`, `assistant/intent.py`, `assistant/llm.py`, `assistant/prompts.py`, `assistant/transport.py`, `assistant/client.py`, `assistant/__main__.py`
* **Intent**: Turn a plain-English request into exactly one tool call, ask when unclear, never let the AI choose a role, and run without an API key.
* **AI assistance**: Drafted the tool contract, the rule-based parser, the LLM backend interface with fallback, the in-memory mock server and the CLI.
* **Review & verification**: Read every file, ran `tests/unit/test_intent.py` and `tests/unit/test_assistant_client.py`, and tried requests in the interactive CLI.
* **Corrections**: Kept the core standard-library only; restricted the device names given to the model to admin-assigned names so a device's self-reported name cannot inject instructions.

---

### Stage 4: Post-Change Verification
* **Relevant files**: `network/verification.py`, `tests/unit/test_verification.py`
* **Intent**: Prove a change took effect by measurement (ping, nftables, tc, iperf3), not by exit codes.
* **AI assistance**: Drafted `verify_block`, `verify_unblock` and `verify_limit` with an injectable command runner.
* **Review & verification**: Unit tests with a mocked runner covering pass and fail cases for each check.
* **Corrections**: Removed a line in `verify_block` that was immediately overwritten by the next assignment.

---

### Stage 5: Evaluation Harness
* **Relevant files**: `eval/cases.py`, `eval/run_eval.py`, `eval/results/*.csv`
* **Intent**: Produce reproducible numbers for the report, and show that the policy layer stops unsafe actions even when the AI front end is fooled.
* **AI assistance**: Drafted the case lists and the runner that records *attempted* vs. *executed* unsafe actions and writes CSV files.
* **Review & verification**: Ran `python3 -m eval.run_eval --repeats 5` and checked the CSVs. Final results: functional 16/16, clarification 10/10, attacks 30/65 attempted and 0/65 executed.
* **Corrections**: The evaluation showed that "-5 mbps" was parsed as 5 Mbit/s; the parser was fixed to ask for clarification on non-positive rates and the evaluation was re-run.

---

### Stage 6: Integration With the MCP Server and Policy Engine
* **Relevant files**: `assistant/intent.py`, `assistant/client.py`, `assistant/transport.py`, `eval/cases.py`, `tests/unit/test_intent.py`, `tests/unit/test_assistant_client.py`
* **Intent**: Make the assistant work with the team's real MCP server without changing teammates' files.
* **AI assistance**: Compared the assistant with `mcp_server/tools.py` and `policy/`, listed mismatches and suggested the edits.
* **Review & verification**: Ran the assistant's unit tests (29 passing) and a demo of list / block / limit / unblock / protected device / different role locally in VS Code before pushing.
* **Corrections**: Switched to canonical tool names (`block_client`, `unblock_client`, `get_status`); converted Mbit/s to integer `download_kbit` / `upload_kbit`; added `target='all'` for block and unblock; added a `--server service` option for development.

---

### Stage 7: Documentation and Final Report
* **Relevant files**: `docs/assistant.md`, final report
* **Intent**: Document the component and write the report following the course template.
* **AI assistance**: Structured the documentation and the report sections from the repository docs and evaluation outputs; drew the architecture and evaluation figures.
* **Review & verification**: Numbers were taken from re-running the tests and the evaluation; hardware claims were limited to what `docs/testing.md` records.
* **Corrections**: Names, roll numbers, ratings and teammates' AI usage are filled in by the team.

---

## Other Members

_Vedant, Ananya and Dhruv add their stages here in the same format._
