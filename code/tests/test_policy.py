import os
import sys

# Add project root to Python path
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )
)

import json
import pytest

from policy.policy_engine import check_policy
from policy.audit_log import log_action, get_all_logs, explain_action


# --------------------------------------------------
# Policy Engine Tests
# --------------------------------------------------

def test_valid_block_operation_is_allowed():
    result = check_policy(
        "block_client",
        {"client": "client1"}
    )

    assert result.allowed is True
    assert "Block permitted" in result.reason


def test_valid_unblock_operation_is_allowed():
    result = check_policy(
        "unblock_client",
        {"client": "client1"}
    )

    assert result.allowed is True
    assert "Unblock permitted" in result.reason


def test_valid_bandwidth_operation_is_allowed():
    result = check_policy(
        "limit_bandwidth",
        {
            "client": "client1",
            "rate": "10mbit"
        }
    )

    assert result.allowed is True
    assert "within allowed range" in result.reason


def test_unauthorized_operation_is_denied():
    result = check_policy(
        "delete_client",
        {"client": "client1"}
    )

    assert result.allowed is False
    assert "not in the list of allowed actions" in result.reason


def test_invalid_client_is_denied():
    result = check_policy(
        "block_client",
        {"client": "unknown_client"}
    )

    assert result.allowed is False
    assert "not a recognized client" in result.reason


def test_protected_client_cannot_be_blocked():
    result = check_policy(
        "block_client",
        {"client": "server"}
    )

    assert result.allowed is False
    assert "protected client" in result.reason


def test_protected_client_cannot_have_bandwidth_changed():
    result = check_policy(
        "limit_bandwidth",
        {
            "client": "server",
            "rate": "10mbit"
        }
    )

    assert result.allowed is False
    assert "protected" in result.reason


def test_bandwidth_below_minimum_is_denied():
    result = check_policy(
        "limit_bandwidth",
        {
            "client": "client1",
            "rate": "0.5mbit"
        }
    )

    assert result.allowed is False
    assert "outside the allowed range" in result.reason


def test_bandwidth_above_maximum_is_denied():
    result = check_policy(
        "limit_bandwidth",
        {
            "client": "client1",
            "rate": "25mbit"
        }
    )

    assert result.allowed is False
    assert "outside the allowed range" in result.reason


def test_invalid_bandwidth_format_is_denied():
    result = check_policy(
        "limit_bandwidth",
        {
            "client": "client1",
            "rate": "invalid_rate"
        }
    )

    assert result.allowed is False
    assert "Could not understand rate value" in result.reason


# --------------------------------------------------
# Additional Policy Engine Tests
# --------------------------------------------------

def test_known_client_unblock_is_allowed():
    result = check_policy(
        "unblock_client",
        {"client": "client2"}
    )

    assert result.allowed is True


def test_bandwidth_minimum_boundary_is_allowed():
    result = check_policy(
        "limit_bandwidth",
        {
            "client": "client1",
            "rate": "1mbit"
        }
    )

    assert result.allowed is True


def test_bandwidth_maximum_boundary_is_allowed():
    result = check_policy(
        "limit_bandwidth",
        {
            "client": "client1",
            "rate": "20mbit"
        }
    )

    assert result.allowed is True


def test_numeric_bandwidth_value_is_allowed():
    result = check_policy(
        "limit_bandwidth",
        {
            "client": "client1",
            "rate": 10
        }
    )

    assert result.allowed is True


# --------------------------------------------------
# Audit Log Tests
# --------------------------------------------------

def test_log_action_creates_log_entry(tmp_path, monkeypatch):
    from policy import audit_log

    log_file = tmp_path / "audit_log.jsonl"

    monkeypatch.setattr(
        audit_log,
        "LOG_PATH",
        str(log_file)
    )

    entry = log_action(
        action="block_client",
        params={"client": "client1"},
        status="ALLOWED",
        reason="Client is not protected."
    )

    assert entry["action"] == "block_client"
    assert entry["params"] == {"client": "client1"}
    assert entry["status"] == "ALLOWED"
    assert entry["reason"] == "Client is not protected."
    assert "timestamp" in entry

    assert log_file.exists()


def test_get_all_logs_returns_logged_entries(tmp_path, monkeypatch):
    from policy import audit_log

    log_file = tmp_path / "audit_log.jsonl"

    monkeypatch.setattr(
        audit_log,
        "LOG_PATH",
        str(log_file)
    )

    log_action(
        "block_client",
        {"client": "client1"},
        "ALLOWED",
        "Block permitted."
    )

    log_action(
        "block_client",
        {"client": "server"},
        "DENIED",
        "Protected client."
    )

    logs = get_all_logs()

    assert len(logs) == 2

    assert logs[0]["action"] == "block_client"
    assert logs[0]["status"] == "ALLOWED"

    assert logs[1]["action"] == "block_client"
    assert logs[1]["status"] == "DENIED"


def test_get_all_logs_returns_empty_list_when_no_log_exists(
    tmp_path,
    monkeypatch
):
    from policy import audit_log

    log_file = tmp_path / "audit_log.jsonl"

    monkeypatch.setattr(
        audit_log,
        "LOG_PATH",
        str(log_file)
    )

    logs = get_all_logs()

    assert logs == []


def test_explain_action_for_allowed_request(tmp_path, monkeypatch):
    from policy import audit_log

    log_file = tmp_path / "audit_log.jsonl"

    monkeypatch.setattr(
        audit_log,
        "LOG_PATH",
        str(log_file)
    )

    log_action(
        "block_client",
        {"client": "client1"},
        "ALLOWED",
        "Client is not protected."
    )

    explanation = explain_action()

    assert "I allowed the request" in explanation
    assert "block_client" in explanation
    assert "client1" in explanation
    assert "Client is not protected." in explanation


def test_explain_action_for_denied_request(tmp_path, monkeypatch):
    from policy import audit_log

    log_file = tmp_path / "audit_log.jsonl"

    monkeypatch.setattr(
        audit_log,
        "LOG_PATH",
        str(log_file)
    )

    log_action(
        "block_client",
        {"client": "server"},
        "DENIED",
        "Protected client cannot be blocked."
    )

    explanation = explain_action()

    assert "I denied the request" in explanation
    assert "block_client" in explanation
    assert "server" in explanation
    assert "Protected client cannot be blocked." in explanation


def test_explain_action_returns_no_logs_message(
    tmp_path,
    monkeypatch
):
    from policy import audit_log

    log_file = tmp_path / "audit_log.jsonl"

    monkeypatch.setattr(
        audit_log,
        "LOG_PATH",
        str(log_file)
    )

    explanation = explain_action()

    assert explanation == "No actions have been logged yet."


# --------------------------------------------------
# Status Policy Tests
# --------------------------------------------------

def test_status_query_for_known_client_is_allowed():
    result = check_policy("get_status", {"client": "client1"})
    assert result.allowed is True
    assert "Status query permitted" in result.reason


def test_network_wide_status_query_is_allowed():
    result_empty = check_policy("get_status", {})
    assert result_empty.allowed is True
    assert "Network status query permitted" in result_empty.reason

    result_all = check_policy("get_status", {"client": "all"})
    assert result_all.allowed is True


def test_status_query_for_unknown_client_is_denied():
    result = check_policy("get_status", {"client": "unknown_client"})
    assert result.allowed is False
    assert "not a recognized client" in result.reason