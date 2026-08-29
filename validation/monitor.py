import subprocess
import sys
import json

CLIENT_IPS = {
    "client1": "172.20.0.2",
    "client2": "172.20.0.4",
    "server": "172.20.0.3"
}

BANDWIDTH_TOLERANCE = 0.20


def get_ip(client):
    if client not in CLIENT_IPS:
        raise ValueError(f"unknown client: {client}")

    return CLIENT_IPS[client]

def run_ping(target_ip):
    result = subprocess.run(
        ["ping", "-c", "3", "-W", "1", "target_ip"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    return result.returncode == 0

def check_block(client):
    target_ip = get_ip(client)

    reachable = run_ping(target_ip)

    return {
        "operation": "BLOCK",
        "client": client,
        "target_ip": target_ip,
        "ping": "SUCCESS" if reachable else "FAILED",
        "passed": not reachable,
        "message": (f"{client} is still reachable" if reachable else f"{client} is successfully blocked")
    }


def check_unblock(client):
    target_ip = get_ip(client)

    reachable = run_ping(target_ip)

    return {
        "operation": "UNBLOCK",
        "client": client,
        "target_ip": target_ip,
        "ping": "SUCCESS" if reachable else "FAILED",
        "passed": reachable,
        "message": (f"{client} is successfully unblocked" if reachable else f"{client} is still unreachable")
    }

def print_result(result):
    """
    Display a human-readable validation result.
    """

    print(f"Validation: {result['operation']}")
    print(f"Target: {result['client']}")
    print(f"Target IP: {result['target_ip']}")
    print(f"Ping: {result['ping']}")

    if result["passed"]:
        print("Result: PASS")
    else:
        print("Result: FAIL")

    print(f"Message: {result['message']}")

def parse_rate(rate):
    """
    Convert a bandwidth rate into Mbps.

    Examples:
        5mbit  -> 5.0
        10mbit -> 10.0
        1gbit  -> 1000.0
    """

    rate = rate.lower().strip()

    if rate.endswith("gbit"):
        value = float(rate[:-4])
        return value * 1000

    if rate.endswith("mbit"):
        value = float(rate[:-4])
        return value

    if rate.endswith("kbit"):
        value = float(rate[:-4])
        return value / 1000

    raise ValueError(
        f"Invalid rate '{rate}'. "
        "Use formats such as 5mbit, 10mbit, or 1gbit."
    )

def bandwidth_within_tolerance(measured, expected):
    """
    Check whether measured bandwidth is within ±20%
    of the expected rate.
    """

    lower_limit = expected * (1 - BANDWIDTH_TOLERANCE)
    upper_limit = expected * (1 + BANDWIDTH_TOLERANCE)

    return lower_limit <= measured <= upper_limit

def run_iperf(client, server_ip):
    """
    Run an iperf3 test from the specified client.

    Returns:
        Measured bandwidth in Mbps.
    """

    result = subprocess.run(
        [
            "docker",
            "exec",
            client,
            "iperf3",
            "-c",
            server_ip,
            "-J"
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"iperf3 failed for {client}: {result.stderr.strip()}"
        )

    return parse_iperf_result(result.stdout)

def parse_iperf_result(output):
    """
    Extract the final throughput from iperf3 JSON output.

    Returns:
        Throughput in Mbps.
    """

    try:
        data = json.loads(output)

        bits_per_second = data["end"]["sum_received"]["bits_per_second"]

        return bits_per_second / 1_000_000

    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError(
            f"Unable to parse iperf3 output: {error}"
        )

def check_bandwidth(client, expected_rate):
    """
    Verify that the client's measured bandwidth
    is within the expected rate.
    """

    target_ip = get_ip(client)

    expected_mbps = parse_rate(expected_rate)

    # Change this to your actual server IP.
    server_ip = CLIENT_IPS["server"]

    measured_mbps = run_iperf(client, server_ip)

    passed = bandwidth_within_tolerance(
        measured_mbps,
        expected_mbps
    )

    if passed:
        message = "Bandwidth is within the expected limit."
    else:
        message = "Measured bandwidth is outside the expected range."

    return {
        "operation": "BANDWIDTH",
        "client": client,
        "target_ip": target_ip,
        "expected_rate": expected_mbps,
        "measured_rate": measured_mbps,
        "tolerance": BANDWIDTH_TOLERANCE * 100,
        "passed": passed,
        "message": message
    }


def print_result(result):
    """
    Display a human-readable validation result.
    """

    print(f"Validation: {result['operation']}")
    print(f"Target: {result['client']}")
    print(f"Target IP: {result['target_ip']}")

    if result["operation"] == "BANDWIDTH":

        print(
            f"Expected Rate: "
            f"{result['expected_rate']:.2f} Mbps"
        )

        print(
            f"Measured Rate: "
            f"{result['measured_rate']:.2f} Mbps"
        )

        print(
            f"Tolerance: "
            f"±{result['tolerance']:.0f}%"
        )

    else:
        print(f"Ping: {result['ping']}")

    if result["passed"]:
        print("Result: PASS")
    else:
        print("Result: FAIL")

    print(f"Message: {result['message']}")


def parse_iperf_result(output):
    """
    Extract throughput from iperf3 JSON output.

    Returns:
        float: measured throughput in Mbps
    """

    try:
        data = json.loads(output)

        bits_per_second = (
            data["end"]
                ["sum_received"]
                ["bits_per_second"]
        )

        return bits_per_second / 1_000_000

    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError(
            f"Unable to parse iperf3 output: {error}"
        )


def run_iperf(client, server_ip):
    """
    Run an iperf3 bandwidth test from a Docker client.
    """

    result = subprocess.run(
        [
            "docker",
            "exec",
            client,
            "iperf3",
            "-c",
            server_ip,
            "-J"
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"iperf3 failed: {result.stderr.strip()}"
        )

    return parse_iperf_result(result.stdout)


def parse_rate(rate):
    """
    Convert a rate such as 5mbit or 1gbit to Mbps.
    """

    rate = rate.lower().strip()

    if rate.endswith("gbit"):
        return float(rate[:-4]) * 1000

    if rate.endswith("mbit"):
        return float(rate[:-4])

    if rate.endswith("kbit"):
        return float(rate[:-4]) / 1000

    raise ValueError(
        f"Invalid rate '{rate}'. "
        "Use formats such as 5mbit, 10mbit or 1gbit."
    )

def bandwidth_within_tolerance(measured, expected):
    """
    Check whether measured bandwidth is within ±20%
    of the requested rate.
    """

    lower = expected * (1 - BANDWIDTH_TOLERANCE)
    upper = expected * (1 + BANDWIDTH_TOLERANCE)

    return lower <= measured <= upper

def check_bandwidth(client, expected_rate):
    """
    Validate that the client's measured bandwidth
    matches the requested bandwidth.
    """

    target_ip = get_ip(client)

    expected_mbps = parse_rate(expected_rate)

    server_ip = CLIENT_IPS["server"]

    measured_mbps = run_iperf(
        client,
        server_ip
    )

    passed = bandwidth_within_tolerance(
        measured_mbps,
        expected_mbps
    )

    if passed:
        message = (
            "Bandwidth is within the expected range."
        )
    else:
        message = (
            "Measured bandwidth is outside "
            "the expected range."
        )

    return {
        "operation": "BANDWIDTH",
        "client": client,
        "target_ip": target_ip,
        "expected_rate": expected_mbps,
        "measured_rate": measured_mbps,
        "tolerance": BANDWIDTH_TOLERANCE * 100,
        "passed": passed,
        "message": message
    }

def print_result(result):

    print(f"Validation: {result['operation']}")
    print(f"Target: {result['client']}")
    print(f"Target IP: {result['target_ip']}")

    if result["operation"] == "BANDWIDTH":

        print(
            f"Expected Rate: "
            f"{result['expected_rate']:.2f} Mbps"
        )

        print(
            f"Measured Rate: "
            f"{result['measured_rate']:.2f} Mbps"
        )

        print(
            f"Tolerance: "
            f"±{result['tolerance']:.0f}%"
        )

    else:

        print(f"Ping: {result['ping']}")

    print(
        "Result: "
        + ("PASS" if result["passed"] else "FAIL")
    )

    print(f"Message: {result['message']}")


def main():

    if len(sys.argv) < 3:
        print("Usage:")
        print("  python3 validation/monitor.py block <client>")
        print("  python3 validation/monitor.py unblock <client>")
        print(
            "  python3 validation/monitor.py "
            "bandwidth <client> <rate>"
        )
        sys.exit(1)

    operation = sys.argv[1].lower()
    client = sys.argv[2]

    try:

        if operation == "block":

            result = check_block(client)

        elif operation == "unblock":

            result = check_unblock(client)

        elif operation == "bandwidth":
            if len(sys.argv) < 4:
                print(
                    "Usage: "
                    "python3 validation/monitor.py "
                    "bandwidth <client> <rate>"
                )
            sys.exit(1)

            rate = sys.argv[3]

            result = check_bandwidth(client, rate)

        else:

            print(
                f"Error: Unknown operation "
                f"'{operation}'"
            )

            print(
                "Supported operations: "
                "block, unblock, bandwidth"
            )

            sys.exit(1)

        print_result(result)

    except (ValueError, RuntimeError) as error:

        print(f"Error: {error}")
        sys.exit(1)