# quill-meetings

Your own meeting recorder and dashboard. Record a call on your Mac; minutes
later it's on your own server as a summary-first working brief — key outcome,
decisions, open questions, action items, searchable transcript with synced
audio, chapters, and a chat you can ask about any meeting.

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

- **Summary first** — every meeting opens on a concise outcome, explicit
  decisions, topic-organized notes, open questions, and source-linked actions.
- **Private meeting notebook** — **My notes** is a language-neutral Markdown
  page that autosaves independently from AI regeneration and never appears in
  an anonymous share link, including a Full meeting link.
- **Own the AI output** — correct the title or structured EN/RU summary in
  place, copy the useful summary alone, or copy the full meeting as Markdown.
- **Transcript on demand** — a secondary tab with speaker-separated turns;
  click a line to hear that moment, follow the audio, or search every word.
- **Name the voices** — optionally label the microphone and system-audio sides
  once per meeting. Names carry through the transcript, player, search, Ask,
  Markdown export, and shares without rewriting the recorded words or notes.
- **AI notes** — title, readable brief, chapters, keywords, tags, and action
  items with owners and timestamps. Generated on arrival.
- **Action items** — checkable, aggregated across every meeting on one page.
- **Ask** — chat about one meeting, or across the whole archive.
- **Search** — owner-only full-text across private **My notes** and every word
  spoken, with note-tab and exact-moment links kept as distinct result types.
- **Multilingual** — Whisper handles ~100 languages and mixed-language calls;
  notes generate in your working language with an on-demand translation toggle.
- **Share links** — read-only and revocable, with **Summary only** as the safe
  default. The owner must explicitly choose **Full meeting** before an anonymous
  link can expose the transcript or audio; recipients can never edit the note,
  and the private **My notes** page is excluded from both scopes.

Quill separates two audio **sources**, not arbitrary people: `me` is the Mac
microphone and `them` is the call's system audio. On a group call, the other
side can contain several voices, so use a collective label such as “Team.”

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
  Dashboard ingest uses the finalized manifest's UTC start and capture duration
  instead of a timezone-naive folder name or the last spoken segment.
- Transcription subprocesses never write into bounded pipes and have a
  30-minute watchdog (`QUILL_WHISPER_TIMEOUT_SECONDS` overrides it). A failed
  session is moved behind later work and retried once; empty output is a visible
  local failure, never a falsely successful transcript.
- Transcription state is written atomically and published as soon as capture
  finalizes, so the dashboard shows a meeting while Whisper works. Startup
  rescans every finalized-but-untranscribed session.
- The uploader works newest-first, announces finalized metadata before the
  transcript exists, records the transcript hash after successful ingest, and
  makes notes visible before starting the independent audio phase.
  Audio retries resumably over keepalive SSH, while per-meeting markers prevent
  historical work or one network timeout from blocking newer notes. A hook
  that collides with an active upload leaves a pending-rescan flag, so the new
  transcript cannot be lost behind a long media transfer. The lock follows its
  owning process rather than a timer, so hours-long uploads stay serialized and
  a crashed owner is reclaimed immediately.
- A five-minute launchd calendar sweep runs at login and coalesces missed runs
  on wake, closing the crash window between transcript creation and the hook.
- Server-side notetaker failures persist a bounded retry deadline in SQLite.
  Authentication failures retry at an increasing interval (capped at hourly),
  transient failures retry five times, and `/api/health` reports pending,
  running, failed, and retrying counts without exposing meeting content.

## License

MIT. `recorder/` is a fork of [digimata/quill](https://github.com/digimata/quill)
(MIT) — see `recorder/LICENSE` and `recorder/NOTICE.md`.
