# Tool Reference: `get_status`

The `get_status` tool reports the known clients with their active packet blocks, bandwidth limits and MAC bans, as recorded in the desired state (`/var/lib/ina/state.json`).

> **Current limitation:** the client list is built from an injected lease map, so on the Pi it is empty. Every listed client is reported `associated` / `REACHABLE`, and `drift_detected` is always `false`. Reading `dnsmasq.leases`, `ip neigh` and the live `nft` / `tc` state is an open item ([implementation_status.md](../implementation_status.md)).

---

## 1. Scope & Network Semantics

### What the Tool Does
- **Comprehensive Read-Only Inspection:** Aggregates live data from five separate router subsystems:
  1. **DHCP Subsystem:** Parses `/var/lib/misc/dnsmasq.leases` to map MAC addresses to allocated IP addresses and hostnames.
  2. **Neighbor Subsystem:** Queries `ip neigh show dev ap0` to determine ARP cache state (`REACHABLE`, `STALE`, `DELAY`, `FAILED`).
  3. **Packet Filtering (`nftables`):** Reads sets from table `inet ina_filter` (`blocked_pairs`, `blocked_internet`, `blocked_gateway`, `isolated`) to identify blocked targets per client.
  4. **Traffic Shaping (`tc`):** Reads classes from `ap0` and `ifb-ap0` to report applied download and upload caps in kbit/s.
  5. **Wireless Access Control (`hostapd`):** Reads active bans stored in `/var/lib/ina/state.json` and verifies active deny ACLs.
- **Drift Detection (planned):** the `drift_detected` field exists, but no comparison with the live kernel state is made yet; it is always `false`.
- **Zero Side Effects:** Strictly read-only; alters no kernel state, files, or queues. Safe to execute repeatedly.

### What the Tool Leaves Untouched
- Performs zero write actions across all subsystems.

---

## 2. API & Schema Specification

### Input Arguments (`GetStatusArgs`)
| Parameter | Type | Required | Constraints / Validation | Description |
| :--- | :--- | :---: | :--- | :--- |
| `client` | `string \| null` | No | Pattern: `^([a-z0-9][a-z0-9-]{0,62}\|192\.168\.50\.[0-9]{1,3})$` | Optional client name or IPv4 address to filter results. If omitted, returns all clients. |

### Return Model (`StatusResult`)
| Field | Type | Description |
| :--- | :--- | :--- |
| `clients` | `List[ClientStatus]` | List of client devices and their active configurations. |
| `bans` | `List[BanEntry]` | List of active MAC bans and their expiry times. |
| `drift_detected` | `boolean` | `true` if kernel configuration diverges from desired state. |
| `message` | `string \| null` | Summary or diagnostic message. |

#### `ClientStatus` Model
| Field | Type | Description |
| :--- | :--- | :--- |
| `client` | `string` | Resolved client hostname. |
| `ip` | `string` | Client IPv4 address. |
| `mac` | `string \| null` | Station hardware MAC address. |
| `associated` | `boolean` | `true` if station is currently associated with Wi-Fi / reachable via ARP. |
| `neighbor_state` | `string \| null` | ARP neighbor state (`REACHABLE`, `STALE`, etc.). |
| `blocked_targets` | `List[string]` | Active blocked scopes or peer IP addresses. |
| `download_kbit` | `integer \| null`| Download bandwidth ceiling in kbit/s (or `null` if unconstrained). |
| `upload_kbit` | `integer \| null`  | Upload bandwidth ceiling in kbit/s (or `null` if unconstrained). |
| `banned` | `boolean` | `true` if the station's MAC address is currently denied association. |

#### `BanEntry` Model
| Field | Type | Description |
| :--- | :--- | :--- |
| `client` | `string` | Client name or identifier. |
| `mac` | `string` | Normalized hardware MAC address. |
| `reason` | `string \| null` | Optional reason provided when banned. |
| `banned_at` | `string` | ISO-8601 UTC timestamp when the ban was enacted. |
| `expires_at` | `string \| null` | ISO-8601 UTC expiry time, or `null` if permanent. |
| `banned_by_role` | `string` | Role that enacted the ban (`admin` or `operator`). |

### MCP Annotations & Permissions
- **MCP Annotations:** `destructive_hint=False`, `idempotent_hint=True`, `open_world_hint=False`, `read_only_hint=True`.
- **Role Requirements:** Allowed for **all roles** (`admin`, `operator`, `viewer`).

---

## 3. Implementation Details

- **MCP Wrapper:** [`inav2/mcp_server/tools.py`](../../mcp_server/tools.py) (`get_status`)
- **Authoritative Pipeline:** [`inav2/network/service.py`](../../network/service.py) (`NetworkService.status`)

### Subsystem Query Commands
```bash
# Query ARP neighbors:
ip -j neigh show dev ap0

# Query active packet blocks:
nft -j list sets inet ina_filter

# Query traffic control classes:
tc -j class show dev ap0
tc -j class show dev ifb-ap0

# Read active bans and persistent desired state:
cat /var/lib/ina/state.json
```

---

## 4. Usage Examples

### MCP JSON-RPC Request
```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "tools/call",
  "params": {
    "name": "get_status",
    "arguments": {}
  }
}
```

### MCP Success Response
```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "clients": [
      {
        "client": "laptop-a",
        "ip": "192.168.50.20",
        "mac": "00:11:22:33:44:55",
        "associated": true,
        "neighbor_state": "REACHABLE",
        "blocked_targets": ["internet"],
        "download_kbit": 5000,
        "upload_kbit": 1000,
        "banned": false
      },
      {
        "client": "phone-b",
        "ip": "192.168.50.21",
        "mac": "aa:bb:cc:dd:ee:ff",
        "associated": false,
        "neighbor_state": null,
        "blocked_targets": [],
        "download_kbit": null,
        "upload_kbit": null,
        "banned": true
      }
    ],
    "bans": [
      {
        "client": "phone-b",
        "mac": "aa:bb:cc:dd:ee:ff",
        "reason": "Excessive roaming",
        "banned_at": "2026-10-30T10:00:00Z",
        "expires_at": "2026-10-30T11:00:00Z",
        "banned_by_role": "operator"
      }
    ],
    "drift_detected": false,
    "message": "Network status retrieved: 2 managed clients, 1 active bans."
  }
}
```

### CLI Equivalent (`ina-netctl`)
```bash
# Query network status across all clients
ina-netctl status

# Query status for specific client
ina-netctl status laptop-a
```

