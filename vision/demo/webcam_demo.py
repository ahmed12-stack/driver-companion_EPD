"""Driver Companion - live prototype demo on a laptop webcam.

Hold a printed sign (or a phone showing one) in front of the camera:
  * every red/blue sign-shaped region gets a box that follows it,
  * the CNN's guess and confidence are written on the box
    (green = counts as a vote, grey = ignored: background or < 80 % sure),
  * the 3-of-5 vote strip at the bottom fills up,
  * when the vote passes, a banner appears and the laptop SPEAKS the alert.
Cover the camera for 10 s and it says "Sign alerts paused".
Drag the "Speed" slider above a detected speed limit (by more than 5 km/h
for 3 s) and it warns "Slow down" (simulated GPS, see speed_sim.py).

This runs the SAME pipeline code as the Pi (health check, proposals, CNN,
voting); only the camera source and the speaker are laptop versions.

    python demo/webcam_demo.py                    # default camera, speech on
    python demo/webcam_demo.py --camera 1         # external USB webcam
    python demo/webcam_demo.py --no-speech

Every sign, overspeed and harsh-braking event is saved to demo/trips.db and
shown in the trip app (app_server.py), which starts automatically: open the
address printed in the terminal on the laptop or on a phone on the same Wi-Fi.

Keys:  Q / Esc quit    M show colour mask    S save screenshot
       T end this trip (speaks the summary) and start a new one
       + / - change speed by 5 km/h (same as the slider)
"""
import argparse
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.classifier import load_classifier          # noqa: E402
from pipeline.config import Config                        # noqa: E402
from pipeline.main import VisionPipeline                  # noqa: E402
from pipeline.proposals import colour_mask                # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from speed_sim import SpeedMonitor                        # noqa: E402
from trip_log import TripLog                              # noqa: E402
import llm                                                # noqa: E402


SNAP_DIR = Path(__file__).resolve().parent / "detections"


def save_snapshots(bgr, pipe, confirmed):
    """Save what the camera saw for every announced sign: demo/detections/
    <time>_<sign>_<confidence>.jpg (the sign with some surroundings). Useful as
    evidence, and to see exactly what caused a wrong alert."""
    SNAP_DIR.mkdir(exist_ok=True)
    H, W = bgr.shape[:2]
    for (x, y, w, h), label, conf, counted in pipe.last_detections:
        if label in confirmed and counted:
            p = int(0.8 * max(w, h))
            crop = bgr[max(0, y - p):min(H, y + h + p), max(0, x - p):min(W, x + w + p)].copy()
            cv2.rectangle(crop, (x - max(0, x - p), y - max(0, y - p)),
                          (x - max(0, x - p) + w, y - max(0, y - p) + h), (60, 220, 90), 2)
            name = f"{time.strftime('%H%M%S')}_{label}_{int(conf * 100)}.jpg"
            cv2.imwrite(str(SNAP_DIR / name), crop)


def finish_trip(trip, pub, wait=False):
    """End the trip: save the template summary at once, then let the LLM write a
    fact-checked one in the background and speak whichever is ready."""
    done = trip.end_trip()
    pub.alert("Trip saved. Writing your summary.", colour=(120, 70, 20),
              banner="Trip saved", speak=False)

    def work():
        con = llm.sqlite3.connect(str(trip.db_path))
        r = llm.summarize_trip(con, done["trip_id"], done["summary"])
        con.close()
        trip.set_summary(done["trip_id"], r["text"], r["source"], r["verified"])
        tag = "LLM, fact-checked" if r["source"] == "llm" else "template"
        print(f"[trip {done['trip_id']} summary, {tag}] {r['text']}")
        pub.alert(r["text"], colour=(120, 70, 20), banner="Trip summary")

    th = threading.Thread(target=work, daemon=True)
    th.start()
    if wait:
        th.join(timeout=90)
        time.sleep(0.5)

# What the driver hears for each sign
PHRASES = {
    "stop": "Stop sign ahead", "give_way": "Give way",
    "no_entry": "No entry", "no_overtaking": "No overtaking",
    "no_u_turn": "No U turn", "no_horn": "No horn zone",
    "no_parking": "No parking", "school_ahead": "School ahead, slow down",
    "pedestrian_crossing": "Pedestrian crossing ahead",
    "speed_breaker": "Speed breaker ahead", "left_curve": "Left curve ahead",
    "right_curve": "Right curve ahead", "men_at_work": "Men at work ahead",
}


def phrase(label):
    if label.startswith("speed_limit_"):
        return f"Speed limit {label.split('_')[-1]}"
    return PHRASES.get(label, label.replace("_", " "))


# ----------------------------------------------------------------- speech
class Speaker(threading.Thread):
    """Speaks in the background so the video never freezes.

    Windows: talks to the built-in SAPI voice directly (via pywin32).
      We do NOT use pyttsx3's runAndWait() on Windows: it is known to speak the
      first sentence and then silently drop every later one.
    Fallbacks: PowerShell's System.Speech, macOS `say`, espeak, a fresh pyttsx3
    engine per sentence, or print-only.
    """

    def __init__(self, enabled=True):
        super().__init__(daemon=True)
        self.q = queue.Queue()
        self.enabled = enabled
        self.backend = "off"
        self.start()

    def say(self, text):
        print(f"[SPEAK] {text}")
        if self.enabled:
            while not self.q.empty():          # never queue stale alerts
                try:
                    self.q.get_nowait()
                except queue.Empty:
                    break
            self.q.put(text)

    def _pick_backend(self):
        if os.name == "nt":
            try:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()       # COM must be set up in THIS thread
                self.voice = win32com.client.Dispatch("SAPI.SpVoice")
                self.voice.Rate = 1
                return "sapi"
            except Exception:
                return "powershell"
        if shutil.which("say"):
            return "say"
        if shutil.which("espeak-ng") or shutil.which("espeak"):
            return "espeak"
        try:
            import pyttsx3  # noqa: F401
            return "pyttsx3"
        except Exception:
            return "print-only"

    def _speak(self, text):
        if self.backend == "sapi":
            self.voice.Speak(text)             # blocks until finished, every time
        elif self.backend == "powershell":
            ps = ("Add-Type -AssemblyName System.Speech; "
                  "(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
                  f".Speak('{text}')")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           creationflags=0x08000000)   # no console window
        elif self.backend == "say":
            subprocess.run(["say", text])
        elif self.backend == "espeak":
            subprocess.run([shutil.which("espeak-ng") or "espeak", text])
        elif self.backend == "pyttsx3":
            import pyttsx3
            engine = pyttsx3.init()            # fresh engine each time avoids the hang
            engine.setProperty("rate", 175)
            engine.say(text)
            engine.runAndWait()
            engine.stop()
            del engine

    def run(self):
        if not self.enabled:
            return
        self.backend = self._pick_backend()
        print(f"[speech] using {self.backend}")
        while True:
            text = self.q.get()
            try:
                self._speak(text)
            except Exception as e:
                print("speech failed:", e)


class DemoPublisher:
    """Stands in for the socket: sends alerts to the speaker and the screen."""

    def __init__(self, speaker, monitor, trip=None):
        self.speaker = speaker
        self.monitor = monitor
        self.trip = trip                             # TripLog, or None
        self.app_url = None
        self.banner, self.banner_until = "", 0.0
        self.banner_colour = (0, 0, 200)
        self.history = deque(maxlen=6)

    def alert(self, text, colour=(0, 0, 200), speak=True, banner=None):
        self.banner, self.banner_until = (banner or text).upper(), time.monotonic() + 3.0
        self.banner_colour = colour
        self.history.appendleft(f"{time.strftime('%H:%M:%S')}  {text}")
        if speak:
            self.speaker.say(text)

    def send(self, ev):
        now = time.monotonic()
        if ev["type"] == "sign":
            text = phrase(ev["label"])
            self.banner, self.banner_until = text.upper(), now + 3.0
            self.history.appendleft(f"{time.strftime('%H:%M:%S')}  {text}  "
                                    f"({ev['confidence']:.0%}, {ev['latency_ms']:.0f} ms)")
            self.banner_colour = (0, 0, 200)
            if "speed_kmh" in ev:                    # tell the speed logic (Amrit's part)
                self.monitor.set_limit(ev["speed_kmh"], now)
            if self.trip:
                self.trip.log_sign(ev["label"])
            self.speaker.say(text)
        elif ev["type"] == "camera":
            text = "Sign alerts paused" if ev["state"] == "blinded" else "Sign alerts resumed"
            if self.trip:
                self.trip.log_camera(ev["state"])
            self.banner, self.banner_until = text.upper(), now + 3.0
            self.history.appendleft(f"{time.strftime('%H:%M:%S')}  {text}")
            self.speaker.say(text)


# ----------------------------------------------------------------- drawing
GREEN, GREY, AMBER, WHITE, DARK = (60, 220, 90), (160, 160, 160), (0, 190, 255), (255, 255, 255), (25, 25, 25)
FONT = cv2.FONT_HERSHEY_SIMPLEX
PANEL_W = 196
DISPLAY_W = 1280      # window width; the pipeline still works on the full camera frame


def put(img, text, org, scale=0.55, color=WHITE, thick=1, bg=True):
    (w, h), base = cv2.getTextSize(text, FONT, scale, thick)
    x, y = org
    if bg:
        cv2.rectangle(img, (x - 3, y - h - 4), (x + w + 3, y + base + 2), DARK, -1)
    cv2.putText(img, text, (x, y), FONT, scale, color, thick, cv2.LINE_AA)


def draw_speed_panel(out, mon):
    """Right-hand panel: current speed, active limit, warning state."""
    H, W = out.shape[:2]
    x0, y0, w, h = W - PANEL_W - 8, 38, PANEL_W, 176
    panel = out[y0:y0 + h, x0:x0 + w]
    panel[:] = (panel * 0.25 + np.array(DARK) * 0.75).astype(np.uint8)
    state = mon.state
    col = {"none": WHITE, "ok": GREEN, "over": AMBER, "warning": (60, 60, 255)}[state]
    put(out, "SPEED (simulated)", (x0 + 8, y0 + 18), 0.42, GREY, bg=False)
    put(out, f"{mon.speed:3.0f}", (x0 + 8, y0 + 70), 1.7, col, 3, bg=False)
    put(out, "km/h", (x0 + 100, y0 + 70), 0.5, GREY, bg=False)
    # limit sign
    cx, cy = x0 + 40, y0 + 120
    cv2.circle(out, (cx, cy), 28, (40, 40, 210), -1, cv2.LINE_AA)
    cv2.circle(out, (cx, cy), 21, (250, 250, 250), -1, cv2.LINE_AA)
    txt = "--" if mon.limit is None else str(mon.limit)
    (tw, th), _ = cv2.getTextSize(txt, FONT, 0.6, 2)
    cv2.putText(out, txt, (cx - tw // 2, cy + th // 2), FONT, 0.6, DARK, 2, cv2.LINE_AA)
    label = {"none": "no limit yet", "ok": "within limit", "over": "over limit...",
             "warning": "SLOW DOWN"}[state]
    put(out, label, (x0 + 76, y0 + 116), 0.42, col, bg=False)
    if mon.limit is not None:
        left = max(0.0, mon.expire - mon.km_since_limit)
        put(out, f"ends in {left:.2f} km", (x0 + 76, y0 + 136), 0.38, GREY, bg=False)
    put(out, f"trip {mon.trip_km:.2f} km  warnings {mon.warnings}", (x0 + 8, y0 + 168), 0.38, GREY, bg=False)


def draw(bgr, pipe, pub, fps, show_mask):
    out = bgr.copy()
    if show_mask:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        m = colour_mask(cv2.cvtColor(cv2.GaussianBlur(rgb, (5, 5), 0), cv2.COLOR_RGB2HSV))
        out = cv2.cvtColor(m, cv2.COLOR_GRAY2BGR)
    # The pipeline sees the full camera frame (e.g. 1920x1080); the window shows it
    # at most DISPLAY_W wide so it fits a laptop screen and the text stays readable.
    s = 1.0
    if out.shape[1] > DISPLAY_W:
        s = DISPLAY_W / out.shape[1]
        out = cv2.resize(out, (DISPLAY_W, int(round(out.shape[0] * s))), interpolation=cv2.INTER_AREA)

    for (x, y, w, h), label, conf, counted in pipe.last_detections:
        col = GREEN if counted else GREY
        size = max(w, h)                                     # size in camera pixels
        x, y, w, h = (int(round(v * s)) for v in (x, y, w, h))
        cv2.rectangle(out, (x, y), (x + w, y + h), col, 2)
        txt = (f"{label} {conf:.0%}  {size}px" if label != "background" else "not a sign")
        put(out, txt, (x, max(18, y - 6)), 0.5, col)

    H, W = out.shape[:2]
    # top bar: status
    cv2.rectangle(out, (0, 0), (W, 28), DARK, -1)
    status = "CAMERA BLOCKED" if pipe.health.blinded else "watching"
    put(out, f"Driver Companion | {status}", (8, 19), 0.55,
        AMBER if pipe.health.blinded else WHITE, bg=False)
    ms = pipe.stage_ms
    perf = f"{bgr.shape[1]}x{bgr.shape[0]} | {fps:4.1f} fps | find {ms['propose']:.1f} ms | CNN {ms['classify']:.1f} ms"
    (pw, _), _ = cv2.getTextSize(perf, FONT, 0.45, 1)
    put(out, perf, (W - pw - 8, 19), 0.45, GREY, bg=False)          # right-aligned

    # bottom: 3-of-5 vote strip for the leading class
    window = list(pipe.voter.window)
    counts = {}
    for votes in window:
        for l in votes:
            counts[l] = counts.get(l, 0) + 1
    lead = max(counts, key=counts.get) if counts else None
    cv2.rectangle(out, (0, H - 34), (W, H), DARK, -1)
    put(out, "vote (3 of 5):", (8, H - 12), 0.5, GREY, bg=False)
    for i in range(pipe.voter.window.maxlen):
        filled = i < len(window) and lead in window[i]
        x0 = 130 + i * 26
        cv2.rectangle(out, (x0, H - 26), (x0 + 20, H - 8), GREEN if filled else GREY,
                      -1 if filled else 1)
    if lead:
        put(out, f"{lead}  {counts[lead]}/5", (270, H - 12), 0.5, GREEN, bg=False)

    # alert banner: in the space left of the speed panel, just under the top bar
    if time.monotonic() < pub.banner_until:
        area = W - PANEL_W - 24
        scale = 1.0
        (tw, th), _ = cv2.getTextSize(pub.banner, FONT, scale, 3)
        while tw > area - 32 and scale > 0.5:
            scale -= 0.05
            (tw, th), _ = cv2.getTextSize(pub.banner, FONT, scale, 3)
        x0 = 8 + (area - tw) // 2
        y_top = H - 34 - th - 34                      # above the vote bar, clear of boxes up top
        cv2.rectangle(out, (x0 - 14, y_top), (x0 + tw + 14, y_top + th + 20), pub.banner_colour, -1)
        cv2.putText(out, pub.banner, (x0, y_top + th + 10), FONT, scale, WHITE, 3, cv2.LINE_AA)

    draw_speed_panel(out, pub.monitor)
    if pub.app_url:
        (uw, _), _ = cv2.getTextSize(f"Trip app: {pub.app_url}", FONT, 0.45, 1)
        put(out, f"Trip app: {pub.app_url}", (W - uw - 10, H - 12), 0.45, GREY, bg=False)

    # recent alerts log
    for i, line in enumerate(pub.history):
        put(out, line, (8, H - 100 - i * 20), 0.42, WHITE)
    return out


# ----------------------------------------------------------------- main
def open_camera(index, width, height):
    backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY   # faster open on Windows
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap = cv2.VideoCapture(index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap


def camera_size(cap, asked):
    """The resolution the camera really delivers (it may refuse what we asked
    for and silently give less). Read from an actual frame, not the driver's claim."""
    for _ in range(5):                        # the first frames can be empty while it starts
        ok, frame = cap.read()
        if ok and frame is not None:
            return frame.shape[1], frame.shape[0]
    return asked


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", default="0", help="camera index or a video file")
    ap.add_argument("--model", default=str(ROOT / "models" / "sign_cnn.npz"))
    ap.add_argument("--no-speech", action="store_true")
    ap.add_argument("--min-conf", type=float, default=0.80)
    ap.add_argument("--exit", action="store_true",
                    help="close the trip app too when the camera closes")
    ap.add_argument("--no-snapshots", action="store_true",
                    help="don't save a picture of each announced sign to demo/detections")
    ap.add_argument("--res", default="1920x1080",
                    help="camera resolution to ask for (default 1920x1080; if the webcam can't, "
                         "it uses the best it gives). Higher = signs recognised from further away")
    ap.add_argument("--save", default=None, help="also record the annotated video to this .avi")
    ap.add_argument("--headless", action="store_true", help="no window (testing / recording only)")
    ap.add_argument("--speed", type=float, default=40, help="starting simulated speed, km/h")
    ap.add_argument("--no-app", action="store_true", help="do not record trips or start the trip app")
    ap.add_argument("--port", type=int, default=5000, help="port for the trip app")
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "trips.db"),
                    help="trip database file")
    ap.add_argument("--repeat", type=float, default=30,
                    help="seconds between repeated overspeed warnings (REQ-05: 30)")
    a = ap.parse_args()

    cfg = Config()
    cfg.model_path = Path(a.model)
    cfg.min_confidence = a.min_conf
    cfg.frame_width, cfg.frame_height = (int(v) for v in a.res.lower().split("x"))
    cfg.roi = (0.0, 0.0, 1.0, 1.0)        # hand-held signs can be anywhere in the frame
    monitor = SpeedMonitor(repeat_s=a.repeat)
    trip = None if a.no_app else TripLog(a.db)
    pub = DemoPublisher(Speaker(enabled=not a.no_speech), monitor, trip)
    if trip:
        trip.start_trip()
        trip_started = trip.clock()
        if trip.recovered:
            print(f"Closed {len(trip.recovered)} unfinished trip(s) from last time.")
        from app_server import serve_in_background
        try:
            pub.app_url = serve_in_background(a.db, a.port)
            print(f"Trip app on this laptop:  http://127.0.0.1:{a.port}")
            print(f"Trip app on your phone:   {pub.app_url}  (phone on the same Wi-Fi)")
        except Exception as e:
            print("Trip app could not start:", e)
    pipe = VisionPipeline(cfg, load_classifier(cfg), pub)

    src = int(a.camera) if a.camera.isdigit() else a.camera
    cap = open_camera(src, cfg.frame_width, cfg.frame_height) if isinstance(src, int) else cv2.VideoCapture(src)
    if not cap.isOpened():
        sys.exit(f"Could not open camera {a.camera}. Try --camera 1, or close other apps using it.")
    if isinstance(src, int):
        asked = (cfg.frame_width, cfg.frame_height)
        got = camera_size(cap, asked)
        if got[0] * got[1] < asked[0] * asked[1] and asked == (1920, 1080):
            # this webcam can't do 1080p: ask for 720p explicitly (some drivers
            # otherwise drop to 640x480 instead of their best mode)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            got2 = camera_size(cap, got)
            got = got2 if got2[0] * got2[1] > got[0] * got[1] else got
        cfg.frame_width, cfg.frame_height = got
        note = "" if got == asked else f"  (asked for {asked[0]}x{asked[1]}; this camera gave less)"
        print(f"Camera resolution: {got[0]}x{got[1]}{note}")

    writer, show_mask, fps, t_prev = None, False, 0.0, time.monotonic()
    print("Running. Q/Esc quit, M colour mask, S screenshot, T end trip.")
    win = "Driver Companion - prototype"
    speed = float(a.speed)
    if not a.headless:
        try:
            cv2.namedWindow(win)
            cv2.createTrackbar("Speed km/h", win, int(speed), 120, lambda v: None)
        except cv2.error:
            sys.exit("This OpenCV build cannot open windows. Fix:\n"
                     "  pip uninstall -y opencv-python-headless opencv-python\n"
                     "  pip install opencv-python")
    is_file = not isinstance(src, int)
    next_t = time.monotonic()
    shown = False
    try:
        while True:
            if is_file:                       # play files at camera speed, not as fast as possible
                next_t += 1.0 / cfg.target_fps
                time.sleep(max(0.0, next_t - time.monotonic()))
            ok, bgr = cap.read()
            if not ok:
                break
            if bgr.shape[1] != cfg.frame_width or bgr.shape[0] != cfg.frame_height:
                bgr = cv2.resize(bgr, (cfg.frame_width, cfg.frame_height))   # video files
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            confirmed = pipe.process(rgb, time.monotonic())
            if confirmed and not a.no_snapshots:
                save_snapshots(bgr, pipe, confirmed)

            if not a.headless:
                speed = float(cv2.getTrackbarPos("Speed km/h", win))
            if trip:
                trip.update(speed, monitor.limit)
            for kind, text in monitor.update(speed, time.monotonic()):
                if trip and kind == "warn":
                    trip.log_warning(speed, monitor.limit)
                if trip and kind == "expired":
                    trip.log_limit_expired(int(text.split()[2]))
                if kind == "warn":
                    pub.alert(text, colour=(0, 0, 230), banner=f"Slow down! Limit {monitor.limit}")
                else:                                   # limit expired: shown, not spoken
                    pub.alert(text, colour=(90, 90, 90), speak=False, banner="Speed limit ended")

            now = time.monotonic()
            fps = 0.9 * fps + 0.1 * (1.0 / max(now - t_prev, 1e-3))
            t_prev = now
            view = draw(bgr, pipe, pub, fps, show_mask)
            if a.save:
                if writer is None:
                    writer = cv2.VideoWriter(a.save, cv2.VideoWriter_fourcc(*"MJPG"), 15,
                                             (view.shape[1], view.shape[0]))
                writer.write(view)
            if a.headless:
                continue
            try:
                cv2.imshow(win, view)
                shown = True
            except cv2.error:
                sys.exit("This OpenCV build cannot open windows. Fix:\n"
                         "  pip uninstall -y opencv-python-headless opencv-python\n"
                         "  pip install opencv-python")
            k = cv2.waitKey(1) & 0xFF
            if k in (ord("q"), 27):
                break
            if cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:
                break                         # window closed with X: save the trip like Q does
            if k in (ord("+"), ord("=")):
                cv2.setTrackbarPos("Speed km/h", win, min(120, int(speed) + 5))
            if k in (ord("-"), ord("_")):
                cv2.setTrackbarPos("Speed km/h", win, max(0, int(speed) - 5))
            if k == ord("t") and trip and trip.clock() - trip_started < 5:
                pub.alert("Trip too short to save", colour=(90, 90, 90), speak=False)
            elif k == ord("t") and trip:                # end trip, speak summary, start a new one
                finish_trip(trip, pub)
                trip_started = trip.clock()
                monitor.trip_km, monitor.warnings = 0.0, 0
                trip.start_trip()
            if k == ord("m"):
                show_mask = not show_mask
            if k == ord("s"):
                name = f"screenshot_{time.strftime('%H%M%S')}.png"
                cv2.imwrite(name, view)
                print("saved", name)
    except KeyboardInterrupt:          # Ctrl+C in the terminal: still save the trip
        print("\nStopping...")
    cap.release()
    if trip:
        finish_trip(trip, pub, wait=True)
    if writer:
        writer.release()
    if not a.headless:
        cv2.destroyAllWindows()
    # Keep the trip app open after the camera closes, so the trip can be shown.
    if pub.app_url and not a.headless and not a.exit and sys.stdin.isatty():
        print()
        print("Camera closed. The trip app is still open:")
        print(f"  on this laptop:  http://127.0.0.1:{a.port}")
        print(f"  on your phone:   {pub.app_url}")
        try:
            input("Press Enter here to close the app. ")
        except (KeyboardInterrupt, EOFError):
            pass


if __name__ == "__main__":
    main()
