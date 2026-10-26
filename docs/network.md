# INA v2 Network Architecture & Telemetry Reference

This document details the interface roles, IP address space, DHCP lease management, NAT mechanics, and device inspection primitives implemented on the **Raspberry Pi 5 (`ina-router`)** network host.

---

## 1. Network Interfaces & Responsibilities

The Raspberry Pi 5 operating system manages four primary network interfaces:

```text
       WAN / Internet
             │
             │ (Wi-Fi STA Link)
      ┌──────▼──────┐
      │    wlan0    │  Physical Wireless NIC (Broadcom/Cypress)
      └──────┬──────┘
             │  (Kernel Routing & nftables NAT)
      ┌──────▼──────┐
      │     ap0     │  Virtual Wireless Interface (type __ap on wlan0)
      └──────┬──────┘
             │
             │ (802.11 AP Link / 192.168.50.0/24)
      ┌──────▼──────┐
      │   Clients   │  Fedora Laptop (192.168.50.46), Mobile Devices, etc.
      └─────────────┘
```

### Detailed Interface Attributes

| Interface | Interface Type | Physical Driver | IPv4 Address | Netmask / CIDR | Default Gateway | Operational Role |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`wlan0`** | Wireless (STA / Station) | `brcmfmac` | Dynamic (from Hotspot) | Varies (e.g., `/24`) | Upstream Hotspot IP | Upstream internet uplink to phone hotspot (`1+ Four`). Carries kernel default route. |
| **`ap0`** | Wireless (AP / Master) | `brcmfmac` (virtual) | `192.168.50.1` | `255.255.255.0` (`/24`) | None (Acts as Gateway) | Local wireless access point (`INA-Router`, Ch 11). Hosts DHCP/DNS and client subnet. |
| **`eth0`** | Ethernet (Gigabit) | `bcmgenet` | Unassigned | N/A | None | On-board RJ-45 port. Unused / disconnected during testing. Available for wired backhaul. |
| **`lo`** | Loopback | Kernel | `127.0.0.1` | `255.0.0.0` (`/8`) | None | Local host IPC. |

---

## 2. IP Addressing Architecture (`192.168.50.0/24`)

The local access point network operates in the private IPv4 block defined by RFC 1918:

- **Network Address:** `192.168.50.0`
- **Broadcast Address:** `192.168.50.255`
- **Subnet Mask:** `255.255.255.0` (`/24`)
- **Total Usable Host Capacity:** 254 IP addresses (`192.168.50.1` to `192.168.50.254`)

### Subnet Allocation Map

```text
192.168.50.0        Network address (Reserved)
192.168.50.1        Raspberry Pi Router (ap0 static IP / Gateway / DNS)
192.168.50.2 - .9   Static Infrastructure Reservations (e.g. managed switches, AP bridges)
192.168.50.10 - .100 Dynamic DHCP Client Pool (Assigned by dnsmasq to wireless clients)
192.168.50.101 - .254 High Static / Server Reservations (e.g. edge compute nodes, static test rigs)
192.168.50.255      Broadcast address (Reserved)
```

---

## 3. DHCP Lease Management & Telemetry

Client address allocation is governed by `dnsmasq` listening on interface `ap0`.

### Lease Storage Location

Active and historical leases are maintained in:
```text
/var/lib/misc/dnsmasq.leases
```

### Observed Lease Example

During live testing with a Fedora laptop client, the following lease record was captured:

```text
1793087276 5e:22:f6:20:9a:e7 192.168.50.46 * 01:5e:22:f6:20:9a:e7
```

### Lease Record Field Breakdown

| Token Index | Field Name | Observed Value | Technical Interpretation |
| :---: | :--- | :--- | :--- |
| **1** | **Lease Expiration Epoch** | `1793087276` | Unix timestamp (seconds since Jan 1, 1970 UTC) **when the lease expires**. It is calculated as `time_of_assignment + lease_duration (12h)`. It is NOT the test execution time. |
| **2** | **MAC Address** | `5e:22:f6:20:9a:e7` | Link-layer hardware address of the client's wireless card. (See note on MAC randomization below). |
| **3** | **Assigned IP** | `192.168.50.46` | IPv4 address assigned to the client from the `192.168.50.10–100` pool. |
| **4** | **Advertised Hostname** | `*` | Client hostname. An asterisk (`*`) denotes that the client did not send DHCP Option 12 (Host Name) in its `DHCPREQUEST`. |
| **5** | **Client Identifier** | `01:5e:22:f6:20:9a:e7` | DHCP Client ID (Option 61). The prefix `01` represents the hardware type (Ethernet/802.11), followed by the client MAC address. |

### Technical Considerations for Client Tracking

1. **MAC Address Randomization:** Modern client operating systems (Android, iOS, Windows 11, Fedora NetworkManager) enable Wi-Fi MAC address randomization by default. In the observed MAC `5e:22:f6:20:9a:e7`, the second least-significant bit of the first byte (`0x5e = 01011110b`, bit 1 is set) indicates a **Locally Administered Address (LAA)**. Network policies must account for clients changing their virtual MAC upon reconnection or network forget/re-join.
2. **Missing Hostnames (`*`):** Security-focused OS privacy settings often suppress hostnames in DHCP requests to prevent tracking. The INA system identifies clients primarily by IP/MAC bindings and link-layer reachability rather than hostnames.

---

## 4. Kernel Forwarding & NAT Mechanics

The Raspberry Pi acts as a stateful Network Address Translation (NAT) router between the local subnet (`192.168.50.0/24`) and the upstream phone hotspot network.

### IPv4 Packet Forwarding

Forwarding is governed by the Linux kernel sysctl parameter:
```text
net.ipv4.ip_forward = 1
```
When a client sends a packet addressed to an external destination (e.g. `8.8.8.8`):
1. The packet arrives on `ap0` with destination MAC matching `ap0`.
2. The kernel IP stack checks the destination IP. Since it is not local to the Pi, the kernel decrements the IP Time-To-Live (TTL) field.
3. The routing table selects `wlan0` as the egress interface via the default route.

### nftables NAT Table Architecture

NAT masquerading is executed via `nftables` in `/etc/nftables.conf`:

```text
table ip ina_nat {
    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        ip saddr 192.168.50.0/24 oifname "wlan0" masquerade
    }
}
```

### Translation Sequence

1. **SNAT (Source NAT / Masquerading):** Before the packet exits `wlan0`, the `postrouting` hook matches `ip saddr 192.168.50.0/24` and `oifname "wlan0"`. It rewrites the packet's source IP from `192.168.50.46` to `wlan0`'s currently assigned dynamic IP, mapping an ephemeral source port.
2. **Conntrack Session Tracking:** The Linux `conntrack` subsystem records the translation tuple:
   `(192.168.50.46:port_A <-> 8.8.8.8:port_B) === (wlan0_IP:port_C <-> 8.8.8.8:port_B)`
3. **Inbound Reply De-masquerading:** When reply packets arrive on `wlan0`, `conntrack` identifies the mapped connection, rewrites the destination IP from `wlan0_IP` back to `192.168.50.46`, and forwards the packet out `ap0`.

### Modular Inclusion vs. `flush ruleset`

Many common Linux NAT guides include `flush ruleset` at the beginning of `/etc/nftables.conf`. In INA v2, **`flush ruleset` is explicitly prohibited**. Flushing the ruleset wipes all existing tables, which destroys unrelated firewall policies, container bridges (Docker/Podman), and fail2ban filters. Using a dedicated namespace (`table ip ina_nat`) ensures safe, modular coexistence.

---

## 5. Device Discovery & Client Inspection Primitives

Administrators and automated INA tools inspect connected clients using two complementary sources of truth:

```text
┌─────────────────────────────────┐     ┌─────────────────────────────────┐
│     /var/lib/misc/dnsmasq.leases │     │    Kernel Neighbor Table (ARP)  │
│  • Records IP lease history     │     │  • Records active L2 reachability│
│  • Expiration timestamps        │     │  • States: REACHABLE, STALE, etc.│
│  • Client-advertised hostnames  │     │  • Hardware MAC bindings         │
└────────────────┬────────────────┘     └────────────────┬────────────────┘
                 │                                       │
                 └───────────────────┬───────────────────┘
                                     │ Correlated View
                         ┌───────────▼───────────┐
                         │   DiscoveredClient    │
                         │   (network/           │
                         │    device_discovery)  │
                         └───────────────────────┘
```

### 1. Kernel Neighbor Table (ARP Inspection)

Query link-layer reachability on the AP interface:
```bash
ip neigh show dev ap0
```

Observed test output:
```text
192.168.50.46 lladdr 5e:22:f6:20:9a:e7 REACHABLE
```

#### Neighbor States

- **`REACHABLE`:** The neighbor was validated as reachable within the reachability timeout (actively communicating or replied to recent ARP probe).
- **`STALE`:** The reachability timeout expired; the kernel will verify reachability upon the next outbound packet.
- **`DELAY` / `PROBE`:** The kernel is sending unicast ARP probes to revalidate link status.
- **`FAILED`:** No ARP reply was received after maximum retries; the host is considered offline.

### 2. DHCP Lease Table

Query active address allocations:
```bash
sudo cat /var/lib/misc/dnsmasq.leases
```

### 3. Comprehensive Host Network Inspection

INA v2 provides Python CLI commands to inspect both host interfaces and connected clients:

```bash
# Print complete network discovery report (interfaces, routes, wireless driver support)
python3 -m network.device_discovery

# Query connected clients (cross-referencing ARP neighbors and DHCP leases)
python3 -m network.device_discovery --clients

# Export discovery telemetry as structured JSON
python3 -m network.device_discovery --json
```

---

## 6. Local Connectivity vs. Internet Connectivity

Network diagnostics must strictly differentiate between **Local Link Reachability** and **External Internet Connectivity**:

```text
Client Device (Fedora)
      │
      │ [Hop 1: Local 802.11 Link]
      ▼
Raspberry Pi (ap0: 192.168.50.1)
      │
      │ [Hop 2: Wi-Fi STA Link to Hotspot]
      ▼
Phone Hotspot (Gateway)
      │
      │ [Hop 3: Mobile Carrier RAN / 4G/5G Cellular Link]
      ▼
Internet / WAN (8.8.8.8)
```

| Scope | Test Command | Monitored Path | Observed Baseline | Diagnostic Meaning |
| :--- | :--- | :--- | :--- | :--- |
| **Local Link** | `ping -c 20 192.168.50.1` | Client Wi-Fi NIC → `ap0` (Hop 1) | **0% packet loss**, 34.6 ms avg RTT | Verifies 802.11 association, WPA2 handshake, DHCP IP assignment, and local ARP resolution. |
| **Internet Path** | `ping -c 20 8.8.8.8` | Client → `ap0` → Kernel NAT → `wlan0` → Hotspot → Cellular Tower → Internet | **15% packet loss**, 210.6 ms avg RTT | Tests full routing pipeline including upstream cellular backhaul. High latency or loss here often reflects mobile network conditions rather than local AP failure. |

