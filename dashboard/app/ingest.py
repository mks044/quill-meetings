"""Session ingest: parse a synced quill session dir into the DB and kick the
AI pipeline. The filesystem is the source of truth (same philosophy as quill
itself) — ingest is idempotent and re-runnable."""

import asyncio
import datetime as dt
import json
import logging
import math
from pathlib import Path

from . import ai, config, db

log = logging.getLogger("quill.ingest")

_ai_tasks: dict[str, asyncio.Task] = {}

_AUTH_FAILURES = (
    "access token", "refresh token", "authentication", "not logged in",
    "log in again", "login required", "unauthorized",
)
_TRANSIENT_FAILURES = (
    "timed out", "timeout", "connection", "temporarily unavailable",
    "rate limit", "too many requests", "http 429", "http 500", "http 502",
    "http 503", "http 504",
)


def retry_delay_seconds(error: str, attempt: int) -> int | None:
    """Return a bounded retry delay for a failed notetaker run.

    Authentication failures retry indefinitely but at most hourly: repairing
    the server credential must recover stranded notes without a manual API
    call. Network/service failures get five attempts. Unexpected model/output
    failures get two automatic retries before remaining visibly failed.
    """
    message = error.lower()
    exponent = max(0, attempt - 1)
    if any(fragment in message for fragment in _AUTH_FAILURES):
        return min(300 * (2 ** min(exponent, 4)), 3600)
    if any(fragment in message for fragment in _TRANSIENT_FAILURES):
        return min(60 * (2 ** min(exponent, 5)), 1800) if attempt <= 5 else None
    return min(120 * (2 ** exponent), 900) if attempt <= 2 else None


def _utc_after(seconds: int) -> str:
    value = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)
    return value.strftime("%Y-%m-%d %H:%M:%S")


def due_ai_sessions() -> list[str]:
    """Return failed sessions whose persisted retry deadline has arrived."""
    with db.closing_conn() as conn:
        rows = conn.execute(
            """SELECT id FROM sessions
               WHERE ai_status='failed' AND ai_retry_at IS NOT NULL
                 AND datetime(ai_retry_at) <= datetime('now')
               ORDER BY started_at"""
        ).fetchall()
    return [row["id"] for row in rows]


async def retry_loop(poll_seconds: int = 60) -> None:
    """Persisted retry worker; safe across dashboard restarts."""
    while True:
        try:
            for session_id in due_ai_sessions():
                if schedule_ai(session_id):
                    log.info("retry deadline reached for %s", session_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # keep one DB/runtime error from killing the worker
            log.exception("notetaker retry scan failed")
        await asyncio.sleep(poll_seconds)


def parse_started_at(session_id: str, metadata_value: str | None = None) -> str:
    # The manifest timestamp is an exact ISO8601 instant. Folder names use the
    # Mac's wall clock and have no offset, so they are only a legacy fallback.
    if metadata_value:
        try:
            parsed = dt.datetime.fromisoformat(metadata_value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.isoformat()
        except (TypeError, ValueError):
            pass
    # quill dir names: 2026.07.29-0114  (local time HHMM)
    try:
        d = dt.datetime.strptime(session_id[:15], "%Y.%m.%d-%H%M")
        return d.isoformat()
    except ValueError:
        return dt.datetime.now().isoformat()


def _capture_duration(metadata: dict) -> float:
    try:
        value = float(metadata.get("duration_seconds", 0))
        return value if math.isfinite(value) and value >= 0 else 0
    except (TypeError, ValueError):
        return 0


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        log.warning("ignoring invalid JSON metadata at %s", path)
        return {}


def read_session_dir(session_id: str) -> dict | None:
    sdir = config.SESSIONS_DIR / session_id
    metadata = _read_json(sdir / "meta.json")
    tpath = sdir / "transcript.json"
    if not tpath.exists():
        # Never expose an active recording. A complete recorder manifest is
        # authoritative enough to create a dashboard placeholder while local
        # Whisper is still running.
        if metadata.get("state") != "complete":
            return None
        pipeline = _read_json(sdir / "transcription.json")
        local_state = pipeline.get("state", "transcribing")
        return {
            "id": session_id,
            "started_at": parse_started_at(session_id, metadata.get("started")),
            "duration_s": _capture_duration(metadata),
            "transcript_ready": False,
            "local_state": local_state,
            "local_error": pipeline.get("error"),
        }

    t = json.loads(tpath.read_text())
    segments = [
        {"speaker": s["speaker"], "start_ms": s["start_ms"],
         "end_ms": s["end_ms"], "text": s["text"]}
        for s in t.get("segments", [])
    ]
    transcript_duration = max((s["end_ms"] for s in segments), default=0) / 1000
    return {
        "id": session_id,
        "started_at": parse_started_at(session_id, metadata.get("started")),
        "duration_s": max(transcript_duration, _capture_duration(metadata)),
        "engine": f"{t.get('engine', '?')} ({t.get('model', '?')})",
        "segments": segments,
        "transcript_ready": True,
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
        raise FileNotFoundError(f"no finalized session data for {session_id}")
    with db.closing_conn() as conn:
        if data["transcript_ready"]:
            db.upsert_session(
                conn, data["id"], data["started_at"], data["duration_s"],
                data["engine"], data["segments"], data["has_mic"], data["has_system"],
                data["has_mixed"])
        else:
            db.upsert_local_session(
                conn, data["id"], data["started_at"], data["duration_s"],
                data["local_state"], data["local_error"])
        status = conn.execute(
            "SELECT ai_status FROM sessions WHERE id=?", (session_id,)
        ).fetchone()["ai_status"]
    return {
        "id": session_id,
        "segments": len(data.get("segments", [])),
        "ai_status": status,
    }


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
            """UPDATE sessions SET ai_status='running', ai_error=NULL,
               ai_retry_at=NULL, ai_attempts=ai_attempts+1 WHERE id=?""",
            (session_id,))
        session = conn.execute(
            """SELECT started_at, ai_attempts, speaker_me_label,
                      speaker_them_label, speakers_revision
               FROM sessions WHERE id=?""", (session_id,)
        ).fetchone()
        if not session:
            return
        rows = conn.execute(
            "SELECT speaker, start_ms, end_ms, text FROM segments"
            " WHERE session_id=? ORDER BY idx", (session_id,)).fetchall()
        started_at = session["started_at"]
        attempt = session["ai_attempts"]
        speaker_labels = {
            "me": db.stored_speaker_label(session["speaker_me_label"]),
            "them": db.stored_speaker_label(session["speaker_them_label"]),
        }
        speakers_revision = session["speakers_revision"]
    segments = [dict(r) for r in rows]
    run_hash = db.segments_hash(segments)
    if not segments:
        with db.closing_conn() as conn:
            conn.execute(
                "UPDATE sessions SET ai_status='failed', ai_error='empty transcript' WHERE id=?",
                (session_id,))
        return
    try:
        art = await ai.generate_artifacts(
            session_id, started_at, segments, speaker_labels=speaker_labels)
        with db.closing_conn() as conn:
            saved = db.save_ai_artifacts(
                conn, session_id, art, expected_hash=run_hash,
                expected_speakers_revision=speakers_revision)
        if saved:
            log.info("AI artifacts done for %s", session_id)
        else:
            log.info("transcript or speaker names changed mid-run for %s — rescheduling",
                     session_id)
            asyncio.get_running_loop().call_later(0.1, schedule_ai, session_id)
            return
    except Exception as e:  # noqa: BLE001 — status must always land in the DB
        log.exception("AI pipeline failed for %s", session_id)
        error = str(e)[:500]
        delay = retry_delay_seconds(error, attempt)
        retry_at = _utc_after(delay) if delay is not None else None
        with db.closing_conn() as conn:
            conn.execute(
                """UPDATE sessions SET ai_status='failed', ai_error=?,
                   ai_retry_at=? WHERE id=?""",
                (error, retry_at, session_id))
        if retry_at:
            log.warning("notetaker retry for %s scheduled at %s", session_id, retry_at)


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
                "SELECT ai_status, ai_retry_at FROM sessions WHERE id=?", (sdir.name,)
            ).fetchone()
            retry_due = (st and st["ai_status"] == "failed"
                         and (st["ai_retry_at"] is None
                              or st["ai_retry_at"] <= dt.datetime.now(
                                  dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
            if st and (st["ai_status"] == "pending" or retry_due):
                pending.append(sdir.name)
    return pending
