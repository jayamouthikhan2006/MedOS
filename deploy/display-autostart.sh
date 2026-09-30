#!/usr/bin/env bash
# Open a MedOS screen full-screen on the Pi's HDMI monitor every time the desktop starts.
#
#   bash deploy/display-autostart.sh                  # waiting-room display
#   PAGE=kiosk bash deploy/display-autostart.sh       # self check-in kiosk (touchscreen)
#
# Arguments (used by install.sh): [user] [home] [port]
set -euo pipefail
APP_USER="${1:-$(whoami)}"
APP_HOME="${2:-$HOME}"
PORT="${3:-80}"
PAGE="${PAGE:-display}"
URL="http://localhost$([ "$PORT" = "80" ] || echo ":$PORT")/$PAGE"

BROWSER="$(command -v chromium-browser || command -v chromium || true)"
if [ -z "$BROWSER" ]; then
  echo "Chromium is not installed. Install it with: sudo apt install chromium-browser"
  exit 1
fi

LAUNCH="$APP_HOME/.local/bin/medos-screen"
mkdir -p "$(dirname "$LAUNCH")" "$APP_HOME/.config/autostart"
cat > "$LAUNCH" <<EOF
#!/bin/sh
# Wait for MedOS to answer, then open it full-screen with sound allowed.
for i in \$(seq 1 60); do curl -s -o /dev/null "$URL" && break; sleep 2; done
exec "$BROWSER" --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble \\
  --autoplay-policy=no-user-gesture-required --check-for-update-interval=31536000 \\
  --password-store=basic "$URL"
EOF
chmod +x "$LAUNCH"

# XDG autostart (LXDE / X11 and the Wayland sessions on Bookworm)
cat > "$APP_HOME/.config/autostart/medos-screen.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=MedOS screen
Exec=$LAUNCH
X-GNOME-Autostart-enabled=true
EOF

# labwc (Raspberry Pi OS Bookworm, late 2024 onwards)
if [ -d "$APP_HOME/.config/labwc" ] || command -v labwc >/dev/null 2>&1; then
  mkdir -p "$APP_HOME/.config/labwc"
  touch "$APP_HOME/.config/labwc/autostart"
  grep -q medos-screen "$APP_HOME/.config/labwc/autostart" || echo "$LAUNCH &" >> "$APP_HOME/.config/labwc/autostart"
fi

chown -R "$APP_USER":"$APP_USER" "$APP_HOME/.local" "$APP_HOME/.config" 2>/dev/null || true
echo "The $PAGE screen will open on the HDMI monitor after the next login or reboot ($URL)."
