# INA v2 Setup and Deployment Guide

This guide provides end-to-end, reproducible instructions for deploying the **Intelligent Network Configuration Assistant v2 (INA v2)**. It covers both the **Raspberry Pi 5 Router (Data Plane)** and the **Operator Laptop (Control Plane)**, including quick-start command runbooks and an in-depth reference configuration.

---

## 1. System Requirements & Prerequisites

### 1.1 Raspberry Pi 5 Router Host (`ina-router`)
- **Hardware:** Raspberry Pi 5 (4GB or 8GB RAM), official 27W USB-C power supply, 32GB+ Class 10 MicroSD or NVMe SSD.
- **Operating System:** Raspberry Pi OS Lite 64-bit (Debian 13 Trixie / Bookworm compatible).
- **Network Interfaces:**
  - `wlan0`: Physical Wi-Fi interface operating in Station (STA) mode connected to upstream WAN (e.g., mobile hotspot or campus network).
  - `ap0`: Virtual Wi-Fi interface (`__ap` mode on `wlan0`) serving local subnet `192.168.50.0/24`.
- **System Packages:** `hostapd`, `dnsmasq`, `nftables`, `iw`, `iperf3`, `ethtool`, `python3-venv`, `openssl`, `curl`, `git`.

### 1.2 Operator Workstation (Control Plane)
- **Operating System:** Linux (Fedora, Ubuntu, Debian), macOS, or Windows 11 with WSL2.
- **Runtime:** Python 3.11 or newer.
- **Python Dependencies:** Official MCP SDK (`mcp==2.3.0`), `pydantic`. Optional: `anthropic>=0.18.0` for Claude.

---

## 2. Raspberry Pi 5 Quick-Start Runbook

Follow these commands sequentially on a fresh Raspberry Pi OS Lite installation.

### Step 1: Base Packages & Repository Setup
```bash
sudo apt update && sudo apt install -y hostapd dnsmasq nftables iw iperf3 ethtool git python3-venv openssl curl
sudo systemctl unmask hostapd

# Create INA directory and clone repository
sudo install -d -o root -g root -m 0755 /opt/ina
sudo git clone https://github.com/vedantb7/inav2.git /opt/ina/inav2
cd /opt/ina/inav2/code

# Setup root-owned Python virtual environment
sudo chown -R root:root /opt/ina
sudo python3 -m venv /opt/ina/venv
sudo /opt/ina/venv/bin/pip install --upgrade pip
sudo /opt/ina/venv/bin/pip install -r requirements.txt
```

### Step 2: Access Point Configuration (`hostapd`)
```bash
cd /opt/ina/inav2/code
sudo install -m 0600 config/hostapd/hostapd.conf.template /etc/hostapd/hostapd.conf
sudo sed -i 's|^wpa_passphrase=.*|wpa_passphrase=MySecurePassphrase123|' /etc/hostapd/hostapd.conf
sudo sed -i 's|^country_code=.*|country_code=IN|' /etc/hostapd/hostapd.conf
sudo install -o root -g root -m 0600 /dev/null /etc/hostapd/hostapd.deny
sudo sed -i 's|^#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
```

### Step 3: DHCP and DNS Server (`dnsmasq`)
```bash
cd /opt/ina/inav2/code
sudo mv /etc/dnsmasq.conf /etc/dnsmasq.conf.bak 2>/dev/null || true
sudo install -m 0644 config/dnsmasq/ina-ap.conf /etc/dnsmasq.d/ina-ap.conf
sudo install -d -o root -g root -m 0755 /etc/ina
sudo install -m 0644 /dev/null /etc/ina/dhcp-hosts
sudo dnsmasq --test --conf-file=/etc/dnsmasq.d/ina-ap.conf
```

### Step 4: Virtual AP Interface & NetworkManager
```bash
cd /opt/ina/inav2/code
# Prevent NetworkManager from managing virtual AP interface ap0
sudo install -o root -g root -m 0644 config/NetworkManager/90-ina-unmanaged.conf /etc/NetworkManager/conf.d/90-ina-unmanaged.conf
sudo systemctl reload NetworkManager

# Install virtual interface creation script
sudo install -o root -g root -m 0755 config/scripts/ina-ap-interface.sh /usr/local/sbin/ina-ap-interface.sh

# Install systemd service for ap0
sudo tee /etc/systemd/system/ina-ap-interface.service >/dev/null <<'EOF'
[Unit]
Description=INA v2 Virtual Access Point Interface Setup
Before=hostapd.service dnsmasq.service
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ina-ap-interface.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

# Order hostapd and dnsmasq after ap0 creation
sudo install -d -m 0755 /etc/systemd/system/hostapd.service.d /etc/systemd/system/dnsmasq.service.d
sudo tee /etc/systemd/system/hostapd.service.d/override.conf >/dev/null <<'EOF'
[Unit]
BindsTo=sys-subsystem-net-devices-ap0.device
After=ina-ap-interface.service sys-subsystem-net-devices-ap0.device
EOF
sudo tee /etc/systemd/system/dnsmasq.service.d/override.conf >/dev/null <<'EOF'
[Unit]
After=ina-ap-interface.service hostapd.service
EOF
```

### Step 5: Routing, NAT Masquerade & Kernel Modules
```bash
cd /opt/ina/inav2/code
# Persistent IPv4 forwarding
sudo tee /etc/sysctl.d/99-ina-router.conf >/dev/null <<'EOF'
net.ipv4.ip_forward=1
net.ipv4.conf.all.forwarding=1
EOF
sudo sysctl --system

# Autoload traffic shaping & ifb modules
sudo install -m 0644 config/modules-load.d/ina.conf /etc/modules-load.d/ina.conf
sudo systemctl restart systemd-modules-load

# Base NAT masquerade
sudo install -m 0644 config/nftables/nftables.conf /etc/nftables.conf
sudo systemctl enable --now nftables.service
```

### Step 6: Privilege Boundary & Runtime Directories
```bash
cd /opt/ina/inav2/code
sudo useradd --system --create-home --shell /bin/sh ina 2>/dev/null || true
sudo install -o root -g root -m 0755 config/scripts/ina-netctl /usr/local/sbin/ina-netctl
sudo install -o root -g root -m 0755 config/scripts/ina-mcp    /usr/local/bin/ina-mcp
sudo visudo -cf config/sudoers.d/ina && sudo install -o root -g root -m 0440 config/sudoers.d/ina /etc/sudoers.d/ina
sudo install -m 0644 config/tmpfiles.d/ina.conf /etc/tmpfiles.d/ina.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/ina.conf

# Reconcile on boot and temporary ban expiry
sudo install -m 0644 config/systemd/ina-reconcile.service config/systemd/ina-expire.service \
     config/systemd/ina-expire.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ina-reconcile.service ina-expire.timer
```

### Step 7: Device Registration & Anti-Spoofing Binding
```bash
# Read connected devices from leases
cat /var/lib/misc/dnsmasq.leases

# Register protected devices and clients
sudo nano /opt/ina/inav2/code/policy/devices.yaml
sudo nano /opt/ina/inav2/code/policy/rules.yaml

# Apply reservations, MAC-IP bindings, and reload dnsmasq
sudo ina-netctl sync-devices
```

### Step 8: Role Grants & TLS Setup for MCP Server
```bash
# Initialize empty grants file and grant admin role bound to admin laptop
sudo install -o root -g ina -m 0640 /dev/null /etc/ina/grants.json
sudo ina-netctl roles grant admin_user admin --device admin-laptop

# Generate elliptic curve TLS certificate for HTTPS
sudo install -d -o root -g ina -m 0750 /etc/ina/tls
sudo openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 365 \
    -subj "/CN=ina-router" \
    -addext "subjectAltName=IP:192.168.50.1,DNS:ina-router,DNS:ina-router.local" \
    -keyout /etc/ina/tls/server.key -out /etc/ina/tls/server.crt
sudo chown root:ina /etc/ina/tls/server.key /etc/ina/tls/server.crt
sudo chmod 0640 /etc/ina/tls/server.key
sudo chmod 0644 /etc/ina/tls/server.crt

# Install and start MCP systemd service
sudo install -m 0644 config/systemd/ina-mcp.service /etc/systemd/system/ina-mcp.service
sudo systemctl daemon-reload
sudo systemctl enable --now ina-mcp.service

# Export public certificate for laptop trust
sudo cp /etc/ina/tls/server.crt ~ina/ina-router.crt
sudo chmod 0644 ~ina/ina-router.crt
```

---

## 3. Operator Laptop Quick-Start Runbook

### Step 1: Environment Setup
```bash
git clone https://github.com/vedantb7/inav2.git
cd inav2/code

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install pyyaml
```

### Step 2: Copy TLS Certificate from Raspberry Pi
Connect laptop to Wi-Fi SSID `INA-Router` (Passphrase configured in `hostapd.conf`). Retrieve the router's TLS public certificate:
```bash
mkdir -p ~/.config/ina
scp ina@192.168.50.1:~/ina-router.crt ~/.config/ina/ina-router.crt
```

### Step 3: Configure Environment Variables
Copy and edit `.env`:
```bash
cp .env.example .env
nano .env
```
Set the following connection values:
```bash
INA_ROLE_TOKEN=<TOKEN_OUTPUT_FROM_ROLES_GRANT>
INA_MCP_URL=https://192.168.50.1:8443/mcp
INA_MCP_CA=/home/<username>/.config/ina/ina-router.crt

# LLM Provider Configuration (Optional, leave blank for rule-based offline parser)
LLM_PROVIDER=gemini
LLM_API_KEY=AIzaSy...
LLM_MODEL=gemini-2.5-flash
```

### Step 4: Execute Natural-Language Commands
```bash
# Query status
python3 -m assistant --server mcp "list devices"

# Apply rate limit
python3 -m assistant --server mcp "limit lab-laptop-3 to 10 mbps"

# Block client
python3 -m assistant --server mcp "block lab-laptop-3"

# Restore client
python3 -m assistant --server mcp "restore lab-laptop-3"
```

---

## 4. Deep-Dive Infrastructure Configuration Reference

### 4.1 Interface Initializer Script (`ina-ap-interface.sh`)
Located at `/usr/local/sbin/ina-ap-interface.sh`, this script executes before `hostapd` and `dnsmasq`:
1. Creates virtual interface `ap0` on physical interface `wlan0` using `iw dev wlan0 interface add ap0 type __ap`.
2. Assigns static gateway address `192.168.50.1/24` with broadcast `192.168.50.255`.
3. Brings link state `UP`.
4. Enables Private VLAN proxy ARP (`sysctl -w net.ipv4.conf.ap0.proxy_arp_pvlan=1`), forcing isolated clients (`ap_isolate=1`) to route inter-station traffic through the Pi gateway.
5. Disables hardware offload features (`ethtool -K ap0 gro off gso off tso off`) to prevent packet drop issues under `tc` shaping.

### 4.2 NetworkManager Unmanaged Configuration
To prevent NetworkManager from overriding `ap0`'s static IP or flushing routes during Wi-Fi scans, `/etc/NetworkManager/conf.d/90-ina-unmanaged.conf` explicitly marks `ap0` unmanaged:
```ini
[keyfile]
unmanaged-devices=interface-name:ap0
```

### 4.3 `hostapd` Configuration
Key security and isolation parameters in `/etc/hostapd/hostapd.conf`:
- `interface=ap0`: Binds exclusively to the virtual AP interface.
- `ssid=INA-Router`: Advertised 802.11 SSID.
- `channel=11`: Operating 2.4 GHz channel (must match `wlan0` hardware radio frequency).
- `wpa=2` & `wpa_key_mgmt=WPA-PSK` & `rsn_pairwise=CCMP`: Modern WPA2-Personal security.
- `ap_isolate=1`: Enforces layer-2 client isolation; wireless cards will not forward frames directly between associated stations.
- `ctrl_interface=/run/hostapd`: Root-only control socket used by `ina-netctl` for `hostapd_cli`.
- `deny_mac_file=/etc/hostapd/hostapd.deny`: File containing actively banned MAC addresses.

### 4.4 `dnsmasq` Configuration
Configured via `/etc/dnsmasq.d/ina-ap.conf`:
- `interface=ap0` & `bind-interfaces`: Listens only on local access point.
- `dhcp-range=192.168.50.10,192.168.50.100,255.255.255.0,12h`: Dynamic lease pool.
- `dhcp-option=option:router,192.168.50.1`: Default gateway announcement.
- `dhcp-option=option:dns-server,192.168.50.1`: Local DNS resolver.
- `dhcp-hostsfile=/etc/ina/dhcp-hosts`: Static reservations synchronized by `ina-netctl sync-devices`.

### 4.5 Packet Filtering & NAT (`nftables`)
Base configuration in `/etc/nftables.conf`:
```nft
table ip ina_nat {
    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        ip saddr 192.168.50.0/24 oifname "wlan0" masquerade
    }
}
```
The active filtering table (`inet ina_filter`) is dynamically managed by INA (`code/config/nftables/ina-filter.nft`), containing sets:
- `blocked_clients`: IP drop set for internet/gateway blocks.
- `blocked_pairs`: Bidirectional pair-drop set for client-to-client blocks.
- `mac_ip_bind`: Anti-spoofing set binding static IPs to approved hardware MAC addresses.

---

## 5. Deployment Verification Checklist

Run these commands to verify router health:

```bash
# 1. Verify system services are active
systemctl is-active ina-ap-interface hostapd dnsmasq nftables ina-mcp

# 2. Check kernel IP forwarding
sysctl net.ipv4.ip_forward    # Expect: net.ipv4.ip_forward = 1

# 3. Check virtual AP interface
ip addr show ap0              # Expect: 192.168.50.1/24, state UP

# 4. Check active leases
cat /var/lib/misc/dnsmasq.leases

# 5. Check anti-spoofing binding set
sudo nft list set inet ina_filter mac_ip_bind

# 6. Test MCP endpoint over HTTPS
curl -k https://192.168.50.1:8443/mcp   # Expect: 401 Unauthorized (confirms server is alive and enforcing auth)
```

