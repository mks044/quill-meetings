"""Session ingest: parse a synced quill session dir into the DB and kick the
AI pipeline. The filesystem is the source of truth (same philosophy as quill
itself) — ingest is idempotent and re-runnable."""

import asyncio
import datetime as dt
import json
import logging
from pathlib import Path

from . import ai, config, db

log = logging.getLogger("quill.ingest")

_ai_tasks: dict[str, asyncio.Task] = {}


def parse_started_at(session_id: str) -> str:
    # quill dir names: 2026.07.29-0114  (local time HHMM)
    try:
        d = dt.datetime.strptime(session_id[:15], "%Y.%m.%d-%H%M")
        return d.isoformat()
    except ValueError:
        return dt.datetime.now().isoformat()


def read_session_dir(session_id: str) -> dict | None:
    sdir = config.SESSIONS_DIR / session_id
    tpath = sdir / "transcript.json"
    if not tpath.exists():
        return None
    t = json.loads(tpath.read_text())
    segments = [
        {"speaker": s["speaker"], "start_ms": s["start_ms"],
         "end_ms": s["end_ms"], "text": s["text"]}
        for s in t.get("segments", [])
    ]
    duration_s = max((s["end_ms"] for s in segments), default=0) / 1000
    return {
        "id": session_id,
        "started_at": parse_started_at(session_id),
        "duration_s": duration_s,
        "engine": f"{t.get('engine', '?')} ({t.get('model', '?')})",
        "segments": segments,
        "has_mic": (sdir / "mic.m4a").exists(),
        "has_system": (sdir / "system.m4a").exists(),
        "has_mixed": (sdir / "mixed.m4a").exists(),
    }


def ingest_session(session_id: str) -> dict:
    with db.closing_conn() as conn:
        if conn.execute("SELECT 1 FROM deleted_sessions WHERE id=?", (session_id,)).fetchone():
            sdir = config.SESSIONS_DIR / session_id
            if sdir.exists():
                import shutil
                shutil.rmtree(sdir)
            return {"id": session_id, "segments": 0, "ai_status": "tombstoned"}
    data = read_session_dir(session_id)
    if data is None:
        raise FileNotFoundError(f"no transcript.json for {session_id}")
    with db.closing_conn() as conn:
        db.upsert_session(
            conn, data["id"], data["started_at"], data["duration_s"],
            data["engine"], data["segments"], data["has_mic"], data["has_system"],
            data["has_mixed"])
        status = conn.execute(
            "SELECT ai_status FROM sessions WHERE id=?", (session_id,)
        ).fetchone()["ai_status"]
    return {"id": session_id, "segments": len(data["segments"]), "ai_status": status}


def schedule_ai(session_id: str) -> bool:
    """Run the AI pipeline for a session in the background (single-flight)."""
    task = _ai_tasks.get(session_id)
    if task and not task.done():
        return False
    _ai_tasks[session_id] = asyncio.get_running_loop().create_task(
        _ai_pipeline(session_id))
    return True


async def _ai_pipeline(session_id: str) -> None:
    with db.closing_conn() as conn:
        conn.execute(
            "UPDATE sessions SET ai_status='running', ai_error=NULL WHERE id=?",
            (session_id,))
        rows = conn.execute(
            "SELECT speaker, start_ms, end_ms, text FROM segments"
            " WHERE session_id=? ORDER BY idx", (session_id,)).fetchall()
        started_at = conn.execute(
            "SELECT started_at FROM sessions WHERE id=?", (session_id,)
        ).fetchone()["started_at"]
    segments = [dict(r) for r in rows]
    run_hash = db.segments_hash(segments)
    if not segments:
        with db.closing_conn() as conn:
            conn.execute(
                "UPDATE sessions SET ai_status='failed', ai_error='empty transcript' WHERE id=?",
                (session_id,))
        return
    try:
        art = await ai.generate_artifacts(session_id, started_at, segments)
        with db.closing_conn() as conn:
            saved = db.save_ai_artifacts(conn, session_id, art, expected_hash=run_hash)
        if saved:
            log.info("AI artifacts done for %s", session_id)
        else:
            log.info("transcript changed mid-run for %s — rescheduling", session_id)
            schedule_ai(session_id)
            return
    except Exception as e:  # noqa: BLE001 — status must always land in the DB
        log.exception("AI pipeline failed for %s", session_id)
        with db.closing_conn() as conn:
            conn.execute(
                "UPDATE sessions SET ai_status='failed', ai_error=? WHERE id=?",
                (str(e)[:500], session_id))


def scan_all() -> list[str]:
    """Ingest every session dir on disk (startup catch-up). Returns ids needing AI."""
    # A restart kills in-memory AI tasks: anything stuck 'running' is dead.
    with db.closing_conn() as conn:
        conn.execute("UPDATE sessions SET ai_status='pending' WHERE ai_status='running'")
    if not config.SESSIONS_DIR.exists():
        return []
    pending = []
    for sdir in sorted(config.SESSIONS_DIR.iterdir()):
        if not sdir.is_dir():
            continue
        try:
            ingest_session(sdir.name)
        except FileNotFoundError:
            continue
        except Exception:
            log.exception("ingest failed for %s", sdir.name)
            continue
        with db.closing_conn() as conn:
            st = conn.execute(
                "SELECT ai_status FROM sessions WHERE id=?", (sdir.name,)
            ).fetchone()
            if st and st["ai_status"] in ("pending", "failed"):
                pending.append(sdir.name)
    return pending
