"""Unit tests for network/verification.py.

All system commands (ping, nft, tc, iperf3) are mocked through the injectable
CommandRunner, so these run with no root and no Pi. They check that each
verifier reads real measurements correctly and, importantly, that verification
can FAIL when the network does not match the request.
"""

from __future__ import annotations

import json
import unittest
from typing import List, Tuple

from network.verification import (
    verify_block,
    verify_limit,
    verify_unblock,
)


def make_runner(responses):
    """Build a CommandRunner that returns a canned (code, out, err) per command.

    ``responses`` maps the first argument (e.g. 'ping', 'nft', 'tc', 'iperf3')
    to a (code, stdout, stderr) tuple.
    """
    def runner(args: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        return responses.get(args[0], (0, "", ""))
    return runner


class TestVerifyBlock(unittest.TestCase):
    def test_block_confirmed_when_ping_fails(self) -> None:
        runner = make_runner({
            "ping": (1, "3 packets transmitted, 0 received, 100% packet loss", ""),
            "nft": (0, "ip filter { ip daddr 192.168.50.44 drop }", ""),
        })
        result = verify_block("192.168.50.44", runner=runner)
        self.assertTrue(result.passed)

    def test_block_fails_when_still_reachable(self) -> None:
        runner = make_runner({
            "ping": (0, "3 packets transmitted, 3 received, 0% packet loss", ""),
            "nft": (0, "", ""),
        })
        result = verify_block("192.168.50.44", runner=runner)
        self.assertFalse(result.passed)


class TestVerifyUnblock(unittest.TestCase):
    def test_unblock_confirmed_when_reachable_and_no_rule(self) -> None:
        runner = make_runner({
            "ping": (0, "3 packets transmitted, 3 received, 0% packet loss", ""),
            "nft": (0, "table ip ina_nat { }", ""),
        })
        result = verify_unblock("192.168.50.44", runner=runner)
        self.assertTrue(result.passed)

    def test_unblock_fails_if_drop_rule_remains(self) -> None:
        runner = make_runner({
            "ping": (0, "0% packet loss", ""),
            "nft": (0, "ip daddr 192.168.50.44 drop", ""),
        })
        result = verify_unblock("192.168.50.44", target_id="192.168.50.44",
                                runner=runner)
        self.assertFalse(result.passed)


class TestVerifyLimit(unittest.TestCase):
    def test_limit_confirmed_within_tolerance(self) -> None:
        iperf = json.dumps({"end": {"sum_received": {"bits_per_second": 4.8e6}}})
        runner = make_runner({
            "tc": (0, "qdisc tbf 1: root rate 5Mbit", ""),
            "iperf3": (0, iperf, ""),
        })
        result = verify_limit("192.168.50.44", 5.0, iperf_server_ip="192.168.50.1",
                              runner=runner)
        self.assertTrue(result.passed)
        self.assertIn("Mbit/s", result.measured)

    def test_limit_fails_when_throughput_too_high(self) -> None:
        iperf = json.dumps({"end": {"sum_received": {"bits_per_second": 20e6}}})
        runner = make_runner({
            "tc": (0, "qdisc tbf 1: root rate 5Mbit", ""),
            "iperf3": (0, iperf, ""),
        })
        result = verify_limit("192.168.50.44", 5.0, iperf_server_ip="192.168.50.1",
                              runner=runner)
        self.assertFalse(result.passed)

    def test_limit_unknown_when_nothing_queryable(self) -> None:
        runner = make_runner({
            "tc": (127, "", "not found"),
        })
        result = verify_limit("192.168.50.44", 5.0, runner=runner)
        self.assertFalse(result.passed)
        self.assertIsNotNone(result.error)


if __name__ == "__main__":
    unittest.main()
