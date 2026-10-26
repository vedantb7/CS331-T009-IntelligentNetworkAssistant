# INA v2 Diagnostic & Troubleshooting Guide

This guide provides diagnostic procedures, verification commands, and safe corrective actions for common operational issues encountered on the **Raspberry Pi 5 (`ina-router`)** infrastructure and connected client devices.

---

## Diagnostic Methodology

When troubleshooting, always follow this order of operations:
1. **Physical & Link Layer:** Verify that `wlan0` and `ap0` interfaces exist and report operational state `UP`.
2. **Service State:** Verify that `ina-ap-interface`, `hostapd`, `dnsmasq`, and `nftables` report state `active`.
3. **Local Addressing & DHCP:** Verify that the client obtained an IP in `192.168.50.0/24` and can ping `192.168.50.1`.
4. **Kernel Forwarding & NAT:** Verify `net.ipv4.ip_forward = 1` and that `nftables` masquerade rule is loaded.
5. **Upstream Path:** Verify that the Pi itself can reach external destinations through `wlan0`.

---

## Troubleshooting Cases

### Case 1: `INA-Router` SSID Does Not Appear in Wi-Fi Scans

#### Symptoms
Client devices scan for Wi-Fi networks, but the SSID `INA-Router` is not listed.

#### Diagnostic Commands (Run on Pi)
```bash
# Check hostapd service status and recent journal logs
sudo systemctl status hostapd.service
sudo journalctl -u hostapd.service -n 30 --no-pager

# Check if ap0 interface exists and is up
ip link show ap0

# Check wireless device regulatory domain and channel
iw reg get
sudo iw dev ap0 info
```

#### Confirmed & Potential Causes
- **Confirmed Cause:** `hostapd` failed to start because `ap0` was not initialized prior to `hostapd` launch.
- **Potential Cause:** Regulatory domain mismatch blocking channel 11.
- **Potential Cause:** `hostapd` is masked by default on Debian/Raspberry Pi OS.

#### Corrective Actions
1. If `hostapd` is masked:
   ```bash
   sudo systemctl unmask hostapd
   sudo systemctl enable hostapd
   ```
2. Manually trigger interface creation and restart `hostapd`:
   ```bash
   sudo /usr/local/sbin/ina-ap-interface.sh
   sudo systemctl restart hostapd
   ```
3. If logs indicate channel configuration error, verify that country code is set in `/etc/default/crda` or `/etc/wpa_supplicant/wpa_supplicant.conf` (e.g. `country=IN` or `country=US`).

---

### Case 2: AP Appears, but Clients Cannot Obtain an IP Address

#### Symptoms
Client associates with `INA-Router` using the correct WPA2 passphrase, but stays stuck in "Obtaining IP address..." and eventually disconnects.

#### Diagnostic Commands (Run on Pi)
```bash
# Check dnsmasq service status and logs
sudo systemctl status dnsmasq.service
sudo journalctl -u dnsmasq.service -n 30 --no-pager

# Verify dnsmasq is bound to ap0 on UDP port 67
sudo ss -ulpn | grep -E ':(67|53)'

# Monitor live DHCP transactions while client attempts connection
sudo journalctl -u dnsmasq.service -f
```

#### Confirmed & Potential Causes
- **Confirmed Cause:** `dnsmasq` failed on boot because it attempted to bind to `ap0` before `ap0` was created.
- **Potential Cause:** IP pool exhaustion in `192.168.50.10`–`192.168.50.100`.
- **Potential Cause:** Port conflict: Another service (e.g., `systemd-resolved` or `NetworkManager` internal dnsmasq) is occupying port 53 or 67.

#### Corrective Actions
1. Restart `dnsmasq` to bind to `ap0`:
   ```bash
   sudo systemctl restart dnsmasq
   ```
2. Check `/etc/dnsmasq.d/ina-ap.conf` and ensure `bind-interfaces` and `listen-address=192.168.50.1` are present.
3. Verify that `/var/lib/misc/dnsmasq.leases` is writable by user `dnsmasq`.

---

### Case 3: `ap0` Interface is Missing or Lacks `192.168.50.1/24`

#### Symptoms
Running `ip addr show dev ap0` outputs `Device "ap0" does not exist` or shows no assigned IPv4 address.

#### Diagnostic Commands (Run on Pi)
```bash
# Check if wlan0 physical interface exists
ip link show wlan0

# Check if the initialization script executed
sudo systemctl status ina-ap-interface.service
sudo journalctl -u ina-ap-interface.service -n 30 --no-pager
```

#### Confirmed & Potential Causes
- **Confirmed Cause:** `ina-ap-interface.service` failed to execute on boot or ran before `wlan0` was initialized by the kernel.
- **Potential Cause:** `ina-ap-interface.sh` lacked execute permissions (`+x`).

#### Corrective Actions
1. Grant execute permissions and run script manually:
   ```bash
   sudo chmod +x /usr/local/sbin/ina-ap-interface.sh
   sudo /usr/local/sbin/ina-ap-interface.sh
   ```
2. Verify interface and IP:
   ```bash
   ip -4 addr show dev ap0
   ```
3. Restart dependent services:
   ```bash
   sudo systemctl restart hostapd dnsmasq
   ```

---

### Case 4: `dnsmasq` Logs Report "ap0 Does Not Exist" on Boot

#### Symptoms
System logs during boot show `dnsmasq[...]: unknown interface ap0` or `dnsmasq[...]: failed to create listening socket for 192.168.50.1: Cannot assign requested address`.

#### Diagnostic Commands (Run on Pi)
```bash
# Check systemd dependency graph for dnsmasq
systemctl list-dependencies dnsmasq.service

# Inspect drop-in override
cat /etc/systemd/system/dnsmasq.service.d/override.conf
```

#### Confirmed & Potential Causes
- **Confirmed Cause:** Race condition during boot: systemd started `dnsmasq` before `ina-ap-interface.service` finished creating `ap0`.

#### Corrective Actions
1. Ensure the drop-in override exists at `/etc/systemd/system/dnsmasq.service.d/override.conf`:
   ```ini
   [Unit]
   After=ina-ap-interface.service hostapd.service
   ```
2. Reload systemd daemon:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl restart dnsmasq
   ```

---

### Case 5: Client Associates but DHCP Times Out (Client-Side Firewall)

#### Symptoms
Pi logs indicate that `dnsmasq` sent a `DHCPOFFER` and `DHCPACK`, but the client machine does not assign the IP address.

#### Diagnostic Commands (Run on Client Workstation)
```bash
# Inspect local Wi-Fi connection logs (Fedora / NetworkManager)
journalctl -u NetworkManager -n 40 --no-pager

# Check local firewall status on client
sudo firewall-cmd --state
```

#### Confirmed & Potential Causes
- **Confirmed Cause:** The client machine's local firewall (e.g., `firewalld` or `ufw`) is dropping inbound DHCP responses on UDP port 68.
- **Potential Cause:** Client Wi-Fi driver is flapping or rejecting the 802.11 GTK/PTK handshake.

#### Corrective Actions
1. Ensure DHCP client traffic is permitted in `firewalld`:
   ```bash
   sudo firewall-cmd --add-service=dhcp --permanent
   sudo firewall-cmd --reload
   ```
2. Disconnect and re-connect client Wi-Fi:
   ```bash
   nmcli connection down INA-Router && nmcli connection up INA-Router
   ```

---

### Case 6: Local Ping to `192.168.50.1` Fails (Connected to Wrong Network)

#### Symptoms
Running `ping 192.168.50.1` on the client fails with `Destination Host Unreachable` or `Request timed out`.

#### Diagnostic Commands (Run on Client Workstation)
```bash
# Check active SSID and IP on the client
nmcli -t -f active,ssid,dev dev wifi | grep '^yes'
ip -4 addr show

# Check client routing table
ip route show default
```

#### Confirmed & Potential Causes
- **Confirmed Cause:** Client silently reconnected or fell back to another known Wi-Fi network (e.g. campus network, phone hotspot, or home Wi-Fi).
- **Potential Cause:** Subnet overlap with a local VPN (e.g. Cloudflare WARP or corporate VPN).

#### Corrective Actions
1. Explicitly connect the client to `INA-Router`:
   ```bash
   nmcli dev wifi connect "INA-Router"
   ```
2. Temporarily pause or disconnect any active client VPN tunnels that might intercept `192.168.50.0/24`.
3. Confirm that the default route on the client points to `192.168.50.1`:
   ```bash
   ip route show default
   # Expected: default via 192.168.50.1 dev <client_wifi_iface>
   ```

---

### Case 7: Internet is Unavailable Despite Successful Local Ping to `192.168.50.1`

#### Symptoms
The client can ping `192.168.50.1` (0% loss), but cannot ping external IP addresses (e.g., `ping 8.8.8.8` fails) or resolve domain names.

#### Diagnostic Commands (Run on Pi)
```bash
# 1. Verify kernel IPv4 forwarding
sysctl net.ipv4.ip_forward

# 2. Verify nftables NAT masquerade table
sudo nft list table ip ina_nat

# 3. Verify Pi uplink connectivity to hotspot
ip route show default dev wlan0
ping -c 3 8.8.8.8
```

#### Confirmed & Potential Causes
- **Confirmed Cause:** `net.ipv4.ip_forward` is set to `0` (forwarding disabled in kernel).
- **Confirmed Cause:** `nftables` service is inactive or `table ip ina_nat` was not loaded.
- **Confirmed Cause:** The Pi lost its connection to the phone hotspot (`wlan0` disconnected or phone hotspot disabled).
- **Potential Cause:** Mobile phone hotspot lacks cellular data or has reached its tethering limit.

#### Corrective Actions
1. Re-enable forwarding if disabled:
   ```bash
   sudo sysctl -w net.ipv4.ip_forward=1
   sudo sysctl --system
   ```
2. Reload `nftables` NAT rules:
   ```bash
   sudo nft -f /etc/nftables.conf
   sudo systemctl restart nftables
   ```
3. Reconnect the Pi to the phone hotspot:
   ```bash
   sudo nmcli dev wifi connect "<HOTSPOT_SSID>" password "<HOTSPOT_PASSWORD>"
   ```

---

### Case 8: `iperf3` Reports "Address already in use"

#### Symptoms
Running `iperf3 -s` on the Raspberry Pi reports:
```text
iperf3: error - unable to start listener for connections: Address already in use
```

#### Diagnostic Commands (Run on Pi)
```bash
# Identify which process is listening on TCP port 5201
sudo ss -tulpn | grep 5201
```

#### Confirmed & Potential Causes
- **Confirmed Cause:** A previous `iperf3` instance is already running in the background or another terminal.

#### Corrective Actions
1. Terminate existing `iperf3` processes:
   ```bash
   sudo pkill -9 iperf3
   ```
2. Start `iperf3` cleanly in daemon mode or foreground:
   ```bash
   iperf3 -s -D  # Background daemon mode
   ```

---

### Case 9: `iperf3` Client Connection Times Out

#### Symptoms
Running `iperf3 -c 192.168.50.1` on the client hangs and outputs:
```text
iperf3: error - unable to connect to server: Connection timed out
```

#### Diagnostic Commands
```bash
# On Pi: Verify iperf3 is listening on 0.0.0.0:5201
sudo ss -tlpn | grep 5201

# On Client: Test TCP port reachability
nc -zv 192.168.50.1 5201
```

#### Confirmed & Potential Causes
- **Confirmed Cause:** `iperf3` server is not running on the Pi.
- **Potential Cause:** Local firewall on the Pi is filtering inbound TCP port 5201.

#### Corrective Actions
1. Start `iperf3` on the Pi:
   ```bash
   iperf3 -s
   ```
2. If `nftables` or `ufw` has an active input filter on the Pi, temporarily allow port 5201 on `ap0`:
   ```bash
   sudo nft add rule ip filter input iifname "ap0" tcp dport 5201 accept 2>/dev/null || true
   ```

---

### Case 10: System Services Fail to Start After Reboot

#### Symptoms
Running `systemctl is-active hostapd dnsmasq ina-ap-interface` reports `failed` or `inactive` for one or more services.

#### Diagnostic Commands (Run on Pi)
```bash
# Inspect failed units
systemctl --failed

# Inspect journal for specific failure reasons
sudo journalctl -xeu hostapd.service --no-pager
sudo journalctl -xeu dnsmasq.service --no-pager
```

#### Corrective Actions
1. Execute the manual bring-up sequence in order:
   ```bash
   sudo /usr/local/sbin/ina-ap-interface.sh
   sudo systemctl restart hostapd
   sudo systemctl restart dnsmasq
   sudo systemctl restart nftables
   ```
2. Re-verify systemd overrides using `systemctl daemon-reload`.

---

### Case 11: `nftables` Configuration Conflicts with Existing System Rules

#### Symptoms
Applying `sudo nft -f /etc/nftables.conf` reports syntax errors, or breaks other host applications.

#### Diagnostic Commands (Run on Pi)
```bash
# List all active nftables tables
sudo nft list tables

# Check existing ruleset safely without modifying it
sudo nft list ruleset
```

#### Confirmed & Potential Causes
- **Confirmed Cause:** `/etc/nftables.conf` contains `flush ruleset`, which wiped existing system tables.
- **Potential Cause:** Conflicting table family or chain priority collision.

#### Corrective Actions
1. Remove any `flush ruleset` directive from `/etc/nftables.conf`.
2. Inspect the current ruleset and ensure only `table ip ina_nat` is defined for INA.
3. Test loading the table file explicitly:
   ```bash
   sudo nft -c -f /etc/nftables.conf  # Syntax check only
   sudo nft -f /etc/nftables.conf     # Apply safely
   ```

