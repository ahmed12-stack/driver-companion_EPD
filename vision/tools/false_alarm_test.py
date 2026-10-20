"""False-alarm benchmark: a 'busy room' with NO road signs in it.

Random static clutter (red and blue objects with white logos, text, rings and
holes, faces, posters), slowly drifting like a hand-held camera, camera noise.
Runs the full pipeline (proposals, CNN, 3-of-5 vote) and counts how many signs
it announces. The right answer is zero. REQ-03 allows at most 1 false alert
per 30 minutes of driving.

    python tools/false_alarm_test.py --minutes 2
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline.classifier import load_classifier          # noqa: E402
from pipeline.config import Config                        # noqa: E402
from pipeline.main import VisionPipeline                  # noqa: E402
from pipeline.publisher import ListPublisher              # noqa: E402

W, H = 1280, 720
RED = [(200, 30, 35), (170, 20, 40), (225, 60, 50), (150, 25, 25)]
BLUE = [(30, 70, 190), (20, 50, 150)]


def room(rng):
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = rng.integers(90, 200, 3)
    cv2.rectangle(img, (0, int(H * 0.7)), (W, H), tuple(int(v) for v in rng.integers(40, 120, 3)), -1)
    for _ in range(rng.integers(6, 14)):
        col = tuple(int(v) for v in (RED + BLUE)[rng.integers(0, 6)])
        x, y = int(rng.integers(0, W)), int(rng.integers(0, H))
        s = int(rng.integers(30, 220))
        kind = rng.integers(0, 6)
        if kind == 0:      # red T-shirt / box with white logo text
            cv2.rectangle(img, (x, y), (x + s, y + int(s * 1.2)), col, -1)
            cv2.putText(img, rng.choice(["NIKE", "OK", "Co", "A", "8"]), (x + s // 6, y + s // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, s / 90, (245, 245, 245), max(2, s // 25))
        elif kind == 1:    # red cup / mug: ring with white inside
            cv2.ellipse(img, (x, y), (s // 2, s // 3), 0, 0, 360, col, -1)
            cv2.ellipse(img, (x, y), (s // 3, s // 5), 0, 0, 360, (240, 240, 235), -1)
        elif kind == 2:    # poster: white sheet with red blob shapes
            cv2.rectangle(img, (x, y), (x + s, y + s), (240, 240, 240), -1)
            pts = (rng.integers(0, s, (5, 2)) + [x, y]).astype(np.int32)
            cv2.fillPoly(img, [pts], col)
        elif kind == 3:    # face / skin
            cv2.ellipse(img, (x, y), (s // 3, s // 2), 0, 0, 360,
                        tuple(int(v) for v in rng.integers([170, 120, 90], [230, 170, 140])), -1)
        elif kind == 4:    # red frame / window with a white interior (square, not round)
            cv2.rectangle(img, (x, y), (x + s, y + s), col, -1)
            cv2.rectangle(img, (x + s // 6, y + s // 6), (x + 5 * s // 6, y + 5 * s // 6), (235, 235, 235), -1)
        else:              # red stripes / cables
            cv2.line(img, (x, y), (x + int(rng.integers(-300, 300)), y + int(rng.integers(-200, 200))),
                     col, int(rng.integers(4, 20)))
    return img


FADED = [(190, 110, 110), (170, 90, 80), (200, 130, 140), (150, 80, 70), (185, 105, 90),
         (120, 70, 60), (210, 150, 160), (110, 120, 170), (140, 150, 190)]


def far_clutter(img, rng):
    """Small, faded reddish / bluish things: what the far-sign pass's gentler colour
    filter could mistake for a distant sign (skin, wood, pink and brown objects,
    faded stickers, small rings and dots)."""
    for _ in range(rng.integers(25, 45)):
        col = tuple(int(v) + int(rng.integers(-15, 15)) for v in FADED[rng.integers(0, len(FADED))])
        x, y = int(rng.integers(0, W)), int(rng.integers(0, H))
        s = int(rng.integers(8, 34))
        kind = rng.integers(0, 5)
        if kind == 0:      # dot / button / bottle cap
            cv2.circle(img, (x, y), s // 2, col, -1)
        elif kind == 1:    # small ring with a light middle (sticker, washer, cup seen from above)
            cv2.circle(img, (x, y), s // 2, col, -1)
            cv2.circle(img, (x, y), max(1, int(s * 0.35)), (225, 225, 220), -1)
        elif kind == 2:    # small triangle (shirt print, logo)
            pts = np.int32([[x, y - s // 2], [x - s // 2, y + s // 2], [x + s // 2, y + s // 2]])
            cv2.fillPoly(img, [pts], col)
            if rng.random() < 0.5:
                cv2.fillPoly(img, [((pts - [x, y]) * 0.5 + [x, y]).astype(np.int32)], (230, 230, 225))
        elif kind == 3:    # small square sticker / box with white text
            cv2.rectangle(img, (x, y), (x + s, y + s), col, -1)
            cv2.putText(img, "5", (x + s // 4, y + 3 * s // 4), cv2.FONT_HERSHEY_SIMPLEX,
                        s / 40, (240, 240, 240), 1)
        else:              # small face / hand far away
            cv2.ellipse(img, (x, y), (s // 3, s // 2), 0, 0, 360,
                        tuple(int(v) for v in rng.integers([170, 110, 80], [235, 170, 140])), -1)
    return img


def webcam(f, rng):
    """Laptop-webcam look: slight blur and JPEG/MJPEG colour compression."""
    if rng.random() < 0.6:
        f = cv2.GaussianBlur(f, (3, 3), 0)
    ok, enc = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(60, 90))])
    return cv2.imdecode(enc, cv2.IMREAD_UNCHANGED)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/sign_cnn.npz")
    ap.add_argument("--minutes", type=float, default=2.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--far-clutter", action="store_true",
                    help="add many small faded reddish objects + webcam compression (harder)")
    ap.add_argument("--no-far", action="store_true", help="turn the far-sign pass off")
    ap.add_argument("--dump", default=None, help="folder to save a picture of each false alarm")
    a = ap.parse_args()
    cfg = Config()
    if a.no_far:
        cfg.far_candidates = 0
    cfg.model_path = Path(a.model)
    cfg.roi = (0.0, 0.0, 1.0, 1.0)
    rng = np.random.default_rng(a.seed)
    pub = ListPublisher()
    pipe = VisionPipeline(cfg, load_classifier(cfg), pub)
    frames = int(a.minutes * 60 * cfg.target_fps)
    scene, t = None, 0.0
    for i in range(frames):
        if i % 150 == 0:                       # a new part of the room every 10 s
            scene = room(rng)
            if a.far_clutter:
                scene = far_clutter(scene, rng)
        dx, dy = int(8 * np.sin(i / 20)), int(5 * np.cos(i / 27))
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        f = cv2.warpAffine(scene, M, (W, H), borderMode=cv2.BORDER_REFLECT)
        f = np.clip(f + rng.normal(0, 4, f.shape), 0, 255).astype(np.uint8)
        if a.far_clutter:
            f = webcam(f, rng)
        got = pipe.process(f, t)
        if got and a.dump:
            Path(a.dump).mkdir(parents=True, exist_ok=True)
            for (x, y, w, h), lab, conf, cnt in pipe.last_detections:
                if cnt and lab in got:
                    p = max(4, w // 2)
                    crop = f[max(0, y - p):y + h + p, max(0, x - p):x + w + p]
                    big = cv2.resize(crop, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
                    cv2.imwrite(f"{a.dump}/{i:05d}_{lab}_{w}px_{conf:.2f}.png",
                                cv2.cvtColor(big, cv2.COLOR_RGB2BGR))
        t += 1 / cfg.target_fps
    signs = [e["label"] for e in pub.events if e["type"] == "sign"]
    mode = ("far clutter" if a.far_clutter else "busy room") + (", far pass OFF" if a.no_far else ", far pass ON")
    print(f"{a.minutes:.0f} min ({mode}), no signs: {len(signs)} false announcements {signs}")


if __name__ == "__main__":
    main()
