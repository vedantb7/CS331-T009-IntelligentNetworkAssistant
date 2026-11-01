"""Per-person, device-bound role grants.

Nobody has a role by default; every device on the network is a client. The
admin grants a role to a person, bound to one registered device with a fixed
IP, and the token only works from that address.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from inav2.network import netctl
from inav2.policy import grants
from inav2.policy.authorization import TokenAuthenticator
from inav2.policy.grants import GrantError
from inav2.policy.guard import PolicyGuard

POLICY_DIR = Path(__file__).resolve().parents[2] / "policy"
ADMIN_LAPTOP_IP = "192.168.50.2"   # policy/devices.yaml
MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None


class GrantsFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "grants.json")
        self._env = mock.patch.dict(os.environ, {"INA_GRANTS_FILE": self.path})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def policy_with_fixed_client_ip(self) -> str:
        """Copy of policy/ where the client 'lab-laptop-3' has fixed IP 192.168.50.40."""
        d = os.path.join(self.tmp, "policy")
        shutil.copytree(POLICY_DIR, d, ignore=shutil.ignore_patterns("__pycache__", "*.py"))
        devices = Path(d, "devices.yaml")
        devices.write_text(devices.read_text().replace(
            '{name: lab-laptop-3, mac: "aa:bb:cc:dd:ee:03", class: client}',
            '{name: lab-laptop-3, mac: "aa:bb:cc:dd:ee:03", class: client, ip: "192.168.50.40"}'))
        return d


class TestGrantStore(GrantsFixture):

    def test_grant_stores_only_a_hash(self):
        g, token = grants.grant("dhruv", "admin", "admin-laptop")
        text = Path(self.path).read_text()
        self.assertNotIn(token, text)
        self.assertIn(g.token_sha256, text)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o640)
        self.assertNotIn("token_sha256", json.dumps([x.public() for x in grants.load_grants()]))

    def test_validation(self):
        with self.assertRaises(GrantError):
            grants.grant("Dhruv!", "admin", "admin-laptop")          # bad name
        with self.assertRaises(GrantError):
            grants.grant("dhruv", "superuser", "admin-laptop")       # unknown role
        with self.assertRaises(GrantError):
            grants.grant("dhruv", "admin", "no-such-device")         # not registered
        with self.assertRaises(GrantError) as ctx:
            grants.grant("dhruv", "operator", "guest-phone")         # no fixed IP to bind to
        self.assertIn("fixed 'ip'", str(ctx.exception))
        grants.grant("dhruv", "admin", "admin-laptop")
        with self.assertRaises(GrantError):
            grants.grant("dhruv", "viewer", "admin-laptop")          # one role per person

    def test_authenticate_revoke_rotate_take_effect_immediately(self):
        grants.save_grants([], self.path)  # as at install time: the server starts before any grant
        auth = TokenAuthenticator.from_env({"INA_GRANTS_FILE": self.path})
        _, token = grants.grant("dhruv", "admin", "admin-laptop")
        p = auth.authenticate(token)
        self.assertEqual((p.name, p.role, p.device, p.label), ("dhruv", "admin", "admin-laptop", "dhruv:admin"))
        _, new_token = grants.rotate("dhruv")
        self.assertIsNone(auth.authenticate(token))
        self.assertEqual(auth.authenticate(new_token).name, "dhruv")
        grants.revoke("dhruv")
        self.assertIsNone(auth.authenticate(new_token))

    def test_no_grants_and_no_demo_is_a_config_error(self):
        from inav2.policy.authorization import AuthConfigError
        with self.assertRaises(AuthConfigError):
            TokenAuthenticator.from_env({"INA_GRANTS_FILE": self.path})  # file absent
        grants.save_grants([], self.path)
        TokenAuthenticator.from_env({"INA_GRANTS_FILE": self.path})     # empty file is fine


class TestDeviceBinding(GrantsFixture):

    def guard(self, policy_dir=None):
        return PolicyGuard.from_config(policy_dir, audit_path=os.path.join(self.tmp, "p.jsonl"),
                                       env={"INA_GRANTS_FILE": self.path})

    def test_token_only_works_from_its_device(self):
        _, token = grants.grant("dhruv", "admin", "admin-laptop")
        g = self.guard()
        ok = g.check(token, "kick_client", {"client": "guest-phone"}, client_address=ADMIN_LAPTOP_IP)
        self.assertTrue(ok.allowed, ok.decision.reason)
        self.assertEqual(ok.principal.label, "dhruv:admin")
        for address in ("192.168.50.47", None):
            res = g.check(token, "kick_client", {"client": "guest-phone"}, client_address=address)
            self.assertEqual(res.decision.rule, "R-token-device")

    def test_cannot_act_on_own_bound_device(self):
        policy = self.policy_with_fixed_client_ip()
        grants.grant("khushi", "operator", "lab-laptop-3", policy_dir=policy)
        token = None  # rotate to obtain a token while keeping the grant
        _, token = grants.rotate("khushi")
        g = self.guard(policy)
        res = g.check(token, "kick_client", {"client": "lab-laptop-3"}, client_address="192.168.50.40")
        self.assertEqual(res.decision.rule, "R-self-device")
        res = g.check(token, "kick_client", {"client": "guest-phone"}, client_address="192.168.50.40")
        self.assertTrue(res.allowed, res.decision.reason)

    def test_unbound_demo_tokens_still_work_for_development(self):
        g = PolicyGuard.from_config(audit_path=os.path.join(self.tmp, "p.jsonl"),
                                    env={"INA_DEMO_TOKENS": "1", "INA_GRANTS_FILE": self.path})
        self.assertTrue(g.check("operator-token", "kick_client", {"client": "guest-phone"}).allowed)


class TestRolesCli(GrantsFixture):

    def run_cli(self, *argv, sudo_user="team009"):
        out = io.StringIO()
        env = {"SUDO_USER": sudo_user, "INA_AUDIT_LOG": os.path.join(self.tmp, "audit.log")}
        with mock.patch.dict(os.environ, env), mock.patch.object(netctl, "append_audit_entry") as audit, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = netctl.main(list(argv))
        return code, out.getvalue(), audit

    def test_grant_list_rotate_revoke(self):
        code, out, audit = self.run_cli("roles", "grant", "dhruv", "admin", "--device", "admin-laptop")
        self.assertEqual(code, 0)
        self.assertIn("usable only from device 'admin-laptop'", out)
        token = out.strip().splitlines()[-1].strip()
        self.assertEqual(TokenAuthenticator.from_env({"INA_GRANTS_FILE": self.path}).authenticate(token).name,
                         "dhruv")
        self.assertEqual(audit.call_args.kwargs["action"], "roles_grant")
        self.assertNotIn("token", audit.call_args.kwargs["details"])

        code, out, _ = self.run_cli("roles", "list")
        self.assertIn("dhruv", out)
        self.assertNotIn(token, out)
        self.assertEqual(self.run_cli("roles", "revoke", "dhruv")[0], 0)
        self.assertIn("No roles granted", self.run_cli("roles", "list")[1])

    def test_service_account_cannot_manage_roles(self):
        code, _, audit = self.run_cli("roles", "grant", "mallory", "admin", "--device", "admin-laptop",
                                      sudo_user="ina")
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.path))
        audit.assert_not_called()


@unittest.skipUnless(MCP_AVAILABLE, "MCP Python SDK not installed")
class TestBindingOverHttp(GrantsFixture):
    """The HTTP layer hands the real TCP peer address to the guard."""

    def test_peer_address_reaches_the_guard(self):
        from inav2.mcp_server import server, tools
        from inav2.tests.unit.test_mcp_http import HttpServerFixture

        _, token = grants.grant("dhruv", "admin", "admin-laptop")
        seen = []

        class Http(HttpServerFixture):
            def runTest(self):
                pass

        fx = Http()
        fx.setUp()
        try:
            os.environ["INA_GRANTS_FILE"] = self.path
            tools._guard = PolicyGuard.from_config(audit_path=os.path.join(self.tmp, "p.jsonl"),
                                                   env={"INA_GRANTS_FILE": self.path, "INA_DEMO_TOKENS": "1"})
            server.mcp._token_verifier._authenticator = TokenAuthenticator.from_env(
                {"INA_GRANTS_FILE": self.path})
            real_check = tools._guard.check
            tools._guard.check = lambda *a, **kw: (seen.append(kw.get("client_address")), real_check(*a, **kw))[1]
            from assistant.contract import ToolCall
            res = fx.connect(token).call(ToolCall(tool="kick_client", arguments={"client": "guest-phone"}))
        finally:
            fx.tearDown()
        self.assertEqual(seen, ["127.0.0.1"])
        self.assertEqual((res.status, res.decision.rule), ("denied", "R-token-device"))

    def test_server_never_trusts_forwarded_headers(self):
        from inav2.mcp_server import server
        with mock.patch("uvicorn.run") as run:
            server.serve_http("127.0.0.1", 8000)
        self.assertIs(run.call_args.kwargs["proxy_headers"], False)


if __name__ == "__main__":
    unittest.main()
