"""
network/discovery.py
====================
Dynamic Docker Engine API container discovery module using the Docker SDK for Python.

This module replaces static, hardcoded client IP dictionaries with runtime container
lookup via `docker.from_env()`.
"""

from typing import Dict, Any, Optional
try:
    import docker
    from docker.errors import DockerException, NotFound, APIError
    DOCKER_SDK_AVAILABLE = True
except ImportError:
    docker = None
    DockerException = Exception
    NotFound = Exception
    APIError = Exception
    DOCKER_SDK_AVAILABLE = False


def get_docker_client():
    """
    Instantiate and return a Docker SDK client connected to the local Docker daemon.

    Raises:
        RuntimeError: If docker package is missing or Docker daemon is unreachable.
    """
    if not DOCKER_SDK_AVAILABLE:
        raise RuntimeError("The 'docker' Python package is not installed. Run 'pip install docker'.")
    try:
        return docker.from_env()
    except DockerException as err:
        raise RuntimeError(
            f"Docker daemon is not running or unreachable: {err}. "
            "Please ensure Docker Desktop/daemon is active."
        ) from err


def get_container_info(container_name: str) -> Dict[str, Any]:
    """
    Retrieve dynamic runtime metadata for a specific container by name using Docker SDK.

    Args:
        container_name: Hostname or container name (e.g. 'client1', 'server').

    Returns:
        Dict containing id, name, status, networks, and resolved ip_address.

    Raises:
        ValueError: If container does not exist or is not running.
        RuntimeError: If Docker daemon connection fails.
    """
    client = get_docker_client()
    try:
        container = client.containers.get(container_name)
    except NotFound:
        raise ValueError(f"Client '{container_name}' not found in Docker environment.")
    except APIError as err:
        raise RuntimeError(f"Docker API error while inspecting '{container_name}': {err}") from err

    state = container.status  # 'running', 'exited', 'paused', etc.
    if state != "running":
        raise ValueError(f"Container '{container_name}' exists but is not running (status: {state}).")

    networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
    return {
        "id": container.id,
        "name": container.name,
        "status": state,
        "networks": networks,
    }


def get_container_ip(container_name: str, preferred_network: str = "project-net") -> str:
    """
    Dynamically resolve the current IPv4 address of a running container on a target network.

    Args:
        container_name: Hostname or container name (e.g. 'client1', 'client2', 'server').
        preferred_network: Substring or network name to match (e.g. 'project-net').

    Returns:
        Assigned IPv4 address string (e.g. '172.20.0.2').

    Raises:
        ValueError: If container cannot be found, is stopped, or lacks an assigned IP.
        RuntimeError: If Docker daemon fails.
    """
    info = get_container_info(container_name)
    networks = info.get("networks", {})

    if not networks:
        raise ValueError(f"Container '{container_name}' is not attached to any Docker network.")

    # 1. Try exact or partial match for preferred_network (e.g. 'network_project-net' or 'project-net')
    for net_name, net_config in networks.items():
        if preferred_network in net_name:
            ip = net_config.get("IPAddress", "").strip()
            if ip:
                return ip

    # 2. Fallback: return the first non-empty IPv4 address found across any connected network
    for net_name, net_config in networks.items():
        ip = net_config.get("IPAddress", "").strip()
        if ip:
            return ip

    raise ValueError(f"Container '{container_name}' has no assigned IPv4 address on any active network.")


def list_known_clients(preferred_network: str = "project-net") -> Dict[str, str]:
    """
    Discover all running containers attached to the project network and return
    a map of container names to their current IP addresses.

    Returns:
        Dict[str, str]: e.g. {"client1": "172.20.0.2", "client2": "172.20.0.4", "server": "172.20.0.3"}
    """
    client = get_docker_client()
    discovered: Dict[str, str] = {}
    try:
        containers = client.containers.list(filters={"status": "running"})
        for container in containers:
            networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
            for net_name, net_config in networks.items():
                if preferred_network in net_name:
                    ip = net_config.get("IPAddress", "").strip()
                    if ip:
                        discovered[container.name] = ip
                        break
        return discovered
    except APIError as err:
        raise RuntimeError(f"Failed to discover containers via Docker API: {err}") from err
