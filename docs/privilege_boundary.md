# Privilege Boundary: Everything Runs Through `ina-netctl`

Workflow step 4 ("the command is executed") happens in exactly one place: `/usr/local/sbin/ina-netctl`, run as root through sudo. The MCP server runs as the unprivileged `ina` user. It never runs `nft`, `tc` or `hostapd_cli` itself, and never opens the state, lock or audit files.

```text
operator laptop                         ina-router (Raspberry Pi 5)
───────────────                         ────────────────────────────────────────────────────────────
assistant ──ssh (stdio MCP)──► ina-mcp  [user ina]
                               │  tools/call → inputSchema check (SDK) → PolicyGuard (token → role)
                               │  allowed only:
                               └─► sudo -n /usr/local/sbin/ina-netctl --json --request-id <id> --role <role> <cmd> -- <args>
                                        [root]  network.service: validate → lock → nft/tc/hostapd_cli
                                                → verify → rollback → /var/lib/ina/state.json
                                                → /var/log/ina/audit.log
                                                each command, as it finishes → /var/log/ina/commands.jsonl
                                        stdout: {"ok": true,  "request_id": …, "result": {...}, "commands": [...]}
                                             or {"ok": false, "request_id": …, "error": "<ErrorClass>", "message": "...", "commands": [...]}
```

## Command output and logs (workflow step 5)

The output of every command goes back to the MCP server and is logged at the same time:

1. Inside `ina-netctl`, the service's runner is wrapped by `RecordingRunner` (`network/command_log.py`). When a command finishes, its record is written straight away to `/var/log/ina/commands.jsonl`, before the next command runs. The record holds argv, exit code, stdout and stderr (each capped at 4000 characters, with a `truncated` flag), duration and the request ID. The log is append-only, fsynced and hash-chained (`verify_command_log()`), so the trail survives a crash in the middle of an operation.
2. The same records are added to the request's trace and returned in the `commands` field of the envelope. This happens **on failure as well**, so the MCP server sees exactly which command failed and what it printed.
3. `NetctlService` adds the returned records to the MCP server's trace. `mcp_server/tools.py` then writes the policy-audit `outcome` with a `commands` summary (seq, argv, exit code). The full output stays in the root-side command log and is linked by `seq`.
4. One request ID, assigned by the policy guard, ties together the policy decision and outcome (`policy_audit.jsonl`), every command record (`commands.jsonl`), and the service audit entry (`audit.log`, `details.request_id`).

If the command log can't be written, the command has already run. Its result is still returned, with a `log_error` field, and a warning goes to stderr. A logging failure never hides what happened.

| Piece | File in repo | Installed as |
|---|---|---|
| Boundary client (unprivileged side) | `network/privileged.py` (`NetctlService`) | used by `mcp_server/tools.py` |
| Boundary command (root side) | `network/netctl.py`, wrapper `config/scripts/ina-netctl` | `/usr/local/sbin/ina-netctl` root:root 0755 |
| sudo rule | `config/sudoers.d/ina` | `/etc/sudoers.d/ina` 0440 |
| MCP launcher | `config/scripts/ina-mcp` | `/usr/local/bin/ina-mcp` root:root 0755 |
| Runtime directories | `config/tmpfiles.d/ina.conf` | `/etc/tmpfiles.d/ina.conf` |

## Rules that keep the boundary meaningful

1. **The `ina` user cannot change what runs as root.** `/opt/ina` (the code) and `/opt/ina/venv` are owned by root and not writable by `ina`. The wrapper runs Python with `-I`, so `PYTHONPATH`, user site-packages and the current directory are ignored. `sudo` resets the environment as well.
2. **One command, typed subcommands, no shell.** `NetctlService` builds an argument list and runs it with `shell=False`. `--` comes before the client and target values, so a value like `-rf` can never become an option. `ina-netctl` validates everything again through the service-layer schemas.
3. **Policy is decided before the boundary is crossed.** A call the MCP policy guard denies never reaches `ina-netctl`. The `--role` passed is the role the guard resolved from the caller's token, never one the AI chose. Inside the boundary, the service layer still applies its own protected-device and range checks.
4. **No fallback.** If `sudo -n` fails (rule missing, password required), the tool returns `Failed: ina-netctl did not return a result (…)`. It never runs the command in-process instead.

Known limit: anyone who controls the `ina` account can run `sudo ina-netctl --role admin …` directly and so skip the MCP policy guard. They are still held by the service layer's checks (protected devices, subnet, rate and ban bounds). Moving the token check inside `ina-netctl` would close this. It needs the token hashes in a root-owned file such as `/etc/ina/tokens.env`, and is planned as a follow-up.

## Install and verify on the Pi

The step-by-step procedure is in [setup.md §15](setup.md#15-installing-the-ina-application): code and venv, the `ina` account, the `ina-netctl` wrapper, sudoers, tmpfiles, systemd units, device registration, role tokens and the SSH forced command. It ends with the checks that prove the boundary holds: `sudo -u ina nft …` must fail and `sudo -u ina sudo -n ina-netctl …` must work.

## Local development (no Pi, no sudo)

The server picks its executor from `INA_EXECUTOR`. By default it uses `netctl` when not running as root, and `direct` when it is.

```bash
# run the real boundary interface without sudo (commands that need root will report "Failed")
export INA_NETCTL_COMMAND="python3 -m inav2.network.netctl" INA_DEMO_TOKENS=1
python -m assistant --server mcp "list devices"
```

The tests (`tests/unit/test_privileged.py`) join both halves in-process: MCP tool → guard → `NetctlService` → `ina-netctl` `main()` → service → mock kernel.
