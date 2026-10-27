# Driver Companion — Bluetooth gateway (Pi side)

This is the device half of the phone link: checkpoint 2, "Bluetooth hello", of the build order. It runs on the Raspberry Pi 3B+, advertises as `DC-XXXX`, pairs with a phone using a spoken 6-digit code, and speaks notifications, SMS and calls that the phone sends.

Test it with our Android app (see `android/README.md`). The nRF Connect steps below are a fallback for checking the Pi on its own.

## What works now

| Feature | Status |
| --- | --- |
| Advertising as `DC-XXXX` with the Driver Companion service | Done |
| Pairing: hold button 5 s, device speaks code, phone types it | Done |
| Every characteristic locked until the phone has paired | Done |
| `hello` handshake on Settings, reply on Status | Done |
| Notifications, SMS and calls spoken, behind higher-priority alerts | Done |
| Settings (volume, allowed apps, read text, quiet) saved on the device | Done |
| Phone GPS fixes received and stored | Done (speed module will read them) |
| Trip Out, Update In | Later checkpoints (Update In replies `NOT_READY`) |

## Folder layout

```
bluetooth/
  dc_ble/
    uuids.py      service and characteristic UUIDs (never change)
    framing.py    3-byte header: split and reassemble packets
    gateway.py    protocol logic (no Bluetooth code; testable on a laptop)
    settings.py   settings file, saved safely
    speaker.py    priority queue + espeak-ng
    bluez.py      BlueZ D-Bus: GATT server, advertising, pairing agent
    __main__.py   starts everything
  tools/frame.py  makes test packets for nRF Connect
  tests/          unit tests
  driver-companion-ble.service   systemd unit
```

## Wiring

Push button between **GPIO17 (physical pin 11)** and **GND (physical pin 9)**. No resistor needed; the Pi's internal pull-up is used. Change the pin with `--button-pin` if Amrit's wiring uses GPIO17 for something else. The same button will later also handle Drive/Mute (REQ-06); only the 5-second hold is used here.

Speaker on the 3.5 mm jack. If there is no sound: `sudo raspi-config` → System Options → Audio → Headphones.

## Install on the Pi

```bash
sudo apt update
sudo apt install -y bluez espeak-ng python3-venv python3-gpiozero python3-lgpio

sudo mkdir -p /opt/driver-companion
sudo cp -r bluetooth /opt/driver-companion/
sudo python3 -m venv --system-site-packages /opt/driver-companion/venv
sudo /opt/driver-companion/venv/bin/pip install -r /opt/driver-companion/bluetooth/requirements.txt
```

`--system-site-packages` lets the venv use the apt-installed `gpiozero` and `lgpio`.

## Run it by hand (for testing)

```bash
cd /opt/driver-companion/bluetooth
sudo /opt/driver-companion/venv/bin/python -m dc_ble --pair -v
```

`--pair` starts in pairing mode so you don't need the button. It needs `sudo` because it registers with BlueZ on the system bus. Stop with Ctrl+C.

## Test with nRF Connect

1. **Get the hex to send.** On your laptop or the Pi:
   ```bash
   python tools/frame.py hello
   python tools/frame.py notif
   ```
   Each prints one line of hex per packet.
2. **Connect.** In nRF Connect, Scanner tab → find `DC-XXXX` → Connect.
3. **Bigger packets.** Top-right menu → Request MTU → 247. (At the default MTU each message needs several writes; run `frame.py` with `--mtu 23` to see them.)
4. **Pair.** Open the unknown service starting `70b60000`. Tap the up-arrow (write) on any characteristic. The write fails with "insufficient authentication", which is correct: Android then shows its pairing box. The Pi speaks a 6-digit code twice; type it in. You should hear "Phone paired."
5. **Turn on Status notifications.** On characteristic `70b60005`, tap the triple-down-arrow icon.
6. **Hello.** Write the `hello` hex to `70b60003` (Settings) as BYTEARRAY. A notification appears on `70b60005`. Decode it:
   ```bash
   python tools/frame.py decode <paste the value nRF Connect shows>
   ```
   You should see `{"t":"hello",...,"dev":"DC-XXXX",...}`.
7. **Notification.** Write the `notif` hex to `70b60001` (Notification In). The Pi says "WhatsApp message from Rahul".
8. Try `call`, `sms`, `settings` (turns on reading message text) and `loc` (`70b60002`) the same way.

Checkpoint 2 passes when steps 4, 6 and 7 work.

## Run as a service (starts at boot)

```bash
sudo cp /opt/driver-companion/bluetooth/driver-companion-ble.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now driver-companion-ble
journalctl -u driver-companion-ble -f          # watch the log
sudo systemctl kill -s USR1 driver-companion-ble   # pairing mode without the button
```

The service restarts itself within 2 seconds if it crashes (REQ-22). Settings live in `/var/lib/driver-companion/settings.json`, readable by root only (REQ-30). Message text is never written to the log.

## Run the tests (laptop or Pi)

```bash
pip install pytest
python -m pytest tests -q
```

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `org.bluez` or `hci0` not found | `sudo systemctl status bluetooth`; `bluetoothctl show` should list the controller as powered |
| Phone doesn't show `DC-XXXX` | Check the log says "advertising as"; restart the service; close other Bluetooth apps on the Pi |
| No pairing box on the phone | Remove any old pairing with the Pi in Android Bluetooth settings, and on the Pi: `bluetoothctl remove <phone MAC>` |
| Code is not spoken | Pairing mode must be on (`--pair`, button hold, or USR1). Outside it, codes are deliberately silent |
| Writes still fail after pairing | Disconnect and reconnect in nRF Connect so the link starts encrypted |
| Garbled or no reply | Every packet needs the 3-byte header; make the hex with `tools/frame.py` |

## Notes for the group

- The UUIDs and message formats are the contract with the Android app. Change them only with everyone's agreement and a protocol version bump.
- Alert priority is overspeed, then signs, then calls, then messages (5 s gap, dropped after 60 s), then system messages. Abhay's alert manager builds on `speaker.py`.
- `gateway.last_location` holds the latest phone GPS fix for Amrit's speed module.
