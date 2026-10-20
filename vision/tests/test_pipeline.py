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


class TestTrainedModel(unittest.TestCase):
    """The shipped prototype model recognises the drawn signs (NumPy backend)."""

    def test_recognises_drawn_signs(self):
        root = Path(__file__).resolve().parents[1]
        model = root / "models" / "sign_cnn.npz"
        if not model.exists():
            self.skipTest("model not trained yet")
        from pipeline.classifier import NumpyClassifier
        from pipeline.config import LABELS
        cfg = Config()
        cfg.model_path = model
        clf = NumpyClassifier(cfg)
        crops, truth = [], []
        for lab in LABELS:
            im = cv2.imread(str(root / "demo" / "signs" / f"{lab}.png"), cv2.IMREAD_UNCHANGED)
            a = im[:, :, 3:4] / 255.0
            rgb = cv2.cvtColor((im[:, :, :3] * a + 128 * (1 - a)).astype(np.uint8), cv2.COLOR_BGR2RGB)
            crops.append(cv2.copyMakeBorder(rgb, 26, 26, 26, 26, cv2.BORDER_CONSTANT, value=(128, 128, 128)))
            truth.append(lab)
        got = [l for l, *_ in clf.classify(crops)]
        self.assertGreaterEqual(sum(g == t for g, t in zip(got, truth)), 19)


if __name__ == "__main__":
    unittest.main()


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "demo"))
from speed_sim import SpeedMonitor  # noqa: E402


def _run(mon, speed, t0, t1, dt=0.1):
    ev, t = [], t0
    while t < t1 - 1e-9:
        ev += mon.update(speed, t)
        t = round(t + dt, 6)
    return ev, t


class TestSpeedMonitor(unittest.TestCase):
    def test_no_limit_no_warning(self):
        ev, _ = _run(SpeedMonitor(), 120, 0, 20)
        self.assertEqual(ev, [])

    def test_within_margin_ok(self):          # 54 km/h under a 50 limit: inside +5 margin
        m = SpeedMonitor(); m.set_limit(50, 0)
        ev, _ = _run(m, 54, 0, 20)
        self.assertEqual(ev, [])

    def test_warns_after_3s_then_every_30s(self):   # REQ-05
        m = SpeedMonitor(); m.set_limit(50, 0)
        ev, _ = _run(m, 70, 0, 2.9)
        self.assertEqual(ev, [])                    # not yet 3 s
        ev, _ = _run(m, 70, 2.9, 3.2)
        self.assertEqual(ev, [("warn", "Slow down. Speed limit 50")])
        ev, _ = _run(m, 70, 3.2, 32.9)
        self.assertEqual(ev, [])                    # no repeat within 30 s
        ev, _ = _run(m, 70, 32.9, 33.3)
        self.assertEqual(len(ev), 1)

    def test_short_overspeed_ignored(self):
        m = SpeedMonitor(); m.set_limit(50, 0)
        ev, t = _run(m, 70, 0, 2.0)
        ev2, _ = _run(m, 45, t, 4.0)
        ev3, _ = _run(m, 70, 4.0, 6.5)
        self.assertEqual(ev + ev2 + ev3, [])

    def test_limit_expires_after_5km(self):        # REQ-19
        m = SpeedMonitor(); m.set_limit(50, 0)
        ev, _ = _run(m, 50, 0, 359, dt=1.0)         # 50 km/h x 359 s = 4.99 km
        self.assertEqual(m.limit, 50)
        ev, _ = _run(m, 50, 359, 362, dt=1.0)
        self.assertEqual(m.limit, None)
        self.assertEqual(ev[0][0], "expired")

    def test_new_sign_replaces_limit(self):
        m = SpeedMonitor(); m.set_limit(50, 0)
        _run(m, 70, 0, 5)
        m.set_limit(80, 5)
        ev, _ = _run(m, 70, 5, 40)
        self.assertEqual(ev, [])
        self.assertEqual(m.state, "ok")


from trip_log import TripLog  # noqa: E402


def _drive(log, speed, limit, t0, t1, dt=0.1):
    t = t0
    while t < t1 - 1e-9:
        log.update(speed, limit, t)
        t = round(t + dt, 6)
    return t


class TestTripLog(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "trips.db"
        self.log = TripLog(self.path)
        self.log.start_trip(0.0)

    def events(self, kind):
        return self.log.db.execute("SELECT * FROM events WHERE kind=?", (kind,)).fetchall()

    def test_overspeed_episode_recorded(self):
        t = _drive(self.log, 40, 50, 0, 5)
        t = _drive(self.log, 72, 50, t, 13)          # 8 s over
        t = _drive(self.log, 45, 50, t, 15)
        ev = self.events("overspeed")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["limit_kmh"], 50)
        self.assertEqual(ev[0]["speed"], 72)
        self.assertAlmostEqual(ev[0]["duration_s"], 8.0, delta=0.15)

    def test_short_overspeed_not_recorded(self):
        t = _drive(self.log, 70, 50, 0, 2)
        _drive(self.log, 40, 50, t, 4)
        self.assertEqual(self.events("overspeed"), [])

    def test_no_limit_no_overspeed(self):
        _drive(self.log, 120, None, 0, 10)
        self.assertEqual(self.events("overspeed"), [])

    def test_harsh_brake(self):
        t = _drive(self.log, 60, None, 0, 2)
        t = _drive(self.log, 30, None, t, 3)         # 60 -> 30 in one frame: harsh
        self.assertEqual(len(self.events("harsh_brake")), 1)

    def test_gentle_brake_not_harsh(self):
        t, v = 0.0, 60.0
        while v > 30:                                 # 5 km/h per second
            self.log.update(v, None, t)
            t += 0.1
            v -= 0.5
        self.assertEqual(self.events("harsh_brake"), [])

    def test_trip_totals_and_summary(self):
        self.log.log_sign("speed_limit_50", 1)
        t = _drive(self.log, 60, None, 0, 60)        # 1 min at 60 = 1 km
        t = _drive(self.log, 72, 50, t, 70)
        out = self.log.end_trip(t)
        row = self.log.db.execute("SELECT * FROM trips WHERE id=?", (out["trip_id"],)).fetchone()
        self.assertAlmostEqual(row["distance_km"], 1.2, delta=0.02)
        self.assertEqual(row["max_speed"], 72)
        self.assertEqual(row["overspeed_count"], 1)
        self.assertEqual(row["signs"], 1)
        self.assertIn("1.2 km", row["summary"])
        self.assertIn("72 km/h in a 50 zone", row["summary"])

    def test_keeps_only_last_20_trips(self):             # REQ-11
        self.log.end_trip(1)
        for i in range(25):
            self.log.start_trip(10 + i)
            self.log.end_trip(10.5 + i)
        n = self.log.db.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
        self.assertEqual(n, 20)


class TestTripApp(unittest.TestCase):
    def test_pages_and_api(self):
        from app_server import create_app
        path = Path(tempfile.mkdtemp()) / "trips.db"
        log = TripLog(path)
        log.start_trip(1000.0)
        log.log_sign("speed_limit_50", 1001)
        t = _drive(log, 72, 50, 1000, 1010)
        log.end_trip(t)
        c = create_app(path).test_client()
        self.assertIn(b"1 over limit", c.get("/").data)
        page = c.get("/trip/1").data
        self.assertIn(b"Speed limit 50", page)
        self.assertIn(b"72 km/h</span> in a 50 zone", page)
        self.assertIn(b"<svg class=\"trace", page)          # speed chart drawn
        data = c.get("/api/trip/1").get_json()
        self.assertEqual(data["trip"]["overspeed_count"], 1)
        self.assertEqual(c.get("/trip/99").status_code, 404)

    def test_empty_database(self):
        from app_server import create_app
        c = create_app(Path(tempfile.mkdtemp()) / "none.db").test_client()
        self.assertIn(b"No trips yet", c.get("/").data)
        self.assertIn(b"Ask about your driving", c.get("/ask").data)


import json as _json
import threading as _threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import llm as llm_mod  # noqa: E402


class _FakeModel(BaseHTTPRequestHandler):
    """Stands in for Ollama / llama-server: replies from a script."""
    replies = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"data":[]}')

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        text = _FakeModel.replies.pop(0)
        body = _json.dumps({"choices": [{"message": {"content": text}}]}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)


class TestLLM(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _FakeModel)
        _threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.llm = llm_mod.LocalLLM(f"http://127.0.0.1:{cls.srv.server_port}/v1", "fake")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def make_trip(self):
        path = Path(tempfile.mkdtemp()) / "trips.db"
        log = TripLog(path)
        log.start_trip(1000.0)
        t = _drive(log, 40, 50, 1000, 1060)
        t = _drive(log, 72, 50, t, 1068)
        t = _drive(log, 45, 50, t, 1120)
        done = log.end_trip(t)
        return log.db, done

    def test_good_summary_is_verified(self):
        con, done = self.make_trip()
        _FakeModel.replies = ["You drove for 2 minutes and reached 72 km/h in a 50 zone. "
                              "Ease off a little next time."]
        r = llm_mod.summarize_trip(con, done["trip_id"], done["summary"], self.llm)
        self.assertEqual(r["source"], "llm")
        self.assertTrue(r["verified"])

    def test_invented_number_is_rejected_then_retried(self):
        con, done = self.make_trip()
        _FakeModel.replies = ["You hit 95 km/h.",                      # not in facts
                              "Your top speed was 72 km/h."]           # ok on retry
        r = llm_mod.summarize_trip(con, done["trip_id"], done["summary"], self.llm)
        self.assertEqual(r["text"], "Your top speed was 72 km/h.")
        self.assertEqual(r["log"][0]["unsupported"], ["95"])

    def test_falls_back_to_template(self):
        con, done = self.make_trip()
        _FakeModel.replies = ["You drove 300 km.", "You drove 301 km."]
        r = llm_mod.summarize_trip(con, done["trip_id"], done["summary"], self.llm)
        self.assertEqual(r["source"], "template")
        self.assertEqual(r["text"], done["summary"])

    def test_no_server_uses_template(self):
        con, done = self.make_trip()
        off = llm_mod.LocalLLM("http://127.0.0.1:9/v1", "x")
        r = llm_mod.summarize_trip(con, done["trip_id"], done["summary"], off)
        self.assertEqual(r["source"], "template")

    def test_ask(self):
        con, _ = self.make_trip()
        _FakeModel.replies = ["Your last trip had 1 overspeed event, at 72 km/h in a 50 zone."]
        r = llm_mod.answer_question(con, "Did I speed?", self.llm)
        self.assertTrue(r["verified"])
        self.assertIn("72", r["answer"])


class TestInterruptedTrip(unittest.TestCase):
    def test_unfinished_trip_is_closed_on_next_start(self):       # REQ-23
        path = Path(tempfile.mkdtemp()) / "trips.db"
        log = TripLog(path)
        log.start_trip(1000.0)
        _drive(log, 50, None, 1000, 1030)          # then the program is killed: no end_trip
        log._flush(1030)
        again = TripLog(path)                       # next start
        self.assertEqual(again.recovered, [1])
        row = again.db.execute("SELECT ended_at, summary FROM trips WHERE id=1").fetchone()
        self.assertAlmostEqual(row["ended_at"], 1029.9, delta=1.5)
        self.assertIn("stopped unexpectedly", row["summary"])

    def test_app_does_not_show_stale_trip_as_driving(self):
        from app_server import create_app
        path = Path(tempfile.mkdtemp()) / "trips.db"
        log = TripLog(path)
        log.start_trip(1000.0)                      # long ago, never ended
        _drive(log, 50, None, 1000, 1010)
        page = create_app(path).test_client().get("/").data
        self.assertIn(b"Not finished", page)
        self.assertNotIn(b"Driving now", page)


class TestShapeCheck(unittest.TestCase):
    def test_red_box_with_white_text_is_not_stop(self):
        from pipeline.verify import shape_agrees
        img = np.full((120, 120, 3), 120, np.uint8)
        cv2.rectangle(img, (10, 15), (110, 105), (200, 30, 35), -1)
        cv2.putText(img, "NIKE", (18, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (245, 245, 245), 3)
        self.assertFalse(shape_agrees(img, "stop"))

    def test_blue_ring_is_not_a_speed_limit(self):
        from pipeline.verify import shape_agrees
        img = np.full((120, 120, 3), 120, np.uint8)
        cv2.circle(img, (60, 60), 50, (30, 70, 190), -1)
        cv2.circle(img, (60, 60), 38, (240, 240, 240), -1)
        self.assertFalse(shape_agrees(img, "speed_limit_100"))

    def test_every_drawn_sign_passes_its_own_check(self):
        from pipeline.config import LABELS
        from pipeline.verify import shape_agrees
        root = Path(__file__).resolve().parents[1]
        for lab in LABELS:
            im = cv2.imread(str(root / "demo" / "signs" / f"{lab}.png"), cv2.IMREAD_UNCHANGED)
            a = im[:, :, 3:4] / 255.0
            rgb = cv2.cvtColor((im[:, :, :3] * a + 120 * (1 - a)).astype(np.uint8), cv2.COLOR_BGR2RGB)
            crop = cv2.copyMakeBorder(cv2.resize(rgb, (80, 80)), 8, 8, 8, 8,
                                      cv2.BORDER_CONSTANT, value=(120, 120, 120))
            self.assertTrue(shape_agrees(crop, lab), lab)


    def test_sign_merged_with_red_clutter_still_passes(self):
        # a red box behind the sign spoils the outline; the round face decides
        from pipeline.verify import outline_shape, shape_agrees
        img = np.full((120, 120, 3), 120, np.uint8)
        cv2.rectangle(img, (0, 0), (120, 30), (200, 30, 35), -1)      # red boards above
        cv2.rectangle(img, (0, 90), (120, 120), (200, 30, 35), -1)    # and below the sign
        cv2.circle(img, (60, 60), 50, (220, 20, 20), -1)
        cv2.circle(img, (60, 60), 36, (245, 245, 245), -1)
        cv2.putText(img, "50", (40, 72), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 3)
        self.assertEqual(outline_shape(img)[0], "rectangle")
        self.assertTrue(shape_agrees(img, "speed_limit_50"))

    def test_red_frame_with_square_window_is_not_round(self):
        from pipeline.verify import shape_agrees
        img = np.full((120, 120, 3), 120, np.uint8)
        cv2.rectangle(img, (10, 10), (110, 110), (200, 30, 35), -1)
        cv2.rectangle(img, (28, 28), (92, 92), (240, 240, 240), -1)
        self.assertFalse(shape_agrees(img, "speed_limit_50"))
        self.assertFalse(shape_agrees(img, "stop"))


class TestFarPass(unittest.TestCase):
    def _scene(self, colour, face=(235, 235, 230), r=8):
        img = np.full((480, 640, 3), 140, np.uint8)
        cv2.circle(img, (300, 150), r, colour, -1)
        cv2.circle(img, (300, 150), int(r * 0.7), face, -1)
        return cv2.GaussianBlur(img, (3, 3), 0)

    def _hit(self, img, far):
        cfg = Config()
        cfg.far_candidates = 2 if far else 0
        return any(abs(x + w / 2 - 300) < 6 and abs(y + h / 2 - 150) < 6
                   for x, y, w, h in propose(img, cfg))

    def test_faded_far_sign_found_only_by_far_pass(self):
        img = self._scene((165, 115, 110))          # washed-out red ring, 16 px
        self.assertFalse(self._hit(img, far=False))
        self.assertTrue(self._hit(img, far=True))

    def test_far_pass_ignores_faded_blue(self):
        self.assertFalse(self._hit(self._scene((110, 120, 175)), far=True))

    def test_far_pass_ignores_big_faded_objects(self):
        # the same faded colour at 80 px is not a far sign: the far pass leaves it alone
        self.assertFalse(self._hit(self._scene((165, 115, 110), r=40), far=True))


class TestIdentityLock(unittest.TestCase):
    def test_confirmed_sign_cannot_flip_to_another(self):
        cfg = Config()
        cfg.verify_shape = False
        pub = ListPublisher()
        pipe = VisionPipeline(cfg, FakeClassifier(), pub)
        seq = ["speed_limit_50"] * 6 + ["speed_limit_100"] * 6      # same spot, label flips
        pipe.clf = type("C", (), {"classify": lambda self, crops: [(seq.pop(0) if seq else "speed_limit_50", 0.97, 0.9)] * len(crops)})()
        for _ in range(12):
            pipe.process(road_frame(cx=520, cy=120), time.monotonic())
        signs = [e["label"] for e in pub.events if e["type"] == "sign"]
        self.assertEqual(signs, ["speed_limit_50"])
