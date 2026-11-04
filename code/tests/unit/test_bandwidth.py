"""Unit tests for bandwidth shaping and Traffic Control (tc) in INA v2."""

import unittest
from typing import Dict, List, Set, Tuple

from inav2.network.bandwidth import (
    calculate_burst_bytes,
    derive_client_tc_ids,
    limit_bandwidth,
    remove_bandwidth_limit,
)


class MockTcRunner:
    """Mock command runner simulating Linux Traffic Control (tc) and iproute2."""

    def __init__(self) -> None:
        self.commands: List[List[str]] = []
        # In-memory representation of classes per device: dev -> set of (classid, rate)
        self.classes: Dict[str, Dict[str, str]] = {
            "ap0": {},
            "ifb-ap0": {},
        }
        self.filters: Dict[str, Set[str]] = {
            "ap0": set(),
            "ifb-ap0": set(),
        }
        self.fail_next_apply: bool = False
        self.fail_verification: bool = False

    def __call__(self, args: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        self.commands.append(list(args))
        cmd = args[0]

        if cmd == "tc":
            sub = args[1]
            if sub == "class":
                action = args[2]
                if action == "show":
                    dev = args[4]
                    classes_on_dev = self.classes.get(dev, {})
                    lines = []
                    for cid, rate in classes_on_dev.items():
                        lines.append(f"class htb {cid} root rate {rate} ceil {rate} burst 15000b cburst 15000b")
                    return 0, "\n".join(lines), ""

                if action == "replace":
                    if self.fail_next_apply:
                        return 1, "", "Error: tc class replace failed"
                    dev = args[4]
                    cid = args[8]
                    rate = args[11]
                    if not self.fail_verification:
                        self.classes[dev][cid] = rate
                    return 0, "", ""

                if action == "del":
                    dev = args[4]
                    cid = args[6]
                    self.classes[dev].pop(cid, None)
                    return 0, "", ""

            if sub == "filter":
                action = args[2]
                if action == "replace":
                    dev = args[4]
                    handle = args[9]
                    self.filters[dev].add(handle)
                    return 0, "", ""
                if action == "del":
                    dev = args[4]
                    handle = args[10]
                    self.filters[dev].discard(handle)
                    return 0, "", ""

            if sub == "qdisc":
                return 0, "", ""

        if cmd == "ip":
            return 0, "", ""

        return 0, "", ""


class TestBandwidthManagement(unittest.TestCase):
    """Test suite for bandwidth shaping, class ID calculation, and rate limits."""

    def setUp(self) -> None:
        self.runner = MockTcRunner()
        self.leases_map = {
            "laptop-a": "192.168.50.20",
            "phone-b": "192.168.50.21",
        }

    def test_classid_derivation(self) -> None:
        """Class IDs are derived deterministically: 0x100 + last octet."""
        # 192.168.50.20 -> last octet 20 (0x14) -> 0x114 -> classid "1:114"
        cid, handle, hex_h = derive_client_tc_ids("192.168.50.20")
        self.assertEqual(cid, "1:114")
        self.assertEqual(handle, "114:")
        self.assertEqual(hex_h, "0x114")

        # 192.168.50.21 -> last octet 21 (0x15) -> 0x115 -> classid "1:115"
        cid2, handle2, hex_h2 = derive_client_tc_ids("192.168.50.21")
        self.assertEqual(cid2, "1:115")
        self.assertEqual(handle2, "115:")
        self.assertEqual(hex_h2, "0x115")

    def test_calculate_burst_bytes(self) -> None:
        """Burst formula clamps to 15000 bytes floor or rate_bit / 800."""
        # 256 kbit -> 256 * 1000 / 800 = 320 -> clamped to 15000
        self.assertEqual(calculate_burst_bytes(256), 15000)

        # 5000 kbit -> 5000 * 1000 / 800 = 6250 -> clamped to 15000
        self.assertEqual(calculate_burst_bytes(5000), 15000)

        # 20000 kbit -> 20000 * 1000 / 800 = 25000 -> 25000
        self.assertEqual(calculate_burst_bytes(20000), 25000)

    def test_limit_bandwidth_success(self) -> None:
        """Applying bandwidth limit sets download on ap0 and upload on ifb-ap0."""
        res = limit_bandwidth(
            client="laptop-a",
            download_kbit=5000,
            upload_kbit=2000,
            runner=self.runner,
            leases_map=self.leases_map,
        )
        self.assertEqual(res.download_kbit, 5000)
        self.assertEqual(res.upload_kbit, 2000)
        self.assertTrue(res.changed)
        self.assertTrue(res.verified)

        # Check classes created in mock
        self.assertIn("1:114", self.runner.classes["ap0"])
        self.assertIn("1:114", self.runner.classes["ifb-ap0"])
        self.assertEqual(self.runner.classes["ap0"]["1:114"], "5000kbit")
        self.assertEqual(self.runner.classes["ifb-ap0"]["1:114"], "2000kbit")

        # Repeat call with identical rates returns changed=False (idempotency)
        res_repeat = limit_bandwidth(
            client="laptop-a",
            download_kbit=5000,
            upload_kbit=2000,
            runner=self.runner,
            leases_map=self.leases_map,
        )
        self.assertFalse(res_repeat.changed)
        self.assertTrue(res_repeat.verified)

    def test_limit_bandwidth_bounds(self) -> None:
        """Rates outside 256 to 20000 kbit/s are rejected."""
        with self.assertRaises(ValueError):
            limit_bandwidth("laptop-a", 100, 2000, runner=self.runner, leases_map=self.leases_map)

        with self.assertRaises(ValueError):
            limit_bandwidth("laptop-a", 5000, 30000, runner=self.runner, leases_map=self.leases_map)

    def test_limit_bandwidth_protected_router(self) -> None:
        """Router infrastructure (192.168.50.1) cannot be rate-limited."""
        with self.assertRaises(PermissionError):
            limit_bandwidth("192.168.50.1", 5000, 2000, runner=self.runner, leases_map=self.leases_map)

    def test_remove_bandwidth_limit(self) -> None:
        """Removing bandwidth limit deletes class from ap0 and ifb-ap0."""
        limit_bandwidth("laptop-a", 5000, 2000, runner=self.runner, leases_map=self.leases_map)
        self.assertIn("1:114", self.runner.classes["ap0"])

        del_res = remove_bandwidth_limit("laptop-a", runner=self.runner, leases_map=self.leases_map)
        self.assertTrue(del_res.changed)
        self.assertTrue(del_res.verified)
        self.assertNotIn("1:114", self.runner.classes["ap0"])
        self.assertNotIn("1:114", self.runner.classes["ifb-ap0"])

        # Repeat remove returns changed=False
        del_repeat = remove_bandwidth_limit("laptop-a", runner=self.runner, leases_map=self.leases_map)
        self.assertFalse(del_repeat.changed)


if __name__ == "__main__":
    unittest.main()
