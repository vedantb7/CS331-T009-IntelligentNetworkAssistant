import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import subprocess
import json
from validation.models import ValidationRequest, ValidationResult

CLIENT_IPS = {
    "client1": "172.20.0.2",
    "client2": "172.20.0.4",
    "server": "172.20.0.3"
}

BANDWIDTH_TOLERANCE = 0.20

def get_ip(client):
    if client not in CLIENT_IPS:
        raise ValueError(f"Unknown client: {client}")
    return CLIENT_IPS[client]

def run_ping(target_ip):
    result = subprocess.run(
        ["ping", "-c", "3", "-W", "1", target_ip],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    return result.returncode == 0

def check_block(client):
    target_ip = get_ip(client)
    reachable = run_ping(target_ip)

    if reachable:
        return ValidationResult(
            operation="BLOCK",
            client=client,
            target_ip=target_ip,
            ping="SUCCESS",
            passed=False,
            message=f"{client} is still reachable."
        )
    else:
        return ValidationResult(
            operation="BLOCK",
            client=client,
            target_ip=target_ip,
            ping="FAILED",
            passed=True,
            message=f"{client} is successfully blocked."
        )

def check_unblock(client):
    target_ip = get_ip(client)
    reachable = run_ping(target_ip)

    if reachable:
        return ValidationResult(
            operation="UNBLOCK",
            client=client,
            target_ip=target_ip,
            ping="SUCCESS",
            passed=True,
            message=f"{client} is successfully unblocked."
        )
    else:
        return ValidationResult(
            operation="UNBLOCK",
            client=client,
            target_ip=target_ip,
            ping="FAILED",
            passed=False,
            message=f"{client} is still unreachable."
        )

def parse_rate(rate):
    rate = rate.lower().strip()
    value = float(rate[:-4])
    unit = rate[-4:]

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
        bits_per_second = data["end"]["sum_received"]["bits_per_second"]
        return bits_per_second / 1_000_000
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError(f"Unable to parse iperf3 output: {error}")

def run_iperf(client, server_ip):
    result = subprocess.run(
        ["docker", "exec", client, "iperf3", "-c", server_ip, "-J"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"iperf3 failed: {result.stderr.strip()}")
    return parse_iperf_result(result.stdout)

def check_bandwidth(client, expected_rate):
    target_ip = get_ip(client)
    expected_mbps = parse_rate(expected_rate)
    server_ip = CLIENT_IPS["server"]
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