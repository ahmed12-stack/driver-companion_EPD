"""Recognition-range benchmark: how small (= how far away) can a sign be and
still be found and named correctly?

For each sign size (in pixels across, at the camera's full resolution) it
makes N synthetic camera frames (cluttered scene, one sign, camera blur,
noise and JPEG), then runs the REAL pipeline steps on them:
  found   = propose() put a box on the sign
  correct = the CNN named it right (confidence, margin and shape checks all pass)
and prints a table. Pixel size converts to distance with the pinhole rule
  distance = focal_px * sign_diameter_m / size_px.

    python tools/range_test.py --model models/sign_cnn.npz --width 1280
"""
import argparse
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline.classifier import load_classifier          # noqa: E402
from pipeline.config import LABELS, Config               # noqa: E402
from pipeline.proposals import propose                   # noqa: E402
from pipeline.verify import shape_agrees                 # noqa: E402
import make_dataset as md                                # noqa: E402


def frame_with_sign(rng, W, H, size, sign_rgba):
    bg = cv2.resize(md.scene(rng).astype(np.uint8), (W, H), interpolation=cv2.INTER_LINEAR)
    img = bg.astype(np.float32)
    s = size
    src = np.float32([[0, 0], [255, 0], [255, 255], [0, 255]])
    rot = np.deg2rad(rng.uniform(-8, 8))
    base = np.float32([[0, 0], [s, 0], [s, s], [0, s]]) - s / 2
    R = np.array([[np.cos(rot), -np.sin(rot)], [np.sin(rot), np.cos(rot)]])
    dst = base @ R.T + rng.uniform(-0.06 * s, 0.06 * s, (4, 2))
    cx, cy = rng.uniform(0.15 * W, 0.85 * W), rng.uniform(0.15 * H, 0.6 * H)
    dst = (dst + [cx, cy]).astype(np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(sign_rgba, M, (W, H), flags=cv2.INTER_AREA)
    a = warped[:, :, 3:4].astype(np.float32) / 255.0
    img = img * (1 - a) + warped[:, :, :3].astype(np.float32) * rng.uniform(0.7, 1.1) * a
    img = np.clip(img + rng.normal(0, rng.uniform(2, 6), img.shape), 0, 255).astype(np.uint8)
    if rng.random() < 0.5:
        img = cv2.GaussianBlur(img, (3, 3), 0)
    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(60, 90))])
    img = cv2.imdecode(enc, cv2.IMREAD_UNCHANGED)
    ys, xs = np.nonzero(warped[:, :, 3] > 127)
    return img, (xs.min(), ys.min(), xs.max() - xs.min() + 1, ys.max() - ys.min() + 1)


def run(cfg, clf, W, H, process_w, sizes, n, seed=0):
    rng = np.random.default_rng(seed)
    signs = {}
    for lab in LABELS:
        im = cv2.imread(str(Path(__file__).resolve().parents[1] / "demo" / "signs" / f"{lab}.png"),
                        cv2.IMREAD_UNCHANGED)
        signs[lab] = cv2.cvtColor(im, cv2.COLOR_BGRA2RGBA)
    rows = []
    scale = process_w / W
    for s in sizes:
        found = correct = 0
        for i in range(n):
            lab = LABELS[i % len(LABELS)]
            img, box = frame_with_sign(rng, W, H, s, signs[lab])
            if scale != 1:
                img = cv2.resize(img, (process_w, int(H * scale)), interpolation=cv2.INTER_AREA)
                box = tuple(int(v * scale) for v in box)
            props = propose(img, cfg)
            hit = [b for b in props if md.iou(b, box) > 0.3]
            if hit:
                found += 1
                x, y, w, h = hit[0]
                (pl, pc, *rest), = clf.classify([img[y:y + h, x:x + w]])  # noqa
                mg = rest[0] if rest else pc
                crop = img[y:y + h, x:x + w]
                if pl == lab and pc >= cfg.min_confidence and mg >= cfg.min_margin \
                        and (not cfg.verify_shape or shape_agrees(crop, pl)):
                    correct += 1
        rows.append((s, found / n, correct / n))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/sign_cnn.npz")
    ap.add_argument("--width", type=int, default=1280, help="camera width; height = 9/16")
    ap.add_argument("--process-width", type=int, default=None,
                    help="width the pipeline works at (default: same as camera)")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--hfov", type=float, default=70.0, help="camera horizontal field of view, deg")
    ap.add_argument("--no-far", action="store_true", help="turn the far-sign pass off")
    ap.add_argument("--sign-m", type=float, default=0.75, help="real sign diameter, metres")
    a = ap.parse_args()
    W, H = a.width, a.width * 9 // 16
    pw = a.process_width or W
    cfg = Config()
    cfg.model_path = Path(a.model)
    cfg.roi = (0.0, 0.0, 1.0, 1.0)
    if a.no_far:
        cfg.far_candidates = 0
    clf = load_classifier(cfg)
    f = (W / 2) / math.tan(math.radians(a.hfov / 2))
    sizes = [8, 10, 12, 14, 16, 20, 24, 32, 48, 64]
    print(f"camera {W}x{H}, pipeline works at {pw} px wide, focal {f:.0f} px, "
          f"sign {a.sign_m*100:.0f} cm, {a.n} frames per size")
    print(f"{'size px':>8} {'distance m':>11} {'found':>7} {'correct':>8}")
    for s, fd, cr in run(cfg, clf, W, H, pw, sizes, a.n):
        print(f"{s:>8} {f * a.sign_m / s:>11.0f} {fd:>7.0%} {cr:>8.0%}")


if __name__ == "__main__":
    main()
