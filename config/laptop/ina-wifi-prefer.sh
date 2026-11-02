#!/bin/sh
# ina-wifi-prefer -- keep the operator laptop on the INA router's Wi-Fi.
#
# When the Pi reboots, INA-Router disappears for a minute and NetworkManager
# falls back to another known network (e.g. the phone hotspot). It does not
# switch back by itself once INA-Router returns, so the assistant can no longer
# reach https://192.168.50.1:8443. This script switches back.
#
# Run every 30 s by the user timer ina-wifi-prefer.timer (no root needed:
# NetworkManager lets the logged-in user activate their own connections).
#
#   INA_WIFI_CONNECTION   NetworkManager connection name (default: INA-Router)
#   INA_WIFI_SSID         SSID broadcast by the Pi        (default: INA-Router)
set -u

CONN="${INA_WIFI_CONNECTION:-INA-Router}"
SSID="${INA_WIFI_SSID:-INA-Router}"

command -v nmcli >/dev/null 2>&1 || exit 0
nmcli -t -f NAME connection show | grep -Fxq "$CONN" || exit 0

# Already on it: nothing to do.
nmcli -t -f NAME connection show --active | grep -Fxq "$CONN" && exit 0

# Only switch when the Pi's network is actually in range.
nmcli -t -f SSID device wifi list --rescan auto 2>/dev/null | grep -Fxq "$SSID" || exit 0

logger -t ina-wifi-prefer "INA-Router is back in range; switching to '$CONN'"
nmcli --wait 30 connection up "$CONN" >/dev/null 2>&1 \
    || logger -t ina-wifi-prefer "could not activate '$CONN'"
exit 0
