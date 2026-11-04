# AI Tools Used — INA v2 (Second Half of the Project)

---

## 1. Scope of This Folder

This folder documents how AI tools were used in the **second half** of the project: the rebuild of the Intelligent Network Configuration Assistant as **INA v2** on a Raspberry Pi 5 router, after the professor's feedback on the first version.

AI usage in the **first half** (the Docker-based INA v1) is documented separately in the previous repository:
[CS331-T009-IntelligentNetworkAssistant / AI_Used](https://github.com/vedantb7/CS331-T009-IntelligentNetworkAssistant/tree/main/AI_Used).

---

## 2. Executive Summary

In v2, AI was used as a **study partner, pair programmer and writing assistant**. It was not used to make project decisions on our behalf. The problem statement, role design, security boundaries, work split and all final code were decided, reviewed, run and tested by the team.

### Primary Purposes of AI Usage in v2
1. **Re-scoping after feedback**: turning the professor's comments (no admin/operator roles, unclear real-world value of MCP, too broad a problem statement, no hardware) into a narrower problem statement and a staged roadmap.
2. **Literature review**: finding and summarising related work on natural-language network management, LLM-generated network configuration, intent-based networking and prompt injection.
3. **Component implementation support**: drafting code for the AI assistant, post-change verification and the evaluation harness, and writing unit tests.
4. **Integration and debugging**: comparing the assistant against teammates' MCP server code, finding mismatches, and running tests.
5. **Documentation and reporting**: structuring component documentation and the final report according to the course template.
6. **Understanding concepts**: plain-language explanations of MCP, nftables vs. iptables, tc, hostapd and the Raspberry Pi routing setup.

---

## 3. Documented AI Tools

| AI Tool | Tool Category | Primary Role in INA v2 | Used by |
| :--- | :--- | :--- | :--- |
| **Claude** (Anthropic) | Conversational LLM and coding assistant | Re-scoping and roadmap, literature review, assistant/verification/evaluation code drafts, integration with the MCP server contract, test runs, documentation and report structure | Khushi |
| _To be added_ | | | Vedant |
| _To be added_ | | | Ananya |
| _To be added_ | | | Dhruv |

> Each member adds the tools they used in v2 to this table and their prompts to `Prompts.md`.

---

## 4. Tool-Specific Contributions

### 4.1 Claude (Anthropic) — Khushi
* **Role**: Planning partner for the v2 rebuild, research assistant for the literature review, and pair programmer for the AI assistant, verification and evaluation component (`assistant/`, `network/verification.py`, `eval/`).
* **Activities**:
  - Helped turn the professor's feedback into 2–3 candidate problem statements, a staged roadmap and a four-way work split (Pi and network infrastructure; policy, roles and security; MCP server and network actions; AI assistant, verification and evaluation).
  - Helped search for and summarise related work: Lumi (USENIX ATC 2021), NetConfEval (CoNEXT 2024), Mondal et al. (HotNets 2023), RFC 9315 on intent-based networking, and Greshake et al. (AISec 2023) on indirect prompt injection.
  - Drafted the first versions of the shared tool contract (`assistant/contract.py`), the rule-based fallback parser (`assistant/intent.py`), the LLM backend interface (`assistant/llm.py`), the in-memory mock server (`assistant/transport.py`), the orchestrator/CLI (`assistant/client.py`), the verification checks (`network/verification.py`) and the evaluation harness (`eval/`).
  - Helped write offline unit tests (`tests/unit/test_intent.py`, `test_assistant_client.py`, `test_verification.py`).
  - During integration, compared the assistant with the MCP server's tool names and arguments and suggested the changes (canonical tool names, Mbit/s → kbit/s, `target` argument for block/unblock).
  - Helped structure `docs/assistant.md` and the final project report.

### 4.2 Other members
_To be completed by Vedant, Ananya and Dhruv._

---

## 5. Authorship Notice

AI tools were used to speed up research, coding, testing and writing. All design decisions, security rules, reviews, test runs and hardware work remained the responsibility of the team members, and every AI-drafted file was read, run and corrected before it was committed.
