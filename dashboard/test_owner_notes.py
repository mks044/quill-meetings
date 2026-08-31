"""Private owner notebook storage, concurrency, and sharing boundaries."""

import json
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import Response

from app import config, db, main


ARTIFACTS = {
    "title": "Planning call",
    "overview_md": "### Result\n- The plan is ready.",
    "summary": {"brief": "The plan is ready.", "decisions": [],
                "open_questions": []},
    "outline": [],
    "keywords": [],
    "tags": [],
    "actions": [],
}


class OwnerNotesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data = config.DATA_DIR
        self.old_sessions = config.SESSIONS_DIR
        self.old_db = config.DB_PATH
        config.DATA_DIR = Path(self.temp.name)
        config.SESSIONS_DIR = config.DATA_DIR / "sessions"
        config.DB_PATH = config.DATA_DIR / "quill.db"
        db.init()
        with db.closing_conn() as conn:
            conn.execute(
                """INSERT INTO sessions
                   (id,started_at,duration_s,title,overview_md,summary_json,
                    outline_json,keywords_json,tags_json,ai_status,segments_hash)
                   VALUES ('meeting','2026-08-31T09:00:00+00:00',60,
                           'Planning call','### Result',?,'[]','[]','[]','done','hash')""",
                (json.dumps(ARTIFACTS["summary"]),))
            conn.execute(
                """INSERT INTO segments
                   (session_id,idx,speaker,start_ms,end_ms,text)
                   VALUES ('meeting',0,'me',0,1000,'Private transcript words')""")
            conn.execute(
                """INSERT INTO artifacts_lang
                   (session_id,lang,title,overview_md,summary_json,outline_json,keywords_json)
                   VALUES ('meeting','ru','Планирование','### Результат',?,'[]','[]')""",
                (json.dumps({"brief": "План готов.", "decisions": [],
                             "open_questions": []}, ensure_ascii=False),))

    def tearDown(self):
        config.DATA_DIR = self.old_data
        config.SESSIONS_DIR = self.old_sessions
        config.DB_PATH = self.old_db
        self.temp.cleanup()

    def _edit(self, markdown, revision=0, lang="en", session_id="meeting"):
        return main.edit_owner_notes(
            session_id,
            main.OwnerNotesEdit(
                expected_revision=revision, markdown=markdown),
            lang=lang,
        )

    def test_unicode_markdown_is_language_neutral_and_clear_is_durable(self):
        note = "## Моё\r\n\r\n- Спросить Дрю\n- Keep **exact** `$value`"
        saved = self._edit(note)
        expected = note.replace("\r\n", "\n")
        self.assertEqual(saved["owner_notes"], {
            "markdown": expected, "revision": 1, "edited": True,
        })
        self.assertEqual(
            main.get_session("meeting", lang="ru")["owner_notes"],
            saved["owner_notes"])

        # An autosave retry of identical content is idempotent.
        same = self._edit(expected, revision=1, lang="ru")
        self.assertEqual(same["owner_notes"]["revision"], 1)

        cleared = self._edit(" \n\t ", revision=1)
        self.assertEqual(cleared["owner_notes"], {
            "markdown": "", "revision": 2, "edited": False,
        })
        with db.closing_conn() as conn:
            row = conn.execute(
                """SELECT owner_notes_md,owner_notes_revision,
                          owner_notes_edited_at FROM sessions WHERE id='meeting'"""
            ).fetchone()
        self.assertIsNone(row["owner_notes_md"])
        self.assertEqual(row["owner_notes_revision"], 2)
        self.assertIsNone(row["owner_notes_edited_at"])

    def test_stale_window_cannot_overwrite_and_validation_is_bounded(self):
        self._edit("first window")
        with self.assertRaises(HTTPException) as stale:
            self._edit("second window", revision=0)
        self.assertEqual(stale.exception.status_code, 409)
        self.assertEqual(
            main.get_session("meeting")["owner_notes"]["markdown"],
            "first window")

        with self.assertRaises(HTTPException) as control:
            self._edit("bad\x00note", revision=1)
        self.assertEqual(control.exception.status_code, 422)
        with self.assertRaises(HTTPException) as c1_control:
            self._edit("bad\x85note", revision=1)
        self.assertEqual(c1_control.exception.status_code, 422)
        with self.assertRaises(HTTPException) as too_large:
            self._edit("я" * (51_201), revision=1)
        self.assertEqual(too_large.exception.status_code, 422)
        with self.assertRaises(HTTPException) as revision:
            self._edit("valid", revision=-1)
        self.assertEqual(revision.exception.status_code, 422)
        with self.assertRaises(HTTPException) as language:
            self._edit("valid", revision=1, lang="fr")
        self.assertEqual(language.exception.status_code, 400)
        with self.assertRaises(HTTPException) as missing:
            self._edit("valid", session_id="missing")
        self.assertEqual(missing.exception.status_code, 404)

    def test_processing_promotion_and_ai_regeneration_preserve_notebook(self):
        with db.closing_conn() as conn:
            db.upsert_local_session(
                conn, "processing", "2026-08-31T10:00:00+00:00", 120)
        processing = self._edit(
            "Question before the transcript is ready", session_id="processing")
        self.assertEqual(processing["owner_notes"]["revision"], 1)
        self.assertEqual(set(processing), {"id", "owner_notes"})
        self.assertEqual(main.get_session("processing")["ai_status"], "transcribing")

        segments = [{"speaker": "me", "start_ms": 0, "end_ms": 1000,
                     "text": "Now transcribed."}]
        with db.closing_conn() as conn:
            db.upsert_session(
                conn, "processing", "2026-08-31T10:00:00+00:00", 120,
                "whisper", segments, True, False)
            db.save_ai_artifacts(conn, "processing", ARTIFACTS)
        promoted = main.get_session("processing")
        self.assertEqual(promoted["ai_status"], "done")
        self.assertEqual(promoted["owner_notes"], {
            "markdown": "Question before the transcript is ready",
            "revision": 1,
            "edited": True,
        })

        with db.closing_conn() as conn:
            db.save_ai_artifacts(
                conn, "processing", {**ARTIFACTS, "title": "Regenerated"})
        regenerated = main.get_session("processing")
        self.assertEqual(regenerated["title"], "Regenerated")
        self.assertEqual(regenerated["owner_notes"], promoted["owner_notes"])

    def test_library_and_both_share_scopes_never_expose_private_note(self):
        private_text = "Never expose marker 7e673e"
        self._edit(private_text)
        library_row = main.list_sessions()["sessions"][0]
        self.assertNotIn("owner_notes", library_row)
        self.assertNotIn(private_text, json.dumps(library_row))

        with db.closing_conn() as conn:
            conn.execute(
                """INSERT INTO share_tokens (token,session_id,lang,access_level)
                   VALUES ('private-test','meeting','en','summary')""")
        for access_level in ("summary", "full"):
            with db.closing_conn() as conn:
                conn.execute(
                    "UPDATE share_tokens SET access_level=? WHERE token='private-test'",
                    (access_level,))
            payload = main.shared_payload("private-test", Response())
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("owner_notes", payload)
            self.assertNotIn(private_text, serialized)
            self.assertEqual(payload["access_level"], access_level)

    def test_schema_migration_fields_are_additive(self):
        with db.closing_conn() as conn:
            columns = {row["name"]: row for row in conn.execute(
                "PRAGMA table_info(sessions)")}
        self.assertIn("owner_notes_md", columns)
        self.assertEqual(columns["owner_notes_revision"]["dflt_value"], "0")
        self.assertIn("owner_notes_edited_at", columns)


if __name__ == "__main__":
    unittest.main()
