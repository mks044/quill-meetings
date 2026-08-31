"""Owner-assigned two-channel voice labels.

The recorder has source separation (microphone/system), not person-level
diarization. These tests protect that immutable role model while ensuring owner
labels propagate to private AI context and the strict anonymous DTO.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import Response

from app import ai, config, db, main


ARTIFACTS = {
    "title": "Named call",
    "overview_md": "### Outcome\n- We agreed.",
    "summary": {
        "brief": "The two sides agreed.",
        "decisions": [],
        "open_questions": [],
    },
    "outline": [],
    "keywords": [],
    "tags": [],
    "actions": [],
}


class SpeakerLabelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data = config.DATA_DIR
        self.old_sessions = config.SESSIONS_DIR
        self.old_db = config.DB_PATH
        config.DATA_DIR = Path(self.temp.name)
        config.SESSIONS_DIR = config.DATA_DIR / "sessions"
        config.DB_PATH = config.DATA_DIR / "quill.db"
        db.init()
        self.segments = [
            {"speaker": "me", "start_ms": 0, "end_ms": 1100,
             "text": "I will send the plan."},
            {"speaker": "them", "start_ms": 1200, "end_ms": 2500,
             "text": "Хорошо, давай завтра."},
        ]
        with db.closing_conn() as conn:
            db.upsert_session(
                conn, "meeting", "2026-08-30T10:00:00+00:00", 3,
                "whisper", self.segments, True, True)
            db.save_ai_artifacts(conn, "meeting", ARTIFACTS)

    def tearDown(self):
        config.DATA_DIR = self.old_data
        config.SESSIONS_DIR = self.old_sessions
        config.DB_PATH = self.old_db
        self.temp.cleanup()

    def _edit(self, revision=0, me=None, them=None, lang="en"):
        return main.edit_speaker_labels(
            "meeting",
            main.SpeakerLabelsEdit(
                expected_revision=revision, me=me, them=them),
            lang=lang,
        )

    def test_set_unicode_same_name_and_reset_without_rewriting_transcript(self):
        with db.closing_conn() as conn:
            before = conn.execute(
                "SELECT segments_hash FROM sessions WHERE id='meeting'"
            ).fetchone()[0]
            before_segments = [dict(row) for row in conn.execute(
                "SELECT * FROM segments WHERE session_id='meeting' ORDER BY idx")]

        named = self._edit(0, "  Макс\n Шама  ", "Дрю")
        self.assertEqual(named["speaker_labels"], {
            "me": "Макс Шама", "them": "Дрю", "revision": 1, "edited": True,
        })
        same = self._edit(1, "Алекс", "Алекс")
        self.assertEqual(same["speaker_labels"]["me"], "Алекс")
        self.assertEqual(same["speaker_labels"]["them"], "Алекс")
        reset = self._edit(2, "   ", None)
        self.assertEqual(reset["speaker_labels"], {
            "me": None, "them": None, "revision": 3, "edited": True,
        })

        with db.closing_conn() as conn:
            row = conn.execute(
                "SELECT segments_hash,artifacts_revision,notes_revision FROM sessions"
                " WHERE id='meeting'").fetchone()
            after_segments = [dict(item) for item in conn.execute(
                "SELECT * FROM segments WHERE session_id='meeting' ORDER BY idx")]
        self.assertEqual(row["segments_hash"], before)
        self.assertEqual(row["artifacts_revision"], 1)
        self.assertEqual(row["notes_revision"], 1)
        self.assertEqual(after_segments, before_segments)

    def test_stale_revision_and_invalid_values_fail_closed(self):
        self._edit(0, "Max", "Drew")
        with self.assertRaises(HTTPException) as stale:
            self._edit(0, "Someone else", "Drew")
        self.assertEqual(stale.exception.status_code, 409)
        with self.assertRaises(HTTPException) as long_name:
            self._edit(1, "x" * 81, "Drew")
        self.assertEqual(long_name.exception.status_code, 422)
        with self.assertRaises(HTTPException) as control:
            self._edit(1, "Max\x00Injected", "Drew")
        self.assertEqual(control.exception.status_code, 422)
        with self.assertRaises(HTTPException) as revision:
            self._edit(-1, "Max", "Drew")
        self.assertEqual(revision.exception.status_code, 422)
        with self.assertRaises(HTTPException) as language:
            self._edit(1, "Max", "Drew", lang="fr")
        self.assertEqual(language.exception.status_code, 400)

        with db.closing_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id,started_at,ai_status)"
                " VALUES ('waiting','2026-08-30T11:00:00+00:00','transcribing')")
        with self.assertRaises(HTTPException) as waiting:
            main.edit_speaker_labels(
                "waiting", main.SpeakerLabelsEdit(
                    expected_revision=0, me="Max", them="Guest"))
        self.assertEqual(waiting.exception.status_code, 409)

    def test_corrupt_stored_labels_fall_back_instead_of_leaking(self):
        with db.closing_conn() as conn:
            conn.execute(
                "UPDATE sessions SET speaker_me_label=?,speaker_them_label=?"
                " WHERE id='meeting'", ("x" * 81, "Bad\x00Name"))
        payload = main.get_session("meeting")
        self.assertEqual(payload["speaker_labels"]["me"], None)
        self.assertEqual(payload["speaker_labels"]["them"], None)
        results = main.search("plan")["results"]
        self.assertIsNone(results[0]["speaker_me_label"])
        self.assertIsNone(results[0]["speaker_them_label"])

    def test_ai_write_rejects_a_changed_voice_snapshot(self):
        with db.closing_conn() as conn:
            run = conn.execute(
                "SELECT segments_hash,speakers_revision FROM sessions WHERE id='meeting'"
            ).fetchone()
        self._edit(0, "Max", "Drew")
        with db.closing_conn() as conn:
            stale = db.save_ai_artifacts(
                conn, "meeting", {**ARTIFACTS, "title": "Stale title"},
                expected_hash=run["segments_hash"],
                expected_speakers_revision=run["speakers_revision"])
            current = conn.execute(
                "SELECT title,speakers_revision FROM sessions WHERE id='meeting'"
            ).fetchone()
            fresh = db.save_ai_artifacts(
                conn, "meeting", {**ARTIFACTS, "title": "Fresh title"},
                expected_hash=run["segments_hash"],
                expected_speakers_revision=current["speakers_revision"])
        self.assertFalse(stale)
        self.assertEqual(current["title"], "Named call")
        self.assertTrue(fresh)

    def test_named_ai_context_keeps_source_roles_explicit(self):
        named = ai.transcript_block(
            self.segments, speaker_labels={"me": "Макс", "them": "Дрю"})
        self.assertIn("[0:00] Макс (me): I will send", named)
        self.assertIn("[0:01] Дрю (them): Хорошо", named)
        self.assertIn("[0:00] me: I will send", ai.transcript_block(self.segments))

        captured = {}
        original = ai.run_codex

        async def fake_run(prompt, *args, **kwargs):
            captured["prompt"] = prompt
            return "grounded answer"

        ai.run_codex = fake_run
        try:
            answer = asyncio.run(ai.chat_session(
                self.segments, [], "Who promised the plan?",
                speaker_labels={"me": 'Max "M"', "them": "Drew"}))
        finally:
            ai.run_codex = original
        self.assertEqual(answer, "grounded answer")
        self.assertIn('"Max \\"M\\"" (me role)', captured["prompt"])
        self.assertIn("Max \"M\" (me): I will send", captured["prompt"])
        self.assertIn("Drew (them): Хорошо", captured["prompt"])

    def test_long_meeting_chunking_retains_the_role_mapping(self):
        captured = {}
        original = ai.run_codex

        async def fake_run(prompt, *args, **kwargs):
            captured["prompt"] = prompt
            return "- dense notes"

        ai.run_codex = fake_run
        try:
            result = asyncio.run(ai._summarize_chunk(
                1, 2, "[0:00] Max (me): Promise.",
                speaker_labels={"me": "Max", "them": "Team"}))
        finally:
            ai.run_codex = original
        self.assertEqual(result, "- dense notes")
        self.assertIn('"Max" (me role)', captured["prompt"])
        self.assertIn('"Team" (them role)', captured["prompt"])

    def test_global_ask_builds_named_retrieval_context(self):
        self._edit(0, "Max", "Drew")
        captured = {}
        original = ai.chat_global

        async def fake_chat(blocks, history, question):
            captured["blocks"] = blocks
            captured["question"] = question
            return "answer"

        async def scenario():
            ai.chat_global = fake_chat
            result = await main.global_ask(main.ChatBody(question="Who sends the plan?"))
            for _ in range(10):
                await asyncio.sleep(0)
                if main.JOBS[result["job_id"]]["status"] != "running":
                    break
            return main.JOBS[result["job_id"]]

        try:
            job = asyncio.run(scenario())
        finally:
            ai.chat_global = original
        self.assertEqual(job["status"], "done")
        self.assertEqual(captured["question"], "Who sends the plan?")
        self.assertIn("Max (me): I will send the plan.", "\n".join(captured["blocks"]))
        self.assertIn("Drew (them): Хорошо", "\n".join(captured["blocks"]))

    def test_search_and_anonymous_dto_receive_only_display_names(self):
        named = self._edit(0, "Max", "Drew")
        self.assertEqual(named["speaker_labels"]["revision"], 1)
        results = main.search("завтра")["results"]
        self.assertEqual(results[0]["speaker_them_label"], "Drew")
        self.assertEqual(results[0]["speaker_me_label"], "Max")

        with db.closing_conn() as conn:
            conn.execute(
                "INSERT INTO share_tokens (token,session_id,lang,access_level)"
                " VALUES ('summary-token','meeting','en','summary')")
        summary = main.shared_payload("summary-token", Response())
        self.assertEqual(summary["speaker_labels"], {"me": "Max", "them": "Drew"})
        self.assertEqual(set(summary["speaker_labels"]), {"me", "them"})
        self.assertNotIn("segments", summary)

        with db.closing_conn() as conn:
            conn.execute(
                "UPDATE share_tokens SET access_level='full'"
                " WHERE token='summary-token'")
        full = main.shared_payload("summary-token", Response())
        self.assertEqual(full["speaker_labels"], {"me": "Max", "them": "Drew"})
        self.assertEqual(full["segments"][1]["speaker"], "them")

    def test_russian_view_survives_a_language_independent_name_edit(self):
        with db.closing_conn() as conn:
            conn.execute(
                """INSERT INTO artifacts_lang
                   (session_id,lang,title,overview_md,summary_json,outline_json,keywords_json)
                   VALUES ('meeting','ru','Звонок','### Итог',?,'[]','[]')""",
                (json.dumps({
                    "brief": "Договорились.", "decisions": [],
                    "open_questions": [],
                }, ensure_ascii=False),))
        payload = self._edit(0, "Макс", "Дрю", lang="ru")
        self.assertEqual(payload["lang"], "ru")
        self.assertEqual(payload["title"], "Звонок")
        self.assertEqual(payload["speaker_labels"]["me"], "Макс")


if __name__ == "__main__":
    unittest.main()
