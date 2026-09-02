"""A plain-text log of what the app saw and how long it took.

Always on. When a read fails there is nothing on screen to inspect afterwards,
so the log has to already exist rather than be something the user is asked to
switch on and reproduce with.

Every call is best-effort: diagnostics must never be the reason the app stops
working, so all filesystem errors are swallowed.
"""

from __future__ import annotations

import time
from pathlib import Path

from .config import base_dir

LOG_NAME = "wardogs.log"

#: Past this the file is started over. A read writes a few hundred bytes, so
#: this still holds a long session while never growing without bound.
_MAX_BYTES = 512 * 1024

_path: Path | None = None


def log_path() -> Path:
    global _path
    if _path is None:
        _path = base_dir() / "debug" / LOG_NAME
    return _path


def log(message: str) -> None:
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > _MAX_BYTES:
            path.write_text("", encoding="utf-8")
        stamp = time.strftime("%H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp}  {message}\n")
    except OSError:
        pass
