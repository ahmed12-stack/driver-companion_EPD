"""Trip logger: saves what happened on each trip into a small SQLite database
that the phone app (app_server.py) reads.

What is recorded (requirement in brackets):
  - every confirmed sign                                   (REQ-25: signs announced)
  - every overspeed episode: limit, peak speed, how long    (REQ-05, REQ-25)
  - harsh braking / acceleration                           (REQ-07; simulated from
    speed here, the real device uses the MPU6050 IMU)
  - camera blocked / resumed                               (REQ-18)
  - per-trip totals: distance, max / average speed, time over the limit
  - a speed sample every second (for the speed chart in the app)
  - a short text trip summary                              (REQ-08; a template is saved
    at once, then llm.py replaces it with a fact-checked LLM summary if a model is running)
Only the last 20 trips are kept, and no images are ever stored (REQ-11).
"""
import sqlite3
import threading
import time
from collections import deque

KEEP_TRIPS = 20
HARSH_KMH_PER_S = 0.35 * 9.81 * 3.6      # 0.35 g = 12.4 km/h per second (REQ-07)
HARSH_WINDOW_S = 0.3                      # must last at least 300 ms
HARSH_COOLDOWN_S = 3.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    ended_at REAL,
    distance_km REAL DEFAULT 0,
    max_speed REAL DEFAULT 0,
    avg_speed REAL DEFAULT 0,
    over_s REAL DEFAULT 0,
    overspeed_count INTEGER DEFAULT 0,
    warnings INTEGER DEFAULT 0,
    harsh_brakes INTEGER DEFAULT 0,
    harsh_accels INTEGER DEFAULT 0,
    signs INTEGER DEFAULT 0,
    summary TEXT,
    summary_source TEXT DEFAULT 'template',   -- template | llm
    summary_verified INTEGER DEFAULT 0        -- 1 = every number checked against the data
);
CREATE TABLE IF NOT EXISTS samples (
    trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    t REAL NOT NULL,
    speed REAL,
    limit_kmh INTEGER
);
CREATE INDEX IF NOT EXISTS samples_trip ON samples(trip_id, t);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    t REAL NOT NULL,
    kind TEXT NOT NULL,          -- sign | overspeed | warning | harsh_brake | harsh_accel | camera | limit_expired
    label TEXT,
    speed REAL,
    limit_kmh INTEGER,
    duration_s REAL
);
CREATE INDEX IF NOT EXISTS events_trip ON events(trip_id, t);
"""


def connect(path):
    db = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")      # app can read while the demo writes
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(SCHEMA)
    for col, ddl in (("summary_source", "TEXT DEFAULT 'template'"),
                     ("summary_verified", "INTEGER DEFAULT 0")):
        try:                                    # upgrade a database from the older version
            db.execute(f"ALTER TABLE trips ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError:
            pass
    return db


def summary_from(distance_km, secs, avg, max_speed, overspeed_count, over_s, worst,
                 harsh_brakes, harsh_accels, signs, interrupted=False):
    """The plain template summary (used when no language model is running)."""
    took = f"{secs:.0f} s" if secs < 60 else f"{secs / 60:.0f} min"
    parts = [f"You drove {distance_km:.1f} km in {took}, "
             f"average {avg:.0f} km/h, top speed {max_speed:.0f} km/h."]
    if overspeed_count:
        s = "s" if overspeed_count != 1 else ""
        parts.append(f"You went over the limit {overspeed_count} time{s}, {over_s:.0f} s in total" +
                     (f"; the worst was {worst['speed']:.0f} km/h in a {worst['limit_kmh']} zone."
                      if worst else "."))
    else:
        parts.append("You stayed within the speed limit the whole trip.")
    if harsh_brakes + harsh_accels:
        parts.append(f"{harsh_brakes} harsh braking and {harsh_accels} harsh acceleration events.")
    if signs:
        parts.append(f"{signs} road signs were announced.")
    if interrupted:
        parts.append("The recording stopped unexpectedly, so the last few seconds may be missing.")
    return " ".join(parts)


class TripLog:
    def __init__(self, path, margin_kmh=5.0, hold_s=3.0, clock=time.time):
        self.db_path = path
        self.db = connect(path)
        self.lock = threading.Lock()
        self.margin, self.hold, self.clock = margin_kmh, hold_s, clock
        self.trip_id = None
        self.recovered = self.close_unfinished()

    def close_unfinished(self):
        """A trip that never ended (power cut, window closed, crash) is closed
        at its last recorded moment and given a summary (REQ-23)."""
        rows = self.db.execute("SELECT * FROM trips WHERE ended_at IS NULL").fetchall()
        for r in rows:
            last = self.db.execute(
                "SELECT MAX(t) FROM (SELECT t FROM samples WHERE trip_id=:i "
                "UNION ALL SELECT t FROM events WHERE trip_id=:i)", {"i": r["id"]}).fetchone()[0]
            end = last or r["started_at"]
            worst = self.db.execute(
                "SELECT speed, limit_kmh FROM events WHERE trip_id=? AND kind='overspeed' "
                "ORDER BY (speed - limit_kmh) DESC LIMIT 1", (r["id"],)).fetchone()
            text = summary_from(r["distance_km"], end - r["started_at"], r["avg_speed"],
                                r["max_speed"], r["overspeed_count"], r["over_s"], worst,
                                r["harsh_brakes"], r["harsh_accels"], r["signs"], interrupted=True)
            with self.lock:
                self.db.execute("UPDATE trips SET ended_at=?, summary=?, summary_source='template' "
                                "WHERE id=?", (end, text, r["id"]))
                self.db.commit()
        return [r["id"] for r in rows]

    # ------------------------------------------------------------------ trip
    def start_trip(self, t=None):
        t = self.clock() if t is None else t
        with self.lock:
            cur = self.db.execute("INSERT INTO trips (started_at) VALUES (?)", (t,))
            self.db.commit()
            self.trip_id = cur.lastrowid
        self._t = None
        self.distance_km = 0.0
        self.moving_s = 0.0
        self.max_speed = 0.0
        self.over_s = 0.0
        self.overspeed_count = self.warnings = self.signs = 0
        self.harsh_brakes = self.harsh_accels = 0
        self.episode = None                # current overspeed episode
        self.history = deque()             # (t, speed) for harsh detection
        self.last_harsh = -1e9
        self.last_flush = 0.0
        self.last_sample = -1e9
        self.sign_names = []
        return self.trip_id

    def end_trip(self, t=None):
        if self.trip_id is None:
            return None
        t = self.clock() if t is None else t
        self._close_episode(t)
        summary = self.summary_text(t)
        self._flush(t, ended=True, summary=summary)
        trip_id, self.trip_id = self.trip_id, None
        self._prune()
        return {"trip_id": trip_id, "summary": summary}

    # ------------------------------------------------------------------ events
    def _event(self, kind, t, label=None, speed=None, limit=None, duration=None):
        with self.lock:
            self.db.execute(
                "INSERT INTO events (trip_id, t, kind, label, speed, limit_kmh, duration_s) "
                "VALUES (?,?,?,?,?,?,?)", (self.trip_id, t, kind, label, speed, limit, duration))
            self.db.commit()

    def log_sign(self, label, t=None):
        t = self.clock() if t is None else t
        self.signs += 1
        self.sign_names.append(label)
        self._event("sign", t, label=label)

    def log_warning(self, speed, limit, t=None):
        t = self.clock() if t is None else t
        self.warnings += 1
        self._event("warning", t, speed=speed, limit=limit)

    def log_camera(self, state, t=None):
        t = self.clock() if t is None else t
        self._event("camera", t, label=state)

    def log_limit_expired(self, limit, t=None):
        t = self.clock() if t is None else t
        self._event("limit_expired", t, limit=limit)

    # ------------------------------------------------------------------ speed
    def update(self, speed, limit, t=None):
        """Call every frame with the current speed and active limit (or None)."""
        t = self.clock() if t is None else t
        if self._t is not None:
            dt = max(0.0, t - self._t)
            self.distance_km += speed * dt / 3600.0
            if speed > 0:
                self.moving_s += dt
            if self.episode is not None:
                self.over_s += dt
        self._t = t
        self.max_speed = max(self.max_speed, speed)

        # overspeed episode: from going over limit+margin until back under it
        over = limit is not None and speed > limit + self.margin
        if over and self.episode is None:
            self.episode = {"start": t, "limit": limit, "peak": speed}
        elif over:
            self.episode["peak"] = max(self.episode["peak"], speed)
            if limit != self.episode["limit"]:        # limit changed mid-episode
                self._close_episode(t)
                self.episode = {"start": t, "limit": limit, "peak": speed}
        elif self.episode is not None:
            self._close_episode(t)

        self._harsh(speed, t)
        if t - self.last_sample >= 1.0:                # 1 Hz speed trace for the chart
            self.last_sample = t
            with self.lock:
                self.db.execute("INSERT INTO samples (trip_id, t, speed, limit_kmh) VALUES (?,?,?,?)",
                                (self.trip_id, t, speed, limit))
        if t - self.last_flush >= 2.0:                 # live numbers for the app
            self._flush(t)

    def _close_episode(self, t):
        ep, self.episode = self.episode, None
        if ep is None:
            return
        dur = t - ep["start"]
        if dur >= self.hold:                           # same 3 s rule as the warning
            self.overspeed_count += 1
            with self.lock:
                self.db.execute(
                    "INSERT INTO events (trip_id, t, kind, label, speed, limit_kmh, duration_s) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (self.trip_id, ep["start"], "overspeed", None, ep["peak"], ep["limit"], dur))
                self.db.commit()

    def _harsh(self, speed, t):
        self.history.append((t, speed))
        while self.history and t - self.history[0][0] > HARSH_WINDOW_S + 0.2:
            self.history.popleft()
        if t - self.last_harsh < HARSH_COOLDOWN_S:
            return
        # compare with the sample at least 300 ms old
        old = [(tt, v) for tt, v in self.history if t - tt >= HARSH_WINDOW_S]
        if not old:
            return
        t0, v0 = old[-1]
        rate = (speed - v0) / (t - t0)                 # km/h per second
        if rate <= -HARSH_KMH_PER_S:
            self.harsh_brakes += 1
            self.last_harsh = t
            self._event("harsh_brake", t, speed=speed, label=f"{v0:.0f}->{speed:.0f} km/h")
        elif rate >= HARSH_KMH_PER_S:
            self.harsh_accels += 1
            self.last_harsh = t
            self._event("harsh_accel", t, speed=speed, label=f"{v0:.0f}->{speed:.0f} km/h")

    # ------------------------------------------------------------------ summary
    def summary_text(self, t):
        avg = self.distance_km / (self.moving_s / 3600.0) if self.moving_s > 0 else 0.0
        return summary_from(self.distance_km, max(0.0, t - self._started()), avg, self.max_speed,
                            self.overspeed_count, self.over_s, self._worst_overspeed(),
                            self.harsh_brakes, self.harsh_accels, self.signs)

    def _started(self):
        row = self.db.execute("SELECT started_at FROM trips WHERE id=?", (self.trip_id,)).fetchone()
        return row["started_at"]

    def _worst_overspeed(self):
        return self.db.execute(
            "SELECT speed, limit_kmh FROM events WHERE trip_id=? AND kind='overspeed' "
            "ORDER BY (speed - limit_kmh) DESC LIMIT 1", (self.trip_id,)).fetchone()

    def _flush(self, t, ended=False, summary=None):
        self.last_flush = t
        avg = self.distance_km / (self.moving_s / 3600.0) if self.moving_s > 0 else 0.0
        with self.lock:
            self.db.execute(
                "UPDATE trips SET distance_km=?, max_speed=?, avg_speed=?, over_s=?, "
                "overspeed_count=?, warnings=?, harsh_brakes=?, harsh_accels=?, signs=?"
                + (", ended_at=?, summary=?" if ended else "") + " WHERE id=?",
                (self.distance_km, self.max_speed, avg, self.over_s, self.overspeed_count,
                 self.warnings, self.harsh_brakes, self.harsh_accels, self.signs)
                + ((t, summary) if ended else ()) + (self.trip_id,))
            self.db.commit()

    def set_summary(self, trip_id, text, source, verified):
        with self.lock:
            self.db.execute("UPDATE trips SET summary=?, summary_source=?, summary_verified=? "
                            "WHERE id=?", (text, source, int(verified), trip_id))
            self.db.commit()

    def _prune(self):
        with self.lock:
            self.db.execute(
                "DELETE FROM trips WHERE id NOT IN "
                "(SELECT id FROM trips ORDER BY started_at DESC LIMIT ?)", (KEEP_TRIPS,))
            self.db.commit()
