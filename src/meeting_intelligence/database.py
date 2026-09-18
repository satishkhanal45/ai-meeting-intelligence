"""SQLite database layer.

Handles schema creation, CRUD operations, and full-text search for all
meeting-related data. Uses WAL mode for better concurrent read performance.
"""

import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from meeting_intelligence.config import DB_PATH
from meeting_intelligence.logger import get_logger
from meeting_intelligence.models import (
    ActionItem,
    DatedDeadline,
    Deadline,
    Decision,
    GraphData,
    Meeting,
    MeetingListItem,
    MeetingMetadata,
    OwnedActionItem,
    PersonDetail,
    PersonSummary,
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
    (
        "normalise participants into their own tables",
        """
        CREATE TABLE IF NOT EXISTS people (
            id             TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            sort_key       TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meeting_participants (
            meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
            person_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            PRIMARY KEY (meeting_id, person_id)
        );
        CREATE INDEX IF NOT EXISTS idx_meeting_participants_person
            ON meeting_participants(person_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_people_sort_key ON people(sort_key);
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
    rebuild_people_index()
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
        _sync_participants(conn, meeting.id, meeting.participants)
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
            "SELECT id, owner, task, priority, status FROM action_items "
            "WHERE meeting_id = ? ORDER BY id",
            (meeting_id,),
        ).fetchall()
        d_rows = conn.execute(
            "SELECT id, description, date, type FROM deadlines "
            "WHERE meeting_id = ? ORDER BY id",
            (meeting_id,),
        ).fetchall()
        dec_rows = conn.execute(
            "SELECT id, decision, rationale FROM decisions "
            "WHERE meeting_id = ? ORDER BY id",
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
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='people'"
        ).fetchone():
            # The cascade removes the links; the people rows they pointed at
            # can be left with nothing to belong to.
            _prune_orphan_people(conn)
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


# ── Editing extracted items ─────────────────────────────────────────────
#
# Extraction output was previously write-once: a wrong owner, a hallucinated
# task or a completed item could not be corrected, which made the data a
# read-only report rather than something a team could work from.

#: Maps the editable child tables onto their columns and model.
_CHILD_TABLES: dict[str, tuple[str, ...]] = {
    "action_items": ("owner", "task", "priority", "status"),
    "deadlines": ("description", "date", "type"),
    "decisions": ("decision", "rationale"),
}


def _require_table(table: str) -> tuple[str, ...]:
    columns = _CHILD_TABLES.get(table)
    if columns is None:
        raise ValueError(f"Unknown table '{table}'. Expected one of {sorted(_CHILD_TABLES)}")
    return columns


def get_child_meeting_id(table: str, item_id: int) -> Optional[str]:
    """Return the meeting a child row belongs to, or ``None`` if absent."""
    _require_table(table)
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT meeting_id FROM {table} WHERE id = ?", (item_id,)
        ).fetchone()
    return row["meeting_id"] if row else None


def update_child(table: str, item_id: int, fields: dict[str, Any]) -> bool:
    """Apply a partial update to one child row.

    Only the columns declared for *table* are writable, so a caller cannot
    reassign ``meeting_id`` or ``id``. Returns ``False`` if the row is gone.
    """
    columns = _require_table(table)
    updates = {k: v for k, v in fields.items() if k in columns and v is not None}
    if not updates:
        return get_child_meeting_id(table, item_id) is not None

    assignments = ", ".join(f"{column} = ?" for column in updates)
    with get_transaction() as conn:
        cursor = conn.execute(
            f"UPDATE {table} SET {assignments} WHERE id = ?",
            (*updates.values(), item_id),
        )
        if cursor.rowcount == 0:
            return False
        row = conn.execute(
            f"SELECT meeting_id FROM {table} WHERE id = ?", (item_id,)
        ).fetchone()
        if row:
            _touch_meeting(conn, row["meeting_id"])
    logger.info(
        "Child row updated",
        extra={"table": table, "item_id": item_id, "fields": sorted(updates)},
    )
    return True


def create_child(table: str, meeting_id: str, fields: dict[str, Any]) -> Optional[int]:
    """Insert one child row against an existing meeting; return its new id."""
    columns = _require_table(table)
    values = {column: fields.get(column) for column in columns}
    values = {k: ("" if v is None else v) for k, v in values.items()}

    with get_transaction() as conn:
        exists = conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if exists is None:
            return None
        placeholders = ", ".join("?" for _ in values)
        cursor = conn.execute(
            f"INSERT INTO {table} (meeting_id, {', '.join(values)}) "
            f"VALUES (?, {placeholders})",
            (meeting_id, *values.values()),
        )
        item_id = cursor.lastrowid
        _touch_meeting(conn, meeting_id)
    logger.info("Child row created", extra={"table": table, "item_id": item_id})
    return item_id


def delete_child(table: str, item_id: int) -> bool:
    """Remove one child row. Returns ``False`` if it was already gone."""
    _require_table(table)
    with get_transaction() as conn:
        row = conn.execute(
            f"SELECT meeting_id FROM {table} WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return False
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))
        _touch_meeting(conn, row["meeting_id"])
    logger.info("Child row deleted", extra={"table": table, "item_id": item_id})
    return True


def _touch_meeting(conn: sqlite3.Connection, meeting_id: str) -> None:
    """Bump ``updated_at`` and refresh the search index after an edit."""
    conn.execute(
        "UPDATE meetings SET updated_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), meeting_id),
    )
    _index_meeting(conn, meeting_id)


def update_meeting_fields(meeting_id: str, title: Optional[str] = None) -> bool:
    """Edit a meeting's own editable fields. The title is often mis-inferred."""
    if title is None:
        return get_meeting_metadata(meeting_id) is not None
    with get_transaction() as conn:
        cursor = conn.execute(
            "UPDATE meetings SET title = ? WHERE id = ?", (title.strip(), meeting_id)
        )
        if cursor.rowcount == 0:
            return False
        _touch_meeting(conn, meeting_id)
    logger.info("Meeting updated", extra={"meeting_id": meeting_id})
    return True


# ── People ──────────────────────────────────────────────────────────────
#
# meetings.participants remains the authoritative per-meeting list, because it
# is what the pipeline produced. These tables are a queryable projection of it:
# without them, "every meeting Alice attended" means deserialising a JSON blob
# for every row in the table.


def _person_key(name: str) -> str:
    """Normalise a name for identity comparison."""
    return " ".join(name.lower().split())


def _sync_participants(conn: sqlite3.Connection, meeting_id: str, names: list[str]) -> None:
    """Rewrite one meeting's rows in the people projection."""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='people'"
    ).fetchone() is None:
        return

    conn.execute("DELETE FROM meeting_participants WHERE meeting_id = ?", (meeting_id,))
    for name in names:
        key = _person_key(name)
        if not key:
            continue
        row = conn.execute("SELECT id FROM people WHERE sort_key = ?", (key,)).fetchone()
        if row is None:
            person_id = hashlib.sha1(key.encode()).hexdigest()[:16]
            conn.execute(
                "INSERT OR IGNORE INTO people (id, canonical_name, sort_key) VALUES (?, ?, ?)",
                (person_id, name, key),
            )
        else:
            person_id = row["id"]
        conn.execute(
            "INSERT OR IGNORE INTO meeting_participants (meeting_id, person_id) VALUES (?, ?)",
            (meeting_id, person_id),
        )

    _prune_orphan_people(conn)


def _prune_orphan_people(conn: sqlite3.Connection) -> None:
    """Drop people who are no longer named in any meeting.

    A person only exists by virtue of attending something, so once their last
    meeting is deleted or they are edited out of its participants, the row
    should go too rather than lingering in the people list with zero meetings.
    """
    conn.execute(
        """DELETE FROM people
           WHERE id NOT IN (SELECT DISTINCT person_id FROM meeting_participants)"""
    )


def rebuild_people_index() -> None:
    """Repopulate the people projection from meetings.participants.

    Runs at startup so databases written before these tables existed, or by an
    older build, gain person pages without manual intervention.
    """
    with get_transaction() as conn:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='people'"
        ).fetchone() is None:
            return
        linked = conn.execute("SELECT COUNT(DISTINCT meeting_id) FROM meeting_participants").fetchone()[0]
        total = conn.execute(
            "SELECT COUNT(*) FROM meetings WHERE participants NOT IN ('[]', '')"
        ).fetchone()[0]
        if linked >= total:
            return
        for row in conn.execute("SELECT id, participants FROM meetings").fetchall():
            try:
                names = json.loads(row["participants"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(names, list):
                _sync_participants(conn, row["id"], [n for n in names if isinstance(n, str)])
        logger.info("People index rebuilt", extra={"meetings": total})


def list_people() -> list[PersonSummary]:
    """Every known person with their meeting and action-item counts."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.canonical_name,
                   COUNT(DISTINCT mp.meeting_id) AS meeting_count,
                   (SELECT COUNT(*) FROM action_items ai
                     WHERE LOWER(TRIM(ai.owner)) = p.sort_key) AS action_item_count,
                   (SELECT COUNT(*) FROM action_items ai
                     WHERE LOWER(TRIM(ai.owner)) = p.sort_key
                       AND ai.status IN ('open', 'in_progress')) AS open_action_item_count,
                   MAX(m.created_at) AS last_seen
            FROM people p
            LEFT JOIN meeting_participants mp ON mp.person_id = p.id
            LEFT JOIN meetings m ON m.id = mp.meeting_id
            GROUP BY p.id
            ORDER BY meeting_count DESC, p.canonical_name
            """
        ).fetchall()
    return [
        PersonSummary(
            id=row["id"],
            name=row["canonical_name"],
            meeting_count=row["meeting_count"],
            action_item_count=row["action_item_count"],
            open_action_item_count=row["open_action_item_count"],
            last_seen=row["last_seen"] or "",
        )
        for row in rows
    ]


def get_person(person_id: str) -> Optional[PersonDetail]:
    """One person with their meetings and every action item assigned to them."""
    with get_connection() as conn:
        person = conn.execute(
            "SELECT id, canonical_name, sort_key FROM people WHERE id = ?", (person_id,)
        ).fetchone()
        if person is None:
            return None

        meeting_rows = conn.execute(
            f"""{_MEETING_LIST_SQL}
                JOIN meeting_participants mp ON mp.meeting_id = m.id
                WHERE mp.person_id = ?
                ORDER BY m.created_at DESC""",
            (person_id,),
        ).fetchall()

        # Action items are matched by owner name, which is what the model
        # produced; there is no foreign key from an item to a person.
        item_rows = conn.execute(
            """SELECT ai.id, ai.owner, ai.task, ai.priority, ai.status,
                      ai.meeting_id, m.title AS meeting_title
               FROM action_items ai
               JOIN meetings m ON m.id = ai.meeting_id
               WHERE LOWER(TRIM(ai.owner)) = ?
               ORDER BY
                 CASE ai.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
                 CASE ai.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                 m.created_at DESC""",
            (person["sort_key"],),
        ).fetchall()

    return PersonDetail(
        id=person["id"],
        name=person["canonical_name"],
        meetings=[_row_to_list_item(row) for row in meeting_rows],
        action_items=[
            OwnedActionItem(
                id=row["id"],
                owner=row["owner"],
                task=row["task"],
                priority=row["priority"],
                status=row["status"],
                meeting_id=row["meeting_id"],
                meeting_title=row["meeting_title"],
            )
            for row in item_rows
        ],
    )


def list_action_items(
    status: str = "",
    owner: str = "",
    limit: int = 500,
    offset: int = 0,
) -> list[OwnedActionItem]:
    """Action items across every meeting, for a cross-meeting workspace."""
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("ai.status = ?")
        params.append(status)
    if owner:
        clauses.append("LOWER(TRIM(ai.owner)) = ?")
        params.append(_person_key(owner))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT ai.id, ai.owner, ai.task, ai.priority, ai.status,
                       ai.meeting_id, m.title AS meeting_title
                FROM action_items ai
                JOIN meetings m ON m.id = ai.meeting_id
                {where}
                ORDER BY
                  CASE ai.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
                  CASE ai.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                  m.created_at DESC
                LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
    return [
        OwnedActionItem(
            id=row["id"],
            owner=row["owner"],
            task=row["task"],
            priority=row["priority"],
            status=row["status"],
            meeting_id=row["meeting_id"],
            meeting_title=row["meeting_title"],
        )
        for row in rows
    ]


def list_deadlines(limit: int = 500) -> list[DatedDeadline]:
    """Every deadline with its meeting, for a timeline view."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT d.id, d.description, d.date, d.type, d.meeting_id,
                      m.title AS meeting_title
               FROM deadlines d
               JOIN meetings m ON m.id = d.meeting_id
               ORDER BY d.date, m.created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [
        DatedDeadline(
            id=row["id"],
            description=row["description"],
            date=row["date"],
            type=row["type"],
            meeting_id=row["meeting_id"],
            meeting_title=row["meeting_title"],
        )
        for row in rows
    ]
