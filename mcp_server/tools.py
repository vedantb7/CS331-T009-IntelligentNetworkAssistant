import subprocess


# Client IPs in the Docker network
CLIENT_IPS = {
    "client1": "172.20.0.2",
    "client2": "172.20.0.4"
}


# --------------------------------------------------
# Helper: initialize nftables bridge filtering
# --------------------------------------------------

def _initialize_bridge_filter() -> tuple[bool, str]:
    """
    Create the nftables bridge table, chain, and blocked-client set
    if they do not already exist.

    The rules are applied directly to bridged traffic rather than
    relying on the normal Layer-3 iptables forwarding path.
    """

    try:
        # Check whether our table already exists.
        check_table = subprocess.run(
            [
                "docker", "exec", "network-controller",
                "nft", "list", "table", "bridge", "network_filter"
            ],
            capture_output=True,
            text=True,
            check=False
        )

        if check_table.returncode == 0:
            return True, "Bridge filter already initialized."

        # Create bridge-family table.
        result = subprocess.run(
            [
                "docker", "exec", "network-controller",
                "nft", "add", "table", "bridge", "network_filter"
            ],
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode != 0:
            return False, result.stderr.strip()

        # Create a bridge forwarding chain.
        result = subprocess.run(
            [
                "docker", "exec", "network-controller",
                "nft", "add", "chain",
                "bridge", "network_filter", "forward",
                "{ type filter hook forward priority 0; policy accept; }"
            ],
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode != 0:
            return False, result.stderr.strip()

        # Create a set containing IP addresses of blocked clients.
        result = subprocess.run(
            [
                "docker", "exec", "network-controller",
                "nft", "add", "set",
                "bridge", "network_filter", "blocked_clients",
                "{ type ipv4_addr; }"
            ],
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode != 0:
            return False, result.stderr.strip()

        # Add the actual blocking rule.
        #
        # Any IPv4 packet whose source IP is present in the
        # blocked_clients set will be dropped.
        result = subprocess.run(
            [
                "docker", "exec", "network-controller",
                "nft", "add", "rule",
                "bridge", "network_filter", "forward",
                "ip", "saddr", "@blocked_clients",
                "drop"
            ],
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode != 0:
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
        if "No such file" in result.stderr:
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