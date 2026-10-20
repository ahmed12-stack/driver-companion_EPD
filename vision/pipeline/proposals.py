"""Stage 3 - Where might a sign be? (region proposals)

Running the CNN over the whole frame is too slow on a Pi 3B+.
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

# Far-sign pass. A sign 30-50 m away is only 11-20 px across and its red ring is
# 1-2 px thick, so blur and the camera's colour compression wash it out below
# the normal saturation threshold (measured: a quarter of 16 px signs). These
# gentler bands are used ONLY for small blobs, in extra slots of their own
# (the far blue band is only used by verify.py, to measure how blue a crop is).
# The hue band is narrower than the normal one so skin tones stay out.
FAR_RED_LO1, FAR_RED_HI1 = (0, 50, 45), (8, 255, 255)
FAR_RED_LO2, FAR_RED_HI2 = (170, 50, 45), (179, 255, 255)
FAR_BLUE_LO, FAR_BLUE_HI = (100, 70, 40), (130, 255, 255)

_SMALL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

# A ring sign's white centre is about this fraction of its full width
# (circle ~0.78, triangle ~0.6); a hole is grown back to the full sign by 1/ratio.
HOLE_GROW = 1.35


def colour_mask(hsv, far=False):
    if far:
        # red only: all 20 signs have a red border, and a faded blue ring
        # (a sticker, a cup) was measured to fool the CNN into "speed limit 100"
        red = cv2.inRange(hsv, FAR_RED_LO1, FAR_RED_HI1) | cv2.inRange(hsv, FAR_RED_LO2, FAR_RED_HI2)
        return cv2.morphologyEx(red, cv2.MORPH_CLOSE, _SMALL)
    else:
        red = cv2.inRange(hsv, RED_LO1, RED_HI1) | cv2.inRange(hsv, RED_LO2, RED_HI2)
        blue = cv2.inRange(hsv, BLUE_LO, BLUE_HI)
    # Only a light 3x3 close: it joins small gaps in a ring without welding the
    # sign to nearby red/blue objects, and without erasing far-away thin rings.
    # (Tiny specks are dropped later by the minimum box size.)
    return cv2.morphologyEx(red | blue, cv2.MORPH_CLOSE, _SMALL)


def _sign_like(w, h, fill):
    """0..1 score: square-ish and properly filled scores high."""
    aspect = min(w, h) / max(w, h)
    return aspect * min(1.0, fill / 0.5)


def _sign_face(c, area):
    """Is this hole shaped like a sign's white face: a circle, a triangle or an
    octagon? Round faces have circularity near 1; triangles reduce to 3 corners."""
    perim = cv2.arcLength(c, True)
    if perim == 0:
        return False
    circularity = 4 * np.pi * area / (perim * perim)
    if circularity > 0.78:                      # circle or octagon
        return True
    corners = len(cv2.approxPolyDP(c, 0.06 * perim, True))
    return corners == 3 and circularity > 0.45  # triangle


def _white_centre(hsv, x, y, w, h):
    """Fraction of the box's middle that is white (low colour, bright).
    Ring signs have a white face; random red/blue clutter does not."""
    cx0, cy0 = x + int(0.3 * w), y + int(0.3 * h)
    cx1, cy1 = x + int(0.7 * w), y + int(0.7 * h)
    mid = hsv[max(0, cy0):max(cy0 + 1, cy1), max(0, cx0):max(cx0 + 1, cx1)]
    if mid.size == 0:
        return 0.0
    white = (mid[:, :, 1] < 70) & (mid[:, :, 2] > 120)
    return float(white.mean())


def _inside(a, b):
    """Fraction of box a that lies inside box b."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    return ix * iy / float(aw * ah + 1e-9)


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / float(aw * ah + bw * bh - inter + 1e-9)


def _candidates(hsv, mask, offset, frame_wh, mn, max_side):
    """Sign-like boxes in one colour mask: list of (score, box, is_hole)."""
    x0, y0 = offset
    W, H = frame_wh
    contours, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return []
    hier = hier[0]
    cands = []
    for i, c in enumerate(contours):
        x, y, w, h = cv2.boundingRect(c)
        is_hole = hier[i][3] >= 0               # has a parent -> it is a hole
        if is_hole:
            if w * HOLE_GROW < mn or h * HOLE_GROW < mn:
                continue
            area = cv2.contourArea(c)
            if area < 0.4 * w * h:              # holes of text/bars are thin; skip
                continue
            if not _sign_face(c, area):         # logos, letters, gaps between objects
                continue
            cx, cy = x + w / 2, y + h / 2
            side = max(w, h) * HOLE_GROW
            x, y, w, h = int(cx - side / 2), int(cy - side / 2), int(side), int(side)
            fill = 0.6
        else:
            if w < mn or h < mn:
                continue
            fill = cv2.contourArea(c) / float(w * h)
        if w > max_side or h > max_side:
            continue
        aspect = w / h
        if not 0.6 <= aspect <= 1.6:            # signs are roughly square
            continue
        if fill < 0.15:                         # thin streak, not a sign
            continue
        # pad 10 % so the CNN sees the whole border
        p = int(0.1 * max(w, h))
        bx, by = max(0, x + x0 - p), max(0, y + y0 - p)
        bw, bh = min(W - bx, w + 2 * p), min(H - by, h + 2 * p)
        if bw < mn or bh < mn:
            continue
        # rank: sign-shaped + white face (STOP / NO ENTRY are mostly red, so
        # whiteness only adds to the score, it never removes a candidate)
        score = _sign_like(w, h, fill) + 1.5 * _white_centre(hsv, x, y, w, h) \
            + (0.0 if is_hole else 0.05)
        cands.append((score, (bx, by, bw, bh), is_hole))

    # A hole lying inside a sign-shaped outline is part of that sign (the letters
    # of STOP, the bar of NO ENTRY), not a sign of its own: drop it.
    outers = [b for _, b, h in cands if not h]
    return [c for c in cands if not c[2] or all(_inside(c[1], o) < 0.6 for o in outers)]


def _pick(cands, n, taken=()):
    """Best n boxes, skipping near-duplicates of each other and of `taken`."""
    cands = sorted(cands, key=lambda c: c[0], reverse=True)
    boxes = []
    for _, b, _ in cands:
        if len(boxes) == n:
            break
        if all(_iou(b, k) < 0.4 for k in boxes) and \
                all(_iou(b, k) < 0.3 and _inside(b, k) < 0.6 for k in taken):
            boxes.append(b)
    return boxes


def propose(rgb, cfg):
    """Return candidate boxes (x, y, w, h) in full-frame pixels, most
    sign-like first.

    Two kinds of candidate:
      outer - a red/blue blob that is itself sign-shaped (the usual case);
      hole  - a white hole inside a red/blue region, grown back to sign size.
              This still finds a sign whose red ring has merged with a red or
              blue object behind it, which an outer-only search misses.

    Then a far-sign pass: the same search on a gentler colour mask, kept only
    for small boxes and given its own cfg.far_candidates slots, so it can never
    push a normal candidate out. Its boxes face the same CNN, margin, shape and
    3-of-5 checks as any other.
    """
    H, W = rgb.shape[:2]
    x0, y0 = int(cfg.roi[0] * W), int(cfg.roi[1] * H)
    x1, y1 = int(cfg.roi[2] * W), int(cfg.roi[3] * H)
    roi = rgb[y0:y1, x0:x1]

    roi = cv2.GaussianBlur(roi, (3, 3), 0)     # light: far signs are only a few px
    hsv = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)
    max_side = cfg.max_box_frac * min(W, H)
    near = _candidates(hsv, colour_mask(hsv), (x0, y0), (W, H), cfg.min_box_px, max_side)
    boxes = _pick(near, cfg.max_candidates)

    n_far = getattr(cfg, "far_candidates", 0)
    if n_far:
        far = _candidates(hsv, colour_mask(hsv, far=True), (x0, y0), (W, H),
                          cfg.far_min_px, cfg.far_max_px)
        boxes += _pick(far, n_far, taken=boxes)
    return boxes
