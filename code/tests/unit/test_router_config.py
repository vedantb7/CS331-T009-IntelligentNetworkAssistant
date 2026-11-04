"""Tests for the router-side configuration INA depends on.

- The nftables filter table is a real file and INA installs it (chains, not
  just sets) when it is missing.
- policy/devices.yaml drives dnsmasq reservations and anti-spoofing sets, and
  names resolve through it even when a lease shows another hostname.
- tests/lab/ina_block_lab.sh: real-kernel check that blocks drop traffic
  (runs only where unprivileged namespaces and nft are available).
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from inav2.network import access_control, devices, netctl, wireless
from inav2.policy.models import Device

REPO = Path(__file__).resolve().parents[2]
FILTER_FILE = REPO / "config" / "nftables" / "ina-filter.nft"


class FakeRunner:
    def __init__(self, replies=None):
        self.calls, self.files = [], {}
        self.replies = replies or {}

    def __call__(self, args, timeout=5.0):
        self.calls.append(list(args))
        if args[:2] == ["nft", "-f"]:
            self.files[args[2]] = Path(args[2]).read_text(encoding="utf-8")
        for prefix, reply in self.replies.items():
            if tuple(args[:len(prefix)]) == prefix:
                return reply
        return 0, "", ""


class TestFilterTable(unittest.TestCase):

    def test_file_defines_every_set_and_chain_the_code_uses(self):
        text = FILTER_FILE.read_text()
        for name in (access_control.SET_BLOCKED_PAIRS, access_control.SET_BLOCKED_INTERNET,
                     access_control.SET_BLOCKED_GATEWAY, access_control.SET_ISOLATED,
                     access_control.SET_MAC_IP_BIND, access_control.SET_MANAGED_IPS):
            self.assertRegex(text, rf"set {name} {{")
            self.assertIn(f"@{name}", text)
        for chain in ("prerouting", "forward", "input"):
            self.assertIn(f"chain {chain} {{", text)
        self.assertNotIn("flush ruleset", text.replace('"flush ruleset"', ""))

    def test_nftables_conf_includes_filter_table(self):
        conf = (REPO / "config" / "nftables" / "nftables.conf").read_text()
        self.assertIn('include "/etc/nftables.d/ina-filter.nft"', conf)
        self.assertIn("table ip ina_nat", conf)

    def test_chain_present_means_no_load(self):
        runner = FakeRunner()
        self.assertTrue(access_control.ensure_ina_filter_table(runner))
        self.assertEqual(runner.calls, [["nft", "list", "chain", "inet", "ina_filter", "forward"]])

    def test_missing_chain_loads_definition_without_reset_lines(self):
        runner = FakeRunner({("nft", "list", "chain"): (1, "", "No such file or directory")})
        with mock.patch.dict(os.environ, {"INA_FILTER_FILE": str(FILTER_FILE)}):
            access_control.ensure_ina_filter_table(runner)
        (loaded,) = runner.files.values()
        self.assertIn("chain forward {", loaded)
        self.assertNotIn("delete table inet ina_filter", loaded)  # keeps existing set elements
        self.assertIsNone(re.search(r"^table inet ina_filter$", loaded, re.M))

    def test_load_failure_raises(self):
        runner = FakeRunner({("nft", "list", "chain"): (1, "", "no chain"),
                             ("nft", "-f"): (1, "", "Error: syntax error")})
        with self.assertRaises(RuntimeError):
            access_control.ensure_ina_filter_table(runner)

    def test_installed_file_preferred_over_repo_copy(self):
        with mock.patch.dict(os.environ, {"INA_FILTER_FILE": ""}):
            with mock.patch.object(access_control.os.path, "isfile", return_value=False):
                self.assertEqual(access_control.filter_table_file(), access_control.REPO_FILTER_FILE)
            with mock.patch.object(access_control.os.path, "isfile", return_value=True):
                self.assertEqual(access_control.filter_table_file(), "/etc/nftables.d/ina-filter.nft")


DEVICES = [
    Device("pi-gateway", "02:00:00:00:00:01", "protected", ip="192.168.50.1"),
    Device("admin-laptop", "02:00:00:00:00:02", "protected", ip="192.168.50.20"),
    Device("guest-phone", "3a:1f:9c:22:7e:10", "guest"),
]


class TestDeviceSync(unittest.TestCase):

    def test_dhcp_hosts_rendering(self):
        lines = devices.render_dhcp_hosts(DEVICES).splitlines()
        body = [l for l in lines if not l.startswith("#")]
        self.assertEqual(body, ["02:00:00:00:00:02,192.168.50.20,admin-laptop",
                                "3a:1f:9c:22:7e:10,guest-phone"])  # router itself not reserved

    def test_ip_outside_subnet_rejected(self):
        with self.assertRaises(ValueError):
            devices.render_dhcp_hosts([Device("x", "02:00:00:00:00:09", "guest", ip="10.0.0.5")])

    def test_antispoof_transaction_binds_fixed_ips_only(self):
        txn = devices.antispoof_transaction(DEVICES)
        self.assertIn("flush set inet ina_filter managed_ips", txn)
        self.assertIn("add element inet ina_filter managed_ips { 192.168.50.20 }", txn)
        self.assertIn("add element inet ina_filter mac_ip_bind { 02:00:00:00:00:02 . 192.168.50.20 }", txn)
        self.assertNotIn("192.168.50.1 ", txn)

    def test_sync_refuses_example_macs(self):
        with self.assertRaises(ValueError) as ctx:
            devices.sync_devices(runner=FakeRunner(), hosts_path=os.devnull)
        self.assertIn("example MACs", str(ctx.exception))

    def test_sync_writes_reservations_sets_and_reloads_dnsmasq(self):
        tmp = tempfile.mkdtemp()
        try:
            hosts = os.path.join(tmp, "dhcp-hosts")
            runner = FakeRunner()
            with mock.patch.object(devices, "load_devices", return_value=DEVICES):
                summary = devices.sync_devices(runner=runner, hosts_path=hosts)
            self.assertEqual(summary["reservations"], 2)
            self.assertEqual(summary["anti_spoofing_bound"], 1)
            self.assertIn("3a:1f:9c:22:7e:10,guest-phone", Path(hosts).read_text())
            self.assertIn(["systemctl", "kill", "--signal=HUP", "dnsmasq.service"], runner.calls)
            self.assertTrue(any("mac_ip_bind" in t for t in runner.files.values()))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_shipped_dnsmasq_conf_reads_the_hosts_file(self):
        conf = (REPO / "config" / "dnsmasq" / "ina-ap.conf").read_text()
        self.assertIn("dhcp-hostsfile=/etc/ina/dhcp-hosts", conf)


class TestNameResolutionFallback(unittest.TestCase):
    """A registered name resolves by MAC even if the lease shows another hostname."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.leases = os.path.join(self.tmp, "dnsmasq.leases")
        with open(self.leases, "w") as f:
            f.write("1893456000 3a:1f:9c:22:7e:10 192.168.50.34 Pixel-7 01:3a:1f:9c:22:7e:10\n")
        self.patch = mock.patch.object(devices, "device_mac",
                                       side_effect=lambda n: {"guest-phone": "3a:1f:9c:22:7e:10",
                                                              "office-printer": "02:00:00:00:00:77"}.get(n))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_access_control_resolves_registered_name(self):
        self.assertEqual(access_control.validate_client_identifier("guest-phone", leases_file=self.leases),
                         "192.168.50.34")
        with self.assertRaises(ValueError):
            access_control.validate_client_identifier("someone-else", leases_file=self.leases)

    def test_wireless_resolves_registered_name_and_offline_device(self):
        st = wireless.resolve_station("guest-phone", runner=FakeRunner(), leases_file=self.leases)
        self.assertEqual((st.ip, st.mac), ("192.168.50.34", "3a:1f:9c:22:7e:10"))
        offline = wireless.resolve_station("office-printer", runner=FakeRunner(), leases_file=self.leases)
        self.assertEqual((offline.ip, offline.mac), (None, "02:00:00:00:00:77"))

    def test_explicit_leases_map_is_not_overridden(self):
        with self.assertRaises(ValueError):
            access_control.validate_client_identifier("guest-phone", leases_map={"other": "192.168.50.40"})


def _lab_available() -> bool:
    if not (shutil.which("unshare") and shutil.which("nft") and shutil.which("ip")):
        return False
    probe = subprocess.run(["unshare", "-r", "-n", "nft", "list", "ruleset"], capture_output=True)
    return probe.returncode == 0


@unittest.skipUnless(os.environ.get("INA_RUN_LAB") == "1" and _lab_available(),
                     "set INA_RUN_LAB=1 (needs unprivileged user namespaces, nft, iproute2)")
class TestBlockingLab(unittest.TestCase):
    """Real kernel: INA installs the filter table itself and blocks drop traffic."""

    def test_blocks_are_enforced(self):
        out = subprocess.run(["unshare", "-r", "-n", "-m", "sh", str(REPO / "tests" / "lab" / "ina_block_lab.sh")],
                             capture_output=True, text=True, timeout=120).stdout
        lines = {l.split(":")[0]: l for l in out.splitlines() if l.startswith(("baseline", "after"))}
        self.assertIn("a->internet reach", lines["baseline"])
        self.assertIn("a->internet BLOCKED", lines["after block internet"])
        self.assertIn("b->internet reach", lines["after block internet"])
        self.assertIn("a->internet reach", lines["after unblock"])
        self.assertIn("a->b BLOCKED   b->a BLOCKED", lines["after block a<->b"])
        self.assertIn("b->internet BLOCKED", lines["after isolate b"])


if __name__ == "__main__":
    unittest.main()
