# policy/ — roles, rulebook, policy engine, tokens, audit log (Member 2)

Authorization backend for the eight MCP tools in [`docs/tools.md`](../docs/tools.md):
`block_client`, `unblock_client`, `limit_bandwidth`, `remove_bandwidth_limit`,
`get_status`, `kick_client`, `ban_client`, `unban_client`.

```
policy/
  roles.yaml        admin / operator / viewer: tools, device classes, speed range, ban limits
  rules.yaml        protected classes/names/MACs/IPs, subnet, name pattern, block targets,
                    hard bandwidth + ban limits (protected_macs is also read by network/wireless.py)
  devices.yaml      admin-owned list: name -> MAC -> class (optional fixed IP)
  models.py         tool names, rule IDs, Device, PolicyDecision
  config.py         loads + validates roles/rules (fails closed)
  registry.py       device list; exact lookup by name / MAC / IP, "did you mean" candidates
  validation.py     safe parsing of names, IPs, MACs, kbit/s, minutes; log sanitising
  engine.py         PolicyEngine.evaluate(role, tool, arguments, context) -> allow/deny + reason
  authorization.py  role tokens (SHA-256 hashed), constant-time check
  audit.py          append-only, hash-chained JSONL audit log + CLI
  guard.py          PolicyGuard: token -> role -> engine -> audit (for the MCP server)
  integration.py    evaluate_policy(): drop-in for service.py's evaluate_policy_placeholder
  _yaml.py          stdlib-only YAML-subset reader, used only when PyYAML is missing
```

## Roles (from `docs/tools/*.md` "Role Requirements")

| | admin | operator | viewer |
|---|---|---|---|
| Tools | all 8 (+ `update_policy`) | all 8 | `get_status` only |
| Device classes | staff, guest, unknown | guest, unknown | – |
| `limit_bandwidth` (download & upload) | 1000–20000 kbit/s | 2000–20000 kbit/s | – |
| `ban_client` duration | 1–10080 min, or permanent | 1–60 min, **required** | – |
| `unban_client` | any ban | only bans made by an operator | – |

Nobody can act on a **protected** device: class `protected`, a name in
`protected_devices`, a MAC in `protected_macs`, or an IP in `protected_ips`
(the router `192.168.50.1`).

### Mapping of the earlier `wifi-00x` rules (docs/implementation_status.md)

| Earlier rule | Now enforced by | Rule ID on deny |
|---|---|---|
| wifi-001 never evict infrastructure | `rules.yaml` protected_* (applies to every tool) | `R-protected` |
| wifi-002 operator may kick guests | operator `device_classes: [guest, unknown]` | `R-role-class` |
| wifi-003 operator bans ≤ 60 min, duration required | operator `ban.max_minutes: 60`, `allow_permanent: false` | `R-ban-duration` |
| wifi-004 operator may lift own bans | operator `ban.unban_scope: own_role` | `R-unban-scope` |
| wifi-005 admin full control (non-protected) | admin role | – |
| wifi-006 viewer cannot mutate | viewer `actions: [get_status]` | `R-role-action` |

## Unknown and ambiguous devices

- **Names** must exactly match `devices.yaml` (after trimming and lower-casing).
  A partial match is refused with `R-ambiguous-device` and the `candidates` list,
  so the assistant asks the user instead of guessing. No match: `R-unknown-device`.
- **IP / MAC** of a device that is not in `devices.yaml` is accepted and treated as
  class `unknown`. IPs must be host addresses in `192.168.50.0/24`. MACs are only
  accepted by `kick_client`, `ban_client` and `unban_client`, as in the schemas.
- If the server passes what it resolved a client to (`client_ip`, `client_mac`),
  the engine cross-checks it against the device list. A name, IP and MAC that point
  at different devices are refused with `R-identity-conflict`, which catches a
  device that names itself `lab-laptop-3`. A protected MAC behind an unregistered
  IP is still protected.
- Peer targets of `block_client`/`unblock_client` must also be devices the role
  may act on, because a pair rule changes the peer's connectivity too.

## For the service layer (`network/service.py`)

`policy/integration.py` has the same call shape as `evaluate_policy_placeholder`:

```python
from inav2.policy.integration import evaluate_policy    # replaces the placeholder

permitted, reason = evaluate_policy(
    "ban_client", client_ip=station.ip, role=role,
    client=args.client, client_mac=station.mac,            # optional, cross-checked
    duration_minutes=args.duration_minutes, reason=args.reason,
)
# limit_bandwidth: pass download_kbit=..., upload_kbit=...
# block/unblock:   pass target=args.target (and target_ip=... for a peer)
# unban_client:    pass banned_by_role=<from state.json, or None if not banned>
if not permitted:
    self._audit_denied(role, "ban_client", args.client, reason)
    raise PolicyDeniedError(reason)
```

`evaluate_policy_decision(...)` returns the full `PolicyDecision` (with rule ID).
`get_tools_for_role(role)` is a policy-backed version of the one in `mcp_server/server.py`.
These adapters only replace the placeholder and `_check_role`. Lockout, rate-limit
and write-switch checks stay in the service.

## For the MCP server: tokens instead of `INA_ROLE`

```python
from inav2.policy import PolicyGuard

guard = PolicyGuard.from_config()                 # once, at start-up

result = guard.check(token, tool, arguments, request_id, context=None)
if not result.allowed:
    return {"status": "denied", "decision": result.decision.to_dict(), ...}  # nothing changed
# Use the validated values: result.role, result.device (name/mac/ip/cls),
# result.target, result.params (download_kbit/upload_kbit, duration_minutes, reason, confirm)
...call NetworkService with role=result.role...
guard.record_outcome(result, "success", "Banned guest-phone for 30 min.", verification_passed=True)
```

- Never take the role from the request or the AI. The role comes from the token.
- Call `record_outcome` once after every allowed request (`success`, `preview`, `failure`).
- The guard already logs denials. If `check` returns `R-audit-unavailable` or
  `R-internal-error`, treat it as a denial.

## Tokens

```bash
python -m policy.authorization --new admin       # prints a token and its SHA-256
python -m policy.authorization --new operator
python -m policy.authorization --new viewer
```
Put only the hashes on the Pi (`.env`, never in git):
`INA_ADMIN_TOKEN_SHA256`, `INA_OPERATOR_TOKEN_SHA256`, `INA_VIEWER_TOKEN_SHA256`.
For local demos with the assistant's built-in tokens, set `INA_DEMO_TOKENS=1`. That
accepts `admin-token`, `operator-token` and `viewer-token`; never use it on the real Pi.

## Audit log

`logs/policy_audit.jsonl` (override with `INA_POLICY_AUDIT_LOG`). Each request writes
two records, linked by `request_id`:
- `decision`: identity (`role:token-id`), action, device, target, sanitised arguments,
  decision, rule and reason.
- `outcome`: status and verification result.

Records are SHA-256 hash-chained. Secrets and unknown argument values are never stored.
```bash
python -m policy.audit --tail 20
python -m policy.audit --denied
python -m policy.audit --request <id>     # plain-language "why was this denied?"
python -m policy.audit --verify           # detects edited or deleted records
```

## Rule IDs

| Rule | Meaning |
|---|---|
| R-token | missing/unknown token |
| R-role-unknown | role not in roles.yaml |
| R-unknown-tool | not one of the allowed tools |
| R-role-action | role may not use this tool (viewer mutating, operator `update_policy`) |
| R-bad-arguments | unexpected/conflicting argument, wrong `confirm` type |
| R-bad-device | client missing or not a valid name / subnet IP / MAC (where allowed) |
| R-unknown-device | name not in devices.yaml |
| R-ambiguous-device | name only partly matches; `candidates` returned, assistant should ask |
| R-identity-conflict | name / IP / MAC disagree with the device list |
| R-protected | protected device, MAC or IP, whatever the role |
| R-role-class | role may not act on this class of device |
| R-bad-target, R-self-target | invalid block target, or client blocked from itself |
| R-bad-rate, R-rate-range | speed missing/not a whole kbit/s, or outside the role's range |
| R-ban-duration, R-bad-reason | ban duration not allowed for the role / bad reason text |
| R-unban-scope | operator lifting a ban it did not make (or origin unknown) |
| R-audit-unavailable, R-internal-error | fail-closed denials |
| R-status, R-block, R-unblock, R-limit, R-unlimit, R-kick, R-ban, R-unban, R-restore, R-policy-admin | allow rules |

Legacy tool names from the assistant contract (`list_devices`, `block_device`,
`restore_device`, and arguments `device`, `mbit`, `minutes`) are still accepted.

## Tests

```bash
PYTHONPATH=.:.. python3 -m unittest discover tests/unit -p "test_policy_*.py" -v
python3 tests/run_all_tests.py      # whole suite
```

PyYAML is optional: without it, the policy files are read by `_yaml.py`.
`TestTeamContracts` checks that the policy agrees with the rest of the code:
- tool names match `assistant/contract.py` and `mcp_server/server.py`;
- hard limits match `mcp_server/schemas.py`;
- decisions match the assistant's `MockTransport`, so the evaluation numbers stay valid.
