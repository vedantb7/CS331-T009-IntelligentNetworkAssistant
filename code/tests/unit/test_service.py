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

    def test_status_live_clients_from_leases_stations_and_neighbors(self) -> None:
        """Without an injected map, status reads the lease file, hostapd and the neighbor table."""
        leases_file = os.path.join(self.test_dir, "dnsmasq.leases")
        with open(leases_file, "w", encoding="utf-8") as f:
            f.write("1793669815 02:00:5e:00:00:70 192.168.50.70 OnePlus-Nord-CE4 01:02\n"
                    "1793669505 02:00:5e:00:00:19 192.168.50.19 dhruv-phone 01:02\n"
                    "1793660000 02:11:22:33:44:55 192.168.50.40 old-tablet 01:02\n")
        neigh = json.dumps([
            {"dst": "192.168.50.70", "dev": "ap0", "lladdr": "02:00:5e:00:00:70", "state": ["REACHABLE"]},
            {"dst": "192.168.50.19", "dev": "ap0", "lladdr": "02:00:5e:00:00:19", "state": ["STALE"]},
        ])

        def runner(args, timeout=5.0):
            if args[:3] == ["hostapd_cli", "-i", "ap0"] and args[3:] == ["list_sta"]:
                return 0, "02:00:5e:00:00:70\n02:00:5e:00:00:19\naa:00:00:00:00:09\n", ""
            if args[:3] == ["ip", "-j", "neigh"]:
                return 0, neigh, ""
            return 1, "", "unexpected"

        service = NetworkService(runner=runner, lock_path=self.lock_path, state_path=self.state_path,
                                 audit_path=self.audit_path, leases_file=leases_file)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump({"blocks": [{"client": "dhruv-phone", "target": "internet"}],
                       "limits": [{"client": "192.168.50.70", "download_kbit": 2000,
                                   "upload_kbit": 2000},
                                  {"client": "old-tablet", "client_ip": "192.168.50.40",
                                   "download_kbit": 1000, "upload_kbit": 1000}]}, f)
        clients = {c.client: c for c in service.status(role="viewer").clients}

        # A left device is listed only while it still has a restriction (here a limit),
        # as not associated; the lease-less station is listed by MAC.
        self.assertEqual(set(clients), {"oneplus-nord-ce4", "dhruv-phone", "aa:00:00:00:00:09",
                                        "old-tablet"})
        self.assertFalse(clients["old-tablet"].associated)
        self.assertEqual(clients["old-tablet"].download_kbit, 1000)
        self.assertTrue(all(c.associated for n, c in clients.items() if n != "old-tablet"))
        self.assertEqual(clients["dhruv-phone"].ip, "192.168.50.19")
        self.assertEqual(clients["dhruv-phone"].neighbor_state, "STALE")
        self.assertEqual(clients["dhruv-phone"].blocked_targets, ["internet"])
        self.assertEqual(clients["oneplus-nord-ce4"].download_kbit, 2000)
        self.assertEqual(service.status(client="dhruv-phone", role="viewer").clients[0].mac,
                         "02:00:5e:00:00:19")


if __name__ == "__main__":
    unittest.main()

