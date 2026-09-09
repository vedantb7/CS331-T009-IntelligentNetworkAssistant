import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from network.discovery import get_container_ip, get_container_info, list_known_clients

def test_get_container_ip_valid_client1():
    ip = get_container_ip("client1")
    assert ip.startswith("172.20.0.")

def test_get_container_ip_valid_server():
    ip = get_container_ip("server")
    assert ip.startswith("172.20.0.")

def test_get_container_ip_invalid_client():
    with pytest.raises(ValueError, match="not found"):
        get_container_ip("non_existent_container_123")

def test_get_container_info():
    info = get_container_info("client1")
    assert info["name"] == "client1"
    assert info["status"] == "running"
    assert "networks" in info

def test_list_known_clients():
    clients = list_known_clients()
    assert "client1" in clients
    assert "client2" in clients
    assert "server" in clients


if __name__ == "__main__":
    print("==========================================")
    print(" Running Discovery Unit Tests")
    print("==========================================")
    tests = [
        test_get_container_ip_valid_client1,
        test_get_container_ip_valid_server,
        test_get_container_ip_invalid_client,
        test_get_container_info,
        test_list_known_clients,
    ]
    passed = 0
    failed = 0
    for test in tests:
        name = test.__name__
        try:
            test()
            print(f"[PASS] {name}")
            passed += 1
        except Exception as exc:
            print(f"[FAIL] {name}: {exc}")
            failed += 1
    print("==========================================")
    print(f" Test Summary: {passed} PASSED, {failed} FAILED")
    print("==========================================")
    if failed > 0:
        sys.exit(1)

