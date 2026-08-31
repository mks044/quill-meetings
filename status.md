# Project status

## Shipped 2026-08-31 — summary-led meeting hub

The private library is now a calm meeting hub rather than a flat undifferentiated
feed. Recordings are grouped under the local day they actually happened, with
Today/Yesterday labels where relevant. Compact semantic rows lead with the
one-sentence at-a-glance summary and retain the actual time, duration, relative
age, open actions, up to three tags, optional assigned voice names, processing
state, and a private **My notes** presence indicator.

The header reports visible meeting/action counts, tag filters preserve day
grouping, and processing/retry states refresh automatically. Invalid dates fall
into a final Date unavailable group instead of breaking the page. Mobile rows
fold all useful metadata below the summary rather than hiding it. EN/RU hub
copy is localized; Russian library rows use translated title/summary artifacts
where they exist and safely fall back to English elsewhere.

The list payload still contains no transcript or notebook body. It adds only
`has_owner_notes: boolean` for the authenticated owner and omits note revision,
edit time, and content. Summary/Full shared DTOs receive neither the notebook
nor its presence indicator. This release has no database migration and writes
no meeting content.

### Verification

- Thirty-two dashboard tests, the sync-agent test, shell sync regression, nine
  Swift recorder tests, JavaScript/Python syntax, and diff checks pass. List
  tests cover RU overlay/EN fallback, strict note-body omission, presence and
  clearing, invalid language, and unchanged guest DTOs.
- Real-data browser checks cover the six-meeting EN/RU archive, day groups,
  localized summaries/counts, tag filtering, default Summary navigation,
  private-note presence without its marker text, responsive rules, and clean
  console logs. Disposable Today/processing/failure/invalid-date rows and the
  1h-60m duration boundary rendered correctly and were removed afterward.
- Live at `a33df3f` after an integrity-checked backup at
  `/home/max/quill-data/backups/quill-before-meeting-hub-20260831T110231Z.db`.
  All six rows report no private notebook, both RU artifacts project correctly,
  and exact backup comparisons show zero changes to every canonical table,
  6,916 transcript-index rows, and the empty private-note index.
- The existing Full share exposes no hub/private fields and the anonymous list
  endpoint returns 401. SQLite integrity is `ok`, queues are empty,
  service/public health is green, served asset hashes match, and the journal is
  clean.

## Shipped 2026-08-31 — private notes in global search

Authenticated global search now finds the owner's **My notes** alongside the
immutable transcript while keeping them as visibly separate result types.
Private-note hits open the meeting's notebook tab; transcript hits retain their
speaker, timestamp, and exact audio position. EN/RU headings, result counts,
empty states, source pills, highlights, and the global search label follow the
saved UI language. Compact search forms keep the flow reachable when the
desktop sidebar collapses on mobile.

Notebook text lives in a dedicated derived FTS5 index rather than transcript
FTS. Successful save/clear/delete operations update it transactionally, and
startup rebuilds it from canonical session notes to repair any stale index.
Queries are bounded before FTS execution. Search stays owner-password gated;
anonymous Summary and Full share paths, meeting/global Ask, and transcript
retrieval never query or receive the private index.

### Verification

- Thirty-two dashboard tests cover Unicode and mixed note/transcript results,
  typed allow-list payloads, edit/clear/delete maintenance, processing notes,
  startup repair, query bounds, migration, immutable transcript FTS, and share
  privacy. The sync-agent test, shell sync regression, nine Swift recorder
  tests, and JavaScript/Python syntax checks also pass.
- Real-data browser checks cover EN/RU groups and live language switching,
  highlighting, note and exact-moment deep links, localized empty state, mobile
  entry contracts, clear-to-index removal, and both guest scopes with no
  console errors.
- Live at `53b89d1` after an integrity-checked backup at
  `/home/max/quill-data/backups/quill-before-private-search-20260831T103632Z.db`.
  The production private index contains zero rows because all six notebooks are
  empty; exact backup comparisons show zero changes to every canonical table
  and the 6,916-row transcript index.
- Production rejected a stale notebook write with 409 and no mutation, returned
  only typed transcript results for an existing query, and kept the existing
  Full share free of private-note fields. Anonymous search returns 401, SQLite
  integrity is `ok`, queues are empty, service/public health is green, served
  asset hashes match, and the journal is clean.

## Shipped 2026-08-31 — private meeting notebook

Every meeting now has a dedicated **My notes** tab between Summary and
Transcript. It is a private, language-neutral Markdown notebook for the owner's
own thoughts rather than another AI artifact: the same note follows EN/RU,
appears while a finalized recording is still processing, autosaves after 800 ms,
and saves immediately with Cmd/Ctrl-S. A save snapshots its draft, so typing
that continues during the request is queued for the next save instead of being
lost.

Revision checks prevent two windows from silently overwriting one another. A
conflict preserves the local draft and offers **Copy draft** or **Reload saved
note**. Notes are normalized, reject control characters, and are bounded at
100 KiB UTF-8. Full owner Markdown export places My notes ahead of the AI
summary; Copy Summary stays AI-only. Both Summary-only and Full guest DTOs omit
private notes, including links issued before this feature.

### Verification

- Thirty dashboard tests, the sync-agent test, the shell sync regression, nine
  Swift recorder tests, JavaScript/Python syntax, and diff checks pass.
- Isolated real-data browser checks cover desktop/mobile EN/RU, Unicode
  Markdown, debounce and keyboard saves, rapid typing during an in-flight save,
  language switching, two-window conflict recovery, processing rows, export,
  and both guest scopes without console errors.
- Live at `039cb0c` after an integrity-checked backup at
  `/home/max/quill-data/backups/quill-before-owner-notes-20260831T091741Z.db`.
  All six existing meetings have empty notes at revision zero; exact logical
  comparisons show zero changes to sessions, 6,916 segments, 40 actions,
  language artifacts, share tokens, deleted rows, chats, and the FTS index.
- Production rejected a stale write with 409 and no mutation; the existing Full
  share serializes no private-note field. SQLite integrity is `ok`, queues are
  empty, the service and public health endpoint are green, served asset hashes
  match the release, and the service journal is clean.

## Shipped 2026-08-31 — owner-assigned voice names

Each processed meeting now has an optional **Name voices** editor in Transcript.
The owner can label the Mac microphone and the call's system-audio side using
short sample quotes for orientation; blank values restore localized **Me /
Guest** defaults. Quill remains honest about its recorder model: these are two
audio-source labels, not person-level diarization, so a group call should use a
collective other-side name such as “Team.”

Names are meeting metadata over the immutable transcript. They propagate to the
private/full-share transcript, mic/system player controls, search results, the
summary Voices rail, full Markdown export, meeting Ask, global Ask, and future
summary regeneration. Existing notes are never silently rewritten. Optimistic
revisions reject a stale naming dialog, and AI output generated while names
change is discarded and rescheduled against the new mapping.

Summary-only shares receive only the two display names and explicitly disclose
that fact; they still do not query or serialize transcript/audio. Shared viewers
have no naming controls. Stored labels are normalized, bounded, escaped for HTML
and Markdown, and fail back to source-role names if database content is invalid.

### Verification

- Twenty-five dashboard tests cover migration, set/reset, Unicode and same-name
  channels, invalid/corrupt values, stale saves, immutable segment/note state,
  AI race rejection, long-call chunk context, search, Ask, and strict
  summary/full DTOs. The sync-agent test and shell sync regression also pass.
- Isolated real-browser checks cover desktop/mobile EN/RU editing, sample
  quotes, player/transcript/Voices propagation, full Markdown (including escaped
  punctuation), search, reset, two-window conflicts, and read-only summary/full
  guest views.
- Live at `75e74bc`: the additive migration followed an integrity-checked SQLite
  backup. All six existing meetings remain unnamed at revision zero; hashes for
  sessions, 6,916 segments, 40 actions, both RU artifacts, all tokens, deleted
  rows, and chat history are unchanged. The existing Full share remains Full.
- Production rejected a stale PATCH with 409 and no mutation. SQLite integrity
  is `ok`, all six meetings are done, queues are empty, the service is active,
  served asset hashes match the release, and the service journal is clean.

## Shipped 2026-08-30 — privacy-tiered sharing

New anonymous links now default to **Summary only**. Their allow-list payload
contains the title, meeting metadata, structured notes, and read-only actions,
but does not query or serialize transcript segments, timeline chapters, audio
availability, or audio bytes. Direct audio requests for a valid summary link
return 403. Guests see a clearly labelled summary-only surface with a locked
Transcript tab and can still copy the useful summary.

The owner Share dialog can explicitly grant **Full meeting** when raw transcript
and playable audio are intended. It shows the current scope, reuses the same
unguessable URL when language/scope changes, confirms revocation, and warns that
anyone holding a full link receives the raw conversation. Existing links were
migrated as Full so already-sent URLs did not silently lose access; the sole live
link retained its token and scope.

### Verification

- Isolated API tests prove strict summary DTOs, 403 audio denial, fail-closed
  corrupt scopes, full payload plus byte-range audio, immediate revocation, and
  simultaneous first-share clicks converging on one token.
- Real-browser checks cover new Summary-only default, existing Full state,
  same-token scope change, owner copy feedback, full transcript/player, locked
  summary guest view, and modal focus trap/return.
- All 17 dashboard/share/sync tests pass with JavaScript/Python syntax and diff
  checks; no skipped assertions or flaky runs were observed.
- Live at `b5c6f3b`: six of six meetings done, queues empty, SQLite integrity
  `ok`, invalid-scope production probe rejected without mutation, legacy full
  DTO/range audio compatible, served hashes exact, and service journal clean.

## Shipped 2026-08-30 — owner-controlled, portable notes

Processed EN and RU notes are now editable from the meeting action menu. The
focused editor covers title, at-a-glance brief, decisions, detailed Markdown
notes, and open questions while preserving existing source timestamps. Edits
are tracked per language, and optimistic revisions reject stale saves instead
of letting a second browser window silently overwrite newer work.

The owner can copy a summary-only Markdown note or a complete Markdown record
with timestamped speaker transcript. Shared meetings remain read-only and can
copy only the summary. Regeneration warns before replacing owner changes;
English edits invalidate machine-derived Russian notes but preserve any RU note
the owner explicitly edited, refreshing only translated action text.

### Verification

- Desktop/mobile editor, EN/RU independence, add/remove rows, dirty-close,
  keyboard focus trap/return, shared copy, and both export sizes passed isolated
  real-browser checks without console errors.
- Dashboard, Mac sync-agent, and shell sync regression suites pass (12 tests in
  total), alongside JavaScript/Python syntax and migration checks.
- A production stale-revision PATCH returned 409 and left the live meeting byte
  state unchanged; private EN/RU payloads expose their own revision while the
  guest DTO exposes no internal edit/revision fields.
- The live service is active at `a4d90e8`; all six meetings are done, queues are
  empty, SQLite integrity is `ok`, deployed/served hashes match the tested
  files, and the service journal contains no warnings.

## Shipped 2026-08-30 — summary-first meeting workspace

The dashboard now opens each meeting on a useful summary instead of a wall of
transcript. The primary page contains a concise “At a glance” brief, explicit
decisions, topic-organized detailed notes, unresolved questions, and a focused
action/timeline rail. Transcript and Ask are dedicated tabs; source timestamps
open the exact transcript moment. The library, private meeting view, read-only
share view, login surface, and mobile layout use the same quiet workspace
design, while the existing EN/RU control remains intact.

AI artifacts now persist a structured brief, decisions, open questions, and
their provenance timestamps for both English and Russian. Artifact revisions
prevent a slow translation from publishing over newly regenerated notes,
including the zero-action race that action-ID checks could not detect. Legacy
rows still render safely from their localized detailed notes.

All six existing meetings were regenerated into the new contract. The two
meetings that previously had Russian notes were translated again, so their RU
views also contain the structured summary. Recordings, transcripts, completed
actions, and audio files were not changed by the backfill.

### Verification

- Desktop, mobile, EN/RU, Transcript, Ask, source deep links, and public-share
  flows passed real-browser checks with no console errors.
- Dashboard reliability, Mac sync-agent, and shell sync regression suites pass.
- All six live summaries have valid non-empty JSON; every decision/open-question
  timestamp is within its recording duration.
- Authenticated EN/RU and strict guest payload checks pass; Russian action text
  is complete for both translated meetings.
- Live queue is empty with no failed/retrying work, the service is active,
  SQLite integrity is `ok`, and deployed static-file hashes match the tested
  release at `b756744`.

## Shipped 2026-08-29 — visible sleep/wake processing and scheduled catch-up

Session `2026.08.29-1415` recorded 7,244 seconds, finalized five seconds before
clamshell sleep, and resumed active Metal transcription after wake. It completed
2,190 transcript segments, AI notes, seven action items, and all three playback
tracks. This incident was healthy processing rather than a repeat deadlock, but
the dashboard had no state until the full local transcript existed.

Quill now writes an atomic `transcription.json` state and invokes its idempotent
sync hook on queued, transcribing, ready, and failed transitions. The uploader
announces finalized metadata before the transcript or audio exists; the
dashboard renders and polls a clear “Transcribing on Mac” placeholder, then
promotes the same row into the AI queue when the transcript arrives.

A dedicated `com.digimata.quill-sync` LaunchAgent runs at login and every five
calendar minutes. Missed calendar events coalesce on wake, closing the crash
window between transcript creation and the completion hook. Its existing
PID-owned lock safely serializes scheduled and hook-triggered runs.

The recovery queue is now newest-first and deduplicated. Every failed local
session retries once behind newer work, and zero usable segments become a
durable visible failure instead of an empty successful transcript. Processing
placeholders cannot be shared, regenerated, or included in global Ask context.

### Verification

- Processing library and detail views passed a real-browser visual check.
- Debug and release recorder suites pass (nine tests).
- Dashboard ingest/promotion tests pass (eight tests).
- Uploader and LaunchAgent regression suites pass.
- The live session completed transcript, notes, actions, and all audio tracks.

## Shipped 2026-08-26 — crash-safe recovery and delivery

The missing Saturday meeting (`2026.08.22-1413`) and the older blocked meeting
(`2026.08.07-2253`) are fully recovered. Their transcripts, microphone audio,
system audio, and mixed audio are present on the server with matching SHA-256
checksums. Both meetings have completed AI notes and action items in the
dashboard.

The root failure was a recorder deadlock: Quill waited for `whisper-cli` to exit
before draining its piped output, so a long transcript could fill the pipe and
block the transcription queue indefinitely. The recorder now redirects output
to bounded files, enforces conversion and transcription deadlines, terminates
stuck subprocesses, creates session metadata at recording start, writes final
metadata atomically, and retries a timed-out transcription once.

Delivery is now failure-isolated and recoverable. Sync processes meetings
newest-first, uploads the transcript before large audio files, retries individual
failures, performs a pending rescan after lock contention, refreshes ingestion
after audio arrives, and uses PID-owned locks so dead workers are reclaimed
without interrupting a valid long upload. SSH uses the stable Tailscale address
with bounded connection and keepalive settings.

The server notetaker now persists retry state, automatically retries transient
and authentication failures, survives polling/database errors, and exposes its
queue and retry state through `/api/health`. Session start times and durations
come from authoritative recorder metadata rather than directory-name estimates.
The recorder is also registered as a macOS LaunchAgent with `RunAtLoad` and
crash restart enabled; a forced restart test confirmed that launchd brings it
back automatically.

## Verification

- Recorder debug and release regression suites pass.
- Sync regression suite passes, including partial failure, lock contention,
  dead-owner recovery, and post-audio reingestion.
- Server ingestion/notetaker tests pass.
- Dashboard range playback works for recovered audio.
- SQLite integrity check passes and the notetaker health queue is clear.
- The Mac launch agent survives a forced restart and the recorder doctor passes.

## Follow-up

The server currently has a working Codex authentication cache and automatic
retry/health visibility. Giving it a completely independent refresh token still
requires an interactive OpenAI device-login approval; this is an account-level
hardening step, not a blocker for recording, transcription, upload, dashboard
visibility, or the current notetaker.
