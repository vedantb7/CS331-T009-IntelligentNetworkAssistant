"""Streamable HTTP transport: assistant -> MCP client -> HTTP(S) -> INA MCP server.

A real uvicorn server runs the same ASGI app as the Pi (stateless, JSON
responses, bearer-token auth, Host/Origin checks) on a free localhost port.
Skipped when the MCP SDK is not installed.
"""

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None

if MCP_AVAILABLE:
    import uvicorn

    from assistant.client import NetworkAssistant
    from assistant.contract import ToolCall
    from assistant.llm import RuleBasedBackend
    from assistant.transport import McpConnectionError, McpTransport
    from inav2.mcp_server import server, tools
    from inav2.network.service import NetworkService
    from inav2.policy.guard import PolicyGuard
    from inav2.tests.unit.test_mcp_server import LEASES, MockCombinedRunner


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class HttpServerFixture(unittest.TestCase):
    """Starts the INA MCP HTTP app on 127.0.0.1 with a mock kernel and demo role tokens."""

    tls = False

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.kernel = MockCombinedRunner()
        self._orig_service = tools.default_service
        tools.default_service = NetworkService(
            runner=self.kernel, leases_map=dict(LEASES),
            lock_path=os.path.join(self.tmp, "l"), state_path=os.path.join(self.tmp, "s.json"),
            audit_path=os.path.join(self.tmp, "a.log"))
        tools._guard = PolicyGuard.from_config(audit_path=os.path.join(self.tmp, "p.jsonl"),
                                               env={"INA_DEMO_TOKENS": "1"})
        # The env token must be ignored over HTTP: set an admin one to prove it.
        self._env = mock.patch.dict(os.environ, {
            "INA_DEMO_TOKENS": "1", tools.TOKEN_ENV: "admin-token",
            "INA_COMMAND_LOG": os.path.join(self.tmp, "c.jsonl")})
        self._env.start()
        self._orig_require = tools.REQUIRE_REQUEST_TOKEN
        tools.REQUIRE_REQUEST_TOKEN = True
        # Fresh verifier so it reads the demo-token environment set above.
        server.mcp._token_verifier._authenticator = None

        self.port = _free_port()
        ssl_args = {}
        if self.tls:
            self.cert, key = os.path.join(self.tmp, "server.crt"), os.path.join(self.tmp, "server.key")
            subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                            "-nodes", "-days", "1", "-subj", "/CN=ina-router",
                            "-addext", "subjectAltName=IP:127.0.0.1", "-keyout", key, "-out", self.cert],
                           check=True, capture_output=True)
            ssl_args = {"ssl_certfile": self.cert, "ssl_keyfile": key}
        app = server.build_http_app("127.0.0.1", self.port)
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port,
                                                    log_level="error", **ssl_args))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.05)
        scheme = "https" if self.tls else "http"
        self.url = f"{scheme}://127.0.0.1:{self.port}/mcp"
        self.transports = []

    def tearDown(self):
        for t in self.transports:
            t.close()
        self.server.should_exit = True
        self.thread.join(timeout=10)
        tools.REQUIRE_REQUEST_TOKEN = self._orig_require
        tools.default_service = self._orig_service
        tools._guard = None
        server.mcp._token_verifier._authenticator = None
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def connect(self, token, **kw):
        t = McpTransport(url=self.url, token=token, **kw)
        self.transports.append(t)
        return t

    def raw_post(self, headers):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code


@unittest.skipUnless(MCP_AVAILABLE, "MCP Python SDK not installed")
class TestStreamableHttp(HttpServerFixture):

    def test_tools_list_over_http(self):
        t = self.connect("operator-token")
        self.assertEqual(len(t.tools), 8)
        self.assertEqual(t.tools["limit_bandwidth"].input_schema["properties"]["download_kbit"]["maximum"], 20000)

    def test_full_pipeline_over_http(self):
        assistant = NetworkAssistant(transport=self.connect("operator-token"), backend=RuleBasedBackend())
        ok = assistant.handle("limit guest-phone to 5 mbps")
        self.assertEqual(ok.outcome, "success", ok.reply)
        self.assertTrue(any(c[0] == "tc" for c in self.kernel.commands))
        denied = assistant.handle("block admin-laptop")
        self.assertEqual((denied.outcome, denied.result["decision"]["rule"]), ("denied", "R-protected"))
        bad_args = assistant.handle("limit guest-phone to 50 mbps")
        self.assertEqual(bad_args.outcome, "denied")

    def test_role_comes_from_each_request_not_the_environment(self):
        # Server env holds an admin token; an operator's request must still act as operator.
        # 1 Mbit/s is inside the admin's range (1000+ kbit/s) but below the operator's (2000+).
        args = {"client": "lab-laptop-3", "download_kbit": 1000, "upload_kbit": 1000}
        operator = self.connect("operator-token")
        res = operator.call(ToolCall(tool="limit_bandwidth", arguments=args))
        self.assertEqual((res.status, res.decision.rule), ("denied", "R-rate-range"))
        admin = self.connect("admin-token")
        res = admin.call(ToolCall(tool="limit_bandwidth", arguments=args))
        self.assertEqual(res.status, "success", res.detail)

    def test_bad_or_missing_token_is_rejected(self):
        with self.assertRaises(McpConnectionError) as ctx:
            self.connect("not-a-token")
        self.assertIn("role token", str(ctx.exception))
        self.assertEqual(self.raw_post({}), 401)
        self.assertEqual(self.kernel.commands, [])

    def test_dns_rebinding_protection(self):
        auth = {"Authorization": "Bearer operator-token"}
        self.assertEqual(self.raw_post({**auth, "Origin": "https://evil.example"}), 403)
        self.assertEqual(self.raw_post({**auth, "Host": f"evil.example:{self.port}"}), 421)

    def test_plain_http_refused_off_localhost(self):
        with self.assertRaises(SystemExit):
            server.serve_http("192.168.50.1", 8443)


@unittest.skipUnless(MCP_AVAILABLE and shutil.which("openssl"), "MCP SDK and openssl needed")
class TestStreamableHttps(HttpServerFixture):
    tls = True

    def test_pinned_certificate(self):
        t = self.connect("operator-token", ca_file=self.cert)
        self.assertIn("ban_client", t.tools)

    def test_untrusted_certificate_rejected(self):
        with self.assertRaises(McpConnectionError) as ctx:
            self.connect("operator-token")
        self.assertIn("TLS certificate is not trusted", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
