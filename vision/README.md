# Vision pipeline

Part of Driver Companion (Group 7, EPD). This part is mine: Asir Ahmed (2430268).

This module watches the road through the Pi camera, recognises IRC:67 road signs,
and lets the other modules know when it's sure it has seen one. It doesn't speak
and doesn't touch the GPS; Abhay's speech module and Amrit's speed logic take it
from there.

## What happens to each frame

```
Camera Module 3 (15 fps, 640x480)
   │  capture.py: only the newest frame is kept, old ones are dropped (so lag never builds up)
   ▼
Camera health check        health.py: dark / washed-out / blurry for 10 s → "blinded"   (REQ-18)
   ▼
Region proposals           proposals.py: red + blue blobs of sign shape, top 65 % of frame, ≤4 crops
   ▼
CNN classifier             classifier.py: 32x32 input, 20 signs + "background", ~207k params (REQ-01)
   │  int8 TFLite on the Pi, plain NumPy on a laptop (same weights)
   │  a frame only votes if it's not background and confidence ≥ 0.80
   ▼
Temporal vote              voting.py: same class in 3 of the last 5 frames → confirmed     (REQ-03)
   │  each sign is announced once per sighting; a sighting ends after 1 s without it      (REQ-17)
   ▼
Publisher                  publisher.py: JSON over a local Unix socket, file mode 0600  (REQ-16, REQ-30)
```

**Want to see it live?** Check out [`demo/README.md`](demo/README.md). It runs all of this on a laptop webcam and speaks the alerts.

There's also a thermal guard (`throttle.py`): if the Pi says it's throttling, the camera drops to 10 fps (REQ-24).

## For the other modules: how to listen to vision

Socket: `/run/driver_companion/vision.sock` (Unix datagram).
Send the bytes `subscribe` to start getting events. If you don't want to write that yourself,
`tools/listen.py` is a ready-made listener you can copy. It keeps retrying until vision is up,
and subscribes again if vision restarts.

| Event | Example | Who does what |
|---|---|---|
| Sign confirmed | `{"type":"sign","label":"speed_limit_50","speed_kmh":50,"confidence":0.93,"latency_ms":41.2,...}` | Abhay: say it. Amrit: if `speed_kmh` is there, set it as the active limit |
| Camera blinded | `{"type":"camera","state":"blinded"}` | Abhay: say "Sign alerts paused". Amrit: keep the overspeed check running |
| Camera recovered | `{"type":"camera","state":"recovered"}` | Abhay: say "Sign alerts resumed" |
| Heartbeat (every 2 s) | `{"type":"heartbeat","fps":15.0,"throttled":false,"stage_ms":{...}}` | Watchdog: no heartbeat for 5 s → restart vision (REQ-22) |

`speed_kmh` only shows up on speed-limit signs. Keeping alerts at least 5 s apart (REQ-17)
and expiring the limit (REQ-19) happen in the speech and speed modules, not here.

## On the device: `device/dc_vision`

On the Raspberry Pi this pipeline runs as the `dc_vision` module of the device software
(see [`device/README.md`](../device/README.md)). `dc_vision` builds the same `VisionPipeline`
but hands it a publisher that puts each event on the device's message board instead of the socket:

| Pipeline event | What `dc_vision` does |
|---|---|
| `sign` | asks the speech module to say it ("Speed limit 50."), counts it for the trip, and for a speed-limit sign keeps the limit on `road/speed_limit` |
| `camera` blinded / recovered | says "Sign alerts paused." / "Sign alerts resumed." |
| no frames at all | says "Camera not found. Sign alerts are off." once and tries again every 30 s |

It starts the camera when the ignition comes on and stops it when it goes off, uses the TFLite
model when TFLite is installed (the NumPy one otherwise), and restarts the pipeline if it ever fails.
The installer copies `pipeline/` and `models/` to `/opt/driver-companion/vision`.
The socket publisher below is still there for running the pipeline on its own.

## Running it

On the Pi:
```
pip install ai-edge-litert opencv-python-headless numpy    # picamera2 already comes with Pi OS
python -m pipeline.main --model models/sign_cnn_int8.tflite
```
On a laptop, with a dash-cam clip or webcam (no Pi needed):
```
python -m pipeline.main --source drive.mp4 --model models/sign_cnn_int8.tflite --socket /tmp/vision.sock
python tools/listen.py /tmp/vision.sock          # in a second terminal
```
Tests (you don't need the model or a camera for these):
```
python -m unittest discover -s tests -v
```

## Training the model

Do this on a laptop or Colab, not on the Pi:
```
python tools/make_signs.py --out demo/signs                    # draws the 20 signs + a printable PDF
python tools/make_dataset.py --signs demo/signs --out data     # makes ~19k training crops
python tools/train_export.py --data data --out models/sign_cnn_int8.tflite
```
The last step writes `models/sign_cnn_int8.tflite` (for the Pi) and `models/sign_cnn_int8.npz`
(rename that one to `sign_cnn.npz` for the laptop demo).

A couple of things worth knowing:
- `make_dataset.py` runs the real `propose()` step on every scene it generates, so the CNN
  learns from exactly the kind of crops it'll see live. Crops that land on red things that
  aren't signs become the `background` class.
- Folder names have to match `pipeline/config.py → CLASSES`, and in the same order as the model.
- We deliberately **don't** flip images left-right during training, because that would turn
  `left_curve` into `right_curve`.

To add real photos later, just drop the crops into `data/train/<label>/` next to the generated ones and retrain.

## How fast is it? (REQ-02: speak within 1.0 s)

| Step | Time |
|---|---|
| Voting: 3rd matching frame at 15 fps | 2 frame gaps = 133 ms after it first sees the sign |
| Processing per frame on a laptop (measured) | ~4–6 ms (finding regions ~2–3 ms, CNN ~1–4 ms) |
| Processing per frame on the Pi 3B+ (measured, int8 TFLite, 640x480) | ~60 ms: health 7 ms, finding regions 33 ms, CNN 17–22 ms. A sign was confirmed 40–56 ms after its deciding frame |
| Speech starting | the speech module plays a ready-made clip of each sign (Piper voice), so it starts at once |

On the Pi the camera runs at 15 fps, or 10 fps when the Pi is hot (REQ-24: it slows itself above 60 °C;
a heatsink on the main chip keeps it at 15).

## What we've tested so far

- **49 automatic tests pass:** voting, camera health, proposals, socket permissions, the trained model,
  speed rules, trip log, trip app, the LLM fact-check (against a stand-in model server), the shape
  check and the far-sign pass.
- **The prototype model** has 207,189 parameters and gets 98.6% on synthetic validation crops, including tiny,
  blurry, far-away signs. (These are drawn signs only, not real roads.)
- **How far away it can recognise signs** (`tools/range_test.py`, 60 cluttered frames per size). "Correct" here means
  the sign was found AND passed every check the live pipeline uses (CNN confidence and margin, shape and colour):

  | Sign size | Distance, 75 cm sign, 720p* | 720p before far pass + face check | 720p now | 1080p now (same size = 1.5x further) |
  |---|---|---|---|---|
  | 14 px | ~49 m | 13% | 20% | 30% (~73 m) |
  | 16 px | ~43 m | 15% | 40% | 38% (~64 m) |
  | 20 px | ~34 m | 47% | 63% | 70% (~51 m) |
  | 24 px | ~29 m | 58% | 70% | 72% (~43 m) |
  | 32 px | ~21 m | 62% | 77% | 78% (~32 m) |
  | 64 px | ~11 m | 75% | 90% | 85% (~16 m) |

  *Assuming a 70 degree field of view and the pinhole rule: distance = focal length x sign size / pixels.
  We made the test scenes extra cluttered with red and blue shapes on purpose, so real roads should be easier.
  At the same real distance, 1080p roughly doubles the hit rate at ~43 m (40% → 72%). For the printed 8 cm
  demo signs, that works out to about 5 m at 1080p versus about 3.5 m at 720p.
- **The far-sign pass** (`pipeline/proposals.py`): at 30–50 m, a sign's red ring is only 1–2 px thick, and blur plus the
  camera's colour compression wash it out below the normal "is this red?" cut-off. So there's a second, gentler
  red-only filter that looks just at small blobs (11–28 px). It gets 2 extra slots of its own, so it never pushes out a
  normal candidate, and anything it finds still has to pass the same CNN, shape and 3-of-5 checks.
- **Other things we changed along the way:** the camera now runs at 1280x720; the detector also looks for the
  **white hole inside a red ring** (which catches signs whose ring blends into red or blue things behind them);
  candidates are ranked by how white their centre is; holes inside a sign (like the letters in STOP) are ignored;
  and the CNN was retrained with signs as small as 10 px, plus partial signs as "background" examples.
- **Speed at 1280x720 on a laptop:** about 15 ms to find regions and 7 ms for the CNN per frame. The demo now
  asks the webcam for 1920x1080 (and falls back to whatever's best if it can't) and prints what it actually got.
- **Webcam-style test clip:** all 7 signs were announced exactly once each, and a red shirt and a face were never announced.
- **False alarms** (`tools/false_alarm_test.py`: a busy room full of red and blue objects, logos, mugs, posters
  and faces, but NO signs): 0 false announcements in 12 minutes across three rooms. Before we added the shape
  check, it was 3 in 4 minutes (red boxes with white writing read as STOP, and a blue mug as Speed limit 100).
  We checked again after the far-sign pass and face check: still 0 in 4 minutes. A tougher test
  (`--far-clutter`: dozens of small faded reddish objects plus webcam compression) gives 3 in 4 minutes,
  exactly the same as before these changes. All 3 are a blank red triangle being read as "speed breaker"
  (see the to-do list below).
- **Holding one sign for 30 s while moving and tilting it:** announced exactly once, and never mistaken for a different sign.
- **Live on the Pi 3B+ (2 Nov 2026):** with the Pi camera (OV5647), printed Speed limit 20 and Men at work signs were read
  with confidence 1.00, 38 ms and 56 ms after the deciding frame, through the whole device (camera → CNN → message board → speech).

### Why it doesn't get fooled easily (three separate checks)

1. **The CNN has to be confident and clear about it:** at least 85% sure, and at least 60 points ahead of its second guess.
2. **The shape and colour have to agree** (`pipeline/verify.py`): every one of our 20 signs has a red border that's either
   round (a circle, or STOP's octagon) or a triangle. So mostly-blue objects, red rectangles and wrong shapes get rejected.
   If something red behind the sign messes up the outline, the white face inside the ring (circle or
   triangle) decides instead. Letters on a red board can't pass as a face, because they have red gaps inside.
3. **A sign can't suddenly change into another one:** once a sign is confirmed in a spot, a different label in that same
   spot is ignored while it stays in view. This stops a "50" from flipping to "100".

## Still to do

- Get real IRC:67 photos and measure accuracy on real roads (REQ-01 needs at least 90% top-1 in daylight).
- Time everything on the actual Pi 3B+.
- Tune the HSV colour thresholds on real Indian dash-cam footage (faded signs, dusk).
- Right now it only finds signs with a red or blue border, so other signs will be missed.
- A blank red triangle (like the warning triangle people put behind a broken-down car) gets read as
  "speed breaker". The fix is to retrain with blank signs as "background" examples.
