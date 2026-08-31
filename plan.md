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

---

# Section 3 — privacy-tiered sharing

## Why this is next

The private workspace and editable note are now strong, but Quill's one-click
share still gives any anonymous token holder the summary, verbatim transcript,
and every audio track. Wispr's current link model makes anonymous links
summary-only and reserves transcript access for an explicitly stronger grant.
Quill has no recipient accounts or email invitations, so the closest honest
adaptation is a safe summary-only link by default with a deliberate owner
choice to grant the full meeting through that same revocable link.

## Product contract

The **Share** button opens an owner-only dialog instead of publishing and
copying immediately. It offers two mutually exclusive scopes:

1. **Summary only (recommended)** — title, meeting metadata, brief, decisions,
   detailed notes, open questions, and read-only actions. The guest API does
   not send transcript segments, timeline chapters, audio availability, or
   audio bytes.
2. **Full meeting** — everything above plus transcript, source navigation,
   timeline, and playable audio. The choice carries a clear warning that anyone
   holding the link can hear and read the raw conversation.

New links start on Summary only. Existing links retain Full meeting during the
additive migration so an already-sent link does not silently break, but their
current scope is shown when the owner next opens Share. Updating language or
scope reuses the existing unguessable URL; revoking removes it immediately.
Recipients are always read-only.

Summary-only guests see a labelled summary-only surface and a disabled
Transcript tab explaining that the owner did not share the raw recording.
Source timestamps are non-interactive, no player is loaded, and Copy summary
continues to work. Full guests retain the current transcript/audio experience.

## Storage and authorization

Add `share_tokens.access_level` with values `summary` or `full`:

- new databases use a `summary` default;
- the one-time old-database migration adds a nullable column and marks only
  pre-existing rows `full` for backwards compatibility;
- all application writes explicitly persist a validated value;
- reads fail closed: anything other than the exact string `full` is treated as
  `summary`.

`POST /api/sessions/{id}/share?lang=en|ru&access_level=summary|full` validates
both fields, serializes create/update with an immediate SQLite transaction, and
returns the reused token, URL, language, and effective scope. `GET .../share`
returns the same state without exposing a token anywhere else.

`GET /api/shared/{token}` constructs two allow-list DTOs. The summary DTO omits
`segments`, `outline`, and all `has_audio_*` fields entirely; the full DTO adds
only the current strict transcript/timeline/audio fields. The shared audio route
checks scope before resolving a session or file and returns 403 for a valid
summary-only link. Revocation and invalid tokens remain 404.

## Frontend composition

- Reuse the modal visual language and keyboard discipline from Edit notes:
  focus trap, Escape/close, focus return, inline failure, and mobile layout.
- Radio-card copy explains the actual data grant rather than using vague
  privacy labels.
- **Copy link** both persists the selected scope and copies the URL, with a
  localized toast that says either “Summary link copied” or “Full meeting link
  copied.”
- Existing active links expose **Revoke link** inside the dialog; the existing
  menu shortcut remains but gains confirmation and success feedback.
- The guest renderer branches only on the server-provided effective scope and
  remains resilient to omitted arrays/flags.

## Files

- `dashboard/app/db.py` — additive access-level migration
- `dashboard/app/main.py` — validated share state, fail-closed DTO builder, and
  audio authorization
- `dashboard/static/app.js` — share dialog and scope-aware guest renderer
- `dashboard/static/style.css` — permission cards, compact dialog, locked tab
- `dashboard/test_share_access.py` — API-level scope and audio leakage tests
- `dashboard/test_reliability.py` — migration/backwards-compatibility assertions
- `README.md`, `AGENTS.md`, `status.md` — exact sharing semantics

## Verification and rollback

Use an isolated database with real-shaped summary, transcript, and dummy audio
to prove summary payload and audio denial, same-token scope transitions, full
payload/range audio, validation, and revocation. Browser-test new-owner default,
existing-full state, both guest surfaces, copy feedback, keyboard/mobile layout,
and direct summary-audio denial. Production verification must not change a real
link: inspect migration/state, send only invalid no-op requests, verify any
existing full link remains compatible, compare hashes, logs, queue, and SQLite
integrity. Rolling back code leaves `access_level` ignored and preserves every
token; no recording, transcript, note, or action data is rewritten.

---

# Section 4 — owner-assigned voice names

## Why this is next

Quill still renders every recording as generic **Me / Guest** even when the
same two voices are obvious throughout a 1:1. That weakens transcript scanning,
Markdown exports, search results, Ask answers, and full-meeting shares. Wispr's
refined transcript treats real names as a separate owner-controlled identity
layer and propagates them across reading/export surfaces. Quill cannot honestly
copy Wispr's multi-speaker diarization: its local recorder deliberately produces
two source roles, `me` (microphone) and `them` (system audio). The right feature
is therefore durable names for those two channels, with explicit copy warning
that “other side” may contain several people.

## Product contract

The private Transcript toolbar gains **Name voices** (or **Rename voices** once
set). A focused dialog contains two rows:

- **Your microphone** (`me`) with one or two short sample quotes;
- **Other side / system audio** (`them`) with its own samples and a note that a
  group call should use a collective label such as “Team,” not one person's
  name.

Each name is optional. Blank resets that channel to localized **Me / Guest**.
Names are single-line, whitespace-normalized, at most 80 characters, and may be
the same (two people can share a name). Saving is optimistic and per meeting: a
stale dialog returns 409 instead of overwriting a newer assignment.

Assigned names propagate without rewriting immutable segment rows:

- private and full-share transcript turn labels;
- mic/system player track labels;
- Copy full meeting Markdown;
- full-text search result speaker metadata;
- a compact Voices card in the summary rail (including summary-only shares);
- meeting and global Ask transcript context;
- future summary regeneration, so a deliberate regeneration can use the names.

Existing summary/notes text is never silently rewritten when a voice is named.
That preserves owner edits. If the owner explicitly regenerates notes, the
existing regeneration warning still applies and the current voice names become
AI context. Shared viewers remain read-only and never receive naming controls.

## Storage, API, and races

Add nullable `speaker_me_label`, `speaker_them_label`, integer
`speakers_revision`, and `speakers_edited_at` to `sessions`. The session DTO
exposes one nested allow-list object:

```json
{
  "speaker_labels": {
    "me": "Max",
    "them": "Drew",
    "revision": 2,
    "edited": true
  }
}
```

`PATCH /api/sessions/{id}/speakers` accepts `expected_revision`, `me`, and
`them`, validates/normalizes them, atomically increments the revision, and
returns the fresh private session payload. It requires a transcript but does
not touch segments, notes, translations, actions, or share scope.

AI generation snapshots `speakers_revision` along with the transcript hash. If
labels change mid-run, the stale output is discarded and the normal single-
flight reschedule uses the new labels. Labels do not increment artifact or note
revisions because they are presentation/context metadata, not generated note
content.

Strict shared DTOs include only the two display labels (no revision/edit
metadata). Summary links can show the Voices card but still do not query or send
segments. Full links use the same labels in transcript/player. Any missing or
invalid stored label falls back to the localized role name.

## Frontend and AI composition

- Reuse the established modal, focus trap/return, inline error, toast, and
  mobile bottom-sheet behavior.
- Sample quotes are escaped, bounded, and read from already-loaded segments;
  they are context only and never submitted back.
- Centralize `speakerLabel(session, role, lang)` so transcript, search, player,
  export, and rail cannot drift to different fallback rules.
- Extend `ai.transcript_block` with optional role labels. Prompts explicitly
  retain the semantic mapping “named me-role = operator” so task ownership and
  citations do not become ambiguous.

## Files

- `dashboard/app/db.py` — additive columns, DTO mapping, atomic speaker save,
  and AI stale guard
- `dashboard/app/main.py` — validated PATCH route, search/share projection,
  and named Ask context
- `dashboard/app/ai.py`, `dashboard/app/ingest.py` — named transcript blocks and
  generation snapshot propagation
- `dashboard/static/app.js`, `style.css` — naming dialog and every display/export
  consumer
- `dashboard/test_reliability.py`, `dashboard/test_speaker_labels.py` — storage,
  validation, races, search/share/AI context
- `README.md`, `AGENTS.md`, `status.md` — exact two-channel behavior

## Verification and rollback

Use an isolated real-shaped database to test set/reset, stale save, quotes,
Unicode names, same-name channels, AI stale reschedule guard, private/full/share
payload differences, named Ask blocks, search metadata, and unchanged segment
hash/count. Browser-test EN/RU defaults, naming and resetting, transcript/player,
summary Voices card, full export, summary/full guests, keyboard/mobile layout,
and error state. Production probes must be invalid/stale only so real meetings
remain unnamed until the owner chooses; verify schema, payload fallbacks,
hashes, service logs, queue, and SQLite integrity. Rollback ignores additive
columns and leaves all canonical transcript/audio/note data unchanged.

---

# Section 5 — private meeting notebook

## Why this is next

Quill now has a strong AI summary and a deliberately secondary transcript, but
the owner still has nowhere to write their own thoughts. Editing the generated
summary is correction, not note-taking: the next regeneration may replace it,
and private observations should not be mixed into AI-authored material. Wispr's
current meeting workspace keeps **Thoughts**, **AI Summary**, and **Transcript**
as independent surfaces. Quill should adopt that separation while retaining its
existing summary-first default and strict sharing boundary.

This section is a dashboard notebook for a finalized or server-visible meeting,
not a claim of live collaborative capture. The Mac recorder remains the source
of audio truth; a lid close finalizes capture, and the server-visible processing
row is the earliest honest place the notebook can attach.

## Product contract

Private meeting detail gains a **My notes** tab between Summary and Transcript.
It is one language-neutral Markdown document per meeting: the exact same owner
note appears while the dashboard is set to English or Russian. The note is
independent from generated EN/RU artifacts, so translation and regeneration
never modify or delete it.

The tab contains a calm, full-width writing surface with a short empty-state
prompt, live character count near the limit, and explicit **Saving / Saved /
Couldn't save** feedback. Input autosaves after 800 ms of inactivity; Command-S
or Control-S saves immediately. A save snapshots the submitted text. If typing
continues while that request is in flight, the newer draft is sent again after
the first response rather than being mistaken for saved content.

Optimistic concurrency prevents two dashboard windows from silently
overwriting each other. A 409 keeps the local draft visible and presents two
deliberate recovery actions: **Copy my draft** and **Reload saved note**. There
is no automatic last-write-wins retry after a conflict.

The owner-side **Copy full meeting** export includes a non-empty `My notes`
section before the AI summary. **Copy summary** remains AI-authored summary
only. Existing and future anonymous links, including Full meeting links, never
receive the owner notebook; the tab and database fields are absent from shared
DTOs. The UI states “Private — never included in share links.” This avoids
silently broadening the one already-issued Full link.

## Storage and API

Add three additive fields to `sessions`:

- `owner_notes_md TEXT`
- `owner_notes_revision INTEGER NOT NULL DEFAULT 0`
- `owner_notes_edited_at TEXT`

Private session DTOs expose a nested allow-list object with `markdown`,
`revision`, and `edited`; shared DTO builders do not copy that object.

`PATCH /api/sessions/{session_id}/owner-notes` accepts:

```json
{
  "expected_revision": 2,
  "markdown": "Questions for the next call…"
}
```

The route accepts server-visible processing or completed meetings, normalizes
line endings, rejects NUL/control characters, caps UTF-8 content at 100 KiB,
and atomically updates only when `owner_notes_revision` matches. It returns a
minimal fresh private notebook payload (never the meeting's potentially huge
transcript) so the client advances from a server-owned revision. Re-saving
identical content is a successful no-op with no revision or timestamp change;
clearing a note stores `NULL`, increments the revision, and reports
`edited: false`.

Deletion follows the existing session lifecycle. Ingestion, transcript
promotion, AI notes, translation, action updates, voice naming, sharing, and
regeneration must not write these fields.

## Frontend composition

- Finished private meetings expose Summary / My notes / Transcript / Ask;
  Summary remains the default and transcript timestamp deep links still open
  Transcript.
- Server-visible processing meetings expose My notes beside the processing
  status, allowing the owner to capture context while local Whisper runs.
- The textarea uses the meeting document width, monospace only for Markdown
  punctuation-sensitive entry, an accessible label, and a minimum mobile-safe
  height. It does not introduce a rich-text dependency.
- Tab state is encoded in the URL, so reload/back navigation preserve the
  current surface without turning My notes into the default.
- Leaving the tab does not discard a dirty draft. Page unload warns only while
  an unsaved or conflicted draft exists.
- All strings, save states, empty prompts, errors, and conflict actions are
  localized in English and Russian; the note body itself is never translated.

## Files

- `dashboard/app/db.py` — additive migration, private projection, and atomic
  idempotent save
- `dashboard/app/main.py` — bounded validation and owner-only PATCH route
- `dashboard/static/app.js` — tab, autosave state machine, conflict recovery,
  URL state, and full export composition
- `dashboard/static/style.css` — notebook writing surface and save states
- `dashboard/test_owner_notes.py`, `dashboard/test_reliability.py` — migration,
  privacy, idempotence, conflicts, processing rows, regeneration isolation, and
  validation
- `README.md`, `AGENTS.md`, `status.md` — exact notebook and privacy semantics

## Verification and rollback

Use an isolated real-data copy to prove a Unicode/Markdown note is identical in
EN and RU, rapid typing survives an in-flight save, Command-S works, clearing is
durable, a stale second window cannot overwrite, processing-to-done promotion
preserves the draft, regeneration/translation do not touch it, and both summary
and full shared payloads omit it. Browser-test desktop/mobile, reload/deep-link,
navigation with a pending save, failure/conflict recovery, owner exports, and
all existing Summary/Transcript/Ask/audio flows.

Production rollout is additive and starts with all six notebooks empty at
revision zero. Before migration, back up SQLite and record canonical hashes;
after deployment, use only invalid or stale no-op probes, confirm the existing
Full link still contains no owner-notes key, verify all prior hashes and share
tokens are unchanged, then check queues, service journal, served asset hashes,
and SQLite integrity. Rollback ignores the three columns; no recording,
transcript, generated note, action, speaker label, or share state is rewritten.

---

# Section 6 — private-note discovery in global search

## Why this is next

The private notebook is only useful across time if the owner can find what they
wrote. Quill's global search currently promises every spoken word but returns
only timestamped transcript segments. A note such as “ask Drew about treasury
before Tuesday” disappears unless the owner remembers which meeting contains
it. Search should cover **My notes** without collapsing private writing into the
immutable transcript or broadening any guest surface.

This remains a focused retrieval improvement, not a new AI feature. Generated
summary search, semantic ranking, calendar integration, and Ask access to the
private notebook remain separate decisions.

## Product contract

The authenticated global search returns two explicit result types:

- **My notes** — at most one result per meeting, linked directly to that
  meeting's My notes tab;
- **Transcript** — the existing timestamped moment, linked to the exact audio
  and transcript position.

Results are grouped under localized headings rather than mixing an owner's
private thought with a spoken quote. The page title reports total results, not
“moments.” Each private-note row carries a clear **Private note** label and no
speaker or timestamp; transcript rows retain timestamp and effective voice
name. Matching terms remain highlighted. Empty state and the global input
explain that search covers private notes and transcripts; this section does not
imply title or generated-summary search.

The same quoted-term FTS semantics apply to both sources: punctuation-only and
blank queries return no results, Unicode EN/RU terms work, and all normalized
terms must match within one note or one transcript segment. Results are capped
independently so a long call cannot crowd the owner's notebook out of the page.

## Storage, update, and privacy boundaries

Add a separate `owner_notes_fts` FTS5 table with `text` and an unindexed
`session_id`. Do not place notebook rows into `segments_fts`: transcript index
invariants, segment joins, timestamps, and Ask retrieval must remain unchanged.

`db.init()` rebuilds this derived index from non-empty canonical
`sessions.owner_notes_md` values. This repairs a missing/stale index after an
unclean shutdown or version transition without rewriting the notes themselves.
Each successful notebook change updates its one index row in the same SQLite
transaction; idempotent saves do no index work, clearing removes the row, and
meeting deletion removes it explicitly. Processing rows are indexed exactly
like completed meetings.

`GET /api/search` remains behind the owner password middleware. It returns a
typed allow-list payload; note results contain only session id, title, date,
highlighted snippet, and `kind: owner_note`. They never contain the full note,
revision, edit timestamp, transcript fields, or speaker labels. Existing
`/api/shared/*` builders and summary/full tokens do not query the index, and
there is no anonymous search route. Queries are bounded to 500 characters and
32 word terms before reaching FTS5.

## Frontend composition

- Preserve the current search route and navigation model.
- Keep a compact search form reachable in the mobile library and result page
  because the desktop sidebar search collapses below 460 px.
- A note hit navigates to `#/m/{id}?tab=notes`; a transcript hit preserves the
  existing exact-moment behavior.
- EN/RU headings, counts, labels, placeholders, and empty states follow the
  saved UI language. Search content is never translated.
- Render only escaped server snippets, then convert the two FTS highlight
  sentinels into `<mark>` tags as the transcript result already does.
- Desktop and mobile use the existing calm result-card visual, with a compact
  source pill and separate groups rather than another filter toolbar.

## Files

- `dashboard/app/db.py` — derived private-note FTS table, startup repair, atomic
  save/delete maintenance
- `dashboard/app/main.py` — independently capped, typed private/transcript
  result projections
- `dashboard/static/index.html`, `app.js`, `style.css` — inclusive search copy,
  localized grouped results, and note-tab deep links
- `dashboard/test_owner_notes.py`, `dashboard/test_reliability.py` — Unicode
  retrieval, update/clear/delete, migration repair, typed projections, and
  immutable transcript-index/privacy boundaries
- `README.md`, `AGENTS.md`, `status.md` — exact owner-only search behavior

## Verification and rollback

Use an isolated real-shaped database to test EN/RU Markdown, edits replacing
old terms, clearing and deletion, a processing note, startup index repair,
punctuation-only input, transcript results and voice labels, result caps, and
that `segments_fts` never receives notebook text. Browser-test desktop/mobile
grouping, highlighting, note and timestamp deep links, language copy, empty
state, keyboard submission, and a marker absent from both share scopes.

Production starts with six empty notebooks, so the derived index should contain
zero rows. Back up SQLite before deployment, compare all canonical tables with
the backup, and use an invalid/stale write only. Verify the new index is empty,
the existing Full share still omits private-note fields, public static assets
match, queues are empty, the service journal is clean, and integrity is `ok`.
Rollback can ignore or drop the derived FTS table; canonical notebook,
recording, transcript, AI artifact, action, voice, and share data are unchanged.

---

# Section 7 — summary-led meeting hub

## Why this is next

The meeting detail is now useful, but the library is still a flat stream of
similarly weighted rows. That makes a six-meeting archive tolerable and a
larger archive hard to scan. Wispr's current hub treats finished meetings as
compact records grouped under the day they were actually recorded, with title,
duration, participants, and a one-sentence summary. Quill already has nearly
all of that data and should use it before adding calendar credentials or an
upcoming-meeting model.

This section is deliberately honest about scope: Quill shows recordings that
exist, including processing recordings. It does not invent calendar events,
scheduled start times, attendees, or live-call controls.

## Product contract

The private library becomes a summary-led **Meeting library** grouped by the
recording's actual local calendar day. Today and yesterday use friendly labels;
older groups use localized weekday/date headings and retain newest-first order.
Filtering by tag preserves the same grouping and shows a clear filtered-empty
state rather than an unexplained blank page.

The header reports the visible meeting count and open-action count. Each compact
meeting row exposes, in priority order:

- actual recording time, title, and one-sentence at-a-glance summary;
- duration and a relative-age label;
- processing/failure state when AI or local transcription is not complete;
- open-action count, up to three useful tags, optional assigned voice names,
  and a **My notes** indicator when the private notebook is non-empty.

The summary remains the dominant preview. Keywords do not compete with tags in
the list, and the transcript is not serialized or shown. Clicking anywhere on
the row opens the meeting on its existing default Summary view; processing
rows still open their Status view.

## Data and privacy boundaries

The list API continues to omit `owner_notes_md` and all transcript segments. It
adds only `has_owner_notes: boolean`, derived from a non-empty saved notebook;
revision, edit timestamp, and content remain exclusive to the authenticated
single-meeting payload. Shared DTOs do not receive this indicator or any hub
metadata change.

All counts and grouping are read-only projections. No session, summary, action,
tag, voice, notebook, transcript, audio, or share row is rewritten. Invalid
dates fall into a final localized **Date unavailable** group rather than
breaking the library. Browser-local calendar boundaries determine Today /
Yesterday so the displayed group agrees with the time shown on that device.

## Frontend composition

- Use semantic day sections and heading levels around normal anchor rows.
- Keep tag chips keyboard reachable and retain the existing processing poll.
- Localize hub heading, count grammar, day labels, relative age, action/note
  metadata, processing copy, filtered empty state, and mobile search.
- Desktop rows use a narrow time column, flexible summary body, and restrained
  right-side metadata. Under 650 px, side metadata folds below the summary
  instead of disappearing; under 460 px the mobile search remains available.
- Preserve a calm document-like surface: day groups provide hierarchy, not a
  grid of dashboard widgets.

## Files

- `dashboard/app/db.py`, `dashboard/app/main.py` — private list projection for
  the notebook-presence boolean
- `dashboard/static/app.js`, `style.css` — localized grouping, compact summary
  rows, state metadata, filtered empty state, and responsive hierarchy
- `dashboard/test_owner_notes.py`, `dashboard/test_reliability.py` — strict
  list DTO, note presence/clear behavior, and unchanged share projection
- `README.md`, `AGENTS.md`, `status.md` — exact hub behavior and scope

## Verification and rollback

Use isolated data for same-day grouping, Today/Yesterday, older and invalid
dates, tag filtering, processing/failure rows, EN/RU grammar, named/unnamed
voices, private-note presence and clearing, and zero note/transcript leakage in
list/share payloads. Browser-test the real six-meeting archive on desktop,
responsive rules, tag filters, row navigation, processing polling, and both
languages with no console errors.

Production rollout changes only code and one private boolean projection. Back
up SQLite, compare every canonical table and transcript/private FTS index, make
no valid write probe, verify all six rows report `has_owner_notes: false`, then
check public assets, queues, journal, auth, and integrity. Rollback is a code
revert; no database migration or content restoration is required.
