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
from inav2.network import command_log, netctl, privileged
from inav2.network.command_log import request_scope, valid_request_id, verify_command_log
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


def without_request_id(argv):
    """argv after the sudo prefix, minus '--request-id <id>' (which must be present and valid)."""
    rest = list(argv[len(PREFIX):])
    i = rest.index("--request-id")
    assert valid_request_id(rest[i + 1]), rest[i + 1]
    del rest[i:i + 2]
    return rest


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


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
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.calls[0][:len(PREFIX)], PREFIX)
        self.assertEqual(without_request_id(runner.calls[0]),
                         ["--json", "--role", "operator", "block", "--", "guest-phone", "internet"])
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
        self.assertEqual(without_request_id(runner.calls[0]), [
            "--json", "--role", "admin", "ban", "--minutes", "30", "--reason", "abuse", "--yes",
            "--", "guest-phone"])

    def test_request_id_and_returned_commands_join_the_trace(self):
        records = [{"seq": 7, "argv": ["nft", "add", "element"], "exit_code": 0}]
        runner = RecordingRunner({"ok": True, "result": block_result_dict(), "commands": records})
        with request_scope("abc123") as trace:
            NetctlService(runner=runner).block("guest-phone", "internet")
        argv = runner.calls[0]
        self.assertEqual(argv[argv.index("--request-id") + 1], "abc123")
        self.assertEqual(trace, records)

    def test_commands_return_even_when_the_operation_fails(self):
        records = [{"seq": 3, "argv": ["nft", "add", "element"], "exit_code": 1, "stderr": "boom"}]
        runner = RecordingRunner({"ok": False, "error": "KernelMutationError", "message": "boom",
                                  "commands": records}, code=1)
        with request_scope("abc124") as trace:
            with self.assertRaises(KernelMutationError):
                NetctlService(runner=runner).block("guest-phone", "internet")
        self.assertEqual(trace, records)

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
        self.command_log = os.path.join(self.tmp, "commands.jsonl")
        self._env = mock.patch.dict(os.environ, {"INA_COMMAND_LOG": self.command_log})
        self._env.start()
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
        self._env.stop()
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
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"], "ClientNotFoundError")
        self.assertEqual(set(env), {"ok", "error", "message", "request_id", "commands"})

    def test_separator_stops_option_injection(self):
        code, env = self.run_netctl("--json", "--role", "operator", "block", "--", "-rf", "internet")
        self.assertEqual(code, 1)
        self.assertFalse(env["ok"])
        self.assertEqual(self.kernel.commands, [])

    def test_ban_owner(self):
        code, env = self.run_netctl("--json", "--role", "viewer", "ban-owner", "--", "guest-phone")
        self.assertEqual(code, 0)
        self.assertEqual(env["result"], {"banned_by_role": None})
        self.assertEqual(env["commands"], [])

    def test_commands_are_returned_and_logged_as_they_run(self):
        code, env = self.run_netctl("--json", "--request-id", "req-42", "--role", "operator",
                                    "block", "--", "guest-phone", "internet")
        self.assertEqual(code, 0)
        self.assertEqual(env["request_id"], "req-42")
        returned = env["commands"]
        self.assertTrue(returned)
        self.assertTrue(all(c["argv"][0] == "nft" for c in returned))
        self.assertTrue(all({"exit_code", "stdout", "stderr", "duration_ms"} <= set(c) for c in returned))
        # Exactly the records in the command log: same seq, hash and request ID.
        logged = read_jsonl(self.command_log)
        self.assertEqual(returned, logged)
        self.assertTrue(all(r["request_id"] == "req-42" for r in logged))
        self.assertEqual(verify_command_log(self.command_log), (True, None))
        # The service audit entry carries the same request ID.
        audit = read_jsonl(os.path.join(self.tmp, "audit.log"))
        self.assertEqual(audit[-1]["details"]["request_id"], "req-42")

    def test_failed_command_output_comes_back(self):
        self.kernel.nft_runner.fail_next_apply = True
        code, env = self.run_netctl("--json", "--role", "operator", "block", "--", "guest-phone", "internet")
        self.assertEqual(code, 1)
        failing = [c for c in env["commands"] if c["exit_code"] != 0]
        self.assertTrue(failing)
        self.assertIn("could not add element", failing[0]["stderr"])

    def test_bad_request_id_rejected(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                netctl.main(["--json", "--request-id", "x; rm -rf /", "status"])

    def test_tampered_command_log_detected(self):
        self.run_netctl("--json", "--role", "viewer", "status", "--")
        self.run_netctl("--json", "--role", "operator", "block", "--", "guest-phone", "internet")
        lines = open(self.command_log, encoding="utf-8").read().splitlines()
        rec = json.loads(lines[0]); rec["exit_code"] = 99
        lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        with open(self.command_log, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        self.assertEqual(verify_command_log(self.command_log), (False, 1))

    def test_logging_failure_does_not_hide_the_result(self):
        with mock.patch.object(command_log, "append_record", side_effect=OSError("disk full")):
            code, env = self.run_netctl("--json", "--role", "operator", "block", "--", "guest-phone", "internet")
        self.assertEqual(code, 0)
        self.assertTrue(env["result"]["blocked"])
        self.assertTrue(all(c.get("log_error") == "disk full" for c in env["commands"]))


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
        self.assertEqual(without_request_id(self.boundary.calls[-1]),
                         ["--json", "--role", "operator", "block", "--", "guest-phone", "internet"])
        self.assertTrue(any(c[0] == "nft" for c in self.kernel.commands))

    def test_role_comes_from_token(self):
        os.environ[TOKEN_ENV] = "admin-token"
        tools.limit_bandwidth(client="lab-laptop-3", download_kbit=5000, upload_kbit=5000)
        self.assertIn("--role admin", [" ".join(without_request_id(c)[1:3]) for c in self.boundary.calls])

    def test_output_returns_to_mcp_and_all_logs_share_the_request_id(self):
        tools.block_client(client="guest-phone", target="internet")
        policy = read_jsonl(os.path.join(self.tmp, "policy.jsonl"))
        decision, outcome = policy[-2], policy[-1]
        self.assertEqual((decision["event"], outcome["event"]), ("decision", "outcome"))
        rid = decision["request_id"]
        self.assertEqual(outcome["request_id"], rid)
        # The request ID crossed the boundary ...
        argv = self.boundary.calls[-1]
        self.assertEqual(argv[argv.index("--request-id") + 1], rid)
        # ... every command was logged on the root side under it ...
        logged = [r for r in read_jsonl(self.command_log) if r["request_id"] == rid]
        self.assertTrue(logged)
        # ... and came back to the MCP server, whose outcome references the same records.
        self.assertEqual([c["seq"] for c in outcome["commands"]], [r["seq"] for r in logged])
        self.assertEqual([c["argv"] for c in outcome["commands"]], [r["argv"] for r in logged])
        audit = read_jsonl(os.path.join(self.tmp, "audit.log"))
        self.assertEqual(audit[-1]["details"]["request_id"], rid)

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
