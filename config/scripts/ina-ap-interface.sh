#!/bin/sh
# /usr/local/sbin/ina-ap-interface.sh -- Interface bring-up and sysctl configuration for INA v2
set -eu

IFACE=ap0

# Ensure interface is up
ip link show "$IFACE" >/dev/null
ip link set dev "$IFACE" up
ip -4 addr show dev "$IFACE" | grep -q 192.168.50.1/24 || ip addr add 192.168.50.1/24 dev "$IFACE"

# Enable IPv4 forwarding and proxy ARP for routed hairpin
sysctl -qw net.ipv4.ip_forward=1
sysctl -qw net.ipv4.conf.all.send_redirects=0
sysctl -qw net.ipv4.conf."$IFACE".send_redirects=0
sysctl -qw net.ipv4.conf."$IFACE".proxy_arp_pvlan=1
sysctl -qw net.ipv6.conf."$IFACE".forwarding=0

# Disable segmentation offloads to prevent buffer overflow in HTB shaping
ethtool -K "$IFACE" gro off gso off tso off 2>/dev/null || true

