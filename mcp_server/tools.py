import subprocess


# Client IPs in the Docker network
CLIENT_IPS = {
    "client1": "172.20.0.2",
    "client2": "172.20.0.4"
}
SERVER_IP = "172.20.0.3"


# --------------------------------------------------
# Helper: initialize nftables bridge filtering
# --------------------------------------------------

def _nft(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", "network-controller", "nft", *args],
        capture_output=True,
        text=True,
        check=False,
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

    except FileNotFoundError:
        return False, "Docker command not found."

    except Exception as error:
        return False, str(error)


# --------------------------------------------------
# Block client
# --------------------------------------------------

def block_client(client: str) -> dict:
    try:
        if client not in CLIENT_IPS:
            return {
                "status": "failure",
                "action": "block_client",
                "client": client,
                "message": f"Client {client} not found in the network."
            }

        client_ip = CLIENT_IPS[client]

        # Make sure the nftables bridge filter exists.
        initialized, message = _initialize_bridge_filter()

        if not initialized:
            return {
                "status": "failure",
                "action": "block_client",
                "client": client,
                "message": f"Failed to initialize bridge filter: {message}"
            }

        # Add the client's IP to the blocked set.
        command = [
            "docker", "exec", "network-controller",
            "nft", "add", "element",
            "bridge", "network_filter", "blocked_clients",
            "{", client_ip, "}"
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode == 0:
            return {
                "status": "success",
                "action": "block_client",
                "client": client,
                "message": f"Client {client} blocked successfully at bridge level."
            }

        # If the IP is already present, treat it as success.
        if "File exists" in result.stderr:
            return {
                "status": "success",
                "action": "block_client",
                "client": client,
                "message": f"Client {client} is already blocked."
            }

        return {
            "status": "failure",
            "action": "block_client",
            "client": client,
            "message": result.stderr.strip()
        }

    except FileNotFoundError:
        return {
            "status": "failure",
            "action": "block_client",
            "client": client,
            "message": "Docker command not found. Please ensure Docker is running and available in PATH."
        }

    except Exception as error:
        return {
            "status": "failure",
            "action": "block_client",
            "client": client,
            "message": str(error)
        }


# --------------------------------------------------
# Unblock client
# --------------------------------------------------

def unblock_client(client: str) -> dict:
    try:
        if client not in CLIENT_IPS:
            return {
                "status": "failure",
                "action": "unblock_client",
                "client": client,
                "message": f"Client {client} not found in the network."
            }

        client_ip = CLIENT_IPS[client]

        # Remove the client's IP from the blocked set.
        command = [
            "docker", "exec", "network-controller",
            "nft", "delete", "element",
            "bridge", "network_filter", "blocked_clients",
            "{", client_ip, "}"
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode == 0:
            return {
                "status": "success",
                "action": "unblock_client",
                "client": client,
                "message": f"Client {client} unblocked successfully."
            }

        # If it was already absent, the desired state is already achieved.
        err = result.stderr or ""
        if "No such file" in err or "No such element" in err or "does not exist" in err:
            return {
                "status": "success",
                "action": "unblock_client",
                "client": client,
                "message": f"Client {client} is already unblocked."
            }

        return {
            "status": "failure",
            "action": "unblock_client",
            "client": client,
            "message": result.stderr.strip()
        }

    except FileNotFoundError:
        return {
            "status": "failure",
            "action": "unblock_client",
            "client": client,
            "message": "Docker command not found. Please ensure Docker is running and available in PATH."
        }

    except Exception as error:
        return {
            "status": "failure",
            "action": "unblock_client",
            "client": client,
            "message": str(error)
        }


# --------------------------------------------------
# Limit bandwidth
# --------------------------------------------------

def limit_bandwidth(client: str, rate: str) -> dict:
    if client not in CLIENT_IPS:
        return {
            "status": "failure",
            "action": "limit_bandwidth",
            "client": client,
            "rate": rate,
            "message": f"Unknown client: {client}."
        }

    try:
        command = [
            "docker", "exec", client,
            "tc", "qdisc", "replace", "dev", "eth0",
            "root", "tbf",
            "rate", rate,
            "burst", "32kbit",
            "latency", "400ms"
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode == 0:
            return {
                "status": "success",
                "action": "limit_bandwidth",
                "client": client,
                "rate": rate,
                "message": f"Bandwidth limited to {rate} successfully."
            }

        return {
            "status": "failure",
            "action": "limit_bandwidth",
            "client": client,
            "rate": rate,
            "message": result.stderr.strip()
        }

    except FileNotFoundError:
        return {
            "status": "failure",
            "action": "limit_bandwidth",
            "client": client,
            "rate": rate,
            "message": "Docker or tc command not found."
        }

    except Exception as error:
        return {
            "status": "failure",
            "action": "limit_bandwidth",
            "client": client,
            "rate": rate,
            "message": str(error)
        }


# --------------------------------------------------
# Client status
# --------------------------------------------------

def get_status(client: str) -> dict:
    known = {**CLIENT_IPS, "server": SERVER_IP}
    if client not in known:
        return {
            "status": "failure",
            "action": "get_status",
            "client": client,
            "message": f"Client {client} not found in the network."
        }

    client_ip = known[client]
    source = "client1" if client == "server" else "server"

    try:
        result = subprocess.run(
            ["docker", "exec", source, "ping", "-c", "1", "-W", "1", client_ip],
            capture_output=True,
            text=True,
            check=False,
        )
        reachable = result.returncode == 0
        return {
            "status": "success",
            "action": "get_status",
            "client": client,
            "message": (
                f"{client} ({client_ip}) is reachable."
                if reachable
                else f"{client} ({client_ip}) is unreachable."
            ),
            "reachable": reachable,
        }
    except FileNotFoundError:
        return {
            "status": "failure",
            "action": "get_status",
            "client": client,
            "message": "Docker command not found. Please ensure Docker is running and available in PATH."
        }
    except Exception as error:
        return {
            "status": "failure",
            "action": "get_status",
            "client": client,
            "message": str(error)
        }