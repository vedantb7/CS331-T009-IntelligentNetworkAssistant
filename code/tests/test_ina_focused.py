"""
tests/test_ina_focused.py
=========================
Focused, deterministic unit test suite verifying the 10 critical areas of INA:
1. Policy: allowed/denied actions and invalid requests.
2. MCP tools: block, unblock, bandwidth limit, status.
3. Input validation: unknown clients, invalid IPs, malformed/unsafe rates.
4. Command safety: verify expected commands/arguments using mocks.
5. Error handling: command failure, missing container, missing utility, permission errors, timeouts.
6. Firewall behavior: block/unblock rule creation/removal and idempotency.
7. Bandwidth behavior: tc/IFB configuration and automatic rollback on failure.
8. Validation: ping and iperf3 success/failure/timeout cases.
9. Logging: recording and explaining ALLOWED, DENIED, APPLIED, and FAILED actions.
10. End-to-end flow: request -> policy -> MCP -> validation pipeline transitions.
"""

import os
import sys
import json
import pytest
import asyncio
import subprocess
from unittest.mock import patch, MagicMock, call

# Ensure project root is in python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from policy.policy_engine import check_policy, PolicyResult
from policy import audit_log
from mcp_server import tools as mcp_tools
from validation import monitor as val_monitor
from validation.models import ValidationResult
from assistant.client import NetworkAssistant, ActionType, Intent, PolicyDecision, ExecutionResult, ValidationResult as ClientValResult


# ===========================================================================
# 1. POLICY ENGINE: Allowed/Denied Actions and Invalid Requests
# ===========================================================================
class TestPolicyEngine:
    def test_allowed_actions_pass_policy(self):
        """Verify normal network actions for known clients pass policy check."""
        for action, params in [
            ("block_client", {"client": "client1"}),
            ("unblock_client", {"client": "client1"}),
            ("limit_bandwidth", {"client": "client1", "rate": "10mbit"}),
            ("get_status", {"client": "client1"}),
            ("get_status", {"client": ""}),
            ("get_status", {"client": "all"}),
        ]:
            res = check_policy(action, params)
            assert res.allowed is True, f"Expected {action} with {params} to be allowed"

    def test_disallowed_and_unknown_actions_denied(self):
        """Actions outside the allowed_actions list must be rejected immediately."""
        for action in ["delete_client", "reboot", "format_disk", ""]:
            res = check_policy(action, {"client": "client1"})
            assert res.allowed is False
            assert res.rule_id in ("action-not-allowed", "unknown-action")

    def test_protected_clients_cannot_be_blocked_or_throttled(self):
        """Protected infrastructure targets (e.g. server) must never be blocked or shaped."""
        res_block = check_policy("block_client", {"client": "server"})
        assert res_block.allowed is False
        assert "protected" in res_block.reason.lower()
        assert res_block.rule_id == "protected-client"

        res_limit = check_policy("limit_bandwidth", {"client": "server", "rate": "10mbit"})
        assert res_limit.allowed is False
        assert "protected" in res_limit.reason.lower()
        assert res_limit.rule_id == "protected-client"

    def test_unknown_clients_denied(self):
        """Clients not in the known_clients list must be denied."""
        res = check_policy("block_client", {"client": "client99"})
        assert res.allowed is False
        assert "not a recognized client" in res.reason

    def test_invalid_client_names_denied(self):
        """Malformed, non-string, or injection-prone client names must be rejected."""
        for bad_client in [None, 12345, "", "client1; id", "--flag", "client name"]:
            res = check_policy("block_client", {"client": bad_client})
            assert res.allowed is False
            assert res.rule_id == "invalid-client"

    def test_bandwidth_rate_bounds_and_format(self):
        """Bandwidth rates must be positive, finite, and within rule bounds (1-20mbit)."""
        # Out of bounds
        assert check_policy("limit_bandwidth", {"client": "client1", "rate": "0.5mbit"}).allowed is False
        assert check_policy("limit_bandwidth", {"client": "client1", "rate": "50mbit"}).allowed is False
        # Invalid format / values
        assert check_policy("limit_bandwidth", {"client": "client1", "rate": "0mbit"}).allowed is False
        assert check_policy("limit_bandwidth", {"client": "client1", "rate": "-5mbit"}).allowed is False
        assert check_policy("limit_bandwidth", {"client": "client1", "rate": "fast"}).allowed is False


# ===========================================================================
# 2. MCP TOOLS & COMMAND SAFETY: Mocking Subprocess and Argument Checks
# ===========================================================================
class TestMCPToolsAndCommandSafety:
    @patch("mcp_server.tools.get_container_ip", return_value="172.20.0.2")
    @patch("mcp_server.tools._initialize_bridge_filter", return_value=(True, "OK"))
    @patch("mcp_server.tools.subprocess.run")
    def test_block_client_command_and_safety(self, mock_run, mock_init, mock_ip):
        """Verify block_client executes the exact intended nftables command with argument list."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        res = mcp_tools.block_client("client1")
        assert res["status"] == "success"
        assert "blocked successfully" in res["message"]

        expected_cmd = [
            "docker", "exec", "network-controller",
            "nft", "add", "element",
            "bridge", "network_filter", "blocked_clients",
            "{", "172.20.0.2", "}",
        ]
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == expected_cmd
        assert mock_run.call_args[1].get("timeout") == mcp_tools.DEFAULT_TIMEOUT

    @patch("mcp_server.tools.get_container_ip", return_value="172.20.0.2")
    @patch("mcp_server.tools._initialize_bridge_filter", return_value=(True, "OK"))
    @patch("mcp_server.tools.subprocess.run")
    def test_block_client_idempotent_when_already_exists(self, mock_run, mock_init, mock_ip):
        """nftables 'File exists' stderr must be handled idempotently as success."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Error: Could not process rule: File exists"
        )

        res = mcp_tools.block_client("client1")
        assert res["status"] == "success"
        assert "already blocked" in res["message"]

    @patch("mcp_server.tools.get_container_ip", return_value="172.20.0.2")
    @patch("mcp_server.tools._cleanup_tc_qdiscs")
    @patch("mcp_server.tools.subprocess.run")
    def test_unblock_client_command_and_safety(self, mock_run, mock_cleanup, mock_ip):
        """Verify unblock_client executes the exact intended nftables delete command."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        res = mcp_tools.unblock_client("client1")
        assert res["status"] == "success"
        assert "unblocked successfully" in res["message"]

        expected_cmd = [
            "docker", "exec", "network-controller",
            "nft", "delete", "element",
            "bridge", "network_filter", "blocked_clients",
            "{", "172.20.0.2", "}",
        ]
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == expected_cmd
        mock_cleanup.assert_called_once_with("client1")

    @patch("mcp_server.tools.get_container_ip", return_value="172.20.0.2")
    @patch("mcp_server.tools._cleanup_tc_qdiscs")
    @patch("mcp_server.tools.subprocess.run")
    def test_unblock_client_idempotent_when_already_absent(self, mock_run, mock_cleanup, mock_ip):
        """nftables 'No such element' stderr must be handled idempotently as success."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Error: No such file or directory: No such element"
        )

        res = mcp_tools.unblock_client("client1")
        assert res["status"] == "success"
        assert "already unblocked" in res["message"]

    @patch("mcp_server.tools.get_container_ip", return_value="172.20.0.2")
    @patch("mcp_server.tools._cleanup_tc_qdiscs")
    @patch("mcp_server.tools.subprocess.run")
    def test_limit_bandwidth_command_sequence(self, mock_run, mock_cleanup, mock_ip):
        """Verify limit_bandwidth configures IFB redirect, ingress, and egress tbf qdiscs."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        res = mcp_tools.limit_bandwidth("client1", "5mbps")
        assert res["status"] == "success"
        assert "limited to 5mbit" in res["message"]

        # Ensure cleanup was performed prior to configuration
        mock_cleanup.assert_called_once_with("client1")
        # Verify 6 subprocess calls: ip link add, ip link set, tc qdisc add ingress, tc filter add, tc replace ifb0, tc replace eth0
        assert mock_run.call_count == 6
        last_call_cmd = mock_run.call_args_list[-1][0][0]
        assert last_call_cmd == [
            "docker", "exec", "client1", "tc", "qdisc", "replace",
            "dev", "eth0", "root", "tbf", "rate", "5mbit",
            "burst", "32kbit", "latency", "400ms",
        ]

    @patch("mcp_server.tools.get_container_ip", return_value="172.20.0.2")
    @patch("mcp_server.tools._cleanup_tc_qdiscs")
    @patch("mcp_server.tools.subprocess.run")
    def test_limit_bandwidth_automatic_rollback_on_failure(self, mock_run, mock_cleanup, mock_ip):
        """If tc configuration fails, atomic rollback must trigger cleanup to avoid orphan rules."""
        # Setup: first 5 calls succeed, 6th call (egress tc) fails
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="RTNETLINK answers: Device or resource busy"),
        ]

        res = mcp_tools.limit_bandwidth("client1", "10mbit")
        assert res["status"] == "failure"
        assert "RTNETLINK answers" in res["message"]
        # Cleanup called before setup AND during rollback
        assert mock_cleanup.call_count == 2

    @patch("mcp_server.tools.get_container_ip", return_value="172.20.0.2")
    @patch("mcp_server.tools._cleanup_tc_qdiscs")
    def test_limit_bandwidth_removal(self, mock_cleanup, mock_ip):
        """Passing 'none' or '0' to limit_bandwidth removes the limit cleanly."""
        with patch("mcp_server.tools.check_policy", return_value=PolicyResult(True, "OK")):
            res = mcp_tools.limit_bandwidth("client1", "none")
            assert res["status"] == "success"
            assert "removed" in res["message"]
            mock_cleanup.assert_called_once_with("client1")

    @patch("network.discovery.list_known_clients", return_value={"client1": "172.20.0.2", "server": "172.20.0.3"})
    @patch("mcp_server.tools._get_blocked_ips", return_value={"172.20.0.2"})
    @patch("mcp_server.tools._get_tc_rate", return_value="5mbit")
    @patch("mcp_server.tools._ping_container", return_value=False)
    def test_get_status_reports_accurate_state(self, mock_ping, mock_tc, mock_blocked, mock_known):
        """get_status should assemble live firewall, bandwidth, and reachability state."""
        res = mcp_tools.get_status("client1")
        assert res["status"] == "success"
        c_info = res["clients"]["client1"]
        assert c_info["firewall"] == "blocked"
        assert c_info["bandwidth_limit"] == "5mbit"
        assert c_info["reachable"] is False


# ===========================================================================
# 3. INPUT VALIDATION: Unsafe/Malformed Clients, IPs, and Rates
# ===========================================================================
class TestInputValidation:
    def test_unsafe_client_names_rejected_immediately(self):
        """Injection attacks or dangerous identifiers must be blocked before subprocess invocation."""
        bad_names = [
            "client1; rm -rf /",
            "client1 && whoami",
            "`id`",
            "--privileged",
            "-client1",
            "client 1",
            "",
            None,
        ]
        for bad in bad_names:
            res = mcp_tools.block_client(bad)  # type: ignore
            assert res["status"] == "failure"
            assert "Invalid client" in res["message"] or "valid string" in res["message"]

    def test_disallowed_control_plane_targets_rejected(self):
        """Users must not target control-plane or host namespaces."""
        for disallowed in ["network-controller", "host", "docker", "root", "bridge"]:
            res = mcp_tools.block_client(disallowed)
            assert res["status"] == "failure"
            assert "not permitted" in res["message"]

    def test_malformed_and_injection_rates_rejected(self):
        """Malformed or malicious rate strings must be rejected."""
        bad_rates = [
            "10mbit; cat /etc/passwd",
            "10mbit burst 64kbit",
            "fast",
            "-10mbit",
            "10mbps && reboot",
        ]
        for bad_rate in bad_rates:
            res = mcp_tools.limit_bandwidth("client1", bad_rate)
            assert res["status"] == "failure"
            assert "Invalid bandwidth rate" in res["message"]

    def test_ipv4_validation(self):
        """_validate_ipv4 must validate authentic IPv4 formatting."""
        assert mcp_tools._validate_ipv4("172.20.0.2") == "172.20.0.2"
        for bad_ip in ["999.999.999.999", "172.20.0.2; evil", "not_an_ip", ""]:
            with pytest.raises(ValueError):
                mcp_tools._validate_ipv4(bad_ip)


# ===========================================================================
# 4. ERROR HANDLING: Missing Binaries, Timeouts, Missing Containers
# ===========================================================================
class TestErrorHandling:
    @patch("mcp_server.tools.get_container_ip", return_value="172.20.0.2")
    @patch("mcp_server.tools._initialize_bridge_filter", return_value=(True, "OK"))
    @patch("mcp_server.tools.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="nft", timeout=10))
    def test_block_client_subprocess_timeout(self, mock_run, mock_init, mock_ip):
        """Subprocess timeouts must return a failure status without hanging or crashing."""
        res = mcp_tools.block_client("client1")
        assert res["status"] == "failure"
        assert "timed out" in res["message"].lower()

    @patch("mcp_server.tools.get_container_ip", return_value="172.20.0.2")
    @patch("mcp_server.tools._initialize_bridge_filter", return_value=(True, "OK"))
    @patch("mcp_server.tools.subprocess.run", side_effect=FileNotFoundError("No such file: 'docker'"))
    def test_block_client_missing_docker_binary(self, mock_run, mock_init, mock_ip):
        """Missing docker CLI must return clean error without stack trace."""
        res = mcp_tools.block_client("client1")
        assert res["status"] == "failure"
        assert "docker command not found" in res["message"].lower()

    @patch("mcp_server.tools.get_container_ip", side_effect=ValueError("Client 'client1' not found in Docker environment."))
    def test_block_client_missing_container(self, mock_ip):
        """Nonexistent container must report failure gracefully."""
        res = mcp_tools.block_client("client1")
        assert res["status"] == "failure"
        assert "not found" in res["message"]

    @patch("subprocess.run")
    def test_policy_denial_blocks_execution_at_mcp_level(self, mock_sub):
        """MCP tools must enforce policy engine check and avoid subprocess calls if denied."""
        with patch("mcp_server.tools.check_policy", return_value=PolicyResult(False, "Protected infrastructure", "protected-client")):
            res = mcp_tools.block_client("server")
            assert res["status"] == "failure"
            assert "Policy denied: Protected infrastructure" in res["message"]
            mock_sub.assert_not_called()


# ===========================================================================
# 5. VALIDATION MONITOR: Ping, iperf3, Tolerances, and Timeouts
# ===========================================================================
class TestValidationMonitor:
    @patch("validation.monitor.get_ip", return_value="172.20.0.2")
    @patch("validation.monitor.pick_source_container", return_value="server")
    @patch("validation.monitor.run_ping", return_value={"reachable": False, "packet_loss": 100.0, "output": ""})
    def test_check_block_passed(self, mock_ping, mock_src, mock_ip):
        """100% packet loss validates block operation passed."""
        res = val_monitor.check_block("client1")
        assert res.passed is True
        assert "successfully blocked" in res.message

    @patch("validation.monitor.get_ip", return_value="172.20.0.2")
    @patch("validation.monitor.pick_source_container", return_value="server")
    @patch("validation.monitor.run_ping", return_value={"reachable": True, "packet_loss": 0.0, "output": ""})
    def test_check_block_failed(self, mock_ping, mock_src, mock_ip):
        """0% packet loss indicates block operation failed."""
        res = val_monitor.check_block("client1")
        assert res.passed is False
        assert "still reachable" in res.message

    @patch("validation.monitor.get_ip", return_value="172.20.0.2")
    @patch("validation.monitor.pick_source_container", return_value="server")
    @patch("validation.monitor.run_ping", return_value={"reachable": True, "packet_loss": 0.0, "output": ""})
    def test_check_unblock_passed(self, mock_ping, mock_src, mock_ip):
        """Reachable ping indicates unblock operation passed."""
        res = val_monitor.check_unblock("client1")
        assert res.passed is True
        assert "successfully unblocked" in res.message

    @patch("validation.monitor.get_ip", return_value="172.20.0.2")
    @patch("validation.monitor.run_iperf", side_effect=[9.8, 10.1])
    def test_check_bandwidth_within_tolerance(self, mock_iperf, mock_ip):
        """Throughput within 20% tolerance of target rate passes."""
        res = val_monitor.check_bandwidth("client1", "10mbit")
        assert res.passed is True
        assert "within expected range" in res.message

    @patch("validation.monitor.get_ip", return_value="172.20.0.2")
    @patch("validation.monitor.run_iperf", side_effect=[4.0, 4.2])
    def test_check_bandwidth_outside_tolerance(self, mock_iperf, mock_ip):
        """Throughput outside tolerance fails validation."""
        res = val_monitor.check_bandwidth("client1", "10mbit")
        assert res.passed is False
        assert "outside expected range" in res.message

    @patch("validation.monitor.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ping", timeout=10))
    def test_run_ping_timeout_handled(self, mock_run):
        """Ping timeout is captured safely without throwing an exception."""
        res = val_monitor.run_ping("server", "172.20.0.2")
        assert res["reachable"] is False
        assert res["packet_loss"] == 100.0

    @patch("validation.monitor.ensure_iperf_server")
    @patch("validation.monitor.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="iperf3", timeout=15))
    def test_run_iperf_timeout_handled(self, mock_run, mock_server):
        """iperf3 timeout raises clean RuntimeError."""
        with pytest.raises(RuntimeError, match="timed out"):
            val_monitor.run_iperf("client1", "172.20.0.3")


# ===========================================================================
# 6. AUDIT LOGGING: Recording and Explaining ALLOWED, DENIED, APPLIED, FAILED
# ===========================================================================
class TestAuditLogging:
    def test_audit_log_lifecycle(self, tmp_path, monkeypatch):
        """Audit log correctly records and explains all operation statuses."""
        temp_log = str(tmp_path / "test_audit.jsonl")
        monkeypatch.setattr(audit_log, "LOG_PATH", temp_log)

        audit_log.log_action("block_client", {"client": "client1"}, "ALLOWED", "Policy check passed")
        audit_log.log_action("block_client", {"client": "server"}, "DENIED", "Protected host")
        audit_log.log_action("block_client", {"client": "client1"}, "APPLIED", "Firewall drop element added")
        audit_log.log_action("limit_bandwidth", {"client": "client1"}, "FAILED", "tc device busy")

        logs = audit_log.get_all_logs()
        assert len(logs) == 4
        assert [e["status"] for e in logs] == ["ALLOWED", "DENIED", "APPLIED", "FAILED"]

        # Verify explain_action mappings
        assert "allowed" in audit_log.explain_action(0)
        assert "denied" in audit_log.explain_action(1)
        assert "applied" in audit_log.explain_action(2)
        assert "failed to apply" in audit_log.explain_action(3)


# ===========================================================================
# 7. END-TO-END FLOW: User Input -> Policy -> MCP -> Validation
# ===========================================================================
class TestEndToEndPipeline:
    @pytest.mark.anyio
    async def test_e2e_successful_block_flow(self):
        """Happy path: User -> Parsing -> Policy (ALLOW) -> MCP (APPLIED) -> Validation (CONFIRMED)."""
        assistant = NetworkAssistant()

        # Mock stages
        assistant.parser.parse = MagicMock(return_value=Intent(
            action=ActionType.BLOCK_CLIENT, target="client1", params={}, raw_text="block client1"
        ))
        assistant.policy_engine.evaluate = MagicMock(return_value=PolicyDecision(
            allowed=True, reason="Block permitted"
        ))
        assistant.mcp_tools.block_client = MagicMock(return_value={
            "status": "success", "success": True, "message": "Client client1 blocked."
        })
        assistant.validator.validate = MagicMock(return_value=ClientValResult(
            success=True, summary="client1 is blocked."
        ))

        report = await assistant.process_command("block client1")
        assert report["halted_at"] is None
        assert report["policy"]["allowed"] is True
        assert report["execution"]["success"] is True
        assert report["validation"]["success"] is True
        assert report["natural_response"] is not None

    @pytest.mark.anyio
    async def test_e2e_policy_blocked_flow(self):
        """Policy denial stops pipeline before MCP execution."""
        assistant = NetworkAssistant()

        assistant.parser.parse = MagicMock(return_value=Intent(
            action=ActionType.BLOCK_CLIENT, target="server", params={}, raw_text="block server"
        ))
        assistant.policy_engine.evaluate = MagicMock(return_value=PolicyDecision(
            allowed=False, reason="'server' is a protected client"
        ))
        assistant.mcp_tools.block_client = MagicMock()

        report = await assistant.process_command("block server")
        assert report["halted_at"] == "policy"
        assert report["policy"]["allowed"] is False
        assert report["execution"] is None
        assert report["validation"] is None
        assistant.mcp_tools.block_client.assert_not_called()

    @pytest.mark.anyio
    async def test_e2e_execution_failure_halts_before_validation(self):
        """Execution failure halts pipeline before validation monitor runs."""
        assistant = NetworkAssistant()

        assistant.parser.parse = MagicMock(return_value=Intent(
            action=ActionType.LIMIT_BANDWIDTH, target="client1", params={"rate": "5mbit"}, raw_text="limit client1 to 5mbps"
        ))
        assistant.policy_engine.evaluate = MagicMock(return_value=PolicyDecision(
            allowed=True, reason="Permitted"
        ))
        assistant.mcp_tools.limit_bandwidth = MagicMock(return_value={
            "status": "failure", "success": False, "message": "tc command failed"
        })
        assistant.validator.validate = MagicMock()

        report = await assistant.process_command("limit client1 to 5mbps")
        assert report["halted_at"] == "execution"
        assert report["execution"]["success"] is False
        assert report["validation"] is None
        assistant.validator.validate.assert_not_called()

    @pytest.mark.anyio
    async def test_e2e_conversational_short_circuit(self):
        """Conversational queries bypass Policy, MCP, and Validation completely."""
        assistant = NetworkAssistant()

        assistant.parser.parse = MagicMock(return_value=Intent(
            action=ActionType.CONVERSATION, target="", params={"reply": "Hello! How can I help?"}, raw_text="hello"
        ))
        assistant.policy_engine.evaluate = MagicMock()
        assistant.mcp_tools.block_client = MagicMock()

        report = await assistant.process_command("hello")
        assert report["halted_at"] == "conversation"
        assert report["policy"] is None
        assert report["execution"] is None
        assert report["validation"] is None
        assert report["natural_response"] == "Hello! How can I help?"
        assistant.policy_engine.evaluate.assert_not_called()
        assistant.mcp_tools.block_client.assert_not_called()

    @pytest.mark.anyio
    async def test_e2e_unknown_command_clarification(self):
        """Unknown commands halt at parsing with a natural clarification."""
        assistant = NetworkAssistant()

        assistant.parser.parse = MagicMock(return_value=Intent(
            action=ActionType.UNKNOWN, target="", params={"reply": "I'm not sure how to help."}, raw_text="gibberish"
        ))

        report = await assistant.process_command("gibberish")
        assert report["halted_at"] == "parsing"
        assert report["policy"] is None
        assert report["execution"] is None
        assert "not sure" in report["natural_response"]
