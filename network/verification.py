"""Post-action verification for INA v2.

After the MCP server applies a change, it calls into this module to confirm,
by measurement, that the network is actually in the intended state. Verifying
is independent of applying: a command that exits cleanly does not prove the
network changed, so we re-check with the network itself.

What each check looks at:
- block   -> a ping from a sibling client to the target should FAIL (100% loss),
             and the nftables drop rule for the target should be present.
- unblock -> the ping should succeed again, and no drop rule should remain.
- limit   -> the tc qdisc/rate on the target's interface should be present, and
             (optionally) an iperf3 run should measure a rate within tolerance
             of the requested rate.

This module follows the same conventions as ``network/device_discovery.py``:
read-only inspection, a 3-tuple ``(exit_code, stdout, stderr)`` command runner
that can be injected for testing, and no external pip dependencies. It can be
run and unit-tested on any machine with the runner mocked; on the Pi it uses
real ``ping``, ``iperf3``, ``nft`` and ``tc``.

Owner: Khushi (verification). Called by Dhruv's MCP server pipeline.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from network.device_discovery import CommandRunner, default_command_runner

# Default tolerance for bandwidth verification: measured within +/- 20% of target
# (the same tolerance used in INA v1).
DEFAULT_TOLERANCE = 0.20

# Default interface the Pi applies client shaping on.
DEFAULT_AP_INTERFACE = "ap0"


# ==============================================================================
# Result model
# ==============================================================================


@dataclass
class Check:
    """One measurement and whether it met expectation."""

    name: str
    expected: str
    measured: str
    passed: bool


@dataclass
class VerificationResult:
    """The outcome Dhruv's pipeline reads. ``passed`` is a plain bool."""

    action: str
    target: str
    passed: bool
    checks: List[Check] = field(default_factory=list)
    expected: Optional[str] = None
    measured: Optional[str] = None
    duration_s: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "target": self.target,
            "passed": self.passed,
            "checks": [asdict(c) for c in self.checks],
            "expected": self.expected,
            "measured": self.measured,
            "duration_s": round(self.duration_s, 3),
            "error": self.error,
        }


# ==============================================================================
# Low-level measurements
# ==============================================================================


def _ping_loss_percent(runner: CommandRunner, target_ip: str, *,
                       source_interface: Optional[str] = None,
                       count: int = 3, timeout: float = 8.0) -> Optional[float]:
    """Return packet-loss percentage from a ping, or None if it couldn't run.

    On the Pi, verifying a block should ping the target from a *sibling* client
    so the traffic crosses the bridge where the rule is enforced. When a source
    interface is given we bind to it with ``-I``.
    """
    args = ["ping", "-c", str(count), "-w", str(int(timeout))]
    if source_interface:
        args += ["-I", source_interface]
    args.append(target_ip)
    code, out, _err = runner(args, timeout + 2)
    if code in (127, 126, 124):
        return None
    m = re.search(r"(\d+(?:\.\d+)?)%\s*packet loss", out)
    if not m:
        # Some ping builds print "100% packet loss" only on total failure; if we
        # saw no transmitted/received line at all, treat as unknown.
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _nft_has_drop_rule(runner: CommandRunner, target: str) -> Optional[bool]:
    """True if the nftables ruleset mentions a drop/quarantine for the target.

    ``target`` may be a MAC or an IP. Returns None if nft can't be queried.
    """
    code, out, _err = runner(["nft", "list", "ruleset"], 5.0)
    if code in (127, 126, 124):
        return None
    if code != 0 and not out:
        return None
    text = out.lower()
    needle = target.lower()
    # A drop rule for the target: the target appears on a line that drops.
    for line in text.splitlines():
        if needle in line and ("drop" in line or "reject" in line):
            return True
    return False


def _tc_rate_mbit(runner: CommandRunner, interface: str) -> Optional[float]:
    """Return the configured tc rate ceiling in Mbit/s on an interface, if any."""
    code, out, _err = runner(["tc", "qdisc", "show", "dev", interface], 5.0)
    if code in (127, 126, 124):
        return None
    # Also look at classes for htb/tbf rates.
    code2, out2, _err2 = runner(["tc", "class", "show", "dev", interface], 5.0)
    blob = (out or "") + "\n" + (out2 or "")
    m = re.search(r"rate\s+(\d+(?:\.\d+)?)([kKmMgG]?)bit", blob)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2).lower()
    factor = {"": 1e-6, "k": 1e-3, "m": 1.0, "g": 1e3}.get(unit, 1.0)
    return value * factor


def _iperf3_mbit(runner: CommandRunner, server_ip: str, *,
                 duration: int = 5, reverse: bool = False,
                 timeout: float = 20.0) -> Optional[float]:
    """Measure throughput to an iperf3 server in Mbit/s, or None if unavailable.

    ``reverse=True`` measures download (server -> client), which is the direction
    a download limit on the client affects.
    """
    args = ["iperf3", "-c", server_ip, "-t", str(duration), "-J"]
    if reverse:
        args.append("-R")
    code, out, _err = runner(args, timeout)
    if code in (127, 126, 124) or not out:
        return None
    try:
        data = json.loads(out)
        bits = data["end"]["sum_received"]["bits_per_second"]
        return float(bits) / 1e6
    except (ValueError, KeyError, TypeError):
        return None


# ==============================================================================
# Public verification entry points
# ==============================================================================


def verify_block(target_ip: str, *,
                 target_id: Optional[str] = None,
                 source_interface: Optional[str] = None,
                 runner: CommandRunner = default_command_runner,
                 time_fn: Callable[[], float] = None) -> VerificationResult:
    """Verify a device is blocked: ping should fail, drop rule should exist."""
    import time as _time
    now = time_fn or _time.monotonic
    start = now()
    checks: List[Check] = []

    loss = _ping_loss_percent(runner, target_ip, source_interface=source_interface)
    ping_passed = loss is not None and loss >= 99.0
    checks.append(Check(
        name="ping from sibling",
        expected="100% packet loss",
        measured=("unknown" if loss is None else f"{loss:g}% loss"),
        passed=ping_passed,
    ))

    rule_target = target_id or target_ip
    has_rule = _nft_has_drop_rule(runner, rule_target)
    if has_rule is not None:
        checks.append(Check(
            name="nftables drop rule",
            expected="present",
            measured="present" if has_rule else "absent",
            passed=bool(has_rule),
        ))

    # A block is verified if the ping confirms unreachability. The nft check is
    # corroborating evidence; if nft can't be queried we still trust the ping.
    passed = ping_passed and (has_rule is not False)
    return VerificationResult(
        action="block", target=rule_target, passed=passed, checks=checks,
        expected="unreachable", measured=checks[0].measured,
        duration_s=now() - start,
    )


def verify_unblock(target_ip: str, *,
                   target_id: Optional[str] = None,
                   source_interface: Optional[str] = None,
                   runner: CommandRunner = default_command_runner,
                   time_fn: Callable[[], float] = None) -> VerificationResult:
    """Verify a device is restored: ping should succeed, no drop rule remain."""
    import time as _time
    now = time_fn or _time.monotonic
    start = now()
    checks: List[Check] = []

    loss = _ping_loss_percent(runner, target_ip, source_interface=source_interface)
    ping_passed = loss is not None and loss <= 20.0
    checks.append(Check(
        name="ping from sibling",
        expected="reachable (<=20% loss)",
        measured=("unknown" if loss is None else f"{loss:g}% loss"),
        passed=ping_passed,
    ))

    rule_target = target_id or target_ip
    has_rule = _nft_has_drop_rule(runner, rule_target)
    if has_rule is not None:
        checks.append(Check(
            name="no leftover drop rule",
            expected="absent",
            measured="absent" if not has_rule else "present",
            passed=not has_rule,
        ))

    passed = ping_passed and (has_rule is not True)
    return VerificationResult(
        action="unblock", target=rule_target, passed=passed, checks=checks,
        expected="reachable", measured=checks[0].measured,
        duration_s=now() - start,
    )


def verify_limit(target_ip: str, expected_mbit: float, *,
                 target_id: Optional[str] = None,
                 interface: str = DEFAULT_AP_INTERFACE,
                 iperf_server_ip: Optional[str] = None,
                 tolerance: float = DEFAULT_TOLERANCE,
                 runner: CommandRunner = default_command_runner,
                 time_fn: Callable[[], float] = None) -> VerificationResult:
    """Verify a bandwidth limit: tc rate present, and measured rate within range.

    If ``iperf_server_ip`` is given, a throughput test is run and must land
    within ``tolerance`` of ``expected_mbit``. If iperf3 is unavailable, the tc
    rule presence alone is used (and the measured value is reported as the
    configured rate).
    """
    import time as _time
    now = time_fn or _time.monotonic
    start = now()
    checks: List[Check] = []
    lo = expected_mbit * (1 - tolerance)
    hi = expected_mbit * (1 + tolerance)

    tc_rate = _tc_rate_mbit(runner, interface)
    if tc_rate is not None:
        checks.append(Check(
            name="tc rate configured",
            expected=f"~{expected_mbit:g} Mbit/s",
            measured=f"{tc_rate:g} Mbit/s",
            passed=lo <= tc_rate <= hi,
        ))

    measured_mbit: Optional[float] = None
    if iperf_server_ip:
        measured_mbit = _iperf3_mbit(runner, iperf_server_ip, reverse=True)
        if measured_mbit is not None:
            checks.append(Check(
                name="iperf3 throughput",
                expected=f"{lo:.1f}-{hi:.1f} Mbit/s",
                measured=f"{measured_mbit:.1f} Mbit/s",
                passed=lo <= measured_mbit <= hi,
            ))

    if not checks:
        return VerificationResult(
            action="limit", target=target_id or target_ip, passed=False,
            checks=[], expected=f"~{expected_mbit:g} Mbit/s",
            measured="unknown", duration_s=now() - start,
            error="Neither tc nor iperf3 could be queried.",
        )

    passed = all(c.passed for c in checks)
    shown = (f"{measured_mbit:.1f} Mbit/s" if measured_mbit is not None
             else (f"{tc_rate:g} Mbit/s" if tc_rate is not None else "unknown"))
    return VerificationResult(
        action="limit", target=target_id or target_ip, passed=passed,
        checks=checks, expected=f"{lo:.1f}-{hi:.1f} Mbit/s",
        measured=shown, duration_s=now() - start,
    )


# ==============================================================================
# CLI (for manual checks on the Pi)
# ==============================================================================


def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="network.verification",
        description="Verify a network change by measurement (INA v2).",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    pb = sub.add_parser("block", help="Verify a device is blocked.")
    pb.add_argument("target_ip")
    pb.add_argument("--id", dest="target_id", default=None)
    pb.add_argument("--from-interface", dest="src", default=None)

    pu = sub.add_parser("unblock", help="Verify a device is restored.")
    pu.add_argument("target_ip")
    pu.add_argument("--id", dest="target_id", default=None)
    pu.add_argument("--from-interface", dest="src", default=None)

    pl = sub.add_parser("limit", help="Verify a bandwidth limit.")
    pl.add_argument("target_ip")
    pl.add_argument("mbit", type=float)
    pl.add_argument("--id", dest="target_id", default=None)
    pl.add_argument("--interface", default=DEFAULT_AP_INTERFACE)
    pl.add_argument("--iperf-server", dest="iperf", default=None)

    args = parser.parse_args(argv)

    if args.action == "block":
        result = verify_block(args.target_ip, target_id=args.target_id,
                              source_interface=args.src)
    elif args.action == "unblock":
        result = verify_unblock(args.target_ip, target_id=args.target_id,
                                source_interface=args.src)
    else:
        result = verify_limit(args.target_ip, args.mbit, target_id=args.target_id,
                              interface=args.interface, iperf_server_ip=args.iperf)

    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(_main())
