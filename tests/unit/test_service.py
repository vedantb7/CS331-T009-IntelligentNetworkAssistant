"""Unit tests for the authoritative NetworkService execution pipeline in INA v2."""

import json
import os
import shutil
import tempfile
import unittest

from inav2.network.audit import verify_audit_log_integrity
from inav2.network.service import (
    ClientNotFoundError,
    NetworkService,
    PolicyDeniedError,
)
from inav2.tests.unit.test_access_control import MockCommandRunner


class TestNetworkService(unittest.TestCase):
    """Test suite for NetworkService pipeline, concurrency, state, and audit."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()
        self.lock_path = os.path.join(self.test_dir, "test.lock")
        self.state_path = os.path.join(self.test_dir, "state.json")
        self.audit_path = os.path.join(self.test_dir, "audit.log")

        self.runner = MockCommandRunner()
        self.leases_map = {
            "laptop-a": "192.168.50.20",
            "phone-b": "192.168.50.21",
        }

        self.service = NetworkService(
            runner=self.runner,
            lock_path=self.lock_path,
            state_path=self.state_path,
            audit_path=self.audit_path,
            leases_map=self.leases_map,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_block_and_unblock_pipeline(self) -> None:
        """Block and unblock operations update state and write audit chain."""
        # 1. Block client
        res = self.service.block(client="laptop-a", target="phone-b", role="operator")
        self.assertTrue(res.blocked)
        self.assertTrue(res.changed)
        self.assertEqual(res.client_ip, "192.168.50.20")

        # Verify state.json was updated
        with open(self.state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
            self.assertEqual(len(state.get("blocks", [])), 1)
            self.assertEqual(state["blocks"][0]["client"], "laptop-a")

        # 2. Unblock client
        unblock_res = self.service.unblock(client="laptop-a", target="phone-b", role="operator")
        self.assertTrue(unblock_res.unblocked)
        self.assertTrue(unblock_res.changed)

        with open(self.state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
            self.assertEqual(len(state.get("blocks", [])), 0)

        # 3. Verify audit log integrity
        valid, msg = verify_audit_log_integrity(self.audit_path)
        self.assertTrue(valid, msg)

    def test_protected_infrastructure_denial(self) -> None:
        """Targeting protected router infrastructure (192.168.50.1) is denied."""
        with self.assertRaises(PolicyDeniedError):
            self.service.block(client="192.168.50.1", target="internet", role="operator")

        # Verify denial is recorded in audit log
        with open(self.audit_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["status"], "denied")

        valid, msg = verify_audit_log_integrity(self.audit_path)
        self.assertTrue(valid, msg)

    def test_client_not_found(self) -> None:
        """Unknown client identifier raises ClientNotFoundError."""
        with self.assertRaises(ClientNotFoundError):
            self.service.block(client="unknown-device", target="internet", role="operator")

    def test_status_query(self) -> None:
        """Status queries return structured client list with active blocks."""
        self.service.block(client="laptop-a", target="phone-b", role="operator")
        status_res = self.service.status(role="viewer")
        self.assertFalse(status_res.drift_detected)
        self.assertEqual(len(status_res.clients), 2)

        client_a = next(c for c in status_res.clients if c.client == "laptop-a")
        self.assertIn("phone-b", client_a.blocked_targets)


if __name__ == "__main__":
    unittest.main()

