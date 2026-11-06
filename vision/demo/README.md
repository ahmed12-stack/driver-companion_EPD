# Webcam demo

This lets you try the whole sign-recognition pipeline on a laptop webcam: no
Raspberry Pi, no breadboard, no TensorFlow. It checks the camera, finds
sign-shaped regions, runs our CNN, waits for 3 out of 5 frames to agree, and
then says the sign out loud.

Since a laptop has no GPS, there's a **speed slider** you can drag around to
pretend you're driving. That way you can also see the overspeed warning in action.

## 1. Set up (just once)

Open a terminal inside the `vision` folder and run:

```
pip install -r demo/requirements.txt
```

That gets you `opencv-python`, `numpy`, `flask` (for the trip app) and, on
Windows, `pywin32`, which lets the demo use Windows' built-in voice directly.
When the demo starts, the terminal should say `[speech] using sapi`. If it says
`powershell` instead, that's fine too; it just speaks about a second later.

## 2. Get some signs ready

You've got two options:
- **Paper:** print `demo/signs/print_signs_A4.pdf` (4 pages, 6 signs per page, about 8 cm each). It has to be in colour, because the system looks for the red border. Cut a few out.
- **Phone:** open the PNGs in `demo/signs/` on your phone and hold it up to the camera. Turn the brightness up.

## 3. Run it

```
python demo/webcam_demo.py
```

Some handy options:

| Option | What it does |
|---|---|
| `--camera 1` | Use an external USB webcam instead of the built-in one |
| `--no-speech` | Keep it quiet |
| `--save demo.avi` | Record the screen to a video too (a nice backup for presentations) |
| `--speed 60` | Start the speed slider at 60 km/h (default is 40) |
| `--repeat 10` | Repeat the overspeed warning every 10 s instead of 30 s (good for a short demo) |
| `--res 1280x720` | Camera resolution to ask for. The default is 1920x1080; if your webcam can't do that, it uses the best it can. The terminal prints `Camera resolution: ...` and the top bar shows it too. Higher means signs are recognised from further away. If fps drops below about 10, switch to 1280x720 |

While the window is open:
- **Q** quits
- **M** shows what the colour filter sees
- **S** saves a screenshot
- **+ / −** changes speed by 5 km/h (or drag the **Speed km/h** slider at the top)

Try to hold signs near the middle of the frame, because the speed panel takes up the top-right corner.

## 4. Things to show off (about 2 minutes)

1. **It finds and follows signs.** Hold up *Speed limit 50* about 40–80 cm from the camera and move it around slowly. A box follows it, showing the name and how confident it is.
2. **It waits before speaking.** Look at the strip along the bottom. It fills up 1/5 → 3/5, and only then does the banner pop up and the laptop say "Speed limit 50". This is how we avoid false alerts.
3. **It only says each sign once.** Keep holding the sign up and it won't repeat itself. Take it away for more than a second, bring it back, and it'll announce it again.
4. **Red things don't fool it.** Hold up something red, like a red cloth or a phone cover. It gets a grey "not a sign" box and no alert.
5. **It knows left from right.** Show *Left curve*, then *Right curve*. It gets both right.
6. **It notices when the camera is blocked.** Cover the webcam with your hand for 10 seconds and you'll hear "Sign alerts paused". Uncover it and it says "Sign alerts resumed".
7. **Overspeed warning (with the fake GPS).** Set the slider to 40, then show *Speed limit 50*. The panel shows limit 50, "within limit".
   Now drag the slider to 70. The panel turns amber ("over limit…"), and after **3 seconds** it goes red, says
   "Slow down. Speed limit 50" and shows the banner (that's REQ-05: more than 5 km/h over, for 3 s).
   Slide back to 50 and it turns green again. Then show *Speed limit 80* while you're at 70: the limit updates and there's no warning.
   The panel also shows how far you can go before the limit expires (REQ-19: 5 km).
8. **How fast it runs.** The top bar shows the camera resolution, fps, and how many milliseconds each step takes.
9. **How far it can see.** At 1080p, a printed 8 cm sign is recognised from about 5 m away (a full A4 sign from about 12 m). The box label shows the sign's size in pixels; around 20 px is about as small as it can handle.

## 5. The language model (trip summaries and "Ask")

The model runs on your laptop. Once it's downloaded, no internet is needed. To set it up:

1. Install **Ollama** from ollama.com (there's a Windows installer).
2. In a terminal, run `ollama pull qwen2.5:1.5b` (about 1 GB, you only download it once).
3. That's it. Ollama keeps running in the background and the demo finds it on its own.

What it does:
- **At the end of a trip** (press **T** or **Q**), it writes the spoken summary from your trip data.
- **The Ask page** in the app lets you ask things like "When did I go over the limit?", and it answers from your last 20 trips.
- **It can't make up numbers.** Python hands the model the facts, and every number in its answer gets checked against them. If something doesn't match, it gets one more try, and if that fails too we fall back to a plain template summary.
  The app only shows "Every number checked against the trip data" on text that passed.
- No model running? No problem. Everything still works, you just get the template summary and simpler answers.

You can also try it straight from the terminal: `python demo/llm.py` (summarises your last trip) or `python demo/llm.py --ask "Did I speed?"`.

On the Raspberry Pi, the same code talks to llama.cpp's `llama-server` with the smaller `qwen2.5-0.5b` model.
Just set `DC_LLM_URL=http://127.0.0.1:8080/v1` and `DC_LLM_MODEL=qwen2.5-0.5b`.

## 6. The trip app (on your phone)

When the demo starts, the terminal shows two addresses:

```
Trip app on this laptop:  http://127.0.0.1:5000
Trip app on your phone:   http://192.168.x.x:5000  (phone on the same Wi-Fi)
```

Open either one in a browser. You'll find:
- **Trips:** your last 20 trips, each with a little speed graph (red wherever you went over the limit).
- **A single trip:** the summary, a full speed chart (hover or tap anywhere to see your speed at that moment, with the
  limit from the last sign drawn underneath), top and average speed, time over the limit, harsh braking,
  every overspeed moment, and everything else that happened along the way. If the trip is still going, it refreshes every 5 seconds.
- **Ask:** ask questions about your driving, answered by the model on the device.

A few more things to know:
- Press **T** in the camera window to end the trip. The summary is spoken and saved, and a new trip starts.
- Quitting with **Q** saves the trip too. The camera closes but **the app stays open**, so you can still show the trip.
  Press **Enter** in the terminal when you're done (or start with `--exit` to close both together).
- Dragging the speed slider down quickly counts as harsh braking (in the car, the IMU will handle this).
- All the data stays in `demo/trips.db` on your laptop. Run `python demo/app_server.py` if you want the app without the camera.
- If your phone can't open it, make sure both are on the same Wi-Fi. Windows might ask whether to let Python through the firewall; choose **Allow**. College Wi-Fi often blocks devices from talking to each other, so a phone hotspot is the easy fix.

## If something goes wrong

- **Every sign it announces gets saved as a picture** in `demo\detections\` (the file name has the time, sign and confidence).
  So if it ever says something wrong, you can see exactly what confused it. Use `--no-snapshots` to turn this off.
- **No box around the sign?** Get better light (face a window or a lamp) and hold it closer. Press **M**: the red ring should show up white in the mask.
- **There's a box, but it's grey or the % is low?** Hold the sign flatter and steadier, and cut down glare if you're using a phone screen.
- **Window won't open** (`The function is not implemented`)? Run `pip uninstall -y opencv-python-headless opencv-python`, then `pip install opencv-python`.
- **Black window or the wrong camera?** Close Zoom, Teams or the Camera app, or try `--camera 1`.

## Being honest about where this is

- The CNN has only been trained on **drawn** signs in the IRC style (`tools/make_signs.py`), dropped into random scenes (`tools/make_dataset.py`). It gets 99.8% on that made-up data. That tells us the pipeline works from start to finish, but **not** that it meets REQ-01 on real roads yet.
- Next up: take photos of real Indian road signs, add them to `data/`, and retrain with the same `tools/train_export.py`.
- In the car, the same pipeline will run on the Pi with the Camera Module 3 and the int8 TFLite model (`models/sign_cnn_int8.tflite`).
