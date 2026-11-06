"""Splits messages into Bluetooth packets and puts them back together.

Every packet starts with a 3-byte header:

    byte 0  message ID   (0-255, counts up and wraps)
    byte 1  chunk index  (0 for the first packet)
    byte 2  chunk count  (1-255)

followed by part of the UTF-8 JSON body.
"""

import math
import time

HEADER_SIZE = 3
ATT_OVERHEAD = 3            # bytes Bluetooth itself uses in each packet
MIN_MTU = 23                # smallest MTU Bluetooth Low Energy allows
MAX_MESSAGE_SIZE = 32 * 1024
REASSEMBLY_TIMEOUT_S = 2.0


class FrameError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def body_size(mtu: int) -> int:
    """Bytes of JSON that fit in one packet at this MTU."""
    return max(mtu, MIN_MTU) - ATT_OVERHEAD - HEADER_SIZE


def split(body: bytes, msg_id: int, mtu: int = MIN_MTU) -> list:
    """Cut one message into packets, each starting with the 3-byte header."""
    if not 0 <= msg_id <= 255:
        raise ValueError("msg_id must be 0-255")
    size = body_size(mtu)
    count = max(1, math.ceil(len(body) / size))
    if count > 255 or len(body) > MAX_MESSAGE_SIZE:
        raise FrameError("TOO_LARGE", f"{len(body)} bytes does not fit in 255 packets")
    return [
        bytes((msg_id, i, count)) + body[i * size:(i + 1) * size]
        for i in range(count)
    ]


class MessageIds:
    """Message ID counter for one direction: 0, 1, ... 255, 0, ..."""

    def __init__(self):
        self._next = 0

    def next(self) -> int:
        value = self._next
        self._next = (value + 1) % 256
        return value


class Reassembler:
    """Collects the packets of one message arriving on one characteristic.

    feed() returns the full message body once its last packet arrives, and
    None otherwise. Problems are reported through on_error(code, detail) and
    the partial message is thrown away.
    """

    def __init__(self, on_error=None, timeout=REASSEMBLY_TIMEOUT_S,
                 max_size=MAX_MESSAGE_SIZE, clock=time.monotonic):
        self._on_error = on_error
        self._timeout = timeout
        self._max_size = max_size
        self._clock = clock
        self.reset()

    def reset(self):
        self._msg_id = None
        self._count = 0
        self._expected = 0
        self._parts = []
        self._size = 0
        self._last = 0.0

    @property
    def busy(self) -> bool:
        return self._msg_id is not None

    def _fail(self, code: str, detail: str):
        self.reset()
        if self._on_error:
            self._on_error(code, detail)

    def feed(self, packet: bytes):
        now = self._clock()
        if self.busy and now - self._last > self._timeout:
            self._fail("INCOMPLETE", "timed out waiting for the next packet")

        if len(packet) < HEADER_SIZE:
            self._fail("BAD_FRAME", "packet is shorter than the 3-byte header")
            return None
        msg_id, index, count = packet[0], packet[1], packet[2]
        if count == 0 or index >= count:
            self._fail("BAD_FRAME", f"packet {index} of {count} is impossible")
            return None

        if index == 0:
            if self.busy:
                self._fail("INCOMPLETE", "a new message started before the last one finished")
            self._msg_id, self._count, self._expected = msg_id, count, 0
        elif (not self.busy or msg_id != self._msg_id
              or index != self._expected or count != self._count):
            self._fail("INCOMPLETE", "packet arrived out of order")
            return None

        body = packet[HEADER_SIZE:]
        self._size += len(body)
        if self._size > self._max_size:
            self._fail("TOO_LARGE", f"message is over {self._max_size} bytes")
            return None

        self._parts.append(body)
        self._expected += 1
        self._last = now
        if self._expected == self._count:
            message = b"".join(self._parts)
            self.reset()
            return message
        return None
