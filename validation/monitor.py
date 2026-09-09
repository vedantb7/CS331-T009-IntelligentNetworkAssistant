# imports
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import subprocess
import json
from validation.models import ValidationRequest, ValidationResult
from network.discovery import get_container_ip, list_known_clients

import re

# initialized variables
PING_COUNT = 3 # no. of pings to perform in each ping command
PING_TIMEOUT = 1 # ping timeout for the ping cmd
PING_REQUIRED_SUCCESS = 2 #     

BANDWIDTH_TEST_DURATION = 5
BANDWIDTH_TOLERANCE = 0.20

def get_ip(client):
    try:
        return get_container_ip(client)
    except (ValueError, RuntimeError) as err:
        raise ValueError(f"Unknown client: {client}")

def pick_source_container(target_client):
    """
    Choose another container on the network to ping FROM.

    This matters more than it looks: block_client()/unblock_client() insert
    rules into the DOCKER-USER chain, which is a FORWARD-chain hook — it
    only inspects traffic being routed *between* networks/containers. A
    ping issued directly from the host (or from a host-networked container
    like network-controller) is host-originated OUTPUT traffic and never
    passes through DOCKER-USER at all, so it would report "reachable"
    regardless of whether the block actually worked. Pinging from one
    container to another forces the traffic across the bridge, where the
    block rule is actually enforced — the same reason check_bandwidth()
    already runs iperf3 via `docker exec` instead of from the host.
    """
    # Verify target container IP / existence
    _ = get_ip(target_client)

    try:
        known = list_known_clients()
    except Exception:
        known = {}

    candidates = ["server", "client1", "client2"]
    for c in known.keys():
        if c not in candidates:
            candidates.append(c)

    for candidate in candidates:
        if candidate != target_client:
            try:
                get_container_ip(candidate)
                return candidate
            except Exception:
                continue
    raise ValueError(f"No available source container to ping from for target '{target_client}'.")


def run_ping(source_container, target_ip):
    result = subprocess.run(
        [
            "docker",
            "exec",
            source_container,
            "ping",
            "-c",
            str(PING_COUNT),
            "-W",
            str(PING_TIMEOUT),
            target_ip,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    output = result.stdout + result.stderr

    packet_loss = parse_packet_loss(output)

    return {
        "reachable": result.returncode == 0,
        "packet_loss": packet_loss,
        "output": output,
    }

def check_block(client):
    target_ip = get_ip(client)
    source = pick_source_container(client)

    ping_result = run_ping(source, target_ip)

    packet_loss = ping_result["packet_loss"]

    if packet_loss is None:
        return ValidationResult(
            operation="BLOCK",
            client=client,
            target_ip=target_ip,
            ping="UNKNOWN",
            passed=False,
            message="Unable to determine packet loss.",
        )

    passed = packet_loss >= 100.0

    if passed:
        message = (
            f"{client} is successfully blocked."
        )
    else:
        message = (
            f"{client} is still reachable. "
            f"Packet loss: {packet_loss:.1f}%."
        )

    return ValidationResult(
        operation="BLOCK",
        client=client,
        target_ip=target_ip,
        ping=(
            f"{packet_loss:.1f}% packet loss"
        ),
        passed=passed,
        message=message,
    )

def check_unblock(client):
    target_ip = get_ip(client)
    source = pick_source_container(client)

    ping_result = run_ping(source, target_ip)

    packet_loss = ping_result["packet_loss"]

    if packet_loss is None:
        return ValidationResult(
            operation="UNBLOCK",
            client=client,
            target_ip=target_ip,
            ping="UNKNOWN",
            passed=False,
            message="Unable to determine packet loss.",
        )

    passed = packet_loss < 100.0

    if passed:
        message = (
            f"{client} is successfully unblocked."
        )
    else:
        message = (
            f"{client} is still unreachable."
        )

    return ValidationResult(
        operation="UNBLOCK",
        client=client,
        target_ip=target_ip,
        ping=(
            f"{packet_loss:.1f}% packet loss"
        ),
        passed=passed,
        message=message,
    )

def parse_rate(rate):
    rate = rate.lower().strip()
    unit = next((u for u in ("kbit", "mbit", "gbit") if rate.endswith(u)), None)
    if unit is None:
        raise ValueError(f"Unsupported rate unit: {rate}")
    value = float(rate[: -len(unit)])

    if unit == "gbit":
        return value * 1000
    if unit == "mbit":
        return value
    if unit == "kbit":
        return value / 1000

    raise ValueError(f"Unsupported rate unit: {unit}")

def bandwidth_within_tolerance(measured, expected):
    lower_limit = expected * (1 - BANDWIDTH_TOLERANCE)
    upper_limit = expected * (1 + BANDWIDTH_TOLERANCE)
    return lower_limit <= measured <= upper_limit

def parse_iperf_result(output):
    try:
        data = json.loads(output)
        end = data["end"]
        summary = end.get("sum_received") or end.get("sum_sent") or end.get("sum")
        bits_per_second = summary["bits_per_second"]
        return bits_per_second / 1_000_000
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError(f"Unable to parse iperf3 output: {error}")

def ensure_iperf_server():
    probe = subprocess.run(
        ["docker", "exec", "server", "sh", "-c", "ss -lnt 2>/dev/null | grep -q ':5201 '"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if probe.returncode == 0:
        return
    subprocess.run(
        ["docker", "exec", "-d", "server", "iperf3", "-s"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

def run_iperf(client, server_ip):
    ensure_iperf_server()
    result = subprocess.run(
        [
            "docker",
            "exec",
            client,
            "iperf3",
            "-c",
            server_ip,
            "-t",
            str(BANDWIDTH_TEST_DURATION),
            "-J",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"iperf3 failed: {(result.stderr or result.stdout).strip()}")
    return parse_iperf_result(result.stdout)

def check_bandwidth(client, expected_rate):
    target_ip = get_ip(client)
    expected_mbps = parse_rate(expected_rate)
    server_ip = get_ip("server")
    measured_mbps = run_iperf(client, server_ip)

    passed = bandwidth_within_tolerance(measured_mbps, expected_mbps)

    if passed:
        message = "Bandwidth is within the expected range."
    else:
        message = "Measured bandwidth is outside the expected range."

    return ValidationResult(
        operation="BANDWIDTH",
        client=client,
        target_ip=target_ip,
        expected_rate=expected_mbps,
        measured_rate=measured_mbps,
        tolerance=BANDWIDTH_TOLERANCE * 100,
        passed=passed,
        message=message
    )

def print_result(result: ValidationResult):
    print(f"Validation: {result.operation}")
    print(f"Target: {result.client}")
    print(f"Target IP: {result.target_ip}")

    if result.operation == "BANDWIDTH":
        print(f"Expected Rate: {result.expected_rate:.2f} Mbps")
        print(f"Measured Rate: {result.measured_rate:.2f} Mbps")
        print(f"Tolerance: ±{result.tolerance:.0f}%")
    else:
        print(f"Ping: {result.ping}")

    print("Result: " + ("PASS" if result.passed else "FAIL"))
    print(f"Message: {result.message}")

def parse_packet_loss(output):
    match = re.search(
        r"(\d+(?:\.\d+)?)%\s*packet loss",
        output,
    )

    if not match:
        return None

    return float(match.group(1))

def main():
    try:
        if len(sys.argv) < 3:
            raise ValueError(
                "Usage:\n"
                "  python3 validation/monitor.py block <client>\n"
                "  python3 validation/monitor.py unblock <client>\n"
                "  python3 validation/monitor.py bandwidth <client> <rate>"
            )

        operation = sys.argv[1].lower()
        client = sys.argv[2]
        rate = None

        if operation == "bandwidth":
            if len(sys.argv) < 4:
                raise ValueError("Bandwidth validation requires a rate.")
            rate = sys.argv[3]
        elif len(sys.argv) >= 4:
            rate = sys.argv[3]

        # Validate using Pydantic ValidationRequest
        request = ValidationRequest(
            operation=operation,
            client=client,
            rate=rate
        )

        if request.operation == "block":
            result = check_block(request.client)
        elif request.operation == "unblock":
            result = check_unblock(request.client)
        elif request.operation == "bandwidth":
            result = check_bandwidth(request.client, request.rate)
        else:
            raise ValueError(f"Unknown operation: {request.operation}")

        print_result(result)

    except Exception as error:
        print(f"Invalid request: {error}")
        sys.exit(1)

if __name__ == "__main__":
    main()