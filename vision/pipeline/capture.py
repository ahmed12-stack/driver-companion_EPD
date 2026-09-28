"""Stage 1 - Frame capture.

A background thread keeps only the NEWEST frame. If the rest of the
pipeline is slow, old frames are dropped instead of queued, so the delay
between the road and the alert never grows (REQ-02).
"""
import threading
import time

import numpy as np


class LatestFrame:
    """One-slot mailbox: writer overwrites, reader takes the newest."""

    def __init__(self):
        self._cond = threading.Condition()
        self._frame = None
        self._ts = 0.0
        self._seq = 0

    def put(self, frame, ts):
        with self._cond:
            self._frame, self._ts = frame, ts
            self._seq += 1
            self._cond.notify()

    def get(self, last_seq, timeout=1.0):
        """Wait for a frame newer than last_seq. Returns (frame, ts, seq) or None."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._seq > last_seq, timeout):
                return None
            return self._frame, self._ts, self._seq


class CameraSource(threading.Thread):
    """Reads frames from Pi Camera Module 3 (Picamera2) or, off-Pi, OpenCV.

    source: "picamera" | int (USB webcam index) | path to a video file
    """

    def __init__(self, cfg, mailbox, source="picamera"):
        super().__init__(daemon=True, name="capture")
        self.cfg, self.mailbox, self.source = cfg, mailbox, source
        self.fps = cfg.target_fps
        self._stop = threading.Event()
        self.frames_read = 0
        self.finished = threading.Event()

    def set_fps(self, fps):
        self.fps = fps

    def stop(self):
        self._stop.set()

    # --- backends ---------------------------------------------------------
    def _open_picamera(self):
        from picamera2 import Picamera2  # only present on the Pi
        cam = Picamera2()
        cam.configure(cam.create_video_configuration(
            main={"size": (self.cfg.frame_width, self.cfg.frame_height),
                  "format": "RGB888"}))
        cam.start()
        return lambda: cam.capture_array(), cam.stop

    def _open_opencv(self):
        import cv2
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            raise RuntimeError(f"cannot open video source {self.source!r}")

        def read():
            ok, bgr = cap.read()
            if not ok:
                return None
            bgr = cv2.resize(bgr, (self.cfg.frame_width, self.cfg.frame_height))
            return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return read, cap.release

    # --- loop -------------------------------------------------------------
    def run(self):
        read, close = (self._open_picamera() if self.source == "picamera"
                       else self._open_opencv())
        try:
            next_t = time.monotonic()
            while not self._stop.is_set():
                frame = read()
                if frame is None:          # end of video file
                    break
                ts = time.monotonic()      # timestamp at capture -> latency
                self.mailbox.put(np.ascontiguousarray(frame), ts)
                self.frames_read += 1
                next_t += 1.0 / self.fps
                delay = next_t - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_t = time.monotonic()  # fell behind: don't burst
        finally:
            close()
            self.finished.set()
