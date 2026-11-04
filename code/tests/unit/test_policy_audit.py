"""Unit tests for the audit log (policy/audit.py). Plan test 11."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from policy.audit import AuditError, AuditLog
from policy.models import allow, deny


class TestAuditLog(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "logs" / "audit.jsonl"
        self.log = AuditLog(self.path)

    def test_decision_and_outcome_are_recorded(self):
        self.log.log_decision("r1", "operator:ab12cd34", "block_device",
                              {"device": "guest-phone"}, allow("R-block", "ok"), "guest-phone")
        self.log.log_outcome("r1", "success", "Blocked guest-phone.", True)
        recs = self.log.find("r1")
        self.assertEqual([r["event"] for r in recs], ["decision", "outcome"])
        d, o = recs
        self.assertEqual(d["principal"], "operator:ab12cd34")
        self.assertEqual((d["tool"], d["decision"], d["rule"]), ("block_device", "ALLOW", "R-block"))
        self.assertEqual((o["status"], o["verification_passed"]), ("success", True))
        self.assertIn("ts", d)

    def test_denial_can_be_explained(self):
        self.log.log_decision("r2", "operator:ab12cd34", "block_device",
                              {"device": "admin-laptop"},
                              deny("R-protected", "'admin-laptop' is a protected device."), "admin-laptop")
        self.log.log_outcome("r2", "denied", "nothing changed")
        text = self.log.explain("r2")
        self.assertIn("DENY", text)
        self.assertIn("R-protected", text)
        self.assertIn("operator:ab12cd34", text)
        self.assertIn("No record", self.log.explain("missing"))

    def test_unauthenticated_requests_are_logged_too(self):
        self.log.log_decision("r3", None, "block_device", {"device": "x"}, deny("R-token", "bad token"))
        self.assertIn("unauthenticated", self.log.explain("r3"))

    def test_hostile_text_cannot_forge_log_lines(self):
        evil = "guest-phone\n{\"event\":\"decision\",\"decision\":\"ALLOW\"}"
        self.log.log_decision("r4", None, "block_device", {"device": evil, "mbit": evil},
                              deny("R-bad-device", "bad"))
        raw = self.path.read_text().splitlines()
        self.assertEqual(len(raw), 1)
        self.assertTrue(all(json.loads(line) for line in raw))
        self.assertNotIn("\n", json.loads(raw[0])["arguments"]["device"])

    def test_no_token_material_is_logged(self):
        self.log.log_decision("r5", "admin:deadbeef", "list_devices",
                              {"device": "x", "token": "SECRET", "role": "admin"}, allow("R-list", "ok"))
        self.assertNotIn("SECRET", self.path.read_text())

    def test_chain_verifies_and_survives_restart(self):
        self.log.log_decision("a", None, "list_devices", {}, allow("R-list", "ok"))
        reopened = AuditLog(self.path)
        reopened.log_outcome("a", "success")
        self.assertEqual(reopened.verify(), (True, None))
        self.assertEqual([r["seq"] for r in reopened.read_all()], [1, 2])

    def test_tampering_is_detected(self):
        for i in range(3):
            self.log.log_decision(f"r{i}", None, "block_device", {"device": "guest-phone"},
                                  deny("R-token", "bad token"))
        lines = self.path.read_text().splitlines()
        lines[1] = lines[1].replace("DENY", "ALLOW")
        self.path.write_text("\n".join(lines) + "\n")
        ok, bad = AuditLog(self.path).verify()
        self.assertFalse(ok)
        self.assertEqual(bad, 2)

    def test_deleted_middle_record_is_detected(self):
        for i in range(3):
            self.log.log_decision(f"r{i}", None, "list_devices", {}, allow("R-list", "ok"))
        lines = self.path.read_text().splitlines()
        self.path.write_text("\n".join([lines[0], lines[2]]) + "\n")
        self.assertFalse(AuditLog(self.path).verify()[0])

    def test_corrupt_existing_log_refuses_to_append(self):
        self.path.write_text("this is not json\n")
        with self.assertRaises(AuditError):
            AuditLog(self.path)

    def test_new_tool_arguments_are_recorded_safely(self):
        self.log.log_decision("n1", "operator:ab12cd34", "ban_client",
                              {"client": "guest-phone", "duration_minutes": 30,
                               "reason": "abuse", "confirm": True, "token": "SECRET"},
                              allow("R-ban", "ok"), "guest-phone")
        self.log.log_decision("n2", "admin:deadbeef", "block_client",
                              {"client": "guest-phone", "target": "internet"},
                              allow("R-block", "ok"), "guest-phone", "internet")
        d = self.log.find("n1")[0]
        self.assertEqual(d["arguments"], {"client": "guest-phone", "reason": "abuse",
                                          "duration_minutes": 30, "confirm": True,
                                          "unexpected_keys": ["token"]})
        self.assertNotIn("SECRET", self.path.read_text())
        self.assertIn("guest-phone -> internet", self.log.explain("n2"))

    def test_outcome_status_is_restricted(self):
        self.log.log_outcome("s1", "preview", "would ban")
        self.log.log_outcome("s2", "totally fine\nALLOW")
        self.assertEqual(self.log.find("s1")[0]["status"], "preview")
        self.assertEqual(self.log.find("s2")[0]["status"], "failure")

    def test_default_path_from_environment(self):
        import os
        from unittest import mock
        from policy import audit as audit_mod
        target = str(Path(self._tmp.name) / "env" / "a.jsonl")
        with mock.patch.dict(os.environ, {audit_mod.ENV_PATH: target}):
            self.assertEqual(str(AuditLog().path), target)

    def test_unwritable_log_raises(self):
        log = AuditLog(self.path)
        self.path.unlink(missing_ok=True)
        self.path.mkdir()  # a directory where the file should be
        with self.assertRaises(AuditError):
            log.log_outcome("x", "success")


if __name__ == "__main__":
    unittest.main()
