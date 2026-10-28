# AI Assistant, Verification & Evaluation (Khushi)

## Overview

This component is the operator-facing side of INA v2 and the evidence side.
It has three parts:

1. **Assistant** (`assistant/`): turns a plain-English request into exactly one
   of four fixed tool calls, asks a question when the request is unclear, sends
   the call to the MCP server, and explains the result in plain words.
2. **Verification** (`network/verification.py`): after a change is applied, it
   confirms by measurement (ping, iperf3, nft, tc) that the network is actually
   in the intended state. Dhruv's MCP pipeline calls it.
3. **Evaluation** (`eval/`): runs the assistant against scripted scenarios and
   an attack set, and writes reproducible numbers for the report.

Design rule for the whole project: **the AI proposes, the policy decides, the
network proves.** The assistant has no authority. It never attaches a role,
never runs a command, and only ever sees admin-assigned device names.

## Request flow

```
operator types a request
  -> interpret (LLM if configured, else the rule-based parser)
  -> if unclear: ask a question and stop
  -> build one tool call + attach the role TOKEN (not a role name)
  -> send via transport to the MCP server
  -> server: role from token -> policy ALLOW/DENY -> apply -> verify -> log
  -> assistant explains the result in plain words
```

## Files

```text
assistant/
  contract.py    Shared tool contract (names, arguments, result shape). Dhruv's
                 server must match this. The single source of truth for C4.
  intent.py      Deterministic parser. Zero dependencies, always runs, asks
                 instead of guessing. The guaranteed fallback.
  prompts.py     System prompt + few-shot examples for the LLM backend.
  llm.py         Backends: RuleBasedBackend (default) and GeminiBackend
                 (optional, stdlib urllib, used only if LLM_API_KEY is set).
  transport.py   MockTransport (in-memory full server, for today's demo and the
                 eval) and HttpTransport (Dhruv's real server, once it is up).
  client.py      Orchestrator + CLI. Keeps a transcript.
  __main__.py    `python -m assistant`

network/verification.py   verify_block / verify_unblock / verify_limit.
eval/                      cases.py, run_eval.py, results/*.csv
tests/unit/                test_intent.py, test_verification.py,
                           test_assistant_client.py
```

## How to run

No API key is needed. The default uses the deterministic parser and the
in-memory mock network, so the whole pipeline runs on any laptop today.

```bash
# interactive
python3 -m assistant

# one request
python3 -m assistant "limit guest-phone to 5 mbps"
python3 -m assistant --role admin "block lab-laptop-3"

# use a real LLM (optional): set these, then run as above
export LLM_PROVIDER=gemini
export LLM_API_KEY=...        # a Google AI Studio key
export LLM_MODEL=gemini-2.0-flash

# point at Dhruv's MCP server once it is up
python3 -m assistant --server http://192.168.50.1:8000 "list devices"
```

## Tests and evaluation

```bash
# unit tests (offline, no root, no Pi)
python3 -m unittest discover tests/unit -v

# evaluation -> prints a summary and writes eval/results/*.csv
python3 -m eval.run_eval --repeats 5
```

### What the evaluation reports (against the mock, deterministic)

| Group | Claim | Result |
| :--- | :--- | :--- |
| Functional (E2) | clear requests get the right outcome | 10/10 correct |
| Clarification (E8) | incomplete requests are questioned, not guessed | 8/8 asked |
| Attacks (E3) | unsafe actions are stopped even when the AI is fooled | AI attempted an unsafe action in 20/40 runs; **0/40 executed** |

The attack numbers are the headline: the AI can be talked into *attempting* an
unsafe action (claiming admin, naming a protected device), but the policy layer
stopped every one. That gap is the point of defence in depth. These numbers
come from the mock policy; they will be re-measured against Ananya's real
policy engine and on the Pi.

## Interfaces with the rest of the team

- **From Dhruv (MCP server):** the four tool names, their arguments, and the
  result shape in `assistant/contract.py`; the server address and the operator
  token. Until his server is up, `MockTransport` stands in.
- **From Vedant (Pi):** the `state` of a device, a probe laptop running an
  iperf3 server, and the baseline speeds. `verify_limit` needs the iperf3
  server IP.
- **From Ananya (policy):** the device list, the real ALLOW/DENY + reason, and
  attack ideas. The mock's policy table must stay consistent with
  `policy/rules.yaml`.
- **To Dhruv:** `verify_block / verify_unblock / verify_limit`, called after
  each change. Each returns a `VerificationResult` whose `.passed` is a plain
  bool.

## Explaining this component to the professor

*In one minute:* The AI is only a translator. It turns a plain request into one
of four tool calls and asks when something is unclear. It cannot choose its
role or run commands, and it only sees admin-assigned device names, so a
malicious device name cannot plant instructions in it. After every change we
measure the network ourselves, because a command that exits cleanly does not
prove the network changed.

*Likely questions:*

- **What if the AI is fooled?** We tested it. Across the attack set the AI
  attempted an unsafe action in half the runs, but zero unsafe actions were
  executed, because the role token and the policy engine, not the AI, decide
  what happens.
- **Why verify with ping and iperf3?** They give independent evidence. This is
  what intent-based networking calls assurance (RFC 9315), and prior work found
  that checking an AI's output with verifiers catches its errors.
- **Which model, and does the design depend on it?** We record the model name
  and version. The default is a deterministic parser so the system runs and the
  results reproduce without any model; the safety design does not depend on the
  model.
```
