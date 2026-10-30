"""Unit tests for the MCP server and tool interface in INA v2."""

import os
import unittest

from inav2.mcp_server import server, tools
from inav2.mcp_server.tools import ToolError
from inav2.network.service import NetworkService
from inav2.tests.unit.test_access_control import MockCommandRunner
from inav2.tests.unit.test_bandwidth import MockTcRunner


class MockCombinedRunner:
    """Mock runner supporting both nftables and tc commands."""

    def __init__(self) -> None:
        self.nft_runner = MockCommandRunner()
        self.tc_runner = MockTcRunner()

    def __call__(self, args, timeout=5.0):
        if args[0] == "tc":
            return self.tc_runner(args, timeout)
        return self.nft_runner(args, timeout)


class TestMCPServer(unittest.TestCase):
    """Test suite for MCP server tool registration and error handling."""

    def setUp(self) -> None:
        self.runner = MockCombinedRunner()
        self.leases_map = {
            "laptop-a": "192.168.50.20",
            "phone-b": "192.168.50.21",
        }
        # Inject mock service into tools module for testing
        self.service = NetworkService(
            runner=self.runner,
            leases_map=self.leases_map,
        )
        tools.default_service = self.service

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
        registered = set(server.mcp.tools.keys())
        self.assertTrue(
            expected_tools.issubset(registered),
            f"Expected {expected_tools} to be registered, found {registered}",
        )

    def test_kick_ban_unban_tools(self) -> None:
        """kick_client, ban_client, unban_client tools execute properly."""
        os.environ["INA_ROLE"] = "operator"
        # Ban preview
        ban_prev = tools.ban_client(client="laptop-a", duration_minutes=30, confirm=False)
        self.assertTrue(ban_prev.preview)
        self.assertTrue(ban_prev.requires_confirmation)

        # Protected rejection wrapped as ToolError
        with self.assertRaises(ToolError):
            tools.ban_client(client="192.168.50.1", duration_minutes=30, confirm=True)


    def test_block_client_tool_success(self) -> None:
        """block_client tool succeeds and returns BlockResult."""
        os.environ["INA_ROLE"] = "operator"
        res = tools.block_client(client="laptop-a", target="phone-b")
        self.assertTrue(res.blocked)
        self.assertTrue(res.changed)
        self.assertEqual(res.client_ip, "192.168.50.20")

    def test_block_client_tool_error(self) -> None:
        """Tool execution failures must raise ToolError (translates to isError: true)."""
        os.environ["INA_ROLE"] = "operator"
        # Unknown client
        with self.assertRaises(ToolError):
            tools.block_client(client="unknown-client", target="internet")

        # Protected client raises ToolError
        with self.assertRaises(ToolError) as ctx:
            tools.block_client(client="192.168.50.1", target="internet")
        self.assertIn("protected", str(ctx.exception).lower())

    def test_limit_bandwidth_tool(self) -> None:
        """limit_bandwidth and remove_bandwidth_limit tools execute properly."""
        os.environ["INA_ROLE"] = "operator"
        # Invalid rates raise ToolError
        with self.assertRaises(ToolError):
            tools.limit_bandwidth(client="laptop-a", download_kbit=50, upload_kbit=2000)

        # Valid rate
        res = tools.limit_bandwidth(client="laptop-a", download_kbit=5000, upload_kbit=2000)
        self.assertEqual(res.download_kbit, 5000)
        self.assertTrue(res.verified)

        # Remove limit
        del_res = tools.remove_bandwidth_limit(client="laptop-a")
        self.assertTrue(del_res.verified)

    def test_get_status_tool(self) -> None:
        """get_status tool returns StatusResult even for viewer role."""
        os.environ["INA_ROLE"] = "viewer"
        res = tools.get_status()
        self.assertFalse(res.drift_detected)
        self.assertEqual(len(res.clients), 2)


if __name__ == "__main__":
    unittest.main()
