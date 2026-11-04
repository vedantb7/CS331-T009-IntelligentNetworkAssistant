# Intelligent Network Configuration Assistant v2 (INA v2)

**INA v2** is a natural-language network assistant for a small Wi-Fi network, built as a Computer Networks engineering project. An operator types a request such as *"limit guest-phone to 5 Mbps"*. An LLM turns it into a Model Context Protocol (MCP) tool call, and a Raspberry Pi 5 router applies it with `nftables`, `tc` or `hostapd`. The Pi checks the call against a policy, verifies the change, logs it and reports back. The LLM then explains the result in plain English.

---

## 1. System at a glance

```text
Phone hotspot ("1+ Four") ──Wi-Fi uplink── Raspberry Pi 5 "ina-router" ──Wi-Fi AP "INA-Router"── client devices
                                           wlan0 uplink, ap0 AP 192.168.50.1/24      DHCP 192.168.50.10–100
                                           NAT: nftables masquerade                  fixed IPs .2–.9 (registered)

Operator laptop                                       Raspberry Pi 5 (ina-router)
───────────────                                       ─────────────────────────────────────────────────────────
request (NL) → LLM picks MCP tool ─HTTPS /mcp, Bearer→ ina-mcp.service (user ina): schema check → policy guard
                                                        (token → person, role, bound device) → allowed only:
                                                        sudo -n ina-netctl ──► root: nft / tc / hostapd_cli
                                                                               verify → rollback → state → logs
reply (NL) ← LLM ← structured result ◄─────────────── structured result (no commands) + request ID
```

- **Devices:** every device on the Wi-Fi is a normal **client**. Only devices the admin lists in `policy/devices.yaml` as `protected` (the router, the admins' laptops) can never be changed.
- **Roles:** nobody has a role by default. The admin grants one by hand, to a person, bound to that person's registered device: `sudo ina-netctl roles grant dhruv admin --device admin-laptop`.
- **Privileges:** the MCP server runs unprivileged. Every privileged action goes through the single sudo-allowed command `ina-netctl`.
- **Logs:** every decision, command (with its output) and result is logged and hash-chained, and the three logs are tied together by one request ID.

Details: [docs/architecture.md](docs/architecture.md).

---

## 2. Repository layout

```text
assistant/    operator CLI: LLM backends (any provider), MCP client (Streamable HTTP / stdio),
              rule-based fallback parser, structured result view for the LLM
mcp_server/   MCP server (official Python SDK): 8 tools, typed schemas, bearer-token auth
policy/       roles.yaml, rules.yaml, devices.yaml, policy engine, role grants, policy audit log
network/      service layer (validate → lock → apply → verify → rollback → state → audit),
              nftables / tc / hostapd control, ina-netctl, privilege-boundary client, command log
config/       hostapd, dnsmasq, nftables, systemd units, sudoers, tmpfiles, scripts (installed on the Pi)
eval/         evaluation harness (functional, clarification, attack cases)
tests/        unit tests (mocked kernel) + tests/lab (real-kernel namespace lab, no root)
docs/         architecture, setup, tools, network, privilege boundary, testing, troubleshooting
```

---

## 3. Quick start (laptop, no Pi needed)

```bash
pip install -r requirements.txt            # MCP SDK (the core modules and tests are stdlib-only)
python3 tests/run_all_tests.py             # unit tests (mocked kernel, no root)
python3 -m eval.run_eval                   # evaluation summary -> eval/results/*.csv

python3 -m assistant "limit guest-phone to 5 mbps"     # against the built-in mock network
python3 -m assistant                                    # interactive
```

**Use an LLM (any provider).** Copy `.env.example` to `.env` and set:

```bash
LLM_PROVIDER=anthropic   LLM_API_KEY=sk-ant-...                   # pip install anthropic
LLM_PROVIDER=openrouter  LLM_API_KEY=sk-or-...  LLM_MODEL=<provider/model>
LLM_PROVIDER=gemini      LLM_API_KEY=<AI Studio key>  LLM_MODEL=<gemini model>
LLM_PROVIDER=ollama      LLM_MODEL=<local model>                  # no key
```

Without an LLM (or if a call fails), the deterministic rule-based parser and reply templates are used.

**Talk to the real Pi:**

```bash
# .env: INA_MCP_URL=https://192.168.50.1:8443/mcp  INA_MCP_CA=/home/<you>/.config/ina/ina-router.crt (absolute)  INA_ROLE_TOKEN=<your token>
python3 -m assistant --server mcp "list devices"
```

**Real-kernel lab (no root, private network namespaces):** `INA_RUN_LAB=1 python3 -m unittest inav2.tests.unit.test_router_config`, run from the directory that contains `inav2/`.

---

## 4. Deploying on the Raspberry Pi

Refer to the consolidated setup and deployment guide [docs/setup_and_deployment.md](docs/setup_and_deployment.md). It contains:

- Quick-start command runbooks for both the Raspberry Pi 5 and the Operator Laptop.
- In-depth infrastructure reference for the virtual AP interface (`ap0`), `hostapd`, `dnsmasq`, `nftables`, systemd units, role grants, and TLS setup.

Router prerequisites: Raspberry Pi 5, Raspberry Pi OS Lite 64-bit (Debian Trixie), `hostapd dnsmasq nftables iw iperf3 ethtool python3-venv`.

---

## 5. Documentation

The technical documentation inside `docs/` is organized into 6 consolidated primary references:

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Topology, packet flow, systemd dependencies, and 14-step request lifecycle |
| [docs/setup_and_deployment.md](docs/setup_and_deployment.md) | Complete Raspberry Pi and laptop runbooks + infrastructure reference |
| [docs/implementation.md](docs/implementation.md) | Assistant, MCP server, 8 canonical tools, network modules, and policy engine |
| [docs/security.md](docs/security.md) | Privilege boundary (`ina-netctl`), RBAC, anti-spoofing, and hash-chained audit logging |
| [docs/testing_and_evaluation.md](docs/testing_and_evaluation.md) | Hardware benchmark records, namespace lab, 339 unit tests, and eval benchmark |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Diagnostics and triage procedures for 25 concrete failure cases |

---

## 6. Status

The router infrastructure (AP, DHCP, NAT, reboot persistence) is **verified on the Pi**, and the full workflow has **run end to end on the hardware**: natural-language requests on the operator laptop, through an LLM (Gemini) or the rule-based parser, over HTTPS to the Pi's MCP server, policy guard and `ina-netctl`, with real phones as clients.

- **Unit tests:** 339 (mocked kernel).
- **Real-kernel namespace lab:** blocks are enforced.
- **On the Pi:** blocks, limits, kicks, bans and role grants applied and read back; everything comes back after a reboot.

See [docs/testing_and_evaluation.md](docs/testing_and_evaluation.md) for the exact state and test logs.
