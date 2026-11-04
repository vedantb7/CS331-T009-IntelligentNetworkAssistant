"""Unit tests for wireless control, hostapd primitives, kick, ban, and unban (§8.1)."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKSPACE_DIR = os.path.dirname(BASE_DIR)
for p in (WORKSPACE_DIR, BASE_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

from inav2.assistant import intent
from inav2.assistant.contract import ToolName
from inav2.mcp_server.schemas import (
    BanClientArgs,
    KickClientArgs,
    UnbanClientArgs,
)
from inav2.network import netctl, wireless
from inav2.network.audit import verify_audit_log_integrity
from inav2.network.device_discovery import DHCPLease
from inav2.network.service import (
    ClientNotFoundError,
    NetworkService,
    PolicyDeniedError,
    RateLimitedError,
    WirelessControlError,
)
from inav2.network.wireless import (
    Station,
    ban_station,
    kick_station,
    normalize_mac,
    read_deny_file,
    resolve_station,
    unban_station,
)


class MockHostapdRunner:
    """Simulates hostapd_cli: associated stations, deny ACL and reconnect behaviour."""

    def __init__(self) -> None:
        self.commands: List[List[str]] = []
        self.stations: Dict[str, int] = {"3a:1f:9c:22:7e:10": 120}  # mac -> connected_time
        self.deny: Set[str] = set()
        self.reconnect_after_kick: bool = False
        self.fail_deauth: bool = False
        self.ctrl_available: bool = True

    def __call__(self, args: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        self.commands.append(list(args))
        if args[0] != "hostapd_cli":
            return 0, "", ""
        if not self.ctrl_available:
            return 255, "", "Failed to connect to hostapd - wpa_ctrl_open: No such file or directory"
        cmd = args[3:]
        if not cmd:
            return 0, "", ""
        if cmd[0] == "sta":
            mac = cmd[1].lower()
            if mac in self.stations:
                return 0, f"{mac}\nflags=[AUTH][ASSOC][AUTHORIZED]\nconnected_time={self.stations[mac]}\n", ""
            return 0, "FAIL\n", ""
        if cmd[0] == "deauthenticate":
            if self.fail_deauth:
                return 0, "FAIL\n", ""
            mac = cmd[1].lower()
            self.stations.pop(mac, None)
            if self.reconnect_after_kick and mac not in self.deny:
                self.stations[mac] = 1
            return 0, "OK\n", ""
        if cmd[:2] == ["deny_acl", "ADD_MAC"]:
            mac = cmd[2].lower()
            self.deny.add(mac)
            self.stations.pop(mac, None)
            return 0, "OK\n", ""
        if cmd[:2] == ["deny_acl", "DEL_MAC"]:
            mac = cmd[2].lower()
            self.deny.discard(mac)
            return 0, "OK\n", ""
        if cmd[:2] == ["deny_acl", "SHOW"]:
            return 0, "".join(f"{m} VLAN_ID=0\n" for m in sorted(self.deny)), ""
        return 0, "", ""


class TestWirelessSuite(unittest.TestCase):
    """24 unit tests matching §4.6-4.8 of docs/implementation.md."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()
        self.deny_file = os.path.join(self.test_dir, "hostapd.deny")
        self.lock_path = os.path.join(self.test_dir, "ina.lock")
        self.state_path = os.path.join(self.test_dir, "state.json")
        self.audit_path = os.path.join(self.test_dir, "audit.log")
        self.kick_history_path = os.path.join(self.test_dir, "kick_history.json")

        self.runner = MockHostapdRunner()
        self.leases = [
            DHCPLease(expiry_epoch=1700000000, mac_address="3a:1f:9c:22:7e:10",
                      ip_address="192.168.50.21", hostname="phone-b"),
            DHCPLease(expiry_epoch=1700000000, mac_address="3a:1f:9c:22:7e:20",
                      ip_address="192.168.50.22", hostname="laptop-a"),
        ]

        self.service = NetworkService(
            runner=self.runner,
            lock_path=self.lock_path,
            state_path=self.state_path,
            audit_path=self.audit_path,
            deny_file=self.deny_file,
            kick_history_path=self.kick_history_path,
        )
        self.service.leases_file = None
        # Mock resolve_station by patching _load_leases in wireless
        self._orig_load_leases = wireless._load_leases
        wireless._load_leases = lambda _: self.leases

        # Fast sleeper for instant execution
        self._orig_sleep = wireless.time.sleep
        wireless.time.sleep = lambda _: None

    def tearDown(self) -> None:
        wireless._load_leases = self._orig_load_leases
        wireless.time.sleep = self._orig_sleep
        shutil.rmtree(self.test_dir, ignore_errors=True)
        os.environ.pop("SSH_CLIENT", None)
        os.environ.pop("INA_ALLOW_WRITE", None)

    # 1. Resolve by name / IPv4 / MAC (upper-case MAC normalised) -> Correct Station
    def test_01_resolve_station(self) -> None:
        s1 = resolve_station("phone-b", runner=self.runner, leases=self.leases)
        self.assertEqual(s1.name, "phone-b")
        self.assertEqual(s1.ip, "192.168.50.21")
        self.assertEqual(s1.mac, "3a:1f:9c:22:7e:10")

        s2 = resolve_station("192.168.50.21", runner=self.runner, leases=self.leases)
        self.assertEqual(s2.mac, "3a:1f:9c:22:7e:10")

        s3 = resolve_station("3A:1F:9C:22:7E:10", runner=self.runner, leases=self.leases)
        self.assertEqual(s3.mac, "3a:1f:9c:22:7e:10")
        self.assertEqual(s3.name, "phone-b")

    # 2. Reject broadcast, multicast, zero MAC, AP's own MAC -> ValueError
    def test_02_reject_invalid_macs(self) -> None:
        with self.assertRaises(ValueError):
            normalize_mac("ff:ff:ff:ff:ff:ff")  # broadcast
        with self.assertRaises(ValueError):
            normalize_mac("01:00:5e:00:00:01")  # multicast
        with self.assertRaises(ValueError):
            normalize_mac("00:00:00:00:00:00")  # zero
        with self.assertRaises(ValueError):
            normalize_mac("not-a-mac")

    # 3. Kick associated station, no reconnect -> kicked=True, reconnected=False, verified=True
    def test_03_kick_associated_no_reconnect(self) -> None:
        station = Station(name="phone-b", ip="192.168.50.21", mac="3a:1f:9c:22:7e:10")
        res = kick_station(station, runner=self.runner, sleeper=lambda s: None)
        self.assertTrue(res.kicked)
        self.assertFalse(res.reconnected)
        self.assertTrue(res.verified)
        self.assertTrue(res.changed)

    # 4. Kick with reconnect_after_kick=True -> reconnected=True, verified=True
    def test_04_kick_with_reconnect(self) -> None:
        self.runner.reconnect_after_kick = True
        station = Station(name="phone-b", ip="192.168.50.21", mac="3a:1f:9c:22:7e:10")
        res = kick_station(station, runner=self.runner, sleeper=lambda s: None)
        self.assertTrue(res.kicked)
        self.assertTrue(res.reconnected)
        self.assertTrue(res.verified)

    # 5. Kick unassociated -> changed=False, kicked=False, no deauthenticate command issued
    def test_05_kick_unassociated(self) -> None:
        station = Station(name="laptop-a", ip="192.168.50.22", mac="3a:1f:9c:22:7e:20")
        res = kick_station(station, runner=self.runner, sleeper=lambda s: None)
        self.assertFalse(res.changed)
        self.assertFalse(res.kicked)
        self.assertFalse(res.was_associated)
        self.assertTrue(res.verified)
        deauth_cmds = [c for c in self.runner.commands if len(c) > 3 and c[3] == "deauthenticate"]
        self.assertEqual(len(deauth_cmds), 0)

    # 6. hostapd replies FAIL to deauth -> WirelessControlError, audit failed
    def test_06_deauth_failure(self) -> None:
        self.runner.fail_deauth = True
        with self.assertRaises(WirelessControlError):
            self.service.kick("phone-b", role="operator")
        # Verify audit failed
        with open(self.audit_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        rec = json.loads(lines[-1])
        self.assertEqual(rec["status"], "failed")

    # 7. Control socket missing -> Clear error mentioning ctrl_interface
    def test_07_control_socket_missing(self) -> None:
        self.runner.ctrl_available = False
        with self.assertRaises(WirelessControlError) as ctx:
            self.service.kick("phone-b", role="operator")
        self.assertIn("ctrl_interface", str(ctx.exception))

    # 8. Kick protected IP / protected MAC -> PolicyDeniedError, no hostapd command issued
    def test_08_kick_protected_denied(self) -> None:
        self.runner.commands.clear()
        # 192.168.50.1 is protected IP
        with self.assertRaises(PolicyDeniedError):
            self.service.kick("192.168.50.1", role="operator")
        self.assertEqual(len(self.runner.commands), 0)

        # aa:bb:cc:dd:ee:01 is protected MAC (admin-laptop)
        with self.assertRaises(PolicyDeniedError):
            self.service.kick("aa:bb:cc:dd:ee:01", role="operator")
        self.assertEqual(len(self.runner.commands), 0)

    # 9. Self-lockout (SSH_CLIENT = client IP) -> PolicyDeniedError
    def test_09_self_lockout(self) -> None:
        os.environ["SSH_CLIENT"] = "192.168.50.21 54321 22"
        with self.assertRaises(PolicyDeniedError) as ctx:
            self.service.kick("phone-b", role="operator")
        self.assertIn("device issuing this command", str(ctx.exception))

    # 10. 4th kick within 10 minutes -> RateLimitedError
    def test_10_kick_rate_limiting(self) -> None:
        # First 3 kicks succeed (with reconnect enabled and connected_time refreshed)
        self.runner.reconnect_after_kick = True
        self.runner.stations["3a:1f:9c:22:7e:10"] = 120
        self.service.kick("phone-b", role="operator")
        self.runner.stations["3a:1f:9c:22:7e:10"] = 120
        self.service.kick("phone-b", role="operator")
        self.runner.stations["3a:1f:9c:22:7e:10"] = 120
        self.service.kick("phone-b", role="operator")

        # 4th kick within 10 minutes raises RateLimitedError
        with self.assertRaises(RateLimitedError):
            self.service.kick("phone-b", role="operator")

    # 11. Ban without confirm -> preview=True, deny set unchanged, file unchanged, audit previewed
    def test_11_ban_preview_without_confirm(self) -> None:
        res = self.service.ban("phone-b", duration_minutes=30, confirm=False, role="operator")
        self.assertTrue(res.preview)
        self.assertTrue(res.requires_confirmation)
        self.assertFalse(res.banned)
        self.assertFalse(res.changed)
        self.assertEqual(len(self.runner.deny), 0)
        self.assertEqual(len(read_deny_file(self.deny_file)), 0)

        # Audit contains status="previewed"
        with open(self.audit_path, "r", encoding="utf-8") as f:
            rec = json.loads(f.readlines()[-1])
        self.assertEqual(rec["status"], "previewed")

    # 12. Ban with confirm -> MAC in deny ACL and file, station gone, state entry with expires_at
    def test_12_ban_confirmed(self) -> None:
        res = self.service.ban("phone-b", duration_minutes=30, confirm=True, role="operator")
        self.assertFalse(res.preview)
        self.assertFalse(res.requires_confirmation)
        self.assertTrue(res.banned)
        self.assertTrue(res.changed)
        self.assertTrue(res.verified)
        mac = "3a:1f:9c:22:7e:10"
        self.assertIn(mac, self.runner.deny)
        self.assertIn(mac, read_deny_file(self.deny_file))
        self.assertNotIn(mac, self.runner.stations)

        with open(self.state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        bans = state.get("bans", [])
        self.assertEqual(len(bans), 1)
        self.assertIsNotNone(bans[0]["expires_at"])

    # 13. Ban twice -> Second call changed=False; expiry refreshed in state
    def test_13_ban_idempotent_refresh_expiry(self) -> None:
        self.service.ban("phone-b", duration_minutes=30, confirm=True, role="operator")
        res2 = self.service.ban("phone-b", duration_minutes=60, confirm=True, role="operator")
        self.assertFalse(res2.changed)
        self.assertTrue(res2.verified)

        with open(self.state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        bans = state.get("bans", [])
        self.assertEqual(len(bans), 1)

    # 14. Deny-file write fails (read-only dir) -> Runtime DEL_MAC issued (rollback), error raised
    def test_14_deny_file_write_failure_rollback(self) -> None:
        ro_dir = os.path.join(self.test_dir, "ro_dir")
        os.makedirs(ro_dir, exist_ok=True)
        bad_deny_file = os.path.join(ro_dir, "hostapd.deny")
        station = Station(name="phone-b", ip="192.168.50.21", mac="3a:1f:9c:22:7e:10")

        # Make dir unwritable
        os.chmod(ro_dir, 0o400)
        try:
            with self.assertRaises(RuntimeError):
                ban_station(station, runner=self.runner, deny_file=bad_deny_file, sleeper=lambda s: None)
            del_cmds = [c for c in self.runner.commands if len(c) > 4 and c[3:5] == ["deny_acl", "DEL_MAC"]]
            self.assertGreaterEqual(len(del_cmds), 1)
            self.assertNotIn("3a:1f:9c:22:7e:10", self.runner.deny)
        finally:
            os.chmod(ro_dir, 0o700)

    # 15. Operator permanent ban / operator 120 min -> PolicyDeniedError
    def test_15_operator_restrictions(self) -> None:
        # Permanent (no duration) denied for operator
        with self.assertRaises(PolicyDeniedError):
            self.service.ban("phone-b", duration_minutes=None, confirm=True, role="operator")

        # Duration > 60 min denied for operator
        with self.assertRaises(PolicyDeniedError):
            self.service.ban("phone-b", duration_minutes=120, confirm=True, role="operator")

    # 16. Unban by MAC when device has no lease -> Works (resolved from state or MAC)
    def test_16_unban_by_mac_no_lease(self) -> None:
        # Device not in leases
        unleased_mac = "5e:aa:01:4c:93:d2"
        self.runner.deny.add(unleased_mac)
        wireless.write_deny_file({unleased_mac}, path=self.deny_file)

        res = self.service.unban(unleased_mac, role="admin")
        self.assertTrue(res.unbanned)
        self.assertTrue(res.changed)
        self.assertNotIn(unleased_mac, self.runner.deny)
        self.assertNotIn(unleased_mac, read_deny_file(self.deny_file))

    # 17. Unban twice -> Second changed=False
    def test_17_unban_twice(self) -> None:
        mac = "3a:1f:9c:22:7e:10"
        self.runner.deny.add(mac)
        wireless.write_deny_file({mac}, path=self.deny_file)

        res1 = self.service.unban(mac, role="admin")
        self.assertTrue(res1.changed)

        res2 = self.service.unban(mac, role="admin")
        self.assertFalse(res2.changed)
        self.assertTrue(res2.verified)

    # 18. reconcile with one active and one expired ban -> Only active written
    def test_18_reconcile_active_and_expired(self) -> None:
        now = datetime.now(timezone.utc)
        active_exp = (now + timedelta(hours=1)).isoformat()
        expired_exp = (now - timedelta(hours=1)).isoformat()

        state_data = {
            "version": "1.1",
            "updated_at": now.isoformat(),
            "blocks": [],
            "limits": [],
            "bans": [
                {"client": "phone-b", "mac": "3a:1f:9c:22:7e:10", "expires_at": active_exp},
                {"client": "laptop-a", "mac": "3a:1f:9c:22:7e:20", "expires_at": expired_exp},
            ],
        }
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(state_data, f)

        orig_deny = wireless.DENY_MAC_FILE
        orig_runner = wireless._default_runner
        orig_load = netctl.load_state
        try:
            wireless.DENY_MAC_FILE = self.deny_file
            wireless._default_runner = self.runner
            netctl.load_state = lambda *a, **kw: state_data
            with mock.patch.dict(os.environ, {"INA_COMMAND_LOG": os.path.join(os.path.dirname(self.deny_file), "commands.jsonl")}):
                netctl.reconcile(deny_file=self.deny_file, runner=self.runner)

            deny_macs = read_deny_file(self.deny_file)
            self.assertIn("3a:1f:9c:22:7e:10", deny_macs)
            self.assertNotIn("3a:1f:9c:22:7e:20", deny_macs)
        finally:
            wireless.DENY_MAC_FILE = orig_deny
            wireless._default_runner = orig_runner
            netctl.load_state = orig_load

    # 19. expire -> Expired ban removed, audit caller_role="system"
    def test_19_expire_bans(self) -> None:
        now = datetime.now(timezone.utc)
        expired_exp = (now - timedelta(minutes=5)).isoformat()
        state_data = {
            "version": "1.1",
            "bans": [
                {"client": "phone-b", "mac": "3a:1f:9c:22:7e:10", "expires_at": expired_exp},
            ],
        }
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(state_data, f)

        count = self.service.expire_bans()
        self.assertEqual(count, 1)

        with open(self.state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        self.assertEqual(len(state.get("bans", [])), 0)

        with open(self.audit_path, "r", encoding="utf-8") as f:
            rec = json.loads(f.readlines()[-1])
        self.assertEqual(rec["caller_role"], "system")
        self.assertEqual(rec["action"], "unban_client")

    # 20. Ban does not touch nft/tc -> No nft/tc in runner.commands
    def test_20_ban_does_not_touch_nft_or_tc(self) -> None:
        self.runner.commands.clear()
        self.service.ban("phone-b", duration_minutes=30, confirm=True, role="operator")
        for cmd in self.runner.commands:
            self.assertNotIn("nft", cmd[0])
            self.assertNotIn("tc", cmd[0])

    # 21. Schema: duration_minutes=0, 10081, reason="x; rm -rf /", extra key force -> Rejected
    def test_21_schema_validation(self) -> None:
        with self.assertRaises(Exception):
            BanClientArgs(client="phone-b", duration_minutes=0)
        with self.assertRaises(Exception):
            BanClientArgs(client="phone-b", duration_minutes=10081)
        with self.assertRaises(Exception):
            BanClientArgs(client="phone-b", reason="x; rm -rf /")
        with self.assertRaises(Exception):
            BanClientArgs(client="phone-b", force=True)

    # 22. Intent: "limit bandwidth for guest-phone" -> Not parsed as ban
    def test_22_intent_limit_not_ban(self) -> None:
        res = intent.parse("limit bandwidth for guest-phone to 5 mbps")
        self.assertEqual(res.action, ToolName.LIMIT_BANDWIDTH.value)

    # 23. Intent: kick / ban for 2 hours / unban
    def test_23_intent_parsing_kick_ban_unban(self) -> None:
        r_kick = intent.parse("kick guest-phone")
        self.assertEqual(r_kick.action, ToolName.KICK_CLIENT.value)
        self.assertEqual(r_kick.target, "guest-phone")

        r_ban = intent.parse("ban guest-phone for 2 hours")
        self.assertEqual(r_ban.action, ToolName.BAN_CLIENT.value)
        self.assertEqual(r_ban.target, "guest-phone")
        self.assertEqual(r_ban.params.get("minutes"), 120)

        r_unban = intent.parse("unban guest-phone")
        self.assertEqual(r_unban.action, ToolName.UNBAN_CLIENT.value)
        self.assertEqual(r_unban.target, "guest-phone")

    # 24. Intent: "ban guest-phone" -> Clarification asking for duration
    def test_24_intent_ban_no_duration_clarifies(self) -> None:
        res = intent.parse("ban guest-phone")
        self.assertTrue(res.needs_clarification)
        self.assertIn("permanently", res.clarification.lower())
        self.assertIn("minutes", res.clarification.lower())


if __name__ == "__main__":
    unittest.main()
