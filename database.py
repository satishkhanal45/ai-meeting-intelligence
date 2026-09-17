"""SQLite database layer.

Handles schema creation, CRUD operations, and full-text search for all
meeting-related data. Uses WAL mode for better concurrent read performance.
"""

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

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
-- The list and search queries both ORDER BY created_at; date was the only
-- indexed column, so every listing sorted with a filesort.
CREATE INDEX IF NOT EXISTS idx_meetings_created_at ON meetings(created_at DESC);
"""


# ── Migrations ──────────────────────────────────────────────────────────
#
# Each entry is applied in order to a database whose ``PRAGMA user_version``
# is below its index. ``CREATE TABLE IF NOT EXISTS`` alone cannot evolve a
# schema that already holds data, so column additions and the FTS index live
# here instead.
#
# APPEND ONLY. The list index *is* the schema version, so inserting an entry
# in the middle renumbers every migration after it: a database already past
# that point will skip the new entry while reporting itself fully migrated.
# To change an applied migration, add a new one that corrects it.

MIGRATIONS: list[tuple[str, str]] = [
    (
        "add processing outcome columns",
        """
        ALTER TABLE meetings ADD COLUMN model TEXT NOT NULL DEFAULT '';
        ALTER TABLE meetings ADD COLUMN chunk_total INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE meetings ADD COLUMN chunk_failures INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE meetings ADD COLUMN input_tokens INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE meetings ADD COLUMN output_tokens INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE meetings ADD COLUMN updated_at TEXT NOT NULL DEFAULT '';
        """,
    ),
    (
        "add full-text search index",
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS meetings_fts USING fts5(
            meeting_id UNINDEXED,
            title,
            participants,
            summary,
            transcript,
            tasks,
            decisions,
            deadlines,
            tokenize = 'porter unicode61'
        );
        """,
    ),
    (
        "record which providers actually served a run",
        """
        ALTER TABLE meetings ADD COLUMN served_by TEXT NOT NULL DEFAULT '';
        """,
    ),
]


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Bring *conn*'s schema up to ``len(MIGRATIONS)``."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= len(MIGRATIONS):
        return

    for index in range(version, len(MIGRATIONS)):
        description, script = MIGRATIONS[index]
        for statement in filter(None, (s.strip() for s in script.split(";"))):
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                # ALTER TABLE ADD COLUMN is not idempotent; tolerate a column
                # that a previous partial run already added.
                if "duplicate column name" not in str(exc).lower():
                    raise
        # "name" is a reserved LogRecord attribute and cannot go in `extra`.
        logger.info(
            "Migration applied",
            extra={"version": index + 1, "migration": description},
        )

    conn.execute(f"PRAGMA user_version = {len(MIGRATIONS)}")


def init_db() -> None:
    """Create all tables if they do not exist, then run pending migrations."""
    with get_transaction() as conn:
        conn.executescript(SCHEMA_SQL)
        _apply_migrations(conn)
    rebuild_search_index()
    logger.info("Database initialised", extra={"path": DB_PATH})


# ── Insert ──────────────────────────────────────────────────────────────


def insert_meeting(meeting: Meeting) -> None:
    """Persist a complete ``Meeting`` object and all its relations."""
    with get_transaction() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO meetings
               (id, title, date, participants, provider, processing_time,
                model, chunk_total, chunk_failures, input_tokens, output_tokens,
                served_by, updated_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       COALESCE((SELECT created_at FROM meetings WHERE id = ?),
                                datetime('now')))""",
            (
                meeting.id,
                meeting.title,
                meeting.date,
                json.dumps(meeting.participants),
                meeting.provider,
                meeting.processing_time,
                meeting.model,
                meeting.chunk_total,
                meeting.chunk_failures,
                meeting.input_tokens,
                meeting.output_tokens,
                json.dumps(meeting.served_by),
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                meeting.id,
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
        # The child tables use AUTOINCREMENT ids, so ``INSERT OR REPLACE`` cannot
        # de-duplicate them. Clear them first, otherwise re-saving a meeting under
        # the same id appends a second copy of every row.
        for table in ("action_items", "deadlines", "decisions"):
            conn.execute(f"DELETE FROM {table} WHERE meeting_id = ?", (meeting.id,))
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
        _index_meeting(conn, meeting.id)
    logger.info(
        "Meeting saved",
        extra={"meeting_id": meeting.id, "title": meeting.title, "provider": meeting.provider},
    )


# ── Retrieve ────────────────────────────────────────────────────────────


def get_meeting_metadata(meeting_id: str) -> Optional[MeetingMetadata]:
    """Return metadata for a single meeting, or ``None``."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT id, title, date, participants, provider, model, processing_time,
                      created_at, updated_at, chunk_total, chunk_failures,
                      input_tokens, output_tokens, served_by
               FROM meetings WHERE id = ?""",
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
        model=row["model"],
        processing_time=row["processing_time"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        chunk_total=row["chunk_total"],
        chunk_failures=row["chunk_failures"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        served_by=json.loads(row["served_by"]) if row["served_by"] else [],
    )


_MEETING_LIST_SQL = """
SELECT m.id, m.title, m.date, m.participants, m.provider, m.created_at,
       m.chunk_failures, m.chunk_total,
       (SELECT COUNT(*) FROM action_items WHERE meeting_id = m.id) AS action_count,
       (SELECT COUNT(*) FROM decisions WHERE meeting_id = m.id) AS decision_count
FROM meetings m
"""


def _row_to_list_item(row: sqlite3.Row) -> MeetingListItem:
    """Map a ``_MEETING_LIST_SQL`` row onto a ``MeetingListItem``."""
    return MeetingListItem(
        id=row["id"],
        title=row["title"],
        date=row["date"],
        participants=json.loads(row["participants"]),
        provider=row["provider"],
        created_at=row["created_at"],
        action_item_count=row["action_count"],
        decision_count=row["decision_count"],
        chunk_failures=row["chunk_failures"],
        chunk_total=row["chunk_total"],
    )


def get_meeting_list(limit: int = 100, offset: int = 0) -> list[MeetingListItem]:
    """Return a page of meetings with summary counts for display.

    Unbounded listing loaded every meeting — with two correlated subqueries
    each and the full participant blob — on every dashboard render.
    """
    with get_connection() as conn:
        rows = conn.execute(
            f"{_MEETING_LIST_SQL} ORDER BY m.created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [_row_to_list_item(row) for row in rows]


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
        model=meta.model,
        processing_time=meta.processing_time,
        chunk_total=meta.chunk_total,
        chunk_failures=meta.chunk_failures,
        input_tokens=meta.input_tokens,
        output_tokens=meta.output_tokens,
        served_by=meta.served_by,
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
        if _has_fts(conn):
            conn.execute("DELETE FROM meetings_fts WHERE meeting_id = ?", (meeting_id,))
    logger.info("Meeting deleted", extra={"meeting_id": meeting_id})
    return True


# ── Search ──────────────────────────────────────────────────────────────


# Each entry is ``(table, column)``. Columns on ``meetings`` are matched against
# ``meetings.id``; columns on child tables are matched against ``meeting_id``.
# Retained for the LIKE fallback used when FTS5 is unavailable.
SEARCHABLE_COLUMNS: list[tuple[str, str]] = [
    ("meetings", "title"),
    ("meetings", "participants"),
    ("action_items", "task"),
    ("action_items", "owner"),
    ("decisions", "decision"),
    ("deadlines", "description"),
]

_FTS_DOCUMENT_SQL = """
SELECT m.id AS meeting_id,
       m.title AS title,
       m.participants AS participants,
       COALESCE(s.executive_summary, '') AS summary,
       COALESCE(t.cleaned_text, '') AS transcript,
       COALESCE((SELECT GROUP_CONCAT(owner || ' ' || task, ' ')
                 FROM action_items WHERE meeting_id = m.id), '') AS tasks,
       COALESCE((SELECT GROUP_CONCAT(decision || ' ' || rationale, ' ')
                 FROM decisions WHERE meeting_id = m.id), '') AS decisions,
       COALESCE((SELECT GROUP_CONCAT(description || ' ' || date, ' ')
                 FROM deadlines WHERE meeting_id = m.id), '') AS deadlines
FROM meetings m
LEFT JOIN summaries s ON s.meeting_id = m.id
LEFT JOIN transcripts t ON t.meeting_id = m.id
"""

_FTS_COLUMNS = (
    "meeting_id, title, participants, summary, transcript, tasks, decisions, deadlines"
)


def _has_fts(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meetings_fts'"
    ).fetchone()
    return row is not None


def _index_meeting(conn: sqlite3.Connection, meeting_id: str) -> None:
    """Refresh one meeting's row in the FTS index."""
    if not _has_fts(conn):
        return
    conn.execute("DELETE FROM meetings_fts WHERE meeting_id = ?", (meeting_id,))
    conn.execute(
        f"INSERT INTO meetings_fts ({_FTS_COLUMNS}) "
        f"{_FTS_DOCUMENT_SQL} WHERE m.id = ?",
        (meeting_id,),
    )


def rebuild_search_index() -> None:
    """Repopulate the FTS index from the source tables.

    Called on startup so that databases predating the index, or rows written
    by an older build, become searchable without manual intervention.
    """
    with get_transaction() as conn:
        if not _has_fts(conn):
            return
        indexed = conn.execute("SELECT COUNT(*) FROM meetings_fts").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
        if indexed == total:
            return
        conn.execute("DELETE FROM meetings_fts")
        conn.execute(f"INSERT INTO meetings_fts ({_FTS_COLUMNS}) {_FTS_DOCUMENT_SQL}")
        logger.info("Search index rebuilt", extra={"meetings": total})


def _fts_query(query: str) -> str:
    """Turn user input into a safe FTS5 MATCH expression.

    Every term is quoted, so punctuation that FTS5 would read as operator
    syntax cannot produce a syntax error or an unintended query. A trailing
    ``*`` on the final term gives prefix matching as the user types.
    """
    terms = [t for t in re.split(r"\s+", query.strip()) if t]
    if not terms:
        return ""
    quoted = ['"' + t.replace('"', '""') + '"' for t in terms]
    quoted[-1] = quoted[-1] + "*"
    return " ".join(quoted)


def search_meetings(query: str, limit: int = 100, offset: int = 0) -> list[MeetingListItem]:
    """Full-text search across titles, summaries, transcripts and extracted data.

    Results are ranked by BM25 relevance. Falls back to the previous LIKE scan
    if the FTS index is unavailable or the query cannot be expressed in FTS5.
    """
    if not query.strip():
        return get_meeting_list(limit=limit, offset=offset)

    match = _fts_query(query)
    if match:
        with get_connection() as conn:
            if _has_fts(conn):
                try:
                    rows = conn.execute(
                        f"""{_MEETING_LIST_SQL}
                            JOIN meetings_fts f ON f.meeting_id = m.id
                            WHERE meetings_fts MATCH ?
                            ORDER BY bm25(meetings_fts, 0.0, 10.0, 5.0, 3.0, 1.0, 4.0, 4.0, 2.0)
                            LIMIT ? OFFSET ?""",
                        (match, limit, offset),
                    ).fetchall()
                    return [_row_to_list_item(row) for row in rows]
                except sqlite3.OperationalError as exc:
                    logger.warning(
                        "FTS query failed, falling back to LIKE",
                        extra={"error": str(exc), "query": query},
                    )

    return _search_meetings_like(query, limit=limit, offset=offset)


def _search_meetings_like(query: str, limit: int = 100, offset: int = 0) -> list[MeetingListItem]:
    """Substring search fallback for databases without the FTS index."""
    pattern = f"%{query.strip()}%"
    meeting_ids: set[str] = set()

    with get_connection() as conn:
        for table, column in SEARCHABLE_COLUMNS:
            id_column = "id" if table == "meetings" else "meeting_id"
            rows = conn.execute(
                f"SELECT DISTINCT {id_column} FROM {table} WHERE {column} LIKE ?",
                (pattern,),
            ).fetchall()
            meeting_ids.update(row[0] for row in rows)

    if not meeting_ids:
        return []

    placeholders = ",".join("?" for _ in meeting_ids)
    with get_connection() as conn:
        rows = conn.execute(
            f"""{_MEETING_LIST_SQL} WHERE m.id IN ({placeholders})
                ORDER BY m.created_at DESC LIMIT ? OFFSET ?""",
            (*meeting_ids, limit, offset),
        ).fetchall()
    return [_row_to_list_item(row) for row in rows]


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
