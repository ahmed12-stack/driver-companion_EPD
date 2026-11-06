"""Bluetooth UUIDs from the Driver Companion Bluetooth Protocol v1.

The Android app uses the same values. Never change them: a change breaks
every phone that is already paired.
"""


def _uuid(n: int) -> str:
    return f"70b6{n:04x}-fb6b-4539-82b8-75e1dfffb8c2"


SERVICE = _uuid(0x0000)
NOTIFICATION_IN = _uuid(0x0001)  # phone -> device: notifications, SMS, calls
LOCATION_IN = _uuid(0x0002)      # phone -> device: phone GPS fixes
SETTINGS = _uuid(0x0003)         # both ways: settings, hello handshake
TRIP_OUT = _uuid(0x0004)         # device -> phone: trip records (later checkpoint)
STATUS = _uuid(0x0005)           # device -> phone: replies, acks, errors
UPDATE_IN = _uuid(0x0006)        # phone -> device: signed updates (later checkpoint)
