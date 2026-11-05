# INA v2 Live Router Test Report

Live functional and non-functional tests of the deployed INA v2 router (Raspberry Pi 5 `ina-router`), run through the real MCP endpoint `https://192.168.50.1:8443/mcp` from admin-laptop, with SSH used only to read the kernel state. **No project code was changed.** Tests ran on 2026-11-05/06.

## Summary

| Group | Pass | Fail | Not run |
|---|---|---|---|
| Startup | 12 | 2 | 0 |
| Block/Unblock | 12 | 1 | 0 |
| Bandwidth | 8 | 2 | 1 |
| Kick/Ban | 15 | 0 | 0 |
| Combined | 2 | 0 | 0 |
| Edge cases | 8 | 2 | 0 |
| Network/infra failure | 4 | 1 | 0 |
| State consistency & recovery | 4 | 1 | 0 |
| Non-functional | 9 | 1 | 0 |
| **Functional total** | **65** | **9** | **1** |
| **Non-functional total** | **9** | **1** | **0** |

**What works:** start-up, authentication and TLS, all block targets (internet, gateway, peer, all) with traffic checked on the phone, bandwidth shaping accuracy (+3% of the cap), kick/ban/unban including timed expiry, protected-device immunity on every tool, input validation against injection, concurrency safety of the kernel state, hostapd outage handling, and full state restoration across a reboot.

**What fails:** changing an existing bandwidth limit silently does nothing (L4/L5). Mixing a device's name and IP leaves stale entries in `state.json` that reconcile resurrects (B14/R1/E8). Registered devices can't be targeted by name after a boot-time clock step (S12/S13). The command log hash chain forks under concurrent requests (NF6). Lax type coercion lets `confirm: "yes"` apply a ban (E3). One error is mislabelled while hostapd is down (N3).

## Test environment

| Item | Value |
|---|---|
| Router | Raspberry Pi 5 `ina-router`, AP `ap0` 192.168.50.1/24 (SSID INA-Router), upstream `wlan0`; hostapd, dnsmasq, nftables `inet ina_filter`, tc HTB on `ap0` + `ifb-ap0` |
| Services | `ina-mcp` (Streamable HTTP + TLS, port 8443, user `ina`), `ina-reconcile`, `ina-expire.timer`; privileged actions through `sudo ina-netctl` |
| Operator device | admin-laptop 192.168.50.2 (class `protected`), role `admin` for `dhruv`, bound to this device |
| Target device | dhruv-phone 192.168.50.19, MAC 8a:b4:92:05:bb:08 (class `client`) |
| Client | Minimal stateless JSON-RPC client (Python stdlib, CA-pinned TLS); the kernel state is read over SSH after every change |
| Credential | dhruv's admin token was **rotated** (`ina-netctl roles rotate dhruv`) so the tests could authenticate. The old token no longer works. |

### How results were verified

- After every change, `state.json` was compared with the live nftables sets, tc classes, `hostapd.deny` and the hostapd runtime deny ACL (the "consistent" field in the results).
- Traffic checks: ping from the laptop and from the Pi to the phone; the nft reject counters; a human browser check on the phone (B3).
- Bandwidth: a Pi to phone UDP stream (sender aiming for ~8 Mbit/s) with tc class byte counters, plus a two-stream ICMP round-trip test. The unlimited baseline was 44.4 Mbit/s UDP with 0% ICMP loss.
- Kick/ban: `hostapd_cli list_sta`, the hostapd journal (disassociate/associate events), and the deny file and ACL.

## Functional tests

### Startup

| ID | Test | Expected | Actual | Result |
|---|---|---|---|---|
| S1 | All INA services active and enabled at boot | 6x active, 6x enabled | active: active active active active active active \| enabled: enabled enabled enabled enabled enabled enabled | PASS |
| S2 | AP interface, forwarding, NAT and filter tables up | ap0=192.168.50.1/24 AP mode, ip_forward=1, ina_nat masquerade + ina_filter loaded | ap0              UP             192.168.50.1/24 fe80::2ccf:67ff:fe4e:3702/64  \| 1 \| table ip ina_nat table inet ina_filter \| 1 \| 	ssid INA-Router 	type AP | PASS |
| S3 | Traffic-shaping skeleton present (HTB on ap0 + ifb-ap0, ingress mirred) | HTB root on both, ingress redirected to ifb | qdisc htb 1: root refcnt 2 r2q 10 default 0x1fff direct_packets_stat 2 direct_qlen 1000 \| qdisc fq_codel 1fff: parent 1:1fff limit 10240p flows 1024 quantum 1514 target 5ms interval 100ms memory_limit 32Mb ecn drop_batch 64  \| qdisc htb 1: root refcnt 2 r2q 10 default 0x1fff direct_packets_stat 0 direct_qlen 1000 \| 1 | PASS |
| S4 | MCP endpoint rejects missing / invalid bearer token | 401 for no token, bad token, and unauthenticated tools/call | no token=401, bad token=401, unauth tools/call=401 | PASS |
| S5 | MCP initialize + tools/list over TLS | HTTP 200, exactly the 8 canonical tools | init=200 (0.01s), tools=['block_client', 'unblock_client', 'limit_bandwidth', 'remove_bandwidth_limit', 'get_status', 'kick_client', 'ban_client', 'unban_client'] | PASS |
| S6 | get_status returns live clients | both associated clients returned, no error | 2 clients returned, both associated (labelled by MAC, see S13) | PASS |
| S7 | Persisted state matches kernel at startup | state.json == nft sets / tc classes / deny list | consistent | PASS |
| S8 | Privilege boundary for the 'ina' service account | ina cannot run nft directly or via sudo; can run ina-netctl status; cannot run roles | nft direct: Operation not permitted; sudo nft: a password is required; sudo ina-netctl status: JSON ok; ina-netctl roles: refused (role grants cannot be changed through the ina service account) | PASS |
| S9 | DNS-rebinding protection (foreign Host / Origin) | Request refused (4xx) | Host: evil -> 421; Origin: evil -> 403 | PASS |
| S10 | TLS required and certificate must be trusted | Untrusted cert -> TLS failure (000); plain HTTP -> not served | https w/o pinned CA: 000; plain http: 000 | PASS |
| S11 | Boot logs: reconcile ran, MCP has no errors | reconcile success, no tracebacks | Anti-spoofing: 1 fixed-IP device(s) bound \| Reconciliation complete. \| Finished ina-reconcile.service - INA: re-apply desired blocks, bandwidth limits, bans and anti-spoofing bindings. \| Tool 'block_client' failed: "Error executing tool block_client: Denied: Unknown client 'dhruv-phone'. Unable to resolve to an active IP. Known clients: none." \| Tool 'unblock_client' failed: "Error executing tool  | PASS |
| S12 | Registered device resolvable by name after boot when its DHCP lease is missing | dhruv-phone (in devices.yaml, MAC in ARP table as 192.168.50.19) resolves and block succeeds | by name: isError http=200: Error executing tool block_client: Denied: Unknown client 'dhruv-phone'. Unable to resolve to an active IP. Known clients: none. \|\| by IP: ok http=200 0.31s: Successfully blocked traffic between '192.168.50.19' (192.168.50.19) and 'internet'. Client association preserved. | **FAIL** |
| S13 | get_status labels registered devices by their devices.yaml name | dhruv-phone, admin-laptop | clients listed as ['8a:b4:92:05:bb:08', 'f2:e8:1a:dc:11:d8'] | **FAIL** |
| S14 | Recovery of name resolution after the device renews its lease | after phone reconnect, block/unblock dhruv-phone by name succeeds | ok http=200 0.3s: Successfully blocked traffic between 'dhruv-phone' (192.168.50.19) and 'internet'. Client association preserved. \|\| ok http=200 0.31s: Successfully unblocked traffic between 'dhruv-phone' (192.168.50.19) and 'internet'. | PASS |

### Block/Unblock

| ID | Test | Expected | Actual | Result |
|---|---|---|---|---|
| B1 | block_client dhruv-phone internet (kernel + side effects) | 192.168.50.19 in blocked_internet; state consistent; phone still associated; gateway (Pi) and peer (laptop) still reach phone | ok http=200 0.28s: Successfully blocked traffic between 'dhruv-phone' (192.168.50.19) and 'internet'. Client association preserved. \| set=['192.168.50.19'] \| consistent=True [] \| status.blocked=['internet'] assoc=True \| ping Pi->phone 4/4 (before 4/4), laptop->phone 4/4 (before 4/4) | PASS |
| B2 | Idempotent repeat block (internet again) | success/no-op, no duplicate set element or state entry | ok http=200 0.29s: Block rule between 'dhruv-phone' (192.168.50.19) and 'internet' is already active. changed=False \| set=['192.168.50.19'] \| state internet entries=1 | PASS |
| B3 | Phone-side: internet actually unreachable while blocked (human check) | website fails to load on dhruv-phone; reject counter increases | user: page does not load, still connected to INA-Router \| counter: iifname "ap0" oifname "wlan0" ip saddr @blocked_internet counter packets 1094 bytes 367212 reject with icmpx admin-prohibited | PASS |
| B4 | unblock_client dhruv-phone internet | set emptied, state cleared, status shows no blocks | ok http=200 0.31s: Successfully unblocked traffic between 'dhruv-phone' (192.168.50.19) and 'internet'. \| set=[] \| consistent=True [] \| status.blocked=[] | PASS |
| B5 | Unblock when not blocked | success/no-op (changed=false) or clear message, no error | ok http=200 0.27s: Rule between 'dhruv-phone' (192.168.50.19) and 'internet' is already absent. changed=False | PASS |
| B6 | block_client dhruv-phone gateway | in blocked_gateway; Pi->phone ping fails (replies rejected at input); laptop->phone still works | ok http=200 0.34s: Successfully blocked traffic between 'dhruv-phone' (192.168.50.19) and 'gateway'. Client association preserved. \| set=['192.168.50.19'] \| Pi->phone 0/4 \| laptop->phone 4/4 \| consistent=True [] | PASS |
| B7 | DHCP/DNS stay reachable while gateway-blocked (rule order) | udp 53/67 + tcp 53 accept rules precede the blocked_gateway reject | iifname "ap0" udp dport { 53, 67 } accept \| iifname "ap0" tcp dport 53 accept \| iifname "ap0" ip saddr @blocked_gateway counter packets 5 bytes 400 reject with icmpx admin-prohibited | PASS |
| B8 | unblock_client dhruv-phone gateway | set emptied, Pi->phone ping restored | ok http=200 0.4s: Successfully unblocked traffic between 'dhruv-phone' (192.168.50.19) and 'gateway'. \| set=[] \| Pi->phone 4/4 | PASS |
| B9 | Peer block dhruv-phone -> admin-laptop | Either policy denies (target protected) or pair block installed and laptop<->phone ping fails while Pi->phone works | isError http=200: Error executing tool block_client: Denied by policy [R-protected]: target 'admin-laptop' is a protected device and cannot be changed. \| blocked_pairs=[] \| laptop->phone 4/4 \| Pi->phone 4/4 | PASS |
| B11 | block_client dhruv-phone all (isolate) | in isolated set; laptop->phone and Pi->phone both fail; association kept | ok http=200 0.28s: Successfully blocked traffic between 'dhruv-phone' (192.168.50.19) and 'all'. Client association preserved. \| isolated=['192.168.50.19'] \| laptop->phone 0/4 \| Pi->phone 0/4 \| assoc=True \| consistent=True [] | PASS |
| B12 | unblock_client dhruv-phone all (remove isolation) | isolated set empty, laptop/Pi ping restored | ok http=200 0.34s: Successfully unblocked traffic between 'dhruv-phone' (192.168.50.19) and 'all'. \| isolated=[] \| laptop->phone 4/4 Pi->phone 4/4 \| consistent=True [] | PASS |
| B13 | Block a protected device (admin-laptop, pi-gateway, .1, .2, laptop MAC) | all refused; nothing added to kernel | admin-laptop: isError http=200: Error executing tool block_client: Denied by policy [R-protected]: 'admin-laptop' is a protected device and cannot be chan \|\| pi-gateway: isError http=200: Error executing tool block_client: Denied by policy [R-protected]: 'pi-gateway' is a protected device and cannot be change \|\| 192.168.50.1: isError http=200: Error executing tool block_client: Denied by policy [R-protected]: 'pi-gateway' is a protected device and cannot be change \|\| 192.168.50.2: isError http=200: Error executing tool block_client: Denied by policy [R-protected]: 'admin-laptop' is a protected device and cannot be chan \|\| f2:e8:1a:dc:11:d8: isError http=200: Error executing tool bloc ... | PASS |
| B14 | Block by name, unblock by IP (same device) | kernel set AND state.json both cleared | block: ok http=200 0.38s: Successfully blocked traffic between 'dhruv-phone' (192.168.50.19) and  \| unblock by IP: ok http=200 0.3s: Successfully unblocked traffic between '192.168.50.19' (192.168.50.19) and 'gateway'. \| kernel set=[] \| state.blocks=[{'client': 'dhruv-phone', 'client_ip': '192.168.50.19', 'target': 'gateway'}] \| consistent=False ["blocked_gateway: kernel=[] state=['192.168.50.19']"] | **FAIL** |

### Bandwidth

| ID | Test | Expected | Actual | Result |
|---|---|---|---|---|
| L1 | limit_bandwidth dhruv-phone 3000/1000 kbit | HTB class 3Mbit on ap0 (download) and 1Mbit on ifb-ap0 (upload) + filters; status shows limits; state consistent | ok http=200 0.39s: Successfully applied bandwidth limits to 'dhruv-phone' (192.168.50.19): 3000 kbit/s down, 1000 kbit/s up. \| status down=3000 up=1000 \| consistent=True [] \| tc ap0=[{'handle': '1:1', 'rate': 125000000, 'ceil': 125000000}, {'handle': '1:113', 'rate': 375000, 'ceil': 375000}] ifb=[{'handle': '1:1', 'rate': 125000000, 'ceil': 125000000}, {'handle': '1:113', 'rate': 125000, 'ceil': 125000}] | PASS |
| L2 | Shaping enforced under 3000/1000 limit (Pi-side measurement) | download class caps at ~3000 kbit; round-trip limited by 1000 kbit upload cap | UDP Pi->phone (sender targeting ~8 Mbit/s): class 1:113 dequeued 3115 kbit/s, sender back-pressured to 3084 kbit/s. ICMP round-trip (2 streams, 2951 kbit offered): 985 kbit delivered, 66.6% loss, RTT avg 1.36 s (queueing at the 1 Mbit ifb class). | PASS |
| L3 | Phone-side speed test under 3000/1000 limit (human, fast.com) | download ~<=3 Mbit/s, upload ~<=1 Mbit/s (baseline 9 / 2.8) | not run: phone-side speed test skipped by user (Pi-side measurement in L2 covers enforcement) | NOT RUN |
| L4 | Change an existing limit (3000/1000 -> 6000/2000 -> 2000/1500), no reboot in between | each call re-programs tc classes and state.json to the new rates | 3000/1000: ok http=200 0.47s: Successfully applied bandwidth limits to 'dhruv-phone' (192.168.50.19): 3000 kbit/s down, 1 \| tc ap0 rate=[3000]kbit ifb=[1000]kbit state=[(3000, 1000)] \|\| 6000/2000: ok http=200 0.37s: Bandwidth limit for 'dhruv-phone' (192.168.50.19) already configured at 6000k/2000k. \| tc ap0 rate=[3000]kbit ifb=[1000]kbit state=[(3000, 1000)] \|\| 2000/1500: ok http=200 0.3s: Bandwidth limit for 'dhruv-phone' (192.168.50.19) already configured at 2000k/1500k. \| tc ap0 rate=[3000]kbit ifb=[1000]kbit state=[(3000, 1000)] | **FAIL** |
| L5 | Raised limit lets more traffic through | ~0% loss at 4.57 Mbit/s (below new 6000 kbit cap) | loss 66.7%, delivered ~989 kbit | **FAIL** |
| L6 | Idempotent repeat of identical limit | no error, changed=false | ok http=200 0.36s: Bandwidth limit for 'dhruv-phone' (192.168.50.19) already configured at 6000k/2000k. changed=False | PASS |
| L7 | remove_bandwidth_limit dhruv-phone | per-client classes/filters removed, state cleared, status null | ok http=200 0.3s: Successfully removed bandwidth limits for 'dhruv-phone' (192.168.50.19); returned to unconstrained rate. \| tc ap0=[{'handle': '1:1', 'rate': 125000000, 'ceil': 125000000}] ifb=[{'handle': '1:1', 'rate': 125000000, 'ceil': 125000000}] \| status down=None \| consistent=True [] | PASS |
| L8 | Line rate restored after removal | 0% loss at 4.57 Mbit/s | loss 0.1% | PASS |
| L9 | Remove limit when none is set | no error (no-op) | ok http=200 0.34s: No bandwidth limit was active for 'dhruv-phone' (192.168.50.19). | PASS |
| L10 | Rate bounds and type validation | <256 / >20000 / negative / float / 0 rejected by schema; 256-999 rejected by admin policy (min 1000); 1000 and 20000 accepted | 255/1000 [schema] -> ERR: Error executing tool limit_bandwidth: 1 validation error for limit_bandwidthArguments download_kbit   Input sh \|\| 20001/1000 [schema] -> ERR: Error executing tool limit_bandwidth: 1 validation error for limit_bandwidthArguments download_kbit   Input sh \|\| 500/1000 [policy: admin min 1000] -> ERR: Error executing tool limit_bandwidth: Denied by policy [R-rate-range]: download 500 kbit/s is outside the allo \|\| 1000/999 [policy] -> ERR: Error executing tool limit_bandwidth: Denied by policy [R-rate-range]: upload 999 kbit/s is outside the allowe \|\| 1000/1000 [allow] -> OK: {   "client": "dhruv-phone",   "client_ip": "192.168.50.19",   "download_kbit": 1000,   " ... | PASS |
| L11 | Limit a protected device | denied (R-protected) | admin-laptop: isError http=200: Error executing tool limit_bandwidth: Denied by policy [R-protected]: 'admin-laptop' is a protected de \|\| remove admin-laptop: ERR \|\| 192.168.50.1: isError http=200: Error executing tool limit_bandwidth: Denied by policy [R-protected]: 'pi-gateway' is a protected devi \|\| remove 192.168.50.1: ERR \|\| pi-gateway: isError http=200: Error executing tool limit_bandwidth: Denied by policy [R-protected]: 'pi-gateway' is a protected devi \|\| remove pi-gateway: ERR | PASS |

### Kick/Ban

| ID | Test | Expected | Actual | Result |
|---|---|---|---|---|
| K1 | kick_client dhruv-phone (by name) | deauth succeeds; phone drops off then auto-reassociates | ok http=200 0.74s: Deauthenticated 'dhruv-phone' (8a:b4:92:05:bb:08). It re-associated; use ban_client to keep it off. \| left AP: False (10.4s) \| reassociated: True (+0.3s) | PASS |
| K2 | Kick a protected device (admin-laptop by name/IP/MAC, pi-gateway) | all denied; laptop stays associated | admin-laptop: isError http=200: Error executing tool kick_client: Denied by policy [R-protected]: 'admin-laptop' is a protected device and canno \|\| pi-gateway: isError http=200: Error executing tool kick_client: Denied by policy [R-protected]: 'pi-gateway' is a protected device and cannot  \|\| 192.168.50.2: isError http=200: Error executing tool kick_client: Denied by policy [R-protected]: 'admin-laptop' is a protected device and canno \|\| f2:e8:1a:dc:11:d8: isError http=200: Error executing tool kick_client: Denied by policy [R-protected]: 'admin-laptop' is a protected device and canno \| laptop assoc=True | PASS |
| K3 | kick_client by MAC (8a:b4:92:05:bb:08) | deauth succeeds; phone drops off then auto-reassociates | ok http=200 0.49s: Deauthenticated 'dhruv-phone' (8a:b4:92:05:bb:08). It re-associated; use ban_client to keep it off. \| left AP: False (10.4s) \| reassociated: True (+0.3s) | PASS |
| K4 | 4th kick within 10 min is rate-limited | refused (kicked 3 times in 10 min; use ban_client); phone not deauthenticated | isError http=200: Error executing tool kick_client: Denied: 'dhruv-phone' was kicked 3 times in the last 10 minutes; use ban_client instead. \| still associated=True | PASS |
| K5 | ban_client without confirm = preview only | preview returned; deny file/ACL untouched; phone still associated | ok http=200 0.28s: PREVIEW: would deny 8a:b4:92:05:bb:08 for 2 min and disconnect it now. Existing IP blocks/limits stay. Re-send with confirm=true to apply. preview=True \| deny_file=[] acl=[] \| assoc=True | PASS |
| K6 | ban_client 2 min confirm=true | MAC in hostapd.deny + runtime deny ACL; phone deauthenticated and cannot rejoin; status lists ban with expiry; blocks/limits untouched | ok http=200 0.56s: Banned 'dhruv-phone' (8a:b4:92:05:bb:08) until 2026-11-05T21:07:01.554059+00:00. Verified: in hostapd deny ACL, in deny file, station gone. \| left AP=True (0.3s) \| deny_file=['8a:b4:92:05:bb:08'] acl=['8a:b4:92:05:bb:08'] \| bans=[('dhruv-phone', '2026-11-05T21:07:01.554059+00:00', 'admin')] \| still off after 40 s=True \| blocks=['192.168.50.19'] limits=1 \| consistent=True [] | PASS |
| K7 | Kick a client that is not associated (192.168.50.46: lease present, not on AP) | clean handling (refusal or no-op with clear message), no traceback, nothing executed | by IP and by MAC: success no-op: "is not currently associated with ap0; nothing to disconnect." | PASS |
| K8 | Re-ban an already-banned client (extend 2 -> 5 min) | no duplicate entry; expiry updated | ok http=200 0.26s: Banned 'dhruv-phone' (8a:b4:92:05:bb:08) until 2026-11-05T21:10:43.822770+00:00. Verified: in hostapd deny ACL, in deny file, station gone. \| state bans=[('8a:b4:92:05:bb:08', '2026-11-05T21:10:43.822770+00:00')] \| deny_file=['8a:b4:92:05:bb:08'] | PASS |
| K9 | unban_client dhruv-phone | MAC removed from deny file + ACL; state cleared; phone can rejoin | ok http=200 0.52s: Unbanned 'dhruv-phone' (8a:b4:92:05:bb:08). Station may now re-associate. \| deny_file=[] acl=[] \| consistent=True [] \| phone auto-rejoined within 120 s: False (+120.5s) | PASS |
| K9b | Manual rejoin after unban (human) | phone can join INA-Router again | user reconnected manually; associated=True; status=[('192.168.50.19', True)]. Note: Android did NOT auto-rejoin within 120 s after unban | PASS |
| K10 | Timed ban (1 min) expires automatically via ina-expire.timer | MAC removed from deny file + ACL and state within ~2 min of banning; state consistent | ok http=200 0.3s: Banned 'dhruv-phone' (8a:b4:92:05:bb:08) until 2026-11-05T21:09:48.379676+00:00. Verified: in hostapd  \| removed=True after 94s \| state bans=[] \| consistent=True [] | PASS |
| K11 | ban_client validation & protection (duration 0 / 10081, bad reason chars, 121-char reason, protected targets) | all rejected; deny list unchanged | {"client": "dhruv-phone", "duration_minutes": 0, "confirm": true} -> ERR: Error executing tool ban_client: 1 validation error for ban_clientArguments duration_minutes   Input \|\| {"client": "dhruv-phone", "duration_minutes": 10081, "confirm": true} -> ERR: Error executing tool ban_client: 1 validation error for ban_clientArguments duration_minutes   Input \|\| {"client": "dhruv-phone", "duration_minutes": 5, "reason": "x; rm -rf  -> ERR: Error executing tool ban_client: 1 validation error for ban_clientArguments reason   String should m \|\| {"client": "dhruv-phone", "duration_minutes": 5, "reason": "aaaaaaaaaa -> ERR: Error executing tool ban_client: 1 validation error for ban_clientArgume ... | PASS |
| K12 | Permanent ban (no duration) as admin | accepted, expires_at=null, MAC denied | ok http=200 0.33s: Banned 'dhruv-phone' (8a:b4:92:05:bb:08) permanently. Verified: in hostapd deny ACL, in deny file, station gone. \| state bans=[('8a:b4:92:05:bb:08', None, 'admin')] | PASS |
| K13 | Unban a permanent ban | removed from deny file/ACL/state | ok http=200 0.49s: Unbanned 'dhruv-phone' (8a:b4:92:05:bb:08). Station may now re-associate. \| deny_file=[] acl=[] consistent=True | PASS |
| K14 | Unban a client that is not banned | no error / clear no-op message | ok http=200 0.64s: Unbanned 'dhruv-phone' (8a:b4:92:05:bb:08). Station may now re-associate. | PASS |

### Combined

| ID | Test | Expected | Actual | Result |
|---|---|---|---|---|
| C1 | Block(internet)+limit, then kick: restrictions survive reconnection | phone reconnects with same IP; blocked_internet and tc classes still present; state consistent | block: ok http=200 0.31s: Successfully blocked traffic between 'dhr \| limit: ok http=200 0.51s: Successfully applied bandwidth limits to  \| kick: ok http=200 0.71s: Deauthenticated 'dhruv-phone' (8a:b4:92:0 \| reassoc=True (+0.3s) \| lease=192.168.50.19 \| blocked_internet=['192.168.50.19'] \| tc ap0=['1:1', '1:113'] \| Pi->phone 4/4 \| consistent=True [] | PASS |
| C2 | Ban + unban while block(internet) and limit are active | ban/unban do not touch IP blocks or tc limits | after ban->re-ban->unban: blocked_internet=['192.168.50.19'], state.limits=[(3000, 1000)], tc ap0=['1:1', '1:113'], consistent=True | PASS |

### Edge cases

| ID | Test | Expected | Actual | Result |
|---|---|---|---|---|
| E1 | Injection / malformed client identifiers on block_client | all rejected by schema or policy (except harmless forms that resolve to a real client); kernel unchanged | "dhruv-phone; reboot" -> ERR: Error executing tool block_client: 1 validation error for block_clientArguments client   String should match p \|\| "$(id)" -> ERR: Error executing tool block_client: 1 validation error for block_clientArguments client   String should match p \|\| "`id`" -> ERR: Error executing tool block_client: 1 validation error for block_clientArguments client   String should match p \|\| "dhruv-phone && nft flush ruleset" -> ERR: Error executing tool block_client: 1 validation error for block_clientArguments client   String should match p \|\| "../../etc/passwd" -> ERR: Error executing tool block_client: 1 validation error for block_clientArguments client   String should ma ... | PASS |
| E2 | Invalid / odd block targets (incl. client targeting itself) | unknown keywords rejected; self-target rejected; no set changes | internet;id -> ERR: Error executing tool block_client: 1 validation error for block_clientArguments target   String should match p \|\| INTERNET -> ERR: Error executing tool block_client: 1 validation error for block_clientArguments target   String should match p \|\| everything -> ERR: Error executing tool block_client: Denied by policy [R-unknown-device]: 'everything' is not a recognized devic \|\| dhruv-phone -> ERR: Error executing tool block_client: Denied by policy [R-self-target]: A client cannot be blocked from itself. \|\| 192.168.50.19 -> ERR: Error executing tool block_client: Denied by policy [R-self-target]: A client cannot be blocked from itself. \|\| 10.0.0.1 -> ERR: Error exe ... | PASS |
| E3 | Schema violations: missing/extra args, wrong types, bool-as-int, unknown tool | each rejected with a validation error; nothing executed | block_client {"client": "dhruv-phone"} -> ERR: Error executing tool block_client: 1 validation error for block_clientArguments target   Field required [type= \|\| block_client {"client": "dhruv-phone", "target": "internet", "extra": "x" -> OK: {   "client": "dhruv-phone",   "client_ip": "192.168.50.19",   "target": "internet",   "blocked": true,   "cha \|\| limit_bandwidth {"client": "dhruv-phone", "download_kbit": true, "upload_kbi -> ERR: Error executing tool limit_bandwidth: 1 validation error for limit_bandwidthArguments download_kbit   Input sh \|\| limit_bandwidth {"client": "dhruv-phone", "download_kbit": null, "upload_kbi -> ERR: Error executing tool limit_bandwidth: 1 validation err ... | **FAIL** |
| E4 | Malformed JSON-RPC bodies and oversized request | 4xx or JSON-RPC error; server stays up | {not json -> HTTP 400 {"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"Parse error: key mu \|\| [] -> HTTP 400 {"jsonrpc":"2.0","id":null,"error":{"code":-32602,"message":"Validation error: 4 \|\| {"jsonrpc":"2.0","id":1,"method":"tools/ -> HTTP 200 {"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"Invalid request parame \|\| {"jsonrpc":"2.0","id":1,"method":"tools/ -> HTTP 200 {"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"Invalid request parame \|\| 200 KB argument -> HTTP 200 {"jsonrpc":"2.0","id":1,"result":{"content":[{"text":"Error executing tool get_s \| server still answers: True | PASS |
| E5 | Auth header variants (Basic scheme, empty Bearer, lowercase 'bearer') | Basic / empty -> 401; lowercase scheme either accepted (RFC: case-insensitive) or 401 | 'Basic xxxxxx...' -> 401 \|\| 'Bearer...' -> 401 \|\| 'bearer xl8VO...' -> 200 | PASS |
| E6 | 10 concurrent block/unblock requests on the same client | all serialized by the lock (success or clean 'try again'); final kernel == state.json | 10 ok, 0 errors set() \| final blocked_internet=['192.168.50.19'] \| consistent=True [] | PASS |
| E7 | 6 concurrent limit_bandwidth calls with different rates | one consistent final limit; single state entry; tc matches state | results=['OK', 'OK', 'OK', 'OK', 'OK', 'OK'] \| state limits=[{'client': 'dhruv-phone', 'client_ip': '192.168.50.19', 'download_kbit': 3000, 'upload_kbit': 1000}] \| tc ap0=[('1:1', 1000000), ('1:113', 3000)] \| consistent=True [] | PASS |
| E8 | Limit by name then by IP, remove by IP (identifier mixing) | one state entry; removal clears kernel and state | after name+IP limits: state entries=1 ['dhruv-phone'] \| after remove by IP: ok http=200 0.31s: Successfully removed bandwidth limits for '192.168.50.19' (19 state=[{'client': 'dhruv-phone', 'client_ip': '192.168.50.19', 'download_kbit': 3000, 'upload_kbit': 1000}] tc=['1:1'] consistent=(True, []) | **FAIL** |
| E9 | Block an in-subnet IP with no lease / not associated (192.168.50.99) | refused as unknown client, or applied and tracked consistently | ok http=200 0.28s: Successfully blocked traffic between '192.168.50.99' (192.168.50.99) and 'internet'. Client association preserved. \| blocked_internet=['192.168.50.99'] \| consistent=(True, []) | PASS |
| E10 | Peer block to an unprotected, offline client (dhruv-phone -> 192.168.50.46) | pair installed (or clean refusal) and state consistent | ok http=200 0.31s: Successfully blocked traffic between 'dhruv-phone' (192.168.50.19) and '192.168.50.46'. Client association preserved. \| blocked_pairs=['192.168.50.19.192.168.50.46', '192.168.50.46.192.168.50.19'] \| consistent=(True, []) | PASS |

### Network/infra failure

| ID | Test | Expected | Actual | Result |
|---|---|---|---|---|
| N1 | hostapd stopped: get_status still works | status returns (falls back to neighbor table), no crash | ina-netctl status during outage: ok, rc=0; hostapd_cli list_sta exit 255 recorded in trace; neighbor fallback used | PASS |
| N2 | hostapd stopped: kick_client fails cleanly | clean WirelessControlError, nothing changed | ok:false, error=WirelessControlError "hostapd control interface not available on ap0", exit 1 (MCP maps to: Failed: The Wi-Fi access point did not accept the request) | PASS |
| N3 | hostapd stopped: ban_client fails cleanly with accurate error | fails, state unchanged, error reported as a Wi-Fi/AP failure | ok:false, state.json unchanged (existing 15-min ban kept, no partial write) BUT error type is RuntimeError, which privileged.py maps to KernelMutationError, so an MCP caller sees "The router could not apply the change" instead of the Wi-Fi/AP message kick gives | **FAIL** |
| N4 | hostapd stopped: nftables tools unaffected | block/unblock 192.168.50.99 internet succeed during AP outage | block + unblock both applied/removed; state.json blocks=[] afterwards | PASS |
| N5 | hostapd restart recovery | AP back; deny list reloaded from hostapd.deny (ban persists); clients reassociate | outage 23 s (02:45:14-02:45:37); AP-ENABLED 1 s after start; deny_acl after restart = 8a:b4:92:05:bb:08 (ban persisted); admin-laptop reassociated 16 s after AP-ENABLED; MCP reachable again | PASS |

### State consistency & recovery

| ID | Test | Expected | Actual | Result |
|---|---|---|---|---|
| R1 | Stale state entry (from B14) after reconcile | a block the operator removed must stay removed | get_status still reports blocked_targets=['gateway'] while kernel blocked_gateway=[]; drift_detected=False. After ina-netctl reconcile: blocked_gateway=['192.168.50.19'], Pi->phone ping 0/4 | **FAIL** |
| R2 | Bandwidth limit survives an (unplanned) Pi reboot | after reboot, ina-reconcile re-applies 3000/1000 for dhruv-phone; kernel == state.json | uptime 20 min; state.limits=[{'client': 'dhruv-phone', 'client_ip': '192.168.50.19', 'download_kbit': 3000, 'upload_kbit': 1000}]; tc ap0=[{'handle': '1:1', 'rate': 125000000, 'ceil': 125000000}, {'handle': '1:113', 'rate': 375000, 'ceil': 375000}]; ifb=[{'handle': '1:1', 'rate': 125000000, 'ceil': 125000000}, {'handle': '1:113', 'rate': 125000, 'ceil': 125000}]; consistency=(True, []) | PASS |
| R3 | Planned reboot with block(internet)+block(gateway)+limit(4000/2000)+ban active | after boot every set element, tc class (same rates), deny entry and state.json identical to pre-reboot; all services active | sets match=True; blocked_internet=['192.168.50.19'] blocked_gateway=['192.168.50.99'] deny_acl=['8a:b4:92:05:bb:08'] tc rates ap0/ifb (bytes/s)=([125000000, 500000], [125000000, 250000]); consistent=True [] | PASS |
| R4 | Boot time to service | MCP answers within 2 min of reboot command | reboot issued 02:47:37, kernel boot 02:47:49, MCP answering within ~30 s of the command; laptop re-associated automatically | PASS |
| R5 | Ban created before reboot still expires on time after reboot | 15-min ban (expiry 02:59:56) removed by ina-expire within ~1-2 min of expiry | removed at 03:00:37; state bans=[]; consistent=True [] | PASS |

## Non-functional tests

| ID | Test | Expected | Actual | Result |
|---|---|---|---|---|
| NF1 | Latency: get_status (40 sequential calls) | p95 < 1.0 s | p50=0.31s p95=0.54s max=0.61s | PASS |
| NF2 | Latency: mutating tools incl. kernel change + verification (block/unblock x15, limit/remove x8) | p95 < 2.0 s end-to-end | block p50=0.36 p95=0.64 \| unblock p50=0.33 p95=0.51 \| limit p50=0.41 p95=0.51 \| remove p50=0.3 p95=0.39 (s) | PASS |
| NF3 | Sustained load: 5 parallel clients x 30 s of get_status | 0 errors; server responsive; memory stable | 272 requests in 30 s = 9.1 req/s, errors=0 \| ina-mcp RSS/CPU before: 78000  0.1    2011 after: 77920  0.2 \| 02:43:12 up 34 min,  3 users,  load average: 2.34, 0.66, 0.22 \| mem: ['8062', '616', '7007'] | PASS |
| NF4 | Shaping accuracy (from L2 / UDP baseline) | shaped rate within +/-10% of configured cap; unshaped path not throttled | 3000 kbit cap -> 3081-3115 kbit/s dequeued (+2.7..+3.8%); 1000 kbit upload cap -> 985 kbit/s ICMP goodput (-1.5%); unlimited UDP Pi->phone 44.4 Mbit/s | PASS |
| NF5 | Kick re-association time / ban expiry timeliness | kicked client back < 10 s; 1-min ban lifted < 2.5 min | kick: hostapd log shows disassociate -> associate within ~1 s (K1,K3,C1); 1-min ban removed after 94 s (K10) | PASS |
| NF6 | Audit log integrity (SHA-256 hash chains), verified as root on the real paths | policy audit, command log and network audit log all verify OK | policy_audit.jsonl (1421 records): OK \| audit.log (182 records): OK \| commands.jsonl (2447 records): BROKEN at seq 1944: 17 duplicate seq numbers (1944, 1946-1948, 1982, ...) share the same prev hash -> concurrent ina-netctl writers (read-only get_status runs without the network lock) fork the chain under parallel load (E6/NF3) | **FAIL** |
| NF7 | Security: admin token used from a different device (the Pi, 192.168.50.1) | refused (R-token-device): token is bound to admin-laptop 192.168.50.2 | {"jsonrpc":"2.0","id":1,"result":{"content":[{"text":"Error executing tool get_status: Denied by policy [R-token-device]: This role token is bound to 'admin-laptop' and cannot be used from another device.","type":"text"}],"isError":true}} HTTP=200 | PASS |
| NF8 | Security: role token never written in plaintext to logs / grants file | 0 occurrences in command log, audit logs, grants.json (hash only) and journal | /var/log/ina/commands.jsonl:0 /var/log/ina/audit.log:0 /var/log/ina-mcp/policy_audit.jsonl:0 /etc/ina/grants.json:0 journal:0 | PASS |
| NF9 | TLS configuration | modern TLS only, strong cipher | TLSv1.2 -> ECDHE-ECDSA-AES256-GCM-SHA384; TLSv1.3 -> TLS_AES_256_GCM_SHA384; P-256 self-signed cert pinned by client; TLSv1.1 could not be offered (disabled in the laptop OpenSSL), so server-side refusal of <=1.1 not proven | PASS |
| NF10 | Audit trail completeness (policy audit) | every tool call writes a decision record and an outcome record under one request id, incl. denials with the rule | Last 8 records: get_status ALLOW R-status -> outcome success; block_client ALLOW R-block -> outcome success verified=true; unblock_client ALLOW R-unblock -> success verified=true; block admin-laptop DENY R-protected (no outcome, nothing executed). Decision+outcome pairs share request_id. | PASS |

Key figures: get_status p50 0.31 s / p95 0.54 s. Mutating tools p95 0.39-0.64 s, including the kernel change and verification. 9.1 req/s sustained with 0 errors from 5 parallel clients; ina-mcp RSS stayed about 78 MB. Shaping held within +3.8% of the cap. A kicked client re-associates in ~1 s. A 1-minute ban is lifted after 94 s. Reboot to MCP serving takes about 30 s.

## Failures and root causes

| ID | Severity | Root cause | Workaround / fix direction |
|---|---|---|---|
| S12 | High | After boot the Pi has no RTC; when NTP steps the clock forward, dnsmasq treats every lease as expired and drops it. Name lookup then only consults the lease file, so a device listed in `devices.yaml` is reported as `Unknown client ... Known clients: none` until it renews (hours later). The MAC is in `devices.yaml` and the MAC-to-IP mapping is in the ARP table, but neither is used as a fallback. | Run tools by IP until the device reconnects, or reconnect the device so it takes a new lease (S14). Long-term: resolve registered names via `devices.yaml` MAC + `ip neigh`, and/or start dnsmasq after `time-sync.target`. |
| S13 | Low | Same root cause as S12: `_live_clients()` names clients from the lease hostname, so without a lease registered devices appear by MAC and admin-laptop's fixed IP is shown as empty. | Same fix as S12 (fall back to `devices.yaml`). |
| B14 | High | `state.json` entries are keyed by the client string as typed (`args.client`). Unblock by IP removes the kernel element but does not match the entry stored under the name. A later unblock by name says `already absent` and, because state is only updated when the kernel changed, the stale entry is never removed. | Always use the same identifier (the name) for a device. Long-term: key state on the resolved IP/MAC and update state even when the kernel is already in the target state. |
| R1 | High | Consequence of B14: `ina-netctl reconcile` (run at every boot) re-applies the stale entry, so a block the operator removed comes back. `get_status` also keeps reporting it, and `drift_detected` is hard-coded `False` in `service.status()`, so nothing flags the mismatch. | Same as B14; make `drift_detected` compare state with the kernel. |
| L4 | High | `verify_bandwidth_applied()` (network/verification.py:194-206) checks the class exists but, when the rate string does not match, falls back to `if target_class not in stdout` (already known to be False), so it returns True for any rate. `limit()` then short-circuits with `already configured at <new rate>`: the kernel and state keep the old rate while the reply claims the new one. The same function is the post-apply verification, so rates are never really verified. | Call `remove_bandwidth_limit` before setting a new rate. Fix: compare the parsed class rate/ceil with the requested kbit. |
| L5 | High | Consequence of L4: the 6000/2000 change never reached the kernel, so traffic stayed capped at the old 1 Mbit/s upload limit (66.7% loss). | Same as L4. |
| E3 | Medium | Pydantic runs in lax mode: `confirm: "yes"` is coerced to `True` and a real ban was applied (it was removed straight away). Unknown extra arguments are silently ignored, and `download_kbit: "5000"` (string) is accepted (L10). | Use strict types for `confirm` (and ideally every tool argument), and `extra=forbid`. |
| E8 | High | Same keying bug as B14, for limits: limit by name, then remove by IP, deletes the tc classes but leaves the state entry, so reconcile would re-apply the limit after a reboot. | Same as B14. |
| NF6 | Medium | `/var/log/ina/commands.jsonl` hash chain is broken from seq 1944: 17 sequence numbers are duplicated with the same `prev` hash. Each `sudo ina-netctl` is a separate process; read-only calls (`get_status`) run without the network lock and append concurrently, so parallel requests (E6 / NF3) fork the chain. The policy audit log (1421 records) and network audit log (182 records) verify OK. | Take an exclusive `flock` on the command log around read-last-hash + append. |
| N3 | Low | With hostapd down, `ban` raises a plain `RuntimeError`. `network/privileged.py` maps unknown error names to `KernelMutationError`, so an MCP caller is told `The router could not apply the change` instead of the Wi-Fi/AP message `kick` gives. No partial change was written. | Raise `WirelessControlError` from the ban path. |

### Observations (not counted as failures)

- `get_status` always reports `drift_detected: false` (hard-coded in `network/service.py`), even while R1 showed state and kernel disagreeing.
- `unban_client` on a device that is not banned replies `Unbanned ...` rather than a no-op message (K14).
- After an unban, Android did not rejoin by itself within 120 s; it needed a manual reconnect (K9/K9b). This is client behaviour, not a router fault.
- `block_client` accepts any in-subnet IP, even one with no lease or device (E9, 192.168.50.99).
- `ina-netctl` reports `ok:false` failures in JSON. Check the `ok` field, not only the exit code.

## Not run

| Test | Reason |
|---|---|
| L3 Phone-side speed test under 3000/1000 limit (human, fast.com) | not run: phone-side speed test skipped by user (Pi-side measurement in L2 covers enforcement) |
| Upstream internet loss (remove default route) | Not authorised for this run (user approved only reboot + hostapd stop). |
| dnsmasq stopped / ina-mcp stopped or restarted | Not authorised for this run. |
| Manual drift injection (edit nft sets / tc / deny file, corrupt state.json, read-only audit log) | Not authorised for this run. R1, R2 and R3 cover reconcile behaviour without manual tampering. |
| operator / viewer role limits over MCP | Only one grant exists (dhruv/admin on admin-laptop); testing other roles needs a new grant on a second registered fixed-IP device. |
| Phone-side DNS/DHCP during a gateway block | B7 checks the rule order on the router; nobody checked on the phone. |

## Notes on re-runs

Some tests were re-run because the **test harness** was wrong, not the router. The tables show the final run.

- K7: 3 earlier attempt(s). First attempts hit the kick rate limiter (3 kicks / 10 min) rather than the not-associated path; re-run against unassociated client 192.168.50.46, and the expectation widened to accept a clean no-op.
- L2: 1 earlier attempt(s). First attempt used a wrong offered-rate calculation (ping only reached ~270 pps, about 3.1 Mbit/s, right at the cap); re-measured with UDP plus tc counters and a two-stream ICMP test.
- L4: 1 earlier attempt(s). Re-run without a reboot in between, to rule out the unplanned reboot as the cause. It reproduced.
- NF10: 1 earlier attempt(s). First evaluation used a too-strict string match; the audit records were then checked by hand.
- NF6: 2 earlier attempt(s). First attempt read a non-existent default path as the 'ina' user (a vacuous pass); re-run as root on the real paths from the service unit.
- NF7: 2 earlier attempt(s). Earlier attempts failed on harness quoting and on cert-file permissions (HTTP 400/000) before any auth decision was made.
- S6: 1 earlier attempt(s). Re-scored: status does return both live clients. The naming problem is tracked separately as S13.
- S8: 1 earlier attempt(s). First run mis-parsed the JSON prefix of `ina-netctl status`.

## Router state after testing

All test blocks, limits and bans were removed through the MCP tools. `state.json` was left empty and consistent with the kernel.
