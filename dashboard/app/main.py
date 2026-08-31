"""quill-dash — self-hosted meeting recordings dashboard.

FastAPI app serving both the JSON API and the static SPA. Binds 127.0.0.1 by
default (see config.HOST): reach it through a TLS reverse proxy, an SSH tunnel,
or a VPN interface you bind explicitly. Set QUILL_PASSWORD for any deployment
reachable from the internet.
"""

import asyncio
import hashlib
import hmac
import logging
import mimetypes
import re
import secrets
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ai, config, db, ingest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("quill.api")

app = FastAPI(title="quill-dash", docs_url=None, openapi_url=None)
_ai_retry_task: asyncio.Task | None = None

STATIC_DIR = Path(__file__).parent.parent / "static"

# ---------------------------------------------------------------- auth
# Single shared password (public deployment). Cookie = HMAC(secret, "quill").
# With no QUILL_PASSWORD set (tailnet-only dev), the gate is off.

if config.PASSWORD and len(config.SECRET) < 16:
    raise RuntimeError("QUILL_PASSWORD is set but QUILL_SECRET is missing/short — refusing to start")


def _sign(iat: str) -> str:
    key = (config.SECRET + config.PASSWORD).encode()
    return hmac.new(key, f"quill|{iat}".encode(), hashlib.sha256).hexdigest()


def _make_cookie() -> str:
    iat = str(int(time.time()))
    return f"{iat}.{_sign(iat)}"


def _cookie_valid(value: str) -> bool:
    try:
        iat, sig = value.split(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(sig, _sign(iat)):
        return False
    return (time.time() - int(iat)) < 180 * 24 * 3600


_login_attempts: dict[str, list[float]] = {}


def _throttled(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < 60]
    _login_attempts[ip] = attempts
    # Global bucket too: proxy egress IPs rotate, so per-IP alone can be
    # side-stepped. 20 failures/min across ALL sources locks the door.
    all_recent = [t for ts in _login_attempts.values() for t in ts if now - t < 60]
    return len(attempts) >= 5 or len(all_recent) >= 20


LOGIN_HTML = """<!DOCTYPE html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Quill</title>
<style>*{box-sizing:border-box}body{background:#f1f3ef;color:#20231f;font-family:-apple-system,sans-serif;
display:grid;place-items:center;min-height:100vh;margin:0}
form{display:flex;flex-direction:column;gap:14px;width:min(340px,88vw);padding:34px;
background:#fff;border:1px solid #dde1da;border-radius:16px;text-align:center;box-shadow:0 14px 45px #323b2d12}
h1{font-family:Charter,'Iowan Old Style',Georgia,serif;font-size:30px;margin:0 0 4px}
input{padding:12px 14px;border-radius:9px;border:1px solid #cbd0c7;background:#f7f8f5;
color:#20231f;font-size:16px;outline:none;text-align:center}input:focus{border-color:#5b57d9}
button{padding:11px;border-radius:9px;border:none;background:#20231f;color:#fff;
font-size:15px;cursor:pointer}.err{color:#b84b52;font-size:13px;min-height:18px}</style>
</head><body><form method=post action=/login>
<h1>🪶 Quill</h1>
<input type=password name=password placeholder="Password" autofocus>
<button>Enter</button><div class=err>{err}</div></form></body></html>"""


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    if not config.PASSWORD:
        return await call_next(request)
    path = request.url.path
    if path == "/login" or path == "/api/health":
        return await call_next(request)
    # Share links: /s/<token> pages and their API are token-authorized —
    # the unguessable token IS the credential (Notion/GDoc model).
    if path.startswith("/s/") or path.startswith("/api/shared/"):
        return await call_next(request)
    # The SPA's own assets carry no data — guests need them to render /s/ pages.
    if path in ("/app.js", "/style.css"):
        return await call_next(request)
    # The Mac sync pings /api/ingest via server-local curl (no cookie). Trust
    # only true local calls: loopback client AND no proxy headers — anything
    # arriving through caddy/Vercel carries X-Forwarded-For and stays gated.
    if (path.startswith("/api/ingest/")
            and request.client and request.client.host == "127.0.0.1"
            and "x-forwarded-for" not in request.headers):
        return await call_next(request)
    if _cookie_valid(request.cookies.get("quill_session", "")):
        return await call_next(request)
    if path.startswith("/api/"):
        return Response(status_code=401, content='{"detail":"unauthorized"}',
                        media_type="application/json")
    return HTMLResponse(LOGIN_HTML.replace("{err}", ""), status_code=401)


@app.post("/login")
async def login(request: Request):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    if _throttled(ip):
        return HTMLResponse(LOGIN_HTML.replace("{err}", "Too many tries — wait a minute"), status_code=429)
    form = await request.form()
    if secrets.compare_digest(str(form.get("password", "")), config.PASSWORD):
        resp = RedirectResponse("/", status_code=303)
        https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        resp.set_cookie("quill_session", _make_cookie(), max_age=180 * 24 * 3600,
                        httponly=True, samesite="lax", secure=https)
        return resp
    _login_attempts.setdefault(ip, []).append(time.time())
    return HTMLResponse(LOGIN_HTML.replace("{err}", "Wrong password"), status_code=401)


@app.get("/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie("quill_session")
    return resp


@app.get("/api/health")
def health():
    with db.closing_conn() as conn:
        row = conn.execute(
            """SELECT
                 sum(CASE WHEN ai_status='pending' THEN 1 ELSE 0 END) AS pending,
                 sum(CASE WHEN ai_status='running' THEN 1 ELSE 0 END) AS running,
                 sum(CASE WHEN ai_status='failed' THEN 1 ELSE 0 END) AS failed,
                 sum(CASE WHEN ai_status='failed' AND ai_retry_at IS NOT NULL
                          THEN 1 ELSE 0 END) AS retrying,
                 sum(CASE WHEN ai_status='transcribing' THEN 1 ELSE 0 END)
                          AS local_processing,
                 sum(CASE WHEN ai_status='transcription_failed' THEN 1 ELSE 0 END)
                          AS local_failed
               FROM sessions"""
        ).fetchone()
    counts = {key: int(row[key] or 0) for key in ("pending", "running", "failed", "retrying")}
    recorder = {key: int(row[key] or 0) for key in ("local_processing", "local_failed")}
    recorder = {"processing": recorder["local_processing"], "failed": recorder["local_failed"]}
    return {
        "ok": recorder["failed"] == 0,
        "notetaker_ok": counts["failed"] == 0,
        "notetaker": counts,
        "recorder": recorder,
    }


@app.on_event("startup")
async def startup() -> None:
    global _ai_retry_task
    db.init()
    pending = ingest.scan_all()
    for sid in pending:
        ingest.schedule_ai(sid)
    if pending:
        log.info("startup: scheduled AI for %s", pending)
    _ai_retry_task = asyncio.create_task(ingest.retry_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    global _ai_retry_task
    if _ai_retry_task:
        _ai_retry_task.cancel()
        try:
            await _ai_retry_task
        except asyncio.CancelledError:
            pass
        _ai_retry_task = None


# ---------------------------------------------------------------- sessions

@app.get("/api/sessions")
def list_sessions(q: str = "", tag: str = "", lang: str = "en"):
    if lang not in ("en", "ru"):
        raise HTTPException(400, "only en and ru are supported")
    with db.closing_conn() as conn:
        rows = conn.execute(
            """SELECT s.*, (SELECT count(*) FROM actions a
                            WHERE a.session_id = s.id AND a.done = 0) AS open_actions
               FROM sessions s ORDER BY started_at DESC""").fetchall()
        alt_rows = (conn.execute(
            "SELECT * FROM artifacts_lang WHERE lang=?", (lang,)).fetchall()
            if lang != "en" else [])
    # The library does not need to transfer up to 100 KiB of private notebook
    # text per card. The individual authenticated session endpoint includes it.
    sessions = [db.session_row_to_dict(r, include_owner_notes=False) for r in rows]
    if alt_rows:
        import json as _json
        alternatives = {row["session_id"]: row for row in alt_rows}
        for session in sessions:
            alt = alternatives.get(session["id"])
            if not alt:
                continue
            session["title"] = alt["title"] or session.get("title")
            if alt["overview_md"] is not None:
                session["overview_md"] = alt["overview_md"]
            if alt["summary_json"]:
                session["summary"] = _json.loads(alt["summary_json"])
            if alt["outline_json"]:
                session["outline"] = _json.loads(alt["outline_json"])
            if alt["keywords_json"]:
                session["keywords"] = _json.loads(alt["keywords_json"])
            session["notes_revision"] = alt["notes_revision"]
            session["notes_edited"] = bool(alt["notes_edited_at"])
    if tag:
        sessions = [s for s in sessions if tag in (s.get("tags") or [])]
    if q:
        ql = q.lower()
        sessions = [s for s in sessions
                    if ql in (s.get("title") or "").lower()
                    or ql in (s.get("overview_md") or "").lower()
                    or ql in s["id"]]
    return {"sessions": sessions}


def _session_payload(session_id: str, lang: str = "en",
                     include_segments: bool = True):
    import json as _json
    with db.closing_conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(404, "unknown session")
        segs = (conn.execute(
            "SELECT idx, speaker, start_ms, end_ms, text FROM segments"
            " WHERE session_id=? ORDER BY idx", (session_id,)).fetchall()
            if include_segments else [])
        actions = conn.execute(
            "SELECT id, text, ru_text, assignee, source_ms, done FROM actions"
            " WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
        alt = conn.execute(
            "SELECT * FROM artifacts_lang WHERE session_id=? AND lang=?",
            (session_id, lang)).fetchone() if lang != "en" else None
        ru_cached = bool(conn.execute(
            "SELECT 1 FROM artifacts_lang WHERE session_id=? AND lang='ru'",
            (session_id,)).fetchone()) and not conn.execute(
            "SELECT 1 FROM actions WHERE session_id=? AND ru_text IS NULL LIMIT 1",
            (session_id,)).fetchone()
    d = db.session_row_to_dict(row)
    d["segments"] = [dict(s) for s in segs]
    d["actions"] = [dict(a) for a in actions]
    d["lang"] = "en"
    d["lang_ready"] = {"en": True, "ru": ru_cached}
    if lang != "en":
        if alt:
            d["lang"] = lang
            d["title"] = alt["title"] or d["title"]
            d["overview_md"] = alt["overview_md"] or d["overview_md"]
            # Old translation rows predate structured summaries. Return an
            # empty localized summary in that case so the client derives its
            # lead from the translated overview instead of mixing in English.
            d["summary"] = _json.loads(alt["summary_json"] or "{}")
            d["notes_edited"] = bool(alt["notes_edited_at"])
            d["notes_revision"] = alt["notes_revision"]
            d["outline"] = _json.loads(alt["outline_json"] or "[]") or d["outline"]
            d["keywords"] = _json.loads(alt["keywords_json"] or "[]") or d["keywords"]
            for a in d["actions"]:
                if a.get("ru_text"):
                    a["text"] = a["ru_text"]
    return d


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str, lang: str = "en"):
    return _session_payload(session_id, lang=lang, include_segments=True)


class NoteItemEdit(BaseModel):
    text: str
    source_ms: int | None = None


class SummaryEdit(BaseModel):
    brief: str
    decisions: list[NoteItemEdit]
    open_questions: list[NoteItemEdit]


class NotesEdit(BaseModel):
    expected_revision: int
    title: str
    overview_md: str
    summary: SummaryEdit


class OwnerNotesEdit(BaseModel):
    expected_revision: int
    markdown: str


def _clean_note_edit(body: NotesEdit, duration_s: float) -> tuple[str, str, dict]:
    title = body.title.strip()
    overview = body.overview_md.strip()
    brief = body.summary.brief.strip()
    if not title:
        raise HTTPException(422, "title cannot be blank")
    if len(title) > 160:
        raise HTTPException(422, "title is too long (160 characters max)")
    if not brief:
        raise HTTPException(422, "brief cannot be blank")
    if len(brief) > 2000:
        raise HTTPException(422, "brief is too long (2,000 characters max)")
    if len(overview) > 50_000:
        raise HTTPException(422, "detailed notes are too long (50,000 characters max)")

    def items(values: list[NoteItemEdit], label: str) -> list[dict]:
        if len(values) > 20:
            raise HTTPException(422, f"too many {label} (20 max)")
        cleaned = []
        for value in values:
            text = value.text.strip()
            if not text:
                continue
            if len(text) > 2000:
                raise HTTPException(422, f"{label} item is too long (2,000 characters max)")
            ms = value.source_ms
            if ms is not None:
                if type(ms) is not int or ms < 0 or ms > round(duration_s * 1000):
                    raise HTTPException(422, f"{label} timestamp is outside the recording")
            cleaned.append({"text": text, "source_ms": ms})
        return cleaned

    summary = {
        "brief": brief,
        "decisions": items(body.summary.decisions, "decision"),
        "open_questions": items(body.summary.open_questions, "open question"),
    }
    return title, overview, summary


@app.patch("/api/sessions/{session_id}/notes")
def edit_notes(session_id: str, body: NotesEdit, lang: str = "en"):
    if lang not in ("en", "ru"):
        raise HTTPException(400, "only en and ru are supported")
    with db.closing_conn() as conn:
        row = conn.execute(
            "SELECT duration_s, ai_status, notes_revision FROM sessions WHERE id=?",
            (session_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "unknown session")
        if row["ai_status"] != "done":
            raise HTTPException(409, "notes can only be edited after processing finishes")
        if type(body.expected_revision) is not int or body.expected_revision < 0:
            raise HTTPException(422, "invalid notes revision")
        if lang == "en":
            current_revision = row["notes_revision"]
        else:
            alt = conn.execute(
                "SELECT notes_revision FROM artifacts_lang WHERE session_id=? AND lang=?",
                (session_id, lang)).fetchone()
            if not alt:
                raise HTTPException(409, "Russian notes are not ready — translate first")
            current_revision = alt["notes_revision"]
        if current_revision != body.expected_revision:
            raise HTTPException(409, "notes changed in another window — reload and try again")
        title, overview, summary = _clean_note_edit(body, row["duration_s"])
        if not db.save_manual_notes(
                conn, session_id, lang, title, overview, summary,
                body.expected_revision):
            raise HTTPException(409, "notes changed while saving — reload and try again")
    return get_session(session_id, lang=lang)


def _clean_owner_notes(value: str) -> str:
    markdown = value.replace("\r\n", "\n").replace("\r", "\n")
    if not markdown.strip():
        return ""
    if len(markdown.encode("utf-8")) > 100 * 1024:
        raise HTTPException(422, "private notes are limited to 100 KiB")
    if any(((ord(char) < 32 and char not in "\n\t")
            or 127 <= ord(char) <= 159) for char in markdown):
        raise HTTPException(422, "private notes cannot contain control characters")
    return markdown


@app.patch("/api/sessions/{session_id}/owner-notes")
def edit_owner_notes(session_id: str, body: OwnerNotesEdit, lang: str = "en"):
    if lang not in ("en", "ru"):
        raise HTTPException(400, "only en and ru are supported")
    if type(body.expected_revision) is not int or body.expected_revision < 0:
        raise HTTPException(422, "invalid private notes revision")
    markdown = _clean_owner_notes(body.markdown)
    stored = markdown or None
    with db.closing_conn() as conn:
        row = conn.execute(
            "SELECT owner_notes_revision FROM sessions WHERE id=?",
            (session_id,)).fetchone()
        if not row:
            raise HTTPException(404, "unknown session")
        if row["owner_notes_revision"] != body.expected_revision:
            raise HTTPException(
                409, "private notes changed in another window — choose which copy to keep")
        result = db.save_owner_notes(
            conn, session_id, stored, body.expected_revision)
        if result == "missing":
            raise HTTPException(404, "unknown session")
        if result == "stale":
            raise HTTPException(
                409, "private notes changed while saving — choose which copy to keep")
        current = conn.execute(
            """SELECT owner_notes_md,owner_notes_revision,owner_notes_edited_at
               FROM sessions WHERE id=?""", (session_id,)).fetchone()
        payload = {
            "id": session_id,
            "owner_notes": {
                "markdown": current["owner_notes_md"] or "",
                "revision": current["owner_notes_revision"],
                "edited": bool(current["owner_notes_edited_at"]
                               and current["owner_notes_md"]),
            },
        }
    # Autosave must not echo thousands of transcript segments back to the
    # browser after every edit; this is the complete private notebook DTO.
    return payload


class SpeakerLabelsEdit(BaseModel):
    expected_revision: int
    me: str | None = None
    them: str | None = None


def _clean_speaker_label(value: str | None) -> str | None:
    if value is None:
        return None
    label = re.sub(r"\s+", " ", value).strip()
    if not label:
        return None
    if len(label) > 80:
        raise HTTPException(422, "speaker names are limited to 80 characters")
    if any(ord(char) < 32 for char in label):
        raise HTTPException(422, "speaker names cannot contain control characters")
    return label


@app.patch("/api/sessions/{session_id}/speakers")
def edit_speaker_labels(session_id: str, body: SpeakerLabelsEdit,
                        lang: str = "en"):
    if lang not in ("en", "ru"):
        raise HTTPException(400, "only en and ru are supported")
    if type(body.expected_revision) is not int or body.expected_revision < 0:
        raise HTTPException(422, "invalid speakers revision")
    me = _clean_speaker_label(body.me)
    them = _clean_speaker_label(body.them)
    with db.closing_conn() as conn:
        row = conn.execute(
            "SELECT segments_hash,speakers_revision FROM sessions WHERE id=?",
            (session_id,)).fetchone()
        if not row:
            raise HTTPException(404, "unknown session")
        if row["segments_hash"] is None:
            raise HTTPException(409, "local transcription is not ready")
        if row["speakers_revision"] != body.expected_revision:
            raise HTTPException(409, "voice names changed in another window — reload and try again")
        if not db.save_speaker_labels(
                conn, session_id, me, them, body.expected_revision):
            raise HTTPException(409, "voice names changed while saving — reload and try again")
    return get_session(session_id, lang=lang)


@app.post("/api/ingest/{session_id}")
async def ingest_endpoint(session_id: str):
    if not re.fullmatch(r"[\w.\-]+", session_id):
        raise HTTPException(400, "bad session id")
    try:
        result = ingest.ingest_session(session_id)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    if result["ai_status"] in ("pending", "failed"):
        ingest.schedule_ai(session_id)
    return result


@app.get("/api/sessions/{session_id}/status")
def session_status(session_id: str):
    with db.closing_conn() as conn:
        row = conn.execute(
            """SELECT ai_status, ai_error, ai_attempts, ai_retry_at
               FROM sessions WHERE id=?""", (session_id,)).fetchone()
    if not row:
        raise HTTPException(404, "unknown session")
    return dict(row)


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    if not re.fullmatch(r"[\w.\-]+", session_id):
        raise HTTPException(400, "bad session id")
    with db.closing_conn() as conn:
        if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
            raise HTTPException(404, "unknown session")
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        conn.execute("DELETE FROM segments_fts WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM owner_notes_fts WHERE session_id=?", (session_id,))
        conn.execute("INSERT OR IGNORE INTO deleted_sessions (id) VALUES (?)", (session_id,))
        conn.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
    sdir = config.SESSIONS_DIR / session_id
    if sdir.exists():
        import shutil
        shutil.rmtree(sdir)
    return {"deleted": session_id}


_translate_inflight: dict[str, str] = {}   # (session:lang) -> job_id


@app.post("/api/sessions/{session_id}/translate")
async def translate(session_id: str, lang: str = "ru"):
    import json as _json
    if lang != "ru":
        raise HTTPException(400, "only ru supported")
    flight_key = f"{session_id}:{lang}"
    existing = _translate_inflight.get(flight_key)
    if existing and JOBS.get(existing, {}).get("status") == "running":
        return {"job_id": existing, "ready": False}
    with db.closing_conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(404, "unknown session")
        if row["ai_status"] != "done":
            raise HTTPException(409, "AI artifacts not ready yet")
        cached = conn.execute(
            "SELECT 1 FROM artifacts_lang WHERE session_id=? AND lang=?",
            (session_id, lang)).fetchone()
        actions = conn.execute(
            "SELECT id, text, ru_text FROM actions WHERE session_id=? ORDER BY id",
            (session_id,)).fetchall()
    missing = [a for a in actions if not a["ru_text"]]
    if cached and not missing:
        return {"job_id": None, "ready": True}

    outline = _json.loads(row["outline_json"] or "[]")
    keywords = _json.loads(row["keywords_json"] or "[]")
    summary = _json.loads(row["summary_json"] or "{}")
    snapshot_hash = row["segments_hash"]
    snapshot_revision = row["artifacts_revision"]
    snapshot_ids = {a["id"] for a in actions}
    jid = _new_job()
    _translate_inflight[flight_key] = jid

    async def run():
        try:
            if cached and missing:
                # Cache exists; only newer actions need translating.
                out = await ai.translate_artifacts(
                    "Russian", row["title"] or session_id, "",
                    {"brief": "", "decisions": [], "open_questions": []},
                    [], [], [{"id": a["id"], "text": a["text"]} for a in missing])
            else:
                out = await ai.translate_artifacts(
                    "Russian", row["title"] or session_id, row["overview_md"] or "",
                    summary, outline, keywords,
                    [{"id": a["id"], "text": a["text"]} for a in actions])
            with db.closing_conn() as conn:
                # Stale guard (#1): if the transcript re-ingested or the AI
                # regenerated (revision, action ids, or transcript hash), this
                # snapshot is dead — discard rather than publish mixed-language state.
                cur = conn.execute(
                    "SELECT segments_hash, artifacts_revision FROM sessions WHERE id=?",
                    (session_id,)).fetchone()
                cur_ids = {r["id"] for r in conn.execute(
                    "SELECT id FROM actions WHERE session_id=?", (session_id,))}
                if (not cur or cur["segments_hash"] != snapshot_hash
                        or cur["artifacts_revision"] != snapshot_revision
                        or not snapshot_ids <= cur_ids):
                    raise ai.AIError("notes changed while translating — flip RU again")
                if not (cached and missing):
                    conn.execute(
                        """INSERT OR REPLACE INTO artifacts_lang
                           (session_id, lang, title, overview_md, summary_json,
                            outline_json, keywords_json)
                           VALUES (?,?,?,?,?,?,?)""",
                        (session_id, lang, out["title"], out["overview_md"],
                         _json.dumps(out["summary"], ensure_ascii=False),
                         _json.dumps(out["outline"], ensure_ascii=False),
                         _json.dumps(out["keywords"], ensure_ascii=False)))
                for item in out["actions"]:
                    conn.execute("UPDATE actions SET ru_text=? WHERE id=?",
                                 (item["text"], item["id"]))
            _finish_job(jid, answer="ready")
        except Exception as e:  # noqa: BLE001
            _finish_job(jid, error=str(e)[:300])
        finally:
            if _translate_inflight.get(flight_key) == jid:
                _translate_inflight.pop(flight_key, None)

    asyncio.get_running_loop().create_task(run())
    return {"job_id": jid, "ready": False}


@app.post("/api/sessions/{session_id}/regenerate")
async def regenerate(session_id: str):
    with db.closing_conn() as conn:
        row = conn.execute(
            "SELECT segments_hash FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "unknown session")
        if row["segments_hash"] is None:
            raise HTTPException(409, "local transcription is not ready")
        conn.execute(
            """UPDATE sessions SET ai_status='pending', ai_error=NULL,
               ai_attempts=0, ai_retry_at=NULL WHERE id=?""", (session_id,))
    started = ingest.schedule_ai(session_id)
    return {"scheduled": started}


# ---------------------------------------------------------------- sharing

def _public_base(request: Request) -> str:
    if config.PUBLIC_BASE:
        return config.PUBLIC_BASE
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _share_access(value: str | None) -> str:
    """Normalize persisted access fail-closed."""
    return "full" if value == "full" else "summary"


@app.post("/api/sessions/{session_id}/share")
def create_share(session_id: str, request: Request, lang: str = "en",
                 access_level: str = "summary"):
    if lang not in ("en", "ru"):
        raise HTTPException(400, "only en and ru are supported")
    if access_level not in ("summary", "full"):
        raise HTTPException(400, "access_level must be summary or full")
    with db.closing_conn() as conn:
        # Serialize token creation so simultaneous Copy link clicks cannot mint
        # two active URLs for one meeting.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT segments_hash FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "unknown session")
        if row["segments_hash"] is None:
            raise HTTPException(409, "local transcription is not ready")
        if lang == "ru" and not conn.execute(
                "SELECT 1 FROM artifacts_lang WHERE session_id=? AND lang='ru'",
                (session_id,)).fetchone():
            raise HTTPException(409, "Russian notes are not ready — translate first")
        row = conn.execute(
            "SELECT token FROM share_tokens WHERE session_id=? ORDER BY created_at LIMIT 1",
            (session_id,)).fetchone()
        if row:
            conn.execute(
                "UPDATE share_tokens SET lang=?, access_level=? WHERE session_id=?",
                (lang, access_level, session_id))
            return {"token": row["token"], "lang": lang, "access_level": access_level,
                    "url": f"{_public_base(request)}/s/{row['token']}"}
        token = secrets.token_urlsafe(18)
        conn.execute(
            """INSERT INTO share_tokens (token, session_id, lang, access_level)
               VALUES (?,?,?,?)""",
            (token, session_id, lang, access_level))
    return {"token": token, "lang": lang, "access_level": access_level,
            "url": f"{_public_base(request)}/s/{token}"}


@app.delete("/api/sessions/{session_id}/share")
def revoke_share(session_id: str):
    with db.closing_conn() as conn:
        conn.execute("DELETE FROM share_tokens WHERE session_id=?", (session_id,))
    return {"revoked": True}


@app.get("/api/sessions/{session_id}/share")
def get_share(session_id: str):
    with db.closing_conn() as conn:
        row = conn.execute(
            """SELECT token, lang, access_level FROM share_tokens
               WHERE session_id=? ORDER BY created_at LIMIT 1""",
            (session_id,)).fetchone()
    return {
        "token": row["token"] if row else None,
        "lang": row["lang"] if row else None,
        "access_level": _share_access(row["access_level"]) if row else None,
    }


def _shared_session(token: str):
    with db.closing_conn() as conn:
        t = conn.execute("SELECT * FROM share_tokens WHERE token=?", (token,)).fetchone()
        if not t:
            raise HTTPException(404, "invalid or revoked link")
    return t


@app.get("/api/shared/{token}")
def shared_payload(token: str, response: Response):
    t = _shared_session(token)
    access_level = _share_access(t["access_level"])
    d = _session_payload(
        t["session_id"], lang=t["lang"], include_segments=access_level == "full")
    # Strict guest DTO: summary links never serialize raw conversation data.
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    payload = {
        "shared": True,
        "access_level": access_level,
        "id": d["id"],
        "lang": d["lang"],
        "title": d.get("title"),
        "started_at": d["started_at"],
        "duration_s": d["duration_s"],
        "overview_md": d.get("overview_md"),
        "speaker_labels": {
            "me": (d.get("speaker_labels") or {}).get("me"),
            "them": (d.get("speaker_labels") or {}).get("them"),
        },
        "summary": {
            "brief": str((d.get("summary") or {}).get("brief") or ""),
            "decisions": [
                {"text": str(item.get("text") or ""),
                 "source_ms": item.get("source_ms")}
                for item in (d.get("summary") or {}).get("decisions", [])
                if isinstance(item, dict) and item.get("text")
            ],
            "open_questions": [
                {"text": str(item.get("text") or ""),
                 "source_ms": item.get("source_ms")}
                for item in (d.get("summary") or {}).get("open_questions", [])
                if isinstance(item, dict) and item.get("text")
            ],
        },
        "actions": [{"text": a["text"], "assignee": a.get("assignee"),
                     "done": bool(a["done"])} for a in d["actions"]],
    }
    if access_level == "full":
        payload.update({
            "outline": [
                {"ms": int(o["ms"]), "label": o["label"]}
                for o in d.get("outline", [])
            ],
            "segments": [
                {"idx": s["idx"],
                 "speaker": s["speaker"] if s["speaker"] in ("me", "them") else "them",
                 "start_ms": int(s["start_ms"]), "end_ms": int(s["end_ms"]),
                 "text": s["text"]}
                for s in d["segments"]
            ],
            "has_audio_mixed": d["has_audio_mixed"],
            "has_audio_system": d["has_audio_system"],
            "has_audio_mic": d["has_audio_mic"],
        })
    return payload


@app.get("/api/shared/{token}/audio/{track}")
def shared_audio(token: str, track: str, request: Request):
    t = _shared_session(token)
    if _share_access(t["access_level"]) != "full":
        raise HTTPException(403, "this link shares the summary only")
    response = audio(t["session_id"], track, request)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


DEAD_LINK_HTML = """<!DOCTYPE html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Quill</title>
<style>body{background:#f1f3ef;color:#20231f;font-family:-apple-system,sans-serif;
display:grid;place-items:center;min-height:100vh;margin:0;text-align:center}
h1{font-family:Charter,'Iowan Old Style',Georgia,serif;font-weight:600}
p{color:#6f756f}</style></head><body><div>
<h1>🪶 Эта ссылка больше не действует</h1>
<p>This link is no longer active. Ask the person who shared it for a new one.</p>
</div></body></html>"""


@app.get("/s/{token}")
def share_page(token: str):
    try:
        _shared_session(token)
    except HTTPException:
        return HTMLResponse(DEAD_LINK_HTML, status_code=404)
    response = FileResponse(STATIC_DIR / "index.html", media_type="text/html")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


# ---------------------------------------------------------------- audio

@app.get("/api/sessions/{session_id}/audio/{track}")
def audio(session_id: str, track: str, request: Request):
    if track not in ("mic", "system", "mixed"):
        raise HTTPException(404, "unknown track")
    with db.closing_conn() as conn:
        if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
            raise HTTPException(404, "unknown session")
    path = config.SESSIONS_DIR / session_id / f"{track}.m4a"
    if not path.exists():
        raise HTTPException(404, "no audio")
    return _range_file_response(path, request)


def _range_file_response(path: Path, request: Request) -> Response:
    """Minimal HTTP Range support so <audio> seeking works."""
    file_size = path.stat().st_size
    content_type = mimetypes.guess_type(str(path))[0] or "audio/mp4"
    range_header = request.headers.get("range")
    if not range_header:
        return FileResponse(path, media_type=content_type)
    m = re.match(r"bytes=(\d*)-(\d*)$", range_header)
    if not m or (not m.group(1) and not m.group(2)):
        raise HTTPException(416, "bad range")
    if not m.group(1):
        # suffix range: last N bytes
        n = int(m.group(2))
        start, end = max(0, file_size - n), file_size - 1
    else:
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else file_size - 1
    end = min(end, file_size - 1)
    if start > end or start >= file_size:
        raise HTTPException(416, "bad range")

    def stream(s=start, e=end):
        with open(path, "rb") as f:
            f.seek(s)
            remaining = e - s + 1
            while remaining > 0:
                chunk = f.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    from starlette.responses import StreamingResponse
    return StreamingResponse(
        stream(), status_code=206, media_type=content_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
        })


# ---------------------------------------------------------------- search

@app.get("/api/search")
def search(q: str):
    q = q.strip()
    if not q:
        return {"results": []}
    if len(q) > 500:
        raise HTTPException(422, "search query is limited to 500 characters")
    # FTS5 phrase-safe query: quote each term.
    terms = re.findall(r"\w+", q)
    if len(terms) > 32:
        raise HTTPException(422, "search query is limited to 32 terms")
    fts_q = " ".join(f'"{term}"' for term in terms)
    if not fts_q:
        return {"results": []}
    with db.closing_conn() as conn:
        note_rows = conn.execute(
            """SELECT n.session_id,
                      snippet(owner_notes_fts, 0, char(1), char(2), '…', 18) AS snip,
                      s.title, s.started_at
               FROM owner_notes_fts n
               JOIN sessions s ON s.id = n.session_id
               WHERE owner_notes_fts MATCH ?
               ORDER BY s.started_at DESC
               LIMIT 50""", (fts_q,)).fetchall()
        transcript_rows = conn.execute(
            """SELECT f.session_id, f.idx,
                      snippet(segments_fts, 0, char(1), char(2), '…', 12) AS snip,
                      seg.start_ms, seg.speaker,
                      s.title, s.started_at,
                      s.speaker_me_label, s.speaker_them_label
               FROM segments_fts f
               JOIN segments seg ON seg.session_id = f.session_id AND seg.idx = f.idx
               JOIN sessions s ON s.id = f.session_id
               WHERE segments_fts MATCH ?
               ORDER BY s.started_at DESC, f.idx
               LIMIT 100""", (fts_q,)).fetchall()
    notes = [{"kind": "owner_note", **dict(row)} for row in note_rows]
    transcripts = [{"kind": "transcript", **dict(row)}
                   for row in transcript_rows]
    for result in transcripts:
        result["speaker_me_label"] = db.stored_speaker_label(
            result["speaker_me_label"])
        result["speaker_them_label"] = db.stored_speaker_label(
            result["speaker_them_label"])
    return {"results": notes + transcripts}


# ---------------------------------------------------------------- actions

class ActionToggle(BaseModel):
    done: bool


@app.get("/api/actions")
def all_actions(include_done: bool = False):
    with db.closing_conn() as conn:
        rows = conn.execute(
            """SELECT a.*, s.title AS session_title, s.started_at
               FROM actions a JOIN sessions s ON s.id = a.session_id
               {} ORDER BY a.done, s.started_at DESC, a.id"""
            .format("" if include_done else "WHERE a.done = 0")).fetchall()
    return {"actions": [dict(r) for r in rows]}


@app.post("/api/actions/{action_id}/toggle")
def toggle_action(action_id: int, body: ActionToggle):
    with db.closing_conn() as conn:
        cur = conn.execute(
            "UPDATE actions SET done=? WHERE id=?", (int(body.done), action_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "unknown action")
    return {"ok": True}


class ActionCreate(BaseModel):
    session_id: str
    text: str
    assignee: str | None = None


@app.post("/api/actions")
def create_action(body: ActionCreate):
    with db.closing_conn() as conn:
        if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (body.session_id,)).fetchone():
            raise HTTPException(404, "unknown session")
        cur = conn.execute(
            "INSERT INTO actions (session_id, text, assignee) VALUES (?,?,?)",
            (body.session_id, body.text, body.assignee))
        return {"id": cur.lastrowid}


# ---------------------------------------------------------------- chat

class ChatBody(BaseModel):
    question: str


# Chat runs can outlive any proxy timeout — submit returns a job id, the SPA
# polls /api/jobs/{id}. Jobs live in memory (single-process deployment).
JOBS: dict[str, dict] = {}


def _new_job() -> str:
    jid = uuid.uuid4().hex[:12]
    JOBS[jid] = {"status": "running", "answer": None, "error": None, "ts": time.time()}
    for k in [k for k, v in JOBS.items() if time.time() - v["ts"] > 3600]:
        JOBS.pop(k, None)
    return jid


def _finish_job(jid: str, answer=None, error=None):
    if jid in JOBS:
        JOBS[jid].update(status="failed" if error else "done", answer=answer, error=error)


@app.get("/api/jobs/{jid}")
def job_status(jid: str):
    j = JOBS.get(jid)
    if not j:
        raise HTTPException(404, "unknown job")
    return j


_chat_scope_locks: dict[str, asyncio.Lock] = {}


def _scope_lock(key: str) -> asyncio.Lock:
    if key not in _chat_scope_locks:
        _chat_scope_locks[key] = asyncio.Lock()
    return _chat_scope_locks[key]


def _history(conn, scope: str, session_id: str | None):
    rows = conn.execute(
        """SELECT role, content FROM chat_messages
           WHERE scope=? AND (session_id=? OR (session_id IS NULL AND ? IS NULL))
           ORDER BY id DESC LIMIT 10""",
        (scope, session_id, session_id)).fetchall()
    return [dict(r) for r in reversed(rows)]


@app.post("/api/sessions/{session_id}/chat")
async def session_chat(session_id: str, body: ChatBody):
    with db.closing_conn() as conn:
        session = conn.execute(
            """SELECT speaker_me_label,speaker_them_label FROM sessions
               WHERE id=?""", (session_id,)).fetchone()
        segs = conn.execute(
            "SELECT speaker, start_ms, end_ms, text FROM segments"
            " WHERE session_id=? ORDER BY idx", (session_id,)).fetchall()
        if not session or not segs:
            raise HTTPException(404, "unknown or empty session")
        speaker_labels = {
            "me": db.stored_speaker_label(session["speaker_me_label"]),
            "them": db.stored_speaker_label(session["speaker_them_label"]),
        }
    jid = _new_job()

    async def run():
        try:
            async with _scope_lock(f"session:{session_id}"):
                with db.closing_conn() as conn:
                    history = _history(conn, "session", session_id)
                answer = await ai.chat_session(
                    [dict(s) for s in segs], history, body.question,
                    speaker_labels=speaker_labels)
            with db.closing_conn() as conn:
                conn.execute(
                    "INSERT INTO chat_messages (scope, session_id, role, content) VALUES ('session',?,?,?)",
                    (session_id, "user", body.question))
                conn.execute(
                    "INSERT INTO chat_messages (scope, session_id, role, content) VALUES ('session',?,?,?)",
                    (session_id, "assistant", answer))
            _finish_job(jid, answer=answer)
        except Exception as e:  # noqa: BLE001
            _finish_job(jid, error=str(e)[:300])

    asyncio.get_running_loop().create_task(run())
    return {"job_id": jid}


@app.post("/api/ask")
async def global_ask(body: ChatBody):
    # Retrieval: bm25-ranked FTS hits (stopwords dropped), context windows
    # around the exact matching segments, recent history folded into the query.
    STOP = {"what","did","does","the","a","an","и","в","на","что","как","did",
            "we","i","you","he","she","it","они","мы","я","ты","о","по","не",
            "is","are","was","were","do","about","tell","me","us"}
    with db.closing_conn() as conn:
        history = _history(conn, "global", None)
        hist_text = " ".join(m["content"] for m in history[-4:] if m["role"] == "user")
        terms = [t for t in re.findall(r"\w+", f"{body.question} {hist_text}")
                 if len(t) > 2 and t.lower() not in STOP]
        blocks: list[str] = []
        seg_hits: list = []
        if terms:
            fts_q = " OR ".join(f'"{t}"' for t in terms[:16])
            seg_hits = conn.execute(
                """SELECT f.session_id, f.idx FROM segments_fts f
                   WHERE segments_fts MATCH ? ORDER BY bm25(segments_fts) LIMIT 40""",
                (fts_q,)).fetchall()
        by_session: dict[str, list[int]] = {}
        for h in seg_hits:
            by_session.setdefault(h["session_id"], []).append(h["idx"])
        hit_ids = list(by_session.keys())[:6]
        if not hit_ids:
            hit_ids = [r["id"] for r in conn.execute(
                """SELECT id FROM sessions WHERE segments_hash IS NOT NULL
                   ORDER BY started_at DESC LIMIT 4""")]
        for sid in hit_ids:
            srow = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            if not srow:
                continue
            idxs = sorted(set(by_session.get(sid, [])))
            if idxs:
                want = set()
                for ix in idxs[:8]:
                    want.update(range(max(0, ix - 6), ix + 7))
                qmarks = ",".join("?" * len(want))
                segs = conn.execute(
                    f"SELECT speaker, start_ms, end_ms, text FROM segments"
                    f" WHERE session_id=? AND idx IN ({qmarks}) ORDER BY idx",
                    (sid, *sorted(want))).fetchall()
            else:
                segs = conn.execute(
                    "SELECT speaker, start_ms, end_ms, text FROM segments"
                    " WHERE session_id=? ORDER BY idx", (sid,)).fetchall()
            speaker_labels = {
                "me": db.stored_speaker_label(srow["speaker_me_label"]),
                "them": db.stored_speaker_label(srow["speaker_them_label"]),
            }
            block = (
                f"=== Meeting: {srow['title'] or sid} ({srow['started_at']}) ===\n"
                + ai.transcript_block(
                    [dict(s) for s in segs], max_chars=30_000,
                    speaker_labels=speaker_labels))
            blocks.append(block)
    jid = _new_job()

    async def run():
        try:
            async with _scope_lock("global"):
                with db.closing_conn() as conn:
                    history2 = _history(conn, "global", None)
                answer = await ai.chat_global(blocks, history2, body.question)
            with db.closing_conn() as conn:
                conn.execute(
                    "INSERT INTO chat_messages (scope, role, content) VALUES ('global','user',?)",
                    (body.question,))
                conn.execute(
                    "INSERT INTO chat_messages (scope, role, content) VALUES ('global','assistant',?)",
                    (answer,))
            _finish_job(jid, answer=answer)
        except Exception as e:  # noqa: BLE001
            _finish_job(jid, error=str(e)[:300])

    asyncio.get_running_loop().create_task(run())
    return {"job_id": jid}


@app.get("/api/chat/{scope}")
def chat_history(scope: str, session_id: str | None = None):
    if scope not in ("session", "global"):
        raise HTTPException(404, "bad scope")
    with db.closing_conn() as conn:
        if session_id and not conn.execute(
                "SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
            raise HTTPException(404, "unknown session")
        return {"messages": _history(conn, scope, session_id)}


# ---------------------------------------------------------------- static SPA

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
