"""Unit tests for ServiceTransport integrating the assistant client with NetworkService."""

import os
import shutil
import tempfile
import unittest

from inav2.assistant.contract import ToolCall, ToolName
from inav2.assistant.transport import ServiceTransport
from inav2.network.service import NetworkService
from inav2.tests.unit.test_mcp_server import MockCombinedRunner


class TestServiceTransport(unittest.TestCase):
    """Test suite for assistant ServiceTransport with real NetworkService logic."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()
        self.lock_path = os.path.join(self.test_dir, "test.lock")
        self.state_path = os.path.join(self.test_dir, "state.json")
        self.audit_path = os.path.join(self.test_dir, "audit.log")

        self.runner = MockCombinedRunner()
        self.leases_map = {
            "laptop-a": "192.168.50.20",
            "phone-b": "192.168.50.21",
        }

        self.service = NetworkService(
            runner=self.runner,
            lock_path=self.lock_path,
            state_path=self.state_path,
            audit_path=self.audit_path,
            leases_map=self.leases_map,
        )
        self.transport = ServiceTransport(service=self.service)

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_block_and_unblock_via_transport(self) -> None:
        """Assistant ToolCall for block and unblock executes through service."""
        # Block
        call = ToolCall(
            tool=ToolName.BLOCK_CLIENT.value,
            arguments={"device": "laptop-a", "target": "phone-b"},
            token="operator-token",
        )
        res = self.transport.call(call)
        self.assertEqual(res.status, "success")
        self.assertTrue(res.verification.passed)

        # Unblock
        unblock_call = ToolCall(
            tool=ToolName.UNBLOCK_CLIENT.value,
            arguments={"device": "laptop-a", "target": "phone-b"},
            token="operator-token",
        )
        unblock_res = self.transport.call(unblock_call)
        self.assertEqual(unblock_res.status, "success")
        self.assertTrue(unblock_res.verification.passed)

    def test_limit_and_remove_via_transport(self) -> None:
        """Assistant ToolCall for rate limiting executes through service."""
        call = ToolCall(
            tool=ToolName.LIMIT_BANDWIDTH.value,
            arguments={"device": "laptop-a", "download_kbit": 5000, "upload_kbit": 2000},
            token="operator-token",
        )
        res = self.transport.call(call)
        self.assertEqual(res.status, "success")
        self.assertTrue(res.verification.passed)

        # Remove limit
        del_call = ToolCall(
            tool=ToolName.REMOVE_BANDWIDTH_LIMIT.value,
            arguments={"device": "laptop-a"},
            token="operator-token",
        )
        del_res = self.transport.call(del_call)
        self.assertEqual(del_res.status, "success")

    def test_restore_via_transport(self) -> None:
        """Restore removes both blocks and bandwidth limits."""
        call = ToolCall(
            tool=ToolName.RESTORE_DEVICE.value,
            arguments={"device": "laptop-a"},
            token="operator-token",
        )
        res = self.transport.call(call)
        self.assertEqual(res.status, "success")

    def test_protected_infrastructure_via_transport(self) -> None:
        """Attempting to block protected infrastructure returns denied."""
        call = ToolCall(
            tool=ToolName.BLOCK_CLIENT.value,
            arguments={"device": "192.168.50.1", "target": "internet"},
            token="operator-token",
        )
        res = self.transport.call(call)
        self.assertEqual(res.status, "denied")
        self.assertFalse(res.verification.passed)

    def test_status_via_transport(self) -> None:
        """Querying status via transport returns device list."""
        call = ToolCall(
            tool=ToolName.GET_STATUS.value,
            token="viewer-token",
        )
        res = self.transport.call(call)
        self.assertEqual(res.status, "success")
        self.assertEqual(len(res.data.get("devices", [])), 2)


if __name__ == "__main__":
    unittest.main()

