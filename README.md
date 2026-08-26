# quill-meetings

Your own meeting recorder and dashboard. Record a call on your Mac; minutes
later it's on your own server as searchable notes — transcript with synced
audio, AI summary, action items, chapters, and a chat you can ask about any
meeting.

**No SaaS. No per-token API bills. Your audio never touches a third party.**
Transcription runs on your Mac (Whisper, on-device). The AI notes run through
the Codex CLI on your own server, on a flat-rate ChatGPT subscription.

---

## Install

**Have an agent do it:** hand it this repo and point at
**[AGENTS.md](AGENTS.md)** — a phase-by-phase spec with checks it must verify,
including the two steps only a human can perform (macOS permission dialogs and
the Codex browser login).

**By hand:** [SETUP.md](SETUP.md), ~30 minutes.

```bash
bash install/mac.sh                      # on the Mac  — recorder
bash install/server.sh                   # on the server — dashboard
bash install/link.sh user@your-server    # on the Mac  — connect them
```

## What you get

- **Transcript** — speaker-separated turns, click a line to hear that moment,
  highlight follows the audio, search inside the transcript.
- **AI notes** — title, topic-sectioned overview, chapters, keywords, tags, and
  action items with owners and timestamps. Generated on arrival.
- **Action items** — checkable, aggregated across every meeting on one page.
- **Ask** — chat about one meeting, or across the whole archive.
- **Search** — full-text over every word ever spoken, jumping to the moment.
- **Multilingual** — Whisper handles ~100 languages and mixed-language calls;
  notes generate in your working language with an on-demand translation toggle.
- **Share links** — read-only page for a single meeting, no account needed on
  the other end, revocable in one click.

## How it fits together

```
Mac                                   Your server
─────────────────────────────         ──────────────────────────────────
quill records mic + system  ─┐
Whisper transcribes locally  │
        on_stop hook         │
        quill-sync ──────────┴─rsync──▶ ~/quill-data/sessions/
                                           │
                                        FastAPI + SQLite (FTS5)
                                           │  AI via `codex exec`
                                           ▼
                                        dashboard :7435
```

| Path | What it is |
|---|---|
| `recorder/` | macOS menu-bar recorder + on-device Whisper (Swift) |
| `dashboard/` | FastAPI service, SQLite/FTS5, vanilla-JS SPA, systemd unit |
| `install/` | the three install scripts |
| `AGENTS.md` | machine-executable install spec |

**Requirements:** a Mac on macOS 15+ (Apple Silicon recommended), any always-on
Linux server, and a ChatGPT subscription for the Codex CLI.

## Recovery guarantees

- Quill writes a provisional session manifest as soon as recording starts, so
  a hard power loss still leaves the CAF tracks discoverable on next launch.
- Transcription subprocesses never write into bounded pipes and have a
  30-minute watchdog (`QUILL_WHISPER_TIMEOUT_SECONDS` overrides it). A timed-out
  session is killed, moved behind later work, and retried once.
- Transcripts are written atomically; the upload hook runs only after a complete
  transcript exists, and startup rescans every finalized-but-untranscribed
  session.
- The uploader works newest-first, records the transcript hash after successful
  ingest, and makes notes visible before starting the independent audio phase.
  Audio retries resumably over keepalive SSH, while per-meeting markers prevent
  historical work or one network timeout from blocking newer notes.

## License

MIT. `recorder/` is a fork of [digimata/quill](https://github.com/digimata/quill)
(MIT) — see `recorder/LICENSE` and `recorder/NOTICE.md`.
