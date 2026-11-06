#!/bin/bash
# Install or update Driver Companion on the Raspberry Pi.
#
#   cd ~/device && sudo bash install.sh
#
# Safe to run again after every update. Keeps settings, trips, the phone
# pairing, the LLM and the voice. Replaces the old single driver-companion-ble
# service. Copy the vision folder next to this one (scp -r device vision ...)
# to install the road-sign reader too.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run it with sudo:  sudo bash install.sh"
    exit 1
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VISION_SRC="$(dirname "$SRC")/vision"
BASE=/opt/driver-companion
MODULES="speech phone trip ignition health speed"
SOCKET=/run/mosquitto/dc-bus.sock
REBOOT=0

step() { echo; echo "== $*"; }

step "System packages (needs internet the first time)"
apt-get install -y mosquitto bluez espeak-ng alsa-utils wget python3-venv python3-gpiozero python3-lgpio

step "Code -> $BASE/device"
mkdir -p "$BASE"
if [ "$SRC" != "$BASE/device" ]; then
    rm -rf "$BASE/device.new"
    cp -r "$SRC" "$BASE/device.new"
    find "$BASE/device.new" -name __pycache__ -prune -exec rm -rf {} +
    rm -rf "$BASE/device.old"
    if [ -d "$BASE/device" ]; then mv "$BASE/device" "$BASE/device.old"; fi
    mv "$BASE/device.new" "$BASE/device"
fi
chmod -R u=rwX,go=rX "$BASE/device"

step "Python packages"
if [ ! -x "$BASE/venv/bin/python" ]; then
    python3 -m venv --system-site-packages "$BASE/venv"
fi
"$BASE/venv/bin/pip" install -q -r "$BASE/device/requirements.txt"

step "Road-sign reader (vision)"
if [ -d "$VISION_SRC/pipeline" ]; then
    rm -rf "$BASE/vision.new"
    mkdir -p "$BASE/vision.new"
    cp -r "$VISION_SRC/pipeline" "$VISION_SRC/models" "$BASE/vision.new/"
    find "$BASE/vision.new" -name __pycache__ -prune -exec rm -rf {} +
    rm -rf "$BASE/vision"
    mv "$BASE/vision.new" "$BASE/vision"
    chmod -R u=rwX,go=rX "$BASE/vision"
    echo "vision pipeline copied from $VISION_SRC"
fi
if [ -d "$BASE/vision/pipeline" ]; then
    apt-get install -y python3-numpy python3-opencv python3-picamera2
    # TFLite for the CNN. Ready-made packages only (never compiled on the Pi), and
    # numpy is left as the system's, which the system OpenCV was built with.
    echo "installing TFLite (a minute or two)..."
    if "$BASE/venv/bin/pip" install --only-binary=:all: "ai-edge-litert" \
            || "$BASE/venv/bin/pip" install --only-binary=:all: "tflite-runtime"; then
        echo "TFLite ready"
    else
        echo "TFLite could not be installed: the sign reader uses the slower NumPy model"
    fi
    MODULES="$MODULES vision"
else
    echo "no vision folder: sign reader not installed (copy vision/ next to device/)"
fi

step "Voice (Piper)"
if bash "$BASE/device/tools/install_piper.sh"; then
    echo "speech uses the Piper voice"
else
    echo "Piper not ready: speech uses espeak for now (run tools/install_piper.sh later)"
fi

step "Speaker amplifier (MAX98357A, I2S)"
BOOTCFG=/boot/firmware/config.txt
[ -f "$BOOTCFG" ] || BOOTCFG=/boot/config.txt
for line in "dtparam=audio=off" "dtoverlay=max98357a"; do
    if ! grep -qx "$line" "$BOOTCFG"; then
        echo "$line" >> "$BOOTCFG"
        echo "added $line to $BOOTCFG"
        REBOOT=1
    fi
done
if [ ! -f /etc/asound.conf ]; then
    install -m 644 "$BASE/device/config/asound.conf" /etc/asound.conf
    echo "MAX98357A set as the default sound card"
else
    echo "/etc/asound.conf already there (left as it is)"
fi

step "Message board (mosquitto, private socket $SOCKET)"
install -m 644 "$BASE/device/config/mosquitto-dc.conf" /etc/mosquitto/conf.d/driver-companion.conf
mkdir -p /etc/systemd/system/mosquitto.service.d
install -m 644 "$BASE/device/config/mosquitto-runtime-dir.conf" \
    /etc/systemd/system/mosquitto.service.d/driver-companion.conf
systemctl daemon-reload
systemctl enable mosquitto
systemctl restart mosquitto
for _ in $(seq 1 20); do
    [ -S "$SOCKET" ] && break
    sleep 0.5
done
if [ ! -S "$SOCKET" ]; then
    echo "The message board did not start. See:  journalctl -u mosquitto -n 30"
    exit 1
fi

step "Removing the old single service (if any)"
if [ -f /etc/systemd/system/driver-companion-ble.service ]; then
    systemctl disable --now driver-companion-ble || true
    rm -f /etc/systemd/system/driver-companion-ble.service
    rm -rf /etc/systemd/system/driver-companion-ble.service.d
    echo "old service removed (its data is kept and used by the new modules)"
else
    echo "none found"
fi

step "Modules: $MODULES"
mkdir -p /var/lib/driver-companion && chmod 700 /var/lib/driver-companion
mkdir -p /etc/driver-companion
for m in $MODULES; do
    if [ ! -f "/etc/driver-companion/$m.env" ]; then
        install -m 644 "$BASE/device/config/$m.env" "/etc/driver-companion/$m.env"
    fi
done
for m in $MODULES; do echo "dc_$m"; done > /etc/driver-companion/modules.list
install -m 644 "$BASE/device/systemd/dc@.service" /etc/systemd/system/dc@.service
systemctl daemon-reload
for m in $MODULES; do
    systemctl enable "dc@$m" >/dev/null 2>&1
    systemctl restart "dc@$m"
done

sleep 4
step "Status"
failed=0
for m in $MODULES; do
    state="$(systemctl is-active "dc@$m" || true)"
    printf "  %-10s %s\n" "$m" "$state"
    [ "$state" = "active" ] || failed=1
done

echo
if [ "$failed" -eq 0 ]; then
    echo "All modules running. In about 20 s the device speaks its self-test result."
else
    echo "Some modules are not running. See:  journalctl -u 'dc@*' -n 50"
fi
if [ "$REBOOT" -eq 1 ]; then
    echo
    echo "The speaker amplifier needs a restart once:   sudo reboot"
fi
echo "Watch everything:   journalctl -u 'dc@*' -f"
echo "Pairing mode:       sudo systemctl kill -s USR1 dc@phone"
echo "Module options:     /etc/driver-companion/<module>.env"
