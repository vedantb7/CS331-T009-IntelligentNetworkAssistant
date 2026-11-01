"""Unit tests for the INA privilege boundary (workflow step 4).

The unprivileged MCP server must never run nft/tc/hostapd_cli itself: allowed
calls go through 'sudo -n ina-netctl --json ...' (network/privileged.py), and
ina-netctl runs them through the service layer as root (network/netctl.py).
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from inav2.mcp_server import tools
from inav2.mcp_server.schemas import BlockResult
from inav2.mcp_server.tools import TOKEN_ENV, ToolError
from inav2.network import netctl, privileged
from inav2.network.privileged import DEFAULT_NETCTL_COMMAND, NetctlService
from inav2.network.service import (
    ClientNotFoundError,
    KernelMutationError,
    NetworkService,
    PolicyDeniedError,
)
from inav2.policy.guard import PolicyGuard
from inav2.tests.unit.test_mcp_server import LEASES, MockCombinedRunner

PREFIX = ["sudo", "-n", "/usr/local/sbin/ina-netctl"]


class RecordingRunner:
    """Stands in for subprocess: records argv and replies with a fixed envelope."""

    def __init__(self, reply=None, code=0, stderr=""):
        self.calls = []
        self.reply = reply if reply is not None else {"ok": True, "result": {}}
        self.code = code
        self.stderr = stderr

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        out = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return self.code, out, self.stderr


class InProcessNetctl:
    """Runs ina-netctl's main() in-process instead of 'sudo ina-netctl' (no root needed)."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = netctl.main(argv[len(PREFIX):])
        return code, out.getvalue(), ""


def block_result_dict(**overrides):
    data = {"client": "guest-phone", "client_ip": "192.168.50.34", "target": "internet",
            "blocked": True, "changed": True, "verified": True, "message": "ok"}
    data.update(overrides)
    return data


class TestNetctlServiceClient(unittest.TestCase):
    """The unprivileged half: argv construction and envelope parsing."""

    def test_default_command_is_sudo_netctl(self):
        self.assertEqual(DEFAULT_NETCTL_COMMAND.split(), PREFIX)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("INA_NETCTL_COMMAND", None)
            self.assertEqual(NetctlService().command, PREFIX)

    def test_block_argv_and_result(self):
        runner = RecordingRunner({"ok": True, "result": block_result_dict()})
        res = NetctlService(command=" ".join(PREFIX), runner=runner).block(
            "guest-phone", "internet", role="operator")
        self.assertEqual(runner.calls, [PREFIX + ["--json", "--role", "operator", "block",
                                                  "--", "guest-phone", "internet"]])
        self.assertIsInstance(res, BlockResult)
        self.assertTrue(res.blocked)
        self.assertEqual(res.client_ip, "192.168.50.34")

    def test_ban_options_precede_separator(self):
        runner = RecordingRunner({"ok": True, "result": {
            "client": "guest-phone", "client_ip": None, "mac": "aa:bb:cc:dd:ee:04",
            "preview": False, "requires_confirmation": False, "banned": True, "changed": True,
            "verified": True, "currently_associated": False, "expires_at": None, "message": None}})
        NetctlService(command=" ".join(PREFIX), runner=runner).ban(
            "guest-phone", duration_minutes=30, reason="abuse", confirm=True, role="admin")
        self.assertEqual(runner.calls[0][len(PREFIX):], [
            "--json", "--role", "admin", "ban", "--minutes", "30", "--reason", "abuse", "--yes",
            "--", "guest-phone"])

    def test_error_envelope_reraises_service_error_class(self):
        runner = RecordingRunner({"ok": False, "error": "PolicyDeniedError", "message": "protected"}, code=1)
        with self.assertRaises(PolicyDeniedError):
            NetctlService(runner=runner).block("admin-laptop", "all")
        runner.reply = {"ok": False, "error": "ClientNotFoundError", "message": "unknown"}
        with self.assertRaises(ClientNotFoundError):
            NetctlService(runner=runner).kick("nobody")

    def test_unknown_error_or_no_envelope_is_a_failure(self):
        runner = RecordingRunner({"ok": False, "error": "PermissionError", "message": "nope"}, code=1)
        with self.assertRaises(KernelMutationError):
            NetctlService(runner=runner).unblock("guest-phone", "all")

        sudo_denied = RecordingRunner("", code=1, stderr="sudo: a password is required")
        with self.assertRaises(KernelMutationError) as ctx:
            NetctlService(runner=sudo_denied).status()
        self.assertIn("sudo: a password is required", str(ctx.exception))

    def test_default_executor_selection(self):
        with mock.patch.dict(os.environ, {"INA_EXECUTOR": ""}):
            with mock.patch.object(privileged.os, "geteuid", return_value=1000):
                self.assertIsInstance(privileged.default_executor(), NetctlService)
            with mock.patch.object(privileged.os, "geteuid", return_value=0):
                self.assertIsInstance(privileged.default_executor(), NetworkService)
        with mock.patch.dict(os.environ, {"INA_EXECUTOR": "netctl"}):
            with mock.patch.object(privileged.os, "geteuid", return_value=0):
                self.assertIsInstance(privileged.default_executor(), NetctlService)


class NetctlFixture(unittest.TestCase):
    """Base: ina-netctl backed by an in-process NetworkService with a mock kernel."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.kernel = MockCombinedRunner()
        self._orig_service = netctl.default_service
        netctl.default_service = NetworkService(
            runner=self.kernel,
            leases_map=dict(LEASES),
            lock_path=os.path.join(self.tmp, "ina.lock"),
            state_path=os.path.join(self.tmp, "state.json"),
            audit_path=os.path.join(self.tmp, "audit.log"),
        )

    def tearDown(self):
        netctl.default_service = self._orig_service
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_netctl(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = netctl.main(list(argv))
        return code, json.loads(out.getvalue())


class TestNetctlJsonInterface(NetctlFixture):
    """The privileged half: 'ina-netctl --json' envelopes."""

    def test_success_envelope(self):
        code, env = self.run_netctl("--json", "--role", "operator", "block", "--", "guest-phone", "internet")
        self.assertEqual(code, 0)
        self.assertTrue(env["ok"])
        self.assertTrue(env["result"]["blocked"])

    def test_error_envelope(self):
        code, env = self.run_netctl("--json", "--role", "operator", "block", "--", "printer-9", "internet")
        self.assertEqual(code, 1)
        self.assertEqual(env, {"ok": False, "error": "ClientNotFoundError", "message": env["message"]})

    def test_separator_stops_option_injection(self):
        code, env = self.run_netctl("--json", "--role", "operator", "block", "--", "-rf", "internet")
        self.assertEqual(code, 1)
        self.assertFalse(env["ok"])
        self.assertEqual(self.kernel.commands, [])

    def test_ban_owner(self):
        code, env = self.run_netctl("--json", "--role", "viewer", "ban-owner", "--", "guest-phone")
        self.assertEqual((code, env), (0, {"ok": True, "result": {"banned_by_role": None}}))


class TestMcpToolsThroughBoundary(NetctlFixture):
    """MCP tool -> policy guard -> NetctlService -> ina-netctl -> service -> kernel."""

    def setUp(self):
        super().setUp()
        self.boundary = InProcessNetctl()
        self._orig_tools_service = tools.default_service
        tools.default_service = NetctlService(command=" ".join(PREFIX), runner=self.boundary)
        tools._guard = PolicyGuard.from_config(audit_path=os.path.join(self.tmp, "policy.jsonl"),
                                               env={"INA_DEMO_TOKENS": "1"})
        self._old_token = os.environ.get(TOKEN_ENV)
        os.environ[TOKEN_ENV] = "operator-token"

    def tearDown(self):
        tools.default_service = self._orig_tools_service
        tools._guard = None
        if self._old_token is None:
            os.environ.pop(TOKEN_ENV, None)
        else:
            os.environ[TOKEN_ENV] = self._old_token
        super().tearDown()

    def test_allowed_call_crosses_boundary_with_guard_role(self):
        res = tools.block_client(client="guest-phone", target="internet")
        self.assertTrue(res.blocked)
        self.assertTrue(res.verified)
        self.assertEqual(self.boundary.calls[-1][len(PREFIX):],
                         ["--json", "--role", "operator", "block", "--", "guest-phone", "internet"])
        self.assertTrue(any(c[0] == "nft" for c in self.kernel.commands))

    def test_role_comes_from_token(self):
        os.environ[TOKEN_ENV] = "admin-token"
        tools.limit_bandwidth(client="lab-laptop-3", download_kbit=5000, upload_kbit=5000)
        self.assertIn(["--role", "admin"], [c[len(PREFIX) + 1:len(PREFIX) + 3] for c in self.boundary.calls])

    def test_denied_call_never_crosses_boundary(self):
        with self.assertRaises(ToolError):
            tools.block_client(client="admin-laptop", target="all")
        with self.assertRaises(ToolError):
            tools.block_client(client="lab-laptop-3", target="all")
        self.assertEqual(self.boundary.calls, [])
        self.assertEqual(self.kernel.commands, [])

    def test_kernel_failure_reported_as_failed_through_boundary(self):
        self.kernel.nft_runner.fail_next_apply = True
        with self.assertRaises(ToolError) as ctx:
            tools.block_client(client="guest-phone", target="internet")
        self.assertTrue(str(ctx.exception).startswith("Failed:"), str(ctx.exception))

    def test_status_and_ban_preview_through_boundary(self):
        status = tools.get_status()
        self.assertEqual(len(status.clients), len(LEASES))
        preview = tools.ban_client(client="guest-phone", duration_minutes=30)
        self.assertTrue(preview.preview)


if __name__ == "__main__":
    unittest.main()
