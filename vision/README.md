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
CNN classifier             classifier.py: int8 TFLite, 32x32 input, 20 classes, ~207k params (REQ-01)
   │  frame vote cast only if confidence ≥ 0.80
   ▼
Temporal vote              voting.py: same class in 3 of last 5 frames → confirmed        (REQ-03)
   │  each sign announced once per sighting; sighting ends after 1 s absence              (REQ-17)
   ▼
Publisher                  publisher.py: JSON over local Unix socket, file mode 0600  (REQ-16, REQ-30)
```

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
python tools/train_export.py --data data --out models/sign_cnn_int8.tflite
```
`data/train/<label>/` and `data/val/<label>/`, with folder names exactly as in
`pipeline/config.py → LABELS`. The class order there must match the model.
Horizontal flip is deliberately **not** used in augmentation: it would turn
`left_curve` into `right_curve`.

## Latency budget (REQ-02: speak within 1.0 s)

| Step | Time |
|---|---|
| Voting: 3rd matching frame at 15 fps | 2 frame gaps = 133 ms after the first sighting |
| Per-frame processing on laptop (measured) | ~3–4 ms |
| Per-frame processing on Pi 3B+ | to be measured in week 5; `stage_ms` in the heartbeat reports it |
| Speech playback start | Abhay's module (pre-rendered clips) |

## Verified so far

- 17 unit tests pass: voting rules, camera health, proposals, socket permissions, end-to-end.
- Training script → int8 export works (207,092 parameters, 224 KB file), checked on synthetic data only.
- Full threaded run on a synthetic 10 s drive video: speed_limit_60 and stop each announced exactly once, 15 fps held.

## Not yet done

- Real IRC:67 dataset and real-road accuracy (REQ-01 needs ≥90 % top-1 in daylight).
- Timing on the actual Pi 3B+.
- HSV colour thresholds need tuning on real Indian dash-cam footage (faded signs, dusk).
- Octagonal STOP and triangular warning signs rely on the red border. Signs without red or blue borders will be missed.
