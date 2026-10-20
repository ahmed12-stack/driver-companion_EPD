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

This installs `opencv-python`, `numpy` and, on Windows, `pywin32`, which lets the demo
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
8. **Speed:** the top bar shows fps and the time taken per step in milliseconds.

## Tips if it misbehaves

- **No box on the sign:** improve the lighting (face a window or a lamp), and hold it closer. Press **M**: the red ring should show up white in the mask.
- **Box but grey / low %:** hold the sign flatter to the camera and steadier, and reduce glare on a phone screen.
- **Window won't open** (`The function is not implemented`): run `pip uninstall -y opencv-python-headless opencv-python`, then `pip install opencv-python`.
- **Black window or wrong camera:** close Zoom, Teams or the Camera app, or try `--camera 1`.

## Be honest about what this is

- The CNN is trained on **drawn** IRC-style signs (`tools/make_signs.py`) placed into random scenes (`tools/make_dataset.py`). It scores 99.8% on that synthetic data. That shows the pipeline works end to end, **not** that it meets REQ-01 on real roads.
- Next step: photograph real Indian signs, add them to `data/`, and retrain with the same `tools/train_export.py`.
- On the car, the same pipeline runs on the Pi with the Camera Module 3 and the int8 TFLite model (`models/sign_cnn_int8.tflite`).
