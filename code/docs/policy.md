# Policy and Rules

## Overview

The `policy/` component is the rule-enforcement layer of the Intelligent Network Configuration Assistant. It sits between the Assistant and the MCP Server, and decides whether a requested network action is allowed to happen at all. It also keeps a record of every decision made, so the system can explain its own past actions.

**Responsibilities:**

* Store the network's rulebook (protected clients, bandwidth limits, allowed actions)
* Check every requested action against the rulebook before it reaches the MCP Server
* Log every decision (allowed or denied) with a reason

The Policy component does **not** talk to Docker, `iptables`, or `tc` directly. It only makes a decision and hands that decision back — the MCP Server is what actually touches the network.

---

## Files

```text
policy/
├── __init__.py        # marks this folder as an importable Python package
├── rules.yaml          # the rulebook
├── policy_engine.py     # check_policy() - the ALLOW/DENY decision logic
└── audit_log.py          # records every decision for later explanation
```

### `rules.yaml`

The rulebook, written as plain YAML so it can be edited without touching any code.

```yaml
protected_clients:
  - server

bandwidth:
  minimum: 1mbit
  maximum: 20mbit

allowed_actions:
  - block_client
  - unblock_client
  - limit_bandwidth

known_clients:
  - client1
  - client2
  - server
```

### `policy_engine.py`

Exposes one main function:

| Function | Parameters | Returns |
|---|---|---|
| `check_policy` | `action: str`, `params: dict` | `PolicyResult(allowed: bool, reason: str)` |

Example:

```python
from policy.policy_engine import check_policy

result = check_policy("block_client", {"client": "server"})
# result.allowed -> False
# result.reason  -> "'server' is a protected client and cannot be blocked."
```

### `audit_log.py`

Records every decision to `policy/audit_log.jsonl` and can turn a log entry into a plain-English explanation.

| Function | Purpose |
|---|---|
| `log_action(action, params, status, reason)` | Appends one entry to the log |
| `get_all_logs()` | Returns every logged entry |
| `explain_action(index=-1)` | Returns a plain-English explanation of a past decision |

---

## Rulebook Configuration

The current rules match the project's Docker network:

```text
Network: project-net
Subnet: 172.20.0.0/24

client1 → 172.20.0.2
server  → 172.20.0.3   (protected - cannot be blocked or bandwidth-limited)
client2 → 172.20.0.4
```

### Client Block / Unblock

Any `block_client` or `unblock_client` request is checked against:
1. Is the client name recognized (`known_clients`)?
2. Is the client protected (`protected_clients`)?

### Bandwidth Limit

Any `limit_bandwidth` request is additionally checked against the `bandwidth.minimum` / `bandwidth.maximum` range in `rules.yaml`.

Example denial:

```json
{
  "allowed": false,
  "reason": "Requested rate 50.0mbit is outside the allowed range (1.0mbit - 20.0mbit)."
}
```

---

## Setup

### 1. Install Python dependency

From the project root:

```bash
pip install pyyaml
```

No other dependency is required — `policy_engine.py` and `audit_log.py` only use `pyyaml` and Python's built-in `json`, `os`, and `datetime` modules.

### 2. Verify the rulebook loads correctly

```bash
python3 -c "from policy.policy_engine import load_policy; print(load_policy())"
```

This should print the contents of `rules.yaml` as a Python dictionary.

---

## Testing

Test cases live in `tests/test_project.py` and can be run with:

```bash
pytest tests/test_project.py -v
```

### Manual testing without pytest

```bash
python3 policy/policy_engine.py
```

Expected output includes lines like:

```text
[DENY] action=block_client params={'client': 'server'} -> 'server' is a protected client and cannot be blocked.
[ALLOW] action=block_client params={'client': 'client1'} -> 'client1' is not protected. Block permitted.
```

---

## Integration With Other Components

The Policy Engine does **not** receive natural-language commands and does **not** call `iptables`/`tc` directly.

The overall flow is:

```text
Admin/User
    ↓
AI Assistant
    ↓
Policy Engine  (check_policy)
    ↓
   Allowed? ----No----> reject request, return reason to Assistant
    ↓ Yes
MCP Server  (block / unblock / limit)
    ↓
Network Configuration (iptables / tc)
    ↓
Docker Network
```

### Interaction

1. **Assistant** interprets the user's natural-language request and identifies the action and parameters.
2. **Assistant or MCP Server calls `check_policy(action, params)`** before any real network command runs.
3. If `check_policy` returns `allowed=False`, the request stops here — the reason is returned to the user, and nothing changes on the network.
4. If `check_policy` returns `allowed=True`, the MCP Server proceeds to execute the real `block` / `unblock` / `limit` tool.
5. Every decision, allowed or denied, is recorded via `audit_log.log_action()` for later explanation.

The Policy component is therefore responsible only for **deciding what is allowed and recording why** — it never executes network changes itself.
