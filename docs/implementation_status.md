# INA v2 Implementation & Verification Status

Every component is classified by the strongest evidence that exists for it:

- **Verified on hardware:** run and checked on the Raspberry Pi 5 with real clients.
- **Verified in lab:** run against the real Linux kernel in private network namespaces (`tests/lab/`), or end to end over real HTTPS on a laptop.
- **Unit-tested:** complete code, tested with a mocked kernel (`tests/unit/`, 332 tests).
- **Open:** missing, partial, or a known gap.

---

## 1. Router infrastructure (Raspberry Pi 5)

| Component | Where | Status | Evidence |
|---|---|---|---|
| Wi-Fi AP `INA-Router` on `ap0` | hostapd | Verified on hardware | Client association; see [testing.md](testing.md) Test A |
| Uplink `wlan0` to phone hotspot | NetworkManager | Verified on hardware | Default route and external ping (Test D) |
| DHCP/DNS (`192.168.50.10–100`) | dnsmasq | Verified on hardware | Lease records (Test B) |
| IPv4 forwarding and NAT | sysctl, `table ip ina_nat` | Verified on hardware | External ping, `iperf3` 25.2 Mbit/s (Tests C–E) |
| Reboot persistence | systemd units and drop-ins | Verified on hardware | All units active after reboot (Test A) |
| Client isolation + routed hairpin (`ap_isolate`, `proxy_arp_pvlan`) | `config/hostapd/`, `config/scripts/ina-ap-interface.sh` | Verified in lab | Bridge port isolation emulation in `tests/lab/ina_block_lab.sh`; not yet re-checked on the Pi's radio |

---

## 2. Application layer

| Component | Where | Status | Notes |
|---|---|---|---|
| Device / client discovery | `network/device_discovery.py` | Unit-tested, run on hardware | Interfaces, routes, ARP, leases |
| Router state inspector | `network/access_point.py` | Unit-tested, run on hardware | |
| INA filter table (sets + chains) | `config/nftables/ina-filter.nft`, `network/access_control.py` | Verified in lab | INA installs the table itself when chains are missing; blocks proven to drop traffic |
| `block_client` / `unblock_client` | `network/access_control.py` | Verified in lab | Pair, internet, gateway, isolate |
| `limit_bandwidth` / `remove_bandwidth_limit` | `network/bandwidth.py` (HTB + IFB) | Unit-tested | Not yet measured with `iperf3` on the Pi |
| `kick_client`, `ban_client`, `unban_client` | `network/wireless.py` (hostapd) | Unit-tested | hostapd `deny_acl` behaviour to confirm on the Pi |
| Service pipeline (validate, lock, verify, rollback, state, audit) | `network/service.py` | Unit-tested | |
| Privilege boundary `ina-netctl` | `network/netctl.py`, `network/privileged.py`, `config/scripts/ina-netctl`, `config/sudoers.d/ina` | Unit-tested + local run | Not yet installed under real sudo |
| Command log + request-ID correlation | `network/command_log.py` | Unit-tested | Hash-chained |
| Device registry → DHCP reservations, anti-spoofing | `network/devices.py` (`ina-netctl sync-devices`) | Verified in lab (nft sets); `dnsmasq --test` | Reserved name in leases to confirm on the Pi |
| MCP server (8 tools, typed schemas) | `mcp_server/` (MCP Python SDK 2.3.0) | Verified in lab | Live HTTPS round trips |
| Streamable HTTP transport, TLS, bearer auth, Host/Origin checks | `mcp_server/server.py`, `auth.py` | Verified in lab | 401 / 403 / 421 behaviours tested |
| Policy engine (roles, rules, devices) | `policy/engine.py`, `*.yaml` | Unit-tested | Classes: `client` (default) and `protected` |
| Per-person, device-bound role grants | `policy/grants.py`, `authorization.py`, `guard.py` | Unit-tested | `ina-netctl roles grant/list/rotate/revoke` |
| Assistant: any-LLM tool calling, structured replies | `assistant/` | Unit-tested | Claude (official SDK) and OpenAI-compatible providers; not run against a live LLM API yet |
| Rule-based fallback parser and reply templates | `assistant/intent.py`, `client.py` | Unit-tested | Used by the evaluation |
| Evaluation harness | `eval/` | Run | 16/16 functional; 0/65 attack runs executed |

---

## 3. Open items and known gaps

1. **End to end on hardware.** The full workflow (laptop → HTTPS → Pi → kernel) has not run on the Pi. Use the checks in [setup.md](setup.md) §15.7 and record the results in [testing.md](testing.md).
2. **`get_status` on the Pi.** It builds its client list from an injected lease map, so on the Pi it currently returns no clients. It also reports every listed client as `associated` and `drift_detected` is always `false`. It needs to read the lease file, the neighbor table and the kernel state.
3. **Service-account trust.** Whoever controls the `ina` account can run `sudo ina-netctl --role admin …` and bypass the MCP policy guard. Protected-device and range checks still apply, and role grants cannot be changed from that account. Moving the token check into `ina-netctl` would close this.
4. **Behaviours to confirm on the Pi:**
   - hostapd `deny_acl ADD_MAC` disconnecting a station, and `sta` output fields;
   - dnsmasq writing reserved names into the lease file;
   - HTB/IFB on the Pi's Wi-Fi driver.
5. **Per-role `tools/list`.** The policy rejects calls outside a role, but every role still sees all eight tools listed.
6. **Defence in depth.** The service layer still runs its own placeholder check after the policy guard. It is redundant, not wrong.
7. **Data quotas.** Not implemented (researched only).

---

## 4. Removed components

1. **Laptop-as-router (`nmcli` hotspot)** helpers were removed from `network/access_point.py`.
2. **Laptop interface names and subnets** (`wlp8s0`, `192.168.10.1/24`) were removed from config and tests.
3. **`flush ruleset`** is never used; INA only manages its own named tables.
4. **Device classes `staff`, `guest`, `unknown`** were replaced by `client` (default) and `protected`.
5. **Shared role tokens as the primary mechanism** were replaced by per-person, device-bound grants. The `INA_*_TOKEN_SHA256` hashes are still accepted for compatibility.
