"""Unit tests for the Access Point and Router Infrastructure Management module.

Tests configuration validation, credential masking, router service inspection,
kernel forwarding and NAT verification, and preflight safety checks.
All system commands and kernel queries are mocked using CommandRunner.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from network.access_point import (
    DEFAULT_AP_CHANNEL,
    DEFAULT_AP_INTERFACE,
    DEFAULT_AP_SSID,
    DEFAULT_AP_SUBNET,
    DEFAULT_ROUTER_IP,
    DEFAULT_UPLINK_INTERFACE,
    APConfig,
    PreflightReport,
    RouterStatus,
    ServiceStatus,
    inspect_router_status,
    load_network_settings,
    preflight_safety_check,
    validate_ap_config,
)
from network.device_discovery import CommandRunner


class TestAccessPointConfigValidation(unittest.TestCase):
    """Unit tests for AP configuration validation and password masking."""

    def test_valid_configuration(self) -> None:
        """Verify default and valid custom configurations pass with zero errors."""
        config = APConfig()
        self.assertEqual(config.ssid, DEFAULT_AP_SSID)
        self.assertEqual(config.channel, DEFAULT_AP_CHANNEL)
        self.assertEqual(config.interface, DEFAULT_AP_INTERFACE)
        self.assertEqual(config.uplink_interface, DEFAULT_UPLINK_INTERFACE)
        self.assertEqual(config.subnet, DEFAULT_AP_SUBNET)
        self.assertEqual(config.router_ip, DEFAULT_ROUTER_IP)

        errors = validate_ap_config(config)
        self.assertEqual(errors, [])

        # Valid custom config with WPA2 passphrase
        custom = APConfig(
            ssid="INA-Lab-AP",
            password="SecurePassphrase123!",
            channel=6,
            interface="ap0",
            uplink_interface="wlan0",
            router_ip="10.0.0.1",
            subnet="10.0.0.0/24",
            dhcp_range_start="10.0.0.10",
            dhcp_range_end="10.0.0.100",
        )
        self.assertEqual(validate_ap_config(custom), [])

    def test_valid_64_hex_password(self) -> None:
        """Verify 64-character hexadecimal pre-shared key is accepted."""
        hex_key = "a" * 64
        config = APConfig(password=hex_key)
        self.assertEqual(validate_ap_config(config), [])

    def test_invalid_ssid(self) -> None:
        """Verify rejection of empty, oversized (>32 octets), or control character SSIDs."""
        # Empty SSID
        self.assertIn("empty", "; ".join(validate_ap_config(APConfig(ssid=""))))
        self.assertIn("empty", "; ".join(validate_ap_config(APConfig(ssid="   "))))

        # Oversized (>32 bytes)
        long_ssid = "A" * 33
        self.assertIn("exceeds 32 octets", "; ".join(validate_ap_config(APConfig(ssid=long_ssid))))

        # Multi-byte UTF-8 boundary
        utf8_ssid = "€" * 11  # 3 bytes each = 33 bytes
        self.assertIn("exceeds 32 octets", "; ".join(validate_ap_config(APConfig(ssid=utf8_ssid))))

        # Control characters
        self.assertIn("control characters", "; ".join(validate_ap_config(APConfig(ssid="INA\x01Router"))))

    def test_invalid_password(self) -> None:
        """Verify passwords <8 chars, >63 chars non-hex, or with non-ASCII chars fail."""
        # Too short (<8)
        self.assertIn("8–63 ASCII", "; ".join(validate_ap_config(APConfig(password="short7"))))

        # Too long non-hex (>63)
        self.assertIn("8–63 ASCII", "; ".join(validate_ap_config(APConfig(password="z" * 64))))

        # Non-ASCII or non-printable characters
        self.assertIn("non-printable", "; ".join(validate_ap_config(APConfig(password="pass\x07word123"))))

    def test_invalid_subnets(self) -> None:
        """Verify rejection of non-CIDR, public IP ranges, and out-of-bounds prefixes."""
        # Non-CIDR format
        self.assertIn("Invalid IPv4 subnet format", "; ".join(validate_ap_config(APConfig(subnet="192.168.1.1"))))

        # Public IP subnet (non-RFC 1918)
        self.assertIn("not within RFC 1918", "; ".join(validate_ap_config(APConfig(subnet="8.8.8.0/24"))))

        # Impractical prefix
        self.assertIn("impractical", "; ".join(validate_ap_config(APConfig(subnet="10.0.0.0/31"))))
        self.assertIn("impractical", "; ".join(validate_ap_config(APConfig(subnet="10.0.0.0/7"))))

        # Router IP outside subnet
        self.assertIn("not within subnet", "; ".join(validate_ap_config(
            APConfig(subnet="192.168.50.0/24", router_ip="192.168.10.1")
        )))

        # Router IP as network address
        self.assertIn("cannot be the network or broadcast", "; ".join(validate_ap_config(
            APConfig(subnet="192.168.50.0/24", router_ip="192.168.50.0")
        )))

    def test_invalid_interface_and_channels(self) -> None:
        """Verify invalid interface characters and conflicting interface pairs fail."""
        # Same interface for AP and uplink
        errors = validate_ap_config(APConfig(interface="wlan0", uplink_interface="wlan0"))
        self.assertIn("cannot be the same physical/virtual device", "; ".join(errors))

        # Invalid interface characters
        self.assertIn("Invalid AP interface name", "; ".join(validate_ap_config(APConfig(interface="wlan0;rm"))))

        # Invalid channel for 2.4 GHz
        self.assertIn("Invalid 2.4 GHz Wi-Fi channel", "; ".join(validate_ap_config(APConfig(channel=15))))
        self.assertIn("Invalid 2.4 GHz Wi-Fi channel", "; ".join(validate_ap_config(APConfig(channel=0))))

    def test_password_masking_in_repr(self) -> None:
        """Verify password is masked in __repr__ and to_dict()."""
        config = APConfig(password="UltraSecretPass123!")
        repr_str = repr(config)
        self.assertNotIn("UltraSecretPass123!", repr_str)
        self.assertIn("***", repr_str)

        d = config.to_dict()
        self.assertEqual(d["password"], "***")


class TestRouterStatusInspection(unittest.TestCase):
    """Unit tests for live status inspection of the Raspberry Pi router infrastructure."""

    def test_router_status_all_active(self) -> None:
        """Verify operational status when all interfaces, services, and forwarding are active."""
        ap_ip_json = json.dumps([
            {
                "ifname": "ap0",
                "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"],
                "operstate": "UP",
                "addr_info": [{"family": "inet", "local": "192.168.50.1", "prefixlen": 24}],
            }
        ])
        uplink_route_json = json.dumps([
            {"dst": "default", "gateway": "192.168.1.1", "dev": "wlan0"}
        ])

        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            cmd = " ".join(args)
            if "ip -j addr show dev ap0" in cmd:
                return 0, ap_ip_json, ""
            elif "ip -j route show dev wlan0" in cmd:
                return 0, uplink_route_json, ""
            elif "systemctl is-active hostapd" in cmd:
                return 0, "active\n", ""
            elif "systemctl is-active dnsmasq" in cmd:
                return 0, "active\n", ""
            elif "systemctl is-active ina-ap-interface" in cmd:
                return 0, "active\n", ""
            elif "systemctl is-active nftables" in cmd:
                return 0, "active\n", ""
            elif "sysctl -n net.ipv4.ip_forward" in cmd:
                return 0, "1\n", ""
            elif "nft list table ip ina_nat" in cmd:
                return 0, "table ip ina_nat { chain postrouting { masquerade } }\n", ""
            return 0, "", ""

        status = inspect_router_status(runner=mock_runner)
        self.assertTrue(status.is_operational)
        self.assertTrue(status.ap_interface_up)
        self.assertEqual(status.ap_interface_ip, "192.168.50.1/24")
        self.assertTrue(status.uplink_interface_up)
        self.assertEqual(status.uplink_gateway, "192.168.1.1")
        self.assertTrue(status.hostapd_status.active)
        self.assertTrue(status.dnsmasq_status.active)
        self.assertTrue(status.ipv4_forwarding_enabled)
        self.assertTrue(status.nat_rule_active)
        self.assertEqual(len(status.warnings), 0)

        # Check formatted report text
        report_text = status.format_report()
        self.assertIn("Raspberry Pi Router Infrastructure Status", report_text)
        self.assertIn("Fully Operational: YES", report_text)
        self.assertIn("AP Interface (ap0): State=UP | IP=192.168.50.1/24", report_text)
        self.assertIn("hostapd", report_text)
        self.assertIn("dnsmasq", report_text)
        self.assertIn("IPv4 Forwarding (net.ipv4.ip_forward): ENABLED [PASS]", report_text)

    def test_router_status_service_inactive(self) -> None:
        """Verify is_operational is False when hostapd or dnsmasq is inactive."""
        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            cmd = " ".join(args)
            if "systemctl is-active hostapd" in cmd:
                return 3, "inactive\n", ""
            elif "systemctl is-active dnsmasq" in cmd:
                return 0, "active\n", ""
            elif "sysctl -n net.ipv4.ip_forward" in cmd:
                return 0, "1\n", ""
            elif "nft list table ip ina_nat" in cmd:
                return 0, "masquerade\n", ""
            return 0, "", ""

        status = inspect_router_status(runner=mock_runner)
        self.assertFalse(status.is_operational)
        self.assertFalse(status.hostapd_status.active)
        self.assertEqual(status.hostapd_status.state, "inactive")

    def test_router_status_forwarding_disabled(self) -> None:
        """Verify warning and non-operational state when IPv4 forwarding is 0."""
        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            cmd = " ".join(args)
            if "sysctl -n net.ipv4.ip_forward" in cmd:
                return 0, "0\n", ""
            elif "systemctl is-active" in cmd:
                return 0, "active\n", ""
            elif "nft list table ip ina_nat" in cmd:
                return 0, "masquerade\n", ""
            return 0, "", ""

        status = inspect_router_status(runner=mock_runner)
        self.assertFalse(status.is_operational)
        self.assertFalse(status.ipv4_forwarding_enabled)
        self.assertTrue(any("net.ipv4.ip_forward" in w for w in status.warnings))


class TestPreflightSafetyChecks(unittest.TestCase):
    """Unit tests for preflight safety checks on the router infrastructure."""

    def test_preflight_clean(self) -> None:
        """Verify preflight passes cleanly with separate AP and uplink interfaces."""
        mock_addr = json.dumps([
            {
                "ifname": "wlan0",
                "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"],
                "operstate": "UP",
                "link_type": "ether",
                "addr_info": [{"family": "inet", "local": "192.168.1.50", "prefixlen": 24}],
            },
            {
                "ifname": "ap0",
                "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"],
                "operstate": "UP",
                "link_type": "ether",
                "addr_info": [{"family": "inet", "local": "192.168.50.1", "prefixlen": 24}],
            },
        ])
        mock_routes = json.dumps([
            {"dst": "default", "gateway": "192.168.1.1", "dev": "wlan0", "metric": 100},
            {"dst": "192.168.1.0/24", "dev": "wlan0"},
        ])

        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            cmd = " ".join(args)
            if "ip -j addr" in cmd:
                return 0, mock_addr, ""
            elif "ip -j route" in cmd:
                return 0, mock_routes, ""
            elif "iw dev" in cmd:
                return 0, "phy#0\n  Interface wlan0\n    type managed\n  Interface ap0\n    type AP\n", ""
            elif "iw list" in cmd:
                return 0, "Wiphy phy0\n  Supported interface modes:\n    * managed\n    * AP\n", ""
            return 0, "", ""

        report = preflight_safety_check(config=APConfig(), runner=mock_runner)
        self.assertTrue(report.can_safely_operate)
        self.assertEqual(report.ap_capability_status, "supported")
        self.assertEqual(report.uplink_status, "clear")
        self.assertEqual(report.subnet_status, "clear")
        self.assertEqual(len(report.conflicts), 0)

    def test_preflight_uplink_conflict_detected(self) -> None:
        """Verify preflight detects when target AP interface carries host default route."""
        mock_routes = json.dumps([
            {"dst": "default", "gateway": "192.168.1.1", "dev": "wlan0", "metric": 100}
        ])

        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            cmd = " ".join(args)
            if "ip -j route" in cmd:
                return 0, mock_routes, ""
            elif "iw list" in cmd:
                return 0, "Supported interface modes:\n  * AP\n", ""
            return 0, "", ""

        # Point AP interface directly at wlan0 (the uplink interface)
        bad_config = APConfig(interface="wlan0", uplink_interface="wlan1")
        report = preflight_safety_check(config=bad_config, runner=mock_runner)
        self.assertFalse(report.can_safely_operate)
        self.assertEqual(report.uplink_status, "conflict")
        self.assertTrue(any("carries default route" in c for c in report.conflicts))

    def test_preflight_subnet_route_collision(self) -> None:
        """Verify preflight detects collision between AP subnet and existing routes."""
        mock_routes = json.dumps([
            {"dst": "default", "gateway": "192.168.1.1", "dev": "wlan0"},
            {"dst": "192.168.50.0/24", "dev": "eth0"},
        ])

        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            cmd = " ".join(args)
            if "ip -j route" in cmd:
                return 0, mock_routes, ""
            elif "iw list" in cmd:
                return 0, "Supported interface modes:\n  * AP\n", ""
            return 0, "", ""

        config = APConfig(subnet="192.168.50.0/24")
        report = preflight_safety_check(config=config, runner=mock_runner)
        self.assertFalse(report.can_safely_operate)
        self.assertEqual(report.subnet_status, "collision")
        self.assertTrue(any("Subnet collision" in c for c in report.conflicts))


class TestSettingsLoader(unittest.TestCase):
    """Unit tests for configuration file loading."""

    def test_load_network_settings(self) -> None:
        """Verify load_network_settings loads YAML properly or returns empty dict if missing."""
        sample_yaml = "network:\n  ssid: 'INA-Router'\n  router_ip: '192.168.50.1'\n"
        with patch("os.path.exists", return_value=True), patch(
            "builtins.open", unittest.mock.mock_open(read_data=sample_yaml)
        ):
            settings = load_network_settings("/fake/path.yaml")
            self.assertEqual(settings["network"]["ssid"], "INA-Router")
            self.assertEqual(settings["network"]["router_ip"], "192.168.50.1")

        with patch("os.path.exists", return_value=False):
            self.assertEqual(load_network_settings("/nonexistent.yaml"), {})


if __name__ == "__main__":
    unittest.main()
