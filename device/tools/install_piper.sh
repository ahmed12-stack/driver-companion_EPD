#!/bin/bash
# Install the Piper voice engine and one voice for dc_speech.
#
#   sudo bash tools/install_piper.sh                 # Cori (British, female)
#   sudo VOICE=en_US-amy-medium bash tools/install_piper.sh
#
# Reuses a copy already downloaded to ~/piper (as in the voice trial), so it
# only needs the internet when nothing is there. install.sh runs this.
set -euo pipefail

BASE=/opt/driver-companion/piper
VOICE="${VOICE:-en_GB-cori-medium}"
RELEASE=https://github.com/rhasspy/piper/releases/download/2023.11.14-2
VOICES=https://huggingface.co/rhasspy/piper-voices/resolve/main

USER_HOME="$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6)"
TRIAL="$USER_HOME/piper"

mkdir -p "$BASE/voices"

if [ ! -x "$BASE/piper/piper" ]; then
    if [ -x "$TRIAL/piper/piper" ]; then
        echo "Piper: copying from $TRIAL"
        cp -r "$TRIAL/piper" "$BASE/"
    else
        arch="$(uname -m)"
        case "$arch" in
            aarch64|armv7l|x86_64) ;;
            *) echo "Piper: no build for $arch"; exit 1 ;;
        esac
        echo "Piper: downloading for $arch"
        tmp="$(mktemp -d)"
        wget -q -O "$tmp/piper.tar.gz" "$RELEASE/piper_linux_${arch}.tar.gz"
        tar xzf "$tmp/piper.tar.gz" -C "$BASE"
        rm -rf "$tmp"
    fi
fi

model="$BASE/voices/$VOICE.onnx"
if [ ! -s "$model" ] || [ ! -s "$model.json" ]; then
    if [ -s "$TRIAL/$VOICE.onnx" ] && [ -s "$TRIAL/$VOICE.onnx.json" ]; then
        echo "Voice $VOICE: copying from $TRIAL"
        cp "$TRIAL/$VOICE.onnx" "$TRIAL/$VOICE.onnx.json" "$BASE/voices/"
    else
        # en_GB-cori-medium -> en/en_GB/cori/medium/en_GB-cori-medium
        IFS=- read -r lang name quality <<< "$VOICE"
        url="$VOICES/${lang%%_*}/$lang/$name/$quality/$VOICE"
        echo "Voice $VOICE: downloading (about 60 MB)"
        wget -q -O "$model.part" "$url.onnx"
        wget -q -O "$model.json" "$url.onnx.json"
        mv "$model.part" "$model"
    fi
fi
chmod -R u=rwX,go=rX "$BASE"

# Make one sentence to be sure it works.
check="$(mktemp --suffix=.wav)"
if echo "Driver Companion voice check." | "$BASE/piper/piper" --model "$model" --output_file "$check" >/dev/null 2>&1 \
        && [ -s "$check" ]; then
    echo "Piper works with $VOICE"
    rm -f "$check"
else
    rm -f "$check"
    echo "Piper is installed but could not make a test sentence"
    exit 1
fi
