"""Thermal guard (REQ-24): if the Pi reports it is throttling,
drop the camera rate to 10 fps; go back to 15 fps when it recovers."""
import subprocess


def pi_is_throttled():
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                             text=True, timeout=1).stdout   # "throttled=0x50005"
        flags = int(out.strip().split("=")[1], 16)
        return bool(flags & 0x4) or bool(flags & 0x8)  # currently throttled / soft temp limit
    except Exception:
        return False                                   # not a Pi


def soc_temp_c():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read()) / 1000.0
    except OSError:
        return None
