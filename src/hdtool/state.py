"""Session state management for resume capability."""

from __future__ import annotations

import fcntl
import json
import os
import uuid
from datetime import datetime, timezone

from .models import SessionState


STATE_FILENAME = ".hdtool-state.json"
LOCK_FILENAME = ".hdtool.lock"
ERROR_LOG_FILENAME = ".hdtool-errors.jsonl"


def state_path(dest: str) -> str:
    return os.path.join(dest, STATE_FILENAME)


def lock_path(dest: str) -> str:
    return os.path.join(dest, LOCK_FILENAME)


def error_log_path(dest: str) -> str:
    return os.path.join(dest, ERROR_LOG_FILENAME)


def load_state(dest: str) -> SessionState | None:
    """Load existing session state from destination directory.

    Returns None if no state file exists or it's corrupted.
    """
    path = state_path(dest)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return SessionState.from_json(f.read())
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def save_state(dest: str, session: SessionState) -> None:
    """Save session state to destination directory."""
    session.last_updated = datetime.now(timezone.utc).isoformat()
    path = state_path(dest)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Write atomically: write to temp file then rename
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        f.write(session.to_json())
    os.replace(tmp_path, path)


def create_session(source: str, dest: str) -> SessionState:
    """Create a new session state."""
    now = datetime.now(timezone.utc).isoformat()
    return SessionState(
        session_id=str(uuid.uuid4()),
        source_path=source,
        dest_path=dest,
        started_at=now,
        last_updated=now,
    )


def delete_state(dest: str) -> None:
    """Remove state and lock files from destination."""
    for path in (state_path(dest), lock_path(dest)):
        try:
            os.remove(path)
        except OSError:
            pass


class SessionLock:
    """File-based lock to prevent concurrent runs on the same destination.

    Usage:
        with SessionLock(dest) as lock:
            if not lock.acquired:
                print("Another instance is running")
                return
            # ... do work
    """

    def __init__(self, dest: str):
        self.path = lock_path(dest)
        self.acquired = False
        self._fd = None

    def __enter__(self) -> SessionLock:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self._fd = open(self.path, "w")
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fd.write(str(os.getpid()))
            self._fd.flush()
            self.acquired = True
        except (OSError, BlockingIOError):
            if self._fd:
                self._fd.close()
                self._fd = None
            self.acquired = False
        return self

    def __exit__(self, *args) -> None:
        if self._fd:
            try:
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
                self._fd.close()
            except OSError:
                pass
            try:
                os.remove(self.path)
            except OSError:
                pass
