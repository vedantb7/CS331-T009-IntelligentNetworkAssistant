# Intelligent Network Assistant (INA) Documentation

Welcome to the documentation for the **Intelligent Network Assistant (INA)**. This repository houses the files, configurations, and scripts for setting up, managing, and testing a containerized multi-node network environment for network analysis and controller actions.

---

## 📂 Project Structure

Here is an overview of the key files and directories in this project:

```text
ina/
├── docs/                             # Project documentation
│   ├── README.md                     # Main documentation entry point (this file)
│   ├── network.md                    # Combined network architecture, setup, and installation guide
│   └── network-baseline.md           # Baseline verification & test results
├── network/                          # Network environment & services
│   ├── Dockerfile                    # Container definition with networking utilities
│   ├── docker-compose.yml            # Multi-container service definitions
│   ├── setup.sh                      # Shell script to automate checks, setup & tests
│   └── client.py                     # Custom TCP client/server socket script
└── README.md                         # Project landing page
```

---

## 🌐 Network Overview

The network topology consists of a custom bridge network (`project-net`) hosting three distinct nodes and one special `network-controller` node running in host mode:

1. **`server` (`172.20.0.3`)**: Runs a standard HTTP server on port 5000.
2. **`client1` (`172.20.0.2`)**: A client node used to check connectivity and run TCP client/server tests.
3. **`client2` (`172.20.0.4`)**: A second client node in the same subnet.
4. **`network-controller` (Host Mode)**: Runs with privileged status and host network access, intended for network control, routing management, and traffic monitoring actions.

For detailed architecture and interface configuration details, see the [Network Documentation](network.md).

---

## 🚀 Quick Start

To quickly get the environment running and perform automated connectivity verification, execute:

```bash
cd network
./setup.sh
```

For full installation prerequisites, step-by-step setup guides, and troubleshooting instructions, see the [Network Documentation](network.md).
