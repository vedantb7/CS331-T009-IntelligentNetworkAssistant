# Raspberry Pi 5 Router Setup Guide

This document is a comprehensive, reproducible step-by-step setup guide for building the **INA v2** dedicated network router infrastructure from scratch on a **Raspberry Pi 5**.

The Raspberry Pi 5 acts as the central router and Wi-Fi access point (`INA-Router`), while connected client devices (including a Fedora developer workstation) join as standard wireless clients.

---

## 1. Hardware & Environmental Prerequisites

- **Router Host:** Raspberry Pi 5 (4GB or 8GB model recommended).
- **Storage:** MicroSD card (16 GB minimum, Class 10 / A2 rated).
- **Power Supply:** Official Raspberry Pi 27W USB-C Power Supply.
- **Client Device:** Linux workstation (e.g., Fedora Workstation) with Wi-Fi support.
- **Internet Uplink:** 4G/5G mobile phone hotspot (e.g. `1+ Four`) or secondary Wi-Fi access point.
- **Network Interface Requirements:** Built-in wireless interface (`wlan0`) supporting nl80211 virtual interface creation (`type __ap`).

---

## 2. Flashing & OS Pre-Configuration

1. Insert the microSD card into your workstation and open **Raspberry Pi Imager**.
2. Select:
   - **Device:** Raspberry Pi 5
   - **Operating System:** Raspberry Pi OS (Other) → **Raspberry Pi OS Lite (64-bit)** (Debian Trixie base)
   - **Storage:** Select your microSD card
3. Click **Next** and click **Edit Settings** (OS Customization):
   - **General Tab:**
     - Set Hostname: `ina-router`
     - Set Username and Password: `team009` (Set a strong administrative password)
     - Wireless LAN: You may enter your temporary configuration or configure it post-boot.
     - Wireless LAN Country: Set your ISO country code (e.g., `IN` for India, `US` for United States).
     - Locale and Time Zone: Configure appropriate local timezone.
   - **Services Tab:**
     - Enable SSH (Select **Use password authentication** or provide your workstation's public SSH key).
4. Save settings and flash the card.

---

## 3. Initial Boot & SSH Access

1. Insert the microSD card into the Raspberry Pi 5 and connect power.
2. Allow 60–90 seconds for first boot and partition expansion.
3. Locate the Pi on your network or connect via mDNS:
   ```bash
   ssh team009@ina-router.local
   ```
4. Verify system release and hardware:
   ```bash
   uname -a
   cat /etc/os-release
   ```

---

## 4. Connecting the Wi-Fi Uplink (`wlan0`)

The Pi uses its physical Wi-Fi adapter (`wlan0`) to connect to the upstream phone hotspot:

1. Scan for available networks:
   ```bash
   sudo nmcli dev wifi list
   ```
2. Connect to the upstream hotspot:
   ```bash
   sudo nmcli dev wifi connect "<YOUR_HOTSPOT_SSID>" password "<YOUR_HOTSPOT_PASSWORD>"
   ```
   *(Replace `<YOUR_HOTSPOT_SSID>` and `<YOUR_HOTSPOT_PASSWORD>` with your actual phone hotspot credentials. Never commit real credentials to Git.)*

3. **Verify uplink connectivity before proceeding:**
   ```bash
   # Check assigned IP and default gateway
   ip -4 addr show dev wlan0
   ip route show default

   # Test external reachability
   ping -c 3 8.8.8.8
   ```
   Do not proceed until the Pi has verified external internet reachability through `wlan0`.

---

## 5. Installing Core Network Packages

Update package repositories and install the required routing and AP utilities:

```bash
sudo apt update
sudo apt install -y hostapd dnsmasq nftables iw iperf3
```

> **Note on `hostapd`:** On Debian/Raspberry Pi OS, `hostapd` is masked by default upon installation. Unmask it now:
> ```bash
> sudo systemctl unmask hostapd
> ```

---

## 6. Configuring the Access Point Daemon (`hostapd`)

Create the configuration file `/etc/hostapd/hostapd.conf`:

```bash
sudo nano /etc/hostapd/hostapd.conf
```

Insert the following verified configuration:

```ini
# INA v2 Access Point Configuration
interface=ap0
driver=nl80211
ssid=INA-Router
hw_mode=g
channel=11
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=ConfigureYourStrongPasswordHere
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
```

Point the system environment to this configuration file:

```bash
sudo sed -i 's|#DAEMON_CONF=""|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
```

---

## 7. Configuring DHCP and DNS (`dnsmasq`)

Back up any default configuration and create the dedicated INA DHCP configuration file:

```bash
sudo mv /etc/dnsmasq.conf /etc/dnsmasq.conf.bak 2>/dev/null || true
sudo nano /etc/dnsmasq.d/ina-ap.conf
```

Insert the following configuration:

```ini
# INA v2 DHCP and DNS Configuration
interface=ap0
bind-interfaces
listen-address=192.168.50.1

# DHCP IP Pool (192.168.50.10 to 192.168.50.100, 12-hour lease)
dhcp-range=192.168.50.10,192.168.50.100,255.255.255.0,12h

# Publish Default Gateway (Option 3)
dhcp-option=option:router,192.168.50.1

# Publish DNS Server (Option 6)
dhcp-option=option:dns-server,192.168.50.1,8.8.8.8

# Authoritative DHCP for this subnet
dhcp-authoritative
log-dhcp
```

---

## 8. Creating the Virtual Interface Script (`ap0`)

Create `/usr/local/sbin/ina-ap-interface.sh` to construct the virtual interface and assign its static IP:

```bash
sudo nano /usr/local/sbin/ina-ap-interface.sh
```

Insert the following script:

```bash
#!/bin/bash
set -e

# INA v2 Virtual AP Interface Initializer
IFACE="ap0"
PHYS="wlan0"
ROUTER_IP="192.168.50.1/24"

# Create ap0 virtual interface if not present
if ! ip link show "$IFACE" >/dev/null 2>&1; then
    echo "Creating virtual interface $IFACE on $PHYS..."
    iw dev "$PHYS" interface add "$IFACE" type __ap
fi

# Bring link up
echo "Bringing $IFACE up..."
ip link set dev "$IFACE" up

# Assign static router IP if not already present
if ! ip addr show dev "$IFACE" | grep -q "192.168.50.1"; then
    echo "Assigning $ROUTER_IP to $IFACE..."
    ip addr add "$ROUTER_IP" dev "$IFACE"
fi

echo "$IFACE successfully initialized."
```

Make the script executable:

```bash
sudo chmod +x /usr/local/sbin/ina-ap-interface.sh
```

---

## 9. Systemd Startup Ordering & Readiness

To guarantee that `ap0` exists and has its IP before `hostapd` and `dnsmasq` attempt to bind to it, create a systemd service and drop-in overrides.

### 1. Create `ina-ap-interface.service`

```bash
sudo nano /etc/systemd/system/ina-ap-interface.service
```

```ini
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
```

### 2. Service Overrides

Create drop-in override for `hostapd`:

```bash
sudo mkdir -p /etc/systemd/system/hostapd.service.d/
sudo nano /etc/systemd/system/hostapd.service.d/override.conf
```

```ini
[Unit]
BindsTo=sys-subsystem-net-devices-ap0.device
After=sys-subsystem-net-devices-ap0.device ina-ap-interface.service
```

Create drop-in override for `dnsmasq`:

```bash
sudo mkdir -p /etc/systemd/system/dnsmasq.service.d/
sudo nano /etc/systemd/system/dnsmasq.service.d/override.conf
```

```ini
[Unit]
After=ina-ap-interface.service hostapd.service
```

Reload systemd daemon:

```bash
sudo systemctl daemon-reload
```

---

## 10. Persistent IPv4 Forwarding

Create `/etc/sysctl.d/99-ina-router.conf` to enable kernel routing:

```bash
sudo nano /etc/sysctl.d/99-ina-router.conf
```

```ini
# INA v2 Router Kernel Forwarding
net.ipv4.ip_forward=1
```

Apply immediately:

```bash
sudo sysctl --system
```

Verify:

```bash
sysctl net.ipv4.ip_forward
# Expected output: net.ipv4.ip_forward = 1
```

---

## 11. Configuring NAT with `nftables`

Edit `/etc/nftables.conf` to create a modular NAT table.

> **Safety Warning:** Do NOT add `flush ruleset` at the beginning of this configuration. A blanket flush wipes existing firewall rules and Docker/system filters. Use an explicit table namespace (`ip ina_nat`).

```bash
sudo nano /etc/nftables.conf
```

Insert the verified NAT configuration:

```text
#!/usr/sbin/nft -f

table ip ina_nat {
    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        ip saddr 192.168.50.0/24 oifname "wlan0" masquerade
    }
}
```

Apply and verify:

```bash
sudo nft -f /etc/nftables.conf
sudo nft list table ip ina_nat
```

Expected output:
```text
table ip ina_nat {
    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        ip saddr 192.168.50.0/24 oifname "wlan0" masquerade
    }
}
```

---

## 12. Enabling and Starting Services

Enable all services for automatic boot persistence:

```bash
sudo systemctl enable ina-ap-interface.service
sudo systemctl enable hostapd.service
sudo systemctl enable dnsmasq.service
sudo systemctl enable nftables.service

# Start services
sudo systemctl start ina-ap-interface.service
sudo systemctl restart hostapd.service
sudo systemctl restart dnsmasq.service
sudo systemctl restart nftables.service
```

Check status:

```bash
systemctl is-active ina-ap-interface hostapd dnsmasq nftables
# Expected output for each: active
```

---

## 13. Client Connection & Verification

From your client workstation (e.g., Fedora laptop):

1. Scan for Wi-Fi networks and select `INA-Router`.
2. Enter the passphrase configured in `/etc/hostapd/hostapd.conf`.
3. Check the assigned IP address on your client Wi-Fi interface:
   ```bash
   ip -4 addr show
   # Should reflect an IP in the 192.168.50.10–100 pool (e.g., 192.168.50.46)
   ```
4. Verify local reachability to the Pi router:
   ```bash
   ping -c 5 192.168.50.1
   ```
5. Verify internet connectivity through the router:
   ```bash
   ping -c 5 8.8.8.8
   ```

---

## 14. Reboot Persistence Test

Verify that all services cleanly recover after a power cycle or reboot:

```bash
sudo reboot
```

After the Pi reboots:
1. Re-connect your workstation to `INA-Router`.
2. SSH into the Pi: `ssh team009@ina-router.local`.
3. Confirm services:
   ```bash
   systemctl is-active ina-ap-interface hostapd dnsmasq nftables
   sysctl net.ipv4.ip_forward
   ```
   All services must report `active` and forwarding must report `1`.
