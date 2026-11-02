# INA v2 Implementation & Verification Status

Every component is classified by the strongest evidence that exists for it:

- **Verified on hardware:** run and checked on the Raspberry Pi 5 with real clients.
- **Run on hardware:** run end to end on the Pi (laptop → HTTPS → Pi → kernel) and read back from the kernel or hostapd; the effect on client traffic was not measured independently.
- **Verified in lab:** run against the real Linux kernel in private network namespaces (`tests/lab/`), or end to end over real HTTPS on a laptop.
- **Unit-tested:** complete code, tested with a mocked kernel (`tests/unit/`, 339 tests).
- **Open:** missing, partial, or a known gap.

---

## 1. Router infrastructure (Raspberry Pi 5)

| Component | Where | Status | Evidence |
|---|---|---|---|
| Wi-Fi AP `INA-Router` on `ap0` | hostapd | Verified on hardware | Client association; see [testing.md](testing.md) Test A |
| Uplink `wlan0` to phone hotspot | NetworkManager | Verified on hardware | Default route and external ping (Test D) |
| DHCP/DNS (`192.168.50.10–100`) | dnsmasq | Verified on hardware | Lease records (Test B) |
| IPv4 forwarding and NAT | sysctl, `table ip ina_nat` | Verified on hardware | External ping, `iperf3` 25.2 Mbit/s (Tests C–E) |
| Reboot persistence | systemd units and drop-ins, `config/NetworkManager/90-ina-unmanaged.conf` | Verified on hardware | All units, the MCP server, the role grant, the DHCP reservations and the anti-spoofing binding back after reboot (Test A; 2026-11-02). Needs NetworkManager to leave `ap0` unmanaged (troubleshooting Case 22) |
| Client isolation + routed hairpin (`ap_isolate`, `proxy_arp_pvlan`) | `config/hostapd/`, `config/scripts/ina-ap-interface.sh` | Verified in lab; settings active on the Pi | hostapd accepts `ap_isolate=1`, `proxy_arp_pvlan=1` set at boot; phone-to-phone traffic through the Pi not yet measured |

---

## 2. Application layer

| Component | Where | Status | Notes |
|---|---|---|---|
| Device / client discovery | `network/device_discovery.py` | Unit-tested, run on hardware | Interfaces, routes, ARP, leases |
| Router state inspector | `network/access_point.py` | Unit-tested, run on hardware | |
| INA filter table (sets + chains) | `config/nftables/ina-filter.nft`, `network/access_control.py` | Verified in lab | INA installs the table itself when chains are missing; blocks proven to drop traffic |
| `block_client` / `unblock_client` | `network/access_control.py` | Verified in lab; run on hardware | Internet, all and pair blocks applied and read back on the Pi from NL requests |
| `limit_bandwidth` / `remove_bandwidth_limit` | `network/bandwidth.py` (HTB + IFB) | Run on hardware | tc classes applied and read back on the Pi; throughput not yet measured with `iperf3` |
| `kick_client`, `ban_client`, `unban_client` | `network/wireless.py` (hostapd) | Verified on hardware | hostapd log: kick disassociated the phone, which rejoined in 38 ms; a banned phone's reassociations were refused; unban lifted it (Android may then need a manual re-join) |
| Service pipeline (validate, lock, verify, rollback, state, audit) | `network/service.py` | Unit-tested | |
| Privilege boundary `ina-netctl` | `network/netctl.py`, `network/privileged.py`, `config/scripts/ina-netctl`, `config/sudoers.d/ina` | Verified on hardware | Installed under real sudo; `ina` may run only `ina-netctl` (direct `nft` refused) |
| Command log + request-ID correlation | `network/command_log.py` | Unit-tested | Hash-chained |
| Device registry → DHCP reservations, anti-spoofing | `network/devices.py` (`ina-netctl sync-devices`) | Verified on hardware | Reserved names and the fixed IP `.2` appear in the leases; the MAC/IP binding is restored after reboot |
| MCP server (8 tools, typed schemas) | `mcp_server/` (MCP Python SDK 2.3.0) | Verified on hardware | Laptop → `https://192.168.50.1:8443/mcp` round trips; unregistered devices resolved from their DHCP lease |
| Streamable HTTP transport, TLS, bearer auth, Host/Origin checks | `mcp_server/server.py`, `auth.py` | Verified in lab; auth on hardware | 401 without or with a wrong token on the Pi; untrusted certificate rejected; 403 / 421 tested in lab |
| Policy engine (roles, rules, devices) | `policy/engine.py`, `*.yaml` | Unit-tested | Classes: `client` (default) and `protected` |
| Per-person, device-bound role grants | `policy/grants.py`, `authorization.py`, `guard.py` | Verified on hardware | Admin grant bound to `admin-laptop`, used from its fixed IP |
| Assistant: any-LLM tool calling, structured replies | `assistant/` | Run against a live LLM | Gemini (OpenAI-compatible endpoint) against the Pi; Claude adapter unit-tested only. Guards refuse tools the request did not ask for, keep the operator's speeds, and ask before an open-ended ban ([assistant.md](assistant.md)) |
| Rule-based fallback parser and reply templates | `assistant/intent.py`, `client.py` | Unit-tested; run on hardware | Used by the evaluation and whenever the LLM is unavailable; cannot choose block targets other than `all` |
| Laptop Wi-Fi recovery after a Pi reboot | `assistant/client.py`, `config/laptop/ina-wifi-prefer.*` | Run on the laptop | The assistant re-joins `INA-Router` once; the user timer must be installed per laptop ([laptop_setup.md](laptop_setup.md) §5.5) |
| Evaluation harness | `eval/` | Run | 16/16 functional; 0/65 attack runs executed |

---

## 3. Open items and known gaps

1. **Record the hardware runs.** The end-to-end runs above are not yet written up as numbered tests in [testing.md](testing.md); measure bandwidth limits with `iperf3` and phone-to-phone blocks with ping at the same time.
2. **`get_status` drift.** Clients now come from the dnsmasq lease file, hostapd's station list and the `ap0` neighbor table (verified on the Pi), but `drift_detected` is still always `false`: it does not yet compare `state.json` with the nft sets and tc classes.
3. **Service-account trust.** Whoever controls the `ina` account can run `sudo ina-netctl --role admin …` and bypass the MCP policy guard. Protected-device and range checks still apply, and role grants cannot be changed from that account. Moving the token check into `ina-netctl` would close this.
4. **Free LLM tiers.** Gemini's free quota runs out per model (HTTP 429) and the endpoint can be overloaded (503) or slow; the assistant retries twice, then uses the rule-based parser. See [assistant.md](assistant.md).
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
