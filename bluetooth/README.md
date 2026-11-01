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
| Phone GPS fixes received and fed to the trip logger | Done |
| Trips: one per ignition cycle, saved every 30 s, last 20 kept | Done |
| Trip summary: factual sentence plus a short remark by the on-device LLM, checked | Done (facts only if no LLM) |
| Trip Out: trips sent to the app, resent until the app confirms | Done |
| Update In | Later checkpoint (replies `NOT_READY`) |

## Trips and summaries

A trip starts when the **ignition** comes on and ends when it goes off, so waiting in traffic never ends it. When it ends, the device speaks a factual sentence straight away ("Trip complete. You drove 12.4 kilometres in 23 minutes…"). About 10 seconds later it adds one short remark written by the on-device LLM ("Ease off a little on the open stretches next time."). Then it sends the trip to the app. If the phone is not connected, the trip waits on the device (last 20 kept) and goes over at the next connection.

Recorded per trip: distance, trip time, driving time, top and average speed, stops along the way, and (once those modules exist) overspeed warnings, harsh braking/acceleration and signs announced. Times come from the phone's GPS fixes, because the Pi has no battery-backed clock.

**Why the LLM only writes the remark.** On the Pi 3B+ the 0.5B model got numbers wrong when asked for the whole summary: in testing it said "harsh braking 2 times" when there was 1, and invented arrival times. Only 2 of 6 attempts were correct. So the numbers always come from the fixed sentence, and the LLM is told only what happened ("Overspeed warnings: yes"), never the numbers. Its remark is checked before it is spoken: no numbers in any form, at most 20 words, spoken to the driver as "you", nothing about things the trip did not measure, no praise after warnings, and no warning tone when nothing went wrong. A remark that fails is tried again, up to 3 times within 45 seconds (each try is sampled afresh); if none passes, the driver hears the facts only. Talk of traffic or patience is allowed only when the trip really had many stops. The trip detail in the app says whether the AI added a remark.

`tools/try_summary.py --runs 6` runs the real LLM on sample trips and shows each remark and the check's verdict. Add `--no-cache` to compare speed without the prompt cache.

The fixed instructions to the LLM are the same every trip, so their processed state is saved in `/var/lib/driver-companion/llm_prompt.cache` and reused, which makes later remarks faster. The device uses this only if its llama.cpp build lists `--prompt-cache` in its help, and deletes the file and retries without it if a run comes back empty (for example after a power cut while it was being saved). Remarks that talk about the device itself ("I'll keep an eye on the speed") are rejected, since it cannot promise anything.

If the program restarts mid-drive it resumes the same trip. If the power is cut without the ignition going off, the saved trip is closed later, marked "ended with a power cut".

### Ignition input

| Where | Wiring |
| --- | --- |
| Bench | Toggle switch between **GPIO27 (pin 13)** and **GND (pin 14)**. Closed = ignition on |
| Car | PC817 optocoupler: car ignition/ACC line → 2.2 kΩ → PC817 LED (pins 1–2) → car ground. Transistor side: pin 4 (collector) → GPIO27, pin 3 (emitter) → Pi GND |

The Pi's pull-up keeps the pin high (ignition off) otherwise. "On" must last 1 s and "off" 5 s before they count, so a dip while cranking does not end the trip. Options: `--ignition-pin N`, or `--no-ignition` to treat the ignition as always on.

In the car the Pi is powered from the always-on 12 V supply through a 5 V buck converter, so it stays on after the ignition goes off to finish the summary. `--halt-after-min 10` shuts it down 10 minutes later (REQ-28). The final product also needs a power-latch circuit that cuts the Pi's power after it halts and restores it when the ignition comes on.

### Install the LLM (after building llama.cpp)

```bash
sudo mkdir -p /opt/driver-companion/llm
sudo cp -r ~/llama.cpp/build/bin/. /opt/driver-companion/llm/
sudo cp ~/models/qwen-0.5b.gguf /opt/driver-companion/llm/model.gguf
sudo systemctl restart driver-companion-ble
journalctl -u driver-companion-ble -n 20 --no-pager | grep "trip summaries"
```

The log says `trip summaries by the LLM`. Without these files it says `the plain template` and everything else still works.

### Test without a car

```bash
sudo systemctl stop driver-companion-ble
cd /opt/driver-companion/bluetooth
sudo /opt/driver-companion/venv/bin/python -m dc_ble --simulate -v
```

This runs a fake 10-minute, 6 km drive (a traffic stop included) at 10x speed, so about a minute, through the same code as a real drive, then ends the trip. Connect the app first; the trip appears under **Trips**. `--sim-speed 1` runs it in real time. Afterwards: `sudo systemctl start driver-companion-ble`.

## Folder layout

```
bluetooth/
  dc_ble/
    uuids.py      service and characteristic UUIDs (never change)
    framing.py    3-byte header: split and reassemble packets
    gateway.py    protocol logic (no Bluetooth code; testable on a laptop)
    settings.py   settings file, saved safely
    speaker.py    priority queue + espeak-ng
    storage.py    power-cut-safe JSON files
    trip.py       trip logger and the last-20 trip store
    trip_service.py  ignition → trip → summary → speech → phone
    summary.py    factual sentence, LLM remark, and the remark check
    ignition.py   ignition sense input with debouncing
    simulate.py   fake drive for testing
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

The service restarts itself within 2 seconds if it crashes (REQ-22). Settings, trips (`trips.json`) and the running trip (`trip_current.json`) live in `/var/lib/driver-companion/`, readable by root only (REQ-30). Message text is never written to the log.

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
- Other modules report trip events with `TripService.record(event)`: `overspeed` (with seconds over), `harsh_brake`, `harsh_accel`, `sign`. They then appear in the trip, the app and the summary.
