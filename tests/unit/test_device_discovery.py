"""Unit tests for the read-only network device discovery module.

Tests interface discovery, route parsing, Wi-Fi AP capability detection,
tunnel/VPN identification, error handling, and candidate inference.
All tests mock system command executions and do not require root privileges
or specific host networking configuration.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from network.device_discovery import (
    APCandidate,
    CommandRunner,
    DHCPLease,
    DiscoveredClient,
    InterfaceInfo,
    IPAddressInfo,
    NeighborEntry,
    NetworkReport,
    RouteInfo,
    TunnelCandidate,
    UplinkCandidate,
    _analyze_candidates,
    _inspect_wifi_and_ap_modes,
    _parse_ip_addr_json,
    _parse_ip_link_details,
    _parse_ip_routes_json,
    default_command_runner,
    discover_clients,
    discover_network,
    parse_dnsmasq_leases,
    parse_ip_neighbors,
)

# ------------------------------------------------------------------------------
# Mock Data Payloads
# ------------------------------------------------------------------------------

MOCK_IP_ADDR_JSON = json.dumps([
    {
        "ifindex": 1,
        "ifname": "lo",
        "flags": ["LOOPBACK", "UP", "LOWER_UP"],
        "mtu": 65536,
        "operstate": "UNKNOWN",
        "link_type": "loopback",
        "address": "00:00:00:00:00:00",
        "addr_info": [
            {"family": "inet", "local": "127.0.0.1", "prefixlen": 8, "scope": "host"},
            {"family": "inet6", "local": "::1", "prefixlen": 128, "scope": "host"},
        ],
    },
    {
        "ifindex": 2,
        "ifname": "eth0",
        "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"],
        "mtu": 1500,
        "operstate": "UP",
        "link_type": "ether",
        "address": "52:54:00:12:34:56",
        "addr_info": [
            {"family": "inet", "local": "192.168.10.5", "prefixlen": 24, "scope": "global"},
        ],
    },
    {
        "ifindex": 3,
        "ifname": "wlan0",
        "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"],
        "mtu": 1500,
        "operstate": "UP",
        "link_type": "ether",
        "address": "00:11:22:33:44:55",
        "addr_info": [
            {"family": "inet", "local": "192.168.1.150", "prefixlen": 24, "scope": "global"},
        ],
    },
    {
        "ifindex": 4,
        "ifname": "eth1",
        "flags": ["BROADCAST", "MULTICAST"],
        "mtu": 1500,
        "operstate": "DOWN",
        "link_type": "ether",
        "address": "52:54:00:ab:cd:ef",
        "addr_info": [],
    },
    {
        "ifindex": 5,
        "ifname": "wg0",
        "flags": ["POINTOPOINT", "UP", "LOWER_UP"],
        "mtu": 1420,
        "operstate": "UNKNOWN",
        "link_type": "none",
        "address": "",
        "addr_info": [
            {"family": "inet", "local": "10.8.0.2", "prefixlen": 24, "scope": "global"},
        ],
    },
    {
        "ifindex": 6,
        "ifname": "tun0",
        "flags": ["POINTOPOINT", "UP", "LOWER_UP"],
        "mtu": 1500,
        "operstate": "UP",
        "link_type": "none",
        "address": "",
        "addr_info": [
            {"family": "inet", "local": "10.9.0.5", "prefixlen": 24, "scope": "global"},
        ],
    },
    {
        "ifindex": 7,
        "ifname": "sit0",
        "flags": ["NOARP"],
        "mtu": 1480,
        "operstate": "DOWN",
        "link_type": "sit",
        "address": "",
        "addr_info": [],
    },
])

MOCK_IP_LINK_JSON = json.dumps([
    {"ifname": "wg0", "linkinfo": {"info_kind": "wireguard"}},
    {"ifname": "tun0", "linkinfo": {"info_kind": "tun"}},
    {"ifname": "sit0", "linkinfo": {"info_kind": "sit"}},
])

MOCK_IW_DEV_OUTPUT = """
phy#0
\tInterface wlan0
\t\tifindex 3
\t\twdev 0x1
\t\taddr 00:11:22:33:44:55
\t\ttype managed
"""

MOCK_IW_LIST_AP_SUPPORTED = """
Wiphy phy0
\tSupported interface modes:
\t\t * IBSS
\t\t * managed
\t\t * AP
\t\t * AP/VLAN
\t\t * monitor
"""

MOCK_IW_LIST_NO_AP = """
Wiphy phy0
\tSupported interface modes:
\t\t * IBSS
\t\t * managed
\t\t * monitor
"""

MOCK_IP_ROUTE_V4_JSON = json.dumps([
    {
        "dst": "default",
        "gateway": "192.168.1.1",
        "dev": "wlan0",
        "protocol": "dhcp",
        "metric": 600,
    },
    {
        "dst": "192.168.1.0/24",
        "dev": "wlan0",
        "protocol": "kernel",
        "scope": "link",
        "prefsrc": "192.168.1.150",
    },
    {
        "dst": "10.8.0.0/24",
        "dev": "wg0",
        "protocol": "kernel",
        "scope": "link",
        "prefsrc": "10.8.0.2",
    },
])

MOCK_IP_ROUTE_V6_JSON = json.dumps([
    {
        "dst": "default",
        "gateway": "fe80::1",
        "dev": "wlan0",
        "metric": 1024,
    }
])


# ------------------------------------------------------------------------------
# Test Suite
# ------------------------------------------------------------------------------


class TestDeviceDiscovery(unittest.TestCase):
    """Unit tests for the device discovery module."""

    def test_successful_interface_discovery(self) -> None:
        """Verify successful interface parsing, route extraction, and candidate inference."""

        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args == ["ip", "-j", "addr"]:
                return 0, MOCK_IP_ADDR_JSON, ""
            elif args == ["ip", "-j", "-d", "link"]:
                return 0, MOCK_IP_LINK_JSON, ""
            elif args == ["iw", "dev"]:
                return 0, MOCK_IW_DEV_OUTPUT, ""
            elif args == ["iw", "list"]:
                return 0, MOCK_IW_LIST_AP_SUPPORTED, ""
            elif args == ["ip", "-j", "route"]:
                return 0, MOCK_IP_ROUTE_V4_JSON, ""
            elif args == ["ip", "-j", "-6", "route"]:
                return 0, MOCK_IP_ROUTE_V6_JSON, ""
            return 1, "", "unhandled command"

        report = discover_network(runner=mock_runner)

        # 1. Interfaces
        self.assertEqual(len(report.interfaces), 7)
        ifaces = {i.name: i for i in report.interfaces}

        # lo
        self.assertTrue(ifaces["lo"].is_loopback)
        self.assertEqual(ifaces["lo"].mac_address, "00:00:00:00:00:00")
        self.assertEqual(len(ifaces["lo"].addresses), 2)

        # eth0
        self.assertEqual(ifaces["eth0"].operstate, "UP")
        self.assertEqual(ifaces["eth0"].mac_address, "52:54:00:12:34:56")
        self.assertFalse(ifaces["eth0"].is_wireless)
        self.assertFalse(ifaces["eth0"].is_tunnel)

        # wlan0
        self.assertTrue(ifaces["wlan0"].is_wireless)
        self.assertEqual(ifaces["wlan0"].interface_type, "wireless")
        self.assertIs(ifaces["wlan0"].ap_mode_supported, True)

        # 2. Routes
        self.assertEqual(len(report.routes), 4)  # 3 IPv4 + 1 IPv6
        self.assertEqual(len(report.default_routes), 2)  # 1 IPv4 + 1 IPv6 default

        # 3. Candidates
        self.assertTrue(len(report.uplink_candidates) >= 1)
        primary_uplink = report.uplink_candidates[0]
        self.assertEqual(primary_uplink.interface, "wlan0")
        self.assertEqual(primary_uplink.gateway, "192.168.1.1")
        self.assertTrue(primary_uplink.is_primary)

        # AP Candidate
        self.assertEqual(len(report.ap_candidates), 1)
        ap_cand = report.ap_candidates[0]
        self.assertEqual(ap_cand.interface, "wlan0")
        self.assertIs(ap_cand.ap_mode_supported, True)
        self.assertTrue(ap_cand.is_current_uplink)

        # Tunnel Candidate
        tunnel_names = [tc.interface for tc in report.tunnel_candidates]
        self.assertIn("wg0", tunnel_names)

    def test_multiple_interfaces_and_tunnel_interfaces(self) -> None:
        """Verify tunnel classification distinguishes tunnel types without assuming VPN."""

        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args == ["ip", "-j", "addr"]:
                return 0, MOCK_IP_ADDR_JSON, ""
            elif args == ["ip", "-j", "-d", "link"]:
                return 0, MOCK_IP_LINK_JSON, ""
            elif args in (["iw", "dev"], ["iw", "list"], ["ip", "-j", "route"], ["ip", "-j", "-6", "route"]):
                return 0, "[]" if "ip" in args else "", ""
            return 0, "", ""

        report = discover_network(runner=mock_runner)
        ifaces = {i.name: i for i in report.interfaces}

        # WireGuard tunnel
        self.assertTrue(ifaces["wg0"].is_tunnel)
        self.assertEqual(ifaces["wg0"].tunnel_type, "wireguard")
        self.assertIn("WireGuard", ifaces["wg0"].vpn_hint or "")

        # TUN tunnel
        self.assertTrue(ifaces["tun0"].is_tunnel)
        self.assertEqual(ifaces["tun0"].tunnel_type, "tun")
        self.assertIn("TUN device", ifaces["tun0"].vpn_hint or "")

        # SIT tunnel
        self.assertTrue(ifaces["sit0"].is_tunnel)
        self.assertEqual(ifaces["sit0"].tunnel_type, "sit")
        self.assertIn("SIT", ifaces["sit0"].vpn_hint or "")

        # Ethernet is not a tunnel
        self.assertFalse(ifaces["eth0"].is_tunnel)
        self.assertIsNone(ifaces["eth0"].tunnel_type)

    def test_missing_system_utilities(self) -> None:
        """Verify graceful error handling and warning generation when tools are missing."""

        def mock_runner_missing_all(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            return 127, "", f"Command not found: '{args[0]}'"

        report = discover_network(runner=mock_runner_missing_all)

        # Must not raise an exception; must return valid NetworkReport with warnings
        self.assertIsInstance(report, NetworkReport)
        self.assertEqual(len(report.interfaces), 0)
        self.assertEqual(len(report.routes), 0)
        self.assertTrue(any("ip' command was not found" in w for w in report.warnings))

    def test_invalid_or_unexpected_command_output(self) -> None:
        """Verify handling of invalid JSON or corrupted text output from utilities."""

        def mock_runner_corrupt(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if "ip" in args:
                return 0, "{not valid json at all!>", ""
            return 0, "", ""

        report = discover_network(runner=mock_runner_corrupt)

        self.assertIsInstance(report, NetworkReport)
        self.assertTrue(any("Failed to parse 'ip -j addr' JSON" in w for w in report.warnings))

    def test_missing_default_routes(self) -> None:
        """Verify report reflects absence of default routes without crashing."""
        local_routes_only = json.dumps([
            {"dst": "192.168.1.0/24", "dev": "eth0", "protocol": "kernel"},
            {"dst": "10.0.0.0/8", "dev": "eth0", "protocol": "kernel"},
        ])

        def mock_runner_no_default(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args == ["ip", "-j", "addr"]:
                return 0, MOCK_IP_ADDR_JSON, ""
            elif args == ["ip", "-j", "-d", "link"]:
                return 0, "[]", ""
            elif args == ["ip", "-j", "route"]:
                return 0, local_routes_only, ""
            elif args == ["ip", "-j", "-6", "route"]:
                return 0, "[]", ""
            return 0, "", ""

        report = discover_network(runner=mock_runner_no_default)

        self.assertEqual(len(report.default_routes), 0)
        self.assertEqual(len(report.uplink_candidates), 0)
        formatted = report.format_report()
        self.assertIn("None detected (No active default routes found)", formatted)

    def test_interfaces_without_assigned_ip(self) -> None:
        """Verify interface with operstate DOWN and no IP address is handled cleanly."""
        interfaces, warnings = _parse_ip_addr_json(MOCK_IP_ADDR_JSON)
        ifaces = {i.name: i for i in interfaces}

        eth1 = ifaces["eth1"]
        self.assertEqual(eth1.operstate, "DOWN")
        self.assertEqual(eth1.mac_address, "52:54:00:ab:cd:ef")
        self.assertEqual(eth1.addresses, [])

    def test_timeout_and_command_failures(self) -> None:
        """Verify timeout (124) and permission error (126) reporting in warnings."""

        def mock_runner_timeout(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if "iw" in args:
                return 124, "", f"Command timed out after {timeout}s"
            if args == ["ip", "-j", "-6", "route"]:
                return 126, "", "Permission denied"
            return 0, "[]", ""

        report = discover_network(runner=mock_runner_timeout)

        self.assertIsInstance(report, NetworkReport)
        self.assertTrue(any("timed out" in w or "Permission denied" in w for w in report.warnings))

    def test_ap_mode_supported_tristate(self) -> None:
        """Verify AP mode detection distinguishes True, False, and None."""
        iface_map = {
            "wlan0": InterfaceInfo(name="wlan0", operstate="UP", is_wireless=True),
        }

        # Case 1: Driver advertises AP mode
        def runner_ap_yes(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args == ["iw", "dev"]:
                return 0, MOCK_IW_DEV_OUTPUT, ""
            if args == ["iw", "list"]:
                return 0, MOCK_IW_LIST_AP_SUPPORTED, ""
            return 0, "", ""

        _inspect_wifi_and_ap_modes(runner_ap_yes, iface_map)
        self.assertIs(iface_map["wlan0"].ap_mode_supported, True)

        # Case 2: Driver does not advertise AP mode
        def runner_ap_no(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args == ["iw", "dev"]:
                return 0, MOCK_IW_DEV_OUTPUT, ""
            if args == ["iw", "list"]:
                return 0, MOCK_IW_LIST_NO_AP, ""
            return 0, "", ""

        _inspect_wifi_and_ap_modes(runner_ap_no, iface_map)
        self.assertIs(iface_map["wlan0"].ap_mode_supported, False)

        # Case 3: iw utility is missing (exit code 127) -> None (unavailable, not False)
        def runner_ap_missing(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            return 127, "", "Command not found"

        _inspect_wifi_and_ap_modes(runner_ap_missing, iface_map)
        self.assertIsNone(iface_map["wlan0"].ap_mode_supported)

    def test_report_formatting_and_dict_serialization(self) -> None:
        """Verify to_dict produces valid JSON-serializable structure and format_report works."""
        report = NetworkReport(
            timestamp="2026-10-25T12:00:00Z",
            platform_info="Linux Test 6.0",
            interfaces=[
                InterfaceInfo(
                    name="wlan0",
                    operstate="UP",
                    flags=["BROADCAST", "MULTICAST", "UP"],
                    mac_address="00:11:22:33:44:55",
                    interface_type="wireless",
                    addresses=[IPAddressInfo(address="192.168.1.50", family="ipv4", prefixlen=24)],
                    is_wireless=True,
                    ap_mode_supported=True,
                )
            ],
            routes=[
                RouteInfo(destination="default", interface="wlan0", gateway="192.168.1.1", is_default=True)
            ],
            default_routes=[
                RouteInfo(destination="default", interface="wlan0", gateway="192.168.1.1", is_default=True)
            ],
            uplink_candidates=[
                UplinkCandidate(
                    interface="wlan0",
                    gateway="192.168.1.1",
                    metric=100,
                    family="ipv4",
                    operstate="UP",
                    is_primary=True,
                    notes="Primary uplink candidate.",
                )
            ],
            ap_candidates=[
                APCandidate(
                    interface="wlan0",
                    ap_mode_supported=True,
                    operstate="UP",
                    mac_address="00:11:22:33:44:55",
                    is_current_uplink=True,
                    notes="AP capable.",
                )
            ],
            tunnel_candidates=[],
            warnings=["Sample diagnostic warning."],
        )

        # Verify to_dict()
        data = report.to_dict()
        self.assertIsInstance(data, dict)
        serialized = json.dumps(data)
        self.assertIn("192.168.1.50", serialized)

        # Verify format_report()
        text = report.format_report()
        self.assertIn("INA v2 — Host Network Discovery Report", text)
        self.assertIn("[ OBSERVED FACTS ]", text)
        self.assertIn("[ INFERRED CANDIDATES & ANALYSIS ]", text)
        self.assertIn("[ WARNINGS & DIAGNOSTICS ]", text)
        self.assertIn("Sample diagnostic warning.", text)


class TestClientDiscovery(unittest.TestCase):
    """Unit tests for dnsmasq lease parsing, ARP neighbor inspection, and client discovery."""

    def test_parse_dnsmasq_leases(self) -> None:
        """Verify parsing of dnsmasq.leases format lines with valid and unassigned hostnames."""
        sample_leases = (
            "# dnsmasq leases file\n"
            "1793087276 5e:22:f6:20:9a:e7 192.168.50.46 * 01:5e:22:f6:20:9a:e7\n"
            "1793088000 00:11:22:33:44:55 192.168.50.20 laptop-test 01:00:11:22:33:44:55\n"
            "invalid line with too few tokens\n"
            "not_a_number 00:11:22:33:44:66 192.168.50.21 host\n"
        )
        leases = parse_dnsmasq_leases(sample_leases)
        self.assertEqual(len(leases), 2)

        # First lease: hostname '*' becomes None
        self.assertEqual(leases[0].expiry_epoch, 1793087276)
        self.assertEqual(leases[0].mac_address, "5e:22:f6:20:9a:e7")
        self.assertEqual(leases[0].ip_address, "192.168.50.46")
        self.assertIsNone(leases[0].hostname)
        self.assertEqual(leases[0].client_id, "01:5e:22:f6:20:9a:e7")

        # Second lease: named host
        self.assertEqual(leases[1].expiry_epoch, 1793088000)
        self.assertEqual(leases[1].hostname, "laptop-test")

    def test_parse_ip_neighbors_text(self) -> None:
        """Verify parsing of plain-text 'ip neigh' output."""
        sample_output = (
            "192.168.50.46 dev ap0 lladdr 5e:22:f6:20:9a:e7 REACHABLE\n"
            "192.168.50.12 dev ap0 FAILED\n"
            "192.168.1.1 dev wlan0 lladdr 00:aa:bb:cc:dd:ee REACHABLE\n"
        )
        # Filter for ap0
        ap0_entries = parse_ip_neighbors(sample_output, target_interface="ap0")
        self.assertEqual(len(ap0_entries), 2)
        self.assertEqual(ap0_entries[0].ip_address, "192.168.50.46")
        self.assertEqual(ap0_entries[0].mac_address, "5e:22:f6:20:9a:e7")
        self.assertEqual(ap0_entries[0].state, "REACHABLE")

        self.assertEqual(ap0_entries[1].ip_address, "192.168.50.12")
        self.assertIsNone(ap0_entries[1].mac_address)
        self.assertEqual(ap0_entries[1].state, "FAILED")

        # No filter
        all_entries = parse_ip_neighbors(sample_output)
        self.assertEqual(len(all_entries), 3)

    def test_parse_ip_neighbors_json(self) -> None:
        """Verify parsing of 'ip -j neigh' JSON output."""
        sample_json = json.dumps([
            {
                "dst": "192.168.50.46",
                "dev": "ap0",
                "lladdr": "5e:22:f6:20:9a:e7",
                "state": ["REACHABLE"],
            },
            {
                "dst": "192.168.1.1",
                "dev": "wlan0",
                "lladdr": "00:aa:bb:cc:dd:ee",
                "state": ["REACHABLE"],
            },
        ])
        entries = parse_ip_neighbors(sample_json, target_interface="ap0")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].ip_address, "192.168.50.46")
        self.assertEqual(entries[0].state, "REACHABLE")

    def test_discover_clients_correlation(self) -> None:
        """Verify correlation between DHCP leases and kernel ARP neighbor table."""
        sample_leases = "1793087276 5e:22:f6:20:9a:e7 192.168.50.46 * 01:5e:22:f6:20:9a:e7\n"
        sample_neigh_text = "192.168.50.46 dev ap0 lladdr 5e:22:f6:20:9a:e7 REACHABLE\n"

        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            return 0, sample_neigh_text, ""

        with patch("os.path.exists", return_value=True), patch(
            "builtins.open", unittest.mock.mock_open(read_data=sample_leases)
        ):
            clients = discover_clients(
                ap_interface="ap0",
                leases_file="/var/lib/misc/dnsmasq.leases",
                runner=mock_runner,
            )

        self.assertEqual(len(clients), 1)
        client = clients[0]
        self.assertEqual(client.ip_address, "192.168.50.46")
        self.assertEqual(client.mac_address, "5e:22:f6:20:9a:e7")
        self.assertIsNone(client.hostname)
        self.assertEqual(client.neighbor_state, "REACHABLE")
        self.assertTrue(client.is_active)
        self.assertEqual(client.source, "both")
        # Randomized MAC detection (5e has locally administered bit set: 0x5e & 0x02 != 0)
        self.assertTrue(any("randomized" in n.lower() for n in client.notes))


if __name__ == "__main__":
    unittest.main()

