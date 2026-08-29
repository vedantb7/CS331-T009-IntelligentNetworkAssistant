#!/bin/bash

# ==========================================
# Intelligent Network Assistant 
# Docker Network Setup Script
# ==========================================

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$PROJECT_DIR"

echo "==========================================" 
echo " Intelligent Network Assistant" 
echo " Docker Network Setup" 
echo "=========================================="

# ------------------------------------------ 
# 1. Check Docker 
# ------------------------------------------

echo 
echo "[1/6] checking docker..."

if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is not installed."
    exit 1
fi

if ! docker info &> /dev/null; then 
    echo "ERROR: Docker daemon is not running."
    exit 1
fi

echo "Docker is available"


# ------------------------------------------ 
# 2. Clean up previous project environment 
# ------------------------------------------

echo
echo "[2/6] cleaning up previous environment..." 

docker compose down --remove-orphans

echo "previous containers removed."

# ------------------------------------------ 
# 3. Remove leftover Compose network 
# ------------------------------------------

echo 
echo "[3/6] checking for leftover network..."

PROJECT_NETWORK="network_projct-net"

if docker network inspect "$PROJECT_NETWORK" &> /dev/null; then
    echo "Removing leftover network: $PROJECT_NETWORK"
    docker network rm "$PROJECT_NETWORK" || true
else
    echo "No leftover network found."
fi

# ------------------------------------------ 
# 4. Start containers 
# ------------------------------------------

echo
echo "[4/6] starting docker environment..."

docker compose up -d --build
echo "Docker environment started."

# ------------------------------------------ 
# 5. Display container/network information 
# ------------------------------------------

echo
echo "[5/6] checking containers and ip addr..."

echo
echo "--- Container status ---"

docker compose ps

echo 
echo "--- ip addr ---"

for container in client1 client2 server; do
    if docker ps --format '{{.Names}}' | grep -q "^${container}$"; then 
        IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$container")
        echo "$container -> $IP"

    else 
        echo "ERROR: $container is not running."
        exit 1
    fi
done

# ------------------------------------------ 
# 6. Connectivity tests 
# ------------------------------------------

echo
echo "[6/6] testing connectivity..."

#client1 -> server
echo
echo "Testing client1 -> server..."

if docker exec client1 python3 -c "import socket; socket.create_connection(('server', 5000), 5)" &> /dev/null; then 
    echo "SUCCESS: client1 -> server:5000"
else 
    echo "ERROR: client1 cant reach server:5000"
    exit 1
fi

# client2 -> server
echo
echo "Testing client2 -> server..."

if docker exec client2 python3 -c "import socket; socket.create_connection(('server', 5000), 5)" &> /dev/null; then 
    echo "SUCCESS: client2 -> server:5000"
else 
    echo "ERROR: client2 cant reach server:5000"
    exit 1
fi


# client1 -> client2
echo 
echo "Testing client1 -> client2..."

if docker exec client1 python3 -c "import socket; socket.gethostbyname('client2')" &> /dev/null; then
    echo "SUCCESS: client1 can resolve client2"
else 
    echo "ERROR: client1 cant resolve client2"
    exit 1
fi

# ------------------------------------------ 
# ICMP connectivity tests 
# ------------------------------------------

echo 
echo "--- ICMP connectivity tests ---"

echo 
echo "testing client1 -> server..."

if docker exec client1 ping -c 3 -W 2 server &> /dev/null; then
    echo "SUCCESS: client1 -> server (ICMP)"
else 
    echo "ERROR: client1 cannot ping server"
    exit 1
fi

echo 
echo "testing client2 -> server..."

if docker exec client2 ping -c 3 -W 2 server &> /dev/null; then
    echo "SUCCESS: client2 -> server (ICMP)"
else 
    echo "ERROR: client2 cannot ping server"
    exit 1
fi


echo 
echo "==========================================" 
echo " Network setup completed successfully!" 
echo "=========================================="

echo
echo "Enter client1:"
echo "  docker exec -it client1 bash"

echo
echo "Enter client2:"
echo "  docker exec -it client2 bash"

echo
echo "Stop environment:"
echo "  docker compose down"