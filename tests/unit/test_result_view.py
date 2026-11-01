"""Tests for workflow steps 6 and 7.

Step 6: the MCP result reaches the LLM as a fixed, structured view that never
contains the commands run on the router or their output.
Step 7: the LLM writes the reply from that view; replies inconsistent with the
result are rejected and the deterministic template is used instead.
"""

import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from assistant.client import NetworkAssistant
from assistant.contract import Decision, ToolResult, Verification
from assistant.llm import RuleBasedBackend, _coerce_llm_reply
from assistant.result_view import llm_result_view, mentions_command
from assistant.transport import MockTransport

MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None

VIEW_KEYS = {"version", "tool", "device", "status", "verified", "summary", "reason", "outcome"}


class CapturingBackend(RuleBasedBackend):
    """Rule-based interpretation; records every view and answers like a well-behaved LLM."""

    name = "capturing"

    def __init__(self, reply=None, status_override=None):
        self.views = []
        self.reply = reply
        self.status_override = status_override

    def explain(self, request, result_view):
        self.views.append(json.loads(json.dumps(result_view)))  # must be plain JSON
        payload = {"status": self.status_override or result_view["status"],
                   "reply": self.reply or f"LLM says: {result_view['status']}."}
        return _coerce_llm_reply(payload, result_view)


class TestResultView(unittest.TestCase):
    """Step 6: the structured format."""

    def test_view_is_whitelisted_and_command_free(self):
        result = ToolResult(
            status="success", tool="block_client", device="guest-phone",
            decision=Decision("ALLOW", "ok"),
            verification=Verification(passed=True, measured="read back from kernel state"),
            detail="Successfully blocked traffic between 'guest-phone' and 'internet'.",
            data={"blocked": True, "changed": True, "target": "internet", "client_ip": "192.168.50.34",
                  "commands": [{"argv": ["nft", "add", "element"], "stdout": "", "exit_code": 0}],
                  "argv": ["nft"], "stdout": "table inet ina_filter", "mac": "aa:bb:cc:dd:ee:04"},
        )
        view = llm_result_view(result)
        self.assertEqual(set(view), VIEW_KEYS)
        self.assertEqual(view["outcome"], {"blocked": True, "changed": True, "target": "internet",
                                           "client_ip": "192.168.50.34"})
        self.assertTrue(view["verified"])
        self.assertFalse(mentions_command(json.dumps(view)))

    def test_rates_are_given_in_mbps(self):
        view = llm_result_view(ToolResult(status="success", tool="limit_bandwidth", device="guest-phone",
                                          data={"download_kbit": 5000, "upload_kbit": 2000}))
        self.assertEqual(view["outcome"], {"download_mbps": 5.0, "upload_mbps": 2.0})

    def test_failure_text_naming_a_command_is_withheld(self):
        view = llm_result_view(ToolResult(
            status="failure", tool="block_client", device="guest-phone",
            detail="Failed to apply nftables block rule: Error: Could not process rule"))
        self.assertEqual(view["reason"], "The change could not be completed on the router.")

    def test_failure_summary_with_reference_is_kept(self):
        view = llm_result_view(ToolResult(
            status="failure", tool="block_client", device="guest-phone",
            detail="The router could not apply the change (reference 4d07ff74fae3)."))
        self.assertIn("reference 4d07ff74fae3", view["reason"])

    def test_denial_reason_comes_from_policy(self):
        view = llm_result_view(ToolResult(
            status="denied", tool="block_client", device="admin-laptop",
            decision=Decision("DENY", "'admin-laptop' is a protected device and cannot be changed.",
                              "R-protected")))
        self.assertEqual(view["reason"], "'admin-laptop' is a protected device and cannot be changed.")
        self.assertNotIn("R-protected", json.dumps(view))  # rule IDs are not for the reply

    def test_status_rows(self):
        view = llm_result_view(ToolResult(status="success", tool="get_status", data={"devices": [
            {"name": "guest-phone", "associated": True, "blocked": ["internet"],
             "download_kbit": 5000, "upload_kbit": 5000, "banned": False, "ip": "192.168.50.34"}]}))
        self.assertEqual(view["devices"], [{"name": "guest-phone", "associated": True,
                                            "blocked": ["internet"], "limit_mbps": 5.0, "banned": False}])


class TestReplyChecks(unittest.TestCase):
    """Step 7: only consistent, command-free replies are shown."""

    view = {"status": "denied"}

    def test_accepts_consistent_reply(self):
        self.assertEqual(_coerce_llm_reply({"status": "denied", "reply": " Not done:  protected. "}, self.view),
                         "Not done: protected.")

    def test_rejects_status_mismatch(self):
        self.assertIsNone(_coerce_llm_reply({"status": "success", "reply": "Done!"}, self.view))

    def test_rejects_command_mentions_and_bad_shapes(self):
        self.assertIsNone(_coerce_llm_reply({"status": "denied", "reply": "I ran nft list ruleset."}, self.view))
        self.assertIsNone(_coerce_llm_reply({"status": "denied", "reply": ""}, self.view))
        self.assertIsNone(_coerce_llm_reply({"status": "denied", "reply": "x" * 601}, self.view))
        self.assertIsNone(_coerce_llm_reply(["denied"], self.view))


class TestAssistantReplies(unittest.TestCase):
    """The assistant hands the view to the LLM and falls back to templates."""

    def test_llm_reply_used_and_view_recorded(self):
        backend = CapturingBackend()
        rec = NetworkAssistant(transport=MockTransport(), backend=backend).handle("block admin-laptop")
        self.assertEqual(rec.outcome, "denied")
        self.assertEqual(rec.reply_source, "llm")
        self.assertEqual(rec.reply, "LLM says: denied.")
        self.assertEqual(rec.llm_view, backend.views[-1])

    def test_inconsistent_llm_reply_falls_back_to_template(self):
        backend = CapturingBackend(reply="All done, blocked!", status_override="success")
        rec = NetworkAssistant(transport=MockTransport(), backend=backend).handle("block admin-laptop")
        self.assertEqual(rec.reply_source, "template")
        self.assertTrue(rec.reply.startswith("I can't do that"))

    def test_rule_based_backend_uses_template(self):
        rec = NetworkAssistant(transport=MockTransport(), backend=RuleBasedBackend()).handle(
            "limit guest-phone to 5 mbps")
        self.assertEqual((rec.outcome, rec.reply_source), ("success", "template"))
        self.assertIsNotNone(rec.llm_view)

    def test_preview_template_never_says_done(self):
        rec = NetworkAssistant(transport=MockTransport(), backend=RuleBasedBackend()).handle(
            "ban guest-phone for 30 minutes")
        self.assertEqual(rec.outcome, "preview")
        self.assertFalse(rec.reply.startswith("Done"), rec.reply)

    def test_explain_exception_falls_back(self):
        backend = RuleBasedBackend()
        with mock.patch.object(backend, "explain", side_effect=RuntimeError("API down")):
            rec = NetworkAssistant(transport=MockTransport(), backend=backend).handle("list devices")
        self.assertEqual(rec.reply_source, "template")


@unittest.skipUnless(MCP_AVAILABLE, "MCP Python SDK not installed")
class TestViewThroughMcpPipeline(unittest.TestCase):
    """Real commands run (mock kernel); the LLM's view must not contain any of them."""

    def setUp(self):
        from assistant.transport import McpTransport
        from inav2.mcp_server import server, tools
        from inav2.mcp_server.tools import TOKEN_ENV
        from inav2.network.service import NetworkService
        from inav2.policy.guard import PolicyGuard
        from inav2.tests.unit.test_mcp_server import LEASES, MockCombinedRunner

        self.tools = tools
        self.tmp = tempfile.mkdtemp()
        self.kernel = MockCombinedRunner()
        self._orig_service = tools.default_service
        tools.default_service = NetworkService(
            runner=self.kernel, leases_map=dict(LEASES),
            lock_path=os.path.join(self.tmp, "l"), state_path=os.path.join(self.tmp, "s.json"),
            audit_path=os.path.join(self.tmp, "a.log"))
        tools._guard = PolicyGuard.from_config(audit_path=os.path.join(self.tmp, "p.jsonl"),
                                               env={"INA_DEMO_TOKENS": "1"})
        self._env = mock.patch.dict(os.environ, {TOKEN_ENV: "operator-token",
                                                 "INA_COMMAND_LOG": os.path.join(self.tmp, "c.jsonl")})
        self._env.start()
        self.transport = McpTransport(server=server.mcp)
        self.backend = CapturingBackend()
        self.assistant = NetworkAssistant(transport=self.transport, backend=self.backend)

    def tearDown(self):
        self.transport.close()
        self._env.stop()
        self.tools.default_service = self._orig_service
        self.tools._guard = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_success_view_has_no_commands(self):
        rec = self.assistant.handle("block guest-phone")
        self.assertEqual(rec.outcome, "success", rec.reply)
        self.assertTrue(any(c[0] == "nft" for c in self.kernel.commands))  # commands did run
        dumped = json.dumps(self.backend.views[-1])
        self.assertFalse(mentions_command(dumped), dumped)
        for word in ("ina_filter", "add element", "argv"):
            self.assertNotIn(word, dumped)
        self.assertEqual(rec.reply_source, "llm")

    def test_kernel_failure_reaches_llm_as_summary_with_reference(self):
        self.kernel.nft_runner.fail_next_apply = True
        rec = self.assistant.handle("block guest-phone")
        self.assertEqual(rec.outcome, "failure")
        view = self.backend.views[-1]
        self.assertIn("could not apply the change", view["reason"])
        self.assertIn("reference", view["reason"])
        self.assertNotIn("could not add element", json.dumps(view))  # raw stderr stays in the logs


if __name__ == "__main__":
    unittest.main()
