"""Stage 2 - Camera health (REQ-18).

If the picture has been too dark, too bright or featureless (mud, a cloth,
the lens cap) for 10 s straight, sign alerts are paused and the speech module
is told to say "Sign alerts paused". Overspeed warnings keep working because
they come from GPS, not the camera.
"""
import cv2
import numpy as np


def frame_quality(rgb):
    """Return (mean brightness, sharpness) on a small grey copy (cheap)."""
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    small = cv2.resize(grey, (160, 120), interpolation=cv2.INTER_AREA)
    return float(small.mean()), float(cv2.Laplacian(small, cv2.CV_64F).var())


class CameraHealth:
    def __init__(self, cfg):
        self.cfg = cfg
        self.bad_since = None
        self.blinded = False

    def is_bad(self, mean, sharp):
        c = self.cfg
        return (mean < c.blind_mean_below or mean > c.blind_mean_above
                or sharp < c.blind_sharpness_below)

    def update(self, rgb, ts):
        """Returns "blinded", "recovered" or None (no change)."""
        return self.update_values(*frame_quality(rgb), ts)

    def update_values(self, mean, sharp, ts):
        if self.is_bad(mean, sharp):
            if self.bad_since is None:
                self.bad_since = ts
            if not self.blinded and ts - self.bad_since >= self.cfg.blind_window_s:
                self.blinded = True
                return "blinded"
        else:
            self.bad_since = None
            if self.blinded:
                self.blinded = False
                return "recovered"
        return None
