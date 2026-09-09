# Report 06: Viva & Presentation Reference Guide

---

## 1. Executive Summary

This reference guide is specifically designed to prepare project team members for **academic presentations, live technical demonstrations, and viva examinations** on the **Intelligent Network Configuration Assistant (INA)** project.

It contains **30+ high-yield viva questions with precise answers**, a **live terminal diagnostic command cheatsheet**, a **step-by-step project presentation outline**, and **architectural defense strategies** focused on networking, Linux kernel mechanisms, MCP integration, and empirical validation.

---

## 2. Top Viva Questions & Technical Answers

### Section A: Docker & Linux Networking Infrastructure

#### Q1: What type of Docker network is used in INA, and how do containers communicate?
* **Answer**: INA uses a custom Docker **bridge network** named `network_project-net` with subnet `172.20.0.0/24` and gateway `172.20.0.1`. Containers communicate at **Layer 2 (Data Link Layer)** via virtual Ethernet pairs (`veth`) attached to the host bridge.

#### Q2: What is a `veth` pair and how does it work?
* **Answer**: A `veth` (virtual Ethernet) pair is a bidirectional Linux virtual link acting like a patch cable. One end (`eth0`) resides inside the container's network namespace (`netns`), and the other end (`vethXXXXXXX`) is attached to the host bridge interface (`network_project-net`).

#### Q3: Why is the `network-controller` container configured with `network_mode: host` and `privileged: true`?
* **Answer**: `host` network mode allows `network-controller` to bypass container namespace isolation and access the host's root network namespace directly. `privileged: true` grants `CAP_SYS_ADMIN` capabilities, allowing it to load kernel modules (`modprobe br_netfilter`) and execute `nftables` bridge filtering commands across the host's bridge interface.

#### Q4: Why is the `br_netfilter` kernel module necessary?
* **Answer**: By default, Linux bridges switch packets at Layer 2 without passing them to Layer 3 firewall hooks. Loading `br_netfilter` sets `net.bridge.bridge-nf-call-iptables = 1`, forcing bridged IPv4 packets to traverse netfilter hooks so firewall rules can inspect and drop them.

---

### Section B: Packet Filtering & Firewall Control (`nftables`)

#### Q5: Why did the project use `nftables` bridge hooks instead of standard `iptables` rules?
* **Answer**: Standard `iptables` operates at Layer 3 (IP forwarding). Bridged container-to-container traffic remains in Layer 2. `nftables` provides a dedicated `bridge` family table (`table bridge network_filter`) hooked directly to the L2 forward chain (`priority 0`), enabling fast $O(1)$ set lookups (`@blocked_clients`) directly on bridged Ethernet frames.

#### Q6: Explain the exact `nftables` rules created for client blocking.
* **Answer**:
  1. `add table bridge network_filter` — Creates an L2 bridge table.
  2. `add chain bridge network_filter forward { type filter hook forward priority 0; policy accept; }` — Hooks into the bridge forward path.
  3. `add set bridge network_filter blocked_clients { type ipv4_addr; }` — Defines an IP set.
  4. `add rule bridge network_filter forward ip saddr @blocked_clients drop` — Drops frames where source IP matches the set.
  5. `add rule bridge network_filter forward ip daddr @blocked_clients drop` — Drops frames where destination IP matches the set.

#### Q7: How does `unblock_client` restore network access?
* **Answer**: It executes `nft delete element bridge network_filter blocked_clients { <client_ip> }`, removing the target IP from the set. Sub-second frame processing resumes immediately without clearing or flushing other rules.

---

### Section C: Traffic Control & Bandwidth Shaping (`tc`)

#### Q8: What Linux queuing discipline (qdisc) is used for bandwidth throttling, and how does it operate?
* **Answer**: INA uses the **Token Bucket Filter (`tbf`)** qdisc. TBF accumulates tokens in a virtual bucket at a constant rate ($R$). Packets can only be transmitted if sufficient tokens exist in the bucket. Excess packets wait in a queue buffer up to a latency threshold ($L = 400\text{ms}$) before being dropped.

#### Q9: What is the exact `tc` command executed for bandwidth limiting?
* **Answer**:
  ```bash
  docker exec client1 tc qdisc replace dev eth0 root tbf rate 5mbit burst 32kbit latency 400ms
  ```

#### Q10: What is the purpose of the `burst` parameter in `tc tbf`?
* **Answer**: `burst 32kbit` defines the maximum size of the token bucket. It specifies the peak amount of data that can be transmitted instantaneously at unthrottled line speed before token replenishment limits the flow to the configured rate ($5\text{ Mbps}$).

---

### Section D: Validation Layer & Edge Cases

#### Q11: Explain the "Host Ping Bypass" bug and how it was solved.
* **Answer**: Pinging a blocked container from the host OS or `network-controller` uses Layer-3 host routing, bypassing the Layer-2 bridge forwarding hook and falsely reporting the client as reachable. Solved by `pick_source_container()`, which forces validation pings to originate from a sibling container (e.g. `client2`), routing traffic through the bridge where `nftables` drops it.

#### Q12: How is bandwidth limiting validated empirically?
* **Answer**: The system probes port `5201` on `server` to ensure `iperf3` is listening, then runs `docker exec client1 iperf3 -c 172.20.0.3 -t 5 -J`. It parses `end.sum_received.bits_per_second`, converts to Mbps, and checks if the measured rate falls within a $\pm 20\%$ tolerance window (`BANDWIDTH_TOLERANCE = 0.20`) of the requested rate.

#### Q13: What happens if an LLM API key is missing or OpenRouter fails?
* **Answer**: INA degrades gracefully using a deterministic fallback pipeline. The `IntentParser` catches LLM failures and invokes `regex_parse()`, which uses compiled regular expressions (`_BLOCK_RE`, `_LIMIT_RE`, etc.) to parse user intent with 0% cloud dependency.

---

## 3. Live Terminal Diagnostic Command Cheatsheet

During a live demo or viva, use these commands to demonstrate system state:

### 1. View Running Containers & Static IPs
```bash
docker compose -f network/docker-compose.yml ps
docker inspect -f '{{.Name}} -> {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' client1 client2 server
```

### 2. Inspect Active `nftables` Bridge Rules & Blocked Set
```bash
# View active bridge rules and blocked IP set elements
docker exec network-controller nft list ruleset
```
* **Expected Output when `client1` is blocked**:
  ```text
  table bridge network_filter {
      set blocked_clients {
          type ipv4_addr
          elements = { 172.20.0.2 }
      }
      chain forward {
          type filter hook forward priority filter; policy accept;
          ip saddr @blocked_clients drop
          ip daddr @blocked_clients drop
      }
  }
  ```

### 3. Inspect Active Traffic Control (`tc`) Qdisc Rules
```bash
# View active qdisc on client1 eth0
docker exec client1 tc qdisc show dev eth0

# View detailed queue statistics (packets dropped, bytes sent)
docker exec client1 tc -s qdisc show dev eth0
```

### 4. Perform Live `iperf3` Benchmark Manual Test
```bash
# Ensure iperf3 server daemon is running on server container
docker exec -d server iperf3 -s

# Run 5-second throughput benchmark from client1 to server
docker exec client1 iperf3 -c 172.20.0.3 -t 5
```

### 5. Inspect Audit Logs & Governance Decisions
```bash
# View raw JSONL log entries
cat policy/audit_log.jsonl

# Interactively query last logged decision explanation
python3 -c "from policy.audit_log import explain_action; print(explain_action())"
```

---

## 4. Step-by-Step Presentation Outline (10-Minute Guide)

```text
+-----------------------------------------------------------------------+
|  Slide 1: Title & Problem Statement (1 min)                           |
|  - Manual network configuration (iptables/tc) is risk-heavy & complex.|
|  - Solution: Intelligent Network Configuration Assistant (INA).       |
+-----------------------------------------------------------------------+
                                  │
                                  ▼
+-----------------------------------------------------------------------+
|  Slide 2: Architecture & Workflow (2 mins)                            |
|  - User Intent -> Policy Engine -> FastMCP -> Linux Kernel -> Validation.|
|  - Highlight zero-trust Policy Engine barrier.                        |
+-----------------------------------------------------------------------+
                                  │
                                  ▼
+-----------------------------------------------------------------------+
|  Slide 3: Network Topology & Kernel Enforcement (3 mins)              |
|  - Explain Docker bridge project-net (172.20.0.0/24) & veth pairs.    |
|  - Layer-2 nftables bridge set filtering (table bridge network_filter).|
|  - Traffic Control tc tbf egress shaping (rate, burst, latency).      |
+-----------------------------------------------------------------------+
                                  │
                                  ▼
+-----------------------------------------------------------------------+
|  Slide 4: Live Demonstration (3 mins)                                 |
|  - Demo 1: "Block client1" -> Show 100% loss & nft list ruleset.      |
|  - Demo 2: "Limit client1 to 5 Mbps" -> Show iperf3 benchmark output. |
|  - Demo 3: "Block server" -> Show Policy DENY refusal.                |
+-----------------------------------------------------------------------+
                                  │
                                  ▼
+-----------------------------------------------------------------------+
|  Slide 5: Conclusion & Future Scope (1 min)                           |
|  - Empirical validation (ping/iperf3) guarantees state convergence.    |
|  - Future: eBPF/XDP filtering, IFB ingress shaping, dynamic CNI.      |
+-----------------------------------------------------------------------+
```

---

## 5. Defense Strategies for Challenging Viva Questions

### Scenario 1: The examiner asks "Why didn't you write an eBPF program instead of using `nftables`?"
* **Defense Strategy**: Acknowledge that eBPF/XDP provides superior wire-speed performance, but explain that `nftables` bridge hooks were selected for INA because `nftables` is natively available across standard Linux kernels without requiring LLVM/Clang eBPF bytecode compilation toolchains inside lightweight containers. Mention eBPF as a planned future improvement (as detailed in Report 05).

### Scenario 2: The examiner asks "What happens if a container changes its IP address?"
* **Defense Strategy**: Explain that INA uses static IP assignments configured in [`network/docker-compose.yml`](file:///home/dhruv/Documents/ina/network/docker-compose.yml) to maintain strict mapping between hostnames (`client1`) and IP addresses (`172.20.0.2`). Note that integrating the Docker Engine API event stream (`docker.from_env()`) is the recommended resolution for dynamic DHCP environments.

### Scenario 3: The examiner asks "Why is your bandwidth validation tolerance set to 20%?"
* **Defense Strategy**: Explain that TCP congestion control (e.g. CUBIC/Reno) requires an initial slow-start phase to scale up congestion window size (`cwnd`). In short 5-second test runs, slow-start warmup overhead and Linux kernel qdisc scheduling granularity produce a slight variance around nominal rates, making $\pm 20\%$ an empirically sound tolerance window.
