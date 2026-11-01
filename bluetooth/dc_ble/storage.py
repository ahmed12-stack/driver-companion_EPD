"""Small JSON files that survive power cuts (REQ-23) and stay private (REQ-30)."""

import json
import logging
import os
import tempfile

log = logging.getLogger(__name__)


def atomic_write_json(path: str, data) -> None:
    """Write to a temp file, flush it to the card, then rename over the old file.
    A power cut leaves either the old file or the new one, never half of each.
    Files are created owner-only (0600)."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
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


def read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        log.warning("could not read %s, ignoring it: %s", path, e)
        return default


def remove(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
