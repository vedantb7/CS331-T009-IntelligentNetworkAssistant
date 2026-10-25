"""Unit tests for the safe access point management module.

Tests configuration validation, interface resolution precedence, profile inspection,
pre-flight safety checks, dry-run behaviors, protection of unrelated profiles,
activation guards, and safe cleanup.

All tests mock system commands and do not require root privileges or a real Wi-Fi adapter.
"""

from __future__ import annotations

import json
import unittest

from network.access_point import (
    APConfig,
    DEFAULT_AP_PROFILE_NAME,
    OperationResult,
    PreflightReport,
    ProfileStatus,
    activate_ap_profile,
    create_ap_profile,
    deactivate_ap_profile,
    inspect_profile,
    preflight_safety_check,
    remove_ap_profile,
    resolve_ap_interface,
    validate_ap_config,
)
from network.device_discovery import (
    InterfaceInfo,
    NetworkReport,
    RouteInfo,
    UplinkCandidate,
)


class TestAccessPointConfigValidation(unittest.TestCase):
    """Tests for validate_ap_config."""

    def test_valid_configuration(self) -> None:
        """Verify valid configurations pass with zero errors."""
        cfg = APConfig(
            ssid="Valid-SSID_123",
            password="StrongPassword123!",
            interface="wlan1",
            subnet="192.168.10.1/24",
            channel=6,
            band="bg",
            profile_name="ina-ap",
        )
        errors = validate_ap_config(cfg)
        self.assertEqual(errors, [])

    def test_valid_auto_resolve_interface_none(self) -> None:
        """Verify interface=None is permitted in config for auto-resolution."""
        cfg = APConfig(
            ssid="Valid-SSID",
            password="ValidPassword123!",
            interface=None,
        )
        self.assertEqual(validate_ap_config(cfg), [])

    def test_valid_64_hex_password(self) -> None:
        """Verify 64-character hexadecimal pre-shared key is valid."""
        hex_key = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        cfg = APConfig(
            ssid="Valid-SSID",
            password=hex_key,
            interface="wlan1",
            subnet="10.50.0.1/24",
            profile_name="ina-ap",
        )
        self.assertEqual(validate_ap_config(cfg), [])

    def test_invalid_ssid(self) -> None:
        """Verify invalid SSIDs are rejected (empty, >32 bytes, control chars)."""
        # Empty
        self.assertTrue(any("empty" in e for e in validate_ap_config(APConfig(ssid="", password="ValidPass123"))))
        # Whitespace only
        self.assertTrue(any("empty" in e for e in validate_ap_config(APConfig(ssid="   ", password="ValidPass123"))))
        # Exceeds 32 bytes
        long_ssid = "A" * 33
        self.assertTrue(any("32 bytes" in e for e in validate_ap_config(APConfig(ssid=long_ssid, password="ValidPass123"))))
        # Control character
        self.assertTrue(any("control characters" in e for e in validate_ap_config(APConfig(ssid="Bad\nSSID", password="ValidPass123"))))

    def test_invalid_password(self) -> None:
        """Verify passwords <8 chars, >63 chars non-hex, or with control chars fail."""
        # Too short (<8)
        self.assertTrue(any("between 8 and 63" in e for e in validate_ap_config(APConfig(password="short"))))
        # Too long (>63 non-hex)
        self.assertTrue(any("between 8 and 63" in e for e in validate_ap_config(APConfig(password="x" * 64))))
        # Control char
        self.assertTrue(any("control characters" in e for e in validate_ap_config(APConfig(password="Pass\t12345"))))

    def test_profile_name_namespace_isolation(self) -> None:
        """Verify profile names must start with 'ina-' to isolate them from host profiles."""
        self.assertTrue(any("must start with 'ina-'" in e for e in validate_ap_config(APConfig(profile_name="IITGN-SSO", password="ValidPass123"))))
        self.assertTrue(any("must start with 'ina-'" in e for e in validate_ap_config(APConfig(profile_name="my-wifi", password="ValidPass123"))))
        # Special characters in profile name
        self.assertTrue(any("invalid characters" in e for e in validate_ap_config(APConfig(profile_name="ina-ap;rm", password="ValidPass123"))))

    def test_invalid_subnets(self) -> None:
        """Verify invalid CIDRs, public IPs, and non-IPv4 subnets fail."""
        # Non-CIDR string
        self.assertTrue(any("Invalid IPv4 subnet" in e for e in validate_ap_config(APConfig(subnet="not-an-ip", password="ValidPass123"))))
        # Public IP range
        self.assertTrue(any("not a private IPv4" in e for e in validate_ap_config(APConfig(subnet="8.8.8.8/24", password="ValidPass123"))))
        # IPv6
        self.assertTrue(any("IPv6" in e for e in validate_ap_config(APConfig(subnet="2001:db8::1/64", password="ValidPass123"))))
        # Out of recommended prefix range (/32 or /8)
        self.assertTrue(any("prefix" in e for e in validate_ap_config(APConfig(subnet="192.168.10.1/32", password="ValidPass123"))))

    def test_invalid_interface_and_channels(self) -> None:
        """Verify invalid interface characters and invalid channel/band pairings fail."""
        self.assertTrue(any("invalid characters" in e for e in validate_ap_config(APConfig(interface="wlan 0", password="ValidPass123"))))
        self.assertTrue(any("invalid for 2.4GHz" in e for e in validate_ap_config(APConfig(band="bg", channel=36, password="ValidPass123"))))
        self.assertTrue(any("invalid for 5GHz" in e for e in validate_ap_config(APConfig(band="a", channel=6, password="ValidPass123"))))

    def test_password_masking_in_repr(self) -> None:
        """Verify string representation of APConfig masks the password."""
        cfg = APConfig(ssid="SecureNet", password="VerySecretPassword123!", interface="wlan1")
        repr_str = repr(cfg)
        self.assertNotIn("VerySecretPassword123!", repr_str)
        self.assertIn("password='***'", repr_str)
        self.assertIn("interface='wlan1'", repr_str)


class TestInterfaceResolution(unittest.TestCase):
    """Tests for resolve_ap_interface precedence and safety rules."""

    def test_explicit_interface_configuration(self) -> None:
        """Verify explicit argument takes highest precedence."""
        settings = {"network": {"interface": "settings_wlan"}}
        chosen, reason, _ = resolve_ap_interface(
            config_interface="explicit_wlan",
            settings=settings,
        )
        self.assertEqual(chosen, "explicit_wlan")
        self.assertIn("Explicitly specified", reason)

    def test_settings_interface_configuration(self) -> None:
        """Verify settings.yaml takes precedence when CLI argument is not provided."""
        settings = {"network": {"interface": "configured_wlan"}}
        chosen, reason, _ = resolve_ap_interface(
            config_interface=None,
            settings=settings,
        )
        self.assertEqual(chosen, "configured_wlan")
        self.assertIn("config/settings.yaml", reason)

    def test_unambiguous_discovery_based_selection(self) -> None:
        """Verify discovery selects single non-uplink wireless interface."""
        report = NetworkReport(
            timestamp="",
            platform_info="",
            interfaces=[
                InterfaceInfo(name="eth0", operstate="UP", is_wireless=False),
                InterfaceInfo(name="wlp8s0", operstate="UP", is_wireless=True, ap_mode_supported=True),
            ],
            default_routes=[
                RouteInfo(destination="default", interface="eth0", gateway="192.168.1.1", is_default=True),
            ],
            uplink_candidates=[
                UplinkCandidate(interface="eth0", gateway="192.168.1.1", metric=100, family="ipv4", operstate="UP", is_primary=True, notes=""),
            ],
        )

        chosen, reason, _ = resolve_ap_interface(
            config_interface=None,
            settings={},
            discovery_report=report,
        )
        self.assertEqual(chosen, "wlp8s0")
        self.assertIn("Unambiguously discovered", reason)

    def test_no_candidate_available_no_wireless(self) -> None:
        """Verify refusal when host has no wireless interfaces."""
        report = NetworkReport(
            timestamp="",
            platform_info="",
            interfaces=[
                InterfaceInfo(name="lo", operstate="UP", is_loopback=True),
                InterfaceInfo(name="eth0", operstate="UP", is_wireless=False),
            ],
        )
        chosen, reason, _ = resolve_ap_interface(
            config_interface=None,
            settings={},
            discovery_report=report,
        )
        self.assertIsNone(chosen)
        self.assertIn("No wireless interfaces discovered", reason)

    def test_no_candidate_available_only_uplink_wireless(self) -> None:
        """Verify refusal to automatically select wireless interface that is active uplink."""
        report = NetworkReport(
            timestamp="",
            platform_info="",
            interfaces=[
                InterfaceInfo(name="wlp8s0", operstate="UP", is_wireless=True, ap_mode_supported=True),
            ],
            default_routes=[
                RouteInfo(destination="default", interface="wlp8s0", gateway="192.168.1.1", is_default=True),
            ],
            uplink_candidates=[
                UplinkCandidate(interface="wlp8s0", gateway="192.168.1.1", metric=600, family="ipv4", operstate="UP", is_primary=True, notes=""),
            ],
        )
        chosen, reason, _ = resolve_ap_interface(
            config_interface=None,
            settings={},
            discovery_report=report,
        )
        self.assertIsNone(chosen)
        self.assertIn("currently active as default uplink", reason)
        self.assertIn("Not selected automatically", reason)

    def test_multiple_ambiguous_candidates(self) -> None:
        """Verify refusal when multiple non-uplink wireless interfaces are discovered."""
        report = NetworkReport(
            timestamp="",
            platform_info="",
            interfaces=[
                InterfaceInfo(name="eth0", operstate="UP", is_wireless=False),
                InterfaceInfo(name="wlan1", operstate="UP", is_wireless=True, ap_mode_supported=True),
                InterfaceInfo(name="wlan2", operstate="UP", is_wireless=True, ap_mode_supported=True),
            ],
            default_routes=[
                RouteInfo(destination="default", interface="eth0", gateway="192.168.1.1", is_default=True),
            ],
        )
        chosen, reason, _ = resolve_ap_interface(
            config_interface=None,
            settings={},
            discovery_report=report,
        )
        self.assertIsNone(chosen)
        self.assertIn("Multiple non-uplink wireless interface candidates", reason)
        self.assertIn("ambiguous", reason)


class TestAccessPointInspectionAndProtection(unittest.TestCase):
    """Tests for profile inspection and protection against touching non-INA profiles."""

    def test_inspect_existing_ina_profile(self) -> None:
        """Verify inspection of an existing INA-managed AP profile."""
        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args[:4] == ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE"]:
                return 0, "ina-ap:1111-2222-3333:802-11-wireless:wlan1", ""
            elif len(args) > 3 and "connection.id" in args[3]:
                output = (
                    "connection.id:ina-ap\n"
                    "connection.uuid:1111-2222-3333\n"
                    "connection.type:802-11-wireless\n"
                    "802-11-wireless.mode:ap\n"
                    "802-11-wireless.ssid:INA-Test-Network\n"
                    "ipv4.method:shared\n"
                    "ipv4.addresses:192.168.10.1/24\n"
                )
                return 0, output, ""
            elif args[:4] == ["nmcli", "-t", "-f", "NAME,UUID,DEVICE"]:
                return 0, "ina-ap:1111-2222-3333:wlan1", ""
            return 0, "", ""

        status = inspect_profile("ina-ap", runner=mock_runner)
        self.assertTrue(status.exists)
        self.assertTrue(status.is_ina_managed)
        self.assertTrue(status.is_active)
        self.assertEqual(status.uuid, "1111-2222-3333")
        self.assertEqual(status.mode, "ap")

    def test_inspect_unrelated_host_profile(self) -> None:
        """Verify non-INA profile is identified as NOT INA-managed."""
        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args[:4] == ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE"]:
                return 0, "IITGN-SSO:aaaa-bbbb-cccc:802-11-wireless:wlp8s0", ""
            elif len(args) > 3 and "connection.id" in args[3]:
                return 0, "connection.id:IITGN-SSO\n802-11-wireless.mode:infrastructure\n", ""
            elif args[:4] == ["nmcli", "-t", "-f", "NAME,UUID,DEVICE"]:
                return 0, "IITGN-SSO:aaaa-bbbb-cccc:wlp8s0", ""
            return 0, "", ""

        status = inspect_profile("IITGN-SSO", runner=mock_runner)
        self.assertTrue(status.exists)
        self.assertFalse(status.is_ina_managed)

    def test_refusal_to_delete_unrelated_profiles(self) -> None:
        """Verify remove_ap_profile strictly refuses to delete non-INA profiles."""
        # Attempt to delete IITGN-SSO
        res = remove_ap_profile("IITGN-SSO", dry_run=False)
        self.assertFalse(res.success)
        self.assertIn("PROTECTION VIOLATION", res.message)

        # Attempt to delete CloudflareWARP
        res_warp = remove_ap_profile("CloudflareWARP", dry_run=False)
        self.assertFalse(res_warp.success)
        self.assertIn("PROTECTION VIOLATION", res_warp.message)

    def test_refusal_to_deactivate_unrelated_profiles(self) -> None:
        """Verify deactivate_ap_profile refuses to touch non-INA profiles."""
        res = deactivate_ap_profile("IITGN-SSO", dry_run=False)
        self.assertFalse(res.success)
        self.assertIn("PROTECTION VIOLATION", res.message)


class TestPreflightSafetyChecks(unittest.TestCase):
    """Tests for preflight_safety_check."""

    def test_missing_configured_interface(self) -> None:
        """Verify preflight flags interface_status as missing when interface is not on host."""
        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args == ["ip", "-j", "addr"]:
                return 0, json.dumps([{"ifname": "lo", "flags": ["LOOPBACK"], "operstate": "UP"}]), ""
            elif "ip" in args or "nmcli" in args:
                return 0, "[]", ""
            return 0, "", ""

        cfg = APConfig(interface="wlan99", password="ValidPass123!")
        report = preflight_safety_check(cfg, runner=mock_runner)

        self.assertFalse(report.can_safely_activate)
        self.assertEqual(report.interface_status, "missing")
        self.assertTrue(any("does not exist" in c for c in report.conflicts))

    def test_not_wireless_interface(self) -> None:
        """Verify preflight flags interface_status as not_wireless when ethernet interface is passed."""
        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args == ["ip", "-j", "addr"]:
                return 0, json.dumps([{"ifname": "eth0", "flags": ["BROADCAST"], "operstate": "UP", "link_type": "ether"}]), ""
            elif "ip" in args or "nmcli" in args:
                return 0, "[]", ""
            return 0, "", ""

        cfg = APConfig(interface="eth0", password="ValidPass123!")
        report = preflight_safety_check(cfg, runner=mock_runner)

        self.assertFalse(report.can_safely_activate)
        self.assertEqual(report.interface_status, "not_wireless")
        self.assertTrue(any("not a wireless interface" in c for c in report.conflicts))

    def test_unsupported_ap_mode(self) -> None:
        """Verify preflight flags ap_capability_status as unsupported when driver lacks AP mode."""
        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args == ["ip", "-j", "addr"]:
                return 0, json.dumps([{"ifname": "wlan1", "flags": ["UP"], "operstate": "UP", "link_type": "ether"}]), ""
            elif args == ["iw", "dev"]:
                return 0, "phy#1\n\tInterface wlan1\n", ""
            elif args == ["iw", "list"]:
                # AP mode missing from supported interface modes
                return 0, "Wiphy phy1\nSupported interface modes:\n\t * managed\n\t * monitor\n", ""
            elif "ip" in args or "nmcli" in args:
                return 0, "[]", ""
            return 0, "", ""

        cfg = APConfig(interface="wlan1", password="ValidPass123!")
        report = preflight_safety_check(cfg, runner=mock_runner)

        self.assertFalse(report.can_safely_activate)
        self.assertEqual(report.ap_capability_status, "unsupported")
        self.assertTrue(any("does not advertise AP mode" in c for c in report.conflicts))

    def test_active_uplink_conflict_detected(self) -> None:
        """Verify preflight detects when target interface is currently the active uplink."""
        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args == ["ip", "-j", "addr"]:
                return 0, json.dumps([{
                    "ifname": "wlp8s0",
                    "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"],
                    "operstate": "UP",
                    "link_type": "ether",
                    "addr_info": [{"family": "inet", "local": "192.168.1.50", "prefixlen": 24}],
                }]), ""
            elif args == ["ip", "-j", "-d", "link"]:
                return 0, "[]", ""
            elif args == ["iw", "dev"]:
                return 0, "phy#0\n\tInterface wlp8s0\n", ""
            elif args == ["iw", "list"]:
                return 0, "Wiphy phy0\nSupported interface modes:\n\t * managed\n\t * AP\n", ""
            elif args == ["ip", "-j", "route"]:
                return 0, json.dumps([
                    {"dst": "default", "gateway": "192.168.1.1", "dev": "wlp8s0", "metric": 600},
                    {"dst": "192.168.1.0/24", "dev": "wlp8s0"},
                ]), ""
            elif args == ["ip", "-j", "-6", "route"]:
                return 0, "[]", ""
            elif args[:4] == ["nmcli", "-t", "-f", "NAME,DEVICE,TYPE"]:
                return 0, "IITGN-SSO:wlp8s0:802-11-wireless\nCloudflareWARP:CloudflareWARP:wireguard", ""
            elif args[:4] == ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE"]:
                return 0, "", ""
            return 0, "", ""

        cfg = APConfig(interface="wlp8s0", password="ValidPass123!")
        report = preflight_safety_check(cfg, runner=mock_runner)

        self.assertFalse(report.can_safely_activate)
        self.assertTrue(report.has_active_uplink_conflict)
        self.assertEqual(report.uplink_status, "conflict")
        self.assertEqual(report.active_connection_on_interface, "IITGN-SSO")
        self.assertTrue(any("Active uplink conflict" in c for c in report.conflicts))
        self.assertTrue(any("secondary Wi-Fi adapter" in r for r in report.recommendations))

    def test_subnet_route_collision(self) -> None:
        """Verify preflight detects collision between AP subnet and existing host routes."""
        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args == ["ip", "-j", "addr"]:
                return 0, json.dumps([
                    {"ifname": "wlan1", "flags": ["UP"], "operstate": "UP", "link_type": "ether"},
                ]), ""
            elif args == ["ip", "-j", "-d", "link"]:
                return 0, "[]", ""
            elif args == ["iw", "dev"]:
                return 0, "phy#1\n\tInterface wlan1\n", ""
            elif args == ["iw", "list"]:
                return 0, "Wiphy phy1\nSupported interface modes:\n\t * AP\n", ""
            elif args == ["ip", "-j", "route"]:
                return 0, json.dumps([{"dst": "192.168.10.0/24", "dev": "eth0"}]), ""
            elif args == ["ip", "-j", "-6", "route"]:
                return 0, "[]", ""
            elif "nmcli" in args:
                return 0, "", ""
            return 0, "", ""

        cfg = APConfig(interface="wlan1", subnet="192.168.10.1/24", password="ValidPass123!")
        report = preflight_safety_check(cfg, runner=mock_runner)

        self.assertFalse(report.can_safely_activate)
        self.assertEqual(report.subnet_status, "collision")
        self.assertTrue(any("collides with existing route" in c for c in report.conflicts))

    def test_untested_conditions_reported(self) -> None:
        """Verify preflight lists conditions that have not been tested and avoids false readiness claims."""
        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args == ["ip", "-j", "addr"]:
                return 0, json.dumps([{"ifname": "wlan1", "flags": ["UP"], "operstate": "UP", "link_type": "ether"}]), ""
            elif args == ["iw", "dev"]:
                return 0, "phy#1\n\tInterface wlan1\n", ""
            elif args == ["iw", "list"]:
                return 0, "Wiphy phy1\nSupported interface modes:\n\t * AP\n", ""
            elif "ip" in args or "nmcli" in args:
                return 0, "[]", ""
            return 0, "", ""

        cfg = APConfig(interface="wlan1", password="ValidPass123!")
        report = preflight_safety_check(cfg, runner=mock_runner)

        self.assertTrue(len(report.untested_conditions) >= 2)
        self.assertTrue(any("DHCP" in u for u in report.untested_conditions))
        self.assertTrue(any("forwarding" in u for u in report.untested_conditions))


class TestAPProfileCreationAndActivation(unittest.TestCase):
    """Tests for create_ap_profile and activate_ap_profile."""

    def test_create_dry_run_default(self) -> None:
        """Verify create_ap_profile defaults to dry-run and masks password."""
        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args[:4] == ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE"]:
                return 0, "", ""
            return 0, "", ""

        cfg = APConfig(
            ssid="INA-Hotspot",
            password="SuperSecretPassword!",
            interface="wlan1",
            subnet="192.168.50.1/24",
        )
        res = create_ap_profile(cfg, dry_run=True, runner=mock_runner)

        self.assertTrue(res.success)
        self.assertTrue(res.dry_run)
        self.assertEqual(len(res.planned_commands), 2)
        modify_cmd = res.planned_commands[1]
        psk_idx = modify_cmd.index("wifi-sec.psk")
        self.assertEqual(modify_cmd[psk_idx + 1], "***")
        self.assertNotIn("SuperSecretPassword!", json.dumps(res.to_dict()))

    def test_create_profile_command_failure(self) -> None:
        """Verify handling of nmcli command failure during profile creation."""
        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args[:4] == ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE"]:
                return 0, "", ""
            if args[:3] == ["nmcli", "connection", "add"]:
                return 1, "", "Error: Failed to add connection: Device not ready"
            return 0, "", ""

        cfg = APConfig(interface="wlan1", password="ValidPass123!")
        res = create_ap_profile(cfg, dry_run=False, runner=mock_runner)
        self.assertFalse(res.success)
        self.assertIn("NetworkManager command failed", res.message)
        self.assertIn("Device not ready", res.message)

    def test_refusal_to_activate_without_confirmation(self) -> None:
        """Verify activation refuses to run without confirm=True."""
        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args == ["ip", "-j", "addr"]:
                return 0, json.dumps([{"ifname": "wlan1", "flags": ["UP"], "operstate": "UP", "link_type": "ether"}]), ""
            elif args == ["iw", "dev"]:
                return 0, "phy#1\n\tInterface wlan1\n", ""
            elif args == ["iw", "list"]:
                return 0, "Wiphy phy1\nSupported interface modes:\n\t * AP\n", ""
            elif "ip" in args:
                return 0, "[]", ""
            elif args[:4] == ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE"]:
                return 0, "ina-ap:1111:802-11-wireless:--", ""
            elif len(args) > 3 and "connection.id" in args[3]:
                return 0, "connection.id:ina-ap\n802-11-wireless.mode:ap\n", ""
            return 0, "", ""

        cfg = APConfig(interface="wlan1", password="ValidPass123!")
        res = activate_ap_profile(cfg, confirm=False, runner=mock_runner)

        self.assertFalse(res.success)
        self.assertIn("Explicit confirmation (confirm=True) is required", res.message)

    def test_refusal_to_activate_with_active_uplink(self) -> None:
        """Verify activation refuses when target interface is the active internet uplink."""
        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args == ["ip", "-j", "addr"]:
                return 0, json.dumps([{"ifname": "wlp8s0", "flags": ["UP"], "operstate": "UP", "link_type": "ether"}]), ""
            elif args == ["ip", "-j", "route"]:
                return 0, json.dumps([{"dst": "default", "gateway": "192.168.1.1", "dev": "wlp8s0"}]), ""
            elif args == ["iw", "dev"]:
                return 0, "phy#0\n\tInterface wlp8s0\n", ""
            elif args == ["iw", "list"]:
                return 0, "Wiphy phy0\nSupported interface modes:\n\t * AP\n", ""
            elif "ip" in args:
                return 0, "[]", ""
            elif args[:4] == ["nmcli", "-t", "-f", "NAME,DEVICE,TYPE"]:
                return 0, "IITGN-SSO:wlp8s0:802-11-wireless", ""
            elif args[:4] == ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE"]:
                return 0, "ina-ap:1111:802-11-wireless:--", ""
            elif len(args) > 3 and "connection.id" in args[3]:
                return 0, "connection.id:ina-ap\n802-11-wireless.mode:ap\n", ""
            return 0, "", ""

        cfg = APConfig(interface="wlp8s0", password="ValidPass123!")
        res = activate_ap_profile(cfg, confirm=True, runner=mock_runner)

        self.assertFalse(res.success)
        self.assertIn("Active uplink conflict detected", res.message)

    def test_activate_profile_command_timeout(self) -> None:
        """Verify handling of nmcli timeout during activation."""
        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args == ["ip", "-j", "addr"]:
                return 0, json.dumps([{"ifname": "wlan1", "flags": ["UP"], "operstate": "UP", "link_type": "ether"}]), ""
            elif args == ["iw", "dev"]:
                return 0, "phy#1\n\tInterface wlan1\n", ""
            elif args == ["iw", "list"]:
                return 0, "Wiphy phy1\nSupported interface modes:\n\t * AP\n", ""
            elif "ip" in args:
                return 0, "[]", ""
            elif args[:4] == ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE"]:
                return 0, "ina-ap:1111:802-11-wireless:--", ""
            elif len(args) > 3 and "connection.id" in args[3]:
                return 0, "connection.id:ina-ap\n802-11-wireless.mode:ap\n", ""
            elif args[:4] == ["nmcli", "-t", "-f", "NAME,DEVICE,TYPE"]:
                return 0, "", ""
            elif args == ["nmcli", "connection", "up", "ina-ap"]:
                return 124, "", "Command timed out after 15.0s"
            return 0, "", ""

        cfg = APConfig(interface="wlan1", password="ValidPass123!")
        res = activate_ap_profile(cfg, dry_run=False, confirm=True, runner=mock_runner)
        self.assertFalse(res.success)
        self.assertIn("Failed to activate", res.message)
        self.assertIn("timed out", res.message)

    def test_post_activation_verification_failure(self) -> None:
        """Verify failure when 'nmcli up' succeeds but NetworkManager reports inactive."""
        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args == ["ip", "-j", "addr"]:
                return 0, json.dumps([{"ifname": "wlan1", "flags": ["UP"], "operstate": "UP", "link_type": "ether"}]), ""
            elif args == ["iw", "dev"]:
                return 0, "phy#1\n\tInterface wlan1\n", ""
            elif args == ["iw", "list"]:
                return 0, "Wiphy phy1\nSupported interface modes:\n\t * AP\n", ""
            elif "ip" in args:
                return 0, "[]", ""
            elif args[:4] == ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE"]:
                return 0, "ina-ap:1111:802-11-wireless:--", ""
            elif len(args) > 3 and "connection.id" in args[3]:
                return 0, "connection.id:ina-ap\n802-11-wireless.mode:ap\n", ""
            elif args[:4] == ["nmcli", "-t", "-f", "NAME,DEVICE,TYPE"]:
                return 0, "", ""
            elif args == ["nmcli", "connection", "up", "ina-ap"]:
                return 0, "Connection successfully activated", ""
            elif args[:4] == ["nmcli", "-t", "-f", "NAME,UUID,DEVICE"]:
                # Still inactive in active list!
                return 0, "", ""
            return 0, "", ""

        cfg = APConfig(interface="wlan1", password="ValidPass123!")
        res = activate_ap_profile(cfg, dry_run=False, confirm=True, runner=mock_runner)

        self.assertFalse(res.success)
        self.assertIn("not reporting active state", res.message)

    def test_safe_cleanup_ina_profile(self) -> None:
        """Verify safe deletion of an INA-managed profile."""
        executed_commands: list[list[str]] = []

        def mock_runner(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            executed_commands.append(args)
            if args[:4] == ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE"]:
                return 0, "ina-ap:1111:802-11-wireless:wlan1", ""
            elif len(args) > 3 and "connection.id" in args[3]:
                return 0, "connection.id:ina-ap\n802-11-wireless.mode:ap\n", ""
            elif args[:4] == ["nmcli", "-t", "-f", "NAME,UUID,DEVICE"]:
                return 0, "ina-ap:1111:wlan1", ""
            elif args == ["nmcli", "connection", "down", "ina-ap"]:
                return 0, "", ""
            elif args == ["nmcli", "connection", "delete", "ina-ap"]:
                return 0, "", ""
            return 0, "", ""

        res = remove_ap_profile("ina-ap", dry_run=False, runner=mock_runner)
        self.assertTrue(res.success)
        self.assertIn("successfully removed", res.message)
        self.assertIn(["nmcli", "connection", "down", "ina-ap"], executed_commands)
        self.assertIn(["nmcli", "connection", "delete", "ina-ap"], executed_commands)


if __name__ == "__main__":
    unittest.main()
