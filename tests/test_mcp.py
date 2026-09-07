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
from unittest.mock import patch

from mcp_server import server

# ============================================================
# block()
# ============================================================

def test_block_calls_block_client():
    expected_response = {
        "status": "success",
        "action": "block_client",
        "client": "client1",
        "message": "Client client1 blocked successfully."
    }

    with patch("mcp_server.server.block_client", return_value=expected_response) as mock_block:
        result = server.block("client1")

        mock_block.assert_called_once_with("client1")
        assert result == expected_response


def test_block_returns_failure_response():
    failure_response = {
        "status": "failure",
        "action": "block_client",
        "client": "invalid_client",
        "message": "Client invalid_client not found in the network."
    }

    with patch("mcp_server.server.block_client", return_value=failure_response) as mock_block:
        result = server.block("invalid_client")

        mock_block.assert_called_once_with("invalid_client")
        assert result == failure_response
        assert result["status"] == "failure"


# ============================================================
# unblock()
# ============================================================

def test_unblock_calls_unblock_client():
    expected_response = {
        "status": "success",
        "action": "unblock_client",
        "client": "client1",
        "message": "Client client1 unblocked successfully."
    }

    with patch(
        "mcp_server.server.unblock_client",
        return_value=expected_response
    ) as mock_unblock:

        result = server.unblock("client1")

        mock_unblock.assert_called_once_with("client1")
        assert result == expected_response


def test_unblock_returns_failure_response():
    failure_response = {
        "status": "failure",
        "action": "unblock_client",
        "client": "invalid_client",
        "message": "Client invalid_client not found in the network."
    }

    with patch(
        "mcp_server.server.unblock_client",
        return_value=failure_response
    ) as mock_unblock:

        result = server.unblock("invalid_client")

        mock_unblock.assert_called_once_with("invalid_client")
        assert result == failure_response
        assert result["status"] == "failure"


# ============================================================
# limit()
# ============================================================

def test_limit_calls_limit_bandwidth():
    expected_response = {
        "status": "success",
        "action": "limit_bandwidth",
        "client": "client1",
        "rate": "10mbit",
        "message": "Bandwidth limited to 10mbit successfully."
    }

    with patch(
        "mcp_server.server.limit_bandwidth",
        return_value=expected_response
    ) as mock_limit:

        result = server.limit("client1", "10mbit")

        mock_limit.assert_called_once_with("client1", "10mbit")
        assert result == expected_response


def test_limit_returns_failure_response():
    failure_response = {
        "status": "failure",
        "action": "limit_bandwidth",
        "client": "invalid_client",
        "rate": "10mbit",
        "message": "Unknown client: invalid_client."
    }

    with patch(
        "mcp_server.server.limit_bandwidth",
        return_value=failure_response
    ) as mock_limit:

        result = server.limit("invalid_client", "10mbit")

        mock_limit.assert_called_once_with(
            "invalid_client",
            "10mbit"
        )

        assert result == failure_response
        assert result["status"] == "failure"


# ============================================================
# Network command failure propagation
# ============================================================

def test_block_propagates_network_failure():
    failure_response = {
        "status": "failure",
        "action": "block_client",
        "client": "client1",
        "message": "iptables command failed."
    }

    with patch(
        "mcp_server.server.block_client",
        return_value=failure_response
    ):

        result = server.block("client1")

        assert result["status"] == "failure"
        assert result["message"] == "iptables command failed."


def test_unblock_propagates_network_failure():
    failure_response = {
        "status": "failure",
        "action": "unblock_client",
        "client": "client1",
        "message": "iptables rule could not be removed."
    }

    with patch(
        "mcp_server.server.unblock_client",
        return_value=failure_response
    ):

        result = server.unblock("client1")

        assert result["status"] == "failure"
        assert result["message"] == "iptables rule could not be removed."


def test_limit_propagates_network_failure():
    failure_response = {
        "status": "failure",
        "action": "limit_bandwidth",
        "client": "client1",
        "rate": "10mbit",
        "message": "tc command failed."
    }

    with patch(
        "mcp_server.server.limit_bandwidth",
        return_value=failure_response
    ):

        result = server.limit("client1", "10mbit")

        assert result["status"] == "failure"
        assert result["message"] == "tc command failed."