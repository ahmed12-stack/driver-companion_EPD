"""Trip summaries: a factual sentence, then a short remark by the on-device LLM.

The numbers always come from a fixed sentence, so they are always right. The
LLM adds one short remark in its own words ("Ease off a little on the open
stretches next time."). The remark is checked before anyone hears it: no
numbers at all, short, spoken to the driver, nothing about things the trip
did not measure, and no praise when there were warnings. A remark that fails
is dropped, and the driver hears only the factual sentence.
"""

import asyncio
import logging
import os
import re
import subprocess
import time

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a friendly in-car assistant. When a trip ends, reply to the driver "
    "with one short, warm sentence of at most 15 words, speaking to them as 'you'. "
    "No numbers, times or places, and do not talk about yourself. If there were "
    "warnings, gently suggest care next time. Otherwise, give brief encouragement."
)

# Example exchanges shown before the real trip. A small model follows examples
# far better than instructions. They are fixed, so they sit in the cached part.
EXAMPLES = [
    ("The trip has ended. It was a medium-length trip. There were overspeed warnings.",
     "Ease off a little on the open stretches next time."),
    ("The trip has ended. It was a short trip. There was harsh braking.",
     "Try leaving a bit more room so you can brake gently."),
    ("The trip has ended. It was a long trip, with heavy traffic and many stops.",
     "That was a lot of stop and go, so take a moment to relax."),
    ("The trip has ended. It was a short trip. Nothing unusual happened.",
     "Nice and easy today. Enjoy the rest of your day."),
]


def chat_prompt(user: str) -> str:
    """The whole conversation in Qwen's chat format (ChatML), written out by
    hand: the instructions, the example exchanges, then the real trip.
    llama.cpp ignores --prompt-cache in its chat mode, so the model is run in
    plain mode, and everything before the real trip is cached.
    Tied to the Qwen model family: change this if the model changes."""
    parts = [f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"]
    for question, answer in EXAMPLES:
        parts.append(f"<|im_start|>user\n{question}<|im_end|>\n"
                     f"<|im_start|>assistant\n{answer}<|im_end|>\n")
    parts.append(f"<|im_start|>user\n{user}<|im_end|>\n<|im_start|>assistant\n")
    return "".join(parts)


MAX_REMARK_CHARS = 120
MAX_REMARK_WORDS = 20
MANY_STOPS = 5
ATTEMPTS = 3          # tries per trip; the first remark that passes is used
ATTEMPT_BUDGET_S = 45  # no new try starts after this long

NUMBER_WORDS = {
    "zero", "one", "once", "two", "twice", "three", "thrice", "four", "five", "six",
    "seven", "eight", "nine", "ten", "eleven", "twelve", "dozen", "hundred",
    "half", "couple", "noon", "midnight",
}
PRAISE = {"well", "great", "good", "nice", "excellent", "perfect", "smooth",
          "safe", "safely", "proud", "superb", "flawless", "careful", "carefully"}
CAUTION = {"ease", "slow", "slower", "slowing", "watch", "avoid", "reduce", "next"}
THIRD_PERSON = {"driver", "drivers", "they", "their", "he", "she", "his", "her"}
# The device cannot promise anything ("I'll keep an eye on the speed").
FIRST_PERSON = {"i", "i'll", "i'm", "i've", "i'd", "me", "my", "myself",
                "we", "we'll", "we're", "us", "our"}

# Words that refer to a measurement; a remark may only touch what was measured.
TOPICS = {
    "overspeed": "overspeed", "overspeeding": "overspeed", "speeding": "overspeed",
    "limit": "overspeed", "limits": "overspeed", "warning": "warnings",
    "warnings": "warnings",
    "brake": "harsh_brake", "brakes": "harsh_brake", "braking": "harsh_brake",
    "braked": "harsh_brake",
    "accelerate": "harsh_accel", "acceleration": "harsh_accel",
    "accelerating": "harsh_accel", "accelerated": "harsh_accel",
    "sign": "signs", "signs": "signs",
    "traffic": "traffic", "jam": "traffic", "jams": "traffic", "congestion": "traffic",
    "patient": "traffic", "patience": "traffic",
}
WORD = re.compile(r"[a-z']+")
LOG_LINE = re.compile(r"^\s*(\d+\.){2,}\d+\s+[A-Z]\s|common_perf_print|^\s*(llama|ggml|main|load)_")


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _warnings(trip: dict) -> bool:
    return any(trip.get(k) for k in ("overspeed", "harsh_brake", "harsh_accel"))


def facts_sentence(trip: dict) -> str:
    """The factual part. Always correct, spoken straight away."""
    text = (f"Trip complete. You drove {trip['km']} kilometres in "
            f"{_plural(trip['min'], 'minute')}, with a top speed of "
            f"{trip['max_kmh']} kilometres per hour.")
    extras = []
    if trip.get("stops"):
        extras.append(f"stopped {_plural(trip['stops'], 'time')}")
    if trip.get("overspeed"):
        extras.append(f"had {_plural(trip['overspeed'], 'overspeed warning')}")
    if trip.get("harsh_brake"):
        extras.append(f"braked hard {_plural(trip['harsh_brake'], 'time')}")
    if trip.get("harsh_accel"):
        extras.append(f"accelerated hard {_plural(trip['harsh_accel'], 'time')}")
    if extras:
        body = extras[0] if len(extras) == 1 else ", ".join(extras[:-1]) + " and " + extras[-1]
        text += f" You {body}."
    return text


# The old name, still used by tests and tools.
template_summary = facts_sentence


def remark_prompt(trip: dict) -> str:
    """What the LLM is told about the trip, in plain words, without numbers."""
    minutes = trip.get("min", 0)
    length = "short" if minutes < 10 else "long" if minutes >= 45 else "medium-length"
    text = f"The trip has ended. It was a {length} trip"
    if trip.get("stops", 0) >= MANY_STOPS:
        text += ", with heavy traffic and many stops"
    events = [phrase for key, phrase in (("overspeed", "overspeed warnings"),
                                         ("harsh_brake", "harsh braking"),
                                         ("harsh_accel", "harsh acceleration"))
              if trip.get(key)]
    if events:
        verb = "was" if len(events) == 1 and events[0].startswith("harsh") else "were"
        text += f". There {verb} " + " and ".join(events) + "."
    elif trip.get("stops", 0) >= MANY_STOPS:
        text += "."
    else:
        text += ". Nothing unusual happened."
    return text


def clean(raw: str) -> str:
    # In plain mode the model may print its end marker, or start another turn.
    text = raw.split("<|im_end|>")[0].split("<|im_start|>")[0]
    text = text.replace("[end of text]", " ")
    lines = [ln for ln in text.splitlines()
             if not ln.strip().startswith(">") and not LOG_LINE.search(ln)]
    text = " ".join(" ".join(lines).split()).strip().strip('"').strip()
    if text and text[-1] not in ".!?":
        text += "."
    return text[:1].upper() + text[1:]


def is_good_remark(text: str, trip: dict) -> bool:
    """True if the remark can be spoken to the driver as it is."""
    if not text or len(text) > MAX_REMARK_CHARS or any(c in text for c in "<>[]{}#*|:"):
        return False
    if any(ch.isdigit() for ch in text):
        return False
    words = WORD.findall(text.lower())
    if not words or len(words) > MAX_REMARK_WORDS or sum(text.count(c) for c in ".!?") > 2:
        return False
    if any(w in NUMBER_WORDS or w in THIRD_PERSON or w in FIRST_PERSON for w in words):
        return False
    low = text.lower()
    if any(p in low for p in ("trip has ended", "nothing unusual", "trip length")):
        return False  # repeating its input back
    measured = {k for k in ("overspeed", "harsh_brake", "harsh_accel", "signs") if trip.get(k)}
    if _warnings(trip):
        measured.add("warnings")
    if trip.get("stops", 0) >= MANY_STOPS:
        measured.add("traffic")
    if any(TOPICS[w] not in measured for w in words if w in TOPICS):
        return False  # e.g. "no harsh braking" when braking is not measured yet
    if _warnings(trip) and any(w in PRAISE for w in words):
        return False  # no "well done" after overspeed warnings
    if not _warnings(trip) and any(w in CAUTION for w in words):
        return False  # no "ease off next time" when nothing went wrong
    return True


class Summarizer:
    def __init__(self, llm_bin: str, model: str, threads: int = 4, timeout_s: float = 120,
                 cache_path: str = None):
        self.llm_bin = llm_bin
        self.model = model
        self.threads = threads
        self.timeout_s = timeout_s
        # The fixed instructions are the same every time; llama.cpp can save
        # their processed state here so later remarks skip that work.
        self.cache_path = cache_path
        self._cache_supported = None
        self.last_used_cache = False
        self.last_attempts = []  # (text, passed) per try, for tools/try_summary.py

    @property
    def available(self) -> bool:
        return os.access(self.llm_bin, os.X_OK) and os.path.isfile(self.model)

    async def remark(self, trip: dict, attempts: int = ATTEMPTS,
                     budget_s: float = ATTEMPT_BUDGET_S):
        """The LLM's remark, or None if there is no LLM or no try passed the check.
        Each try is sampled afresh, so a failed one is often followed by a good one."""
        self.last_attempts = []
        if not self.available:
            log.info("no LLM installed: factual sentence only")
            return None
        prompt = remark_prompt(trip)
        started = time.monotonic()
        for n in range(attempts):
            if n and time.monotonic() - started > budget_s:
                break
            try:
                text = clean(await self._run(prompt, use_cache=True))
                if not text and self.last_used_cache:
                    # Most likely a damaged cache file (e.g. a power cut while saving).
                    log.warning("empty LLM reply with the prompt cache: deleting it and retrying")
                    self._drop_cache()
                    text = clean(await self._run(prompt, use_cache=False))
            except asyncio.TimeoutError:
                log.warning("LLM took longer than %d s: factual sentence only", self.timeout_s)
                return None
            except OSError as e:
                log.warning("could not run the LLM: %s", e)
                return None
            ok = is_good_remark(text, trip)
            self.last_attempts.append((text, ok))
            if ok:
                return text
            log.info("LLM remark try %d failed the check", n + 1)
        log.warning("no LLM remark passed the check: factual sentence only")
        return None

    async def summarize(self, trip: dict):
        """Returns (full text, "llm") or (factual sentence, "template")."""
        remark = await self.remark(trip)
        facts = facts_sentence(trip)
        return (f"{facts} {remark}", "llm") if remark else (facts, "template")

    def _env(self) -> dict:
        env = dict(os.environ)
        lib_dir = os.path.dirname(os.path.abspath(self.llm_bin))
        env["LD_LIBRARY_PATH"] = lib_dir + (":" + env["LD_LIBRARY_PATH"]
                                            if env.get("LD_LIBRARY_PATH") else "")
        return env

    async def cache_supported(self) -> bool:
        """Checked once from the program's own help text, so an llama.cpp build
        without the option is never given it."""
        if self._cache_supported is None:
            self._cache_supported = False
            try:
                proc = await asyncio.create_subprocess_exec(
                    self.llm_bin, "--help", stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=self._env())
                try:
                    out, _ = await asyncio.wait_for(proc.communicate(), 30)
                    self._cache_supported = b"--prompt-cache" in out
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            except OSError:
                pass
            log.info("LLM prompt cache %s", "available" if self._cache_supported else "not available")
        return self._cache_supported

    def _drop_cache(self):
        if self.cache_path:
            try:
                os.unlink(self.cache_path)
            except FileNotFoundError:
                pass

    async def _run(self, prompt: str, use_cache: bool = True) -> str:
        cmd = ["nice", "-n", "10", self.llm_bin, "-m", self.model,
               "-t", str(self.threads), "-c", "1024", "-n", "40", "--temp", "0.5",
               "-no-cnv", "--no-display-prompt", "-p", chat_prompt(prompt)]
        self.last_used_cache = bool(use_cache and self.cache_path and await self.cache_supported())
        if self.last_used_cache:
            cmd += ["--prompt-cache", self.cache_path]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=self._env())
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), self.timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return out.decode("utf-8", errors="replace")
