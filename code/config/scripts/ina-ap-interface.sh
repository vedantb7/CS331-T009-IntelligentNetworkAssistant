#!/bin/sh
# /usr/local/sbin/ina-ap-interface.sh -- bring up ap0 and the kernel settings INA relies on
#
# Run by ina-ap-interface.service before hostapd and dnsmasq.
#   - ap0 is a virtual AP interface on the onboard radio (wlan0 stays the uplink).
#   - proxy_arp_pvlan + ap_isolate=1 (hostapd) route client<->client traffic
#     through the Pi, so nftables can block it; without them the radio would
#     bridge clients directly and pairwise blocks would have no effect.
#   - segmentation offloads off so HTB shaping sees real packet sizes.
set -eu

IFACE=ap0
PHYS=wlan0
ROUTER_IP=192.168.50.1/24

# Create ap0 on the onboard radio if it does not exist yet
if ! ip link show "$IFACE" >/dev/null 2>&1; then
    iw dev "$PHYS" interface add "$IFACE" type __ap
fi

ip link set dev "$IFACE" up
ip -4 addr show dev "$IFACE" | grep -q "$ROUTER_IP" || ip addr add "$ROUTER_IP" dev "$IFACE"

# IPv4 forwarding and proxy ARP for the routed hairpin
sysctl -qw net.ipv4.ip_forward=1
sysctl -qw net.ipv4.conf.all.send_redirects=0
sysctl -qw net.ipv4.conf."$IFACE".send_redirects=0
sysctl -qw net.ipv4.conf."$IFACE".proxy_arp_pvlan=1
sysctl -qw net.ipv6.conf."$IFACE".forwarding=0

# Disable segmentation offloads to keep HTB shaping accurate
ethtool -K "$IFACE" gro off gso off tso off 2>/dev/null || true
