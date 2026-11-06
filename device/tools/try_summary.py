"""Run the real LLM on sample trips several times and show what the device would say.

  sudo /opt/driver-companion/venv/bin/python tools/try_summary.py --runs 6

Each run prints what the LLM was told, its remark, whether the remark passed
the check, the time it took, and the full summary the driver would hear.
"""

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from dc_trip.summary import Summarizer, facts_sentence, remark_prompt  # noqa: E402

SAMPLES = [
    {"km": 12.4, "min": 23, "max_kmh": 58, "avg_kmh": 39, "stops": 3, "overspeed": 2,
     "harsh_brake": 1},
    {"km": 6.3, "min": 10, "max_kmh": 72, "avg_kmh": 45, "stops": 1},
    {"km": 31.8, "min": 47, "max_kmh": 84, "avg_kmh": 52, "stops": 6, "overspeed": 1},
]


async def main(args):
    s = Summarizer(os.path.expanduser(args.llm_bin), os.path.expanduser(args.model),
                   threads=args.threads, cache_path=None if args.no_cache else args.cache)
    if not s.available:
        sys.exit(f"LLM not found: {s.llm_bin} / {s.model}")
    if not args.no_cache:
        print("prompt cache:", "used" if await s.cache_supported() else "not supported by this llama.cpp")
        print("(the first run fills the cache, so later runs should be faster)")
    passed = 0
    for i in range(args.runs):
        trip = SAMPLES[i % len(SAMPLES)]
        print(f"\n--- trip {i + 1}: the LLM is told ---\n{remark_prompt(trip)}")
        t0 = time.monotonic()
        remark = await s.remark(trip)
        for n, (text, ok) in enumerate(s.last_attempts, 1):
            print(f"  try {n}: {'PASSED' if ok else 'rejected'}: {text}")
        passed += remark is not None
        print(f"time: {time.monotonic() - t0:.1f} s for {len(s.last_attempts)} tr"
              f"{'y' if len(s.last_attempts) == 1 else 'ies'}")
        print(f"driver hears: {facts_sentence(trip)}" + (f" {remark}" if remark else ""))
    print(f"\n{passed} of {args.runs} trips got an LLM remark")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--llm-bin", default="/opt/driver-companion/llm/llama-completion")
    p.add_argument("--model", default="/opt/driver-companion/llm/model.gguf")
    p.add_argument("--runs", type=int, default=6)
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--cache", default="/tmp/dc_llm_prompt.cache")
    p.add_argument("--no-cache", action="store_true", help="run without the prompt cache, to compare")
    asyncio.run(main(p.parse_args()))
