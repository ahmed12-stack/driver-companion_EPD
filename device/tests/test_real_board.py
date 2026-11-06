"""The real thing: Mosquitto on a socket file, the modules as separate programs.

Skipped where Mosquitto is not installed. On the Pi: `sudo apt install mosquitto`.
"""

import asyncio
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DEVICE = os.path.dirname(HERE)
sys.path.insert(0, DEVICE)

import pytest  # noqa: E402

from dc_common import topics  # noqa: E402
from dc_common.bus import MqttBus  # noqa: E402

MOSQUITTO = shutil.which("mosquitto") or "/usr/sbin/mosquitto"
pytestmark = pytest.mark.skipif(not os.path.exists(MOSQUITTO), reason="mosquitto not installed")


@pytest.fixture
def board():
    run_dir = tempfile.mkdtemp(prefix="dc-bus-")
    try:  # Mosquitto drops to its own user, which must be able to create the socket
        user = pwd.getpwnam("mosquitto")
        os.chown(run_dir, user.pw_uid, user.pw_gid)
    except KeyError:
        pass
    os.chmod(run_dir, 0o750)
    sock = os.path.join(run_dir, "bus.sock")
    conf_dir = tempfile.mkdtemp(prefix="dc-conf-")
    conf = os.path.join(conf_dir, "m.conf")
    with open(conf, "w") as f:
        f.write(f"listener 0 {sock}\nallow_anonymous true\npersistence false\n")
    os.chmod(conf_dir, 0o755)
    os.chmod(conf, 0o644)
    broker = subprocess.Popen([MOSQUITTO, "-c", conf], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    for _ in range(50):
        if os.path.exists(sock):
            break
        time.sleep(0.1)
    else:
        broker.kill()
        pytest.skip("mosquitto did not start")
    yield sock
    broker.terminate()
    broker.wait(5)
    shutil.rmtree(run_dir, ignore_errors=True)
    shutil.rmtree(conf_dir, ignore_errors=True)


def start(module, sock, data_dir, *extra):
    env = dict(os.environ, PYTHONPATH=DEVICE, DC_BUS=sock, DC_DATA_DIR=data_dir)
    return subprocess.Popen([sys.executable, "-m", module, *extra], cwd=DEVICE, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


class Observer:
    def __init__(self, sock):
        self.bus = MqttBus("test_observer", sock, announce=False)
        self.seen = []
        self.bus.subscribe("#", lambda t, d: self.seen.append((t, d)))

    def on(self, topic):
        return [d for t, d in self.seen if t == topic]

    async def wait_for(self, check, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if check():
                return True
            await asyncio.sleep(0.1)
        return False


def test_two_buses_talk_and_retained_survives(board):
    async def run():
        a, b = MqttBus("a", board), MqttBus("b", board)
        got = []
        b.subscribe("x/#", lambda t, d: got.append((t, d)))
        tasks = [asyncio.create_task(a.run()), asyncio.create_task(b.run())]
        await asyncio.wait_for(b.connected.wait(), 5)  # b must be listening before a speaks
        await a.publish("x/live", {"n": 1})
        await a.publish("x/kept", {"n": 2}, retain=True)
        late = MqttBus("late", board)
        late_got = []
        late.subscribe("x/kept", lambda t, d: late_got.append(d))
        tasks.append(asyncio.create_task(late.run()))
        for _ in range(50):  # wait up to 5 s (a busy machine can be slow)
            if got and late_got:
                break
            await asyncio.sleep(0.1)
        for t in tasks:
            t.cancel()
        return got, late_got
    got, late_got = asyncio.run(run())
    assert ("x/live", {"n": 1}) in got and late_got == [{"n": 2}]


def test_whole_system_fake_drive_selftest_and_crash(board, tmp_path):
    procs = {
        "dc_speech": start("dc_speech", board, str(tmp_path), "--print-speech"),
        "dc_trip": start("dc_trip", board, str(tmp_path), "--llm-bin", "/nonexistent"),
        "dc_speed": start("dc_speed", board, str(tmp_path)),
        "dc_health": start("dc_health", board, str(tmp_path), "--modules", "dc_speech,dc_trip",
                           "--startup-wait", "2", "--restart-grace", "1"),
    }

    async def run():
        obs = Observer(board)
        task = asyncio.create_task(obs.bus.run())
        try:
            # 1. the self-test sees both modules running
            assert await obs.wait_for(lambda: obs.on(topics.SELFTEST)), "no self-test"
            result = obs.on(topics.SELFTEST)[-1]
            assert result["ok"], result
            assert {"text": "Self test passed. System ready.", "priority": "system"} \
                in obs.on(topics.SAY)

            # 2. a fake drive through the board: dc_trip records it and asks to speak
            env = dict(os.environ, PYTHONPATH=DEVICE)
            sim = await asyncio.create_subprocess_exec(
                sys.executable, os.path.join(DEVICE, "tools", "sim_drive.py"),
                "--bus", board, "--speed", "0", "--speed-limit", "50", env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(sim.communicate(), 60)
            assert sim.returncode == 0, out.decode()
            assert await obs.wait_for(lambda: obs.on(topics.TRIP_SAVED)), "no trip saved"
            trip = obs.on(topics.TRIP_SAVED)[0]
            assert 4.5 < trip["km"] < 6.5 and trip["max_kmh"] == 72 and trip["stops"] == 1
            assert trip["overspeed"] >= 1 and trip["overspeed_s"] > 60  # over 55 for minutes
            assert any(m["text"].startswith("Trip complete. You drove") for m in obs.on(topics.SAY))

            # 3. dc_trip crashes: the board publishes its last will, health warns
            procs["dc_trip"].kill()
            assert await obs.wait_for(lambda: "offline" in obs.on(topics.status("dc_trip"))), \
                "no last will"
            assert await obs.wait_for(lambda: any(
                m["text"] == "Warning: the trip logger stopped working."
                for m in obs.on(topics.SAY))), "health did not warn"
            return trip
        finally:
            task.cancel()

    try:
        asyncio.run(run())
    finally:
        for p in procs.values():
            if p.poll() is None:
                p.terminate()
        logs = {name: p.communicate(timeout=10)[0].decode() for name, p in procs.items()}
    # dc_speech really spoke (printed, here) what it was asked to
    assert "[SAY] Self test passed. System ready." in logs["dc_speech"], logs["dc_speech"]
    assert "[SAY] Trip complete. You drove" in logs["dc_speech"]
    assert "[SAY] Slow down. Speed limit 50." in logs["dc_speech"]
    assert "Traceback" not in logs["dc_speech"] + logs["dc_health"], logs


def test_files_written_are_private(board, tmp_path):
    p = start("dc_trip", board, str(tmp_path), "--llm-bin", "/nonexistent")
    try:
        async def run():
            obs = Observer(board)
            task = asyncio.create_task(obs.bus.run())
            await obs.wait_for(lambda: "online" in obs.on(topics.status("dc_trip")), 15)
            await obs.bus.publish(topics.IGNITION, {"on": True}, retain=True)
            await asyncio.sleep(1)
            task.cancel()
        asyncio.run(run())
    finally:
        p.terminate()
        p.wait(10)
    cur = tmp_path / "trip_current.json"
    assert cur.exists() and oct(cur.stat().st_mode & 0o777) == "0o600"
    assert json.loads(cur.read_text())["km"] == 0.0


def test_sign_reader_as_its_own_program(board, tmp_path):
    """dc_vision as a real service: reads a sign from a test video, the speech
    module (another program) speaks it."""
    vision_dir = os.path.join(DEVICE, "..", "vision")
    if not os.path.isdir(os.path.join(vision_dir, "pipeline")):
        pytest.skip("vision folder not next to device/")
    from test_vision import make_video
    video = tmp_path / "drive.avi"
    make_video(video, frames=60)
    speech = start("dc_speech", board, str(tmp_path), "--print-speech")
    vision = start("dc_vision", board, str(tmp_path), "--vision-dir", vision_dir, "--source", str(video),
                   "--model", os.path.join(vision_dir, "models", "sign_cnn.npz"), "--always-on")
    try:
        async def run():
            obs = Observer(board)
            task = asyncio.create_task(obs.bus.run())
            ok = await obs.wait_for(lambda: obs.on(topics.SPEED_LIMIT), 30)
            await asyncio.sleep(1)
            task.cancel()
            return ok, obs
        ok, obs = asyncio.run(run())
        assert ok, "no speed limit published"
        assert obs.on(topics.SPEED_LIMIT)[-1]["kmh"] == 50
    finally:
        for p in (vision, speech):
            p.terminate()
        logs = {n: p.communicate(timeout=15)[0].decode() for n, p in (("vision", vision), ("speech", speech))}
    assert "[SAY] Speed limit 50." in logs["speech"], logs
    assert "Traceback" not in logs["vision"], logs["vision"]
