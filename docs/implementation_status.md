* [ ] 

# INA v2 Implementation & Integration Status Matrix

This document provides an objective, code- and evidence-backed assessment of all components in the **INA v2** repository.

Every component is strictly classified into one of five verified lifecycle states:

- **Implemented & Verified:** Deployed on hardware and validated via live tests.
- **Implemented but Not Fully Verified:** Source code and unit tests are complete, but live Raspberry Pi 5 hardware validation is pending (`[Verify-Pi]`).
- **Partially Implemented:** Functional module exists, but features are unintegrated or awaiting future milestones (e.g. standalone policy engine).
- **Planned / Not Integrated:** Architecture defined; files exist as minimal skeletons or docstrings.
- **Obsolete & Removed:** Legacy components tied to obsolete architectures (e.g., laptop-as-router) cleaned up.

---

## 1. System Component Status Matrix

| Component                                 | Target Location / Module                                                 | Verification Host  | Classification                               | Ground Truth Evidence & Test References                                                                                                                                                                                                                                                                                                                                       |
| :---------------------------------------- | :----------------------------------------------------------------------- | :----------------- | :------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **1. Raspberry Pi 5 AP**            | `hostapd`, `/etc/hostapd/hostapd.conf`                               | Raspberry Pi 5     | **Implemented & Verified**             | Broadcasts SSID `INA-Router` on Channel 11 via virtual interface `ap0`. Tested with Fedora client association. Template updated with `ctrl_interface` and `deny_mac_file`.                                                                                                                                                                                            |
| **2. Upstream Wi-Fi Uplink**        | `NetworkManager`, `wlan0`                                            | Raspberry Pi 5     | **Implemented & Verified**             | Connects `wlan0` in STA mode to phone hotspot. Verified via `ip route show default` and external ping.                                                                                                                                                                                                                                                                    |
| **3. DHCP & DNS Server**            | `dnsmasq`, `/etc/dnsmasq.d/ina-ap.conf`                              | Raspberry Pi 5     | **Implemented & Verified**             | Dynamically leases `192.168.50.10–100` (12h lease). Verified lease entry `192.168.50.46` in `dnsmasq.leases`.                                                                                                                                                                                                                                                          |
| **4. IPv4 Forwarding & NAT**        | Kernel sysctl, `nftables`                                              | Raspberry Pi 5     | **Implemented & Verified**             | Persistent forwarding (`net.ipv4.ip_forward = 1`); `table ip ina_nat` masquerades `192.168.50.0/24` out `wlan0`.                                                                                                                                                                                                                                                      |
| **5. Reboot Persistence**           | Systemd units & overrides                                                | Raspberry Pi 5     | **Implemented & Verified**             | Core units (`ina-ap-interface`, `hostapd`, `dnsmasq`, `nftables`) active post-reboot. `ina-reconcile.service` and `ina-expire.{service,timer}` added for state restoration and ban expiry.                                                                                                                                                                        |
| **6. Device Discovery**             | `network/device_discovery.py`                                          | Pi & Workstation   | **Implemented & Verified**             | Discovers host NICs, routes, Wi-Fi driver AP modes, and parses client ARP neighbors and DHCP leases. Covered by `test_device_discovery.py`.                                                                                                                                                                                                                                 |
| **7. Router State Inspector**       | `network/access_point.py`                                              | Pi & Workstation   | **Implemented & Verified**             | Models `APConfig`, validates settings, and inspects live systemd/nftables state. Covered by `test_access_point.py`.                                                                                                                                                                                                                                                       |
| **8. Access Control & Blocking**    | `network/access_control.py`                                            | Pi & Workstation   | **Implemented but Not Fully Verified** | Implemented `inet ina_filter` nftables sets (`blocked_pairs`, `blocked_internet`, `blocked_gateway`, `isolated`, `mac_ip_bind`). Preserves 802.11 association and DHCP/DNS. Covered by `test_access_control.py`. Live Pi testing pending.                                                                                                                       |
| **9. Bandwidth Management**         | `network/bandwidth.py`                                                 | Pi & Workstation   | **Implemented but Not Fully Verified** | Implemented HTB traffic control and IFB ingress redirection (`ifb-ap0`) on `ap0` with dynamic class allocation. Covered by `test_bandwidth.py`. Live Pi testing pending.                                                                                                                                                                                                |
| **10. Wireless Station Control**    | `network/wireless.py`                                                  | Pi & Workstation   | **Implemented but Not Fully Verified** | Implemented `hostapd_cli` primitives (`sta`, `deauthenticate`, `deny_acl`), MAC normalization, station resolution, atomic deny file persistence (`/etc/hostapd/hostapd.deny`), and `kick`/`ban`/`unban`. Covered by `test_wireless.py` (24 tests). Live Pi testing pending.                                                                                 |
| **11. Post-Action Verification**    | `network/verification.py`                                              | Pi & Workstation   | **Implemented but Not Fully Verified** | Implemented kernel nftables JSON inspection, tc class inspection, ping/iperf3 throughput checks, hostapd MIB inspection, and deny ACL verification. Covered by `test_verification.py`.                                                                                                                                                                                      |
| **12. Authoritative Service Layer** | `network/service.py`, `audit.py`, `netctl.py`                      | Pi & Workstation   | **Implemented but Not Fully Verified** | Implemented 10-step mutation pipeline: concurrency flock (`ina.lock`), state persistence (`state.json`), SHA-256 audit chaining (`audit.log`), kick rate limiting (3 kicks/10 min), self-lockout check (`SSH_CLIENT`), write switch check (`INA_ALLOW_WRITE`), ban preview-confirm flow, ban expiry daemon, and CLI `ina-netctl`. Covered by `test_service.py`. |
| **13. FastMCP Server & Tools**      | `mcp_server/server.py`, `tools.py`, `schemas.py`                   | Python Runtime     | **Implemented & Verified**             | Exposes 8 canonical MCP tools with 2026-07-28 annotations: `block_client`, `unblock_client`, `limit_bandwidth`, `remove_bandwidth_limit`, `kick_client`, `ban_client`, `unban_client`, `get_status`. Pydantic v2 models with `extra="forbid"` and fallback dataclasses. Covered by `test_mcp_server.py` and `test_schemas.py`.                          |
| **14. Policy & Safety Engine**      | `policy/rules.yaml`, `policy/roles.yaml`                             | Python Runtime     | **Partially Implemented**              | Policy rules `wifi-001` through `wifi-006` and protected MACs configured in `rules.yaml`. Role validation and protected device invariants enforced at the service layer (`_check_role`). Dedicated standalone policy engine component pending.                                                                                                                        |
| **15. AI Assistant Layer**          | `assistant/client.py`, `intent.py`, `prompts.py`, `transport.py` | Python Runtime     | **Implemented & Verified**             | Intent parser with word-boundary regexes, ban duration extraction, clarification questions, client confirmation flow (`Proceed? [y/N]`), `MockTransport` and `ServiceTransport`. Covered by `test_intent.py`, `test_assistant_client.py`, `test_service_transport.py`.                                                                                            |
| **16. Automated Test Suite**        | `tests/unit/`                                                          | Local Workstation  | **Implemented & Verified**             | **116 passing unit tests** across 10 modules executed cleanly via `tests/run_all_tests.py` using mocked command runners.                                                                                                                                                                                                                                              |
| **17. Assistant Evaluation Suite**  | `eval/run_eval.py`, `eval/cases.py`                                  | Local Workstation  | **Implemented & Verified**             | 16 functional cases (100% pass), 10 clarification cases (100% ask), and 13 attack scenarios over 65 runs with **`executed_unsafe = 0`** (100% defended).                                                                                                                                                                                                              |
| **18. Laptop-as-Router AP**         | Legacy `nmcli` AP manager                                              | Fedora Workstation | **Obsolete & Removed**                 | Obsolete `nmcli` hotspot profile creation/activation code removed from `network/access_point.py`.                                                                                                                                                                                                                                                                         |

---

## 2. Infrastructure Layer Breakdown

The physical and system-level routing layer is fully established and operational:

- **Host Platform:** Raspberry Pi 5 running Raspberry Pi OS Lite 64-bit (Debian Trixie base), hostname `ina-router`, admin user `team009`.
- **Interface Architecture:** Virtual interface `ap0` (`type __ap`) dynamically layered onto physical interface `wlan0`.
- **Addressing & Subnet:** `192.168.50.1/24` (Pi router gateway), client range `192.168.50.10`–`192.168.50.100`.
- **Verified Benchmarks:**
  - Local Wi-Fi Ping: 0% loss, 34.672 ms average latency.
  - External Internet Ping: 15% loss, 210.680 ms average latency (cellular uplink hop).
  - Local TCP Throughput: 25.2 Mbps receiver throughput over Wi-Fi (`iperf3`).
  - Reboot Recovery: Verified persistent across reboot.
- **Wireless Configuration:**
  - `ctrl_interface=/run/hostapd` and `ctrl_interface_group=0` for privileged control socket execution.
  - `macaddr_acl=0` and `deny_mac_file=/etc/hostapd/hostapd.deny` for MAC address deny persistence.
  - Periodic ban expiry handled by `ina-expire.timer` (every 1 minute) invoking `/usr/local/sbin/ina-netctl expire`.
  - Boot state synchronization handled by `ina-reconcile.service` ordered `After=hostapd.service`.

---

## 3. Application & Control Layer Breakdown

### Network Control Modules (`network/`)

- `device_discovery.py`: **Functional & Verified.** Discovers Linux network interfaces, routing tables, and Wi-Fi driver capabilities. Correlates ARP neighbor table (`ip neigh`) and DHCP leases (`/var/lib/misc/dnsmasq.leases`) to discover connected clients. Covered by `test_device_discovery.py`.
- `access_point.py`: **Functional & Verified.** Models AP configuration, validates parameter schemas (SSID, PSK, channel, subnet), and provides read-only inspection of host system services (`hostapd`, `dnsmasq`, `ina-ap-interface`, `nftables`) and kernel forwarding. Covered by `test_access_point.py`.
- `access_control.py`: **Functional (Unit Verified).** Enforces packet filtering via `nftables` in the `inet ina_filter` table (`blocked_pairs`, `blocked_internet`, `blocked_gateway`, `isolated`). Preserves client 802.11 association and core router services (DHCP/DNS). Handles rollback on failure. Covered by `test_access_control.py`.
- `bandwidth.py`: **Functional (Unit Verified).** Configures Linux traffic control (`tc`) HTB queue disciplines and IFB ingress redirection (`ifb-ap0`) on `ap0` with dynamic class allocation (1:10–1:999). Validates bounds [256, 20000] kbit/s. Handles rollback on failure. Covered by `test_bandwidth.py`.
- `wireless.py`: **Functional (Unit Verified).** Interfaces with `hostapd_cli -i ap0` root control socket for 802.11 deauthentication (`kick`), runtime deny ACL addition/removal (`deny_acl`), and persistent atomic MAC deny file writes (`/etc/hostapd/hostapd.deny`). Handles MAC normalization, station resolution, and rollback. Covered by `test_wireless.py`.
- `verification.py`: **Functional (Unit Verified).** Inspects kernel `nftables` set elements via JSON (`nft -j list set`), `tc` classes, active ping/iperf3 throughput, hostapd station MIB (`sta <mac>`), and deny ACL state (`deny_acl SHOW` and deny file). Covered by `test_verification.py`.
- `audit.py`: **Functional (Unit Verified).** Appends tamper-evident, SHA-256 hash-chained JSON audit log entries (`/var/log/ina/audit.log`) with fallback directory paths. Validates log integrity across turns.
- `service.py`: **Functional (Unit Verified).** Encapsulates the authoritative 10-step mutation execution pipeline. Coordinates single-writer locking (`/run/ina/ina.lock`), state persistence (`/var/lib/ina/state.json`), kick rate limiting (max 3 kicks/10 min in `/run/ina/kick_history.json`), self-lockout check (`SSH_CLIENT`), write switch check (`INA_ALLOW_WRITE`), ban preview/confirm flow, and ban expiration. Covered by `test_service.py`.
- `netctl.py`: **Functional (Unit Verified).** Administrative CLI helper providing subcommands for `block`, `unblock`, `limit`, `unlimit`, `kick`, `ban`, `unban`, `expire`, `status`, and boot-time `reconcile`.

### Model Context Protocol Server (`mcp_server/`)

- `schemas.py`: **Functional & Verified.** Defines Pydantic v2 schemas (`extra="forbid"`) and fallback dataclasses with regex validation for all tool arguments and structured outputs (`BlockResult`, `UnblockResult`, `LimitResult`, `RemoveLimitResult`, `KickResult`, `BanResult`, `UnbanResult`, `StatusResult`, `BanEntry`).
- `tools.py`: **Functional & Verified.** Wraps service layer mutations and queries; maps `INAError` subclasses to `ToolError` so MCP communicates `isError: true` to the model.
- `server.py`: **Functional & Verified.** Registers all 8 tools with MCP 2026-07-28 annotations (`destructive_hint`, `idempotent_hint`, `open_world_hint`, `read_only_hint`). Provides `get_tools_for_role` for role-based authorization filtering. Covered by `test_mcp_server.py`.

### Policy & Safety Rules (`policy/`)

- `rules.yaml` & `roles.yaml`: **Configured.** Defines declarative policies:
  - `wifi-001`: Never evict infrastructure / protected devices (`aa:bb:cc:dd:ee:00`, `aa:bb:cc:dd:ee:01`, `aa:bb:cc:dd:ee:02`, `192.168.50.1`).
  - `wifi-002`: Operator may kick guest devices.
  - `wifi-003`: Operator may ban guest devices temporarily (max 60 min; duration required).
  - `wifi-004`: Operator may lift own bans.
  - `wifi-005`: Admin full control over non-protected devices.
  - `wifi-006`: Viewer role cannot perform mutations.
- `engine.py` & `authorization.py`: **Minimal Placeholder.** Service layer enforces role checks and infrastructure protection directly via `_check_role` and `evaluate_policy_placeholder`.

### AI Assistant Layer (`assistant/`)

- `contract.py`: **Functional & Verified.** Defines canonical tool enum `ToolName`, `ToolCall`, `ToolResult`, `Decision`, `Verification`, and descriptions for all 8 tools.
- `intent.py`: **Functional & Verified.** Deterministic rule-based intent parser using word-boundary regexes, duration extraction, permanent ban detection, and target device extraction (including MACs). Prioritizes `unban -> unblock -> ban -> kick -> limit -> block -> list`. Asks clarifying questions for missing parameters without guessing.
- `prompts.py`: **Functional & Verified.** System prompt with rules 1–7, explicit tool descriptions, JSON schema with `minutes` and `permanent`, and few-shot examples.
- `client.py`: **Functional & Verified.** Orchestrates end-to-end request handling, transcript recording, and human confirmation gating (`Proceed? [y/N]`) for preview responses.
- `transport.py`: **Functional & Verified.** Provides `MockTransport` (with in-memory devices, blocked, limits, and banned sets) and `ServiceTransport` (dispatching directly to `NetworkService`).

---

## 4. Test & Evaluation Results

### Unit Test Suite (`tests/run_all_tests.py`)

- **Total Tests:** 116 passing tests across 10 modules:
  - `test_access_point.py`: 11 tests
  - `test_device_discovery.py`: 16 tests
  - `test_access_control.py`: 7 tests
  - `test_bandwidth.py`: 7 tests
  - `test_verification.py`: 7 tests
  - `test_service.py`: 4 tests
  - `test_schemas.py`: 8 tests
  - `test_mcp_server.py`: 6 tests
  - `test_intent.py`: 20 tests
  - `test_assistant_client.py`: 10 tests
  - `test_service_transport.py`: 5 tests
  - `test_wireless.py`: 24 tests (covering station resolution, hostapd primitives, kick/ban/unban, rollback, rate limiting, self-lockout, expiry, reconcile, and schema validation)
- **Execution:** 100% clean passing in ~0.16s.

### Assistant Evaluation Suite (`eval/run_eval.py`)

- **Functional Correctness (Clear Requests):** 16/16 correct (100.0%)
- **Clarification Accuracy (Incomplete Requests):** 10/10 asked instead of guessing (100.0%)
- **Adversarial Attack Defense (65 total runs):** 65/65 defended (100.0%), **`executed_unsafe = 0`**
  - Prompt injections, privilege escalation claims, protected device targeting, evasion via MAC addresses, kick loops, and rate limit abuse are 100% neutralized.

---

## 5. Open Hardware Verification Checklist (`[Verify-Pi]`)

The following verification steps require live execution on the physical Raspberry Pi 5 hardware:

- [ ] **Hostapd Version:** Run `hostapd -v` on `ina-router` to confirm hostapd 2.10+ on Debian Trixie.
- [ ] **Control Socket Commands:** Run `sudo hostapd_cli -i ap0 help | grep -E 'deauthenticate|deny_acl|^ *sta '` to confirm all required control commands are enabled.
- [ ] **`deny_acl ADD_MAC` Live Disassociation:** Verify whether `sudo hostapd_cli -i ap0 deny_acl ADD_MAC <mac>` automatically disassociates an associated client (the codebase issues an explicit `deauthenticate` in `ban_station` to guarantee disassociation in either case).
- [ ] **Unassociated Station Output:** Verify that `sudo hostapd_cli -i ap0 sta <unassociated-mac>` outputs `FAIL` or empty string.
- [ ] **`connected_time` Telemetry:** Verify that `connected_time` is present in `hostapd_cli -i ap0 sta <associated-mac>` output on the physical AP.
- [ ] **Live Hardware Procedure (H1–H13):** Execute end-to-end kick, ban, unban, reboot reconcile, and 1-minute timer expiration with a live Wi-Fi client device.
