"""Tests for the guard (token -> role -> engine -> audit, failing closed) and the
service-layer adapter in policy/integration.py."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (str(REPO), str(REPO.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from policy import integration  # noqa: E402
from policy.audit import AuditError, AuditLog  # noqa: E402
from policy.authorization import TokenAuthenticator, hash_token  # noqa: E402
from policy.engine import PolicyEngine  # noqa: E402
from policy.guard import PolicyGuard  # noqa: E402

POLICY_DIR = REPO / "policy"
OP, AD, VW = "op-token-aaaa", "ad-token-bbbb", "vw-token-cccc"


class GuardBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.audit_path = Path(self._tmp.name) / "audit.jsonl"
        self.guard = PolicyGuard(
            PolicyEngine.from_files(POLICY_DIR),
            TokenAuthenticator({"operator": hash_token(OP), "admin": hash_token(AD),
                                "viewer": hash_token(VW)}),
            AuditLog(self.audit_path))


class TestGuard(GuardBase):
    def test_allowed_flow_returns_validated_values_and_logs(self):
        res = self.guard.check(OP, "limit_bandwidth",
                               {"client": "Guest-Phone", "download_kbit": 5000,
                                "upload_kbit": 2000}, "q1")
        self.assertTrue(res.allowed)
        self.assertEqual((res.role, res.tool, res.device.mac),
                         ("operator", "limit_bandwidth", "aa:bb:cc:dd:ee:04"))
        self.assertEqual(res.params, {"download_kbit": 5000, "upload_kbit": 2000})
        self.guard.record_outcome(res, "success", "Limited guest-phone.", True)
        recs = self.guard.audit.find("q1")
        self.assertEqual([r["event"] for r in recs], ["decision", "outcome"])
        self.assertEqual(recs[0]["principal"].split(":")[0], "operator")

    def test_role_comes_from_token_not_from_arguments(self):
        res = self.guard.check(OP, "kick_client",
                               {"client": "lab-laptop-3", "role": "admin", "token": AD}, "q2")
        self.assertFalse(res.allowed)
        self.assertEqual(res.role, "operator")
        self.assertNotIn(AD, self.audit_path.read_text())

    def test_bad_token_is_denied_and_logged(self):
        for token in (None, "", "guess", AD + "x"):
            res = self.guard.check(token, "get_status", {}, f"t-{token}")
            self.assertFalse(res.allowed)
            self.assertEqual(res.decision.rule, "R-token")
        self.assertIn("unauthenticated", self.guard.audit.explain("t-guess"))

    def test_viewer_token(self):
        self.assertTrue(self.guard.check(VW, "get_status", {}).allowed)
        res = self.guard.check(VW, "kick_client", {"client": "guest-phone"})
        self.assertEqual(res.decision.rule, "R-role-action")
        self.assertEqual(self.guard.tools_for_token(VW), ["get_status"])
        self.assertEqual(self.guard.tools_for_token("nope"), [])
        self.assertEqual(len(self.guard.tools_for_token(OP)), 8)

    def test_denied_request_carries_no_parameters(self):
        res = self.guard.check(OP, "limit_bandwidth",
                               {"client": "admin-laptop", "download_kbit": 5000,
                                "upload_kbit": 5000})
        self.assertFalse(res.allowed)
        self.assertEqual(res.params, {})

    def test_context_is_passed_to_engine(self):
        args = {"client": "guest-phone"}
        self.assertFalse(self.guard.check(OP, "unban_client", args).allowed)
        self.assertTrue(self.guard.check(OP, "unban_client", args,
                                         context={"banned_by_role": "operator"}).allowed)

    def test_operator_rule_change_denied_with_role_reason(self):
        res = self.guard.check(OP, "update_policy", {})
        self.assertEqual(res.decision.rule, "R-role-action")
        self.assertTrue(self.guard.check(AD, "update_policy", {}).allowed)

    def test_request_id_generated_when_missing(self):
        res = self.guard.check(OP, "get_status", {})
        self.assertTrue(res.request_id)
        self.assertEqual(len(self.guard.audit.find(res.request_id)), 1)

    def test_every_check_is_audited_including_denials(self):
        self.guard.check(OP, "kick_client", {"client": "admin-laptop"}, "a")
        self.guard.check("bad", "kick_client", {"client": "guest-phone"}, "b")
        self.guard.check(AD, "block_client", {"client": "lab-laptop-3", "target": "internet"}, "c")
        recs = {r["request_id"]: r for r in self.guard.audit.read_all()}
        self.assertEqual({k: r["decision"] for k, r in recs.items()},
                         {"a": "DENY", "b": "DENY", "c": "ALLOW"})
        self.assertEqual(recs["c"]["target"], "internet")
        self.assertEqual(self.guard.audit.verify(), (True, None))

    def test_ban_preview_outcome(self):
        res = self.guard.check(OP, "ban_client", {"client": "guest-phone",
                                                  "duration_minutes": 15}, "p1")
        self.assertTrue(res.allowed)
        self.assertEqual(res.params["confirm"], False)
        self.guard.record_outcome(res, "preview", "would ban for 15 minutes")
        self.assertEqual(self.guard.audit.find("p1")[1]["status"], "preview")

    def test_fails_closed_when_engine_crashes(self):
        class Broken:
            def evaluate(self, *a, **k):
                raise RuntimeError("boom")
        self.guard.engine = Broken()
        res = self.guard.check(OP, "kick_client", {"client": "guest-phone"}, "x")
        self.assertFalse(res.allowed)
        self.assertEqual(res.decision.rule, "R-internal-error")

    def test_fails_closed_when_audit_unavailable(self):
        class Broken:
            def log_decision(self, *a, **k):
                raise AuditError("disk full")
        self.guard.audit = Broken()
        res = self.guard.check(OP, "kick_client", {"client": "guest-phone"}, "x")
        self.assertFalse(res.allowed)
        self.assertEqual(res.decision.rule, "R-audit-unavailable")


class TestServiceAdapter(unittest.TestCase):
    """policy.integration.evaluate_policy mirrors evaluate_policy_placeholder."""

    def test_placeholder_compatible_call_shape(self):
        ok, reason = integration.evaluate_policy("block_client", "192.168.50.20", "internet",
                                                 "operator")
        self.assertTrue(ok, reason)
        ok, reason = integration.evaluate_policy("block_client", "192.168.50.1", "all", "admin")
        self.assertFalse(ok)
        self.assertIn("protected", reason)
        ok, _ = integration.evaluate_policy("block_client", "192.168.50.20", "192.168.50.1",
                                            "admin")
        self.assertFalse(ok)

    def test_service_role_rules(self):
        ev = integration.evaluate_policy
        self.assertFalse(ev("kick_client", "192.168.50.20", role="viewer")[0])
        self.assertFalse(ev("ban_client", "192.168.50.20", role="operator")[0])
        self.assertFalse(ev("ban_client", "192.168.50.20", role="operator",
                            duration_minutes=90)[0])
        self.assertTrue(ev("ban_client", "192.168.50.20", role="operator",
                           duration_minutes=30)[0])
        self.assertTrue(ev("ban_client", "192.168.50.20", role="admin", duration_minutes=None)[0])
        self.assertFalse(ev("unban_client", "192.168.50.20", role="operator",
                            banned_by_role="admin")[0])
        self.assertTrue(ev("unban_client", "192.168.50.20", role="operator",
                           banned_by_role="operator")[0])
        self.assertTrue(ev("get_status", role="viewer")[0])

    def test_resolved_identity_is_cross_checked(self):
        d = integration.evaluate_policy_decision(
            "kick_client", "192.168.50.60", role="admin", client="laptop-x",
            client_mac="aa:bb:cc:dd:ee:01")
        self.assertEqual(d.rule, "R-protected")
        d = integration.evaluate_policy_decision(
            "limit_bandwidth", "192.168.50.60", role="operator", download_kbit=5000,
            upload_kbit=5000, client_mac="aa:bb:cc:dd:ee:03")
        self.assertEqual(d.rule, "R-role-class")

    def test_get_tools_for_role(self):
        self.assertEqual(integration.get_tools_for_role("Viewer"), ["get_status"])
        self.assertEqual(len(integration.get_tools_for_role("admin")), 8)
        self.assertEqual(integration.get_tools_for_role("nobody"), [])

    def test_fails_closed_on_bad_config(self):
        from unittest import mock
        from policy.config import PolicyConfigError
        with mock.patch.object(integration, "get_engine", side_effect=PolicyConfigError("x")):
            ok, reason = integration.evaluate_policy("get_status", role="admin")
        self.assertFalse(ok)
        self.assertIn("invalid", reason)


if __name__ == "__main__":
    unittest.main()
