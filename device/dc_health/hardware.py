"""Best-effort checks for the plug-in hardware (REQ-29). Each returns True
(found), False (not found) or None (cannot tell, e.g. the tool is missing)."""

import asyncio
import fcntl
import logging
import os
import shutil
import termios
import time

log = logging.getLogger(__name__)

I2C_SLAVE = 0x0703
MPU6050_ADDRESSES = (0x68, 0x69)


async def check_camera(timeout_s: float = 15):
    for tool in ("rpicam-hello", "libcamera-hello"):
        if shutil.which(tool):
            break
    else:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            tool, "--list-cameras", stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
    except (OSError, asyncio.TimeoutError):
        return None
    text = out.decode("utf-8", errors="replace")
    return "Available cameras" in text and "No cameras available" not in text


def check_imu(bus_no: int = 1):
    """The MPU6050 answers on I2C address 0x68 (or 0x69)."""
    path = f"/dev/i2c-{bus_no}"
    if not os.path.exists(path):
        return False  # I2C is switched off, or nothing to talk to
    for address in MPU6050_ADDRESSES:
        try:
            fd = os.open(path, os.O_RDWR)
        except OSError:
            return None
        try:
            fcntl.ioctl(fd, I2C_SLAVE, address)
            os.read(fd, 1)
            return True
        except OSError:
            continue
        finally:
            os.close(fd)
    return False


def _read_serial(path: str, baud: int, seconds: float) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(fd)
        speed = getattr(termios, f"B{baud}")
        attrs[0] = 0                                  # iflag: raw
        attrs[1] = 0                                  # oflag
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[3] = 0                                  # lflag
        attrs[4] = attrs[5] = speed
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        data, deadline = b"", time.monotonic() + seconds
        while time.monotonic() < deadline and len(data) < 4096:
            try:
                data += os.read(fd, 1024)
            except BlockingIOError:
                time.sleep(0.05)
        return data
    finally:
        os.close(fd)


async def check_gnss(path: str = "/dev/serial0", baud: int = 9600, seconds: float = 3):
    """The NEO-6M sends NMEA sentences ($GP..., $GN...) every second.
    Once a dc_gnss module owns this port, check its status instead."""
    if not os.path.exists(path):
        return False
    try:
        data = await asyncio.to_thread(_read_serial, path, baud, seconds)
    except OSError as e:
        log.info("could not read %s: %s", path, e)
        return None
    return b"$GP" in data or b"$GN" in data


async def check_all() -> dict:
    return {
        "camera": await check_camera(),
        "imu": check_imu(),
        "gnss": await check_gnss(),
    }
