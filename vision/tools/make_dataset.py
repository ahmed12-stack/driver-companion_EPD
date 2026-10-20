"""Build a training set for the prototype from the drawn signs.

Each sample is made like this:
  1. a random cluttered scene (gradients, noise, coloured shapes, red and
     skin-coloured distractors),
  2. a sign pasted in at a random size, tilt, perspective and lighting,
  3. camera effects: blur, motion blur, noise, glare, JPEG compression,
  4. the REAL pipeline step `propose()` is run on the frame, and its crops
     are saved: the crop on the sign gets the sign's label, any other crop
     is saved as "background".

So the CNN is trained on exactly the kind of crops it will see live.

    python tools/make_dataset.py --signs demo/signs --out data --per-class 1500
"""
import argparse
import os
import sys
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.config import CLASSES, LABELS, Config  # noqa: E402
from pipeline.proposals import propose               # noqa: E402

W, H = 320, 240                          # half-res scenes: 4x faster, crops end up 32x32 anyway
CFG = Config()
CFG.roi = (0.0, 0.0, 1.0, 1.0)          # whole frame, like the webcam demo
SIGNS = {}


def load_signs(folder):
    for lab in LABELS:
        im = cv2.imread(str(Path(folder) / f"{lab}.png"), cv2.IMREAD_UNCHANGED)
        SIGNS[lab] = cv2.cvtColor(im, cv2.COLOR_BGRA2RGBA)


def scene(rng):
    """Random cluttered background with some red / skin distractors."""
    c1, c2 = rng.integers(20, 235, 3), rng.integers(20, 235, 3)
    t = np.linspace(0, 1, H)[:, None, None]
    img = (c1 * (1 - t) + c2 * t).repeat(W, 1).astype(np.float32)
    for _ in range(rng.integers(3, 12)):                      # random shapes
        col = tuple(int(v) for v in rng.integers(0, 255, 3))
        x, y = int(rng.integers(0, W)), int(rng.integers(0, H))
        if rng.random() < 0.5:
            cv2.rectangle(img, (x, y), (x + int(rng.integers(10, 200)),
                                        y + int(rng.integers(10, 200))), col, -1)
        else:
            cv2.ellipse(img, (x, y), (int(rng.integers(5, 90)), int(rng.integers(5, 90))),
                        float(rng.uniform(0, 180)), 0, 360, col, -1)
    for _ in range(rng.integers(0, 4)):                       # red distractors
        red = (int(rng.integers(150, 240)), int(rng.integers(0, 60)), int(rng.integers(0, 60)))
        x, y = int(rng.integers(0, W)), int(rng.integers(0, H))
        k = rng.integers(0, 4)
        if k == 0:
            cv2.rectangle(img, (x, y), (x + int(rng.integers(20, 120)), y + int(rng.integers(20, 120))), red, -1)
        elif k == 1:
            cv2.circle(img, (x, y), int(rng.integers(12, 60)), red, int(rng.choice([-1, 6, 12])))
        elif k == 2:
            cv2.putText(img, "SALE", (x, y), cv2.FONT_HERSHEY_SIMPLEX, float(rng.uniform(1, 3)), red, 5)
        else:
            cv2.line(img, (x, y), (x + int(rng.integers(-150, 150)), y + int(rng.integers(-150, 150))), red, int(rng.integers(5, 25)))
    if rng.random() < 0.4:                                    # face-like blob
        skin = (int(rng.integers(170, 235)), int(rng.integers(110, 170)), int(rng.integers(80, 140)))
        cv2.ellipse(img, (int(rng.integers(0, W)), int(rng.integers(0, H))),
                    (int(rng.integers(40, 110)), int(rng.integers(55, 140))), 0, 0, 360, skin, -1)
    img += rng.normal(0, rng.uniform(2, 12), img.shape)
    return np.clip(img, 0, 255)


def paste_sign(img, sign_rgba, rng):
    """Warp the sign into the scene; returns the sign's bounding box."""
    # log-uniform 10..115 px: plenty of small, blurry, far-away signs, so the
    # CNN learns what a sign looks like at long range (up-scaled to 32x32)
    size = int(np.exp(rng.uniform(np.log(10), np.log(115))))
    src = np.float32([[0, 0], [255, 0], [255, 255], [0, 255]])
    j = size * 0.12                                           # perspective jitter
    rot = np.deg2rad(rng.uniform(-14, 14))
    base = np.float32([[0, 0], [size, 0], [size, size], [0, size]]) - size / 2
    R = np.array([[np.cos(rot), -np.sin(rot)], [np.sin(rot), np.cos(rot)]])
    dst = base @ R.T + rng.uniform(-j, j, (4, 2))
    cx = rng.uniform(size * 0.7, W - size * 0.7)
    cy = rng.uniform(size * 0.7, H - size * 0.7)
    dst = (dst + [cx, cy]).astype(np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(sign_rgba, M, (W, H), flags=cv2.INTER_LINEAR)
    a = warped[:, :, 3:4].astype(np.float32) / 255.0
    rgb = warped[:, :, :3].astype(np.float32)
    # sign lighting differs from the scene (shade, phone screen brightness)
    rgb = rgb * rng.uniform(0.55, 1.15) + rng.uniform(-25, 25)
    img[:] = img * (1 - a) + rgb * a
    ys, xs = np.nonzero(warped[:, :, 3] > 127)
    if len(xs) == 0:
        return None
    return xs.min(), ys.min(), xs.max() - xs.min(), ys.max() - ys.min()


def camera(img, rng):
    img = img * rng.uniform(0.6, 1.3) + rng.uniform(-30, 30)          # exposure
    img = img * rng.uniform(0.85, 1.15, 3)                            # white balance
    if rng.random() < 0.3:                                            # glare
        gx, gy = rng.uniform(0, W), rng.uniform(0, H)
        yy, xx = np.mgrid[0:H, 0:W]
        g = np.exp(-((xx - gx) ** 2 + (yy - gy) ** 2) / (2 * rng.uniform(20, 80) ** 2))
        img = img + g[..., None] * rng.uniform(40, 140)
    img = np.clip(img, 0, 255).astype(np.uint8)
    if rng.random() < 0.5:
        k = int(rng.choice([3, 5]))
        img = cv2.GaussianBlur(img, (k, k), 0)
    if rng.random() < 0.25:                                           # motion blur
        k = int(rng.integers(3, 9))
        ker = np.zeros((k, k), np.float32)
        ker[k // 2, :] = 1.0 / k
        img = cv2.filter2D(img, -1, ker)
    noise = rng.normal(0, rng.uniform(1, 8), img.shape)
    img = np.clip(img + noise, 0, 255).astype(np.uint8)
    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(35, 95))])
    return cv2.imdecode(enc, cv2.IMREAD_UNCHANGED)


def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / float(aw * ah + bw * bh - inter + 1e-9)


def crop32(img, box):
    x, y, w, h = box
    return cv2.resize(img[y:y + h, x:x + w], (CFG.input_size, CFG.input_size),
                      interpolation=cv2.INTER_AREA)


def worker(args):
    lab, n, seed, signs_dir = args
    if not SIGNS:
        load_signs(signs_dir)
    rng = np.random.default_rng(seed)
    pos, neg, tries = [], [], 0
    while len(pos) < n and tries < n * 12:
        tries += 1
        img = scene(rng)
        box = paste_sign(img, SIGNS[lab], rng)
        if box is None:
            continue
        frame = camera(img, rng)
        for b in propose(frame, CFG):
            if iou(b, box) > 0.45:
                if len(pos) < n:
                    pos.append(crop32(frame, b))
            elif iou(b, box) < 0.25 and len(neg) < n // 2:
                # includes partial views of the sign (a letter, a quarter of the
                # ring): the CNN must learn those are NOT a sign
                neg.append(crop32(frame, b))
    return lab, pos, neg


def bg_worker(args):
    n, seed = args
    rng = np.random.default_rng(seed)
    out = []
    while len(out) < n:
        frame = camera(scene(rng), rng)
        for b in propose(frame, CFG):
            out.append(crop32(frame, b))
    return out[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signs", default="demo/signs")
    ap.add_argument("--out", default="data")
    ap.add_argument("--per-class", type=int, default=1500)
    ap.add_argument("--val-frac", type=float, default=0.15)
    a = ap.parse_args()

    jobs = [(lab, a.per_class, 1000 + i, a.signs) for i, lab in enumerate(LABELS)]
    with Pool(max(1, os.cpu_count() - 1)) as pool:
        results = pool.map(worker, jobs)
        bg_chunks = pool.map(bg_worker, [(a.per_class // 4, 9000 + i) for i in range(4)])

    data = {lab: pos for lab, pos, _ in results}
    data["background"] = [c for _, _, neg in results for c in neg] + [c for ch in bg_chunks for c in ch]
    rng = np.random.default_rng(0)
    for lab in CLASSES:
        items = data[lab]
        rng.shuffle(items)
        n_val = int(len(items) * a.val_frac)
        for split, part in (("val", items[:n_val]), ("train", items[n_val:])):
            d = Path(a.out) / split / lab
            d.mkdir(parents=True, exist_ok=True)
            for i, im in enumerate(part):
                cv2.imwrite(str(d / f"{i:05d}.png"), cv2.cvtColor(im, cv2.COLOR_RGB2BGR))
        print(f"{lab:<22} {len(items):>5} crops")


if __name__ == "__main__":
    main()
