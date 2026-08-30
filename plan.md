# Quill summary-first dashboard redesign

## Objective

Make a finished meeting useful before anyone opens the transcript. The default
meeting view must answer: what happened, what was decided, what needs doing,
and what remains unresolved. The transcript remains exact, searchable, and
audio-synced, but moves behind a deliberate secondary tab. English/Russian
translation, local-processing visibility, sharing, Ask, audio, and recovery
must keep working.

## Research findings

- Quill currently renders a `5fr / 7fr` split. Even though the latest real
  meeting has substantial topic notes, the transcript is physically the
  largest object and the notes have no short executive brief or explicit
  decision/open-question model.
- Wispr Flow Notetaker's current meeting screen makes **Summary** the default
  tab beside **Transcript**, starts with one concise outcome paragraph, then
  organizes the meeting by topic and next steps. Ask remains immediately
  available without turning the transcript into the home screen.
- The durable pattern to copy is the information hierarchy, not Wispr's brand:
  title and controls -> mode tabs -> readable result -> source transcript.
- Existing Quill data is valuable and must degrade gracefully. Older meetings
  have `overview_md` but no new structured summary fields; the UI will derive a
  useful lead from localized overview text until that meeting is regenerated.

References:

- <https://wisprflow.ai/notetaker>
- <https://wisprflow.ai/post/wispr-flow-notetaker>

## Product structure

### Application shell

Use a compact left workspace rail on desktop and a compact top shell on narrow
screens. The rail contains Quill, Meetings, Action items, Ask, and global
search. It is navigation, not decoration, and leaves the meeting document with
a stable reading width.

### Library

Keep the chronological library, but make cards lead with the generated brief
when present and a clean localized overview fallback otherwise. Processing and
failure states remain visible in-place. Tags and open-action counts remain
secondary metadata.

### Meeting detail

The finished-meeting information architecture is:

```text
┌ workspace rail ┐  ← Meetings                         EN | RU   Share   •••
│ Meetings       │  Meeting title
│ Action items   │  date · time · duration · tags
│ Ask            │
│                │  Summary     Transcript     Ask
│ Search…        │  ─────────────────────────────────────────────────────
└────────────────┘  ┌ readable summary document ┐  ┌ meeting rail      ┐
                    │ At a glance                │  │ Action items      │
                    │ concise outcome paragraph │  │ Timeline          │
                    │                            │  │ Keywords          │
                    │ Decisions                  │  └───────────────────┘
                    │ Topic notes                │
                    │ Open questions             │
                    └────────────────────────────┘
```

- **Summary** is always the default unless a deep link targets a transcript
  moment.
- **Transcript** owns the full reading width and preserves search, speaker
  turns, time rail, click-to-seek, follow mode, and audio playback.
- **Ask** owns a focused conversation view with the existing suggestions and
  history. A compact prompt at the end of Summary switches into this view.
- Timestamp links in decisions, actions, and the timeline switch to Transcript
  and seek to the source moment.
- Share pages use the same Summary/Transcript hierarchy without private
  controls or Ask.

## Visual system

Subject: a private operator's working notebook. Audience: Max, revisiting long
calls to act quickly. Single job: turn a recording into a trusted working
brief.

### Tokens

- `canvas` `#F1F3EF` — cool desk surface, not warm editorial cream
- `paper` `#FFFFFF` — the meeting document
- `ink` `#20231F` — primary text
- `muted` `#6F756F` — metadata and quiet controls
- `line` `#DDE1DA` — structure
- `quill` `#5B57D9` / `quill-soft` `#EFEEFF` — ink accent and focus
- `source` `#2F758A` — timestamps and playback provenance
- `warning` `#B86A35` / `danger` `#B84B52` — pipeline/error states only

Typography uses Charter/Iowan Old Style for meeting titles and lead prose,
the system sans stack for controls and notes, and SF Mono for time. The serif
is restrained to material that reads like a document.

Signature: a slim violet margin rule beside the executive brief and active
meeting tab, echoing an annotated page. This is the one expressive device;
the rest stays quiet and precise.

### Design critique before build

An initial warm parchment/serif concept was too close to the common editorial
AI-dashboard look and to Wispr's marketing palette. The revised system keeps
Wispr's proven hierarchy but uses a cool mineral canvas, white working paper,
and violet/source-blue ink so it reads as Quill rather than a clone. Cards do
not float everywhere: borders and spacing carry most of the structure.

## Data and AI contract

Add a nullable `summary_json` to `sessions` and `artifacts_lang`, plus an
integer `artifacts_revision` on `sessions`:

```json
{
  "brief": "Two direct sentences describing the outcome and why it matters.",
  "decisions": [{"text": "A real decision", "source_ms": 123000}],
  "open_questions": [{"text": "An unresolved issue", "source_ms": 456000}]
}
```

Keep `overview_md` as the detailed topic-organized notes and compatibility
surface. Generation will explicitly separate observations from decisions,
limit topic density, retain exact names/numbers, and avoid invented actions.
Translation returns the same structure and must preserve every timestamp.
Each successful regeneration increments `artifacts_revision`; translation jobs
capture that revision and discard their result if the English notes changed
while they were running. This also protects meetings with no action items,
where action-ID comparison alone cannot detect a regeneration race.

Migration is additive and idempotent. Existing meetings return an empty
structured summary and render a localized fallback from `overview_md`.
Regenerating a meeting upgrades it to the new summary contract without changing
the transcript or completed manual actions.

## Files

- `dashboard/static/index.html` — application shell and accessible landmarks
- `dashboard/static/app.js` — summary model fallback, tabs, new meeting/share
  composition, deep-link behavior, and control menu
- `dashboard/static/style.css` — complete responsive visual system
- `dashboard/app/ai.py` — structured brief/decisions/open-questions contract
- `dashboard/app/db.py` — additive columns and serialization
- `dashboard/app/main.py` — localized summary payloads and strict share DTO
- `dashboard/test_reliability.py` — migration, persistence, localization, and
  backward-compatibility coverage where possible without live AI calls
- `README.md`, `AGENTS.md`, `status.md` — product behavior and shipped state

## Verification and rollout

1. Run Python reliability tests and add storage round-trip coverage for both
   new and legacy artifact shapes.
2. Run JavaScript syntax checks and targeted static assertions for tab and
   accessibility contracts.
3. Start an isolated password-free preview against a SQLite backup of real
   Quill data; visually test desktop, narrow desktop, and mobile.
4. Verify Summary is default, timestamp deep links open Transcript, EN/RU
   localized fallback is never mixed, Ask works, actions toggle, and audio
   controls still bind.
5. Deploy the dashboard, verify health and authenticated API behavior, then
   regenerate the latest real meeting so its summary uses the richer contract.
6. Recheck the live meeting in-browser and verify no console errors, service
   failures, or database integrity issues.

## Rollback

The database change is additive. Old server code ignores `summary_json`, and
the old UI still consumes `overview_md`; reverting the static/backend code does
not require removing columns or regenerating transcripts. Deploy keeps the
recording/session filesystem untouched.

---

# Section 2 — owner-controlled, portable meeting notes

## Why this is next

The summary-first reading hierarchy is shipped. The next usability gap is what
happens after Quill produces the note: the owner cannot correct a title or
summary, copy the useful summary by itself, or export a complete Markdown
record. Wispr's current Notetaker makes ended summaries independently editable,
offers a summary-only copy action, and keeps a separate whole-meeting Markdown
export. Quill should adopt that ownership model without importing a rich-text
editor or weakening its structured source links.

## Product contract

Private meeting owners get three actions in the existing `•••` menu:

1. **Edit notes** opens a focused modal containing title, brief, detailed
   Markdown notes, decisions, and open questions. Structured list rows keep
   their existing source timestamp; newly added rows have no source timestamp.
2. **Copy summary** copies a clean Markdown note containing title, meeting
   metadata, at-a-glance brief, decisions, detailed notes, open questions, and
   action items. It deliberately excludes the verbatim transcript.
3. **Copy full meeting** copies the same note plus a timestamped, speaker-labeled
   transcript. This is an explicit high-volume export, not the default copy.

The editor operates on the language currently visible. English edits update the
canonical AI artifacts, increment `artifacts_revision`, and invalidate
machine-derived translations so a stale Russian cache cannot survive a changed
source. A Russian artifact the owner edited is independent content and survives;
only its action translations refresh. Russian edits update only the Russian
artifact row. Regeneration is an explicit replace operation and will warn if the
owner has edited the note.

Shared views stay read-only. They get **Copy summary**, never edit controls.
Copying must have a prompt fallback when the Clipboard API is unavailable and a
non-blocking localized toast when it succeeds.

## API and validation

Add `PATCH /api/sessions/{session_id}/notes?lang=en|ru` with this body:

```json
{
  "expected_revision": 3,
  "title": "Specific title",
  "overview_md": "### Topic\n- Detail",
  "summary": {
    "brief": "Direct outcome",
    "decisions": [{"text": "Decision", "source_ms": 123000}],
    "open_questions": [{"text": "Question", "source_ms": null}]
  }
}
```

Validation rules:

- meeting must exist, be fully processed, and not currently regenerate;
- title and brief are non-blank and bounded; overview/list text and list counts
  are bounded to prevent an authenticated browser mistake from bloating SQLite;
- timestamps are integers or null and must fall within the recording duration;
- English writes run in one transaction, increment `artifacts_revision`, clear
  machine-derived translated artifacts/action text, preserve owner-edited
  language rows, and set `notes_edited_at`;
- Russian writes require a current Russian artifact and preserve English;
- every language has a monotonic `notes_revision`; a stale editor receives 409
  instead of overwriting a newer save or regeneration;
- API returns the newly rendered language payload, avoiding a second ambiguous
  client-side merge.

Add nullable `notes_edited_at` and integer `notes_revision` to sessions and
artifacts_lang. The timestamp drives the regeneration warning and is returned
as `notes_edited` rather than exposing internal timestamps to guests; the
revision is the optimistic-concurrency token.

## Frontend composition

- Keep the paper as the read surface. Editing happens in a modal rather than
  turning every line into a permanent input.
- List-row controls use plain text inputs, a quiet remove button, and a single
  “Add” action per section. Timestamp provenance remains visible but immutable.
- Escape closes only a clean editor; a dirty editor asks for confirmation.
- Save disables while in flight, reports server errors inline, and rerenders
  the meeting from the returned payload without losing the active Summary tab.
- Export helpers are pure functions so formatting is deterministic and can be
  covered by static/behavioral tests.

## Files

- `dashboard/app/db.py` — additive edit timestamps and transactional language
  update helpers
- `dashboard/app/main.py` — validated PATCH route and returned payload
- `dashboard/static/app.js` — editor modal, deterministic Markdown exporters,
  clipboard fallback, toasts, and regeneration warning
- `dashboard/static/style.css` — modal/editor/list-row/toast states
- `dashboard/test_reliability.py` — storage, revision, invalidation, bounds, and
  timestamp tests
- `README.md`, `AGENTS.md`, `status.md` — new ownership/export behavior

## Verification and rollback

Run migration/reliability and sync regressions, JavaScript/Python syntax checks,
then browser-test English and Russian edits, add/remove rows, dirty-close,
summary/full copy, shared copy, mobile modal layout, and regeneration warning on
an isolated real-data backup. Deploy only after all prior recording/playback
flows remain green. The migration is additive; a rollback ignores edit metadata
and leaves the latest saved note content readable by the previous release.
