"""Unit + end-to-end tests. Run from the project root:
    python -m unittest discover -s tests -v
"""
import json
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.classifier import FakeClassifier          # noqa: E402
from pipeline.config import Config                      # noqa: E402
from pipeline.health import CameraHealth                # noqa: E402
from pipeline.main import VisionPipeline                # noqa: E402
from pipeline.proposals import propose                  # noqa: E402
from pipeline.publisher import ListPublisher, Publisher  # noqa: E402
from pipeline.voting import TemporalVoter               # noqa: E402


def road_frame(with_sign=True, cx=520, cy=120, r=30):
    """Grey road scene with texture; optional red-ringed circular sign."""
    rng = np.random.default_rng(0)
    img = (rng.normal(110, 25, (480, 640, 3))).clip(0, 255).astype(np.uint8)
    img[300:] = 70                                   # road
    if with_sign:
        cv2.circle(img, (cx, cy), r, (220, 20, 20), -1)       # red disc (RGB)
        cv2.circle(img, (cx, cy), int(r * 0.7), (245, 245, 245), -1)  # white face
        cv2.putText(img, "50", (cx - 16, cy + 8), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 0, 0), 2)
    return img


class TestVoting(unittest.TestCase):
    def test_needs_three_of_five(self):
        v = TemporalVoter(5, 3, 15)
        self.assertEqual(v.update({"stop"}), [])
        self.assertEqual(v.update(set()), [])
        self.assertEqual(v.update({"stop"}), [])
        self.assertEqual(v.update({"stop"}), ["stop"])   # 3rd vote in window

    def test_single_glitches_never_confirm(self):
        v = TemporalVoter(5, 3, 15)
        out = []
        for i in range(100):                              # 1 bad frame in 5
            out += v.update({"no_entry"} if i % 5 == 0 else set())
        self.assertEqual(out, [])

    def test_spoken_once_per_sighting(self):
        v = TemporalVoter(5, 3, 15)
        out = []
        for _ in range(40):                               # long sighting
            out += v.update({"speed_limit_50"})
        self.assertEqual(out, ["speed_limit_50"])

    def test_new_sighting_after_gap(self):
        v = TemporalVoter(5, 3, 15)
        out = []
        for _ in range(5):
            out += v.update({"stop"})
        for _ in range(20):                               # > 1 s gone
            out += v.update(set())
        for _ in range(5):
            out += v.update({"stop"})
        self.assertEqual(out, ["stop", "stop"])

    def test_short_occlusion_does_not_repeat(self):
        v = TemporalVoter(5, 3, 15)
        out = []
        for _ in range(5):
            out += v.update({"stop"})
        for _ in range(5):                                # truck passes briefly
            out += v.update(set())
        for _ in range(5):
            out += v.update({"stop"})
        self.assertEqual(out, ["stop"])

    def test_confirmation_delay_at_15fps(self):
        # REQ-02 budget: confirmation needs 3 frames = 2 frame intervals
        v = TemporalVoter(5, 3, 15)
        n = 0
        while not v.update({"stop"}):
            n += 1
        self.assertEqual(n, 2)                            # 2/15 s = 133 ms


class TestHealth(unittest.TestCase):
    def test_blinded_after_10s_and_recovers(self):
        h = CameraHealth(Config())
        self.assertIsNone(h.update_values(5, 1, 0.0))
        self.assertIsNone(h.update_values(5, 1, 9.9))
        self.assertEqual(h.update_values(5, 1, 10.0), "blinded")
        self.assertIsNone(h.update_values(5, 1, 11.0))
        self.assertEqual(h.update_values(120, 300, 12.0), "recovered")

    def test_brief_dark_patch_ignored(self):
        h = CameraHealth(Config())
        h.update_values(5, 1, 0.0)                         # tunnel entry
        h.update_values(120, 300, 3.0)                     # out again
        self.assertIsNone(h.update_values(5, 1, 9.0))
        self.assertFalse(h.blinded)

    def test_real_frames(self):
        h = CameraHealth(Config())
        black = np.zeros((480, 640, 3), np.uint8)
        self.assertFalse(h.is_bad(*__import__("pipeline.health", fromlist=["x"])
                                  .frame_quality(road_frame())))
        for t in range(12):
            r = h.update(black, float(t))
        self.assertTrue(h.blinded)


class TestProposals(unittest.TestCase):
    def test_finds_red_sign(self):
        boxes = propose(road_frame(cx=520, cy=120), Config())
        self.assertGreaterEqual(len(boxes), 1)
        x, y, w, h = boxes[0]
        self.assertTrue(x <= 520 <= x + w and y <= 120 <= y + h)

    def test_empty_scene(self):
        self.assertEqual(propose(road_frame(with_sign=False), Config()), [])

    def test_ignores_below_roi(self):
        # red object on the road surface (e.g. a car's tail) is outside the ROI
        self.assertEqual(propose(road_frame(cy=420), Config()), [])

    def test_blue_sign(self):
        img = road_frame(with_sign=False)
        cv2.circle(img, (100, 100), 28, (20, 60, 200), -1)
        self.assertEqual(len(propose(img, Config())), 1)


class TestEndToEnd(unittest.TestCase):
    def test_drive_past_sign(self):
        """30 frames: sign visible 10 frames in the middle -> one alert."""
        cfg = Config()
        pub = ListPublisher()
        pipe = VisionPipeline(cfg, FakeClassifier(), pub)
        pipe.clf = type("C", (), {"classify": lambda self, crops:
                                  [("speed_limit_50", 0.94)] * len(crops)})()
        for i in range(30):
            pipe.process(road_frame(with_sign=10 <= i < 20), time.monotonic())
        signs = [e for e in pub.events if e["type"] == "sign"]
        self.assertEqual(len(signs), 1)
        self.assertEqual(signs[0]["label"], "speed_limit_50")
        self.assertEqual(signs[0]["speed_kmh"], 50)

    def test_low_confidence_ignored(self):
        cfg = Config()
        pub = ListPublisher()
        pipe = VisionPipeline(cfg, FakeClassifier(), pub)
        pipe.clf = type("C", (), {"classify": lambda self, crops:
                                  [("stop", 0.55)] * len(crops)})()
        for _ in range(20):
            pipe.process(road_frame(), time.monotonic())
        self.assertEqual([e for e in pub.events if e["type"] == "sign"], [])

    def test_blinded_camera_event(self):
        cfg = Config()
        pub = ListPublisher()
        pipe = VisionPipeline(cfg, FakeClassifier(), pub)
        black = np.zeros((480, 640, 3), np.uint8)
        for t in range(12):
            pipe.process(black, float(t))
        self.assertIn({"type": "camera", "state": "blinded"},
                      [{k: e[k] for k in ("type", "state")} for e in pub.events
                       if e["type"] == "camera"])


class TestSocket(unittest.TestCase):
    def test_subscribe_and_receive(self):
        d = tempfile.mkdtemp()
        pub = Publisher(Path(d) / "vision.sock")
        self.assertEqual(os.stat(Path(d) / "vision.sock").st_mode & 0o777, 0o600)
        cli = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        cli.bind(os.path.join(d, "client.sock"))
        cli.sendto(b"subscribe", str(Path(d) / "vision.sock"))
        time.sleep(0.05)
        pub.send({"type": "sign", "label": "stop"})
        cli.settimeout(1)
        ev = json.loads(cli.recv(4096))
        self.assertEqual(ev["label"], "stop")
        pub.close()


if __name__ == "__main__":
    unittest.main()
