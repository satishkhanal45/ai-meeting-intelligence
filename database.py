"""SQLite database layer.

Handles schema creation, CRUD operations, and full-text search for all
meeting-related data. Uses WAL mode for better concurrent read performance.
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Optional

from config import DB_PATH
from logger import get_logger
from models import (
    ActionItem,
    Deadline,
    Decision,
    GraphData,
    Meeting,
    MeetingListItem,
    MeetingMetadata,
    Summary,
    Transcript,
)

logger = get_logger(__name__)

_RETRY_ATTEMPTS = 3
_RETRY_DELAY = 0.05


@contextmanager
def get_connection():
    """Yield a WAL-mode SQLite connection with row factory.

    Retries on SQLITE_BUSY up to ``_RETRY_ATTEMPTS`` times.
    """
    conn: Optional[sqlite3.Connection] = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            return
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() and attempt < _RETRY_ATTEMPTS - 1:
                time.sleep(_RETRY_DELAY * (attempt + 1))
                continue
            raise
        finally:
            if conn is not None:
                conn.close()


@contextmanager
def get_transaction():
    """Yield a connection inside an explicit transaction.

    Commits on success, rolls back on exception.
    """
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema ──────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meetings (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    date            TEXT NOT NULL,
    participants    TEXT NOT NULL DEFAULT '[]',
    provider        TEXT NOT NULL DEFAULT '',
    processing_time REAL NOT NULL DEFAULT 0.0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transcripts (
    meeting_id   TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
    raw_text     TEXT NOT NULL DEFAULT '',
    cleaned_text TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS summaries (
    meeting_id       TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
    executive_summary TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS action_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    owner       TEXT NOT NULL DEFAULT '',
    task        TEXT NOT NULL DEFAULT '',
    priority    TEXT NOT NULL DEFAULT 'medium',
    status      TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS deadlines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    description TEXT NOT NULL DEFAULT '',
    date        TEXT NOT NULL DEFAULT '',
    type        TEXT NOT NULL DEFAULT 'explicit'
);

CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    decision    TEXT NOT NULL DEFAULT '',
    rationale   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS graph_data (
    meeting_id TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
    graph_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_action_items_meeting ON action_items(meeting_id);
CREATE INDEX IF NOT EXISTS idx_deadlines_meeting ON deadlines(meeting_id);
CREATE INDEX IF NOT EXISTS idx_decisions_meeting ON decisions(meeting_id);
CREATE INDEX IF NOT EXISTS idx_meetings_date ON meetings(date);
CREATE INDEX IF NOT EXISTS idx_meetings_title ON meetings(title);
"""


def init_db() -> None:
    """Create all tables if they do not exist."""
    with get_transaction() as conn:
        conn.executescript(SCHEMA_SQL)
    logger.info("Database initialised", extra={"path": DB_PATH})


# ── Insert ──────────────────────────────────────────────────────────────


def insert_meeting(meeting: Meeting) -> None:
    """Persist a complete ``Meeting`` object and all its relations."""
    with get_transaction() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO meetings
               (id, title, date, participants, provider, processing_time)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                meeting.id,
                meeting.title,
                meeting.date,
                json.dumps(meeting.participants),
                meeting.provider,
                meeting.processing_time,
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO transcripts (meeting_id, raw_text, cleaned_text) VALUES (?, ?, ?)",
            (meeting.id, meeting.transcript.raw_text, meeting.transcript.cleaned_text),
        )
        conn.execute(
            "INSERT OR REPLACE INTO summaries (meeting_id, executive_summary) VALUES (?, ?)",
            (meeting.id, meeting.summary.executive_summary),
        )
        for item in meeting.action_items:
            conn.execute(
                "INSERT INTO action_items (meeting_id, owner, task, priority, status) VALUES (?, ?, ?, ?, ?)",
                (meeting.id, item.owner, item.task, item.priority, item.status),
            )
        for dl in meeting.deadlines:
            conn.execute(
                "INSERT INTO deadlines (meeting_id, description, date, type) VALUES (?, ?, ?, ?)",
                (meeting.id, dl.description, dl.date, dl.type),
            )
        for dec in meeting.decisions:
            conn.execute(
                "INSERT INTO decisions (meeting_id, decision, rationale) VALUES (?, ?, ?)",
                (meeting.id, dec.decision, dec.rationale),
            )
        conn.execute(
            "INSERT OR REPLACE INTO graph_data (meeting_id, graph_json) VALUES (?, ?)",
            (meeting.id, meeting.graph_data.graph_json),
        )
    logger.info(
        "Meeting saved",
        extra={"meeting_id": meeting.id, "title": meeting.title, "provider": meeting.provider},
    )


# ── Retrieve ────────────────────────────────────────────────────────────


def get_meeting_metadata(meeting_id: str) -> Optional[MeetingMetadata]:
    """Return metadata for a single meeting, or ``None``."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, title, date, participants, provider, processing_time, created_at FROM meetings WHERE id = ?",
            (meeting_id,),
        ).fetchone()
    if row is None:
        return None
    return MeetingMetadata(
        id=row["id"],
        title=row["title"],
        date=row["date"],
        participants=json.loads(row["participants"]),
        provider=row["provider"],
        processing_time=row["processing_time"],
        created_at=row["created_at"],
    )


def get_meeting_list() -> list[MeetingListItem]:
    """Return all meetings with summary counts for display."""
    items: list[MeetingListItem] = []
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT m.id, m.title, m.date, m.participants, m.provider, m.created_at,
                      (SELECT COUNT(*) FROM action_items WHERE meeting_id = m.id) AS action_count,
                      (SELECT COUNT(*) FROM decisions WHERE meeting_id = m.id) AS decision_count
               FROM meetings m
               ORDER BY m.created_at DESC""",
        ).fetchall()
    for row in rows:
        items.append(
            MeetingListItem(
                id=row["id"],
                title=row["title"],
                date=row["date"],
                participants=json.loads(row["participants"]),
                provider=row["provider"],
                created_at=row["created_at"],
                action_item_count=row["action_count"],
                decision_count=row["decision_count"],
            )
        )
    return items


def get_full_meeting(meeting_id: str) -> Optional[Meeting]:
    """Reconstruct a full ``Meeting`` from all related tables."""
    meta = get_meeting_metadata(meeting_id)
    if meta is None:
        return None

    with get_connection() as conn:
        t_row = conn.execute(
            "SELECT raw_text, cleaned_text FROM transcripts WHERE meeting_id = ?", (meeting_id,)
        ).fetchone()
        s_row = conn.execute(
            "SELECT executive_summary FROM summaries WHERE meeting_id = ?", (meeting_id,)
        ).fetchone()
        a_rows = conn.execute(
            "SELECT owner, task, priority, status FROM action_items WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchall()
        d_rows = conn.execute(
            "SELECT description, date, type FROM deadlines WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchall()
        dec_rows = conn.execute(
            "SELECT decision, rationale FROM decisions WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchall()
        g_row = conn.execute(
            "SELECT graph_json FROM graph_data WHERE meeting_id = ?", (meeting_id,)
        ).fetchone()

    return Meeting(
        id=meta.id,
        title=meta.title,
        date=meta.date,
        participants=meta.participants,
        provider=meta.provider,
        processing_time=meta.processing_time,
        transcript=Transcript(
            raw_text=t_row["raw_text"] if t_row else "",
            cleaned_text=t_row["cleaned_text"] if t_row else "",
        ),
        summary=Summary(
            executive_summary=s_row["executive_summary"] if s_row else "",
        ),
        action_items=[ActionItem(**dict(r)) for r in a_rows],
        deadlines=[Deadline(**dict(r)) for r in d_rows],
        decisions=[Decision(**dict(r)) for r in dec_rows],
        graph_data=GraphData(graph_json=g_row["graph_json"] if g_row else "{}"),
    )


# ── Delete ──────────────────────────────────────────────────────────────


def delete_meeting(meeting_id: str) -> bool:
    """Remove a meeting and all related data. Returns ``True`` on success."""
    with get_transaction() as conn:
        cursor = conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
        if cursor.rowcount == 0:
            return False
    logger.info("Meeting deleted", extra={"meeting_id": meeting_id})
    return True


# ── Search ──────────────────────────────────────────────────────────────


SEARCHABLE_COLUMNS: list[tuple[str, str, str]] = [
    ("meetings", "m", "title"),
    ("meetings", "m", "participants"),
    ("action_items", "ai", "task"),
    ("action_items", "ai", "owner"),
    ("decisions", "dec", "decision"),
    ("deadlines", "dl", "description"),
]


def search_meetings(query: str) -> list[MeetingListItem]:
    """Search across all searchable columns using LIKE.

    Returns deduplicated meeting list items sorted by date descending.
    """
    if not query.strip():
        return get_meeting_list()

    pattern = f"%{query.strip()}%"
    meeting_ids: set[str] = set()

    with get_connection() as conn:
        for table, alias, column in SEARCHABLE_COLUMNS:
            sql = f"SELECT DISTINCT meeting_id FROM {table} WHERE {alias}.{column} LIKE ?"
            if table == "meetings":
                sql = f"SELECT id FROM meetings WHERE {column} LIKE ?"
            rows = conn.execute(sql, (pattern,)).fetchall()
            meeting_ids.update(row[0] for row in rows)

    if not meeting_ids:
        return []

    placeholders = ",".join("?" for _ in meeting_ids)
    items: list[MeetingListItem] = []
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT m.id, m.title, m.date, m.participants, m.provider, m.created_at,
                       (SELECT COUNT(*) FROM action_items WHERE meeting_id = m.id) AS action_count,
                       (SELECT COUNT(*) FROM decisions WHERE meeting_id = m.id) AS decision_count
                FROM meetings m
                WHERE m.id IN ({placeholders})
                ORDER BY m.created_at DESC""",
            tuple(meeting_ids),
        ).fetchall()
    for row in rows:
        items.append(
            MeetingListItem(
                id=row["id"],
                title=row["title"],
                date=row["date"],
                participants=json.loads(row["participants"]),
                provider=row["provider"],
                created_at=row["created_at"],
                action_item_count=row["action_count"],
                decision_count=row["decision_count"],
            )
        )
    return items


# ── Utility ─────────────────────────────────────────────────────────────


def get_meeting_count() -> int:
    """Return the total number of stored meetings."""
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM meetings").fetchone()
    return row["cnt"] if row else 0


def get_all_participants() -> list[str]:
    """Return every unique participant name across all meetings."""
    seen: set[str] = set()
    with get_connection() as conn:
        rows = conn.execute("SELECT participants FROM meetings").fetchall()
    for row in rows:
        for name in json.loads(row["participants"]):
            seen.add(name)
    return sorted(seen)
