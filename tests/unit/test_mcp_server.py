"""Unit tests for the MCP server and tool interface in INA v2.

Every tool call must pass the policy guard (role token -> role -> policy
engine -> audit) before the service layer runs anything.
"""

import asyncio
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from inav2.mcp_server import server, tools
from inav2.mcp_server.tools import TOKEN_ENV, ToolError
from inav2.network.service import NetworkService
from inav2.policy.guard import PolicyGuard
from inav2.tests.unit.test_access_control import MockCommandRunner
from inav2.tests.unit.test_bandwidth import MockTcRunner


class MockCombinedRunner:
    """Mock runner supporting both nftables and tc commands."""

    def __init__(self) -> None:
        self.nft_runner = MockCommandRunner()
        self.tc_runner = MockTcRunner()
        self.commands = []

    def __call__(self, args, timeout=5.0):
        self.commands.append(list(args))
        if args[0] == "tc":
            return self.tc_runner(args, timeout)
        return self.nft_runner(args, timeout)


# Names from policy/devices.yaml, mapped to lease IPs as dnsmasq would.
LEASES = {
    "guest-phone": "192.168.50.34",
    "lab-laptop-3": "192.168.50.23",
    "admin-laptop": "192.168.50.20",
}


def registered_tool_names():
    """Tool names from the real SDK (tools/list) or the fallback stub."""
    if hasattr(server.mcp, "tools"):
        return set(server.mcp.tools.keys())
    return {t.name for t in asyncio.run(server.mcp.list_tools())}


class TestMCPServer(unittest.TestCase):
    """Test suite for MCP server tool registration, policy guard and error handling."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.runner = MockCombinedRunner()
        self.service = NetworkService(
            runner=self.runner,
            leases_map=dict(LEASES),
            lock_path=os.path.join(self.tmp, "ina.lock"),
            state_path=os.path.join(self.tmp, "state.json"),
            audit_path=os.path.join(self.tmp, "audit.log"),
        )
        # Inject mock service and a guard with demo tokens into the tools module
        tools.default_service = self.service
        self.policy_audit = os.path.join(self.tmp, "policy_audit.jsonl")
        tools._guard = PolicyGuard.from_config(audit_path=self.policy_audit,
                                               env={"INA_DEMO_TOKENS": "1"})
        self._old_token = os.environ.get(TOKEN_ENV)
        os.environ[TOKEN_ENV] = "operator-token"
        self.command_log = os.path.join(self.tmp, "commands.jsonl")
        self._env = mock.patch.dict(os.environ, {"INA_COMMAND_LOG": self.command_log})
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        tools._guard = None
        if self._old_token is None:
            os.environ.pop(TOKEN_ENV, None)
        else:
            os.environ[TOKEN_ENV] = self._old_token
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_registered_tools(self) -> None:
        """The MCP server must expose all canonical tools including wireless control."""
        expected_tools = {
            "block_client",
            "unblock_client",
            "limit_bandwidth",
            "remove_bandwidth_limit",
            "get_status",
            "kick_client",
            "ban_client",
            "unban_client",
        }
        registered = registered_tool_names()
        self.assertTrue(
            expected_tools.issubset(registered),
            f"Expected {expected_tools} to be registered, found {registered}",
        )

    def test_tools_for_role_come_from_policy(self) -> None:
        """Visible tools per role follow policy/roles.yaml."""
        self.assertEqual(server.get_tools_for_role("viewer"), ["get_status"])
        self.assertIn("ban_client", server.get_tools_for_role("operator"))

    def test_kick_ban_unban_tools(self) -> None:
        """kick_client, ban_client, unban_client tools execute properly."""
        # Ban preview
        ban_prev = tools.ban_client(client="guest-phone", duration_minutes=30, confirm=False)
        self.assertTrue(ban_prev.preview)
        self.assertTrue(ban_prev.requires_confirmation)

        # Protected rejection wrapped as ToolError
        with self.assertRaises(ToolError) as ctx:
            tools.ban_client(client="192.168.50.1", duration_minutes=30, confirm=True)
        self.assertIn("protected", str(ctx.exception).lower())

    def test_operator_cannot_ban_permanently(self) -> None:
        """roles.yaml: operator bans need a duration of at most 60 minutes."""
        with self.assertRaises(ToolError) as ctx:
            tools.ban_client(client="guest-phone", duration_minutes=None, confirm=True)
        self.assertIn("Denied by policy", str(ctx.exception))

    def test_unban_scope_uses_server_side_ban_owner(self) -> None:
        """An operator may not lift a ban an admin created (context comes from state, not the AI)."""
        with open(self.service.state_path, "w", encoding="utf-8") as f:
            json.dump({"version": "1.1", "blocks": [], "limits": [], "bans": [{
                "client": "guest-phone", "client_ip": "192.168.50.34",
                "mac": "aa:bb:cc:dd:ee:04", "banned_by_role": "admin",
            }]}, f)
        self.assertEqual(self.service.ban_owner_role("guest-phone"), "admin")
        with self.assertRaises(ToolError) as ctx:
            tools.unban_client(client="guest-phone")
        self.assertIn("R-unban-scope", str(ctx.exception))

    def test_block_client_tool_success(self) -> None:
        """block_client tool succeeds and returns BlockResult."""
        res = tools.block_client(client="guest-phone", target="internet")
        self.assertTrue(res.blocked)
        self.assertTrue(res.changed)
        self.assertEqual(res.client_ip, "192.168.50.34")

    def test_block_client_tool_error(self) -> None:
        """Tool execution failures must raise ToolError (translates to isError: true)."""
        # Unknown client
        with self.assertRaises(ToolError):
            tools.block_client(client="unknown-client", target="internet")

        # Protected client raises ToolError
        with self.assertRaises(ToolError) as ctx:
            tools.block_client(client="192.168.50.1", target="internet")
        self.assertIn("protected", str(ctx.exception).lower())

    def test_policy_denial_runs_no_command(self) -> None:
        """A denied call never reaches the kernel."""
        with self.assertRaises(ToolError) as ctx:
            tools.limit_bandwidth(client="lab-laptop-3", download_kbit=1000, upload_kbit=1000)
        self.assertIn("Denied by policy [R-rate-range]", str(ctx.exception))
        self.assertEqual(self.runner.commands, [])

    def test_missing_token_is_denied(self) -> None:
        """No role token, no action."""
        os.environ.pop(TOKEN_ENV, None)
        with self.assertRaises(ToolError) as ctx:
            tools.get_status()
        self.assertIn("R-token", str(ctx.exception))

        os.environ[TOKEN_ENV] = "not-a-real-token"
        with self.assertRaises(ToolError):
            tools.block_client(client="guest-phone", target="internet")
        self.assertEqual(self.runner.commands, [])

    def test_kernel_failure_is_reported_as_failed(self) -> None:
        """Errors after the command started are prefixed 'Failed', not 'Denied'."""
        self.runner.nft_runner.fail_next_apply = True
        with self.assertRaises(ToolError) as ctx:
            tools.block_client(client="guest-phone", target="internet")
        self.assertTrue(str(ctx.exception).startswith("Failed:"), str(ctx.exception))

    def test_decisions_are_audited(self) -> None:
        """Every guarded call writes a decision and an outcome to the policy audit log."""
        tools.get_status()
        with open(self.policy_audit, encoding="utf-8") as f:
            events = [json.loads(line)["event"] for line in f if line.strip()]
        self.assertEqual(events[-2:], ["decision", "outcome"])

    def test_limit_bandwidth_tool(self) -> None:
        """limit_bandwidth and remove_bandwidth_limit tools execute properly."""
        # Invalid rates raise ToolError
        with self.assertRaises(ToolError):
            tools.limit_bandwidth(client="guest-phone", download_kbit=50, upload_kbit=2000)

        # Valid rate
        res = tools.limit_bandwidth(client="guest-phone", download_kbit=5000, upload_kbit=2000)
        self.assertEqual(res.download_kbit, 5000)
        self.assertTrue(res.verified)

        # Remove limit
        del_res = tools.remove_bandwidth_limit(client="guest-phone")
        self.assertTrue(del_res.verified)

    def test_get_status_tool(self) -> None:
        """get_status tool returns StatusResult for the viewer role."""
        os.environ[TOKEN_ENV] = "viewer-token"
        res = tools.get_status()
        self.assertFalse(res.drift_detected)
        self.assertEqual(len(res.clients), 3)

    def test_viewer_cannot_mutate(self) -> None:
        os.environ[TOKEN_ENV] = "viewer-token"
        with self.assertRaises(ToolError) as ctx:
            tools.block_client(client="guest-phone", target="internet")
        self.assertIn("R-role-action", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
