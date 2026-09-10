# Manual Network Testing Guide

This document outlines the step-by-step procedure for verifying the network control utilities (firewall blocking/unblocking and bandwidth throttling) inside the Intelligent Network Assistant Docker environment.

---

## Prerequisites & Requirements
To intercept Layer 2 bridged traffic on the custom Docker network from the host's `iptables` chains:
1. The `network-controller` container must mount `/lib/modules` from the host:
   ```yaml
   volumes:
     - /lib/modules:/lib/modules:ro
   ```
2. The `br_netfilter` kernel module must be loaded to enable bridge-level traffic routing through host `iptables`:
   ```bash
   docker exec network-controller modprobe br_netfilter
   ```

---

## Step-by-Step Test Procedure

### 0. Start the Environment
Boot all the containers in detached mode from the project root:
```bash
docker compose -f network/docker-compose.yml up -d
```
**Verification Command:**
```bash
docker ps
```
**Expected Output:** Four containers (`server`, `client1`, `client2`, and `network-controller`) should be in the `Up` status.

---

### 1. Verify Docker Network
Locate and inspect the project network name:
```bash
docker network ls
docker network inspect network_project-net
```
**Expected Output:** The inspect details should list `server`, `client1`, and `client2` connected to the `172.20.0.0/24` subnet. (Note: `network-controller` runs in host network mode, so it won't appear on this bridge).

---

### 2. Find All IP Addresses
Query the IP address configuration of each container:
```bash
docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' server
docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' client1
docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' client2
```
**Expected IPs:**
* `server` = `172.20.0.3`
* `client1` = `172.20.0.2`
* `client2` = `172.20.0.4`

---

### 3. Verify Interfaces and Routes
Ensure interfaces and routing configurations inside the containers match the subnet specifications:
```bash
docker exec client1 ip addr && docker exec client1 ip route
docker exec client2 ip addr && docker exec client2 ip route
docker exec server ip addr && docker exec server ip route
```
**Expected Output:** Each container should have a default gateway (`172.20.0.1`) and `eth0` configured with the corresponding IP on the `172.20.0.0/24` subnet.

---

### 4. Baseline Connectivity
Verify that ping requests flow normally between all nodes without packets dropping:
```bash
docker exec client1 ping -c 4 172.20.0.3
docker exec client2 ping -c 4 172.20.0.3
docker exec client1 ping -c 4 172.20.0.4
docker exec client2 ping -c 4 172.20.0.2
```
**Expected Output:** 100% packet transmission, 0% packet loss for all ping tests.

---

### 5. Verify iptables Availability
Ensure that the `iptables` tool is present and query the default chains on the controller:
```bash
docker exec network-controller iptables --version
docker exec network-controller iptables -L FORWARD -n -v --line-numbers
```
**Expected Output:** `iptables v1.8.11 (nf_tables)` should be returned. The `FORWARD` chain should show docker rules forwarding traffic to `DOCKER-USER` and `DOCKER-FORWARD`.

---

### 6. Test iptables BLOCK Manually
Verify first that `client2` can reach `client1`, then apply a DROP rule for `client1`'s IP:
```bash
# Check reachability (expected 0% loss)
docker exec client2 ping -c 4 172.20.0.2

# Apply the BLOCK rule
docker exec network-controller iptables -I DOCKER-USER -s 172.20.0.2 -j DROP

# Check the DOCKER-USER chain
docker exec network-controller iptables -L DOCKER-USER -n -v --line-numbers
```
**Expected Output:** The DROP rule with source `172.20.0.2` should appear at line 1 in the `DOCKER-USER` chain.

---

### 7. Verify That the Block Actually Works
Ensure the `br_netfilter` module is loaded on the host, and attempt to send traffic from the blocked client:
```bash
# Load br_netfilter (requires /lib/modules mount)
docker exec network-controller modprobe br_netfilter

# Send ping from blocked client (expected 100% packet loss)
docker exec client1 ping -c 4 -W 1 172.20.0.4

# Check packet counters
docker exec network-controller iptables -L DOCKER-USER -n -v --line-numbers
```
**Expected Output:** The ping from `client1` should fail (100% packet loss). The packet counter for the DROP rule should increase to **4 packets (336 bytes)**.

---

### 8. Test iptables UNBLOCK
Remove the block rule and verify communication is fully restored:
```bash
# Delete the rule
docker exec network-controller iptables -D DOCKER-USER 1

# Check the chain (expected empty)
docker exec network-controller iptables -L DOCKER-USER -n -v --line-numbers

# Verify connectivity (expected 0% packet loss)
docker exec client1 ping -c 4 172.20.0.4
```
**Expected Output:** The rule is successfully deleted, and client-to-client ping has 0% packet loss.

---

### 9. Verify tc Availability
Confirm that `tc` (Traffic Control) is present in the target container:
```bash
docker exec client1 tc -V
docker exec client1 tc qdisc show dev eth0
```
**Expected Output:** `tc utility, iproute2-6.15.0` should be returned. The initial queuing discipline (`qdisc`) should display as `noqueue`.

---

### 10. Establish Bandwidth Baseline
Start `iperf3` server, then measure baseline throughput:
```bash
# Start server in background (if not already running)
docker exec -d server iperf3 -s

# Run benchmark from client1
docker exec client1 iperf3 -c 172.20.0.3 -t 10
```
**Expected Output:** High throughput corresponding to the local virtual bridge speed (typically >10 Gbps / >10,000 Mbps).

---

### 11. Apply a tc Bandwidth Limit
Apply a custom token bucket filter (`tbf`) queuing discipline restricting egress traffic to **5 Mbps**:
```bash
docker exec client1 tc qdisc replace dev eth0 root tbf rate 5mbit burst 32kbit latency 400ms
```
**Expected Output:** The command exits with code `0`.

---

### 12. Verify the tc Configuration
Ensure the queuing discipline has been correctly set on `eth0` inside the container:
```bash
docker exec client1 tc qdisc show dev eth0
```
**Expected Output:** Output should show `qdisc tbf ... rate 5Mbit burst 4Kb lat 400ms`.

---

### 13. Verify Actual Bandwidth
Run `iperf3` from the limited container to verify traffic throttling:
```bash
docker exec client1 iperf3 -c 172.20.0.3 -t 10
```
**Expected Output:** Throughput should be throttled near the expected limit (measured rate should be $\approx 4.5\text{--}5.5\text{ Mbps}$).

---

### 14. Test a Different Bandwidth
Replace the queuing discipline rate parameter with a new limit (e.g. **10 Mbps**):
```bash
docker exec client1 tc qdisc replace dev eth0 root tbf rate 10mbit burst 32kbit latency 400ms
docker exec client1 iperf3 -c 172.20.0.3 -t 10
```
**Expected Output:** Measured bandwidth should dynamically change to match the new rate ($\approx 9.0\text{--}10.5\text{ Mbps}$).

---

### 15. Remove the tc Restriction
Delete the root queuing discipline on the client's interface:
```bash
docker exec client1 tc qdisc del dev eth0 root
docker exec client1 tc qdisc show dev eth0
docker exec client1 iperf3 -c 172.20.0.3 -t 10
```
**Expected Output:** Queuing discipline returns to `noqueue`, and `iperf3` throughput goes back to the high baseline (>10 Gbps).

---

### 16. Final Connectivity Test
Verify that normal network operations are restored and all hosts can communicate:
```bash
docker exec client1 ping -c 4 172.20.0.3
docker exec client2 ping -c 4 172.20.0.3
docker exec client1 ping -c 4 172.20.0.4
docker exec client2 ping -c 4 172.20.0.2
```
**Expected Output:** 100% success rate and 0% packet loss.

---

### 17. Final Container Check
Confirm that all containers remain running and healthy:
```bash
docker ps
docker compose -f network/docker-compose.yml ps
```
**Expected Output:** All 4 services are in `Up` status.
