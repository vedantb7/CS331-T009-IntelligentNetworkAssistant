"""Unit tests for MCP schemas and data models in INA v2.

Tests typed input arguments, bounds checking, regex patterns, and structured result models.
"""

import unittest
from typing import Any, Dict

from inav2.mcp_server.schemas import (
    PYDANTIC_AVAILABLE,
    BlockClientArgs,
    BlockResult,
    ClientStatus,
    GetStatusArgs,
    LimitBandwidthArgs,
    LimitResult,
    RemoveLimitArgs,
    RemoveLimitResult,
    StatusResult,
    UnblockClientArgs,
    UnblockResult,
)


class TestMCPSchemas(unittest.TestCase):
    """Test suite for MCP tool input schemas and result data models."""

    def test_block_client_args_valid(self) -> None:
        """Valid client and target combinations must pass validation."""
        valid_cases = [
            ("laptop-a", "phone-b"),
            ("192.168.50.20", "192.168.50.21"),
            ("laptop-a", "internet"),
            ("laptop-a", "gateway"),
            ("laptop-a", "all"),
            ("device-1", "all"),
        ]
        for client, target in valid_cases:
            args = BlockClientArgs(client=client, target=target)
            self.assertEqual(args.client, client)
            self.assertEqual(args.target, target)

    def test_block_client_args_invalid(self) -> None:
        """Invalid characters, shell injection, or illegal scopes must fail."""
        invalid_cases = [
            ("laptop-a; rm -rf /", "phone-b"),
            ("laptop-a", "something_else"),
            ("", "internet"),
            ("laptop-a", ""),
            ("10.0.0.1", "internet"),  # Not 192.168.50.X
        ]
        for client, target in invalid_cases:
            with self.assertRaises((ValueError, Exception)):
                BlockClientArgs(client=client, target=target)

    def test_limit_bandwidth_args_valid_bounds(self) -> None:
        """Valid bandwidth limits within [256, 20000] kbit/s must pass."""
        args = LimitBandwidthArgs(client="laptop-a", download_kbit=5000, upload_kbit=2000)
        self.assertEqual(args.client, "laptop-a")
        self.assertEqual(args.download_kbit, 5000)
        self.assertEqual(args.upload_kbit, 2000)

        # Boundary checks
        args_min = LimitBandwidthArgs(client="laptop-a", download_kbit=256, upload_kbit=256)
        self.assertEqual(args_min.download_kbit, 256)

        args_max = LimitBandwidthArgs(client="laptop-a", download_kbit=20000, upload_kbit=20000)
        self.assertEqual(args_max.download_kbit, 20000)

    def test_limit_bandwidth_args_out_of_bounds(self) -> None:
        """Rates below 256 or above 20000 kbit/s must fail validation."""
        out_of_bounds = [
            ("laptop-a", 100, 2000),      # download too low
            ("laptop-a", 2000, 50),       # upload too low
            ("laptop-a", 50000, 2000),    # download too high
            ("laptop-a", 5000, 25000),    # upload too high
        ]
        for client, down, up in out_of_bounds:
            with self.assertRaises((ValueError, Exception)):
                LimitBandwidthArgs(client=client, download_kbit=down, upload_kbit=up)

    def test_unblock_client_args(self) -> None:
        """Unblock arguments validate client and target."""
        args = UnblockClientArgs(client="laptop-a", target="internet")
        self.assertEqual(args.client, "laptop-a")
        self.assertEqual(args.target, "internet")

        with self.assertRaises((ValueError, Exception)):
            UnblockClientArgs(client="bad$client", target="internet")

    def test_remove_limit_args(self) -> None:
        """Remove limit arguments validate client name/IP."""
        args = RemoveLimitArgs(client="laptop-a")
        self.assertEqual(args.client, "laptop-a")

        with self.assertRaises((ValueError, Exception)):
            RemoveLimitArgs(client="bad$name")

    def test_get_status_args(self) -> None:
        """Get status accepts None or a valid client."""
        args_empty = GetStatusArgs()
        self.assertIsNone(args_empty.client)

        args_filtered = GetStatusArgs(client="laptop-a")
        self.assertEqual(args_filtered.client, "laptop-a")

        with self.assertRaises((ValueError, Exception)):
            GetStatusArgs(client="invalid/client")

    def test_result_models(self) -> None:
        """Output models serialize properly and contain required fields."""
        block_res = BlockResult(
            client="laptop-a",
            client_ip="192.168.50.20",
            target="phone-b",
            blocked=True,
            changed=True,
            verified=True,
            message="Success",
        )
        self.assertTrue(block_res.blocked)
        self.assertTrue(block_res.verified)
        dump = block_res.model_dump()
        self.assertEqual(dump["client_ip"], "192.168.50.20")

        unblock_res = UnblockResult(
            client="laptop-a",
            client_ip="192.168.50.20",
            target="phone-b",
            unblocked=True,
            changed=True,
            verified=True,
        )
        self.assertTrue(unblock_res.unblocked)

        limit_res = LimitResult(
            client="laptop-a",
            client_ip="192.168.50.20",
            download_kbit=5000,
            upload_kbit=2000,
            changed=True,
            verified=True,
        )
        self.assertEqual(limit_res.download_kbit, 5000)

        remove_res = RemoveLimitResult(
            client="laptop-a",
            client_ip="192.168.50.20",
            changed=True,
            verified=True,
        )
        self.assertTrue(remove_res.changed)

        client_status = ClientStatus(
            client="laptop-a",
            ip="192.168.50.20",
            mac="aa:bb:cc:dd:ee:01",
            associated=True,
            neighbor_state="REACHABLE",
            blocked_targets=["phone-b"],
            download_kbit=5000,
            upload_kbit=2000,
        )
        status_res = StatusResult(
            clients=[client_status],
            drift_detected=False,
            message="All services synchronized",
        )
        self.assertEqual(len(status_res.clients), 1)
        self.assertEqual(status_res.clients[0].client, "laptop-a")


if __name__ == "__main__":
    unittest.main()

