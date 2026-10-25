# INA v2 Setup Guide

## Overview

This document provides guidelines for environment setup and project prerequisites for **Intelligent Network Configuration Assistant v2 (INA v2)**.

> **Status Notice:** The project is currently at the repository skeleton milestone. Implementation, runtime dependencies, and network scripts have not yet been installed or executed.

## Prerequisites (Planned)

- **Operating System:** Linux (Fedora 38+ recommended for laptop testing; Raspberry Pi OS planned for edge deployment)
- **Python:** Python 3.11+
- **Network Interface:** Dedicated wireless or wired network interface dedicated to the test network
- **System Tools (Future Milestones):** `nftables` or `iptables`, `tc` (traffic control), `iproute2`, DHCP/DNS services (`dnsmasq` or `NetworkManager`)

## Initial Environment Setup (Preview)

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd intelligent-network-assistant-v2
   ```

2. **Environment Variables:**
   Copy the example environment configuration:
   ```bash
   cp .env.example .env
   ```
   Configure the environment variables in `.env` according to your local environment.

3. **Dependencies:**
   Dependencies will be defined in `requirements.txt` and `pyproject.toml` during subsequent implementation milestones.

