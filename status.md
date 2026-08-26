# Project status

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
