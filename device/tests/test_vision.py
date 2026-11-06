import asyncio
import os
import sys
import threading
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from dc_common import phrases, topics  # noqa: E402
from dc_common.bus import MemoryBroker, MemoryBus  # noqa: E402
from dc_vision.__main__ import Supervisor  # noqa: E402
from dc_vision.reporter import SignReporter  # noqa: E402

VISION_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "vision")


class Posts:
    def __init__(self):
        self.items = []

    def __call__(self, topic, data, retain=False):
        self.items.append((topic, data, retain))

    def on(self, topic):
        return [d for t, d, _ in self.items if t == topic]

    def said(self):
        return [d["text"] for d in self.on(topics.SAY)]


def test_sign_is_spoken_counted_and_limit_kept():
    posts = Posts()
    r = SignReporter(posts, wall=lambda: 1000.0)
    r.on_event({"type": "sign", "label": "speed_limit_50", "speed_kmh": 50, "confidence": 0.97})
    assert posts.on(topics.SAY) == [{"text": "Speed limit 50.", "priority": "sign", "max_age": 3.0}]
    assert posts.on(topics.TRIP_EVENT) == [{"event": "sign"}]
    assert (topics.SPEED_LIMIT, {"kmh": 50, "ts": 1000.0}, True) in posts.items


def test_other_signs_do_not_set_a_limit():
    posts = Posts()
    r = SignReporter(posts)
    r.on_event({"type": "sign", "label": "school_ahead"})
    assert posts.said() == ["School ahead."]
    assert posts.on(topics.SPEED_LIMIT) == []


def test_every_sign_has_words():
    sys.path.insert(0, VISION_DIR)
    try:
        from pipeline.config import LABELS
    except ImportError:
        pytest.skip("vision folder not next to device/")
    assert set(LABELS) == set(phrases.SIGN_PHRASES)


def test_blinded_camera_pauses_and_resumes():
    posts = Posts()
    r = SignReporter(posts)
    r.on_event({"type": "heartbeat", "fps": 15})
    r.on_event({"type": "camera", "state": "blinded"})
    r.on_event({"type": "sign", "label": "stop"})  # never spoken during a pause
    r.on_event({"type": "camera", "state": "recovered"})
    assert posts.said() == [phrases.SIGNS_PAUSED, phrases.SIGNS_RESUMED]
    assert [d["state"] for d in posts.on(topics.VISION_CAMERA)] == ["ok", "blinded", "ok"]


def test_missing_camera_said_once():
    posts = Posts()
    r = SignReporter(posts)
    for _ in range(3):
        r.set_camera("missing")
        r.set_camera("off")
    assert posts.said() == [phrases.CAMERA_MISSING]


def test_camera_found_after_missing():
    posts = Posts()
    r = SignReporter(posts)
    r.set_camera("missing")
    r.on_event({"type": "heartbeat", "fps": 15})
    assert posts.said() == [phrases.CAMERA_MISSING, phrases.CAMERA_FOUND]


# ----- supervisor -------------------------------------------------------------

def run_supervisor(script, steps):
    """script: results the fake pipeline returns per run ("block" = runs until stopped)."""
    async def go():
        posts = Posts()
        r = SignReporter(posts)
        stops = []

        async def start(stop):
            stops.append(stop)
            result = script.pop(0) if script else "block"
            if result == "block":
                while not stop.is_set():
                    await asyncio.sleep(0.01)
                return "stopped"
            if isinstance(result, Exception):
                raise result
            return result

        sup = Supervisor(r, start, retry_s=0.05)
        task = asyncio.ensure_future(sup.run())
        for step in steps:
            if step == "on":
                sup.ignition(True)
            elif step == "off":
                sup.ignition(False)
            else:
                await asyncio.sleep(step)
        task.cancel()
        return sup, r, posts, stops
    return asyncio.run(go())


def test_runs_only_while_ignition_on():
    sup, r, posts, stops = run_supervisor([], [0.05, "on", 0.05, "off", 0.05])
    assert sup.runs == 1 and stops[0].is_set() and r.camera == "off"


def test_no_camera_retries_and_warns_once():
    sup, r, posts, stops = run_supervisor(["missing", "missing", "missing"], ["on", 0.3])
    assert sup.runs >= 3
    assert posts.said().count(phrases.CAMERA_MISSING) == 1


def test_pipeline_crash_is_retried():
    sup, r, posts, stops = run_supervisor([RuntimeError("model")], ["on", 0.2])
    assert sup.runs >= 2
    assert phrases.SIGNS_FAILED in posts.said()


# ----- the real pipeline on a test video ------------------------------------------

def make_video(path, frames=45):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    sign_png = os.path.join(VISION_DIR, "demo", "signs", "speed_limit_50.png")
    sign = cv2.imread(sign_png)
    if sign is None:
        pytest.skip("demo sign image not available")
    rng = np.random.default_rng(0)
    bg = rng.normal(110, 25, (480, 640, 3)).clip(0, 255).astype(np.uint8)
    bg[300:] = 70
    frame = bg.copy()
    frame[80:150, 480:550] = cv2.resize(sign, (70, 70), interpolation=cv2.INTER_AREA)
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 15, (640, 480))
    for i in range(frames):
        out.write(frame if i >= 5 else bg)
    out.release()


def test_real_pipeline_reads_a_sign_from_video(tmp_path):
    if not os.path.isdir(os.path.join(VISION_DIR, "pipeline")):
        pytest.skip("vision folder not next to device/")
    video = tmp_path / "drive.avi"
    make_video(video)
    from dc_vision import __main__ as vision

    async def go():
        broker = MemoryBroker()
        bus = MemoryBus("dc_vision", broker)
        args = types.SimpleNamespace(vision_dir=VISION_DIR, source=str(video), always_on=False,
                                     model=os.path.join(VISION_DIR, "models", "sign_cnn.npz"))
        cleanup = await vision.setup(bus, args)
        other = MemoryBus("dc_ignition", broker)
        await other.publish(topics.IGNITION, {"on": True}, retain=True)
        for _ in range(100):
            if any(t == topics.SPEED_LIMIT for t, _ in broker.log):
                break
            await asyncio.sleep(0.1)
        await other.publish(topics.IGNITION, {"on": False}, retain=True)
        await cleanup()
        return broker.log

    log = asyncio.run(go())
    said = [d["text"] for t, d in log if t == topics.SAY]
    assert said.count("Speed limit 50.") == 1
    assert [d for t, d in log if t == topics.TRIP_EVENT] == [{"event": "sign"}]
    assert [d["kmh"] for t, d in log if t == topics.SPEED_LIMIT] == [50]
    assert threading.active_count() < 10  # the worker thread was stopped


# ----- health -------------------------------------------------------------------

def test_selftest_reports_missing_camera():
    from dc_health.monitor import Monitor
    m = Monitor(["dc_vision"], say=lambda t: None)
    m.on_status("module/dc_vision/status", "online")
    m.on_camera(topics.VISION_CAMERA, {"state": "missing"})
    result = m.selftest({}, [])
    assert not result["ok"] and "camera not found" in result["problems"]
    m.on_camera(topics.VISION_CAMERA, {"state": "off"})
    assert m.selftest({}, [])["ok"]


def test_health_reads_installed_modules(tmp_path):
    from dc_health.__main__ import BUILT_IN_MODULES, default_modules
    f = tmp_path / "modules.list"
    f.write_text("dc_speech\ndc_vision\n")
    assert default_modules(str(f)) == "dc_speech,dc_vision"
    assert default_modules(str(tmp_path / "none")) == BUILT_IN_MODULES
