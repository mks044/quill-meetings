"""Permission-boundary tests for anonymous meeting links.

Uses route functions directly so the suite needs FastAPI but not the optional
httpx TestClient dependency. All storage and audio live in a disposable temp
directory selected before app configuration is imported.
"""

import asyncio
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

_temp = tempfile.TemporaryDirectory()
os.environ["QUILL_DATA"] = _temp.name
os.environ["QUILL_PASSWORD"] = ""
os.environ["QUILL_PUBLIC_BASE"] = "https://quill.test"

from fastapi import HTTPException, Request  # noqa: E402
from fastapi.responses import Response  # noqa: E402

from app import config, db, main  # noqa: E402


def request(path: str = "/", range_header: str | None = None) -> Request:
    headers = [(b"host", b"quill.test")]
    if range_header:
        headers.append((b"range", range_header.encode()))
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("quill.test", 443),
    })


class ShareAccessTests(unittest.TestCase):
    def setUp(self):
        db.init()
        with db.closing_conn() as conn:
            conn.execute("DELETE FROM share_tokens")
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM segments_fts")
            conn.execute(
                """INSERT INTO sessions
                   (id,started_at,duration_s,title,overview_md,summary_json,
                    outline_json,keywords_json,tags_json,ai_status,segments_hash,
                    has_audio_mic)
                   VALUES ('meeting','2026-08-30T10:00:00+00:00',60,
                           'Planning','### Topic\n- Detail',?,?,'[]','[]','done',
                           'transcript-hash',1)""",
                (json.dumps({
                    "brief": "A concise result.",
                    "decisions": [{"text": "Ship it", "source_ms": 1000}],
                    "open_questions": [],
                }), json.dumps([{"ms": 1000, "label": "Decision"}])))
            conn.execute(
                """INSERT INTO segments
                   (session_id,idx,speaker,start_ms,end_ms,text)
                   VALUES ('meeting',0,'me',0,2000,'Raw secret words')""")
            conn.execute(
                """INSERT INTO actions
                   (session_id,text,assignee,source_ms,source)
                   VALUES ('meeting','Send plan','me',1000,'ai')""")
        session_dir = config.SESSIONS_DIR / "meeting"
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "mic.m4a").write_bytes(b"synthetic-audio")

    def test_summary_link_omits_raw_data_and_same_token_can_be_upgraded(self):
        created = main.create_share(
            "meeting", request(), lang="en", access_level="summary")
        self.assertEqual(created["access_level"], "summary")
        self.assertEqual(created["url"], f"https://quill.test/s/{created['token']}")

        response = Response()
        queries = []
        connect = db.connect
        def traced_connect():
            conn = connect()
            conn.set_trace_callback(queries.append)
            return conn
        db.connect = traced_connect
        try:
            summary = main.shared_payload(created["token"], response)
        finally:
            db.connect = connect
        self.assertEqual(summary["access_level"], "summary")
        self.assertEqual(summary["summary"]["brief"], "A concise result.")
        self.assertEqual(summary["actions"][0]["text"], "Send plan")
        self.assertTrue({"segments", "outline", "has_audio_mic",
                         "has_audio_system", "has_audio_mixed"}.isdisjoint(summary))
        self.assertFalse(any(" FROM SEGMENTS " in f" {query.upper()} " for query in queries))
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        with self.assertRaises(HTTPException) as denied:
            main.shared_audio(created["token"], "mic", request())
        self.assertEqual(denied.exception.status_code, 403)

        upgraded = main.create_share(
            "meeting", request(), lang="en", access_level="full")
        self.assertEqual(upgraded["token"], created["token"])
        full = main.shared_payload(created["token"], Response())
        self.assertEqual(full["access_level"], "full")
        self.assertEqual(full["segments"][0]["text"], "Raw secret words")
        self.assertEqual(full["outline"][0]["label"], "Decision")
        self.assertTrue(full["has_audio_mic"])
        audio = main.shared_audio(
            created["token"], "mic", request(range_header="bytes=0-3"))
        async def read_audio():
            return b"".join([chunk async for chunk in audio.body_iterator])
        self.assertEqual(audio.status_code, 206)
        self.assertEqual(audio.headers["content-range"], "bytes 0-3/15")
        self.assertEqual(asyncio.run(read_audio()), b"synt")
        self.assertEqual(audio.headers["cache-control"], "private, no-store")

        downgraded = main.create_share(
            "meeting", request(), lang="en", access_level="summary")
        self.assertEqual(downgraded["token"], created["token"])
        self.assertNotIn("segments", main.shared_payload(created["token"], Response()))

    def test_invalid_or_corrupt_scope_fails_closed(self):
        for lang, access in (("fr", "summary"), ("en", "everything")):
            with self.assertRaises(HTTPException) as invalid:
                main.create_share("meeting", request(), lang=lang, access_level=access)
            self.assertEqual(invalid.exception.status_code, 400)
        self.assertIsNone(main.get_share("meeting")["token"])

        created = main.create_share(
            "meeting", request(), lang="en", access_level="full")
        with db.closing_conn() as conn:
            conn.execute(
                "UPDATE share_tokens SET access_level='unexpected' WHERE token=?",
                (created["token"],))
        payload = main.shared_payload(created["token"], Response())
        self.assertEqual(payload["access_level"], "summary")
        self.assertNotIn("segments", payload)
        with self.assertRaises(HTTPException) as denied:
            main.shared_audio(created["token"], "mic", request())
        self.assertEqual(denied.exception.status_code, 403)

    def test_simultaneous_creation_reuses_one_token(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda _: main.create_share(
                    "meeting", request(), lang="en", access_level="summary"),
                range(2)))
        self.assertEqual(len({result["token"] for result in results}), 1)
        with db.closing_conn() as conn:
            count = conn.execute(
                "SELECT count(*) FROM share_tokens WHERE session_id='meeting'"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_revoke_immediately_invalidates_payload_and_audio(self):
        created = main.create_share(
            "meeting", request(), lang="en", access_level="full")
        self.assertEqual(main.revoke_share("meeting"), {"revoked": True})
        for lookup in (
            lambda: main.shared_payload(created["token"], Response()),
            lambda: main.shared_audio(created["token"], "mic", request()),
        ):
            with self.assertRaises(HTTPException) as revoked:
                lookup()
            self.assertEqual(revoked.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
