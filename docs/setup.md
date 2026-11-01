# Raspberry Pi 5 Router Setup Guide

This document is a comprehensive, reproducible step-by-step setup guide for building the **INA v2** dedicated network router infrastructure from scratch on a **Raspberry Pi 5**.

The Raspberry Pi 5 acts as the central router and Wi-Fi access point (`INA-Router`), while connected client devices (including a Fedora developer workstation) join as standard wireless clients.

All configuration files referenced below live in this repository under `config/` and are the source of truth. Copy them instead of retyping them. Sections 1–14 build the router. Section 15 installs the INA application on it (MCP server, policy engine, privilege boundary).

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

Update package repositories and install the required routing and AP utilities, plus what the INA application needs (Python venv, git, ethtool):

```bash
sudo apt update
sudo apt install -y hostapd dnsmasq nftables iw iperf3 ethtool git python3-venv
```

> **Note on `hostapd`:** On Debian/Raspberry Pi OS, `hostapd` is masked by default upon installation. Unmask it now:
> ```bash
> sudo systemctl unmask hostapd
> ```

Get the repository onto the Pi now. The following sections copy files from it:

```bash
sudo install -d -o root -g root -m 0755 /opt/ina
sudo git clone <repository-url> /opt/ina/inav2
cd /opt/ina/inav2
```

---

## 6. Configuring the Access Point Daemon (`hostapd`)

Install the template, then set your passphrase (and `country_code` if you are not in India):

```bash
sudo install -m 0600 config/hostapd/hostapd.conf.template /etc/hostapd/hostapd.conf
sudo nano /etc/hostapd/hostapd.conf        # change wpa_passphrase (and country_code)
sudo install -o root -g root -m 0600 /dev/null /etc/hostapd/hostapd.deny
```

The template contains everything INA relies on. The plain AP settings used earlier do not:

| Setting | Why INA needs it |
|---|---|
| `ap_isolate=1` | Stops the radio from bridging clients directly to each other, so client↔client traffic passes through the Pi and `block_client <a> <b>` can stop it (works together with `proxy_arp_pvlan`, §8) |
| `ctrl_interface=/run/hostapd`, `ctrl_interface_group=0` | Root-only control socket that `kick_client`/`ban_client` use through `ina-netctl` |
| `macaddr_acl=0`, `deny_mac_file=/etc/hostapd/hostapd.deny` | MAC deny list for `ban_client` (hostapd fails to start if the file is missing, hence the `install` above) |
| `wpa=2`, `rsn_pairwise=CCMP`, `ieee80211n=1`, `wmm_enabled=1` | WPA2-CCMP only (no TKIP); 802.11n rates need WMM |

Point the system environment to this configuration file:

```bash
sudo sed -i 's|#DAEMON_CONF=""|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
```

---

## 7. Configuring DHCP and DNS (`dnsmasq`)

Back up any default configuration and install the INA DHCP/DNS configuration:

```bash
sudo mv /etc/dnsmasq.conf /etc/dnsmasq.conf.bak 2>/dev/null || true
sudo install -m 0644 config/dnsmasq/ina-ap.conf /etc/dnsmasq.d/ina-ap.conf
sudo install -d -o root -g root -m 0755 /etc/ina
sudo install -m 0644 /dev/null /etc/ina/dhcp-hosts     # filled by 'ina-netctl sync-devices' (§15.4)
sudo dnsmasq --test --conf-file=/etc/dnsmasq.d/ina-ap.conf
```

Besides the pool (`192.168.50.10–100`, 12 h), gateway and DNS options, the file sets `dhcp-hostsfile=/etc/ina/dhcp-hosts`. That file holds the per-device reservations generated from `policy/devices.yaml`, so each registered device's lease carries its admin-assigned name (e.g. `guest-phone`) and not the hostname the device picked for itself. INA tools and the policy engine refer to devices by those names. dnsmasq re-reads the file on `SIGHUP`, which `sync-devices` sends, so no restart is needed.

---

## 8. Creating the Virtual Interface Script (`ap0`)

Install the interface script:

```bash
sudo install -o root -g root -m 0755 config/scripts/ina-ap-interface.sh /usr/local/sbin/ina-ap-interface.sh
```

It creates `ap0` on the onboard radio if missing (`iw dev wlan0 interface add ap0 type __ap`), assigns `192.168.50.1/24` and sets:

- `net.ipv4.ip_forward=1` and `send_redirects=0`.
- `net.ipv4.conf.ap0.proxy_arp_pvlan=1`. With `ap_isolate=1` this routes client↔client traffic through the Pi (RFC 3069 style). Without it, isolated clients cannot reach each other at all. Without `ap_isolate`, INA cannot block them from each other.
- Segmentation offloads off on `ap0`, so HTB bandwidth shaping is accurate.

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

## 11. Configuring `nftables` (NAT + INA filter table)

> **Safety Warning:** Do NOT add `flush ruleset` to `/etc/nftables.conf`. A blanket flush wipes unrelated firewall rules (Docker, fail2ban). INA uses named tables only: `ip ina_nat` and `inet ina_filter`.

Install the NAT configuration and the INA filter table:

```bash
sudo install -m 0644 config/nftables/nftables.conf /etc/nftables.conf
sudo install -d -m 0755 /etc/nftables.d
sudo install -m 0644 config/nftables/ina-filter.nft /etc/nftables.d/ina-filter.nft
sudo nft -c -f /etc/nftables.conf && sudo nft -f /etc/nftables.conf
```

`/etc/nftables.conf` defines `table ip ina_nat` (masquerade `192.168.50.0/24` out of `wlan0`) and includes `/etc/nftables.d/ina-filter.nft`. That file is the `inet ina_filter` table: the block/isolation sets plus the `prerouting` (anti-spoofing), `forward` (client↔client, Internet) and `input` (Pi services; DHCP/DNS always allowed) chains that make the sets take effect.

Verify both tables, and that the filter chains exist (the sets alone do nothing):

```bash
sudo nft list table ip ina_nat
sudo nft list chain inet ina_filter forward     # must list the @blocked_pairs/@blocked_internet/@isolated rules
```

Reloading nftables recreates `ina_filter` with empty sets. `ina-reconcile.service` (§15) is `PartOf=nftables.service`, so it re-applies the saved blocks, bans and bindings automatically when nftables restarts.

---

## 12. Enabling and Starting Services

Enable all services for automatic boot persistence:

```bash
sudo systemctl enable ina-ap-interface.service hostapd.service dnsmasq.service nftables.service

sudo systemctl start ina-ap-interface.service
sudo systemctl restart hostapd.service dnsmasq.service nftables.service
```

Check status:

```bash
systemctl is-active ina-ap-interface hostapd dnsmasq nftables
# Expected output for each: active
sudo hostapd_cli -i ap0 ping          # Expected: PONG (control socket for kick/ban)
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

---

## 15. Installing the INA Application

This section installs the INA software on the router set up above. The design and the security reasoning behind each piece are in [privilege_boundary.md](privilege_boundary.md).

### 15.1 Code, virtualenv and kernel modules

```bash
cd /opt/ina/inav2
sudo chown -R root:root /opt/ina                       # 'ina' must not be able to modify code that runs as root
sudo python3 -m venv /opt/ina/venv
sudo /opt/ina/venv/bin/pip install -r requirements.txt
sudo install -m 0644 config/modules-load.d/ina.conf /etc/modules-load.d/ina.conf
sudo systemctl restart systemd-modules-load            # ifb, sch_htb, sch_sfq, cls_flower, act_mirred
```

### 15.2 Service account, privilege boundary and runtime directories

```bash
sudo useradd --system --create-home --shell /bin/sh ina
sudo install -o root -g root -m 0755 config/scripts/ina-netctl /usr/local/sbin/ina-netctl
sudo install -o root -g root -m 0755 config/scripts/ina-mcp    /usr/local/bin/ina-mcp
sudo visudo -cf config/sudoers.d/ina && sudo install -o root -g root -m 0440 config/sudoers.d/ina /etc/sudoers.d/ina
sudo install -m 0644 config/tmpfiles.d/ina.conf /etc/tmpfiles.d/ina.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/ina.conf   # /run/ina, /var/lib/ina, /var/log/ina, /var/log/ina-mcp
```

### 15.3 Boot-time reconcile and ban expiry

```bash
sudo install -m 0644 config/systemd/ina-reconcile.service config/systemd/ina-expire.service \
     config/systemd/ina-expire.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ina-reconcile.service ina-expire.timer
```

### 15.4 Register the real devices

Edit `policy/devices.yaml` (name → MAC → class, optional fixed IP) and `policy/rules.yaml` → `protected_macs`. **Replace every example MAC (`aa:bb:cc:dd:ee:..`).** Phones and laptops usually use a per-network private MAC. Read the one shown for the `INA-Router` network, or take it from the lease file:

```bash
cat /var/lib/misc/dnsmasq.leases      # <expiry> <mac> <ip> <hostname> ...
sudo nano /opt/ina/inav2/policy/devices.yaml
sudo nano /opt/ina/inav2/policy/rules.yaml
sudo ina-netctl sync-devices          # writes /etc/ina/dhcp-hosts, binds fixed IPs, SIGHUPs dnsmasq
```

`sync-devices` refuses to run while example MACs remain. Devices pick up their new name at their next DHCP renewal; reconnecting them (or `kick_client`) makes it immediate. Until then INA still finds a registered device by its MAC.

### 15.5 Role tokens and operator access

Create one random token per role, give the plain tokens to the people who need them, and store only their SHA-256 hashes on the Pi:

```bash
for role in admin operator viewer; do
  t=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
  echo "$role token (give to the $role, store safely): $t"
  echo "INA_$(echo $role | tr a-z A-Z)_TOKEN_SHA256=$(printf %s "$t" | sha256sum | cut -d' ' -f1)" | sudo tee -a /etc/ina/mcp.env >/dev/null
done
sudo chown root:ina /etc/ina/mcp.env && sudo chmod 0640 /etc/ina/mcp.env
```

For each operator laptop, add one line to `~ina/.ssh/authorized_keys` (owned by `ina`, mode 0600). The key is bound to a role through its token:

```text
restrict,command="INA_ROLE_TOKEN=<operator-token> /usr/local/bin/ina-mcp" ssh-ed25519 AAAA... operator-laptop
```

On the laptop: `python -m assistant --server mcp --mcp-command "ssh ina@ina-router"`.

### 15.6 Verify the installation

```bash
sudo -u ina sudo -n /usr/local/sbin/ina-netctl --json --role viewer status --   # {"ok": true, ...}
sudo -u ina nft list ruleset 2>&1 | head -1                                     # must FAIL: 'ina' has no privileges
sudo -u ina sudo -n /usr/sbin/nft list ruleset 2>&1 | head -1                   # must FAIL: only ina-netctl is allowed
sudo nft list chain inet ina_filter forward | grep -c '@'                       # > 0: filter rules loaded
systemctl is-active ina-reconcile.service ina-expire.timer                      # active, active
```

Then run the functional check from a client (here `guest-phone`, registered in §15.4):

```bash
sudo ina-netctl block guest-phone internet    # guest-phone: browsing stops, Wi-Fi and DHCP stay up
sudo ina-netctl unblock guest-phone internet  # browsing resumes
sudo ina-netctl limit guest-phone 5000 5000   # iperf3 from guest-phone tops out near 5 Mbit/s
sudo ina-netctl unlimit guest-phone
sudo reboot                                   # afterwards: sudo nft list set inet ina_filter blocked_internet
```

Record the results in [testing.md](testing.md).

---
