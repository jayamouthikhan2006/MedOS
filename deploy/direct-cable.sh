#!/usr/bin/env bash
# Connect a laptop to the Pi with just an Ethernet cable (no router, no Wi-Fi).
# The Pi hands the laptop an address and is reachable at http://10.43.0.1
#
#   sudo bash deploy/direct-cable.sh        # enable
#   sudo bash deploy/direct-cable.sh off    # disable
#
# If the Pi is also plugged into a router, the normal wired connection still wins; this profile
# is used when no router answers (after about a minute).
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo"; exit 1; }
command -v nmcli >/dev/null || { echo "NetworkManager (nmcli) is required."; exit 1; }

if [ "${1:-}" = "off" ]; then
  nmcli con delete medos-direct 2>/dev/null || true
  echo "Direct-cable profile removed."
  exit 0
fi

nmcli con delete medos-direct 2>/dev/null || true
nmcli con add type ethernet ifname eth0 con-name medos-direct autoconnect yes \
  ipv4.method shared ipv4.addresses 10.43.0.1/24 ipv6.method ignore \
  connection.autoconnect-priority -10
echo "Plug the laptop into the Pi's Ethernet port. After about a minute open http://10.43.0.1"
