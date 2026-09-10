import re
import ipaddress
import subprocess
from typing import Dict, Any, Optional, Set, Tuple
from network.discovery import get_container_ip

try:
    from policy.policy_engine import check_policy
except ImportError:
    check_policy = None

try:
    from policy.audit_log import log_action as _audit_log_action
except ImportError:
    _audit_log_action = None

DEFAULT_TIMEOUT = 10  # Seconds to prevent hanging subprocess calls

_CLIENT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
_DISALLOWED_TARGETS = {"network-controller", "host", "root", "docker", "bridge"}
_RATE_RE = re.compile(r"^\d+(\.\d+)?\s*(kbit|mbit|gbit|kbps|mbps|mb/s|gbps)$", re.IGNORECASE)


# --------------------------------------------------
# Security Helpers & Validators
# --------------------------------------------------

def _log_audit(action: str, params: dict, status: str, reason: str = "") -> None:
    """Safely append entry to audit log if module is available."""
    if _audit_log_action is not None:
        try:
            _audit_log_action(action, params, status, reason)
        except Exception:
            pass


def _validate_client(client: str) -> str:
    """Validate client container name to prevent command injection or targeting arbitrary containers."""
    if not isinstance(client, str):
        raise ValueError("Client identifier must be a valid string.")
    cleaned = client.strip()
    if not cleaned or not _CLIENT_NAME_RE.match(cleaned) or cleaned.startswith("-"):
        raise ValueError(f"Invalid client identifier: '{client}'.")
    if cleaned.lower() in _DISALLOWED_TARGETS:
        raise ValueError(f"Targeting '{cleaned}' is not permitted.")
    return cleaned


def _validate_ipv4(ip: str) -> str:
    """Validate that the string is a genuine IPv4 address."""
    if not isinstance(ip, str):
        raise ValueError("IP address must be a string.")
    cleaned = ip.strip()
    try:
        ipaddress.IPv4Address(cleaned)
        return cleaned
    except ValueError:
        raise ValueError(f"Invalid IPv4 address format: '{ip}'.")


def _validate_rate(rate: str) -> Tuple[bool, Optional[str]]:
    """
    Validate and sanitize rate parameter for tc traffic shaper.
    Returns (is_removal, normalized_rate_str).
    """
    if not isinstance(rate, str):
        return False, None
    cleaned = rate.strip().lower()
    if cleaned in ("0", "0mbit", "none", "off", "del", "delete", "remove"):
        return True, "none"
    if not _RATE_RE.match(cleaned):
        return False, None
    for u in ("mbps", "mb/s"):
        cleaned = cleaned.replace(u, "mbit")
    for u in ("kbps", "kb/s"):
        cleaned = cleaned.replace(u, "kbit")
    for u in ("gbps", "gb/s"):
        cleaned = cleaned.replace(u, "gbit")
    return False, cleaned


# --------------------------------------------------
# Helper: initialize nftables bridge filtering
# --------------------------------------------------

def _nft(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", "network-controller", "nft", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=DEFAULT_TIMEOUT,
    )


def _nft_ok(result: subprocess.CompletedProcess) -> bool:
    if result.returncode == 0:
        return True
    err = (result.stderr or "").lower()
    return "file exists" in err or "already exists" in err


def _initialize_bridge_filter() -> tuple[bool, str]:
    """
    Create the nftables bridge table, chain, and blocked-client set
    if they do not already exist.

    The rules are applied directly to bridged traffic rather than
    relying on the normal Layer-3 iptables forwarding path.
    """
    try:
        steps = [
            ("add", "table", "bridge", "network_filter"),
            (
                "add", "chain", "bridge", "network_filter", "forward",
                "{ type filter hook forward priority 0; policy accept; }",
            ),
            (
                "add", "set", "bridge", "network_filter", "blocked_clients",
                "{ type ipv4_addr; }",
            ),
        ]
        for args in steps:
            result = _nft(*args)
            if not _nft_ok(result):
                return False, result.stderr.strip()

        listed = _nft("list", "chain", "bridge", "network_filter", "forward")
        chain_text = listed.stdout if listed.returncode == 0 else ""

        if "saddr @blocked_clients" not in chain_text:
            result = _nft(
                "add", "rule", "bridge", "network_filter", "forward",
                "ip", "saddr", "@blocked_clients", "drop",
            )
            if not _nft_ok(result):
                return False, result.stderr.strip()

        if "daddr @blocked_clients" not in chain_text:
            result = _nft(
                "add", "rule", "bridge", "network_filter", "forward",
                "ip", "daddr", "@blocked_clients", "drop",
            )
            if not _nft_ok(result):
                return False, result.stderr.strip()

        return True, "Bridge filter initialized successfully."

    except subprocess.TimeoutExpired:
        return False, "Timed out initializing bridge filter."

    except FileNotFoundError:
        return False, "Docker command not found."

    except Exception:
        return False, "Failed to initialize bridge filter."


# --------------------------------------------------
# Block client
# --------------------------------------------------

def block_client(client: str) -> dict:
    # 1. Strict validation of client identifier
    try:
        client = _validate_client(client)
    except ValueError as err:
        return {
            "status": "failure",
            "action": "block_client",
            "client": str(client),
            "message": str(err),
        }

    # 2. Enforce Policy Engine approval
    if check_policy is not None:
        decision = check_policy("block_client", {"client": client})
        if not decision.allowed:
            _log_audit("block_client", {"client": client}, "DENIED", decision.reason)
            return {
                "status": "failure",
                "action": "block_client",
                "client": client,
                "message": f"Policy denied: {decision.reason}",
            }

    try:
        client_ip = get_container_ip(client)
        _validate_ipv4(client_ip)

        # Make sure the nftables bridge filter exists.
        initialized, message = _initialize_bridge_filter()
        if not initialized:
            _log_audit("block_client", {"client": client}, "FAILED", f"Bridge filter init failed: {message}")
            return {
                "status": "failure",
                "action": "block_client",
                "client": client,
                "message": f"Failed to initialize bridge filter: {message}",
            }

        # Add the client's IP to the blocked set.
        command = [
            "docker", "exec", "network-controller",
            "nft", "add", "element",
            "bridge", "network_filter", "blocked_clients",
            "{", client_ip, "}",
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=DEFAULT_TIMEOUT,
        )

        if result.returncode == 0:
            msg = f"Client {client} blocked successfully at bridge level."
            _log_audit("block_client", {"client": client}, "APPLIED", msg)
            return {
                "status": "success",
                "action": "block_client",
                "client": client,
                "message": msg,
            }

        # If the IP is already present, treat it as success.
        if "File exists" in result.stderr:
            msg = f"Client {client} is already blocked."
            _log_audit("block_client", {"client": client}, "APPLIED", msg)
            return {
                "status": "success",
                "action": "block_client",
                "client": client,
                "message": msg,
            }

        err_msg = result.stderr.strip() or "nftables command failed."
        _log_audit("block_client", {"client": client}, "FAILED", err_msg)
        return {
            "status": "failure",
            "action": "block_client",
            "client": client,
            "message": err_msg,
        }

    except subprocess.TimeoutExpired:
        msg = "Operation timed out while communicating with network-controller."
        _log_audit("block_client", {"client": client}, "FAILED", msg)
        return {
            "status": "failure",
            "action": "block_client",
            "client": client,
            "message": msg,
        }

    except (ValueError, RuntimeError) as error:
        _log_audit("block_client", {"client": client}, "FAILED", str(error))
        return {
            "status": "failure",
            "action": "block_client",
            "client": client,
            "message": str(error),
        }

    except FileNotFoundError:
        msg = "Docker command not found. Please ensure Docker is running and available in PATH."
        _log_audit("block_client", {"client": client}, "FAILED", msg)
        return {
            "status": "failure",
            "action": "block_client",
            "client": client,
            "message": msg,
        }

    except Exception:
        msg = f"Failed to execute block for client '{client}'."
        _log_audit("block_client", {"client": client}, "FAILED", msg)
        return {
            "status": "failure",
            "action": "block_client",
            "client": client,
            "message": msg,
        }


# --------------------------------------------------
# Unblock client
# --------------------------------------------------

def unblock_client(client: str) -> dict:
    # 1. Strict validation of client identifier
    try:
        client = _validate_client(client)
    except ValueError as err:
        return {
            "status": "failure",
            "action": "unblock_client",
            "client": str(client),
            "message": str(err),
        }

    # 2. Enforce Policy Engine approval
    if check_policy is not None:
        decision = check_policy("unblock_client", {"client": client})
        if not decision.allowed:
            _log_audit("unblock_client", {"client": client}, "DENIED", decision.reason)
            return {
                "status": "failure",
                "action": "unblock_client",
                "client": client,
                "message": f"Policy denied: {decision.reason}",
            }

    try:
        client_ip = get_container_ip(client)
        _validate_ipv4(client_ip)

        # Remove previous tc qdiscs, filters, and IFB devices if any
        _cleanup_tc_qdiscs(client)

        # Remove the client's IP from the blocked set.
        command = [
            "docker", "exec", "network-controller",
            "nft", "delete", "element",
            "bridge", "network_filter", "blocked_clients",
            "{", client_ip, "}",
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=DEFAULT_TIMEOUT,
        )

        if result.returncode == 0:
            msg = f"Client {client} unblocked successfully."
            _log_audit("unblock_client", {"client": client}, "APPLIED", msg)
            return {
                "status": "success",
                "action": "unblock_client",
                "client": client,
                "message": msg,
            }

        # If it was already absent, the desired state is already achieved.
        err = result.stderr or ""
        if "No such file" in err or "No such element" in err or "does not exist" in err:
            msg = f"Client {client} is already unblocked."
            _log_audit("unblock_client", {"client": client}, "APPLIED", msg)
            return {
                "status": "success",
                "action": "unblock_client",
                "client": client,
                "message": msg,
            }

        err_msg = result.stderr.strip() or "iptables rule could not be removed."
        _log_audit("unblock_client", {"client": client}, "FAILED", err_msg)
        return {
            "status": "failure",
            "action": "unblock_client",
            "client": client,
            "message": err_msg,
        }

    except subprocess.TimeoutExpired:
        msg = "Operation timed out while communicating with network-controller."
        _log_audit("unblock_client", {"client": client}, "FAILED", msg)
        return {
            "status": "failure",
            "action": "unblock_client",
            "client": client,
            "message": msg,
        }

    except (ValueError, RuntimeError) as error:
        _log_audit("unblock_client", {"client": client}, "FAILED", str(error))
        return {
            "status": "failure",
            "action": "unblock_client",
            "client": client,
            "message": str(error),
        }

    except FileNotFoundError:
        msg = "Docker command not found. Please ensure Docker is running and available in PATH."
        _log_audit("unblock_client", {"client": client}, "FAILED", msg)
        return {
            "status": "failure",
            "action": "unblock_client",
            "client": client,
            "message": msg,
        }

    except Exception:
        msg = f"Failed to execute unblock for client '{client}'."
        _log_audit("unblock_client", {"client": client}, "FAILED", msg)
        return {
            "status": "failure",
            "action": "unblock_client",
            "client": client,
            "message": msg,
        }


def _cleanup_tc_qdiscs(client: str) -> None:
    """
    Remove previous tc qdiscs, filters, and IFB devices cleanly from container.
    """
    try:
        client = _validate_client(client)
    except ValueError:
        return

    commands = [
        ["docker", "exec", client, "tc", "qdisc", "del", "dev", "eth0", "root"],
        ["docker", "exec", client, "tc", "qdisc", "del", "dev", "eth0", "ingress"],
        ["docker", "exec", client, "tc", "qdisc", "del", "dev", "ifb0", "root"],
        ["docker", "exec", client, "ip", "link", "set", "dev", "ifb0", "down"],
        ["docker", "exec", client, "ip", "link", "delete", "dev", "ifb0"],
    ]
    for cmd in commands:
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=DEFAULT_TIMEOUT)
        except Exception:
            pass


# --------------------------------------------------
# Limit bandwidth
# --------------------------------------------------

def limit_bandwidth(client: str, rate: str) -> dict:
    # 1. Strict validation of client identifier
    try:
        client = _validate_client(client)
    except ValueError as err:
        return {
            "status": "failure",
            "action": "limit_bandwidth",
            "client": str(client),
            "rate": str(rate),
            "message": str(err),
        }

    # 2. Validate rate format
    is_removal, clean_rate = _validate_rate(rate)
    if not is_removal and clean_rate is None:
        return {
            "status": "failure",
            "action": "limit_bandwidth",
            "client": client,
            "rate": str(rate),
            "message": f"Invalid bandwidth rate format: '{rate}'. Expected numeric value with unit (e.g. '10mbit', '5mbps').",
        }

    # 3. Enforce Policy Engine approval
    if check_policy is not None:
        decision = check_policy("limit_bandwidth", {"client": client, "rate": rate})
        if not decision.allowed:
            _log_audit("limit_bandwidth", {"client": client, "rate": rate}, "DENIED", decision.reason)
            return {
                "status": "failure",
                "action": "limit_bandwidth",
                "client": client,
                "rate": rate,
                "message": f"Policy denied: {decision.reason}",
            }

    try:
        # Dynamically verify container existence & IP
        _ = get_container_ip(client)

        # Clean up any existing qdiscs / IFB devices first (prevents conflicting/duplicate rules)
        _cleanup_tc_qdiscs(client)

        if is_removal:
            msg = f"Bandwidth limit removed for {client} successfully."
            _log_audit("limit_bandwidth", {"client": client, "rate": rate}, "APPLIED", msg)
            return {
                "status": "success",
                "action": "limit_bandwidth",
                "client": client,
                "rate": rate,
                "message": msg,
            }

        # 2. Create and enable IFB device for Ingress Shaping
        subprocess.run(["docker", "exec", client, "ip", "link", "add", "name", "ifb0", "type", "ifb"], capture_output=True, text=True, check=False, timeout=DEFAULT_TIMEOUT)
        subprocess.run(["docker", "exec", client, "ip", "link", "set", "dev", "ifb0", "up"], capture_output=True, text=True, check=False, timeout=DEFAULT_TIMEOUT)

        # 3. Add ingress qdisc on eth0 and redirect ingress traffic to ifb0
        subprocess.run(["docker", "exec", client, "tc", "qdisc", "add", "dev", "eth0", "handle", "ffff:", "ingress"], capture_output=True, text=True, check=False, timeout=DEFAULT_TIMEOUT)
        subprocess.run(["docker", "exec", client, "tc", "filter", "add", "dev", "eth0", "parent", "ffff:", "protocol", "ip", "u32", "match", "u32", "0", "0", "action", "mirred", "egress", "redirect", "dev", "ifb0"], capture_output=True, text=True, check=False, timeout=DEFAULT_TIMEOUT)

        # 4. Apply TBF qdisc on ifb0 (Ingress Shaping)
        res_ing = subprocess.run(["docker", "exec", client, "tc", "qdisc", "replace", "dev", "ifb0", "root", "tbf", "rate", clean_rate, "burst", "32kbit", "latency", "400ms"], capture_output=True, text=True, check=False, timeout=DEFAULT_TIMEOUT)

        # 5. Apply TBF qdisc on eth0 (Egress Shaping)
        res_egr = subprocess.run(["docker", "exec", client, "tc", "qdisc", "replace", "dev", "eth0", "root", "tbf", "rate", clean_rate, "burst", "32kbit", "latency", "400ms"], capture_output=True, text=True, check=False, timeout=DEFAULT_TIMEOUT)

        if res_ing.returncode == 0 and res_egr.returncode == 0:
            msg = f"Bi-directional (ingress & egress) bandwidth limited to {clean_rate} successfully."
            _log_audit("limit_bandwidth", {"client": client, "rate": clean_rate}, "APPLIED", msg)
            return {
                "status": "success",
                "action": "limit_bandwidth",
                "client": client,
                "rate": clean_rate,
                "message": msg,
            }

        # Configuration failed: rollback cleanly to prevent conflicting/half-configured tc rules
        _cleanup_tc_qdiscs(client)
        err_msg = (res_ing.stderr or res_egr.stderr or "tc command failed").strip()
        _log_audit("limit_bandwidth", {"client": client, "rate": clean_rate}, "FAILED", err_msg)
        return {
            "status": "failure",
            "action": "limit_bandwidth",
            "client": client,
            "rate": rate,
            "message": err_msg,
        }

    except subprocess.TimeoutExpired:
        _cleanup_tc_qdiscs(client)
        msg = "Operation timed out while applying traffic shaping."
        _log_audit("limit_bandwidth", {"client": client, "rate": rate}, "FAILED", msg)
        return {
            "status": "failure",
            "action": "limit_bandwidth",
            "client": client,
            "rate": rate,
            "message": msg,
        }

    except (ValueError, RuntimeError) as error:
        _cleanup_tc_qdiscs(client)
        _log_audit("limit_bandwidth", {"client": client, "rate": rate}, "FAILED", str(error))
        return {
            "status": "failure",
            "action": "limit_bandwidth",
            "client": client,
            "rate": rate,
            "message": str(error),
        }

    except FileNotFoundError:
        msg = "Docker or tc command not found."
        _log_audit("limit_bandwidth", {"client": client, "rate": rate}, "FAILED", msg)
        return {
            "status": "failure",
            "action": "limit_bandwidth",
            "client": client,
            "rate": rate,
            "message": msg,
        }

    except Exception:
        _cleanup_tc_qdiscs(client)
        msg = f"Failed to limit bandwidth for client '{client}'."
        _log_audit("limit_bandwidth", {"client": client, "rate": rate}, "FAILED", msg)
        return {
            "status": "failure",
            "action": "limit_bandwidth",
            "client": client,
            "rate": rate,
            "message": msg,
        }


# --------------------------------------------------
# Client / Network status
# --------------------------------------------------

def _get_blocked_ips() -> set[str]:
    """Query live nftables blocked_clients set from network-controller."""
    try:
        res = subprocess.run(
            [
                "docker", "exec", "network-controller",
                "nft", "list", "set", "bridge", "network_filter", "blocked_clients",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=DEFAULT_TIMEOUT,
        )
        if res.returncode == 0 and res.stdout:
            found = set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", res.stdout))
            valid_ips = set()
            for ip in found:
                try:
                    ipaddress.IPv4Address(ip)
                    valid_ips.add(ip)
                except ValueError:
                    pass
            return valid_ips
    except Exception:
        pass
    return set()


def _get_tc_rate(container: str) -> str:
    """Inspect current bandwidth limit on container's eth0 via tc."""
    try:
        container = _validate_client(container)
        res = subprocess.run(
            ["docker", "exec", container, "tc", "qdisc", "show", "dev", "eth0"],
            capture_output=True,
            text=True,
            check=False,
            timeout=DEFAULT_TIMEOUT,
        )
        if res.returncode == 0 and res.stdout:
            m = re.search(r"\brate\s+([0-9]+(?:\.[0-9]+)?[a-zA-Z/]+)", res.stdout)
            if m:
                return m.group(1)
    except Exception:
        pass
    return "none"


def _ping_container(target_ip: str, source_container: str = "server") -> bool:
    """Check ping reachability from source_container to target_ip."""
    try:
        source_container = _validate_client(source_container)
        _validate_ipv4(target_ip)
        res = subprocess.run(
            ["docker", "exec", source_container, "ping", "-c", "1", "-W", "1", target_ip],
            capture_output=True,
            text=True,
            check=False,
            timeout=DEFAULT_TIMEOUT,
        )
        return res.returncode == 0
    except Exception:
        return False


def get_status(client: str = "") -> dict:
    """
    Report the current state of the managed Docker network or a specific client.

    Gathers live Docker network settings, container reachability, nftables
    firewall blocked status, and tc bandwidth limits.
    """
    try:
        from network.discovery import get_docker_client, list_known_clients

        target = (client or "").strip().lower()
        is_single = bool(target and target not in ("all", "network", "*", "status"))

        # If single client requested, validate client name
        if is_single:
            try:
                target = _validate_client(target)
            except ValueError as err:
                return {
                    "status": "failure",
                    "action": "get_status",
                    "client": str(client),
                    "message": str(err),
                }

        # Policy Engine authorization for status
        if check_policy is not None:
            decision = check_policy("get_status", {"client": target if is_single else ""})
            if not decision.allowed:
                return {
                    "status": "failure",
                    "action": "get_status",
                    "client": target or "all",
                    "message": f"Policy denied: {decision.reason}",
                }

        # 1. Discover live Docker network info
        net_name = "network_project-net"
        net_available = False
        subnet = "unknown"
        gateway = "unknown"

        try:
            docker_client = get_docker_client()
            net = next((n for n in docker_client.networks.list() if "project-net" in n.name), None)
            if net:
                net_name = net.name
                net_available = True
                ipam_configs = net.attrs.get("IPAM", {}).get("Config", [])
                if ipam_configs:
                    subnet = ipam_configs[0].get("Subnet", "unknown")
                    gateway = ipam_configs[0].get("Gateway", "unknown")
        except Exception:
            pass

        # 2. Query live nftables blocked clients set
        blocked_ips = _get_blocked_ips()

        # 3. Discover known containers and IPs
        try:
            known_map = list_known_clients("project-net")
        except Exception:
            known_map = {}

        if not known_map:
            for c in ("client1", "client2", "server"):
                try:
                    known_map[c] = get_container_ip(c)
                except Exception:
                    pass

        server_ip = known_map.get("server", "172.20.0.3")
        unblocked_clients = [c for c, ip in known_map.items() if c != "server" and ip not in blocked_ips]
        test_source = unblocked_clients[0] if unblocked_clients else ("client1" if "client1" in known_map else None)
        server_reachable = _ping_container(server_ip, source_container=test_source) if test_source else True

        # 4. Handle single-client query vs full-network query
        if is_single:
            if target not in known_map:
                try:
                    target_ip = get_container_ip(target)
                except Exception as err:
                    return {
                        "status": "failure",
                        "action": "get_status",
                        "client": target,
                        "message": f"Client '{target}' not found in Docker network: {err}",
                    }
            else:
                target_ip = known_map[target]

            if target == "server":
                reachable = server_reachable
            else:
                src = "server"
                reachable = _ping_container(target_ip, source_container=src)

            fw_status = "blocked" if target_ip in blocked_ips else ("protected" if target == "server" else "unblocked")
            bw_limit = _get_tc_rate(target)

            msg = (
                f"Client '{target}' ({target_ip}) is {'reachable' if reachable else 'unreachable'}. "
                f"Firewall: {fw_status}. Bandwidth limit: {bw_limit}. Network: {net_name}."
            )
            return {
                "status": "success",
                "action": "get_status",
                "client": target,
                "network": {
                    "name": net_name,
                    "available": net_available,
                    "subnet": subnet,
                    "gateway": gateway,
                },
                "server": {
                    "name": "server",
                    "ip": server_ip,
                    "status": "running" if "server" in known_map else "stopped",
                    "reachable": server_reachable,
                },
                "clients": {
                    target: {
                        "ip": target_ip,
                        "status": "running",
                        "reachable": reachable,
                        "firewall": fw_status,
                        "bandwidth_limit": bw_limit,
                    }
                },
                "message": msg,
            }

        # 5. Full network status
        clients_info = {}
        for c_name, c_ip in known_map.items():
            if c_name == "server":
                reachable = server_reachable
            else:
                reachable = _ping_container(c_ip, source_container="server")
            fw_status = "blocked" if c_ip in blocked_ips else ("protected" if c_name == "server" else "unblocked")
            bw_limit = _get_tc_rate(c_name)
            clients_info[c_name] = {
                "ip": c_ip,
                "status": "running",
                "reachable": reachable,
                "firewall": fw_status,
                "bandwidth_limit": bw_limit,
            }

        reachable_count = sum(1 for c in clients_info.values() if c["reachable"])
        total_count = len(clients_info)
        blocked_list = [c for c, d in clients_info.items() if d["firewall"] == "blocked"]
        limited_list = [f"{c} ({d['bandwidth_limit']})" for c, d in clients_info.items() if d["bandwidth_limit"] != "none"]

        msg_parts = [
            f"Network '{net_name}' ({subnet}, Gateway: {gateway}) active.",
            f"Server 'server' ({server_ip}): {'reachable' if server_reachable else 'unreachable'}.",
            f"Hosts: {reachable_count}/{total_count} reachable.",
        ]
        if blocked_list:
            msg_parts.append(f"Blocked: {', '.join(blocked_list)}.")
        else:
            msg_parts.append("Firewall: all unblocked.")

        if limited_list:
            msg_parts.append(f"Rate limited: {', '.join(limited_list)}.")
        else:
            msg_parts.append("Bandwidth limits: none.")

        return {
            "status": "success",
            "action": "get_status",
            "client": "all",
            "network": {
                "name": net_name,
                "available": net_available,
                "subnet": subnet,
                "gateway": gateway,
            },
            "server": {
                "name": "server",
                "ip": server_ip,
                "status": "running" if "server" in known_map else "stopped",
                "reachable": server_reachable,
            },
            "clients": clients_info,
            "message": " ".join(msg_parts),
        }

    except (ValueError, RuntimeError) as error:
        return {
            "status": "failure",
            "action": "get_status",
            "client": client,
            "message": str(error),
        }
    except FileNotFoundError:
        return {
            "status": "failure",
            "action": "get_status",
            "client": client,
            "message": "Docker command not found. Please ensure Docker is running and available in PATH.",
        }
    except Exception:
        return {
            "status": "failure",
            "action": "get_status",
            "client": client,
            "message": "An error occurred while retrieving network status.",
        }