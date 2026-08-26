# Quill transcription recovery plan

## Objective

Recover the finalized 2026-08-07 and 2026-08-22 recordings, make them visible
with completed AI notes on the dashboard, and prevent one stuck decoder from
blocking every later meeting.

## Root cause

The recorder gives `whisper-cli` pipe-backed stdout and stderr, then waits for
the child to exit before reading either pipe. A sufficiently long transcript
fills stdout, leaving Whisper blocked in `write(2)` and Quill blocked in
`waitUntilExit()` forever. The serial transcription queue cannot advance and
the upload hook never runs.

## Design

1. Add a small testable process-runner target. Route unused stdout to the null
   device and stderr to a temporary file, eliminating bounded-pipe backpressure.
2. Give every subprocess an explicit wall-clock timeout with TERM, a short
   grace period, and KILL fallback. Return only a bounded stderr tail.
3. Use the runner for both `afconvert` and `whisper-cli`. Treat timeouts as
   session failures instead of silently producing a partial/empty transcript.
4. Requeue a timed-out session once behind the remaining queue, so one bad job
   cannot block later recordings and a transient sleep/wake failure self-heals.
5. Add regression tests for output larger than pipe capacity, stderr capture,
   non-zero exit status, and timeout termination.
6. Make the uploader a durable newest-first outbox: transcript-hash markers,
   remote hash bootstrapping, resumable transfers, and per-session failure
   isolation keep old or offline work from blocking the newest meeting. Split
   transcript ingest from audio delivery so notes become visible first and
   partial media resumes independently.
7. Persist notetaker attempt counts and retry deadlines. Retry credential
   failures with capped backoff, bound transient/model retries, and expose
   aggregate notetaker health so failures cannot remain silent or require a
   manual re-ingest after credentials recover.

## Files

- `recorder/Package.swift`
- `recorder/Sources/QuillProcess/ProcessRunner.swift`
- `recorder/Sources/QuillSession/SessionManifest.swift`
- `recorder/Sources/quill/RecordingSession.swift`
- `recorder/Sources/quill/Transcription/WhisperEngine.swift`
- `recorder/Sources/quill/Transcription/TranscriptionCoordinator.swift`
- `recorder/Tests/QuillProcessTests/ProcessRunnerTests.swift`
- `recorder/Tests/QuillSessionTests/SessionManifestTests.swift`
- `dashboard/mac/quill-sync`
- `dashboard/mac/test-quill-sync.zsh`
- `dashboard/app/db.py`
- `dashboard/app/ingest.py`
- `dashboard/app/main.py`
- `dashboard/test_reliability.py`
- `README.md`, `SETUP.md`, and `AGENTS.md`

## Verification and deployment

1. Run debug and release Swift builds plus the full test suite.
2. Commit and push the fix, then fast-forward the canonical life-os checkout.
3. Stop the verified idle old recorder and its wedged child, install the new
   app bundle, and relaunch it.
4. Watch both pending sessions produce transcripts, sync, ingest, and finish
   AI notes. Verify through the authenticated dashboard API and server DB.
5. Confirm no Whisper process remains stuck and both Git worktrees are clean.

## Rollback

The recordings are immutable inputs. If the new binary fails, restore the
previous executable from a timestamped backup and relaunch; no session data is
deleted or rewritten before an atomic transcript write succeeds.
