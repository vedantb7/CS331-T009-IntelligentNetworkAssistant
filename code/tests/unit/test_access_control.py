"""Unit tests for access control and nftables packet filtering in INA v2."""

import unittest
from typing import Dict, List, Tuple

from inav2.network.access_control import (
    block_client,
    unblock_client,
    validate_client_identifier,
    validate_target_identifier,
)


class MockCommandRunner:
    """Mock command runner for testing nftables operations without root or kernel access."""

    def __init__(self) -> None:
        self.commands: List[List[str]] = []
        # In-memory mock sets
        self.sets: Dict[str, set] = {
            "blocked_pairs": set(),
            "blocked_internet": set(),
            "blocked_gateway": set(),
            "isolated": set(),
        }
        self.fail_next_apply: bool = False
        self.fail_verification: bool = False

    def __call__(self, args: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        self.commands.append(list(args))
        cmd = args[0]

        if cmd == "nft":
            sub = args[1]
            if sub == "list" and args[2] == "table":
                return 0, "table inet ina_filter { ... }", ""

            if sub == "list" and args[2] == "set":
                # nft list set inet ina_filter <set_name>
                set_name = args[5]
                elements = self.sets.get(set_name, set())
                out_lines = [f"set {set_name} {{"]
                for elem in elements:
                    out_lines.append(f"\t\telements = {{ {elem} }}")
                out_lines.append("}")
                return 0, "\n".join(out_lines), ""

            if sub == "add" and args[2] == "element":
                if self.fail_next_apply:
                    return 1, "", "Error: could not add element"
                set_name = args[5]
                elem_spec = args[6].strip("{} ")
                if not self.fail_verification:
                    for part in elem_spec.split(","):
                        self.sets[set_name].add(part.strip())
                return 0, "", ""

            if sub in ("destroy", "delete") and args[2] == "element":
                set_name = args[5]
                elem_spec = args[6].strip("{} ")
                for part in elem_spec.split(","):
                    self.sets[set_name].discard(part.strip())
                return 0, "", ""

            return 0, "", ""

        if cmd == "ip":
            if args[1] == "neigh":
                ip = args[5] if len(args) > 5 else "192.168.50.20"
                return 0, f"{ip} dev ap0 lladdr 02:00:00:00:00:02 REACHABLE", ""
            return 0, "", ""

        return 0, "", ""


class TestAccessControl(unittest.TestCase):
    """Test suite for client access control, blocking, and unblocking."""

    def setUp(self) -> None:
        self.runner = MockCommandRunner()
        self.leases_map = {
            "laptop-a": "192.168.50.20",
            "phone-b": "192.168.50.21",
            "tablet-c": "192.168.50.22",
        }

    def test_validate_client_identifier(self) -> None:
        """Resolution of valid hostnames and direct IP addresses."""
        self.assertEqual(
            validate_client_identifier("laptop-a", leases_map=self.leases_map),
            "192.168.50.20",
        )
        self.assertEqual(
            validate_client_identifier("192.168.50.25", leases_map=self.leases_map),
            "192.168.50.25",
        )

        # Unknown hostname
        with self.assertRaises(ValueError) as ctx:
            validate_client_identifier("unknown-pc", leases_map=self.leases_map)
        self.assertIn("Unknown client", str(ctx.exception))

        # Outside subnet range
        with self.assertRaises(ValueError):
            validate_client_identifier("10.0.0.1", leases_map=self.leases_map)

        # Illegal format
        with self.assertRaises(ValueError):
            validate_client_identifier("bad$name", leases_map=self.leases_map)

    def test_validate_target_identifier(self) -> None:
        """Target validation for scope keywords and peer clients."""
        t_type, t_val = validate_target_identifier("internet", "192.168.50.20")
        self.assertEqual(t_type, "internet")
        self.assertEqual(t_val, "internet")

        t_type, t_val = validate_target_identifier("gateway", "192.168.50.20")
        self.assertEqual(t_type, "gateway")

        t_type, t_val = validate_target_identifier("all", "192.168.50.20")
        self.assertEqual(t_type, "all")

        # Peer client target
        t_type, t_val = validate_target_identifier("phone-b", "192.168.50.20", leases_map=self.leases_map)
        self.assertEqual(t_type, "client")
        self.assertEqual(t_val, "192.168.50.21")

        # Self-target rejection
        with self.assertRaises(ValueError) as ctx:
            validate_target_identifier("laptop-a", "192.168.50.20", leases_map=self.leases_map)
        self.assertIn("client and target must differ", str(ctx.exception))

    def test_block_client_pairwise(self) -> None:
        """Pairwise block inserts bidirectional pairs into blocked_pairs."""
        res = block_client("laptop-a", "phone-b", runner=self.runner, leases_map=self.leases_map)
        self.assertTrue(res.blocked)
        self.assertTrue(res.changed)
        self.assertTrue(res.verified)

        # Check in-memory mock set
        pairs = self.runner.sets["blocked_pairs"]
        self.assertIn("192.168.50.20 . 192.168.50.21", pairs)
        self.assertIn("192.168.50.21 . 192.168.50.20", pairs)

        # Repeating the call returns changed=False (idempotency)
        res_repeat = block_client("laptop-a", "phone-b", runner=self.runner, leases_map=self.leases_map)
        self.assertTrue(res_repeat.blocked)
        self.assertFalse(res_repeat.changed)
        self.assertTrue(res_repeat.verified)

    def test_block_client_scopes(self) -> None:
        """Scope blocking for internet, gateway, and all."""
        # Internet
        res_net = block_client("laptop-a", "internet", runner=self.runner, leases_map=self.leases_map)
        self.assertTrue(res_net.blocked)
        self.assertIn("192.168.50.20", self.runner.sets["blocked_internet"])

        # Gateway
        res_gw = block_client("laptop-a", "gateway", runner=self.runner, leases_map=self.leases_map)
        self.assertTrue(res_gw.blocked)
        self.assertIn("192.168.50.20", self.runner.sets["blocked_gateway"])

        # All
        res_all = block_client("laptop-a", "all", runner=self.runner, leases_map=self.leases_map)
        self.assertTrue(res_all.blocked)
        self.assertIn("192.168.50.20", self.runner.sets["isolated"])

    def test_block_client_policy_protected(self) -> None:
        """Protected router infrastructure cannot be blocked."""
        with self.assertRaises(PermissionError):
            block_client("192.168.50.1", "internet", runner=self.runner, leases_map=self.leases_map)

    def test_block_client_failure_and_rollback(self) -> None:
        """Failure during command execution or kernel verification triggers rollback."""
        # Command execution failure
        self.runner.fail_next_apply = True
        with self.assertRaises(RuntimeError) as ctx:
            block_client("laptop-a", "internet", runner=self.runner, leases_map=self.leases_map)
        self.assertIn("Failed to apply nftables block rule", str(ctx.exception))

        # Verification failure triggers rollback
        self.runner.fail_next_apply = False
        self.runner.fail_verification = True
        with self.assertRaises(RuntimeError) as ctx:
            block_client("laptop-a", "gateway", runner=self.runner, leases_map=self.leases_map)
        self.assertIn("Verification failed", str(ctx.exception))
        self.assertIn("rolled back", str(ctx.exception))

    def test_unblock_client(self) -> None:
        """Unblocking removes rule element and is idempotent."""
        # First block
        block_client("laptop-a", "internet", runner=self.runner, leases_map=self.leases_map)
        self.assertIn("192.168.50.20", self.runner.sets["blocked_internet"])

        # Unblock
        unblock_res = unblock_client("laptop-a", "internet", runner=self.runner, leases_map=self.leases_map)
        self.assertTrue(unblock_res.unblocked)
        self.assertTrue(unblock_res.changed)
        self.assertNotIn("192.168.50.20", self.runner.sets["blocked_internet"])

        # Unblock again (idempotent no-op)
        repeat_res = unblock_client("laptop-a", "internet", runner=self.runner, leases_map=self.leases_map)
        self.assertTrue(repeat_res.unblocked)
        self.assertFalse(repeat_res.changed)


if __name__ == "__main__":
    unittest.main()

