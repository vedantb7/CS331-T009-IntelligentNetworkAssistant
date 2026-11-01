# Privilege Boundary: Everything Runs Through `ina-netctl`

Workflow step 4 ("the command is executed") happens in exactly one place: `/usr/local/sbin/ina-netctl`, run as root through sudo. The MCP server runs as the unprivileged `ina` user. It never runs `nft`, `tc` or `hostapd_cli` itself, and never opens the state, lock or audit files.

```text
operator laptop                         ina-router (Raspberry Pi 5)
───────────────                         ────────────────────────────────────────────────────────────
assistant ──ssh (stdio MCP)──► ina-mcp  [user ina]
                               │  tools/call → inputSchema check (SDK) → PolicyGuard (token → role)
                               │  allowed only:
                               └─► sudo -n /usr/local/sbin/ina-netctl --json --role <role> <cmd> -- <args>
                                        [root]  network.service: validate → lock → nft/tc/hostapd_cli
                                                → verify → rollback → /var/lib/ina/state.json
                                                → /var/log/ina/audit.log
                                        stdout: {"ok": true, "result": {...}}
                                             or {"ok": false, "error": "<ErrorClass>", "message": "..."}
```

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

## Install on the Pi

```bash
# users and code (root-owned)
sudo useradd --system --create-home --shell /bin/sh ina   # sshd runs the forced command via the login shell, so not nologin
sudo install -d -o root -g root -m 0755 /opt/ina
sudo cp -r inav2 /opt/ina/ && sudo chown -R root:root /opt/ina/inav2
sudo python3 -m venv /opt/ina/venv && sudo /opt/ina/venv/bin/pip install -r /opt/ina/inav2/requirements.txt

# boundary
sudo install -o root -g root -m 0755 /opt/ina/inav2/config/scripts/ina-netctl /usr/local/sbin/ina-netctl
sudo visudo -cf /opt/ina/inav2/config/sudoers.d/ina && \
  sudo install -o root -g root -m 0440 /opt/ina/inav2/config/sudoers.d/ina /etc/sudoers.d/ina
sudo install -m 0644 /opt/ina/inav2/config/tmpfiles.d/ina.conf /etc/tmpfiles.d/ina.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/ina.conf

# MCP server
sudo install -o root -g root -m 0755 /opt/ina/inav2/config/scripts/ina-mcp /usr/local/bin/ina-mcp
sudo install -d -o root -g ina -m 0750 /etc/ina
#   /etc/ina/mcp.env (root:ina 0640): INA_ADMIN_TOKEN_SHA256=… INA_OPERATOR_TOKEN_SHA256=… INA_VIEWER_TOKEN_SHA256=…
#   ~ina/.ssh/authorized_keys, one line per operator key:
#   restrict,command="INA_ROLE_TOKEN=<operator-token> /usr/local/bin/ina-mcp" ssh-ed25519 AAAA… operator-laptop
```

On the operator laptop:

```bash
python -m assistant --server mcp --mcp-command "ssh ina@ina-router" "list devices"
```

## Verify

```bash
sudo -u ina sudo -n /usr/local/sbin/ina-netctl --json --role viewer status --   # {"ok": true, ...}
sudo -u ina sudo -n /usr/sbin/nft list ruleset                                  # must FAIL (not allowed)
sudo -u ina nft list ruleset                                                    # must FAIL (no privilege)
ls -ld /opt/ina /opt/ina/venv /usr/local/sbin/ina-netctl                        # root-owned, not group/world-writable
```

## Local development (no Pi, no sudo)

The server picks its executor from `INA_EXECUTOR`. By default it uses `netctl` when not running as root, and `direct` when it is.

```bash
# run the real boundary interface without sudo (commands that need root will report "Failed")
export INA_NETCTL_COMMAND="python3 -m inav2.network.netctl" INA_DEMO_TOKENS=1
python -m assistant --server mcp "list devices"
```

The tests (`tests/unit/test_privileged.py`) join both halves in-process: MCP tool → guard → `NetctlService` → `ina-netctl` `main()` → service → mock kernel.
