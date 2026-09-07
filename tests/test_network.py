import subprocess
import json
import pytest


# --------------------------------------------------
# Configuration
# --------------------------------------------------

NETWORK_NAME = "network_project-net"

CONTAINERS = [
    "server",
    "client1",
    "client2",
    "network-controller",
]

CLIENT_CONTAINERS = [
    "client1",
    "client2",
]

EXPECTED_IPS = {
    "server": "172.20.0.3",
    "client1": "172.20.0.2",
    "client2": "172.20.0.4",
}


# --------------------------------------------------
# Helper Functions
# --------------------------------------------------

def run_command(command):
    """
    Run a shell command and return the completed process.
    """
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )


def container_exists(container):
    """
    Check whether a Docker container exists.
    """
    result = run_command(
        ["docker", "inspect", container]
    )

    return result.returncode == 0


def container_is_running(container):
    """
    Check whether a Docker container is currently running.
    """
    result = run_command(
        ["docker", "inspect", "-f", "{{.State.Running}}", container]
    )

    return (
        result.returncode == 0
        and result.stdout.strip() == "true"
    )


def get_container_networks(container):
    """
    Return the Docker networks attached to a container.
    """
    result = run_command(
        [
            "docker",
            "inspect",
            "-f",
            "{{json .NetworkSettings.Networks}}",
            container,
        ]
    )

    if result.returncode != 0:
        return {}

    return json.loads(result.stdout)


def docker_available():
    """
    Check whether Docker is available.
    """
    result = run_command(["docker", "info"])

    return result.returncode == 0


# --------------------------------------------------
# Docker Availability
# --------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def require_docker():
    """
    Skip the complete network test suite if Docker is
    unavailable.
    """
    if not docker_available():
        pytest.skip(
            "Docker is not available or Docker daemon is not running."
        )


# --------------------------------------------------
# Container Existence Tests
# --------------------------------------------------

def test_server_exists():
    """
    Verify that the server container exists.
    """
    assert container_exists("server"), \
        "server container does not exist."


def test_client1_exists():
    """
    Verify that client1 exists.
    """
    assert container_exists("client1"), \
        "client1 container does not exist."


def test_client2_exists():
    """
    Verify that client2 exists.
    """
    assert container_exists("client2"), \
        "client2 container does not exist."


def test_network_controller_exists():
    """
    Verify that the network-controller container exists.
    """
    assert container_exists("network-controller"), \
        "network-controller container does not exist."


# --------------------------------------------------
# Container Running Tests
# --------------------------------------------------

@pytest.mark.parametrize(
    "container",
    CONTAINERS
)
def test_container_is_running(container):
    """
    Verify that every required container is running.
    """
    assert container_is_running(container), \
        f"{container} exists but is not running."


# --------------------------------------------------
# Docker Network Tests
# --------------------------------------------------

@pytest.mark.parametrize(
    "container",
    ["server", "client1", "client2"]
)
def test_container_is_connected_to_project_network(container):
    """
    Verify that server/client containers are connected
    to project-net.
    """
    networks = get_container_networks(container)

    assert NETWORK_NAME in networks, (
        f"{container} is not connected to {NETWORK_NAME}."
    )


def test_server_has_expected_ip():
    """
    Verify the server has the expected static IP.
    """
    networks = get_container_networks("server")

    assert NETWORK_NAME in networks

    ip_address = networks[NETWORK_NAME]["IPAddress"]

    assert ip_address == EXPECTED_IPS["server"]


def test_client1_has_expected_ip():
    """
    Verify client1 has the expected static IP.
    """
    networks = get_container_networks("client1")

    assert NETWORK_NAME in networks

    ip_address = networks[NETWORK_NAME]["IPAddress"]

    assert ip_address == EXPECTED_IPS["client1"]


def test_client2_has_expected_ip():
    """
    Verify client2 has the expected static IP.
    """
    networks = get_container_networks("client2")

    assert NETWORK_NAME in networks

    ip_address = networks[NETWORK_NAME]["IPAddress"]

    assert ip_address == EXPECTED_IPS["client2"]


# --------------------------------------------------
# Basic Connectivity Tests
# --------------------------------------------------

def test_client1_can_ping_server():
    """
    Verify client1 can initially communicate with server.
    """
    result = run_command(
        [
            "docker",
            "exec",
            "client1",
            "ping",
            "-c",
            "3",
            "-W",
            "1",
            EXPECTED_IPS["server"],
        ]
    )

    assert result.returncode == 0, (
        "client1 cannot communicate with server.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_client2_can_ping_server():
    """
    Verify client2 can initially communicate with server.
    """
    result = run_command(
        [
            "docker",
            "exec",
            "client2",
            "ping",
            "-c",
            "3",
            "-W",
            "1",
            EXPECTED_IPS["server"],
        ]
    )

    assert result.returncode == 0, (
        "client2 cannot communicate with server.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_client1_can_ping_client2():
    """
    Verify clients can communicate with each other initially.
    """
    result = run_command(
        [
            "docker",
            "exec",
            "client1",
            "ping",
            "-c",
            "3",
            "-W",
            "1",
            EXPECTED_IPS["client2"],
        ]
    )

    assert result.returncode == 0, (
        "client1 cannot communicate with client2.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


# --------------------------------------------------
# HTTP Server Test
# --------------------------------------------------

def test_server_http_service_is_reachable():
    """
    Verify that the HTTP server running inside the server
    container is reachable from client1.
    """
    result = run_command(
        [
            "docker",
            "exec",
            "client1",
            "python3",
            "-c",
            (
                "import urllib.request; "
                "urllib.request.urlopen("
                "'http://172.20.0.3:5000', "
                "timeout=3"
                ")"
            ),
        ]
    )

    assert result.returncode == 0, (
        "HTTP server on server:5000 is not reachable.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


# --------------------------------------------------
# iperf3 Tests
# --------------------------------------------------

def test_iperf3_is_installed_on_client1():
    """
    Verify that iperf3 is installed on client1.
    """
    result = run_command(
        [
            "docker",
            "exec",
            "client1",
            "iperf3",
            "--version",
        ]
    )

    assert result.returncode == 0, (
        "iperf3 is not available inside client1.\n"
        f"stderr:\n{result.stderr}"
    )


def test_iperf3_server_is_reachable():
    """
    Verify that client1 can communicate with an iperf3
    server running on the server container.

    This test starts a temporary iperf3 server inside the
    server container, performs a short client test, and
    then terminates the temporary server.
    """

    server_process = subprocess.Popen(
        [
            "docker",
            "exec",
            "server",
            "iperf3",
            "-s",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # Give iperf3 a moment to start listening.
        import time
        time.sleep(1)

        result = run_command(
            [
                "docker",
                "exec",
                "client1",
                "iperf3",
                "-c",
                EXPECTED_IPS["server"],
                "-t",
                "2",
            ]
        )

        assert result.returncode == 0, (
            "client1 could not reach the iperf3 server.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    finally:
        # Stop the temporary iperf3 server.
        run_command(
            [
                "docker",
                "exec",
                "server",
                "pkill",
                "iperf3",
            ]
        )

        server_process.terminate()