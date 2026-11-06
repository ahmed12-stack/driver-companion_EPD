# Driver Companion 🚗

Hey! This is our Electronic Product Development (EPD) project at KIIT, built by Group 7.

Driver Companion is a small box that sits on your dashboard, watches the road
through a camera, and tells you out loud about the road signs you pass. It also
reads out your WhatsApp messages, SMS and calls, so your phone stays in your
pocket. When you switch the car off, it gives you a quick spoken summary of the
trip, with one tip written by a small AI running on the box itself, and the trip
(with its route on a map) shows up in our Android app.

Everything runs on the device itself, a Raspberry Pi with a camera and a speaker.
No screen to look at, no internet needed. You just drive and listen.

## Who's doing what

| Person | Part |
|---|---|
| Asir | Vision: spotting and recognising signs (`vision/`) |
| Amrit | Sensors and speed logic |
| Abhay | Speech and alerts |
| Anirudha | The language model and documentation |

## What's in here

- [`vision/`](vision/) holds the camera pipeline: it finds signs in each frame, recognises them with our own CNN (trained from scratch), and double-checks before saying anything.
- [`vision/demo/`](vision/demo/) is a laptop version you can try right now with just a webcam, plus a small trip app you can open on your phone.
- [`device/`](device/) is the software on the Raspberry Pi: small programs (phone link, speech, trip log, ignition, sign reader, health check) that talk through a private message board. One command installs everything. Start with [`device/README.md`](device/README.md).
- [`android/`](android/) is the phone app: pairs with the box over Bluetooth, forwards the notifications you choose, sends the phone's GPS, and shows every trip with its route on a map.
- `bluetooth/` is the old single-program version of the Pi software, replaced by `device/`. It will be removed.

## Want to try it?

You don't need a Raspberry Pi for this. A laptop with a webcam is enough:

```
cd vision
pip install -r demo/requirements.txt
python demo/webcam_demo.py
```

Then hold up one of the signs from `vision/demo/signs/` (print them, or open them on your phone).
The full walkthrough is in [`vision/demo/README.md`](vision/demo/README.md).

On the real box: copy `device/` and `vision/` to the Pi and run `sudo bash install.sh`
(see [`device/README.md`](device/README.md)), then install the app from `android/`.

## Where we are

Working on the real hardware (Raspberry Pi 3B+, November 2026):

- Pairing the phone with a spoken 6-digit code, and reconnecting by itself.
- WhatsApp, SMS and calls read out loud, for the apps you pick in the app.
- Road signs read live by the Pi camera and our CNN: a sign is confirmed about 50 ms after the camera sees it.
- A natural female voice (Piper, offline) through a MAX98357A digital amplifier.
- Trips from "car on" to "car off", with distance, speeds, stops and the route from the phone's GPS,
  a spoken summary plus an AI remark, and the trip in the app with its map.
- Overspeed warnings ("Slow down. Speed limit 50.") from the phone's GPS speed against the last speed-limit
  sign: built and tested with a fake drive, still to be tried on a real ride.

Next up: photographing real Indian road signs and retraining the CNN with them, overspeed warnings
on a real ride, the motion sensor for harsh braking and potholes,
and a car-ready build (battery backup, cooling, the 3D-printed case).
