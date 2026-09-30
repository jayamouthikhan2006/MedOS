#!/usr/bin/env bash
# Turn the Pi into its own Wi-Fi network, so any laptop or phone can connect without a router.
#
#   sudo bash deploy/hotspot.sh                        # Wi-Fi "MedOS-Hospital", password medos1234
#   sudo bash deploy/hotspot.sh "Ward-3" "secret123"   # your own name and password (8+ characters)
#   sudo bash deploy/hotspot.sh off                    # back to normal Wi-Fi
#
# Needs NetworkManager (default on Raspberry Pi OS Bookworm).
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo"; exit 1; }
command -v nmcli >/dev/null || { echo "NetworkManager (nmcli) is required: Raspberry Pi OS Bookworm has it."; exit 1; }

if [ "${1:-}" = "off" ]; then
  nmcli con down medos-hotspot 2>/dev/null || true
  nmcli con delete medos-hotspot 2>/dev/null || true
  echo "Hotspot removed. The Pi will reconnect to its usual Wi-Fi."
  exit 0
fi

SSID="${1:-MedOS-Hospital}"
PASS="${2:-medos1234}"
if [ ${#PASS} -lt 8 ]; then echo "The password needs at least 8 characters"; exit 1; fi

nmcli con delete medos-hotspot 2>/dev/null || true
nmcli con add type wifi ifname wlan0 con-name medos-hotspot autoconnect yes ssid "$SSID" \
  802-11-wireless.mode ap 802-11-wireless.band bg \
  ipv4.method shared ipv4.addresses 10.42.0.1/24 ipv6.method ignore \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PASS"
nmcli con modify medos-hotspot connection.autoconnect-priority 100
nmcli con up medos-hotspot

echo
echo "Wi-Fi network : $SSID"
echo "Password      : $PASS"
echo "Open MedOS    : http://10.42.0.1   (or http://$(hostname).local)"
echo
echo "To use the Qwen model on a laptop: connect the laptop to $SSID, run laptop\\enable-ollama-lan.ps1"
echo "on it once, then press Admin > AI assistant > Find on network."
