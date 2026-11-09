"""Local call history: what happened on each call, kept in SQLite.

No Qt and no PJSIP here. CallTracker follows one call's states and turns
them into a CallRecord; CallHistory stores records in a small database.

Why SQLite (Python's built-in sqlite3, no extra dependency): adding a call
is a one-row insert instead of rewriting a whole file, a crash mid-write
can't corrupt it, and later features (missed calls only, search) are just
queries.
"""

import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.paths import DATA_DIR
from app.sip.calls import CallState, Remote

DEFAULT_HISTORY_PATH = DATA_DIR / "history.db"
MAX_ENTRIES = 1000   # oldest calls beyond this are deleted


class Direction(Enum):
    INCOMING = "in"
    OUTGOING = "out"


class Result(Enum):
    ANSWERED = "answered"
    MISSED = "missed"            # incoming, never answered (incl. rejected as busy)
    DECLINED = "declined"        # incoming, we pressed Hang up while it rang
    NOT_ANSWERED = "not_answered"  # outgoing, never connected (busy, no answer...)


@dataclass
class CallRecord:
    direction: Direction
    remote: Remote
    started_at: float            # Unix time the call began ringing / dialing
    result: Result
    duration: float = 0.0        # seconds connected; 0 if never answered
    reason: str = ""             # e.g. "486 Busy Here" for an unanswered call
    id: int | None = None        # database row id once stored


class CallTracker:
    """Follows one call from start to end and produces its CallRecord."""

    def __init__(self, direction: Direction, remote: Remote, now: float | None = None):
        self.direction = direction
        self.remote = remote
        self.started_at = time.time() if now is None else now
        self._answered_at = None
        self._declined = False
        self.note = ""            # kept for answered calls, e.g. "Transferred to 103"

    def on_state(self, state: CallState, remote: Remote | None = None, now: float | None = None):
        # The far end's name is often only known once PJSIP has the reply.
        if remote is not None and remote.number:
            self.remote = remote
        if state is CallState.ACTIVE and self._answered_at is None:
            self._answered_at = time.time() if now is None else now

    def decline(self):
        """We hung up on an incoming call that was still ringing."""
        self._declined = True

    def finish(self, reason: str = "", now: float | None = None) -> CallRecord:
        now = time.time() if now is None else now
        if self._answered_at is not None:
            result, duration, reason = Result.ANSWERED, now - self._answered_at, self.note
        elif self.direction is Direction.OUTGOING:
            result, duration = Result.NOT_ANSWERED, 0.0
        else:
            result, duration = (Result.DECLINED if self._declined else Result.MISSED), 0.0
        return CallRecord(self.direction, self.remote, self.started_at, result,
                          duration, reason)


class CallHistory:
    """The call list on disk. Newest first."""

    def __init__(self, path: Path = DEFAULT_HISTORY_PATH):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS calls (
                id         INTEGER PRIMARY KEY,
                started_at REAL NOT NULL,
                direction  TEXT NOT NULL,
                number     TEXT NOT NULL,
                name       TEXT NOT NULL,
                result     TEXT NOT NULL,
                duration   REAL NOT NULL,
                reason     TEXT NOT NULL
            )""")
        self._db.commit()

    def add(self, record: CallRecord) -> CallRecord:
        with self._db:  # one transaction: commits, or rolls back on error
            cursor = self._db.execute(
                "INSERT INTO calls (started_at, direction, number, name, result, duration, reason)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (record.started_at, record.direction.value, record.remote.number,
                 record.remote.name, record.result.value, record.duration, record.reason),
            )
            self._db.execute(
                "DELETE FROM calls WHERE id NOT IN"
                " (SELECT id FROM calls ORDER BY started_at DESC, id DESC LIMIT ?)",
                (MAX_ENTRIES,),
            )
        record.id = cursor.lastrowid
        return record

    def recent(self, limit: int = MAX_ENTRIES) -> list[CallRecord]:
        rows = self._db.execute(
            "SELECT id, started_at, direction, number, name, result, duration, reason"
            " FROM calls ORDER BY started_at DESC, id DESC LIMIT ?", (limit,),
        )
        return [
            CallRecord(Direction(direction), Remote(name, number), started_at,
                       Result(result), duration, reason, id=row_id)
            for row_id, started_at, direction, number, name, result, duration, reason in rows
        ]

    def delete(self, record_id: int):
        with self._db:
            self._db.execute("DELETE FROM calls WHERE id = ?", (record_id,))

    def clear(self):
        with self._db:
            self._db.execute("DELETE FROM calls")

    def close(self):
        self._db.close()
