import json
import sqlite3
import tempfile
import sys
import types
import unittest
from pathlib import Path

# Keep this policy/storage test runnable with the macOS system Python; the
# production virtualenv supplies python-dotenv, but none of these tests need it.
if "dotenv" not in sys.modules:
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda: None
    sys.modules["dotenv"] = dotenv

from app import config, db, ingest


class RetryPolicyTests(unittest.TestCase):
    def test_auth_failures_keep_retrying_with_hourly_cap(self):
        self.assertEqual(ingest.retry_delay_seconds("refresh token already used", 1), 300)
        self.assertEqual(ingest.retry_delay_seconds("access token expired", 20), 3600)

    def test_transient_and_unknown_failures_are_bounded(self):
        self.assertIsNotNone(ingest.retry_delay_seconds("connection timed out", 5))
        self.assertIsNone(ingest.retry_delay_seconds("connection timed out", 6))
        self.assertIsNotNone(ingest.retry_delay_seconds("bad model output", 2))
        self.assertIsNone(ingest.retry_delay_seconds("bad model output", 3))


class RetryPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data = config.DATA_DIR
        self.old_sessions = config.SESSIONS_DIR
        self.old_db = config.DB_PATH
        config.DATA_DIR = Path(self.temp.name)
        config.SESSIONS_DIR = config.DATA_DIR / "sessions"
        config.DB_PATH = config.DATA_DIR / "quill.db"
        db.init()

    def tearDown(self):
        config.DATA_DIR = self.old_data
        config.SESSIONS_DIR = self.old_sessions
        config.DB_PATH = self.old_db
        self.temp.cleanup()

    def test_changed_transcript_resets_failed_retry_state(self):
        segments = [{"speaker": "me", "start_ms": 0, "end_ms": 1000, "text": "hello"}]
        with db.closing_conn() as conn:
            db.upsert_session(conn, "2026.08.26-1000", "2026-08-26T10:00:00", 1,
                              "test", segments, False, False)
            conn.execute(
                """UPDATE sessions SET ai_status='failed', ai_attempts=9,
                   ai_retry_at='2099-01-01 00:00:00' WHERE id='2026.08.26-1000'""")
        changed = [{**segments[0], "text": "updated"}]
        with db.closing_conn() as conn:
            db.upsert_session(conn, "2026.08.26-1000", "2026-08-26T10:00:00", 1,
                              "test", changed, False, False)
            row = conn.execute(
                """SELECT ai_status, ai_attempts, ai_retry_at FROM sessions
                   WHERE id='2026.08.26-1000'""").fetchone()
        self.assertEqual(dict(row), {
            "ai_status": "pending", "ai_attempts": 0, "ai_retry_at": None,
        })

    def test_only_due_failures_are_selected(self):
        with db.closing_conn() as conn:
            for sid, retry_at in (("due", "2000-01-01 00:00:00"),
                                  ("later", "2099-01-01 00:00:00")):
                conn.execute(
                    """INSERT INTO sessions
                       (id, started_at, ai_status, ai_retry_at)
                       VALUES (?, '2026-08-26T10:00:00', 'failed', ?)""",
                    (sid, retry_at))
        self.assertEqual(ingest.due_ai_sessions(), ["due"])

    def test_session_manifest_supplies_exact_start_and_capture_duration(self):
        session_dir = config.SESSIONS_DIR / "2026.08.22-1413"
        session_dir.mkdir(parents=True)
        (session_dir / "transcript.json").write_text(
            '{"engine":"whisper","model":"test","segments":['
            '{"speaker":"me","start_ms":0,"end_ms":1200,"text":"hello"}]}'
        )
        (session_dir / "meta.json").write_text(
            '{"started":"2026-08-22T07:13:33Z","duration_seconds":6089}'
        )
        data = ingest.read_session_dir(session_dir.name)
        self.assertEqual(data["started_at"], "2026-08-22T07:13:33+00:00")
        self.assertEqual(data["duration_s"], 6089)

    def test_folder_time_and_transcript_duration_remain_the_fallback(self):
        session_dir = config.SESSIONS_DIR / "2026.08.26-1000"
        session_dir.mkdir(parents=True)
        (session_dir / "transcript.json").write_text(
            '{"segments":['
            '{"speaker":"me","start_ms":0,"end_ms":2500,"text":"hello"}]}'
        )
        data = ingest.read_session_dir(session_dir.name)
        self.assertEqual(data["started_at"], "2026-08-26T10:00:00")
        self.assertEqual(data["duration_s"], 2.5)

    def test_finalized_capture_is_visible_then_promoted_when_transcript_arrives(self):
        session_dir = config.SESSIONS_DIR / "2026.08.29-1415"
        session_dir.mkdir(parents=True)
        (session_dir / "meta.json").write_text(
            '{"state":"complete","started":"2026-08-29T07:15:35Z",'
            '"duration_seconds":7244}'
        )
        (session_dir / "transcription.json").write_text(
            '{"state":"transcribing","updated":"2026-08-29T09:16:20Z"}'
        )

        result = ingest.ingest_session(session_dir.name)
        self.assertEqual(result, {
            "id": session_dir.name, "segments": 0, "ai_status": "transcribing",
        })
        with db.closing_conn() as conn:
            row = conn.execute(
                "SELECT started_at, duration_s, ai_status, segments_hash FROM sessions"
                " WHERE id=?", (session_dir.name,)
            ).fetchone()
        self.assertEqual(row["started_at"], "2026-08-29T07:15:35+00:00")
        self.assertEqual(row["duration_s"], 7244)
        self.assertEqual(row["ai_status"], "transcribing")
        self.assertIsNone(row["segments_hash"])

        (session_dir / "transcript.json").write_text(
            '{"engine":"whisper","model":"test","segments":['
            '{"speaker":"me","start_ms":0,"end_ms":1200,"text":"hello"}]}'
        )
        result = ingest.ingest_session(session_dir.name)
        self.assertEqual(result["segments"], 1)
        self.assertEqual(result["ai_status"], "pending")

    def test_local_failure_is_visible_but_cannot_demote_ingested_transcript(self):
        sid = "2026.08.29-1500"
        with db.closing_conn() as conn:
            db.upsert_local_session(
                conn, sid, "2026-08-29T08:00:00+00:00", 60,
                state="failed", error="decoder stopped")
            failed = conn.execute(
                "SELECT ai_status, ai_error FROM sessions WHERE id=?", (sid,)
            ).fetchone()
            self.assertEqual(failed["ai_status"], "transcription_failed")
            self.assertEqual(failed["ai_error"], "decoder stopped")

            segments = [{
                "speaker": "me", "start_ms": 0, "end_ms": 1000, "text": "ready",
            }]
            db.upsert_session(
                conn, sid, "2026-08-29T08:00:00+00:00", 60,
                "test", segments, False, False)
            db.upsert_local_session(
                conn, sid, "2026-08-29T08:00:00+00:00", 60,
                state="failed", error="stale announcement")
            promoted = conn.execute(
                "SELECT ai_status, ai_error, segments_hash FROM sessions WHERE id=?", (sid,)
            ).fetchone()
        self.assertEqual(promoted["ai_status"], "pending")
        self.assertIsNone(promoted["ai_error"])
        self.assertIsNotNone(promoted["segments_hash"])

    def test_structured_summary_round_trips_and_legacy_rows_default_empty(self):
        segments = [{
            "speaker": "me", "start_ms": 0, "end_ms": 1000,
            "text": "We will ship on Friday. The budget is still open.",
        }]
        with db.closing_conn() as conn:
            db.upsert_session(
                conn, "structured", "2026-08-30T10:00:00+00:00", 1,
                "test", segments, False, False)
            db.save_ai_artifacts(conn, "structured", {
                "title": "Friday launch",
                "overview_md": "### Launch\n- Ship on Friday.",
                "summary": {
                    "brief": "The launch is set for Friday while budget remains open.",
                    "decisions": [{"text": "Ship on Friday", "source_ms": 0}],
                    "open_questions": [{"text": "What is the budget?", "source_ms": 500}],
                },
                "outline": [], "keywords": [], "tags": [], "actions": [],
            })
            structured = db.session_row_to_dict(conn.execute(
                "SELECT * FROM sessions WHERE id='structured'").fetchone())
            conn.execute(
                """INSERT INTO sessions (id, started_at, overview_md, ai_status)
                   VALUES ('legacy', '2026-08-29T10:00:00+00:00',
                           '### Existing notes', 'done')""")
            legacy = db.session_row_to_dict(conn.execute(
                "SELECT * FROM sessions WHERE id='legacy'").fetchone())

        self.assertEqual(structured["summary"]["decisions"][0]["source_ms"], 0)
        self.assertEqual(structured["summary"]["open_questions"][0]["text"],
                         "What is the budget?")
        self.assertEqual(structured["artifacts_revision"], 1)
        self.assertEqual(legacy["summary"], {})
        with db.closing_conn() as conn:
            translated_columns = {row["name"] for row in conn.execute(
                "PRAGMA table_info(artifacts_lang)")}
            session_columns = {row["name"] for row in conn.execute(
                "PRAGMA table_info(sessions)")}
        self.assertIn("summary_json", translated_columns)
        self.assertIn("notes_edited_at", translated_columns)
        self.assertIn("notes_edited_at", session_columns)
        self.assertIn("notes_revision", translated_columns)
        self.assertIn("notes_revision", session_columns)
        self.assertIn("speaker_me_label", session_columns)
        self.assertIn("speaker_them_label", session_columns)
        self.assertIn("speakers_revision", session_columns)
        self.assertIn("speakers_edited_at", session_columns)

    def test_share_scope_migration_preserves_old_links_but_new_default_is_summary(self):
        with db.closing_conn() as conn:
            columns = {row["name"]: row for row in conn.execute(
                "PRAGMA table_info(share_tokens)")}
        self.assertEqual(columns["access_level"]["notnull"], 1)
        self.assertEqual(columns["access_level"]["dflt_value"], "'summary'")

        legacy_temp = tempfile.TemporaryDirectory()
        legacy_db = Path(legacy_temp.name) / "legacy.db"
        raw = sqlite3.connect(legacy_db)
        raw.execute(
            """CREATE TABLE share_tokens (
                 token TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                 lang TEXT NOT NULL DEFAULT 'en', created_at TEXT)""")
        raw.execute(
            "INSERT INTO share_tokens VALUES ('already-sent','meeting','en','2026-08-01')")
        raw.commit()
        raw.close()
        current_db = config.DB_PATH
        try:
            config.DB_PATH = legacy_db
            db.init()
            with db.closing_conn() as conn:
                migrated = conn.execute(
                    "SELECT access_level FROM share_tokens WHERE token='already-sent'"
                ).fetchone()["access_level"]
            self.assertEqual(migrated, "full")
        finally:
            config.DB_PATH = current_db
            legacy_temp.cleanup()

    def test_english_owner_edit_is_atomic_and_invalidates_translation(self):
        segments = [{
            "speaker": "me", "start_ms": 0, "end_ms": 1000,
            "text": "We will ship on Friday.",
        }]
        art = {
            "title": "Friday launch", "overview_md": "### Launch\n- Friday.",
            "summary": {"brief": "Ship Friday.", "decisions": [],
                        "open_questions": []},
            "outline": [], "keywords": [], "tags": [],
            "actions": [{"text": "Ship it", "assignee": "me", "source_ms": 0}],
        }
        with db.closing_conn() as conn:
            db.upsert_session(conn, "edited", "2026-08-30T10:00:00+00:00", 1,
                              "test", segments, False, False)
            db.save_ai_artifacts(conn, "edited", art)
            conn.execute(
                """INSERT INTO artifacts_lang
                   (session_id,lang,title,overview_md,summary_json,outline_json,keywords_json)
                   VALUES ('edited','ru','Запуск','Заметки','{}','[]','[]')""")
            conn.execute("UPDATE actions SET ru_text='Отправить' WHERE session_id='edited'")
            saved = db.save_manual_notes(
                conn, "edited", "en", "Owner title", "### Owner notes", {
                    "brief": "Owner brief",
                    "decisions": [{"text": "Keep Friday", "source_ms": 0}],
                    "open_questions": [],
                }, expected_revision=1)
            stale = db.save_manual_notes(
                conn, "edited", "en", "Stale title", "", {
                    "brief": "Stale brief", "decisions": [], "open_questions": [],
                }, expected_revision=1)
            row = conn.execute("SELECT * FROM sessions WHERE id='edited'").fetchone()
            translated = conn.execute(
                "SELECT count(*) FROM artifacts_lang WHERE session_id='edited'").fetchone()[0]
            action = conn.execute(
                "SELECT ru_text FROM actions WHERE session_id='edited'").fetchone()
        self.assertTrue(saved)
        self.assertFalse(stale)
        self.assertEqual(row["artifacts_revision"], 2)
        self.assertEqual(row["notes_revision"], 2)
        self.assertIsNotNone(row["notes_edited_at"])
        self.assertEqual(row["title"], "Owner title")
        self.assertEqual(json.loads(row["summary_json"])["brief"], "Owner brief")
        self.assertEqual(translated, 0)
        self.assertIsNone(action["ru_text"])

    def test_russian_owner_edit_preserves_english_revision(self):
        segments = [{
            "speaker": "me", "start_ms": 0, "end_ms": 1000, "text": "Decision.",
        }]
        art = {
            "title": "English title", "overview_md": "### Notes",
            "summary": {"brief": "English brief", "decisions": [],
                        "open_questions": []},
            "outline": [], "keywords": [], "tags": [], "actions": [],
        }
        with db.closing_conn() as conn:
            db.upsert_session(conn, "ru-edit", "2026-08-30T10:00:00+00:00", 1,
                              "test", segments, False, False)
            db.save_ai_artifacts(conn, "ru-edit", art)
            conn.execute(
                """INSERT INTO artifacts_lang
                   (session_id,lang,title,overview_md,summary_json,outline_json,keywords_json)
                   VALUES ('ru-edit','ru','Старое','### Старое',
                           '{"brief":"Старое","decisions":[],"open_questions":[]}',
                           '[]','[]')""")
            saved = db.save_manual_notes(
                conn, "ru-edit", "ru", "Новое название", "### Новые заметки", {
                    "brief": "Новое резюме", "decisions": [], "open_questions": [],
                }, expected_revision=0)
            en = conn.execute(
                "SELECT title,artifacts_revision,notes_edited_at FROM sessions WHERE id='ru-edit'"
            ).fetchone()
            ru = conn.execute(
                "SELECT title,summary_json,notes_edited_at FROM artifacts_lang"
                " WHERE session_id='ru-edit' AND lang='ru'").fetchone()
        self.assertTrue(saved)
        self.assertEqual(dict(en), {
            "title": "English title", "artifacts_revision": 1, "notes_edited_at": None,
        })
        self.assertEqual(ru["title"], "Новое название")
        self.assertEqual(json.loads(ru["summary_json"])["brief"], "Новое резюме")
        self.assertIsNotNone(ru["notes_edited_at"])
        with db.closing_conn() as conn:
            en_saved = db.save_manual_notes(
                conn, "ru-edit", "en", "Updated English", "### Updated", {
                    "brief": "Updated English brief", "decisions": [],
                    "open_questions": [],
                }, expected_revision=1)
            preserved = conn.execute(
                "SELECT title,notes_revision,notes_edited_at FROM artifacts_lang"
                " WHERE session_id='ru-edit' AND lang='ru'").fetchone()
        self.assertTrue(en_saved)
        self.assertEqual(preserved["title"], "Новое название")
        self.assertEqual(preserved["notes_revision"], 1)
        self.assertIsNotNone(preserved["notes_edited_at"])


if __name__ == "__main__":
    unittest.main()
