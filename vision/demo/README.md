# Prototype demo: road-sign alerts on a laptop webcam

This demo runs the real vision pipeline (camera check → find sign-shaped
regions → CNN → 3-of-5 vote) on a laptop webcam and speaks each confirmed sign.
A **speed slider** stands in for the GPS, so the overspeed warning can be shown too.
It needs no Raspberry Pi, no breadboard and no TensorFlow.

## 1. Set up (once)

In a terminal inside the `vision` folder:

```
pip install -r demo/requirements.txt
```

This installs `opencv-python`, `numpy`, `flask` (for the trip app) and, on Windows, `pywin32`, which lets the demo
use Windows' built-in voice directly. When the demo starts, the terminal should print
`[speech] using sapi`. If it says `powershell`, it still speaks, but about 1 s later.

## 2. Get the signs ready

Use either:
- **Paper:** print `demo/signs/print_signs_A4.pdf` (4 pages, 6 signs per page, about 8 cm each). Colour printing is needed, because the system looks for the red border. Cut a few out.
- **Phone:** open the PNGs in `demo/signs/` on your phone and hold it up. Turn the brightness up.

## 3. Run

```
python demo/webcam_demo.py
```

| Option | Use |
|---|---|
| `--camera 1` | Use an external USB webcam instead of the built-in one |
| `--no-speech` | Silent mode |
| `--save demo.avi` | Also record the screen to a video (a good backup for the presentation) |
| `--speed 60` | Start the speed slider at 60 km/h (default 40) |
| `--repeat 10` | Repeat the overspeed warning every 10 s instead of 30 s (handy in a short demo) |
| `--res 1280x720` | Camera resolution to ask for (default 1920x1080; if the webcam can't do it, the best it gives is used). The terminal prints `Camera resolution: ...` and the top bar shows it. Higher = signs recognised from further away; use 1280x720 if fps drops below about 10 |

Keys in the window: **Q** quit, **M** show what the colour filter sees, **S** save a screenshot,
**+ / −** change speed by 5 km/h (or drag the **Speed km/h** slider at the top of the window).
Hold signs near the centre of the frame: the speed panel sits in the top-right corner.

## 4. What to show (about 2 minutes)

1. **Detection and tracking:** hold up *Speed limit 50* about 40–80 cm from the camera and move it slowly. A box follows it, with the name and confidence on it.
2. **The vote:** point at the bottom strip. It fills 1/5 → 3/5, and only then does the banner appear and the laptop say "Speed limit 50". This is how false alerts are prevented.
3. **Once per sighting:** keep holding the sign. It is **not** repeated. Take it away for more than 1 s and show it again, and it's announced again.
4. **Not fooled by red things:** hold up something red (a red cloth or a red phone cover). It gets a grey "not a sign" box and no alert.
5. **Left vs right:** show *Left curve*, then *Right curve*. Both are told apart correctly.
6. **Camera blocked:** cover the webcam with your hand for 10 s. The laptop says "Sign alerts paused". Uncover it and it says "Sign alerts resumed".
7. **Overspeed (simulated GPS):** set the slider to 40, then show *Speed limit 50*. The panel shows limit 50, "within limit".
   Drag the slider to 70. The panel turns amber ("over limit…"), and after **3 s** it turns red, says
   "Slow down. Speed limit 50" and shows the banner (REQ-05: more than 5 km/h over, for 3 s).
   Slide back to 50 and it goes green. Then show *Speed limit 80* while at 70: the limit updates and there's no warning.
   The panel also shows distance left before the limit expires (REQ-19: 5 km).
8. **Speed:** the top bar shows the camera resolution, fps and the time taken per step in milliseconds.
9. **Range:** at 1080p a printed 8 cm sign is recognised from about 5 m (a full-A4 sign from about 12 m). The box label shows the sign's size in pixels; around 20 px is the far limit.

## 5. The language model (trip summaries and "Ask")

The model runs on the laptop itself, with no internet once it's downloaded. Set it up once:

1. Install **Ollama** from ollama.com (Windows installer).
2. In a terminal: `ollama pull qwen2.5:1.5b` (about 1 GB, one-time download).
3. That's all: Ollama runs in the background and the demo finds it automatically.

What it does:
- **When a trip ends** (press **T** or **Q**), the model writes the spoken summary from the trip data.
- **Ask page** in the app: questions such as "When did I go over the limit?" are answered from your last 20 trips.
- **It can't make numbers up.** Python gives the model the facts; every number in its answer is checked against
  them. If any number isn't in the data, it gets one retry, then the plain template summary is used instead.
  The app shows "Every number checked against the trip data" only on text that passed.
- No model running? Everything still works with the template summary and basic answers.

Try it from the terminal: `python demo/llm.py` (summarises the last trip) or `python demo/llm.py --ask "Did I speed?"`.
On the Raspberry Pi the same code talks to llama.cpp's `llama-server` with the smaller `qwen2.5-0.5b` model:
set `DC_LLM_URL=http://127.0.0.1:8080/v1` and `DC_LLM_MODEL=qwen2.5-0.5b`.

## 6. Trip app (phone)

When the demo starts, the terminal prints two addresses:

```
Trip app on this laptop:  http://127.0.0.1:5000
Trip app on your phone:   http://192.168.x.x:5000  (phone on the same Wi-Fi)
```

Open either in a browser.
- **Trips:** your last 20 trips, each with a small speed trace (red where you were over the limit).
- **A trip:** the summary, a full speed chart (hover or touch it to read the speed at any moment, with the
  limit from the last sign drawn underneath), top and average speed, time over the limit, harsh braking,
  every overspeed moment, and everything that happened along the way. A trip in progress updates every 5 s.
- **Ask:** questions about your driving, answered by the on-device model.

- Press **T** in the camera window to end the trip: the summary is spoken and saved, and a new trip starts.
- Quitting with **Q** also saves the trip. The camera closes but **the app stays open** so you can show the
  trip; press **Enter** in the terminal to close it (or start with `--exit` to close both at once).
- Dragging the speed slider down fast counts as harsh braking (in the car this comes from the IMU).
- Data is stored in `demo/trips.db` on the laptop only. Run `python demo/app_server.py` to open the app without the camera.
- If the phone can't open it: both must be on the same Wi-Fi, and Windows may ask to allow Python through the firewall (choose **Allow**). College Wi-Fi often blocks device-to-device traffic; a phone hotspot works.

## Tips if it misbehaves

- **Every announced sign is saved as a picture** in `demo\detections\` (time, sign, confidence in the name).
  If it ever announces something wrong, that picture shows exactly what fooled it. Turn off with `--no-snapshots`.

- **No box on the sign:** improve the lighting (face a window or a lamp), and hold it closer. Press **M**: the red ring should show up white in the mask.
- **Box but grey / low %:** hold the sign flatter to the camera and steadier, and reduce glare on a phone screen.
- **Window won't open** (`The function is not implemented`): run `pip uninstall -y opencv-python-headless opencv-python`, then `pip install opencv-python`.
- **Black window or wrong camera:** close Zoom, Teams or the Camera app, or try `--camera 1`.

## Be honest about what this is

- The CNN is trained on **drawn** IRC-style signs (`tools/make_signs.py`) placed into random scenes (`tools/make_dataset.py`). It scores 99.8% on that synthetic data. That shows the pipeline works end to end, **not** that it meets REQ-01 on real roads.
- Next step: photograph real Indian signs, add them to `data/`, and retrain with the same `tools/train_export.py`.
- On the car, the same pipeline runs on the Pi with the Camera Module 3 and the int8 TFLite model (`models/sign_cnn_int8.tflite`).
