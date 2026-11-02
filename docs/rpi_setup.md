# Raspberry Pi 5 Setup (Router + INA Server)

This is a command-by-command runbook that turns a fresh Raspberry Pi 5 into the INA router and MCP server. Run the sections in order. [setup.md](setup.md) explains each step in more detail. The operator laptop side is in [laptop_setup.md](laptop_setup.md).

**Result:**

- Wi-Fi AP `INA-Router` on `ap0` (`192.168.50.1/24`), with the uplink on `wlan0` (phone hotspot);
- DHCP/DNS, NAT and the INA filter table;
- `ina-netctl` as the only privileged entry point;
- the MCP server on `https://192.168.50.1:8443/mcp`.

---

## 0. Flash the SD card (on any computer)

Use **Raspberry Pi Imager** with:

- **Device:** Raspberry Pi 5
- **OS:** Raspberry Pi OS Lite (64-bit), Debian Trixie
- **Hostname:** `ina-router`
- **User:** `team009`, with a strong password
- **Wireless LAN country:** your country (e.g. `IN`)
- **SSH:** enabled

Boot the Pi and log in from a computer on the same network:

```bash
ssh team009@ina-router.local
uname -a && cat /etc/os-release
```

---

## 1. Uplink (`wlan0`) to the phone hotspot

```bash
sudo nmcli dev wifi list
sudo nmcli dev wifi connect "<HOTSPOT_SSID>" password "<HOTSPOT_PASSWORD>"
ip -4 addr show dev wlan0
ip route show default
ping -c 3 8.8.8.8                          # do not continue until this works
```

---

## 2. Packages and the INA code

```bash
sudo apt update
sudo apt install -y hostapd dnsmasq nftables iw iperf3 ethtool git python3-venv openssl curl
sudo systemctl unmask hostapd

sudo install -d -o root -g root -m 0755 /opt/ina
sudo git clone https://github.com/vedantb7/inav2.git /opt/ina/inav2     # or your repository URL
cd /opt/ina/inav2
```

---

## 3. Access point (`hostapd`)

```bash
cd /opt/ina/inav2
sudo install -m 0600 config/hostapd/hostapd.conf.template /etc/hostapd/hostapd.conf
sudo sed -i 's|^wpa_passphrase=.*|wpa_passphrase=<YOUR_STRONG_AP_PASSPHRASE>|' /etc/hostapd/hostapd.conf
sudo sed -i 's|^country_code=.*|country_code=IN|' /etc/hostapd/hostapd.conf          # your country
sudo install -o root -g root -m 0600 /dev/null /etc/hostapd/hostapd.deny             # ban list (must exist)
sudo sed -i 's|^#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
grep -E '^(interface|ssid|channel|ap_isolate|ctrl_interface|deny_mac_file)=' /etc/hostapd/hostapd.conf
```

---

## 4. DHCP and DNS (`dnsmasq`)

```bash
cd /opt/ina/inav2
sudo mv /etc/dnsmasq.conf /etc/dnsmasq.conf.bak 2>/dev/null || true
sudo install -m 0644 config/dnsmasq/ina-ap.conf /etc/dnsmasq.d/ina-ap.conf
sudo install -d -o root -g root -m 0755 /etc/ina
sudo install -m 0644 /dev/null /etc/ina/dhcp-hosts           # reservations; filled by sync-devices (§8)
sudo dnsmasq --test --conf-file=/etc/dnsmasq.d/ina-ap.conf   # dnsmasq: syntax check OK.
```

---

## 5. The `ap0` interface, boot ordering and forwarding

```bash
cd /opt/ina/inav2
# keep NetworkManager off ap0 (otherwise it strips 192.168.50.1 and dnsmasq fails at boot)
sudo install -o root -g root -m 0644 config/NetworkManager/90-ina-unmanaged.conf /etc/NetworkManager/conf.d/90-ina-unmanaged.conf
sudo systemctl reload NetworkManager
sudo install -o root -g root -m 0755 config/scripts/ina-ap-interface.sh /usr/local/sbin/ina-ap-interface.sh

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

sudo mkdir -p /etc/systemd/system/hostapd.service.d /etc/systemd/system/dnsmasq.service.d
sudo tee /etc/systemd/system/hostapd.service.d/override.conf >/dev/null <<'EOF'
[Unit]
BindsTo=sys-subsystem-net-devices-ap0.device
After=sys-subsystem-net-devices-ap0.device ina-ap-interface.service
EOF
sudo tee /etc/systemd/system/dnsmasq.service.d/override.conf >/dev/null <<'EOF'
[Unit]
After=ina-ap-interface.service hostapd.service
EOF

echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-ina-router.conf >/dev/null
sudo sysctl --system >/dev/null && sysctl net.ipv4.ip_forward     # net.ipv4.ip_forward = 1
sudo systemctl daemon-reload
```

---

## 6. NAT and the INA filter table (`nftables`)

Never add `flush ruleset`: INA only manages its own named tables.

```bash
cd /opt/ina/inav2
sudo install -m 0644 config/nftables/nftables.conf /etc/nftables.conf
sudo install -d -m 0755 /etc/nftables.d
sudo install -m 0644 config/nftables/ina-filter.nft /etc/nftables.d/ina-filter.nft
sudo nft -c -f /etc/nftables.conf && sudo nft -f /etc/nftables.conf
sudo nft list table ip ina_nat
sudo nft list chain inet ina_filter forward        # must list the @blocked_* / @isolated rules
```

---

## 7. Start the router services and check

```bash
sudo systemctl enable ina-ap-interface.service hostapd.service dnsmasq.service nftables.service
sudo systemctl start ina-ap-interface.service
sudo systemctl restart hostapd.service dnsmasq.service nftables.service
systemctl is-active ina-ap-interface hostapd dnsmasq nftables     # active x4
ip -4 addr show ap0                                               # 192.168.50.1/24
sysctl net.ipv4.conf.ap0.proxy_arp_pvlan                          # = 1
sudo hostapd_cli -i ap0 ping                                      # PONG
```

Connect a laptop or phone to `INA-Router`. It should get an address in `192.168.50.10–100`, and be able to ping `192.168.50.1` and `8.8.8.8`.

```bash
cat /var/lib/misc/dnsmasq.leases                                  # <expiry> <mac> <ip> <hostname> ...
```

---

## 8. Install the INA application

### 8.1 Code, virtualenv, kernel modules

```bash
cd /opt/ina/inav2
sudo chown -R root:root /opt/ina                   # 'ina' must not be able to modify code that runs as root
sudo python3 -m venv /opt/ina/venv
sudo /opt/ina/venv/bin/pip install -r requirements.txt
sudo install -m 0644 config/modules-load.d/ina.conf /etc/modules-load.d/ina.conf
sudo systemctl restart systemd-modules-load
lsmod | grep -E 'ifb|sch_htb|sch_sfq|cls_flower|act_mirred'
```

### 8.2 Service account, privilege boundary, runtime directories

```bash
cd /opt/ina/inav2
sudo useradd --system --create-home --shell /bin/sh ina
sudo install -o root -g root -m 0755 config/scripts/ina-netctl /usr/local/sbin/ina-netctl
sudo install -o root -g root -m 0755 config/scripts/ina-mcp    /usr/local/bin/ina-mcp
sudo visudo -cf config/sudoers.d/ina && sudo install -o root -g root -m 0440 config/sudoers.d/ina /etc/sudoers.d/ina
sudo install -m 0644 config/tmpfiles.d/ina.conf /etc/tmpfiles.d/ina.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/ina.conf
sudo -u ina sudo -n /usr/local/sbin/ina-netctl --json --role viewer status --     # {"ok": true, ...}
```

### 8.3 Reconcile on boot, ban expiry

```bash
cd /opt/ina/inav2
sudo install -m 0644 config/systemd/ina-reconcile.service config/systemd/ina-expire.service \
     config/systemd/ina-expire.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ina-reconcile.service ina-expire.timer
```

### 8.4 Register the devices

Every connected device is a normal **client** by default. Register the devices you want to name, give a fixed IP, or **protect** (the admins' laptops). Read their MACs while they are connected:

```bash
cat /var/lib/misc/dnsmasq.leases
sudo nano /opt/ina/inav2/policy/devices.yaml
```

Replace every example MAC (`aa:bb:cc:dd:ee:..`). For example:

```yaml
devices:
  - {name: pi-gateway,   mac: "<ap0 MAC: cat /sys/class/net/ap0/address>", class: protected, ip: "192.168.50.1"}
  - {name: admin-laptop, mac: "<laptop MAC on INA-Router>", class: protected, ip: "192.168.50.2"}
  - {name: guest-phone,  mac: "<phone MAC>", class: client}
```

Also put the protected MACs into `rules.yaml` → `protected_macs`, then apply:

```bash
sudo nano /opt/ina/inav2/policy/rules.yaml
sudo ina-netctl sync-devices                       # reservations, MAC<->IP binding, dnsmasq reload
cat /etc/ina/dhcp-hosts
sudo nft list set inet ina_filter mac_ip_bind
```

Reconnect the registered devices so they pick up their reservation. The laptop should now have `192.168.50.2`.

### 8.5 Grant roles (manual, per person, bound to a device)

```bash
sudo install -o root -g ina -m 0640 /dev/null /etc/ina/grants.json
sudo ina-netctl roles grant dhruv admin --device admin-laptop
#   dhruv now has role 'admin', usable only from device 'admin-laptop'.
#   Token (shown once - give it to dhruv privately):
#     <token>
sudo ina-netctl roles list
```

Other commands:

```bash
sudo ina-netctl roles grant khushi operator --device khushi-laptop
sudo ina-netctl roles rotate dhruv          # new token, old one stops working
sudo ina-netctl roles revoke khushi
```

Each grant's device must be registered with a fixed `ip` in `devices.yaml`.

### 8.6 TLS certificate and the MCP server

```bash
cd /opt/ina/inav2
sudo install -d -o root -g ina -m 0750 /etc/ina/tls
sudo openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 825 \
     -subj "/CN=ina-router" \
     -addext "subjectAltName=IP:192.168.50.1,DNS:ina-router,DNS:ina-router.local" \
     -keyout /etc/ina/tls/server.key -out /etc/ina/tls/server.crt
sudo chown root:ina /etc/ina/tls/server.key && sudo chmod 0640 /etc/ina/tls/server.key
sudo chmod 0644 /etc/ina/tls/server.crt

sudo install -m 0644 config/systemd/ina-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ina-mcp.service
systemctl is-active ina-mcp.service                                # active
sudo ss -ltnp | grep 8443                                         # listening on 192.168.50.1:8443
```

Operators copy `/etc/ina/tls/server.crt` to their laptop ([laptop_setup.md](laptop_setup.md) §5.3).

---

## 9. Verify the installation

```bash
# privilege boundary
sudo -u ina sudo -n /usr/local/sbin/ina-netctl --json --role viewer status --   # {"ok": true, ...}
sudo -u ina nft list ruleset 2>&1 | head -1                                     # must FAIL
sudo -u ina sudo -n /usr/sbin/nft list ruleset 2>&1 | head -1                   # must FAIL
ls -ld /opt/ina /opt/ina/venv /usr/local/sbin/ina-netctl                        # root-owned

# services and filter table
systemctl is-active ina-ap-interface hostapd dnsmasq nftables ina-reconcile ina-mcp ina-expire.timer
sudo nft list chain inet ina_filter forward | grep -c '@'                       # > 0

# MCP endpoint (no token -> 401)
curl -s -o /dev/null -w "%{http_code}\n" --cacert /etc/ina/tls/server.crt -X POST \
     -H "Content-Type: application/json" -d '{}' https://192.168.50.1:8443/mcp

# functional checks with a client device (replace guest-phone with a registered client)
sudo ina-netctl block guest-phone internet       # browsing stops; Wi-Fi and DHCP stay up
sudo ina-netctl unblock guest-phone internet
sudo ina-netctl limit guest-phone 5000 5000      # iperf3 from the client: about 5 Mbit/s
sudo ina-netctl unlimit guest-phone
sudo ina-netctl kick guest-phone                 # the device reconnects
sudo ina-netctl ban guest-phone --minutes 2 --yes   # cannot join; rejoins after expiry

# persistence
sudo systemctl restart nftables && sudo nft list set inet ina_filter blocked_internet
sudo reboot
```

After the reboot, run `systemctl is-active` again and check that the saved blocks, limits and bans are back. Record the results in [testing.md](testing.md) §5.

---

## 10. Logs and day-to-day administration

```bash
sudo tail -f /var/log/ina-mcp/policy_audit.jsonl        # decisions (person, role, tool, rule)
sudo tail -f /var/log/ina/commands.jsonl                # every command with exit code and output
sudo tail -f /var/log/ina/audit.log                     # every change
journalctl -u ina-mcp -f                                # MCP server

sudo ina-netctl status                                  # current blocks, limits, bans
sudo ina-netctl sync-devices                            # after editing devices.yaml
sudo ina-netctl roles list                              # who has which role

# update the code
cd /opt/ina/inav2 && sudo git pull && sudo /opt/ina/venv/bin/pip install -r requirements.txt
sudo systemctl restart ina-mcp
```

When something fails, see [troubleshooting.md](troubleshooting.md).
