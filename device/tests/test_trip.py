import asyncio
import os
import stat
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from dc_common import topics  # noqa: E402
from dc_common.bus import MemoryBroker, MemoryBus  # noqa: E402
from dc_trip import simulate  # noqa: E402
from dc_trip.__main__ import make_service  # noqa: E402
from dc_trip.summary import (Summarizer, clean, is_good_remark, remark_prompt,  # noqa: E402
                            template_summary)
from dc_trip.trip import MAX_TRIPS, TripLogger, TripStore, haversine_km  # noqa: E402

T0 = 1_793_000_000.0
KM_PER_DEG_LAT = 111.195


def drive(logger, segments, lat=20.0, lon=85.0, ts=T0):
    """segments: list of (seconds, kmh). Drives north at a steady speed."""
    closed = []
    for seconds, kmh in segments:
        for _ in range(seconds):
            lat += kmh / 3600 / KM_PER_DEG_LAT
            ts += 1
            c = logger.add_fix(lat, lon, kmh, 5.0, ts)
            if c:
                closed.append(c)
    return lat, ts, closed


def test_haversine():
    assert haversine_km(20, 85, 21, 85) == pytest.approx(111.2, abs=0.1)


def test_distance_time_speed_and_stops(tmp_path):
    lg = TripLogger(str(tmp_path / "cur.json"))
    lg.start()
    # 10 s idle, 6 min at 60 (6 km), 40 s traffic stop, 3 min at 40 (2 km), 30 s parked
    drive(lg, [(10, 0), (360, 60), (40, 0), (180, 40), (30, 0)])
    trip = lg.finish()
    assert trip["km"] == pytest.approx(8.0, abs=0.1)
    assert trip["max_kmh"] == 60
    assert trip["stops"] == 1          # the traffic stop, not the final parking
    assert trip["moving_min"] == 9
    assert trip["min"] == 10
    assert trip["avg_kmh"] == pytest.approx(53, abs=1)
    assert trip["ended"] == "ignition"
    assert not os.path.exists(tmp_path / "cur.json")


def test_short_trip_dropped(tmp_path):
    lg = TripLogger(str(tmp_path / "cur.json"))
    lg.start()
    drive(lg, [(30, 0), (5, 10)])
    assert lg.finish() is None


def test_bad_fixes_ignored(tmp_path):
    lg = TripLogger(str(tmp_path / "cur.json"))
    lg.start()
    lat, ts, _ = drive(lg, [(60, 50)])
    lg.add_fix(lat + 1, 85.0, 50, 5.0, ts + 1)      # 111 km jump in a second
    lg.add_fix(lat, 85.0, 50, 500.0, ts + 2)        # poor accuracy
    lg.add_fix(lat, 85.0, 50, 5.0, ts - 10)         # out of order
    trip = lg.finish()
    assert trip["km"] == pytest.approx(0.8, abs=0.05)


def test_checkpoint_and_resume_after_restart(tmp_path):
    path = str(tmp_path / "cur.json")
    lg = TripLogger(path)
    lg.start()
    lat, ts, _ = drive(lg, [(120, 60)])
    lg.checkpoint(force=True)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    # program restarts mid-drive
    lg2 = TripLogger(path)
    assert lg2.pending is not None
    lg2.start()
    _, _, closed = drive(lg2, [(120, 60)], lat=lat, ts=ts)
    assert closed == []
    assert lg2.finish()["km"] == pytest.approx(4.0, abs=0.1)


def test_long_gap_after_restart_splits_trip(tmp_path):
    path = str(tmp_path / "cur.json")
    lg = TripLogger(path)
    lg.start()
    lat, ts, _ = drive(lg, [(120, 60)])
    lg.checkpoint(force=True)
    lg2 = TripLogger(path)
    lg2.start()
    _, _, closed = drive(lg2, [(60, 60)], lat=lat, ts=ts + 3600)  # car started an hour later
    assert len(closed) == 1 and closed[0]["ended"] == "power_loss"
    assert closed[0]["km"] == pytest.approx(2.0, abs=0.1)
    assert lg2.finish()["km"] == pytest.approx(1.0, abs=0.1)


def test_close_pending_when_ignition_stays_off(tmp_path):
    path = str(tmp_path / "cur.json")
    lg = TripLogger(path)
    lg.start()
    drive(lg, [(120, 60)])
    lg.checkpoint(force=True)
    lg2 = TripLogger(path)
    trip = lg2.close_pending()
    assert trip["ended"] == "power_loss" and trip["km"] == pytest.approx(2.0, abs=0.1)
    assert not os.path.exists(path)


def test_events(tmp_path):
    lg = TripLogger(str(tmp_path / "cur.json"))
    lg.start()
    drive(lg, [(60, 50)])
    lg.record("overspeed", 12)
    lg.record("overspeed", 4)
    lg.record("harsh_brake")
    lg.record("nonsense")
    t = lg.finish()
    assert (t["overspeed"], t["overspeed_s"], t["harsh_brake"]) == (2, 16, 1)


def test_store_keeps_last_20_and_sync_flags(tmp_path):
    path = str(tmp_path / "trips.json")
    s = TripStore(path)
    for i in range(25):
        s.add({"id": str(i), "km": 1.0})
    assert len(s.trips) == MAX_TRIPS and s.trips[0]["id"] == "5"
    assert s.mark_synced("24") and not s.mark_synced("24")
    s2 = TripStore(path)
    assert len(s2.unsynced()) == 19
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


# ----- summaries --------------------------------------------------------

TRIP = {"id": "1", "km": 12.4, "min": 23, "moving_min": 19, "max_kmh": 58, "avg_kmh": 39,
        "stops": 3, "overspeed": 2, "overspeed_s": 20, "harsh_brake": 1, "harsh_accel": 0,
        "signs": 0, "ended": "ignition"}


def test_facts_sentence():
    assert template_summary(TRIP) == (
        "Trip complete. You drove 12.4 kilometres in 23 minutes, with a top speed of "
        "58 kilometres per hour. You stopped 3 times, had 2 overspeed warnings and "
        "braked hard 1 time.")
    calm = {**TRIP, "stops": 0, "overspeed": 0, "harsh_brake": 0}
    assert template_summary(calm).endswith("58 kilometres per hour.")


def test_remark_prompt_has_no_numbers():
    text = remark_prompt(TRIP)
    assert text == ("The trip has ended. It was a medium-length trip. "
                    "There were overspeed warnings and harsh braking.")
    assert not any(c.isdigit() for c in text)
    calm = {**TRIP, "min": 8, "stops": 0, "overspeed": 0, "harsh_brake": 0}
    assert remark_prompt(calm) == "The trip has ended. It was a short trip. Nothing unusual happened."
    busy = {**calm, "min": 50, "stops": 7}
    assert remark_prompt(busy) == "The trip has ended. It was a long trip, with heavy traffic and many stops."
    assert remark_prompt({**calm, "harsh_brake": 1}).endswith("There was harsh braking.")


def test_examples_pass_the_check():
    from dc_trip.summary import EXAMPLES
    warned = {**TRIP, "stops": 7}
    calm = {**TRIP, "stops": 0, "overspeed": 0, "harsh_brake": 0}
    for i, (_, answer) in enumerate(EXAMPLES):
        assert is_good_remark(answer, calm if i == 3 else warned), answer


def test_echoed_input_rejected():
    assert not is_good_remark("The trip has ended, nothing unusual happened.", CALM)


CALM = {**TRIP, "stops": 1, "overspeed": 0, "harsh_brake": 0}


@pytest.mark.parametrize("text,trip,ok", [
    ("Ease off a little on the open stretches next time.", TRIP, True),
    ("Try leaving a bit more room so you can brake gently.", TRIP, True),
    ("A steady, relaxed drive. Enjoy the rest of your day.", CALM, True),
    ("Nice, smooth drive today!", CALM, True),
    # numbers in any form
    ("You had 2 warnings, slow down.", TRIP, False),
    ("Two warnings today, so take it easy.", TRIP, False),
    ("You arrived at noon.", CALM, False),
    # praise after warnings, or a warning tone when nothing went wrong
    ("Well done on a smooth drive!", TRIP, False),
    ("Ease off a little on the open stretches next time.", CALM, False),
    # talking about what was not measured
    ("You stayed within the limit the whole way.", CALM, False),
    ("No harsh braking at all, lovely.", CALM, False),
    # third person, too long, junk
    ("The driver drove carefully.", CALM, False),
    # traffic only when there really were many stops
    ("Thanks for staying patient through all that traffic.", CALM, False),
    ("Thanks for staying patient through all that traffic.", {**CALM, "stops": 7}, True),
    ("Ease off the throttle, and I'll make sure to keep an eye on the speed.", TRIP, False),
    ("We had a lovely drive.", CALM, False),
    ("You " + "really " * 25 + "drove.", CALM, False),
    ("", CALM, False),
    ("Remark: drive safe", CALM, False),
])
def test_remark_check(text, trip, ok):
    assert is_good_remark(text, trip) is ok


def test_real_pi_outputs_are_rejected():
    # what the Pi's 0.5B model wrote when asked for a whole summary
    for text in ["Driver travels 12.4 km in 23 minutes. Harsh braking occurs 2 times.",
                 "The driver drove for 47 minutes, covering 31.8 km. They reached their "
                 "destination at 12:00 noon."]:
        assert not is_good_remark(text, TRIP)


def test_clean_stops_at_end_marker():
    assert clean("Drive gently.<|im_end|>\n<|im_start|>user\nmore") == "Drive gently."


def test_clean():
    raw = ('0.03.296.787 I \n"ease off a little next time" [end of text]\n'
           '0.13.757.369 I common_perf_print:    sampling time =  49.72 ms\n> \n')
    assert clean(raw) == "Ease off a little next time."


def fake_llm(tmp_path, body):
    exe = tmp_path / "llama-completion"
    exe.write_text("#!/bin/sh\n" + body + "\n")
    exe.chmod(0o755)
    model = tmp_path / "model.gguf"
    model.write_text("x")
    return str(exe), str(model)


def test_summarizer_adds_good_remark(tmp_path):
    exe, model = fake_llm(tmp_path, 'echo "Ease off a little on the open stretches next time."')
    text, by = asyncio.run(Summarizer(exe, model).summarize(TRIP))
    assert by == "llm"
    assert text == template_summary(TRIP) + " Ease off a little on the open stretches next time."


def test_summarizer_drops_bad_remark(tmp_path):
    exe, model = fake_llm(tmp_path, 'echo "You drove 99 km, well done."')
    text, by = asyncio.run(Summarizer(exe, model).summarize(TRIP))
    assert (text, by) == (template_summary(TRIP), "template")


def test_summarizer_falls_back_on_timeout(tmp_path):
    exe, model = fake_llm(tmp_path, "sleep 5")
    text, by = asyncio.run(Summarizer(exe, model, timeout_s=0.3).summarize(TRIP))
    assert by == "template"


def test_summarizer_without_llm(tmp_path):
    s = Summarizer(str(tmp_path / "missing"), str(tmp_path / "missing.gguf"))
    assert not s.available
    assert asyncio.run(s.summarize(TRIP))[1] == "template"


def test_summarizer_sends_no_numbers_to_llm(tmp_path):
    exe, model = fake_llm(tmp_path, 'for a; do last="$a"; done; echo "$last" > "$(dirname "$0")/prompt.txt"; echo "Drive gently."')
    asyncio.run(Summarizer(exe, model).summarize(TRIP))
    prompt = (tmp_path / "prompt.txt").read_text()
    assert prompt.startswith("<|im_start|>system\n")
    assert prompt.count("<|im_start|>user\n") == 5          # 4 examples + the real trip
    user = prompt.split("<|im_start|>user\n")[-1].split("<|im_end|>")[0]
    assert user.startswith("The trip has ended. It was a medium-length trip")
    assert not any(c.isdigit() for c in user)
    assert prompt.endswith("<|im_start|>assistant\n\n")      # echo adds the last newline


# ----- service, sync and simulation --------------------------------------


# ----- the service, through the real module wiring -------------------------

class TemplateOnly:
    available = False

    async def remark(self, trip):
        return None


class FixedRemark:
    available = True

    async def remark(self, trip):
        return "Ease off a little next time."


def board(tmp_path, summarizer, **kw):
    """dc_trip wired to an in-memory board, plus a 'sim' bus to drive it."""
    broker = MemoryBroker()
    trip_bus = MemoryBus("dc_trip", broker)
    sim = MemoryBus("dc_sim", broker)
    service = make_service(trip_bus, str(tmp_path), summarizer, **kw)
    return broker, sim, service


async def drive_through_board(sim):
    await simulate.fake_drive(
        lambda on: sim.post(topics.IGNITION, {"on": on}, retain=True),
        lambda lat, lon, kmh, acc, ts: sim.post(
            topics.GPS_FIX, {"lat": lat, "lon": lon, "kmh": kmh, "acc": acc, "ts": ts,
                             "source": "sim"}),
        speed=0)
    for _ in range(20):
        await asyncio.sleep(0)


def said(broker):
    return [d["text"] for t, d in broker.log if t == topics.SAY]


def test_fake_drive_through_the_board(tmp_path):
    async def run():
        broker, sim, service = board(tmp_path, TemplateOnly())
        await drive_through_board(sim)
        await service.wait_idle()
        await asyncio.sleep(0)
        return broker, service

    broker, service = asyncio.run(run())
    saved = [d for t, d in broker.log if t == topics.TRIP_SAVED]
    assert len(saved) == 1
    t = saved[0]
    assert 4.5 < t["km"] < 6.5 and t["max_kmh"] == 72 and t["stops"] == 1 and t["min"] == 10
    assert t["summary_by"] == "template" and t["summary"] == template_summary(t)
    assert said(broker) == [t["summary"]]
    assert service.store.trips[0]["id"] == t["id"]          # kept in the history too


def test_facts_spoken_first_then_remark(tmp_path):
    async def run():
        broker, sim, service = board(tmp_path, FixedRemark())
        await drive_through_board(sim)
        await service.wait_idle()
        await asyncio.sleep(0)
        return broker

    broker = asyncio.run(run())
    trip = [d for t, d in broker.log if t == topics.TRIP_SAVED][0]
    assert said(broker) == [template_summary(trip), "Ease off a little next time."]
    assert trip["summary"] == template_summary(trip) + " Ease off a little next time."
    assert trip["summary_by"] == "llm"


def test_gps_fix_and_events_from_the_board(tmp_path):
    async def run():
        broker, sim, service = board(tmp_path, TemplateOnly())
        await sim.publish(topics.IGNITION, {"on": True}, retain=True)
        lat = 20.0
        for i in range(120):
            lat += 60 / 3600 / KM_PER_DEG_LAT
            await sim.publish(topics.GPS_FIX, {"lat": lat, "lon": 85.0, "kmh": 60, "acc": 4,
                                               "ts": T0 + i, "source": "phone"})
        await sim.publish(topics.GPS_FIX, {"lat": "x"})                    # malformed: ignored
        await sim.publish(topics.TRIP_EVENT, {"event": "overspeed", "seconds": 7})
        await sim.publish(topics.TRIP_EVENT, {"event": "nonsense"})        # ignored
        await sim.publish(topics.IGNITION, {"on": False}, retain=True)
        await service.wait_idle()
        await asyncio.sleep(0)
        return service

    service = asyncio.run(run())
    t = service.store.trips[0]
    assert t["km"] == pytest.approx(2.0, abs=0.1)
    assert t["start"] == int(T0)                  # phone time, not the Pi's clock
    assert (t["overspeed"], t["overspeed_s"]) == (1, 7)


def test_halt_after_ignition_off(tmp_path):
    halted = []

    async def halt():
        halted.append(True)

    async def run():
        broker, sim, service = board(tmp_path, TemplateOnly(), halt_after_min=0.001, halt=halt)
        for on in (True, False):
            await sim.publish(topics.IGNITION, {"on": on}, retain=True)
        await asyncio.sleep(0.2)
        for on in (True, False, True):            # back on before the timer: no halt
            await sim.publish(topics.IGNITION, {"on": on}, retain=True)
        await asyncio.sleep(0.2)

    asyncio.run(run())
    assert halted == [True]

def fake_llm_with_help(tmp_path, help_text, reply):
    """A fake llama-completion: prints help_text for --help, else records its
    arguments and prints reply (or nothing if the cache file is 'damaged')."""
    exe = tmp_path / "llama-completion"
    exe.write_text(f"""#!/bin/sh
if [ "$1" = "--help" ]; then echo "{help_text}"; exit 0; fi
echo "CALL $*" >> "$(dirname "$0")/calls.txt"
case "$*" in *--prompt-cache*) [ -f "$(dirname "$0")/damaged" ] && exit 0 ;; esac
echo "{reply}"
""")
    exe.chmod(0o755)
    (tmp_path / "model.gguf").write_text("x")
    return str(exe), str(tmp_path / "model.gguf")


def test_prompt_cache_used_when_supported(tmp_path):
    exe, model = fake_llm_with_help(tmp_path, "  --prompt-cache FNAME   file to cache", "Drive gently.")
    s = Summarizer(exe, model, cache_path=str(tmp_path / "c.cache"))
    assert asyncio.run(s.remark(CALM)) == "Drive gently."
    assert "--prompt-cache" in (tmp_path / "calls.txt").read_text()


def test_prompt_cache_not_passed_when_unsupported(tmp_path):
    exe, model = fake_llm_with_help(tmp_path, "usage: llama-completion [options]", "Drive gently.")
    s = Summarizer(exe, model, cache_path=str(tmp_path / "c.cache"))
    assert asyncio.run(s.remark(CALM)) == "Drive gently."
    assert "--prompt-cache" not in (tmp_path / "calls.txt").read_text()


def test_damaged_cache_is_deleted_and_retried(tmp_path):
    exe, model = fake_llm_with_help(tmp_path, "--prompt-cache", "Drive gently.")
    cache = tmp_path / "c.cache"
    cache.write_text("garbage")
    (tmp_path / "damaged").write_text("")
    s = Summarizer(exe, model, cache_path=str(cache))
    assert asyncio.run(s.remark(CALM)) == "Drive gently."
    assert not cache.exists()
    calls = (tmp_path / "calls.txt").read_text().split("CALL ")[1:]
    assert len(calls) == 2 and "--prompt-cache" in calls[0] and "--prompt-cache" not in calls[1]


def test_retries_until_a_remark_passes(tmp_path):
    exe = tmp_path / "llama-completion"
    exe.write_text("""#!/bin/sh
[ "$1" = "--help" ] && exit 0
n=$(cat "$(dirname "$0")/n" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$(dirname "$0")/n"
if [ $n -lt 3 ]; then echo "I'll keep an eye on our speed."; else echo "Drive gently out there."; fi
""")
    exe.chmod(0o755)
    (tmp_path / "m.gguf").write_text("x")
    s = Summarizer(str(exe), str(tmp_path / "m.gguf"))
    assert asyncio.run(s.remark(CALM)) == "Drive gently out there."
    assert [ok for _, ok in s.last_attempts] == [False, False, True]


def test_gives_up_after_three_tries(tmp_path):
    exe, model = fake_llm(tmp_path, 'echo "We had fun."')
    s = Summarizer(exe, model)
    assert asyncio.run(s.remark(CALM)) is None
    assert len(s.last_attempts) == 3


def test_no_new_try_after_the_time_budget(tmp_path):
    exe, model = fake_llm(tmp_path, 'sleep 0.3; echo "We had fun."')
    s = Summarizer(exe, model)
    assert asyncio.run(s.remark(CALM, budget_s=0.1)) is None
    assert len(s.last_attempts) == 1


# ----- route -----------------------------------------------------------------

from dc_trip import route as route_path  # noqa: E402
from dc_phone.framing import MAX_MESSAGE_SIZE  # noqa: E402


def test_polyline_known_value_and_round_trip():
    # Google's documented example
    pts = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert route_path.encode(pts) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    assert route_path.decode(route_path.encode(pts)) == pts


def test_route_recorded_and_ends_where_parked(tmp_path):
    lg = TripLogger(str(tmp_path / "cur.json"))
    lg.start()
    lat, ts, _ = drive(lg, [(600, 36)])  # 6 km north
    trip = lg.finish()
    pts = route_path.decode(trip["route"])
    assert pts[0][0] == pytest.approx(20.0, abs=1e-4)
    assert pts[-1][0] == pytest.approx(lat, abs=1e-5)
    assert 150 <= len(pts) <= 300          # about one point every 25 m
    assert all(p[1] == 85.0 for p in pts)


def test_route_survives_a_restart(tmp_path):
    path = str(tmp_path / "cur.json")
    lg = TripLogger(path)
    lg.start()
    drive(lg, [(120, 36)])
    lg.checkpoint(force=True)
    lg2 = TripLogger(path)
    lg2.start()
    assert len(lg2.current["route"]) >= 30  # 1.2 km, a point every 30 m


def test_long_trip_route_stays_small(tmp_path):
    lg = TripLogger(str(tmp_path / "cur.json"))
    lg.start()
    drive(lg, [(3 * 3600, 100)])  # 300 km
    trip = lg.finish()
    pts = route_path.decode(trip["route"])
    assert len(pts) <= route_path.ROUTE_MAX_POINTS + 1
    import json
    size = len(json.dumps({"t": "trip", **trip}).encode())
    assert size < 4000 < MAX_MESSAGE_SIZE


def test_route_skips_gps_jumps(tmp_path):
    lg = TripLogger(str(tmp_path / "cur.json"))
    lg.start()
    lat, ts, _ = drive(lg, [(60, 36)])
    lg.add_fix(lat + 1.0, 85.0, 36, 5.0, ts + 1)   # 111 km in a second
    drive(lg, [(60, 36)], lat=lat, ts=ts + 2)
    pts = [p for p in route_path.decode(lg.finish()["route"])]
    assert max(p[0] for p in pts) < 20.1


def test_old_checkpoint_without_route_still_works(tmp_path):
    lg = TripLogger(str(tmp_path / "cur.json"))
    lg.start()
    del lg.current["route"], lg.current["route_step_m"]
    drive(lg, [(120, 36)])
    assert route_path.decode(lg.finish()["route"])


def test_too_short_path_sends_no_route(tmp_path):
    lg = TripLogger(str(tmp_path / "cur.json"))
    lg.start()
    lg.current.update(first_ts=T0, last_ts=T0 + 60, km=0.5)
    trip = lg.finish()
    assert trip["route"] == ""
