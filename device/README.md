# Driver Companion — device software (Raspberry Pi)

Each job on the device is its own small program (a **module**) running as its own service. The modules never call each other: they talk only through a **message board** (Mosquitto) on a private socket file with no network port. A module can crash, restart or be added without touching the others.

```
                 ┌──────────────── message board (Mosquitto, /run/mosquitto/dc-bus.sock) ────────────────┐
 phone app ⇄ BT ─┤ dc_phone     →  alert/say, gps/fix, settings/current     ←  trip/saved                 │
 ignition wire  ─┤ dc_ignition  →  vehicle/ignition                                                       │
                 │ dc_trip      ←  vehicle/ignition, gps/fix, trip/event    →  alert/say, trip/saved      │
 speaker  ◄──────┤ dc_speech    ←  alert/say, settings/current                                            │
                 │ dc_health    ←  module/+/status, module/+/alive          →  alert/say, health/selftest │
 camera  ────────┤ dc_vision    ←  vehicle/ignition     →  alert/say, trip/event, road/speed_limit        │
                 │ dc_speed     ←  gps/fix, road/speed_limit, vehicle/ignition  →  alert/say, trip/event     │
 (next) motion (MPU6050), GPS module  →  trip/event, gps/fix                                              │
                 └───────────────────────────────────────────────────────────────────────────────────────┘
```

| Module | Job | Owner |
| --- | --- | --- |
| `dc_phone` | Bluetooth link to the app: pairing, notifications, calls, settings, phone GPS, sending trips | Asir |
| `dc_speech` | The only module that uses the speaker; speaks one message at a time, most urgent first, in the Piper voice | Abhay |
| `dc_trip` | One trip per ignition cycle; facts + LLM remark; last 20 trips | Anirudha |
| `dc_ignition` | Ignition sense input (GPIO27) | Amrit |
| `dc_speed` | Overspeed warnings: GPS speed against the last speed-limit sign (REQ-05, REQ-19) | Amrit |
| `dc_health` | Power-on self-test (REQ-09); warns when a module crashes or hangs | all |
| `dc_vision` | Road signs with the Pi camera while the car is on: runs `vision/pipeline` (CNN, 3-of-5 vote), speaks the sign, counts it for the trip, keeps the last speed limit. Needs no phone | Asir |
| `dc_template` | Copy this to start a new module (not installed as a service) | — |

## The topic contract

The full table, with every field, is at the top of [`dc_common/topics.py`](dc_common/topics.py). That table is the agreement between the four of us: change a topic or its fields only together.

| Topic | Retained | From | Carries |
| --- | --- | --- | --- |
| `vehicle/ignition` | yes | dc_ignition | `on` |
| `gps/fix` | no | dc_phone (later dc_gnss) | `lat lon kmh acc ts source` |
| `alert/say` | no | anyone | `text priority max_age?` — priority: overspeed, sign, call, message, system |
| `settings/current` | yes | dc_phone | `vol apps read_text quiet` |
| `trip/event` | no | speed, motion, vision | `event` (overspeed, harsh_brake, harsh_accel, sign), `seconds?`, `continued?` (more time for an event already counted) |
| `trip/saved` | no | dc_trip | the finished trip, with its `route` |
| `road/speed_limit` | yes | dc_vision | `kmh ts`: the last speed-limit sign read (for the overspeed check) |
| `road/overspeed` | yes | dc_speed | `over kmh limit`: whether the driver is over the limit right now |
| `vision/camera` | yes | dc_vision | `state`: ok, blinded, missing, off |
| `health/selftest` | yes | dc_health | `ok modules hardware problems` |
| `module/<name>/status` | yes | every module | `online`, `stopped`, or `offline` (crash: the board says it for the module) |
| `module/<name>/alive` | no | every module | heartbeat every 5 s |

"Retained" means the board keeps the last value, so a module that starts later still gets it at once.

## Install or update on the Pi

From the laptop, in the repo folder:

```
scp -r device vision epd_7@<pi-address>:~/
```

(`vision` is only needed for the sign reader; without it, everything else is installed.)

On the Pi:

```
cd ~/device
sudo bash install.sh
```

Works on Raspberry Pi OS Bookworm and Trixie (Python 3.13); TFLite is installed from ready-made packages only, never compiled on the Pi.

The script installs Mosquitto and the Python packages, copies the code to `/opt/driver-companion/device` (and the vision pipeline and model to `/opt/driver-companion/vision`), installs the Piper voice, sets up the MAX98357A amplifier, removes the old single `driver-companion-ble` service, and starts the modules. Settings, trips, the phone pairing, the LLM and the voice are kept. The first time, it asks for one `sudo reboot` (for the amplifier). It ends with each module's status; about 20 seconds later the device speaks its self-test ("Self test passed. System ready."). Run it again after every update.

## Everyday commands

| What | Command |
| --- | --- |
| Watch every module's log | `journalctl -u 'dc@*' -f` |
| One module's log | `journalctl -u dc@trip -f` |
| Status of all | `systemctl status 'dc@*'` |
| Restart one | `sudo systemctl restart dc@speech` |
| Pairing mode for a new phone | `sudo systemctl kill -s USR1 dc@phone` |
| Change a module's options | edit `/etc/driver-companion/<module>.env`, then restart it |
| Watch the message board itself | `sudo /opt/driver-companion/venv/bin/python /opt/driver-companion/device/tools/watch_board.py` |

Useful options (in the `.env` files, as `ARGS="..."`):

| Module | Option |
| --- | --- |
| speech | `--print-speech` (no speaker: write speech to the log) |
| ignition | `--always-on` (no ignition wire yet) |
| trip | `--halt-after-min 10` (in the car, shut down 10 min after ignition off; REQ-28) |
| health | `--expect-hardware camera,imu,gnss` (once fitted: warn if missing) |

## Test without a car

```
sudo systemctl stop dc@ignition
sudo /opt/driver-companion/venv/bin/python /opt/driver-companion/device/tools/sim_drive.py
sudo systemctl start dc@ignition
```

`sim_drive.py` publishes the ignition coming on, a 10-minute, 6 km drive with a traffic stop (at 10x speed, so about a minute), and the ignition going off, all through the board. The real `dc_trip`, `dc_speech` and `dc_phone` handle it exactly as a real trip: you hear the summary, and the trip appears in the app. `dc_ignition` must be stopped first because it owns `vehicle/ignition`.

## Demo in a car (no car wiring)

1. Power the Pi from a **power bank** (5 V, 2.4 A or more), not the car socket: most cars cut the socket with the key, and the Pi must stay on after "car off" to speak the summary and send the trip.
2. Use a jumper wire on **pins 13 and 14** as the ignition: on = trip starts (after 1 s), off = trip ends (after 5 s). `ignition.env` must be `ARGS=""` (not `--always-on`, or the trip never ends).
3. Wait for "Self test passed. System ready.", check the app shows **Connected** with a green *Phone GPS* line, then put the jumper on and drive (at least 100 m). Signs and messages are spoken on the way.
4. Park and pull the jumper: the facts are spoken, then the AI remark (5–8 s; run `sim_drive.py` once beforehand, since the AI's very first remark takes about a minute), and the trip appears in the app with its route.

In a real car the jumper is replaced by a PC817 optocoupler on a key-switched supply (see Wiring), and the power bank by a UPS board between the car charger and the Pi.

## Writing a new module

1. Copy the template: `cp -r dc_template dc_motion`, and replace `dc_template` with `dc_motion` inside it.
2. In `setup()`, subscribe to what you need (`bus.subscribe(topics.GPS_FIX, on_fix)`) and publish what you find (`bus.post(topics.TRIP_EVENT, {"event": "overspeed", "seconds": 4})`). Keep handlers quick; start a task for slow work.
3. Try it by hand next to the running system: `sudo /opt/driver-companion/venv/bin/python -m dc_motion -v` (from `/opt/driver-companion/device`).
4. Make it a service: add `motion` to `MODULES` in `install.sh` (and a `config/motion.env`) and run the installer; it also adds `dc_motion` to the modules the self-test checks.

`dc_common` gives every module the same message board connection (it reconnects by itself), heartbeats, the online/stopped/offline status, logging, and safe file writing (`dc_common/storage.py`).

## Voice

`dc_speech` uses **Piper** (natural voice, offline; Cori, British female) and falls back to espeak if Piper is missing or fails. Piper takes a few seconds per new sentence on the Pi 3B+, so:

- every fixed phrase (all 20 signs, pairing, self-test, warnings: `dc_common/phrases.py`) is made in advance in the background after start-up, the first time (a few minutes), and kept in `/var/lib/driver-companion/voice_cache`. A sign alert then starts at once;
- other sentences are made one at a time while the previous one plays; the last 100 are kept;
- message and call text is never kept on disk.

Another voice: `sudo VOICE=en_US-amy-medium bash /opt/driver-companion/device/tools/install_piper.sh`, then `ARGS="--voice /opt/driver-companion/piper/voices/en_US-amy-medium.onnx"` in `/etc/driver-companion/speech.env` and `sudo systemctl restart dc@speech`.

## Road signs

`dc_vision` starts the camera when the ignition comes on and stops it when it goes off (`--always-on` in `vision.env` for bench tests). Each confirmed sign is spoken once per sighting ("Speed limit 50."), counted for the trip, and a speed limit is kept on `road/speed_limit`. If the camera is covered or dazzled for 10 s it says "Sign alerts paused." (REQ-18). With no camera it says so once and tries again every 30 s; the self-test reports it. It uses the TFLite model if TFLite is installed, otherwise the same CNN in NumPy.

Watch it: `journalctl -u dc@vision -f` (logs each sign and, every minute, the camera fps and the time per stage).

Measured on the Pi 3B+ (int8 TFLite, 640x480): about 60 ms per frame (health 7, regions 33, CNN 17–22 ms); Speed limit 20 and Men at work were confirmed 38 and 56 ms after the deciding frame.

## Overspeed warnings

`dc_speed` takes the speed from the GPS fixes (the phone's GPS for now; fixes worse than ±30 m are ignored) and the limit from the last speed-limit sign the camera read:

- more than **5 km/h over the limit for 3 s** → "Slow down. Speed limit 50." (REQ-05), spoken before anything else; again **at most every 30 s** while still over;
- back under for 2 s ends the warning. Each warning counts once for the trip, and the time over the limit is added to it (the app's *Overspeed warnings* and *Time over the limit*);
- the limit is forgotten **after 5 km** or **when the trip ends** (REQ-19), so an old sign never causes warnings later. With no sign read, there are no warnings.

Test without a car: `sim_drive.py --speed-limit 50` pretends a 50 sign at the start; the fake drive goes up to 72 km/h, so you hear the warnings. On the bench with the real camera: show a 20 sign (the phone's GPS must then show more than 25 km/h, so this needs a ride).

## Trips and summaries

A trip starts when the ignition comes on and ends when it goes off, so waiting in traffic never ends it. The device then speaks a factual sentence straight away, and a few seconds later one short remark by the on-device LLM (Qwen2.5 0.5B, llama.cpp). The numbers always come from the fixed sentence; the LLM is only told what happened, in words. Its remark is checked (no numbers, short, spoken to "you", nothing unmeasured, no praise after warnings, no warning tone on a calm trip) and retried up to 3 times; if none passes, only the facts are spoken. The trip goes to the app on `trip/saved` and is resent until the app confirms it.

Each trip also carries its path (`route`: a point about every 25 m, at most 300 points, as an encoded polyline of a few kilobytes at most), which the app draws on a map. Like the rest of the trip it stays on the device (last 20 trips, owner-only files) and goes only to the paired phone.

If `dc_trip` restarts mid-drive it resumes the same trip. If the power is cut without the ignition going off, the trip is closed later, marked "ended with a power cut".

`tools/try_summary.py --runs 6` runs the real LLM on sample trips and shows each remark and the check's verdict.

### Install the LLM (after building llama.cpp)

```
sudo mkdir -p /opt/driver-companion/llm
sudo cp -r ~/llama.cpp/build/bin/. /opt/driver-companion/llm/
sudo cp ~/models/qwen-0.5b.gguf /opt/driver-companion/llm/model.gguf
sudo systemctl restart dc@trip
```

`journalctl -u dc@trip -n 20` then says `trip remarks by the LLM`.

## Wiring

| Part | Pins |
| --- | --- |
| Pairing button | GPIO17 (pin 11) to GND (pin 9) |
| Camera | Pi camera ribbon in the CAMERA connector (between HDMI and the audio jack), blue side towards the jack |
| Ignition, bench | switch or jumper between GPIO27 (pin 13) and GND (pin 14); closed = on |
| Ignition, car | PC817 optocoupler: ignition/ACC line → 2.2 kΩ → PC817 pins 1–2 → car ground; pin 4 → GPIO27, pin 3 → Pi GND |
| Speaker amplifier (MAX98357A, digital I2S) | Vin → pin 2 (5V), GND → pin 6, BCLK → pin 12, LRC → pin 35, DIN → pin 40, GAIN → pin 39 (GND, 12 dB); SD not connected; speaker (4 Ω, 3 W) on the + and − screw terminals, never to GND |

`install.sh` sets the amplifier up: `dtparam=audio=off` and `dtoverlay=max98357a` in `/boot/firmware/config.txt` (then one reboot), and `config/asound.conf` as `/etc/asound.conf` (MAX98357A as the default sound card, shared through `dmix`).

**Cooling.** Above 60 °C the Pi 3B+ slows itself down and the camera drops to 10 fps (`throttled True` in the `dc@vision` log; `vcgencmd get_throttled` shows `0x80008`). Stick the heatsinks on the BROADCOM chip (and the USB chip), and in the case add air holes or a 5 V fan (red → pin 4, black → pin 9). Optional: `temp_soft_limit=70` in `/boot/firmware/config.txt`. Check with `vcgencmd measure_temp`.

**Ignition from a car charger (prototype).** The second USB port of a key-switched cigarette-lighter charger: 5 V → 470 Ω → PC817 pin 1, GND → pin 2; PC817 pin 4 → GPIO27 (pin 13), pin 3 → GND (pin 14). A cheap charger is not enough to run the Pi itself: it needs 5 V, 2.5 A or more (`vcgencmd get_throttled` must show `0x0`).

In the car the Pi runs from the always-on 12 V supply through a 5 V buck converter, so it stays on after the ignition goes off to finish the summary. The final product also needs a power-latch circuit that cuts the Pi's power after it halts.

## Security (REQ-16, REQ-30)

- The message board has no network port: only root and the mosquitto user can open its socket file.
- Every module writes its files owner-only (0600); data lives in `/var/lib/driver-companion` (0700).
- Bluetooth: only phones paired with the spoken code can read or write anything.

## Run the tests

```
sudo apt install -y mosquitto         # for the real-board tests (skipped without it)
/opt/driver-companion/venv/bin/pip install pytest
cd /opt/driver-companion/device && sudo /opt/driver-companion/venv/bin/python -m pytest tests -q
```

`tests/test_real_board.py` starts its own Mosquitto, runs `dc_speech`, `dc_trip` and `dc_health` as separate programs, drives a fake trip through the board, checks the self-test, then kills `dc_trip` and checks that `dc_health` warns.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Camera only 10 fps, `throttled True` | The Pi is hot (above 60 °C): heatsinks, airflow (see Cooling) |
| `throttled` value with a 5 at the end (e.g. `0x50005`) | The power supply is too weak: 5 V, 2.5 A or more, short thick cable |
| No phone GPS | Watch `tools/watch_board.py gps/fix`; the app's *Phone GPS* line says if the phone has a fix |
| Signs read (in the `dc@vision` log) but not spoken | `journalctl -u dc@speech -n 30`; the first time, the voice clips are still being made |
| A module keeps restarting | `journalctl -u dc@<name> -n 50` shows the error |
| "cannot reach the message board" | `systemctl status mosquitto`; `ls -l /run/mosquitto/dc-bus.sock` must exist |
| No trips recorded | Is the ignition on? With no wire, set `ARGS="--always-on"` in `/etc/driver-companion/ignition.env` and restart `dc@ignition` |
| Nothing spoken | `dc@speech` running? Try `ARGS="--print-speech"` to see what it would say |
| Phone not found | `sudo systemctl kill -s USR1 dc@phone` for pairing mode; see `dc@phone` log |
