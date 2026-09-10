# Network Baseline

## Docker Network

* Network: `network_project-net`
* Subnet: `172.20.0.0/24`
* Gateway: `172.20.0.1`
* Driver: `bridge`

## Containers

| Container | IP Address   | Hostname  |
| --------- | ------------ | --------- |
| client1   | `172.20.0.2` | `client1` |
| server    | `172.20.0.3` | `server`  |
| client2   | `172.20.0.4` | `client2` |

## Connectivity

### client1 → server

```text
client1 (172.20.0.2)
        |
        | ICMP
        ↓
server (172.20.0.3)

Result: PASS
Packet loss: 0%
```

**Actual Ping Output:**

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

### client2 → server

```text
client2 (172.20.0.4)
        |
        | ICMP
        ↓
server (172.20.0.3)

Result: PASS
Packet loss: 0%
```

**Actual Ping Output:**

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

## Client1 Interface

Command:

```bash
ip addr
```

Expected network:

```text
172.20.0.2/24
```

**Actual Output:**

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

## Client2 Interface

Command:

```bash
ip addr
```

Expected network:

```text
172.20.0.4/24
```

**Actual Output:**

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

## Routing

Command:

```bash
ip route
```

Expected network route:

```text
172.20.0.0/24
```

Expected default gateway:

```text
172.20.0.1
```

**Actual Output:**

```text
default via 172.20.0.1 dev eth0 
172.20.0.0/24 dev eth0 proto kernel scope link src 172.20.0.2 
```

## Baseline Status

**PASS — basic container-to-container networking is working.**

No firewall rules or bandwidth shaping have been applied at this stage.
