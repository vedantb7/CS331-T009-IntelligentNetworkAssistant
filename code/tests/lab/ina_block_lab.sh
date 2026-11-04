#!/bin/sh
# INA blocking lab: real nftables, private namespaces, no root needed.
#   unshare -r -n -m sh tests/lab/ina_block_lab.sh
# Topology (as in the research lab; bridge port isolation == hostapd ap_isolate=1):
#   ns a 192.168.50.20 / ns b .21 -- ns air (isolated bridge) -- ns pi: ap0 .1, wlan0 -- ns wan "internet"
# No ina_filter table is pre-loaded: INA's own block_client must install it.
set -u
REPO=$(cd "$(dirname "$0")/../../.." && pwd)        # directory containing inav2/
S=$(mktemp -d)
mount -t tmpfs none /run && mkdir -p /run/netns
for n in air pi a b wan; do ip netns add $n; done
X() { n=$1; shift; ip netns exec "$n" "$@"; }
P() { if X "$1" ping -c 2 -W 1 -i 0.2 "$2" >/dev/null 2>&1; then echo reach; else echo BLOCKED; fi; }
# --- topology from the research lab (ap_isolate == bridge port isolation) ---
X air ip link add br-air type bridge; X air ip link set br-air up
ip link add a-eth0 netns a type veth peer name a-air netns air
ip link add b-eth0 netns b type veth peer name b-air netns air
ip link add ap0 netns pi type veth peer name ap-air netns air
ip link add wlan0 netns pi type veth peer name up0 netns wan
for p in a-air b-air ap-air; do X air ip link set $p master br-air up; done
X air bridge link set dev a-air isolated on; X air bridge link set dev b-air isolated on
for c in a:20:02 b:21:03; do
  n=${c%%:*}; rest=${c#*:}; o=${rest%%:*}; mac=${rest#*:}
  X $n ip link set ${n}-eth0 name eth0; X $n ip link set eth0 address 02:00:00:00:00:$mac
  X $n ip addr add 192.168.50.$o/24 dev eth0; X $n ip link set eth0 up; X $n ip link set lo up
  X $n ip route add default via 192.168.50.1
done
X pi ip link set lo up; X pi ip addr add 192.168.50.1/24 dev ap0; X pi ip link set ap0 up
X pi ip addr add 10.0.0.2/24 dev wlan0; X pi ip link set wlan0 up; X pi ip route add default via 10.0.0.1
X wan ip link set lo up; X wan ip addr add 10.0.0.1/24 dev up0; X wan ip link set up0 up
X wan ip addr add 198.51.100.1/32 dev lo
X pi sysctl -qw net.ipv4.ip_forward=1; X pi sysctl -qw net.ipv4.conf.ap0.proxy_arp_pvlan=1
# NAT exactly as shipped (config/nftables/nftables.conf), minus the include of the filter table.
grep -v '^include' "$REPO/inav2/config/nftables/nftables.conf" | X pi nft -f -
# --- NO ina_filter table loaded: INA must install it itself (fix #1) ---
svc() { X pi env INA_COMMAND_LOG="$S/e2e_cmd.jsonl" python3 -c "
import sys; sys.path.insert(0, '$REPO')
from inav2.network.service import NetworkService
s = NetworkService(leases_map={'guest-a': '192.168.50.20', 'guest-b': '192.168.50.21'},
                   lock_path='$S/e2e.lock', state_path='$S/e2e_state.json', audit_path='$S/e2e_audit.log')
r = getattr(s, sys.argv[1])(*sys.argv[2:], role='admin')
print('   ', sys.argv[1:], '->', r.message)" "$@"; }
rm -f $S/e2e_state.json $S/e2e_audit.log
echo "baseline:              a->internet $(P a 198.51.100.1)   a->b $(P a 192.168.50.21)"
svc block guest-a internet
echo "after block internet:  a->internet $(P a 198.51.100.1)   a->b $(P a 192.168.50.21)   b->internet $(P b 198.51.100.1)"
svc unblock guest-a internet
echo "after unblock:         a->internet $(P a 198.51.100.1)"
svc block guest-a guest-b
echo "after block a<->b:     a->b $(P a 192.168.50.21)   b->a $(P b 192.168.50.20)   a->internet $(P a 198.51.100.1)"
svc block guest-b all
echo "after isolate b:       b->internet $(P b 198.51.100.1)   b->pi $(P b 192.168.50.1)"
echo "forward chain counters:"; X pi nft list chain inet ina_filter forward | grep -E "counter packets [1-9]" | sed 's/^/   /'
