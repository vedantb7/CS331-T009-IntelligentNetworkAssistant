# AI Assistant, Verification & Evaluation (Khushi)

## Overview

This component is the operator-facing side of INA v2 and the evidence side.
It has three parts:

1. **Assistant** (`assistant/`): an LLM (any provider), or the rule-based
   fallback parser, turns a plain-English request into exactly one of the eight
   MCP tools the server lists, or asks a question when the request is unclear.
   It sends the call to the MCP server and explains the structured result in
   plain words.
2. **Verification** (`network/verification.py`): after a change is applied, it
   confirms by measurement (ping, iperf3, nft, tc) that the network is actually
   in the intended state. The service layer calls it after every change.
3. **Evaluation** (`eval/`): runs the assistant against scripted scenarios and
   an attack set, and writes reproducible numbers for the report.

Design rule for the whole project: **the AI proposes, the policy decides, the
network proves.** The assistant has no authority. Its role comes from the
person's role token (granted by the admin, bound to their device), never from
the model. It never runs a command, never sees the commands the router ran,
and cannot confirm a ban: that takes a human y/N.

## Request flow

```
operator types a request
  -> LLM (any provider, from .env) is given the MCP server's tools/list schemas
     and chooses one tool + arguments by native function calling, or asks a
     question; without an LLM, or if the call fails, the rule-based parser
  -> if unclear: ask a question and stop
  -> MCP tools/call over Streamable HTTPS, role TOKEN as the bearer credential
  -> server: role from token -> policy ALLOW/DENY -> sudo ina-netctl -> apply
     -> verify -> log
  -> the structured, command-free result goes back to the same LLM
     conversation as the tool's result; the LLM writes the reply (shown only
     if it echoes the real status), else the deterministic template
```

## Files

```text
assistant/
  contract.py    Shared tool contract (names, arguments, result shape). The MCP
                 server must match this. The single source of truth for C4.
  intent.py      Deterministic parser. Zero dependencies, always runs, asks
                 instead of guessing. The guaranteed fallback.
  prompts.py     Tool-choosing system prompt and reply instructions.
  llm.py         Backends: RuleBasedBackend (default / fallback),
                 AnthropicBackend (Claude, official SDK) and
                 OpenAICompatibleBackend (OpenRouter, Gemini, OpenAI, Groq,
                 Ollama, any /chat/completions endpoint; stdlib only).
  config.py      Loads inav2/.env (see .env.example).
  result_view.py The structured view of a result the LLM is allowed to see.
  transport.py   McpTransport (the real path: official MCP SDK client, Streamable
                 HTTPS to the Pi, or stdio for development), MockTransport
                 (in-memory server for the demo and the eval), ServiceTransport
                 (direct service layer, development) and HttpTransport (legacy
                 placeholder, not MCP; do not use).
  client.py      Orchestrator + CLI. Keeps a transcript.
  __main__.py    `python -m assistant`

network/verification.py   verify_block / verify_unblock / verify_limit.
eval/                      cases.py, run_eval.py, results/*.csv
tests/unit/                test_intent.py, test_assistant_client.py,
                           test_llm_backends.py, test_result_view.py,
                           test_mcp_pipeline.py, test_mcp_http.py,
                           test_verification.py
```

## Guards around the LLM's tool call

The server's policy is the safety boundary, but the client also keeps the LLM honest about *what the operator asked*, so a refusal is explained instead of being quietly "fixed":

| Request | Without the guard | Now |
|---|---|---|
| No tool can do it (`give dhruv-phone a 1 GB data quota`, `make the wifi faster`, change the Wi-Fi password) | The LLM called `get_status` and the status was shown as the answer | Answered directly (`assistant/intent.py` `unsupported_reason`), no tool and no LLM call |
| A speed outside the role's range (`limit dhruv-phone to 50 mbps`) | The LLM lowered it to the 20 Mbit/s maximum and it was applied | The operator's own number is sent (`stated_rates_kbit`); the server refuses and says why |
| `ban dhruv-phone` with no duration | The LLM left the duration out, which means a permanent ban | The assistant asks: permanently, or for how many minutes? |

The prompts (`assistant/prompts.py`, tool rules 9-11 and reply rules 6-8) say the same; the code checks in `NetworkAssistant._vet_llm_call` enforce it whatever the model does. Banned devices stay in the LLM's known-device list (they are off the Wi-Fi but can still be named, e.g. to unban them).

**Kick vs ban.** `kick_client` disconnects a device once; phones usually rejoin within a fraction of a second, so the operator may not notice. The reply says so. Use `ban_client` to keep a device off.

## How to run

No API key is needed. The default uses the deterministic parser and the
in-memory mock network, so the whole pipeline runs on any laptop today.

```bash
# interactive
python3 -m assistant

# one request
python3 -m assistant "limit guest-phone to 5 mbps"
python3 -m assistant --role admin "limit lab-laptop-3 to 1 mbps"   # demo tokens, mock network

# use a real LLM (optional): copy .env.example to .env and set, e.g.
#   LLM_PROVIDER=anthropic  LLM_API_KEY=sk-ant-...            (pip install anthropic)
#   LLM_PROVIDER=openrouter LLM_API_KEY=sk-or-...  LLM_MODEL=<provider/model>
#   LLM_PROVIDER=gemini     LLM_API_KEY=<AI Studio key> LLM_MODEL=<gemini model>
#   LLM_PROVIDER=ollama     LLM_MODEL=<local model>           (no key)

# the real Pi (Streamable HTTPS; INA_MCP_URL / INA_MCP_CA / INA_ROLE_TOKEN in .env).
# INA_ROLE_TOKEN is your personal token from 'sudo ina-netctl roles grant ...';
# it only works from the device your role is bound to.
python3 -m assistant --server mcp "list devices"
```

## Tests and evaluation

```bash
# unit tests (offline, no root, no Pi)
python3 tests/run_all_tests.py

# evaluation -> prints a summary and writes eval/results/*.csv
python3 -m eval.run_eval --repeats 5
```

### What the evaluation reports (against the mock, deterministic)

| Group | Claim | Result |
| :--- | :--- | :--- |
| Functional (E2) | clear requests get the right outcome | 16/16 correct |
| Clarification (E8) | incomplete requests are questioned, not guessed | 10/10 asked |
| Attacks (E3) | unsafe actions are stopped even when the AI is fooled | AI attempted an unsafe action in 30/65 runs; **0/65 executed** |

The attack numbers are the headline: the AI can be talked into *attempting* an
unsafe action (claiming admin, naming a protected device), but the policy layer
stopped every one. That gap is the point of defence in depth. These numbers
come from the mock (whose decisions are tested for parity with the real policy
engine); they still have to be re-measured with a live LLM and on the Pi.

## Interfaces with the rest of the team

- **MCP server (`mcp_server/`):** the assistant reads the tool names and
  schemas live from `tools/list` and sends `tools/call` over HTTPS with the
  person's bearer token. Denials and failures come back as `isError` results
  (`Denied by policy [R-...]` / `Failed: ... (reference <id>)`).
- **Policy (`policy/`):** decides every call; the role comes from the token.
  The mock's decisions are kept in parity with the real engine
  (`test_decision_parity_with_assistant_mock`).
- **Pi (`network/`, `config/`):** `verify_limit` needs an iperf3 server (probe
  laptop) and the baseline speeds.
- **Verification:** `verify_block / verify_unblock / verify_limit` return a
  `VerificationResult` whose `.passed` is a plain bool.

## Explaining this component to the professor

*In one minute:* The AI is only a translator. It turns a plain request into one
of the server's tool calls and asks when something is unclear. It cannot choose its
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
