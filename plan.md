# Quill sleep/wake visibility and delivery plan

## Objective

Make a lid-closed meeting visible on the dashboard as soon as capture is safely
finalized, keep its local transcription state accurate, and guarantee eventual
upload even if Quill exits in the narrow window between writing the transcript
and launching the completion hook.

## Current incident

Session `2026.08.29-1415` started at `2026-08-29T07:15:35Z` and finalized at
`09:16:19Z` with 7,244 seconds of capture. macOS entered clamshell sleep five
seconds later and woke five minutes afterward. Whisper resumed on wake and a
live process sample confirmed active Metal decoding, so the recording is not
lost or wedged. It is absent from the dashboard only because Quill currently
waits for the complete local transcript before invoking sync.

## Design

1. Persist an atomic `transcription.json` state beside each recording. Quill
   records queued, transcribing, ready, and failed transitions so a restart,
   uploader, or dashboard never has to infer pipeline state from a process.
2. Invoke the idempotent sync hook when a completed capture enters the queue,
   on meaningful transcription-state changes, and after the transcript is
   ready. Restart recovery republishes unfinished work.
3. Extend `quill-sync` with a metadata-only announcement phase. It uploads only
   `meta.json`, `transcription.json`, and the diagnostic log before a transcript
   exists; raw CAF audio remains local. State-hash markers keep scans cheap.
4. Let dashboard ingest create a placeholder from authoritative recorder
   metadata, with explicit local-transcribing/local-failed states. Arrival of
   `transcript.json` atomically promotes that row into the existing AI queue.
5. Show the local state in the meeting library and meeting page, and poll until
   the transcript arrives. Do not expose chat, regeneration, or sharing against
   an empty transcript.
6. Install a dedicated `com.digimata.quill-sync` LaunchAgent. `RunAtLoad` plus
   five-minute `StartCalendarInterval` entries provide a durable outbox sweep;
   launchd coalesces missed calendar events and runs once on wake. The existing
   PID-owned uploader lock makes hook and scheduled invocations safe together.
7. Mark the recorder's long-running decode as user-initiated work that still
   permits system sleep, while disabling sudden/automatic termination for the
   duration of the queue drain.
8. Retry any failed local session once behind newer work and refuse to publish
   an empty transcript. The durable failed state remains visible if retry also
   fails.

## Files

- `recorder/Sources/QuillSession/SessionManifest.swift`
- `recorder/Sources/quill/Transcription/TranscriptionCoordinator.swift`
- `recorder/Tests/QuillSessionTests/SessionManifestTests.swift`
- `dashboard/mac/quill-sync`
- `dashboard/mac/test-quill-sync.zsh`
- `dashboard/app/db.py`
- `dashboard/app/ingest.py`
- `dashboard/app/main.py`
- `dashboard/static/app.js`
- `dashboard/static/style.css`
- `dashboard/test_reliability.py`
- `install/link.sh`, `install/sync_agent.py`, `install/test_sync_agent.py`
- `README.md`, `SETUP.md`, `AGENTS.md`, `status.md`

## Verification and deployment

1. Test manifest transitions, metadata-only ingest and promotion, uploader
   announcement/idempotence, and LaunchAgent plist generation.
2. Run debug and release Swift suites, dashboard tests, uploader regressions,
   JavaScript syntax checks, and shell syntax checks.
3. Deploy server support first, then install the new uploader and scheduled
   agent on the Mac. Do not restart Quill while the live Whisper child is
   decoding; install the new recorder only after this session finishes.
4. Verify the current session appears, promotes to a complete transcript,
   receives AI notes, exposes all audio tracks, and matches Mac/server hashes.
5. Force a sync-agent run and a recorder restart, confirm both return cleanly,
   then verify Mac/server worktrees and service health.

## Rollback

All new files are additive and session inputs remain immutable. The dashboard
accepts legacy sessions without `transcription.json`; removing the sync agent
returns delivery to the existing completion hook. The previous app executable
is retained before deployment so recorder rollback never touches recordings.
