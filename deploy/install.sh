#!/usr/bin/env bash
# MedOS installer for Raspberry Pi OS (Bookworm recommended, Bullseye works) on a Pi 4 / Pi 5.
#
#   cd ~/medos
#   sudo bash deploy/install.sh              # everything: service on port 80, voice, HTTPS
#   sudo bash deploy/install.sh --display    # also open the waiting-room display on the HDMI screen at boot
#   sudo bash deploy/install.sh --no-voice   # skip the offline speech model (saves ~40 MB download)
#
# Afterwards open  http://medos.local  from any laptop or phone on the same network.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="${SUDO_USER:-$(logname 2>/dev/null || whoami)}"
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
NEW_HOSTNAME="medos"
PORT=80
WITH_VOICE=1
WITH_DISPLAY=0
VOSK_URL="https://alphacephei.com/vosk/models/vosk-model-small-en-in-0.4.zip"

while [ $# -gt 0 ]; do
  case "$1" in
    --no-voice) WITH_VOICE=0 ;;
    --display) WITH_DISPLAY=1 ;;
    --hostname) NEW_HOSTNAME="$2"; shift ;;
    --port) PORT="$2"; shift ;;
    *) echo "Unknown option $1"; exit 1 ;;
  esac
  shift
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo:  sudo bash deploy/install.sh"
  exit 1
fi

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

step "Installing system packages"
apt-get update
apt-get install -y python3-venv python3-pip alsa-utils avahi-daemon openssl unzip curl
# speech-dispatcher + espeak-ng give Chromium a voice for the waiting-room announcements
for pkg in python3-gpiozero python3-lgpio python3-rpi-lgpio python3-psutil speech-dispatcher espeak-ng; do
  apt-get install -y "$pkg" >/dev/null 2>&1 && echo "  installed $pkg" || echo "  skipped $pkg (not available on this OS)"
done

step "Creating the Python environment in $APP_DIR/.venv"
sudo -u "$APP_USER" python3 -m venv --system-site-packages "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip wheel
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [ "$WITH_VOICE" -eq 1 ]; then
  step "Installing offline speech recognition (Vosk, Indian English)"
  sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install vosk || echo "  Vosk failed to install"
  echo "  Downloading the Whisper speech model (base.en, about 150 MB)"
  sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" -c "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8', download_root='$APP_DIR/data/whisper')" || echo "  Whisper model download failed; it will retry on first use"
  if [ ! -d "$APP_DIR/data/vosk-model" ]; then
    sudo -u "$APP_USER" mkdir -p "$APP_DIR/data"
    tmp="$(mktemp -d)"
    if curl -L --fail -o "$tmp/model.zip" "$VOSK_URL"; then
      unzip -q "$tmp/model.zip" -d "$tmp"
      mv "$tmp"/vosk-model-* "$APP_DIR/data/vosk-model"
      chown -R "$APP_USER":"$APP_USER" "$APP_DIR/data/vosk-model"
      echo "  speech model installed"
    else
      echo "  couldn't download the speech model; run this script again when online"
    fi
    rm -rf "$tmp"
  fi
fi

step "Giving $APP_USER access to GPIO and the microphone"
for g in gpio audio; do getent group "$g" >/dev/null && usermod -aG "$g" "$APP_USER"; done

step "Setting the hostname to $NEW_HOSTNAME (http://$NEW_HOSTNAME.local)"
current="$(hostname)"
if [ "$current" != "$NEW_HOSTNAME" ]; then
  hostnamectl set-hostname "$NEW_HOSTNAME"
  sed -i "s/127\.0\.1\.1.*/127.0.1.1\t$NEW_HOSTNAME/" /etc/hosts
  grep -q "127.0.1.1" /etc/hosts || echo -e "127.0.1.1\t$NEW_HOSTNAME" >> /etc/hosts
fi
systemctl enable --now avahi-daemon

step "Installing the MedOS service"
cat > /etc/systemd/system/medos.service <<EOF
[Unit]
Description=MedOS - Smart Hospital Emergency Scheduler
After=network-online.target sound.target
Wants=network-online.target

[Service]
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP_DIR/.venv/bin/python run.py --port $PORT --https
# Bind port 80 without running as root
AmbientCapabilities=CAP_NET_BIND_SERVICE
# Run the scheduler at a higher CPU priority than ordinary programs
Nice=-5
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable medos
systemctl restart medos

if [ "$WITH_DISPLAY" -eq 1 ]; then
  step "Opening the waiting-room display on the HDMI screen at login"
  bash "$APP_DIR/deploy/display-autostart.sh" "$APP_USER" "$APP_HOME" "$PORT"
fi

ip_list="$(hostname -I 2>/dev/null || true)"
suffix=""; [ "$PORT" != "80" ] && suffix=":$PORT"
step "Done"
echo "  Open MedOS from any device on this network:"
echo "    http://$NEW_HOSTNAME.local$suffix"
for ip in $ip_list; do case "$ip" in *:*) ;; *) echo "    http://$ip$suffix" ;; esac; done
echo "  Browser microphone (HTTPS): https://$NEW_HOSTNAME.local:8443"
echo "  First PINs: staff 1111, admin 1234 (change them in Admin > Settings)"
echo "  No router? Make the Pi a Wi-Fi hotspot:  sudo bash deploy/hotspot.sh"
echo "  Logs: journalctl -u medos -f"
echo "  A reboot applies the new hostname and group memberships:  sudo reboot"
