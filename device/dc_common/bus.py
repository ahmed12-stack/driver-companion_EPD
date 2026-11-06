"""The message board: publish JSON messages to topics, and react to topics.

MqttBus talks to Mosquitto over a private socket file (no network port).
MemoryBus does the same inside one program, for tests.

Handlers are called one at a time, in the order messages arrive, so they must
be quick; long work (the LLM, speech) goes into a task of its own.
"""

import asyncio
import inspect
import json
import logging

from . import topics

log = logging.getLogger(__name__)

PUBLISH_WAIT_S = 5.0  # a publish waits this long for a connection before dropping


def matches(pattern: str, topic: str) -> bool:
    """MQTT topic matching: '+' is one level, '#' is everything below."""
    p, t = pattern.split("/"), topic.split("/")
    for i, part in enumerate(p):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(p) == len(t)


class Bus:
    def __init__(self, name: str):
        self.name = name
        self.connected = asyncio.Event()
        self._subs = []  # (pattern, handler)

    def subscribe(self, pattern: str, handler):
        """handler(topic, data) is called for every message on a matching topic.
        It may be a plain function or an async one."""
        self._subs.append((pattern, handler))
        self._on_new_subscription(pattern)

    def _on_new_subscription(self, pattern: str):
        pass

    async def publish(self, topic: str, data, retain: bool = False) -> bool:
        raise NotImplementedError

    def post(self, topic: str, data, retain: bool = False):
        """publish() for code that is not async: sends in the background."""
        asyncio.get_running_loop().create_task(self.publish(topic, data, retain))

    async def run(self):
        raise NotImplementedError

    async def _dispatch(self, topic: str, payload: bytes):
        try:
            data = json.loads(payload.decode("utf-8")) if payload else None
        except (UnicodeDecodeError, ValueError):
            log.warning("ignored a message on %s that is not JSON", topic)
            return
        for pattern, handler in list(self._subs):
            if not matches(pattern, topic):
                continue
            try:
                result = handler(topic, data)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                log.exception("handler for %s failed", topic)

    async def _wait_connected(self) -> bool:
        if self.connected.is_set():
            return True
        try:
            await asyncio.wait_for(self.connected.wait(), PUBLISH_WAIT_S)
            return True
        except asyncio.TimeoutError:
            return False


def _encode(data) -> bytes:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class MqttBus(Bus):
    """The real board: Mosquitto on a unix socket. Reconnects by itself."""

    def __init__(self, name: str, socket_path: str = topics.BUS_SOCKET, announce: bool = True):
        super().__init__(name)
        self.socket_path = socket_path
        self.announce = announce  # tools pass False: no module status on the board
        self._client = None

    def _on_new_subscription(self, pattern: str):
        if self._client is not None:
            asyncio.get_running_loop().create_task(self._client.subscribe(pattern, qos=1))

    async def publish(self, topic, data, retain=False) -> bool:
        import aiomqtt
        if not await self._wait_connected() or self._client is None:
            log.warning("not connected to the message board: dropped a message on %s", topic)
            return False
        try:
            await self._client.publish(topic, _encode(data), qos=1, retain=retain)
            return True
        except aiomqtt.MqttError as e:
            log.warning("could not publish on %s: %s", topic, e)
            return False

    async def run(self):
        import aiomqtt
        backoff = 1.0
        while True:
            will = aiomqtt.Will(topics.status(self.name), _encode("offline"), qos=1,
                                retain=True) if self.announce else None
            try:
                async with aiomqtt.Client(self.socket_path, transport="unix", will=will,
                                          identifier=f"dc-{self.name}", keepalive=10) as client:
                    self._client = client
                    for pattern in dict.fromkeys(p for p, _ in self._subs):
                        await client.subscribe(pattern, qos=1)
                    if self.announce:
                        await client.publish(topics.status(self.name), _encode("online"),
                                             qos=1, retain=True)
                    self.connected.set()
                    backoff = 1.0
                    log.info("connected to the message board")
                    async for message in client.messages:
                        await self._dispatch(str(message.topic), message.payload)
            except aiomqtt.MqttError as e:
                log.warning("message board connection lost (%s); retrying in %.0f s", e, backoff)
            finally:
                self._client = None
                self.connected.clear()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5.0)

    async def stop(self):
        """Say 'stopped' (on purpose, not a crash) before going away."""
        if self.announce:
            await self.publish(topics.status(self.name), "stopped", retain=True)


class MemoryBroker:
    """An in-process stand-in for Mosquitto, for tests: same topics, retained
    messages and wildcards."""

    def __init__(self):
        self.buses = []
        self.retained = {}
        self.log = []  # (topic, data) of everything published, for tests

    async def deliver(self, topic: str, payload: bytes, retain: bool):
        self.log.append((topic, json.loads(payload) if payload else None))
        if retain:
            self.retained[topic] = payload
        for bus in list(self.buses):
            if any(matches(p, topic) for p, _ in bus._subs):
                await bus._dispatch(topic, payload)


class MemoryBus(Bus):
    def __init__(self, name: str, broker: MemoryBroker):
        super().__init__(name)
        self.broker = broker
        broker.buses.append(self)
        self.connected.set()

    def _on_new_subscription(self, pattern: str):
        for topic, payload in list(self.broker.retained.items()):
            if matches(pattern, topic):
                asyncio.get_running_loop().create_task(self._dispatch(topic, payload))

    async def publish(self, topic, data, retain=False) -> bool:
        await self.broker.deliver(topic, _encode(data), retain)
        return True

    async def run(self):
        await self.publish(topics.status(self.name), "online", retain=True)
        await asyncio.Event().wait()

    async def stop(self):
        await self.publish(topics.status(self.name), "stopped", retain=True)
