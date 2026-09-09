import os
import sys

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )
)

import pytest
# pyrefly: ignore [missing-import]
from pydantic import ValidationError

from validation.models import ValidationRequest, ValidationResult
from validation import monitor


# ============================================================
# ValidationRequest Tests
# ============================================================

def test_valid_block_request():
    request = ValidationRequest(
        operation="block",
        client="client1"
    )

    assert request.operation == "block"
    assert request.client == "client1"
    assert request.rate is None


def test_valid_unblock_request():
    request = ValidationRequest(
        operation="unblock",
        client="client1"
    )

    assert request.operation == "unblock"
    assert request.client == "client1"
    assert request.rate is None


def test_valid_bandwidth_request():
    request = ValidationRequest(
        operation="bandwidth",
        client="client1",
        rate="10mbit"
    )

    assert request.operation == "bandwidth"
    assert request.client == "client1"
    assert request.rate == "10mbit"


def test_invalid_operation():
    with pytest.raises(ValidationError):
        ValidationRequest(
            operation="invalid",
            client="client1"
        )


def test_missing_bandwidth_rate():
    with pytest.raises(ValidationError):
        ValidationRequest(
            operation="bandwidth",
            client="client1"
        )


def test_invalid_rate():
    invalid_rates = [
        "10",
        "10mbps",
        "abcmbit",
        "-10mbit",
        "0mbit"
    ]

    for rate in invalid_rates:
        with pytest.raises(ValidationError):
            ValidationRequest(
                operation="bandwidth",
                client="client1",
                rate=rate
            )


def test_invalid_client():
    # ValidationRequest only validates the type/structure of client.
    # The actual client name is validated by monitor.get_ip().
    request = ValidationRequest(
        operation="block",
        client="invalid_client"
    )

    with pytest.raises(ValueError):
        monitor.get_ip(request.client)


# ============================================================
# ValidationResult Tests
# ============================================================

def test_valid_result():
    result = ValidationResult(
        operation="BLOCK",
        client="client1",
        target_ip="172.20.0.2",
        ping="100.0% packet loss",
        passed=True,
        message="client1 is successfully blocked."
    )

    assert result.operation == "BLOCK"
    assert result.client == "client1"
    assert result.target_ip == "172.20.0.2"
    assert result.ping == "100.0% packet loss"
    assert result.passed is True


def test_invalid_result():
    # BANDWIDTH results require expected_rate,
    # measured_rate, and tolerance.
    with pytest.raises(ValidationError):
        ValidationResult(
            operation="BANDWIDTH",
            client="client1",
            target_ip="172.20.0.2",
            passed=True,
            message="Bandwidth validation passed."
        )


# ============================================================
# Helper Function Tests
# ============================================================

def test_get_ip_valid_client():
    assert monitor.get_ip("client1") == "172.20.0.2"
    assert monitor.get_ip("client2") == "172.20.0.4"
    assert monitor.get_ip("server") == "172.20.0.3"


def test_get_ip_invalid_client():
    with pytest.raises(ValueError):
        monitor.get_ip("unknown_client")


def test_parse_rate_mbit():
    assert monitor.parse_rate("10mbit") == 10


def test_parse_rate_kbit():
    assert monitor.parse_rate("1000kbit") == 1


def test_parse_rate_gbit():
    assert monitor.parse_rate("1gbit") == 1000


def test_parse_rate_case_and_whitespace():
    assert monitor.parse_rate(" 10MBIT ") == 10


def test_bandwidth_within_tolerance():
    # Default tolerance is ±20%.
    assert monitor.bandwidth_within_tolerance(10, 10) is True
    assert monitor.bandwidth_within_tolerance(8, 10) is True
    assert monitor.bandwidth_within_tolerance(12, 10) is True


def test_bandwidth_outside_tolerance():
    assert monitor.bandwidth_within_tolerance(7, 10) is False
    assert monitor.bandwidth_within_tolerance(13, 10) is False


# ============================================================
# Network Validation Tests
# ============================================================

def test_block_ping_fails_pass():
    """
    Simulate a blocked client.

    100% packet loss means the target is unreachable,
    so check_block() should return PASS.
    """

    def mock_run_ping(source_container, target_ip):
        return {
            "reachable": False,
            "packet_loss": 100.0,
            "output": "3 packets transmitted, 0 received, 100% packet loss"
        }

    original_run_ping = monitor.run_ping
    monitor.run_ping = mock_run_ping

    try:
        result = monitor.check_block("client1")

        assert result.operation == "BLOCK"
        assert result.client == "client1"
        assert result.target_ip == "172.20.0.2"
        assert result.passed is True
        assert result.ping == "100.0% packet loss"

    finally:
        monitor.run_ping = original_run_ping


def test_unblock_ping_succeeds_pass():
    """
    Simulate an unblocked client.

    Packet loss below 100% means the target is reachable,
    so check_unblock() should return PASS.
    """

    def mock_run_ping(source_container, target_ip):
        return {
            "reachable": True,
            "packet_loss": 0.0,
            "output": "3 packets transmitted, 3 received, 0% packet loss"
        }

    original_run_ping = monitor.run_ping
    monitor.run_ping = mock_run_ping

    try:
        result = monitor.check_unblock("client1")

        assert result.operation == "UNBLOCK"
        assert result.client == "client1"
        assert result.target_ip == "172.20.0.2"
        assert result.passed is True
        assert result.ping == "0.0% packet loss"

    finally:
        monitor.run_ping = original_run_ping


def test_bandwidth_measured_rate_within_tolerance_pass():
    """
    Simulate iperf3 measuring a bandwidth value within
    the allowed ±20% tolerance.
    """

    def mock_run_iperf(client, server_ip, reverse=False):
        return 10.0

    original_run_iperf = monitor.run_iperf
    monitor.run_iperf = mock_run_iperf

    try:
        result = monitor.check_bandwidth(
            "client1",
            "10mbit"
        )

        assert result.operation == "BANDWIDTH"
        assert result.client == "client1"
        assert result.target_ip == "172.20.0.2"
        assert result.expected_rate == 10.0
        assert result.measured_rate == 10.0
        assert result.tolerance == 20.0
        assert result.passed is True

    finally:
        monitor.run_iperf = original_run_iperf


# ============================================================
# Additional Failure-Case Network Tests
# ============================================================

def test_block_ping_succeeds_fail():
    """
    If a supposedly blocked client is still reachable,
    the block validation should fail.
    """

    def mock_run_ping(source_container, target_ip):
        return {
            "reachable": True,
            "packet_loss": 0.0,
            "output": "3 packets transmitted, 3 received, 0% packet loss"
        }

    original_run_ping = monitor.run_ping
    monitor.run_ping = mock_run_ping

    try:
        result = monitor.check_block("client1")

        assert result.passed is False
        assert result.operation == "BLOCK"

    finally:
        monitor.run_ping = original_run_ping


def test_unblock_ping_fails():
    """
    If an supposedly unblocked client is still unreachable,
    the unblock validation should fail.
    """

    def mock_run_ping(source_container, target_ip):
        return {
            "reachable": False,
            "packet_loss": 100.0,
            "output": "3 packets transmitted, 0 received, 100% packet loss"
        }

    original_run_ping = monitor.run_ping
    monitor.run_ping = mock_run_ping

    try:
        result = monitor.check_unblock("client1")

        assert result.passed is False
        assert result.operation == "UNBLOCK"

    finally:
        monitor.run_ping = original_run_ping


def test_bandwidth_outside_tolerance_fail():
    """
    Simulate an iperf3 result outside the allowed ±20% range.
    """

    def mock_run_iperf(client, server_ip, reverse=False):
        return 15.0

    original_run_iperf = monitor.run_iperf
    monitor.run_iperf = mock_run_iperf

    try:
        result = monitor.check_bandwidth(
            "client1",
            "10mbit"
        )

        assert result.expected_rate == 10.0
        assert result.measured_rate == 15.0
        assert result.passed is False

    finally:
        monitor.run_iperf = original_run_iperf


# ============================================================
# Packet Loss Parsing Tests
# ============================================================

def test_parse_packet_loss():
    output = (
        "3 packets transmitted, 0 received, "
        "100% packet loss"
    )

    assert monitor.parse_packet_loss(output) == 100.0


def test_parse_packet_loss_partial():
    output = (
        "3 packets transmitted, 2 received, "
        "33.3% packet loss"
    )

    assert monitor.parse_packet_loss(output) == 33.3


def test_parse_packet_loss_missing():
    output = "ping failed"

    assert monitor.parse_packet_loss(output) is None


# ============================================================
# iperf3 Result Parsing Tests
# ============================================================

def test_parse_iperf_result():
    output = """
    {
        "end": {
            "sum_received": {
                "bits_per_second": 10000000
            }
        }
    }
    """

    result = monitor.parse_iperf_result(output)

    assert result == 10.0


def test_parse_invalid_iperf_result():
    output = '{"invalid": "data"}'

    with pytest.raises(RuntimeError):
        monitor.parse_iperf_result(output)


# ============================================================
# Source Container Selection Tests
# ============================================================

def test_pick_source_container_for_client1():
    source = monitor.pick_source_container("client1")

    assert source in ["server", "client2"]


def test_pick_source_container_for_client2():
    source = monitor.pick_source_container("client2")

    assert source in ["server", "client1"]


def test_pick_source_container_for_server():
    source = monitor.pick_source_container("server")

    assert source in ["client1", "client2"]


def test_pick_source_container_invalid_client():
    with pytest.raises(ValueError):
        monitor.pick_source_container("invalid_client")

