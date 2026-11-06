"""Print every message on the board as it happens (Ctrl+C to stop).

  sudo /opt/driver-companion/venv/bin/python tools/watch_board.py [topic-pattern]

Heartbeats (module/+/alive) are hidden unless you ask for them.
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from dc_common import topics  # noqa: E402
from dc_common.bus import MqttBus, matches  # noqa: E402


async def main(pattern: str, sock: str):
    bus = MqttBus(f"watch_{os.getpid()}", sock, announce=False)

    def show(topic, data):
        if pattern == "#" and matches(topics.ALIVE_ALL, topic):
            return
        print(time.strftime("%H:%M:%S"), topic, json.dumps(data, ensure_ascii=False), flush=True)

    bus.subscribe(pattern, show)
    await bus.run()


if __name__ == "__main__":
    try:
        asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "#",
                         os.environ.get("DC_BUS", topics.BUS_SOCKET)))
    except KeyboardInterrupt:
        pass
