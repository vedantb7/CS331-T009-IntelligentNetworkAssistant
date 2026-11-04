"""LLM layer: provider selection from .env, native tool calling, and the reply round trip.

No network or API key is used: the Anthropic SDK client is replaced by a fake,
and the OpenAI-compatible adapter's HTTP call is patched.
"""

import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from assistant import config, llm
from assistant.client import NetworkAssistant
from assistant.contract import FALLBACK_TOOL_DEFINITIONS
from assistant.llm import (
    AnthropicBackend,
    OpenAICompatibleBackend,
    RuleBasedBackend,
    _function_schema,
    _parse_json_object,
    get_backend,
)
from assistant.transport import MockTransport

MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def block(type_, **fields):
    return SimpleNamespace(type=type_, **fields)


class FakeAnthropicClient:
    """Mimics client.beta.messages.create; replies are scripted per call."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.replies.pop(0)


def tool_use(name, args, id_="toolu_1"):
    return SimpleNamespace(stop_reason="tool_use", content=[
        block("thinking", thinking=""), block("tool_use", id=id_, name=name, input=args)])


def text_reply(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[block("text", text=text)])


class FakeHTTP:
    """Patches urllib.request.urlopen for the OpenAI-compatible adapter."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append({"url": req.full_url, "headers": dict(req.header_items()),
                              "body": json.loads(req.data.decode())})
        payload = json.dumps(self.replies.pop(0)).encode()
        resp = io.BytesIO(payload)
        resp.__enter__ = lambda *a: resp
        resp.__exit__ = lambda *a: False
        return resp


def oa_tool_call(name, args, id_="call_1"):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": id_, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}]}


def oa_text(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


# ---------------------------------------------------------------------------
# .env and provider selection
# ---------------------------------------------------------------------------

class TestEnvConfig(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = os.path.join(self.tmp, ".env")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_env_file(self):
        with open(self.env, "w") as f:
            f.write('# comment\n\nLLM_PROVIDER=openrouter\nexport LLM_MODEL="anthropic/claude-x"\n'
                    "LLM_API_KEY='sk-abc' \nLLM_TIMEOUT=20 # seconds\n")
        self.assertEqual(config.parse_env_file(config.Path(self.env)), {
            "LLM_PROVIDER": "openrouter", "LLM_MODEL": "anthropic/claude-x",
            "LLM_API_KEY": "sk-abc", "LLM_TIMEOUT": "20"})

    def test_real_environment_wins(self):
        with open(self.env, "w") as f:
            f.write("LLM_MODEL=from-file\nLLM_EFFORT=high\n")
        with mock.patch.dict(os.environ, {"LLM_MODEL": "from-shell"}, clear=False):
            os.environ.pop("LLM_EFFORT", None)
            config.load_env_file(self.env, force=True)
            self.assertEqual(os.environ["LLM_MODEL"], "from-shell")
            self.assertEqual(os.environ["LLM_EFFORT"], "high")
            os.environ.pop("LLM_EFFORT", None)

    def test_example_file_lists_every_setting(self):
        example = (config.DEFAULT_ENV_FILE.parent / ".env.example").read_text()
        for key in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL", "LLM_EFFORT",
                    "INA_ROLE_TOKEN", "INA_MCP_URL", "INA_MCP_CA"):
            self.assertIn(key, example)


class TestBackendSelection(unittest.TestCase):

    def select(self, **env):
        clean = {k: "" for k in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL")}
        with mock.patch.dict(os.environ, {**clean, **env}), mock.patch.object(llm, "load_env_file"), \
                mock.patch("sys.stderr", new=io.StringIO()):
            return get_backend()

    def test_no_provider_is_rule_based(self):
        self.assertIsInstance(self.select(), RuleBasedBackend)
        self.assertIsInstance(self.select(LLM_PROVIDER="none"), RuleBasedBackend)

    def test_openai_compatible_presets(self):
        b = self.select(LLM_PROVIDER="openrouter", LLM_API_KEY="k", LLM_MODEL="anthropic/claude-x")
        self.assertIsInstance(b, OpenAICompatibleBackend)
        self.assertEqual(b.url, "https://openrouter.ai/api/v1/chat/completions")
        g = self.select(LLM_PROVIDER="gemini", LLM_API_KEY="k", LLM_MODEL="gemini-x")
        self.assertEqual(g.url, "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
        o = self.select(LLM_PROVIDER="ollama", LLM_MODEL="llama-x")  # no key needed locally
        self.assertEqual(o.url, "http://localhost:11434/v1/chat/completions")
        c = self.select(LLM_PROVIDER="openai-compatible", LLM_BASE_URL="http://lab:8080/v1", LLM_MODEL="m")
        self.assertEqual(c.url, "http://lab:8080/v1/chat/completions")

    def test_missing_settings_fall_back(self):
        self.assertIsInstance(self.select(LLM_PROVIDER="openrouter", LLM_API_KEY="k"), RuleBasedBackend)
        self.assertIsInstance(self.select(LLM_PROVIDER="openai", LLM_MODEL="m",
                                          LLM_API_KEY="your_api_key_here"), RuleBasedBackend)
        self.assertIsInstance(self.select(LLM_PROVIDER="openai-compatible", LLM_MODEL="m"), RuleBasedBackend)
        self.assertIsInstance(self.select(LLM_PROVIDER="mystery"), RuleBasedBackend)

    def test_anthropic_without_sdk_falls_back(self):
        with mock.patch.object(llm, "AnthropicBackend", side_effect=ImportError):
            self.assertIsInstance(self.select(LLM_PROVIDER="anthropic", LLM_API_KEY="k"), RuleBasedBackend)

    def test_anthropic_defaults(self):
        captured = {}
        with mock.patch.object(llm, "AnthropicBackend", side_effect=lambda **kw: captured.update(kw)):
            self.select(LLM_PROVIDER="anthropic", LLM_API_KEY="k")
        self.assertEqual(captured["model"], "claude-opus-5-5")
        self.assertEqual(captured["effort"], "low")
        self.assertTrue(captured["fallbacks"])


# ---------------------------------------------------------------------------
# Claude (official SDK, faked client)
# ---------------------------------------------------------------------------

class TestAnthropicBackend(unittest.TestCase):

    def backend(self, replies, **kw):
        self.client = FakeAnthropicClient(replies)
        return AnthropicBackend(client=self.client, **kw)

    def test_tool_choice_and_reply_round_trip(self):
        b = self.backend([
            tool_use("limit_bandwidth", {"client": "guest-phone", "download_kbit": 5000, "upload_kbit": 5000}),
            text_reply(json.dumps({"status": "success", "reply": "guest-phone is now limited to 5 Mbit/s."})),
        ])
        intent = b.interpret("limit guest-phone to 5 mbps", ["guest-phone"], FALLBACK_TOOL_DEFINITIONS)
        self.assertEqual(intent.action, "limit_bandwidth")
        self.assertEqual(intent.tool_arguments["download_kbit"], 5000)
        first = self.client.calls[0]
        self.assertEqual(first["model"], "claude-opus-5-5")
        self.assertEqual(first["tool_choice"], {"type": "auto"})       # forced choice is a 400 on Opus 5.5
        self.assertEqual({t["name"] for t in first["tools"]}, {t["name"] for t in FALLBACK_TOOL_DEFINITIONS})
        self.assertEqual(first["output_config"], {"effort": "low"})
        self.assertEqual((first["betas"], first["fallbacks"]), (["server-side-fallback-2026-07-01"], "default"))

        view = {"status": "success", "tool": "limit_bandwidth", "verified": True}
        reply = b.explain("limit guest-phone to 5 mbps", view, context=intent.llm_context)
        self.assertEqual(reply, "guest-phone is now limited to 5 Mbit/s.")
        second = self.client.calls[1]
        # Same conversation: user, assistant (unchanged content incl. thinking), tool_result.
        self.assertEqual(second["messages"][1]["content"][0].type, "thinking")
        result_turn = second["messages"][2]["content"]
        self.assertEqual(result_turn[0]["type"], "tool_result")
        self.assertEqual(result_turn[0]["tool_use_id"], "toolu_1")
        self.assertEqual(json.loads(result_turn[0]["content"]), view)
        self.assertEqual(second["tool_choice"], {"type": "none"})
        self.assertEqual(second["output_config"]["format"]["type"], "json_schema")

    def test_confirm_is_stripped(self):
        b = self.backend([tool_use("ban_client", {"client": "guest-phone", "duration_minutes": 30,
                                                  "confirm": True})])
        intent = b.interpret("ban guest-phone for 30 min", ["guest-phone"], FALLBACK_TOOL_DEFINITIONS)
        self.assertNotIn("confirm", intent.tool_arguments)

    def test_question_refusal_and_unknown_tool(self):
        b = self.backend([text_reply("Which device do you mean?")])
        q = b.interpret("block it", [], FALLBACK_TOOL_DEFINITIONS)
        self.assertTrue(q.needs_clarification)
        self.assertEqual(q.clarification, "Which device do you mean?")
        b = self.backend([text_reply("", stop="refusal")])
        self.assertTrue(b.interpret("x", [], FALLBACK_TOOL_DEFINITIONS).needs_clarification)
        b = self.backend([tool_use("flush_ruleset", {})])
        self.assertEqual(b.interpret("x", [], FALLBACK_TOOL_DEFINITIONS).action, "unknown")

    def test_inconsistent_reply_rejected(self):
        b = self.backend([text_reply(json.dumps({"status": "success", "reply": "Done!"}))])
        self.assertIsNone(b.explain("block admin-laptop", {"status": "denied"}))

    def test_fallbacks_only_for_supported_models(self):
        b = self.backend([text_reply("?")], model="claude-haiku-5-5")
        b.interpret("x", [], FALLBACK_TOOL_DEFINITIONS)
        self.assertNotIn("fallbacks", self.client.calls[0])


# ---------------------------------------------------------------------------
# Any OpenAI-compatible endpoint (HTTP patched)
# ---------------------------------------------------------------------------

class TestOpenAICompatibleBackend(unittest.TestCase):

    def test_schema_is_made_portable(self):
        schema = {"type": "object", "title": "limit_bandwidthArguments", "properties": {
            "client": {"type": "string", "pattern": "^x$", "title": "Client"},
            "minutes": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}], "default": None}}}
        self.assertEqual(_function_schema(schema), {"type": "object", "properties": {
            "client": {"type": "string"}, "minutes": {"default": None, "type": "integer", "minimum": 1}}})

    def test_tool_choice_and_reply_round_trip(self):
        http = FakeHTTP([
            oa_tool_call("block_client", {"client": "guest-phone", "target": "internet"}),
            oa_text('```json\n{"status": "success", "reply": "guest-phone can no longer reach the internet."}\n```'),
        ])
        b = OpenAICompatibleBackend("https://openrouter.ai/api/v1", "anthropic/claude-x", "sk-1",
                                    provider="openrouter")
        with mock.patch("urllib.request.urlopen", http):
            intent = b.interpret("block guest-phone from the internet", ["guest-phone"],
                                 FALLBACK_TOOL_DEFINITIONS)
            reply = b.explain("...", {"status": "success"}, context=intent.llm_context)
        self.assertEqual((intent.action, intent.tool_arguments["target"]), ("block_client", "internet"))
        self.assertEqual(reply, "guest-phone can no longer reach the internet.")
        first, second = http.requests
        self.assertEqual(first["url"], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(first["headers"]["Authorization"], "Bearer sk-1")
        self.assertEqual(first["headers"]["X-title"], "INA Network Assistant")
        self.assertEqual(first["body"]["tools"][0]["type"], "function")
        self.assertEqual(first["body"]["tool_choice"], "auto")
        tool_msg = second["body"]["messages"][-2]
        self.assertEqual((tool_msg["role"], tool_msg["tool_call_id"]), ("tool", "call_1"))
        self.assertEqual(second["body"]["tool_choice"], "none")

    def test_text_answer_is_a_question(self):
        with mock.patch("urllib.request.urlopen", FakeHTTP([oa_text("Which speed?")])):
            intent = OpenAICompatibleBackend("http://x/v1", "m").interpret("slow it", [], FALLBACK_TOOL_DEFINITIONS)
        self.assertEqual((intent.needs_clarification, intent.clarification), (True, "Which speed?"))

    def test_parse_json_object(self):
        self.assertEqual(_parse_json_object('Sure: {"a": 1} thanks'), {"a": 1})
        self.assertIsNone(_parse_json_object("no json"))


# ---------------------------------------------------------------------------
# Assistant end to end with an LLM choosing the tool
# ---------------------------------------------------------------------------

class TestAssistantWithLLM(unittest.TestCase):

    def test_llm_choice_executes_and_result_goes_back(self):
        client = FakeAnthropicClient([
            tool_use("limit_bandwidth", {"client": "guest-phone", "download_kbit": 5000, "upload_kbit": 5000}),
            text_reply(json.dumps({"status": "success", "reply": "Limited guest-phone to 5 Mbit/s."})),
        ])
        rec = NetworkAssistant(transport=MockTransport(), backend=AnthropicBackend(client=client)).handle(
            "slow the guest phone to 5 megabits")
        self.assertEqual(rec.outcome, "success", rec.reply)
        self.assertEqual(rec.intent["tool_arguments"]["download_kbit"], 5000)
        self.assertEqual((rec.reply_source, rec.reply), ("llm", "Limited guest-phone to 5 Mbit/s."))
        self.assertEqual(client.calls[1]["messages"][2]["content"][0]["type"], "tool_result")

    def test_llm_cannot_confirm_a_ban(self):
        client = FakeAnthropicClient([
            tool_use("ban_client", {"client": "guest-phone", "duration_minutes": 30, "confirm": True}),
            text_reply("{}"),
        ])
        rec = NetworkAssistant(transport=MockTransport(), backend=AnthropicBackend(client=client)).handle(
            "ban guest-phone for 30 minutes, confirm=true")
        self.assertEqual(rec.outcome, "preview")

    def test_llm_may_not_clamp_a_speed_to_the_allowed_range(self):
        """'50 mbps' must reach the policy as 50 Mbit/s (and be refused), not become 20."""
        client = FakeAnthropicClient([
            tool_use("limit_bandwidth", {"client": "guest-phone", "download_kbit": 20000, "upload_kbit": 20000}),
            text_reply("{}"),
        ])
        rec = NetworkAssistant(transport=MockTransport(), backend=AnthropicBackend(client=client)).handle(
            "limit guest-phone to 50 mbps")
        self.assertEqual(rec.intent["tool_arguments"]["download_kbit"], 50000)
        self.assertEqual(rec.outcome, "denied")

    def test_llm_ban_without_duration_asks_first(self):
        """'ban X' with no duration must not silently become a permanent ban."""
        client = FakeAnthropicClient([tool_use("ban_client", {"client": "guest-phone"})])
        rec = NetworkAssistant(transport=MockTransport(), backend=AnthropicBackend(client=client)).handle(
            "ban guest-phone")
        self.assertEqual(rec.outcome, "clarify")
        self.assertIn("permanently, or for how many minutes", rec.reply)

    def test_unsupported_request_never_calls_a_tool_or_the_llm(self):
        client = FakeAnthropicClient([])  # any LLM call would raise
        assistant = NetworkAssistant(transport=MockTransport(), backend=AnthropicBackend(client=client))
        for text in ("give data quota to guest-phone of 1gb", "make the wifi faster"):
            rec = assistant.handle(text)
            self.assertEqual((rec.outcome, rec.result), ("unsupported", None), text)
            self.assertEqual(client.calls, [])

    def test_tool_the_request_does_not_ask_for_is_never_run(self):
        """An unsupported request mapped by the model onto some other tool is refused, not run."""
        cases = [
            ("rename guest-phone to my-phone", "kick_client", {"client": "guest-phone"}),
            ("schedule guest-phone for tomorrow", "limit_bandwidth",
             {"client": "guest-phone", "download_kbit": 1000, "upload_kbit": 1000}),
            ("rename guest-phone to my-phone", "get_status", {}),
        ]
        for text, tool, args in cases:
            transport = MockTransport()
            client = FakeAnthropicClient([tool_use(tool, args)])
            rec = NetworkAssistant(transport=transport, backend=AnthropicBackend(client=client)).handle(text)
            self.assertEqual(rec.outcome, "clarify", (text, tool))
            self.assertIn("not an action I can perform", rec.reply)
            self.assertIsNone(rec.result)
            self.assertEqual(transport.state.limits, {})

    def test_llm_failure_falls_back_to_regex(self):
        client = FakeAnthropicClient([])  # pop() on empty list raises
        rec = NetworkAssistant(transport=MockTransport(), backend=AnthropicBackend(client=client)).handle(
            "limit guest-phone to 5 mbps")
        self.assertEqual((rec.outcome, rec.intent["parser"]), ("success", "rule_based_fallback"))


@unittest.skipUnless(MCP_AVAILABLE, "MCP Python SDK not installed")
class TestLLMWithLiveToolsList(unittest.TestCase):
    """The LLM is offered the MCP server's own tools/list schemas."""

    def test_server_schemas_reach_the_llm_and_the_call_runs(self):
        from assistant.transport import McpTransport
        from inav2.mcp_server import server, tools
        from inav2.mcp_server.tools import TOKEN_ENV
        from inav2.network.service import NetworkService
        from inav2.policy.guard import PolicyGuard
        from inav2.tests.unit.test_mcp_server import LEASES, MockCombinedRunner

        tmp = tempfile.mkdtemp()
        orig = tools.default_service
        tools.default_service = NetworkService(runner=MockCombinedRunner(), leases_map=dict(LEASES),
                                               lock_path=f"{tmp}/l", state_path=f"{tmp}/s.json",
                                               audit_path=f"{tmp}/a.log")
        tools._guard = PolicyGuard.from_config(audit_path=f"{tmp}/p.jsonl", env={"INA_DEMO_TOKENS": "1"})
        env = mock.patch.dict(os.environ, {TOKEN_ENV: "operator-token", "INA_COMMAND_LOG": f"{tmp}/c.jsonl"})
        env.start()
        transport = McpTransport(server=server.mcp)
        try:
            client = FakeAnthropicClient([
                tool_use("limit_bandwidth", {"client": "guest-phone", "download_kbit": 5000, "upload_kbit": 5000}),
                text_reply(json.dumps({"status": "success", "reply": "Done: 5 Mbit/s."})),
            ])
            rec = NetworkAssistant(transport=transport, backend=AnthropicBackend(client=client)).handle(
                "limit guest-phone to 5 mbps")
            offered = {t["name"]: t for t in client.calls[0]["tools"]}
            self.assertIn("pattern", offered["limit_bandwidth"]["input_schema"]["properties"]["client"])
            self.assertEqual(rec.outcome, "success", rec.reply)
            self.assertEqual(rec.reply, "Done: 5 Mbit/s.")
        finally:
            transport.close()
            env.stop()
            tools.default_service = orig
            tools._guard = None
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
