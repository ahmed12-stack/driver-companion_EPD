"""Stage 3 - Where might a sign be? (region proposals)

Running the CNN over the whole 640x480 frame is too slow on a Pi 3B+.
Indian signs are almost all red-bordered (prohibitory / warning) or blue
(mandatory), so we find red and blue blobs of a sign-like shape and only
send those small crops to the CNN.
"""
import cv2
import numpy as np

# HSV ranges (OpenCV: H 0-179). Red wraps around 0, so it has two bands.
RED_LO1, RED_HI1 = (0, 90, 60), (10, 255, 255)
RED_LO2, RED_HI2 = (165, 90, 60), (179, 255, 255)
BLUE_LO, BLUE_HI = (100, 120, 50), (130, 255, 255)

_KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
_SMALL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))


def colour_mask(hsv):
    red = cv2.inRange(hsv, RED_LO1, RED_HI1) | cv2.inRange(hsv, RED_LO2, RED_HI2)
    blue = cv2.inRange(hsv, BLUE_LO, BLUE_HI)
    mask = red | blue
    # remove isolated coloured specks (sensor noise), then close small gaps
    # in the red ring so a circle becomes one blob
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _SMALL)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _KERNEL)


def propose(rgb, cfg):
    """Return up to cfg.max_candidates boxes (x, y, w, h) in full-frame pixels,
    biggest (= nearest) first."""
    H, W = rgb.shape[:2]
    x0, y0 = int(cfg.roi[0] * W), int(cfg.roi[1] * H)
    x1, y1 = int(cfg.roi[2] * W), int(cfg.roi[3] * H)
    roi = rgb[y0:y1, x0:x1]

    roi = cv2.GaussianBlur(roi, (5, 5), 0)     # smooth per-pixel colour noise
    hsv = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)
    mask = colour_mask(hsv)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    max_side = cfg.max_box_frac * min(W, H)
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w < cfg.min_box_px or h < cfg.min_box_px:
            continue
        if w > max_side or h > max_side:
            continue
        aspect = w / h
        if not 0.6 <= aspect <= 1.6:          # signs are roughly square
            continue
        fill = cv2.contourArea(c) / float(w * h)
        if fill < 0.15:                       # thin streak, not a sign
            continue
        # pad 10 % so the CNN sees the whole border
        p = int(0.1 * max(w, h))
        bx, by = max(0, x + x0 - p), max(0, y + y0 - p)
        bw, bh = min(W - bx, w + 2 * p), min(H - by, h + 2 * p)
        boxes.append((bx, by, bw, bh))

    boxes.sort(key=lambda b: b[2] * b[3], reverse=True)
    return boxes[: cfg.max_candidates]
