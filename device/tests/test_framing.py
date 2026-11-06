import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from dc_phone.framing import FrameError, MessageIds, Reassembler, body_size, split  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def collect():
    errors = []
    return errors, (lambda code, detail: errors.append(code))


@pytest.mark.parametrize("size", [0, 1, 17, 18, 241, 242, 1000, 5000])
@pytest.mark.parametrize("mtu", [23, 100, 247, 517])
def test_round_trip(size, mtu):
    body = bytes(i % 251 for i in range(size))
    if size > 255 * body_size(mtu):
        with pytest.raises(FrameError):
            split(body, 7, mtu)
        return
    packets = split(body, 7, mtu)
    assert all(len(p) <= mtu - 3 for p in packets)
    assert all(p[0] == 7 and p[2] == len(packets) for p in packets)
    r = Reassembler()
    results = [r.feed(p) for p in packets]
    assert results[:-1] == [None] * (len(packets) - 1)
    assert results[-1] == body


def test_body_size_matches_spec():
    assert body_size(247) == 241
    assert body_size(23) == 17
    assert body_size(10) == 17  # below the minimum MTU we assume 23


def test_short_message_has_header():
    assert split(b"{}", 3, 247) == [bytes((3, 0, 1)) + b"{}"]


def test_too_large_to_send():
    with pytest.raises(FrameError):
        split(b"x" * 20000, 0, 23)


def test_message_ids_wrap():
    ids = MessageIds()
    seen = [ids.next() for _ in range(258)]
    assert seen[:3] == [0, 1, 2] and seen[255] == 255 and seen[256] == 0


def test_out_of_order_is_dropped():
    errors, on_error = collect()
    r = Reassembler(on_error=on_error)
    p = split(b"a" * 100, 1, 23)
    assert r.feed(p[0]) is None
    assert r.feed(p[2]) is None
    assert errors == ["INCOMPLETE"]
    assert not r.busy


def test_new_message_mid_message():
    errors, on_error = collect()
    r = Reassembler(on_error=on_error)
    first = split(b"a" * 100, 1, 23)
    second = split(b"hello", 2, 23)
    r.feed(first[0])
    assert r.feed(second[0]) == b"hello"
    assert errors == ["INCOMPLETE"]


def test_timeout():
    clock = Clock()
    errors, on_error = collect()
    r = Reassembler(on_error=on_error, clock=clock)
    p = split(b"a" * 100, 1, 23)
    r.feed(p[0])
    clock.t = 2.5
    assert r.feed(p[1]) is None
    assert "INCOMPLETE" in errors


def test_bad_frames():
    errors, on_error = collect()
    r = Reassembler(on_error=on_error)
    assert r.feed(b"\x00\x00") is None
    assert r.feed(bytes((0, 0, 0)) + b"x") is None
    assert r.feed(bytes((0, 3, 2)) + b"x") is None
    assert errors == ["BAD_FRAME"] * 3


def test_too_large_received():
    errors, on_error = collect()
    r = Reassembler(on_error=on_error, max_size=50)
    for p in split(b"a" * 100, 1, 23):
        r.feed(p)
    assert errors[0] == "TOO_LARGE"
    assert not r.busy
