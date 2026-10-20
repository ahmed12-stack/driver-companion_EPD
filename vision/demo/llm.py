"""Reflective layer: an on-device language model that
  1. writes the spoken trip summary when a trip ends (REQ-08), and
  2. answers the driver's questions about their trips in the app ("Ask").

How it stays honest
-------------------
A small model (0.5-1.5 billion parameters) can invent numbers. So the model is
never asked to compute anything: Python pulls the facts out of the trip
database first and hands them over as a short list. After the model writes, every number in
its text is checked against those facts. If any number is not in the facts,
the text is rejected, the model gets one more try at temperature 0, and if
that also fails the plain template summary is used. The app marks LLM text
as "verified" only when it passed this check.

Where the model runs
--------------------
Any server that speaks the standard OpenAI "chat completions" API, on the
same machine, no internet:
  laptop:  Ollama          ->  http://127.0.0.1:11434/v1   (default)
  Pi:      llama.cpp's llama-server  ->  http://127.0.0.1:8080/v1
Change with the environment variables DC_LLM_URL and DC_LLM_MODEL.
If no server is running, everything still works with the template text.
"""
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request

DEFAULT_URL = os.environ.get("DC_LLM_URL", "http://127.0.0.1:11434/v1")
DEFAULT_MODEL = os.environ.get("DC_LLM_MODEL", "qwen2.5:1.5b")

NUM = re.compile(r"\d+(?:\.\d+)?")
WORD_NUMS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "ten": 10, "once": 1, "twice": 2}


# ----------------------------------------------------------------- client
class LocalLLM:
    def __init__(self, url=DEFAULT_URL, model=DEFAULT_MODEL, timeout=60):
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout

    def available(self):
        try:
            with urllib.request.urlopen(self.url + "/models", timeout=2) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def chat(self, system, user, temperature=0.3, max_tokens=160):
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }).encode()
        req = urllib.request.Request(self.url + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        return data["choices"][0]["message"]["content"].strip()


# ----------------------------------------------------------------- facts
def _nice(label):
    if label and label.startswith("speed_limit_"):
        return f"speed limit {label.split('_')[-1]}"
    return (label or "").replace("_", " ")


def trip_facts(con, trip_id):
    """Everything the model may say about one trip, already rounded."""
    con.row_factory = sqlite3.Row
    t = con.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    if t is None:
        return None
    ev = con.execute("SELECT * FROM events WHERE trip_id=? ORDER BY t", (trip_id,)).fetchall()
    end = t["ended_at"] or time.time()
    secs = max(0, end - t["started_at"])
    over = [e for e in ev if e["kind"] == "overspeed"]
    worst = max(over, key=lambda e: e["speed"] - e["limit_kmh"], default=None)
    signs = [_nice(e["label"]) for e in ev if e["kind"] == "sign"]
    f = {
        "trip_number": t["id"],
        "date": time.strftime("%A %d %B", time.localtime(t["started_at"])),
        "start_time": time.strftime("%H:%M", time.localtime(t["started_at"])),
        "duration_minutes": round(secs / 60),
        "duration_seconds": round(secs),
        "distance_km": round(t["distance_km"], 1),
        "top_speed_kmh": round(t["max_speed"]),
        "average_speed_kmh": round(t["avg_speed"]),
        "overspeed_events": t["overspeed_count"],
        "seconds_over_limit": round(t["over_s"]),
        "warnings_spoken": t["warnings"],
        "harsh_braking": t["harsh_brakes"],
        "harsh_acceleration": t["harsh_accels"],
        "signs_seen": len(signs),
        "sign_names": sorted(set(signs)),
    }
    if worst is not None:
        f["worst_overspeed"] = {"speed_kmh": round(worst["speed"]), "limit_kmh": worst["limit_kmh"],
                                "over_by_kmh": round(worst["speed"] - worst["limit_kmh"]),
                                "lasted_seconds": round(worst["duration_s"])}
    return f


def history_facts(con, n=20):
    con.row_factory = sqlite3.Row
    ids = [r["id"] for r in con.execute(
        "SELECT id FROM trips WHERE ended_at IS NOT NULL ORDER BY started_at DESC LIMIT ?", (n,))]
    trips = [trip_facts(con, i) for i in ids]
    tot = {
        "trips": len(trips),
        "total_distance_km": round(sum(t["distance_km"] for t in trips), 1),
        "total_overspeed_events": sum(t["overspeed_events"] for t in trips),
        "total_harsh_braking": sum(t["harsh_braking"] for t in trips),
        "trips_with_no_overspeed": sum(1 for t in trips if t["overspeed_events"] == 0),
    }
    return {"totals": tot, "trips_newest_first": trips}


# ----------------------------------------------------------------- checking
def _numbers_in(obj, out):
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.add(round(float(obj), 1))
        out.add(float(round(obj)))
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers_in(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers_in(v, out)
    elif isinstance(obj, str):
        for m in re.findall(r"\d+(?:\.\d+)?", obj):
            out.add(round(float(m), 1))
    return out


def unsupported_numbers(text, facts):
    """Numbers in `text` that do not appear anywhere in `facts`."""
    allowed = _numbers_in(facts, set())
    bad = []
    for m in NUM.findall(text.replace(",", "")):
        v = round(float(m), 1)
        if v not in allowed and float(round(v)) not in allowed:
            bad.append(m)
    for w, v in WORD_NUMS.items():                      # "twice", "three times"...
        if re.search(rf"\b{w}\b", text, re.I) and float(v) not in allowed and v > 1:
            bad.append(w)
    return bad


# ----------------------------------------------------------------- tasks
SUMMARY_SYSTEM = (
    "You are Driver Companion, a calm in-car assistant. Write a spoken trip summary for the "
    "driver in 2 or 3 short sentences, then one short, kind safety tip. Use ONLY the facts "
    "given. Never invent or calculate numbers; copy them exactly as given. Say speeds as "
    "'km/h'. No lists, no markdown, no emojis.")

ASK_SYSTEM = (
    "You are Driver Companion. Answer the driver's question in 1 to 3 short sentences using "
    "ONLY the trip facts given. Copy numbers exactly; never calculate or estimate new ones. "
    "If the facts do not contain the answer, say you don't have that information. "
    "No markdown, no emojis.")


def _generate_checked(llm, system, facts, prompt):
    """Ask, check numbers, retry once cold. Returns (text or None, attempts log)."""
    log = []
    for temp in (0.3, 0.0):
        try:
            text = llm.chat(system, prompt, temperature=temp)
        except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
            log.append(f"model error: {e}")
            return None, log
        bad = unsupported_numbers(text, facts)
        log.append({"temperature": temp, "text": text, "unsupported": bad})
        if not bad:
            return text, log
    return None, log


def summarize_trip(con, trip_id, fallback_text, llm=None):
    """Returns dict(text, source, verified, log)."""
    llm = llm or LocalLLM()
    facts = trip_facts(con, trip_id)
    if facts is None or not llm.available():
        return {"text": fallback_text, "source": "template", "verified": False,
                "log": ["no model server running"]}
    prompt = "Trip facts:\n" + json.dumps(facts, indent=1)
    text, log = _generate_checked(llm, SUMMARY_SYSTEM, facts, prompt)
    if text is None:
        return {"text": fallback_text, "source": "template", "verified": False, "log": log}
    return {"text": text, "source": "llm", "verified": True, "log": log}


def answer_question(con, question, llm=None):
    """Returns dict(answer, source, verified, log)."""
    llm = llm or LocalLLM()
    facts = history_facts(con)
    if not facts["trips_newest_first"]:
        return {"answer": "No finished trips yet. Drive one and ask me again.",
                "source": "app", "verified": True, "log": []}
    if not llm.available():
        t = facts["totals"]
        last = facts["trips_newest_first"][0]
        return {"answer": (f"The language model isn't running, so here are the basics: "
                           f"{t['trips']} trips, {t['total_distance_km']} km in total, "
                           f"{t['total_overspeed_events']} overspeed events. Your last trip was "
                           f"{last['distance_km']} km with a top speed of {last['top_speed_kmh']} km/h."),
                "source": "app", "verified": True, "log": ["no model server running"]}
    prompt = ("Trip facts (trips_newest_first[0] is the most recent trip):\n"
              + json.dumps(facts, indent=1) + "\n\nQuestion: " + question.strip()[:300])
    text, log = _generate_checked(llm, ASK_SYSTEM, facts, prompt)
    if text is None:
        return {"answer": "I couldn't answer that reliably from your trip data. Try asking about "
                          "your last trip, overspeeding or harsh braking.",
                "source": "app", "verified": False, "log": log}
    return {"answer": text, "source": "llm", "verified": True, "log": log}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Try the LLM layer from the terminal")
    ap.add_argument("--db", default=os.path.join(os.path.dirname(__file__), "trips.db"))
    ap.add_argument("--ask", default=None, help="a question; omit to summarise the last trip")
    a = ap.parse_args()
    llm = LocalLLM()
    print(f"model server {llm.url}  model {llm.model}  running: {llm.available()}")
    con = sqlite3.connect(a.db)
    if a.ask:
        print(answer_question(con, a.ask, llm)["answer"])
    else:
        row = con.execute("SELECT id, summary FROM trips WHERE ended_at IS NOT NULL "
                          "ORDER BY started_at DESC LIMIT 1").fetchone()
        r = summarize_trip(con, row[0], row[1], llm)
        print(f"[{r['source']}, verified={r['verified']}] {r['text']}")
