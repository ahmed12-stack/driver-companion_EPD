# Driver Companion: Vision Pipeline

Group 7, EPD. Module owner: Asir Ahmed (2430268).

This module watches the road through the Pi camera, recognises IRC:67 road signs,
and tells the other modules when a sign is confirmed. It never speaks and never
reads GPS. Speech (Abhay) and speed logic (Amrit) act on its events.

## How a frame flows

```
Camera Module 3 (15 fps, 640x480)
   │  capture.py: newest-frame-only mailbox, old frames dropped (no lag build-up)
   ▼
Camera health check        health.py: dark / washed-out / blurry for 10 s → "blinded"   (REQ-18)
   ▼
Region proposals           proposals.py: red + blue blobs of sign shape, top 65 % of frame, ≤4 crops
   ▼
CNN classifier             classifier.py: 32x32 input, 20 signs + "background", ~207k params (REQ-01)
   │  int8 TFLite on the Pi, plain NumPy on a laptop (same weights)
   │  frame vote cast only if not background and confidence ≥ 0.80
   ▼
Temporal vote              voting.py: same class in 3 of last 5 frames → confirmed        (REQ-03)
   │  each sign announced once per sighting; sighting ends after 1 s absence              (REQ-17)
   ▼
Publisher                  publisher.py: JSON over local Unix socket, file mode 0600  (REQ-16, REQ-30)
```

**Prototype demo:** see [`demo/README.md`](demo/README.md) to run all of this live on a laptop webcam with spoken alerts.

Thermal guard (`throttle.py`): if the Pi reports throttling, the camera drops to 10 fps (REQ-24).

## Interface for other modules (the contract)

Socket: `/run/driver_companion/vision.sock` (Unix datagram).
Subscribe by sending the bytes `subscribe`. `tools/listen.py` is a ready-made
subscriber you can copy. It retries until vision is up and resubscribes if vision restarts.

| Event | Example | Who acts |
|---|---|---|
| Sign confirmed | `{"type":"sign","label":"speed_limit_50","speed_kmh":50,"confidence":0.93,"latency_ms":41.2,...}` | Abhay: speak it. Amrit: if `speed_kmh` present, set active limit |
| Camera blinded | `{"type":"camera","state":"blinded"}` | Abhay: say "Sign alerts paused". Amrit: keep overspeed going |
| Camera recovered | `{"type":"camera","state":"recovered"}` | Abhay: say "Sign alerts resumed" |
| Heartbeat (every 2 s) | `{"type":"heartbeat","fps":15.0,"throttled":false,"stage_ms":{...}}` | Watchdog: no heartbeat for 5 s → restart vision (REQ-22) |

`speed_kmh` appears only on speed-limit signs. Spacing alerts ≥5 s apart (REQ-17)
and limit expiry (REQ-19) are handled by the speech / speed modules, not here.

## Running

On the Pi:
```
pip install ai-edge-litert opencv-python-headless numpy    # picamera2 comes with Pi OS
python -m pipeline.main --model models/sign_cnn_int8.tflite
```
On a laptop, against a dash-cam clip or webcam (no Pi needed):
```
python -m pipeline.main --source drive.mp4 --model models/sign_cnn_int8.tflite --socket /tmp/vision.sock
python tools/listen.py /tmp/vision.sock          # in another terminal
```
Tests (no model or camera needed):
```
python -m unittest discover -s tests -v
```

## Training the model

Run on a laptop or Colab, not on the Pi:
```
python tools/make_signs.py --out demo/signs                    # draw the 20 signs + printable PDF
python tools/make_dataset.py --signs demo/signs --out data     # ~19k training crops
python tools/train_export.py --data data --out models/sign_cnn_int8.tflite
```
The last step writes `models/sign_cnn_int8.tflite` (Pi) and `models/sign_cnn_int8.npz`
(rename it to `sign_cnn.npz` for the laptop demo).

`make_dataset.py` runs the real `propose()` step on each generated scene, so the
CNN learns from exactly the crops it will see live. Crops that land on red
things which are not signs become the `background` class.
Folder names must match `pipeline/config.py → CLASSES`, and that order must match the model.
Horizontal flip is deliberately **not** used in augmentation: it would turn
`left_curve` into `right_curve`.

To add real photos later, put crops in `data/train/<label>/` next to the generated ones and retrain.

## Latency budget (REQ-02: speak within 1.0 s)

| Step | Time |
|---|---|
| Voting: 3rd matching frame at 15 fps | 2 frame gaps = 133 ms after the first sighting |
| Per-frame processing on laptop (measured) | ~4–6 ms (find regions ~2–3 ms, CNN ~1–4 ms) |
| Per-frame processing on Pi 3B+ | to be measured; `stage_ms` in the heartbeat reports it |
| Speech playback start | Abhay's module (pre-rendered clips) |

## Verified so far

- 49 automatic tests pass: voting, camera health, proposals, socket permissions, trained model,
  speed rules, trip log, trip app, the LLM fact-check (against a stand-in model server), the shape
  check and the far-sign pass.
- Prototype model: 207,189 parameters, 98.6% on synthetic validation crops that include tiny,
  blurry, far-away signs (drawn signs only, not real roads).
- **Recognition range** (`tools/range_test.py`, 60 cluttered frames per size). "Correct" means the sign
  was found AND passed every check the live pipeline uses (CNN confidence and margin, shape and colour):

  | Sign size | Distance, 75 cm sign, 720p* | 720p before far pass + face check | 720p now | 1080p now (same size = 1.5x further) |
  |---|---|---|---|---|
  | 14 px | ~49 m | 13% | 20% | 30% (~73 m) |
  | 16 px | ~43 m | 15% | 40% | 38% (~64 m) |
  | 20 px | ~34 m | 47% | 63% | 70% (~51 m) |
  | 24 px | ~29 m | 58% | 70% | 72% (~43 m) |
  | 32 px | ~21 m | 62% | 77% | 78% (~32 m) |
  | 64 px | ~11 m | 75% | 90% | 85% (~16 m) |

  *70 degree field of view, pinhole rule distance = focal length x sign size / pixels. The test scenes are
  deliberately cluttered with red and blue shapes; real roads are easier. At the same real distance, 1080p
  roughly doubles the hit rate at ~43 m (40% -> 72%). For the demo's printed 8 cm signs, that is about
  5 m at 1080p versus about 3.5 m at 720p.
- **Far-sign pass** (`pipeline/proposals.py`): at 30-50 m a sign's red ring is 1-2 px thick, and blur and the
  camera's colour compression wash it out below the normal "is this red?" threshold. A second, gentler
  red-only filter looks at small blobs (11-28 px) in 2 extra slots, so it never pushes out a normal candidate.
  Its finds face the same CNN, shape and 3-of-5 checks.
- What changed: the camera runs at 1280x720; the detector also looks for the **white hole inside a red ring**
  (finds signs whose ring merges with red/blue things behind them); candidates are ranked by how white
  their centre is; holes inside a sign (the letters of STOP) are ignored; and the CNN was retrained with
  signs down to 10 px plus partial-sign "background" examples.
- Speed at 1280x720 on a laptop: about 15 ms to find regions + 7 ms for the CNN per frame. The demo now
  asks the webcam for 1920x1080 (falls back to its best if it can't) and prints what it got.
- Webcam-style test clip: all 7 signs announced exactly once each; red shirt and face never announced.
- **False alarms** (`tools/false_alarm_test.py`, a busy room with red/blue objects, logos, mugs, posters,
  faces and NO signs): 0 false announcements in 12 minutes across three rooms. Before the shape check
  was added: 3 in 4 minutes (red boxes with white writing read as STOP, a blue mug as Speed limit 100).
  Re-checked after the far-sign pass and face check: still 0 in 4 minutes. A harder test
  (`--far-clutter`: dozens of small faded reddish objects, webcam compression) gives 3 in 4 minutes,
  exactly the same as before these changes; all 3 are a blank red triangle read as "speed breaker"
  (see Not yet done).
- **One sign held for 30 s while moving and tilting**: announced exactly once, never as a different sign.

### Why it doesn't get fooled (three independent checks)

1. **CNN confidence and margin:** at least 85% sure, and at least 60 points ahead of its second guess.
2. **Shape and colour agree** (`pipeline/verify.py`): all 20 signs have a red border whose outline is round
   (circle, or STOP's octagon) or a triangle. Mostly blue objects, red rectangles and wrong shapes are rejected.
   If a red object behind the sign spoils the outline, the white face inside the ring (circle or
   triangle) decides instead; letters on a red board can't pass as a face (they have red gaps inside).
3. **A sign can't change identity:** once a sign is confirmed at a spot, a different label at that spot is
   ignored while it stays in view (stops "50" flipping to "100").

## Not yet done

- Real IRC:67 photos and real-road accuracy (REQ-01 needs ≥90% top-1 in daylight).
- Timing on the actual Pi 3B+.
- HSV colour thresholds need tuning on real Indian dash-cam footage (faded signs, dusk).
- Only red- or blue-bordered signs are found. Others will be missed.
- A blank red triangle (like the warning triangle placed behind a broken-down car) is read as
  "speed breaker". Fix: retrain with blank signs as "background" examples.
