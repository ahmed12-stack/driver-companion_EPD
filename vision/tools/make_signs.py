"""Draw the 20 IRC:67-style sign designs used for the prototype demo.

These are simplified drawings, NOT official artwork. They are used to
(1) train the prototype CNN and (2) print / show on a phone in front of
the laptop camera.

    python tools/make_signs.py --out demo/signs        # PNGs + printable PDF
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.config import LABELS  # noqa: E402

S = 256                      # canvas size
RED = (205, 25, 30)          # RGB
WHITE = (250, 250, 250)
BLACK = (15, 15, 15)
C = S // 2


def _canvas():
    img = np.full((S, S, 3), 255, np.uint8)
    mask = np.zeros((S, S), np.uint8)
    return img, mask


def _circle_sign(img, mask, ring=26):
    cv2.circle(img, (C, C), 120, RED, -1, cv2.LINE_AA)
    cv2.circle(img, (C, C), 120 - ring, WHITE, -1, cv2.LINE_AA)
    cv2.circle(mask, (C, C), 120, 255, -1)


def _triangle_sign(img, mask, inverted=False):
    if inverted:
        outer = np.array([[8, 22], [248, 22], [128, 238]])
        inner = np.array([[48, 44], [208, 44], [128, 190]])
    else:
        outer = np.array([[128, 14], [250, 232], [6, 232]])
        inner = np.array([[128, 64], [212, 210], [44, 210]])
    cv2.fillPoly(img, [outer], RED, cv2.LINE_AA)
    cv2.fillPoly(img, [inner], WHITE, cv2.LINE_AA)
    cv2.fillPoly(mask, [outer], 255)


def _slash(img):
    cv2.line(img, (C - 70, C - 70), (C + 70, C + 70), RED, 22, cv2.LINE_AA)


def _text(img, txt, scale, thick, color=BLACK, dy=0):
    f = cv2.FONT_HERSHEY_DUPLEX
    (w, h), _ = cv2.getTextSize(txt, f, scale, thick)
    cv2.putText(img, txt, (C - w // 2, C + h // 2 + dy), f, scale, color, thick, cv2.LINE_AA)


def _stick(img, x, y, s=1.0, shovel=False, walking=False):
    """Simple person figure, feet at (x, y)."""
    sc = lambda v: int(v * s)
    cv2.circle(img, (x, y - sc(78)), sc(11), BLACK, -1, cv2.LINE_AA)
    cv2.line(img, (x, y - sc(66)), (x, y - sc(30)), BLACK, sc(9), cv2.LINE_AA)
    lean = sc(14) if walking else sc(9)
    cv2.line(img, (x, y - sc(30)), (x - lean, y), BLACK, sc(8), cv2.LINE_AA)
    cv2.line(img, (x, y - sc(30)), (x + lean, y), BLACK, sc(8), cv2.LINE_AA)
    cv2.line(img, (x, y - sc(58)), (x - sc(16), y - sc(36)), BLACK, sc(7), cv2.LINE_AA)
    cv2.line(img, (x, y - sc(58)), (x + sc(16), y - sc(40)), BLACK, sc(7), cv2.LINE_AA)
    if shovel:
        cv2.line(img, (x + sc(16), y - sc(40)), (x + sc(34), y + sc(2)), BLACK, sc(5), cv2.LINE_AA)
        cv2.ellipse(img, (x + sc(36), y + sc(4)), (sc(9), sc(5)), 0, 0, 360, BLACK, -1)


def draw(label):
    img, mask = _canvas()
    if label.startswith("speed_limit_"):
        _circle_sign(img, mask)
        n = label.split("_")[-1]
        _text(img, n, 3.0 if len(n) == 2 else 2.3, 9)
    elif label == "stop":
        pts = [(C + 122 * np.cos(np.pi / 8 + k * np.pi / 4),
                C + 122 * np.sin(np.pi / 8 + k * np.pi / 4)) for k in range(8)]
        pts = np.array(pts, np.int32)
        cv2.fillPoly(img, [pts], RED, cv2.LINE_AA)
        cv2.fillPoly(mask, [pts], 255)
        _text(img, "STOP", 2.0, 8, WHITE)
    elif label == "give_way":
        _triangle_sign(img, mask, inverted=True)
    elif label == "no_entry":
        cv2.circle(img, (C, C), 120, RED, -1, cv2.LINE_AA)
        cv2.circle(mask, (C, C), 120, 255, -1)
        cv2.rectangle(img, (C - 88, C - 20), (C + 88, C + 20), WHITE, -1)
    elif label == "no_overtaking":
        _circle_sign(img, mask)
        cv2.rectangle(img, (C - 70, C - 20), (C - 14, C + 40), BLACK, -1)   # car 1
        cv2.rectangle(img, (C + 14, C - 20), (C + 70, C + 40), RED, -1)     # car 2
        cv2.rectangle(img, (C - 62, C - 40), (C - 22, C - 20), BLACK, -1)
        cv2.rectangle(img, (C + 22, C - 40), (C + 62, C - 20), RED, -1)
    elif label == "no_u_turn":
        _circle_sign(img, mask)
        cv2.ellipse(img, (C, C - 10), (36, 36), 0, 180, 360, BLACK, 16, cv2.LINE_AA)
        cv2.line(img, (C - 36, C - 10), (C - 36, C + 60), BLACK, 16, cv2.LINE_AA)
        cv2.line(img, (C + 36, C - 10), (C + 36, C + 30), BLACK, 16, cv2.LINE_AA)
        cv2.fillPoly(img, [np.array([[C + 12, C + 30], [C + 60, C + 30], [C + 36, C + 64]])], BLACK)
        _slash(img)
    elif label == "no_horn":
        _circle_sign(img, mask)
        cv2.fillPoly(img, [np.array([[C - 60, C - 18], [C - 30, C - 18], [C + 40, C - 52],
                                     [C + 40, C + 52], [C - 30, C + 18], [C - 60, C + 18]])], BLACK)
        _slash(img)
    elif label == "no_parking":
        _circle_sign(img, mask)
        _text(img, "P", 4.0, 12)
        _slash(img)
    elif label == "school_ahead":
        _triangle_sign(img, mask)
        _stick(img, C - 24, 196, 0.85)
        _stick(img, C + 26, 196, 0.65)
    elif label == "pedestrian_crossing":
        _triangle_sign(img, mask)
        for k in range(4):
            cv2.rectangle(img, (C - 60 + k * 32, 186), (C - 44 + k * 32, 200), BLACK, -1)
        _stick(img, C, 178, 0.85, walking=True)
    elif label == "speed_breaker":
        _triangle_sign(img, mask)
        cv2.rectangle(img, (C - 70, 180), (C + 70, 196), BLACK, -1)
        cv2.ellipse(img, (C, 182), (34, 30), 0, 180, 360, BLACK, -1, cv2.LINE_AA)
    elif label in ("left_curve", "right_curve"):
        _triangle_sign(img, mask)
        d = -1 if label == "left_curve" else 1
        cv2.line(img, (C, 200), (C, 150), BLACK, 14, cv2.LINE_AA)
        cv2.ellipse(img, (C + d * 34, 150), (34, 34), 0, 180 if d > 0 else 270,
                    270 if d > 0 else 360, BLACK, 14, cv2.LINE_AA)
        tip = (C + d * 34, 116)
        cv2.fillPoly(img, [np.array([[tip[0] + d * 30, tip[1]], [tip[0] - d * 4, tip[1] - 20],
                                     [tip[0] - d * 4, tip[1] + 20]])], BLACK, cv2.LINE_AA)
    elif label == "men_at_work":
        _triangle_sign(img, mask)
        _stick(img, C - 10, 196, 0.9, shovel=True)
        cv2.ellipse(img, (C + 40, 202), (26, 10), 0, 180, 360, BLACK, -1)
    else:
        raise ValueError(label)
    return img, mask


def printable_pdf(images, labels, path):
    """Printable A4 sheet: 6 signs per page (~8 cm each), names underneath."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.pdfgen import canvas
    except ImportError:
        return _printable_png(images, labels, path)
    tmp = Path(path).parent
    c = canvas.Canvas(str(path), pagesize=A4)
    W, H = A4
    for i, lab in enumerate(labels):
        k = i % 6
        if k == 0 and i:
            c.showPage()
        r, col = divmod(k, 2)
        x, y = 2.2 * cm + col * 9.3 * cm, H - (2.0 * cm + (r + 1) * 9.0 * cm)
        c.drawImage(str(tmp / f"{lab}.png"), x, y + 0.8 * cm, 8 * cm, 8 * cm, mask="auto")
        c.setFont("Helvetica", 11)
        c.drawString(x, y + 0.2 * cm, lab)
    c.save()


def _printable_png(images, labels, path):
    """A4 pages, 6 signs per page (each ~8 cm), with names underneath."""
    W, H = 2480, 3508                          # A4 at 300 dpi
    pages, per = [], 6
    for p in range(0, len(images), per):
        page = np.full((H, W, 3), 255, np.uint8)
        for k, (im, lab) in enumerate(zip(images[p:p + per], labels[p:p + per])):
            r, c = divmod(k, 2)
            big = cv2.resize(im, (940, 940), interpolation=cv2.INTER_CUBIC)
            x, y = 200 + c * 1140, 180 + r * 1120
            page[y:y + 940, x:x + 940] = big
            cv2.putText(page, lab, (x, y + 1010), cv2.FONT_HERSHEY_SIMPLEX, 2.0, BLACK, 4, cv2.LINE_AA)
        pages.append(page)
    try:
        from PIL import Image
        ims = [Image.fromarray(pg) for pg in pages]
        ims[0].save(path, save_all=True, append_images=ims[1:], resolution=300)
    except Exception:                          # no PIL / PIL built without JPEG
        for i, pg in enumerate(pages):
            cv2.imwrite(str(path).replace(".pdf", f"_{i + 1}.png"), cv2.cvtColor(pg, cv2.COLOR_RGB2BGR))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="demo/signs")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    imgs = []
    for lab in LABELS:
        img, mask = draw(lab)
        imgs.append(img)
        rgba = np.dstack([img, mask])
        cv2.imwrite(str(out / f"{lab}.png"), cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
    printable_pdf(imgs, LABELS, out / "print_signs_A4.pdf")
    print(f"wrote {len(LABELS)} signs + print_signs_A4.pdf to {out}")


if __name__ == "__main__":
    main()
