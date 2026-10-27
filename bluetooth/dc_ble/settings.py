"""User settings, stored on the device and changed from the app.

The file is written atomically (temp file, fsync, rename) so a power cut
never leaves it half-written (REQ-23), and it is readable by the owner
only (REQ-30).
"""

import json
import logging
import os
import tempfile

log = logging.getLogger(__name__)

DEFAULTS = {
    "vol": 1,            # 0 = low, 1 = medium, 2 = high (REQ-20)
    "apps": [],          # Android package names the phone forwards
    "read_text": False,  # speak message text, not just app and sender
    "quiet": False,      # do not speak notifications or calls
}


class SettingsError(ValueError):
    pass


def validate(changes: dict) -> dict:
    """Check a partial settings update and return it unchanged if valid."""
    clean = {}
    for key, value in changes.items():
        if key == "vol":
            if isinstance(value, bool) or value not in (0, 1, 2):
                raise SettingsError("vol must be 0, 1 or 2")
        elif key == "apps":
            if (not isinstance(value, list) or len(value) > 50
                    or not all(isinstance(a, str) and 0 < len(a) <= 100 for a in value)):
                raise SettingsError("apps must be a list of up to 50 package names")
        elif key in ("read_text", "quiet"):
            if not isinstance(value, bool):
                raise SettingsError(f"{key} must be true or false")
        else:
            raise SettingsError(f"unknown setting {key!r}")
        clean[key] = value
    return clean


class SettingsStore:
    def __init__(self, path: str):
        self.path = path
        self.values = dict(DEFAULTS)
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.values.update(validate({k: v for k, v in data.items() if k in DEFAULTS}))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError) as e:
            log.warning("settings file unreadable, using defaults: %s", e)

    def update(self, changes: dict) -> dict:
        clean = validate(changes)
        self.values.update(clean)
        self._save()
        return clean

    def _save(self):
        folder = os.path.dirname(self.path) or "."
        os.makedirs(folder, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".settings-")  # created 0600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.values, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        dir_fd = os.open(folder, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
