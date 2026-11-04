# Thought Process & AI Workflow — INA v2

---

## 1. What Changed From the First Half

The first version of INA ran in Docker and was built around a fixed set of tools. The professor's feedback pointed out that it had no real admin/operator roles, did not show why MCP matters in practice, had a broad problem statement and no hardware. In v2 we kept the core idea but narrowed the problem to **one real Wi-Fi router (Raspberry Pi 5)** and put **who is allowed to do what** at the centre.

This also changed how we used AI. In the first half AI was mostly used to build features. In the second half it was used more for **thinking through scope, reading literature, and checking our work**, and the design was decided before any code was drafted.

---

## 2. Guiding Principle

> **The AI proposes, the policy decides, the network proves.**

The same rule applied to our own use of AI during development: an AI tool could suggest code or text, but the team decided what was accepted, and nothing was accepted until it had been run and tested.

---

## 3. Workflow

```text
1. Read the feedback and decide the problem      (team)
        │
        ▼
2. Explore options and related work              (team + AI)
        │
        ▼
3. Agree on roles, tools and interfaces          (team)
        │
        ▼
4. Draft code for one component at a time        (member + AI)
        │
        ▼
5. Read the diff, run unit tests                  (member)
        │
        ▼
6. Integrate with teammates' code, find mismatches (member + AI)
        │
        ▼
7. Re-run all tests and the evaluation           (member)
        │
   [failures?] ── yes ──> fix, explain, re-test
        │
        ▼
8. Document only what was actually verified      (member + AI)
```

---

## 4. Stage Notes

### Stage 1: Deciding the problem
The team chose the final problem statement from the options we discussed. The deciding factors were the professor's comments: a defined admin and operator, a clear real-world use for MCP (letting a non-expert make safe changes without root), and a Raspberry Pi demonstration.

### Stage 2: Literature review
AI was used to search for and summarise papers. Every reference was checked before being cited, and the gap we claim (authorisation, untrusted LLM, verification on real hardware) came from comparing those papers with our design.

### Stage 3: Interfaces before code
Before drafting the assistant, the shared tool contract was written so that the assistant and the MCP server would build against the same tool names, arguments and result shape. A key decision was that **no tool takes a `role` argument**; the role always comes from the caller's token.

### Stage 4: One component at a time
The assistant was built so it could run end to end against an in-memory mock server with no API key and no Pi. This let me test my whole component before teammates' parts were ready, and kept the evaluation deterministic.

### Stage 5: Testing and evaluation
The evaluation separates what the AI *attempted* from what was *executed*. This was a deliberate choice: the point is not that the AI is never fooled, but that being fooled cannot change the network.

### Stage 6: Integration
When teammates pushed their code, the tool names in the assistant (`block_device`, `list_devices`, `restore_device`) no longer matched the server (`block_client`, `get_status`, `unblock_client`), and the server expected bandwidth in integer kbit/s. These were found by comparing the two sides, fixed on the assistant's side only, and confirmed with the unit tests and a demo run before pushing.

### Stage 7: Documentation and reporting
Documents and the report only describe what has evidence. For example, the report states that the full pipeline has not yet been run end to end on the Pi, because our test records say so.

---

## 5. Lessons Learned

1. **Scope first, code later.** Deciding the problem, the roles and the interfaces before asking for code saved a lot of rework.
2. **Interfaces drift.** Even with a shared contract, names and units changed as teammates built their parts. Integration needs a deliberate comparison step.
3. **AI output must be run, not trusted.** The evaluation itself caught a bug (negative rates parsed as positive) in AI-drafted code.
4. **Honest reporting matters.** "Tested in a lab" and "tested on the Pi" are different claims, and the documents keep them separate.
