# Validation Manual Testing Guide

This document outlines the validation component verification steps to ensure that the network state monitoring and validation logic is working correctly, safe from crashes, and robust.

---

## 1. Start Docker Environment
Boot the container topology:
```bash
docker compose -f network/docker-compose.yml up -d
```
Verify that all four containers (`client1`, `client2`, `server`, `network-controller`) are in `Up` status:
```bash
docker ps
```

---

## 2. Verify Validation Files
Ensure the validation suite is complete:
```bash
ls -la validation
```
**Expected files:**
* `models.py` (Pydantic model definitions)
* `monitor.py` (CLI validation orchestrator)

---

## 3. Verify Python Dependencies
Verify Pydantic is installed in your project virtual environment:
```bash
.venv/bin/python3 -c "import pydantic; print(pydantic.__version__)"
```
**Expected Output:** Version `2.x.x` (e.g. `2.13.5`).

---

## 4. Verify monitor.py CLI Startup
Execute the monitor without arguments to verify it starts without syntax errors:
```bash
.venv/bin/python3 validation/monitor.py
```
**Expected Output:** Exits with a usage description:
```text
Invalid request: Usage:
  python3 validation/monitor.py block <client>
  python3 validation/monitor.py unblock <client>
  python3 validation/monitor.py bandwidth <client> <rate>
```

---

## 5. Verify the validation CLI Help Output
Verify that help requests return the supported operations cleanly:
```bash
.venv/bin/python3 validation/monitor.py --help
```
**Expected Output:** Prints the usage details and exits with code 1.

---

## 6. Record Container IPs
Get the active IP address map:
```bash
docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' client1
docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' client2
docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' server
```
**Expected Output:** 
* `client1` = `172.20.0.2`
* `client2` = `172.20.0.4`
* `server` = `172.20.0.3`

---

## 7. Verify monitor.py Client Mappings
Verify that the `CLIENT_IPS` dictionary inside `validation/monitor.py` matches the active container network:
```bash
grep -A 5 "CLIENT_IPS =" validation/monitor.py
```

---

## 8. Test Basic Connectivity (Baseline)
Check the baseline reachability state before introducing policy modifications:
```bash
docker exec client2 ping -c 4 172.20.0.2
docker exec client1 ping -c 4 172.20.0.4
```
**Expected Output:** 0% packet loss (State: `CONNECTED`).

---

## 9. Test BLOCK Validation (Successful Case)
Manually block `client1` on the host forwarding and local INPUT paths, then validate using the monitor:
```bash
# Add block rules
docker exec network-controller iptables -I DOCKER-USER -s 172.20.0.2 -j DROP
docker exec network-controller iptables -I INPUT -s 172.20.0.2 -j DROP

# Check that client2 cannot reach client1
docker exec client2 ping -c 4 -W 1 172.20.0.2

# Validate using monitor
.venv/bin/python3 validation/monitor.py block client1
```
**Expected Output:**
```text
Validation: BLOCK
Target: client1
Target IP: 172.20.0.2
Ping: 100.0% packet loss
Result: PASS
Message: client1 is successfully blocked.
```

---

## 10. Test BLOCK Validation (Failure Case)
Remove the block rules to restore traffic, then verify the validator correctly reports a failure when asked to check a block:
```bash
# Remove block rules
docker exec network-controller iptables -D DOCKER-USER -s 172.20.0.2 -j DROP
docker exec network-controller iptables -D INPUT -s 172.20.0.2 -j DROP

# Run validation
.venv/bin/python3 validation/monitor.py block client1
```
**Expected Output:**
```text
Validation: BLOCK
Target: client1
Target IP: 172.20.0.2
Ping: 0.0% packet loss
Result: FAIL
Message: client1 is still reachable. Packet loss: 0.0%.
```

---

## 11. Test UNBLOCK Validation (Successful Case)
With no rules applied, run the unblock validator:
```bash
.venv/bin/python3 validation/monitor.py unblock client1
```
**Expected Output:**
```text
Validation: UNBLOCK
Target: client1
Target IP: 172.20.0.2
Ping: 0.0% packet loss
Result: PASS
Message: client1 is successfully unblocked.
```

---

## 12. Test UNBLOCK Validation (Failure Case)
Manually apply the block rules again, and check if the unblock validator reports a failure:
```bash
# Apply block rules
docker exec network-controller iptables -I DOCKER-USER -s 172.20.0.2 -j DROP
docker exec network-controller iptables -I INPUT -s 172.20.0.2 -j DROP

# Run validation
.venv/bin/python3 validation/monitor.py unblock client1
```
**Expected Output:**
```text
Validation: UNBLOCK
Target: client1
Target IP: 172.20.0.2
Ping: 100.0% packet loss
Result: FAIL
Message: client1 is still unreachable.
```

---

## 13. Restore Network State
Remove the block rules to reset the environment:
```bash
docker exec network-controller iptables -D DOCKER-USER -s 172.20.0.2 -j DROP
docker exec network-controller iptables -D INPUT -s 172.20.0.2 -j DROP
```

---

## 14. Start the iperf3 Server
Check if the `iperf3` server is listening on port `5201` inside the `server` container. Start it in the background if necessary:
```bash
docker exec server ss -lntp | grep 5201 || docker exec -d server iperf3 -s
```

---

## 15. Establish Bandwidth Baseline
Measure unrestricted bandwidth twice to determine the baseline variation:
```bash
docker exec client1 iperf3 -c 172.20.0.3 -t 10
docker exec client1 iperf3 -c 172.20.0.3 -t 10
```

---

## 16. Manually Apply a Bandwidth Limit
Apply a 5 Mbps limit to `client1`'s egress interface (`eth0`):
```bash
docker exec client1 tc qdisc replace dev eth0 root tbf rate 5mbit burst 32kbit latency 400ms
```
Verify the config:
```bash
docker exec client1 tc qdisc show dev eth0
```

---

## 17. Test Bandwidth Validation (Successful Case)
Validate the 5 Mbps limit with the validation tool:
```bash
.venv/bin/python3 validation/monitor.py bandwidth client1 5mbit
```
**Expected Output:**
```text
Validation: BANDWIDTH
Target: client1
Target IP: 172.20.0.2
Expected Rate: 5.00 Mbps
Measured Rate: <value between 4.0 and 6.0> Mbps
Tolerance: ±20%
Result: PASS
Message: Bandwidth is within the expected range.
```

---

## 18. Test Bandwidth Validation (Failure Case)
Request validation using an incorrect expected rate (e.g. 100 Mbps) to verify the comparison:
```bash
.venv/bin/python3 validation/monitor.py bandwidth client1 100mbit
```
**Expected Output:**
```text
Validation: BANDWIDTH
Target: client1
Target IP: 172.20.0.2
Expected Rate: 100.00 Mbps
Measured Rate: <value around 5.0> Mbps
Tolerance: ±20%
Result: FAIL
Message: Measured bandwidth is outside the expected range.
```

---

## 19. Test Tolerance Boundary Calculation
Verify unit conversion and tolerance ranges (±20%) by running a mock assertion script:
```bash
.venv/bin/python3 -c '
from validation.monitor import bandwidth_within_tolerance
assert bandwidth_within_tolerance(4.8, 5.0) == True
assert bandwidth_within_tolerance(3.0, 5.0) == False
assert bandwidth_within_tolerance(4.0, 5.0) == True
assert bandwidth_within_tolerance(6.0, 5.0) == True
print("Tolerance check: PASS")
'
```

---

## 20. Verify ValidationRequest Constraints (Invalid Requests)
Ensure input parameters are strictly validated by Pydantic:
```bash
# 1. Invalid operation type
.venv/bin/python3 validation/monitor.py something client1

# 2. Missing bandwidth rate
.venv/bin/python3 validation/monitor.py bandwidth client1

# 3. Invalid rate unit
.venv/bin/python3 validation/monitor.py bandwidth client1 abc
```
**Expected Output:** All commands must exit with code `1` and show a clean Pydantic ValidationError message starting with `Invalid request:`.

---

## 21. Verify ValidationResult Constraints
Run a Python snippet to ensure `ValidationResult` raises a `ValidationError` if necessary fields are missing for a bandwidth test:
```bash
.venv/bin/python3 -c '
from validation.models import ValidationResult
try:
    # Omit expected_rate, measured_rate, and tolerance
    ValidationResult(operation="BANDWIDTH", client="client1", target_ip="172.20.0.2", passed=True, message="test")
    print("Failed to raise error!")
except Exception as e:
    print(f"Success: {type(e).__name__} raised.")
'
```
**Expected Output:** Prints `Success: ValidationError raised.`.

---

## 22. Test Command Failure Handling (Nonexistent Client)
Verify that unknown clients are handled gracefully:
```bash
.venv/bin/python3 validation/monitor.py block client99
```
**Expected Output:**
```text
Invalid request: Unknown client: client99
```

---

## 23. Test Ping Timeout Handling
Ensure that the validator doesn't hang if a client is blocked:
```bash
# Block client1
docker exec network-controller iptables -I DOCKER-USER -s 172.20.0.2 -j DROP
docker exec network-controller iptables -I INPUT -s 172.20.0.2 -j DROP

# Check validation execution time
time .venv/bin/python3 validation/monitor.py unblock client1
```
**Expected Output:** The script must exit in $\approx 3$ seconds and report `Result: FAIL`.
```bash
# Cleanup
docker exec network-controller iptables -D DOCKER-USER -s 172.20.0.2 -j DROP
docker exec network-controller iptables -D INPUT -s 172.20.0.2 -j DROP
```

---

## 24. Test iperf3 Failure Handling
Ensure that iperf3 server connection failures are captured without tracebacks:
```bash
# Kill server iperf3
docker exec server pkill iperf3

# Test validation
.venv/bin/python3 validation/monitor.py bandwidth client1 5mbit
```
**Expected Output:** Exits cleanly showing: `Invalid request: iperf3 failed:`.
```bash
# Restore iperf3 server
docker exec -d server iperf3 -s
```

---

## 25. Complete Network Cleanup
Clear all rules and queue disciplines to return the network to its default state:
```bash
docker exec network-controller iptables -D DOCKER-USER -s 172.20.0.2 -j DROP 2>/dev/null || true
docker exec network-controller iptables -D INPUT -s 172.20.0.2 -j DROP 2>/dev/null || true
docker exec client1 tc qdisc del dev eth0 root 2>/dev/null || true

# Final connectivity verification
docker exec client1 ping -c 4 172.20.0.4
docker exec client2 ping -c 4 172.20.0.2
```
**Expected Output:** All pings succeed with 0% packet loss.
