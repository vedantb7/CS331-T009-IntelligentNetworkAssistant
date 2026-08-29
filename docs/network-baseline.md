# Network Baseline Verification

This document contains the verified network baseline measurements for the Intelligent Network Assistant (INA) environment, capturing the connectivity status, IP addressing, routing tables, and diagnostic outputs.

---

## 🌐 Network Specifications

* **Docker Network Name**: `network_project-net`
* **Subnet Range**: `172.20.0.0/24`
* **Default Gateway**: `172.20.0.1`
* **Driver Type**: `bridge`

---

## 📦 Container IP Map

| Container | Subnet IP | Hostname | Role |
| :--- | :--- | :--- | :--- |
| **`client1`** | `172.20.0.2` | `client1` | Network Client Node |
| **`server`** | `172.20.0.3` | `server` | HTTP/TCP Server Daemon |
| **`client2`** | `172.20.0.4` | `client2` | Network Client Node |

---

## ⚡ Connectivity Matrix & Verification

### 1. `client1` ➔ `server` (ICMP & TCP)
* **Status**: `PASS`
* **Packet Loss**: `0%`
* **Average RTT**: `0.044 ms`

**Ping Command Execution Output:**
```text
PING server (172.20.0.3) 56(84) bytes of data.
64 bytes from server.network_project-net (172.20.0.3): icmp_seq=1 ttl=64 time=0.031 ms
64 bytes from server.network_project-net (172.20.0.3): icmp_seq=2 ttl=64 time=0.053 ms
64 bytes from server.network_project-net (172.20.0.3): icmp_seq=3 ttl=64 time=0.039 ms
64 bytes from server.network_project-net (172.20.0.3): icmp_seq=4 ttl=64 time=0.053 ms

--- server ping statistics ---
4 packets transmitted, 4 received, 0% packet loss, time 3082ms
rtt min/avg/max/mdev = 0.031/0.044/0.053/0.009 ms
```

---

### 2. `client2` ➔ `server` (ICMP & TCP)
* **Status**: `PASS`
* **Packet Loss**: `0%`
* **Average RTT**: `0.055 ms`

**Ping Command Execution Output:**
```text
PING server (172.20.0.3) 56(84) bytes of data.
64 bytes from server.network_project-net (172.20.0.3): icmp_seq=1 ttl=64 time=0.030 ms
64 bytes from server.network_project-net (172.20.0.3): icmp_seq=2 ttl=64 time=0.092 ms
64 bytes from server.network_project-net (172.20.0.3): icmp_seq=3 ttl=64 time=0.048 ms
64 bytes from server.network_project-net (172.20.0.3): icmp_seq=4 ttl=64 time=0.052 ms

--- server ping statistics ---
4 packets transmitted, 4 received, 0% packet loss, time 3096ms
rtt min/avg/max/mdev = 0.030/0.055/0.092/0.022 ms
```

---

## ⚙️ Interfaces & Routing Outputs

### Client 1 (`client1`) Interfaces (`ip addr`)
```text
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
    inet6 ::1/128 scope host proto kernel_lo 
       valid_lft forever preferred_lft forever
2: eth0@if75: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default 
    link/ether ae:3b:f5:9e:6c:ac brd ff:ff:ff:ff:ff:ff link-netnsid 0
    inet 172.20.0.2/24 brd 172.20.0.255 scope global eth0
       valid_lft forever preferred_lft forever
```

### Client 2 (`client2`) Interfaces (`ip addr`)
```text
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
    inet6 ::1/128 scope host proto kernel_lo 
       valid_lft forever preferred_lft forever
2: eth0@if74: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default 
    link/ether 2a:64:78:2f:15:11 brd ff:ff:ff:ff:ff:ff link-netnsid 0
    inet 172.20.0.4/24 brd 172.20.0.255 scope global eth0
       valid_lft forever preferred_lft forever
```

### Routing Table (`ip route`)
Same routing configuration verified on both client nodes:
```text
default via 172.20.0.1 dev eth0 
172.20.0.0/24 dev eth0 proto kernel scope link src 172.20.0.2 
```

---

## 📊 Summary Status

* **Status**: `PASS`
* **Security & Traffic Control**: No firewall policies, traffic shaping, or packet dropping configured. This baseline serves as the raw network speed/routing benchmark.
