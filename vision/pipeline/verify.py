"""Stage 4b - Does the shape agree with the CNN? (independent second check)

The CNN looks at a 32x32 patch and cannot tell a red rectangle with white
writing (a T-shirt, a box) from a STOP sign, or a blue mug from a speed-limit
ring. Plain geometry can. All 20 sign classes have a RED border whose outline
is either ROUND (circle, or STOP's octagon) or a TRIANGLE. So a detection only
counts if:
  1. the object is mainly red, not blue, and
  2. its red outline has the shape the CNN's class should have
     (a rectangle is never a valid sign). If background clutter has merged
     with the ring and spoiled the outline, the white face inside the ring
     (circle or triangle) can stand in for it (face_shape).
Very small, far-away signs (< 24 px) are too few pixels to measure shape
reliably, so for them only the colour rule is applied.
"""
import cv2
import numpy as np

from .proposals import (BLUE_HI, BLUE_LO, FAR_BLUE_HI, FAR_BLUE_LO, FAR_RED_HI1,
                        FAR_RED_HI2, FAR_RED_LO1, FAR_RED_LO2, RED_HI1, RED_HI2,
                        RED_LO1, RED_LO2)

TRIANGLE = {"give_way", "school_ahead", "pedestrian_crossing", "speed_breaker",
            "left_curve", "right_curve", "men_at_work"}
MIN_SHAPE_PX = 24
_K = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))


def outline_shape(crop_rgb):
    """Return (shape, red_fraction) where shape is 'round', 'triangle',
    'rectangle' or 'unknown'."""
    hsv = cv2.cvtColor(cv2.GaussianBlur(crop_rgb, (3, 3), 0), cv2.COLOR_RGB2HSV)
    if max(crop_rgb.shape[:2]) < MIN_SHAPE_PX:
        # far sign: its thin ring is washed out, so use the far-pass colour bands
        red = cv2.inRange(hsv, FAR_RED_LO1, FAR_RED_HI1) | cv2.inRange(hsv, FAR_RED_LO2, FAR_RED_HI2)
        blue = cv2.inRange(hsv, FAR_BLUE_LO, FAR_BLUE_HI)
    else:
        red = cv2.inRange(hsv, RED_LO1, RED_HI1) | cv2.inRange(hsv, RED_LO2, RED_HI2)
        blue = cv2.inRange(hsv, BLUE_LO, BLUE_HI)
    n_red, n_blue = int(np.count_nonzero(red)), int(np.count_nonzero(blue))
    red_frac = n_red / max(1, n_red + n_blue)
    red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, _K)
    cs, _ = cv2.findContours(red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cs:
        return "unknown", red_frac
    c = max(cs, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(c)
    hull = cv2.convexHull(c)                 # outer envelope: works even if the ring is broken
    hull_area = max(1.0, cv2.contourArea(hull))
    if hull_area < 0.15 * crop_rgb.shape[0] * crop_rgb.shape[1]:
        return "unknown", red_frac           # the red part is a small piece of the crop
    fill = hull_area / float(w * h)          # triangle ~0.5, circle ~0.79, octagon ~0.83, square ~1
    perim = cv2.arcLength(hull, True)
    corners = len(cv2.approxPolyDP(hull, 0.04 * perim, True))
    if fill > 0.9 and corners <= 5:
        return "rectangle", red_frac
    if fill < 0.68 and corners <= 4:
        return "triangle", red_frac
    if 0.68 <= fill <= 0.92:
        return "round", red_frac
    return "unknown", red_frac


def face_shape(crop_rgb):
    """Shape of the white face inside the red ring: 'round', 'triangle' or None.

    A red object behind the sign can merge with the ring and make the OUTLINE
    look square, but it cannot change the face inside the ring. Measured on
    cluttered scenes this rescues ~1 in 8 real signs the outline test dropped.
    A face must be big, centred, roughly square, and solid non-red inside its
    hull: letters on a red board fail that (they have red gaps; measured
    <= 0.68 vs >= 0.89 for real sign faces), as does a square window frame (fill ~1).
    """
    hsv = cv2.cvtColor(cv2.GaussianBlur(crop_rgb, (3, 3), 0), cv2.COLOR_RGB2HSV)
    red = cv2.inRange(hsv, RED_LO1, RED_HI1) | cv2.inRange(hsv, RED_LO2, RED_HI2)
    red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, _K)
    cs, hier = cv2.findContours(red, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return None
    H, W = crop_rgb.shape[:2]
    for c, h in zip(cs, hier[0]):
        if h[3] < 0:                         # only holes in the red mask
            continue
        x, y, w, hh = cv2.boundingRect(c)
        if max(w, hh) < 0.45 * min(H, W):    # small hole: a letter or a logo
            continue
        if not 0.75 <= w / hh <= 1.33:
            continue
        if abs(x + w / 2 - W / 2) > 0.2 * W or abs(y + hh / 2 - H / 2) > 0.25 * H:
            continue
        hull = cv2.convexHull(c)
        m = np.zeros(red.shape, np.uint8)
        cv2.drawContours(m, [hull], -1, 255, -1)
        inside = cv2.erode(m, _K) > 0        # ignore the blurry edge pixel
        if np.count_nonzero(inside & (red == 0)) < 0.8 * np.count_nonzero(inside):
            continue                         # red gaps inside: letters, not a face
        area = cv2.contourArea(c)
        fill = area / float(w * hh)          # circle ~0.79, triangle ~0.5, square ~1
        perim = cv2.arcLength(c, True)
        circ = 4 * np.pi * area / (perim * perim + 1e-9)
        corners = len(cv2.approxPolyDP(c, 0.06 * perim, True))
        if 0.68 <= fill <= 0.88 and circ > 0.75:
            return "round"
        if fill < 0.65 and corners == 3:
            return "triangle"
    return None


def shape_agrees(crop_rgb, label):
    shape, red_frac = outline_shape(crop_rgb)
    if red_frac < 0.6:                       # mostly blue (or no red at all): not one of our signs
        return False
    if max(crop_rgb.shape[:2]) < MIN_SHAPE_PX:
        return True                          # too small to judge shape; colour check only
    want = "triangle" if label in TRIANGLE else "round"
    # outline first; if clutter spoiled the outline, the face inside the ring decides
    return shape == want or face_shape(crop_rgb) == want
