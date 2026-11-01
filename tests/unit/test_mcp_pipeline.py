"""End-to-end tests of workflow step 3 over the real MCP protocol.

natural language -> assistant -> McpTransport (official MCP SDK client)
-> tools/call -> INA MCP server: inputSchema validation -> policy guard
-> service layer -> (mock) kernel -> structured result -> assistant reply.

Skipped when the MCP Python SDK is not installed (pip install -r requirements.txt).
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None

if MCP_AVAILABLE:
    from assistant.client import NetworkAssistant
    from assistant.contract import ToolCall
    from assistant.llm import RuleBasedBackend
    from assistant.transport import McpTransport
    from inav2.mcp_server import server, tools
    from inav2.mcp_server.tools import TOKEN_ENV
    from inav2.network.service import NetworkService
    from inav2.policy.guard import PolicyGuard
    from inav2.tests.unit.test_mcp_server import LEASES, MockCombinedRunner


@unittest.skipUnless(MCP_AVAILABLE, "MCP Python SDK not installed")
class TestMcpPipelineInProcess(unittest.TestCase):
    """Assistant -> MCP client -> in-process MCP server (same protocol path as stdio)."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.runner = MockCombinedRunner()
        tools.default_service = NetworkService(
            runner=self.runner,
            leases_map=dict(LEASES),
            lock_path=os.path.join(self.tmp, "ina.lock"),
            state_path=os.path.join(self.tmp, "state.json"),
            audit_path=os.path.join(self.tmp, "audit.log"),
        )
        tools._guard = PolicyGuard.from_config(
            audit_path=os.path.join(self.tmp, "policy_audit.jsonl"),
            env={"INA_DEMO_TOKENS": "1"},
        )
        self._old_token = os.environ.get(TOKEN_ENV)
        os.environ[TOKEN_ENV] = "operator-token"
        self._env = mock.patch.dict(os.environ, {"INA_COMMAND_LOG": os.path.join(self.tmp, "commands.jsonl")})
        self._env.start()
        self.transport = McpTransport(server=server.mcp)
        self.assistant = NetworkAssistant(transport=self.transport, backend=RuleBasedBackend())

    def tearDown(self) -> None:
        self.transport.close()
        self._env.stop()
        tools._guard = None
        if self._old_token is None:
            os.environ.pop(TOKEN_ENV, None)
        else:
            os.environ[TOKEN_ENV] = self._old_token
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tools_list_publishes_constraints(self) -> None:
        """The model sees the same patterns and ranges the server enforces."""
        self.assertEqual(len(self.transport.tools), 8)
        limit = self.transport.tools["limit_bandwidth"].input_schema["properties"]
        self.assertEqual(limit["download_kbit"]["maximum"], 20000)
        self.assertEqual(limit["download_kbit"]["minimum"], 256)
        self.assertIn("pattern", limit["client"])
        ban = self.transport.tools["ban_client"].input_schema["properties"]
        self.assertIn("duration_minutes", ban)

    def test_allowed_call_executes_and_returns_structured_result(self) -> None:
        rec = self.assistant.handle("limit guest-phone to 5 mbps")
        self.assertEqual(rec.outcome, "success", rec.reply)
        self.assertEqual(rec.result["data"]["download_kbit"], 5000)
        self.assertTrue(rec.result["verification"]["passed"])
        self.assertTrue(any(c[0] == "tc" for c in self.runner.commands))

    def test_policy_denial_never_reaches_kernel(self) -> None:
        rec = self.assistant.handle("block admin-laptop")
        self.assertEqual(rec.outcome, "denied")
        self.assertIn("protected", rec.reply)
        self.assertEqual(rec.result["decision"]["rule"], "R-protected")
        self.assertEqual(self.runner.commands, [])

    def test_schema_violation_rejected_by_sdk(self) -> None:
        rec = self.assistant.handle("limit guest-phone to 50 mbps")
        self.assertEqual(rec.outcome, "denied")
        self.assertIn("download_kbit", rec.reply)
        self.assertEqual(self.runner.commands, [])

    def test_role_class_denial(self) -> None:
        rec = self.assistant.handle("block lab-laptop-3")
        self.assertEqual(rec.outcome, "denied")
        self.assertEqual(rec.result["decision"]["rule"], "R-role-class")

    def test_ban_returns_preview(self) -> None:
        rec = self.assistant.handle("ban guest-phone for 30 minutes")
        self.assertEqual(rec.outcome, "preview", rec.reply)

    def test_status_and_device_names(self) -> None:
        self.assertEqual(sorted(self.transport.device_names()), sorted(LEASES))
        rec = self.assistant.handle("list devices")
        self.assertEqual(rec.outcome, "success")
        self.assertIn("guest-phone", rec.reply)

    def test_extra_arguments_are_not_sent(self) -> None:
        """Only arguments declared in the tool's inputSchema go on the wire."""
        res = self.transport.call(ToolCall(tool="block_client", arguments={
            "device": "guest-phone", "target": "internet", "force": True, "role": "admin"}))
        self.assertEqual(res.status, "success", res.detail)

    def test_unknown_tool(self) -> None:
        res = self.transport.call(ToolCall(tool="flush_ruleset", arguments={}))
        self.assertEqual(res.status, "denied")


@unittest.skipUnless(MCP_AVAILABLE, "MCP Python SDK not installed")
class TestMcpPipelineStdio(unittest.TestCase):
    """The real deployment shape: the server runs as a stdio subprocess."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self._saved = {k: os.environ.get(k) for k in ("INA_DEMO_TOKENS", "INA_POLICY_AUDIT_LOG")}
        os.environ["INA_DEMO_TOKENS"] = "1"
        os.environ["INA_POLICY_AUDIT_LOG"] = os.path.join(self.tmp, "policy_audit.jsonl")

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stdio_round_trip(self) -> None:
        transport = McpTransport(token="operator-token")
        try:
            self.assertIn("ban_client", transport.tools)
            denied = transport.call(ToolCall(tool="block_client",
                                             arguments={"client": "admin-laptop", "target": "all"}))
            self.assertEqual(denied.status, "denied")
            self.assertEqual(denied.decision.rule, "R-protected")
        finally:
            transport.close()

    def test_stdio_bad_token_denied(self) -> None:
        transport = McpTransport(token="wrong-token")
        try:
            res = transport.call(ToolCall(tool="get_status", arguments={}))
            self.assertEqual(res.status, "denied")
            self.assertEqual(res.decision.rule, "R-token")
        finally:
            transport.close()


if __name__ == "__main__":
    unittest.main()
