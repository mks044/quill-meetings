"""SQLite storage. One connection per request (FastAPI dependency), WAL mode,
FTS5 index over segment text + session titles/overviews for global search."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,              -- quill dir name, e.g. 2026.07.29-0114
    started_at TEXT NOT NULL,         -- ISO8601 local
    duration_s REAL NOT NULL DEFAULT 0,
    engine TEXT,
    title TEXT,                       -- AI; falls back to id
    overview_md TEXT,                 -- AI topic-sectioned markdown
    summary_json TEXT,                -- AI {brief, decisions, open_questions}
    outline_json TEXT,                -- AI [{ms,label}]
    keywords_json TEXT,               -- AI [str]
    tags_json TEXT,                   -- AI [str]
    ai_status TEXT NOT NULL DEFAULT 'pending',  -- transcribing|transcription_failed|pending|running|done|failed
    ai_error TEXT,
    ai_attempts INTEGER NOT NULL DEFAULT 0,
    ai_retry_at TEXT,
    artifacts_revision INTEGER NOT NULL DEFAULT 0,
    notes_revision INTEGER NOT NULL DEFAULT 0,
    notes_edited_at TEXT,
    segments_hash TEXT,
    has_audio_mic INTEGER NOT NULL DEFAULT 0,
    has_audio_system INTEGER NOT NULL DEFAULT 0,
    has_audio_mixed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS segments (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    speaker TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (session_id, idx)
);

CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    text, session_id UNINDEXED, idx UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    assignee TEXT,
    source_ms INTEGER,
    source TEXT NOT NULL DEFAULT 'ai',   -- 'ai' | 'manual'
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS artifacts_lang (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    lang TEXT NOT NULL,
    title TEXT,
    overview_md TEXT,
    summary_json TEXT,
    outline_json TEXT,
    keywords_json TEXT,
    notes_revision INTEGER NOT NULL DEFAULT 0,
    notes_edited_at TEXT,
    PRIMARY KEY (session_id, lang)
);

CREATE TABLE IF NOT EXISTS share_tokens (
    token TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    lang TEXT NOT NULL DEFAULT 'en',
    access_level TEXT NOT NULL DEFAULT 'summary',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS deleted_sessions (
    id TEXT PRIMARY KEY,
    deleted_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,              -- 'session' | 'global'
    session_id TEXT,                  -- null for global
    role TEXT NOT NULL,               -- 'user' | 'assistant'
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init() -> None:
    with closing_conn() as conn:
        conn.executescript(SCHEMA)
        # Migrations for pre-existing DBs (ALTERs are idempotent via try).
        for stmt in (
            "ALTER TABLE sessions ADD COLUMN segments_hash TEXT",
            "ALTER TABLE actions ADD COLUMN source TEXT NOT NULL DEFAULT 'ai'",
            "ALTER TABLE sessions ADD COLUMN has_audio_mixed INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE actions ADD COLUMN ru_text TEXT",
            "ALTER TABLE sessions ADD COLUMN ai_attempts INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN ai_retry_at TEXT",
            "ALTER TABLE sessions ADD COLUMN summary_json TEXT",
            "ALTER TABLE artifacts_lang ADD COLUMN summary_json TEXT",
            "ALTER TABLE sessions ADD COLUMN artifacts_revision INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN notes_edited_at TEXT",
            "ALTER TABLE artifacts_lang ADD COLUMN notes_edited_at TEXT",
            "ALTER TABLE sessions ADD COLUMN notes_revision INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE artifacts_lang ADD COLUMN notes_revision INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass
        # Preserve the behavior of links that were already sent before scoped
        # sharing existed. New databases/defaults and all application inserts
        # are summary-only; only rows present during this one-time ALTER become
        # full-access for backwards compatibility.
        try:
            conn.execute("ALTER TABLE share_tokens ADD COLUMN access_level TEXT")
            conn.execute(
                "UPDATE share_tokens SET access_level='full' WHERE access_level IS NULL")
        except sqlite3.OperationalError:
            pass


@contextmanager
def closing_conn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_session(conn, session_id, started_at, duration_s, engine,
                   segments, has_mic, has_system, has_mixed=0) -> None:
    """Idempotent ingest: replaces segments, preserves AI artifacts + action
    done-states unless the transcript actually changed."""
    new_hash = segments_hash(segments)
    old = conn.execute(
        "SELECT segments_hash FROM sessions WHERE id=?", (session_id,)).fetchone()
    changed = (old is None) or (old["segments_hash"] != new_hash)

    conn.execute(
        """INSERT INTO sessions (id, started_at, duration_s, engine, has_audio_mic, has_audio_system, has_audio_mixed)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             started_at=excluded.started_at, duration_s=excluded.duration_s,
             engine=excluded.engine, has_audio_mic=excluded.has_audio_mic,
             has_audio_system=excluded.has_audio_system,
             has_audio_mixed=excluded.has_audio_mixed""",
        (session_id, started_at, duration_s, engine, has_mic, has_system, has_mixed))

    if changed:
        conn.execute("DELETE FROM segments WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM segments_fts WHERE session_id=?", (session_id,))
        conn.executemany(
            "INSERT INTO segments (session_id, idx, speaker, start_ms, end_ms, text)"
            " VALUES (?,?,?,?,?,?)",
            [(session_id, i, s["speaker"], s["start_ms"], s["end_ms"], s["text"])
             for i, s in enumerate(segments)])
        conn.executemany(
            "INSERT INTO segments_fts (text, session_id, idx) VALUES (?,?,?)",
            [(s["text"], session_id, i) for i, s in enumerate(segments)])
        conn.execute(
            """UPDATE sessions SET ai_status='pending', ai_error=NULL,
               ai_attempts=0, ai_retry_at=NULL, segments_hash=? WHERE id=?""",
            (new_hash, session_id))


def upsert_local_session(conn, session_id, started_at, duration_s,
                         state="transcribing", error=None) -> None:
    """Publish a finalized capture before its local transcript exists.

    A stale metadata-only announcement must never demote a row that already has
    transcript segments, so conflict updates are conditional on segments_hash
    still being null. Normal transcript ingest later promotes the row to
    ``pending`` through ``upsert_session``.
    """
    status = "transcription_failed" if state == "failed" else "transcribing"
    detail = str(error)[:500] if status == "transcription_failed" and error else None
    conn.execute(
        """INSERT INTO sessions
             (id, started_at, duration_s, engine, ai_status, ai_error)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             started_at=excluded.started_at,
             duration_s=excluded.duration_s,
             engine=excluded.engine,
             ai_status=excluded.ai_status,
             ai_error=excluded.ai_error
           WHERE sessions.segments_hash IS NULL""",
        (session_id, started_at, duration_s, "whisper (local processing)", status, detail))


def segments_hash(segments) -> str:
    h = hashlib.sha256()
    for s in segments:
        h.update(f"{s['speaker']}|{s['start_ms']}|{s['end_ms']}|{s['text']}\n".encode())
    return h.hexdigest()


def save_ai_artifacts(conn, session_id, art, expected_hash: str | None = None) -> bool:
    """Persist AI output. With expected_hash, refuse when the transcript moved
    underneath the run (stale artifacts must never overwrite fresh segments).
    Manual actions survive; AI actions are replaced but carry done-state
    forward by exact text match."""
    if expected_hash is not None:
        cur = conn.execute(
            "SELECT segments_hash FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not cur or cur["segments_hash"] != expected_hash:
            return False
    _save_ai_artifacts(conn, session_id, art)
    return True


def _save_ai_artifacts(conn, session_id, art) -> None:
    conn.execute(
        """UPDATE sessions SET title=?, overview_md=?, summary_json=?, outline_json=?,
           keywords_json=?, tags_json=?, ai_status='done', ai_error=NULL,
           ai_attempts=0, ai_retry_at=NULL,
           artifacts_revision=artifacts_revision+1,
           notes_revision=notes_revision+1,
           notes_edited_at=NULL
           WHERE id=?""",
        (art["title"], art["overview_md"], json.dumps(art.get("summary", {})),
         json.dumps(art.get("outline", [])),
         json.dumps(art.get("keywords", [])), json.dumps(art.get("tags", [])),
         session_id))
    conn.execute("DELETE FROM artifacts_lang WHERE session_id=?", (session_id,))
    done_texts = {r["text"] for r in conn.execute(
        "SELECT text FROM actions WHERE session_id=? AND source='ai' AND done=1",
        (session_id,))}
    conn.execute("DELETE FROM actions WHERE session_id=? AND source='ai'", (session_id,))
    for a in art.get("actions", []):
        conn.execute(
            "INSERT INTO actions (session_id, text, assignee, source_ms, source, done)"
            " VALUES (?,?,?,?, 'ai', ?)",
            (session_id, a["text"], a.get("assignee"), a.get("source_ms"),
             int(a["text"] in done_texts)))


def save_manual_notes(conn, session_id: str, lang: str, title: str,
                      overview_md: str, summary: dict,
                      expected_revision: int) -> bool:
    """Persist an owner edit without touching transcript/audio/action truth.

    English is the canonical source for translations, so an English edit bumps
    the artifact revision and invalidates every derived language atomically.
    A translated edit stays local to that language. Returns False when the
    requested row vanished or (for translated edits) was never generated.
    """
    payload = json.dumps(summary, ensure_ascii=False)
    if lang == "en":
        cur = conn.execute(
            """UPDATE sessions SET title=?, overview_md=?, summary_json=?,
               artifacts_revision=artifacts_revision+1,
               notes_revision=notes_revision+1,
               notes_edited_at=datetime('now')
               WHERE id=? AND ai_status='done' AND notes_revision=?""",
            (title, overview_md, payload, session_id, expected_revision))
        if cur.rowcount == 0:
            return False
        # Machine translations are derived cache and must be rebuilt. A row the
        # owner explicitly edited is independent content: preserve it, then
        # refresh only its action translations on next RU use.
        conn.execute(
            "DELETE FROM artifacts_lang WHERE session_id=? AND notes_edited_at IS NULL",
            (session_id,))
        conn.execute("UPDATE actions SET ru_text=NULL WHERE session_id=?", (session_id,))
        return True
    cur = conn.execute(
        """UPDATE artifacts_lang SET title=?, overview_md=?, summary_json=?,
           notes_revision=notes_revision+1, notes_edited_at=datetime('now')
           WHERE session_id=? AND lang=? AND notes_revision=?""",
        (title, overview_md, payload, session_id, lang, expected_revision))
    return cur.rowcount > 0


def session_row_to_dict(row) -> dict:
    d = dict(row)
    for k in ("outline_json", "keywords_json", "tags_json"):
        d[k.replace("_json", "")] = json.loads(d.pop(k) or "[]")
    d["summary"] = json.loads(d.pop("summary_json", None) or "{}")
    d["notes_edited"] = bool(d.pop("notes_edited_at", None))
    return d
