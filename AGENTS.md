# Agent instructions

You are installing **quill-meetings** for your operator: a private meeting
recorder (their Mac) plus a dashboard (their server) that turns recordings into
searchable notes with AI summaries, action items, search, chat, and share links.

Work through the phases in order. Each ends with a check — do not advance past a
failed check. Some steps require a human; ask for them explicitly and wait.

## Phase 0 — Gather (ask the operator, then stop and wait)

1. **Server**: an always-on Linux box (Debian/Ubuntu assumed) with SSH access,
   as `user@host`. Any small VPS works; it does no transcription. No server ⇒
   say so and stop.
2. **Access model** — pick one now, it decides the rest:
   - **SSH tunnel** (simplest, nothing to expose): dashboard stays on
     `127.0.0.1`, they run `ssh -N -L 7435:localhost:7435 user@host`.
   - **VPN/LAN**: bind to the VPN interface IP and firewall the port.
   - **Internet**: needs a hostname + TLS reverse proxy **and** a password.
     Ask them to choose a strong password now.
3. **Codex CLI**: AI notes run on their own ChatGPT subscription via the `codex`
   CLI on the server. Confirm they have a ChatGPT plan.
4. **SSH keys**: the Mac must reach the server without a password prompt. If
   `ssh -o BatchMode=yes user@host true` fails, have them run `ssh-copy-id
   user@host` (one interactive password entry) before Phase 3.
5. **Mac prerequisites**: Xcode Command Line Tools (`xcode-select --install`,
   GUI installer) and Homebrew. Both need a human; check them first.
6. **Language**: notes are generated in one language (default English), with an
   on-demand translation toggle in the UI. Set `SUMMARY_LANGUAGE` in
   `dashboard/.env` for anything else.

## Phase 1 — Recorder (run on the Mac)

```bash
bash install/mac.sh
```

Installs `whisper-cpp` + `ffmpeg`, builds the Swift app, installs
`~/Applications/quill.app`.

**Human-only step — you cannot do this, ask them:** launch the app once
(`open ~/Applications/quill.app`), then in System Settings → Privacy & Security
enable **quill** under **Microphone** and under **Screen & System Audio
Recording**. macOS requires a real click.

**Check:** `pgrep -x quill` returns a pid, and a feather icon is in the menu
bar. If the icon never appears (a full menu bar hides it behind the notch),
create the fallback Desktop button shown at the end of `install/mac.sh`.

## Phase 2 — Dashboard (run on the server)

Install prerequisites and the Codex CLI **first** — `server.sh` refuses to run
without them, by design (it must not record a guessed `codex` path):

```bash
ssh user@host 'sudo apt update && sudo apt install -y python3 python3-venv git rsync curl openssl nodejs npm'
ssh user@host 'npm i -g @openai/codex@latest'
ssh user@host 'codex login --device-auth'   # prints a URL + code; the OPERATOR opens it
ssh user@host 'codex login status'          # must succeed before continuing
```

The CLI must be current — an old one rejects today's models with "requires a
newer version of Codex".

Then clone and install:

```bash
ssh user@host 'git clone https://github.com/mks044/quill-meetings.git ~/quill-meetings'
ssh user@host 'cd ~/quill-meetings && QUILL_PASSWORD="<chosen-password>" bash install/server.sh'
```

Omit `QUILL_PASSWORD` only for a tunnel/VPN-only install. The script verifies
prerequisites, writes `dashboard/.env` (mode 600, managed keys only — safe to
re-run with a password later), creates the venv, enables linger, and installs a
systemd user service **bound to 127.0.0.1:7435** — not exposed to any network
until you deliberately expose it in Phase 4.

**Check:** `ssh user@host 'curl -fsS http://127.0.0.1:7435/api/health'` returns
`{"ok":true}`, and `ssh user@host 'systemctl --user is-active quill-dash'` says
`active`.

## Phase 3 — Link them (run on the Mac)

```bash
bash install/link.sh user@host
```

Writes `~/.config/quill-dash/sync.conf`, installs `~/.local/bin/quill-sync`,
and sets it as the recorder's `on_stop` hook so every finished recording
uploads itself.

**Check (end-to-end, do this — don't assume):** ask the operator to record ~20
seconds of speech and stop. Then:

- `tail ~/.local/state/quill-sync.log` shows `synced <session-id>`
- the dashboard lists the meeting
- within a few minutes its `ai_status` is `done`. Poll it from the server —
  with a password, log in first and reuse the cookie jar:

  ```bash
  ssh user@host '
    curl -sc /tmp/qj -X POST http://127.0.0.1:7435/login \
         --data-urlencode "password=<the-password>" -o /dev/null
    curl -sb /tmp/qj http://127.0.0.1:7435/api/sessions/<session-id>/status'
  ```
  Without a password, drop the login line.

If `ai_status` is `failed`, read the error: it names the cause (usually the
Codex CLI — not logged in, or too old).

## Phase 4 — Access

The service binds `127.0.0.1` — nothing is reachable from outside the server
until you do one of these.

- **SSH tunnel** (default, nothing to expose): on the Mac,
  `ssh -N -L 7435:localhost:7435 user@host`, then open `http://localhost:7435`.
- **VPN/LAN**: set `QUILL_HOST=<vpn-interface-ip>` in `dashboard/.env`, re-run
  `bash dashboard/deploy/deploy.sh`, and firewall port 7435 to that interface
  only.
- **Internet**: terminate TLS in front of it — keep the bind on `127.0.0.1`,
  never open port 7435 itself, and never proxy over plain HTTP; passwords and
  transcripts cross that hop. Caddy is enough:

  ```caddy
  quill.example.com {
      reverse_proxy 127.0.0.1:7435
  }
  ```

  Then set `QUILL_PUBLIC_BASE=https://quill.example.com` in `dashboard/.env` so
  share links point at the public hostname, and restart:
  `systemctl --user restart quill-dash`.

- Optional clean public URL via Vercel: edit `dashboard/vercel-proxy/vercel.json`
  to your HTTPS origin, `cd dashboard/vercel-proxy && vercel deploy --prod`.

## Phase 5 — Hand over

Tell the operator, in their words:

- **Record**: click the feather (or the Desktop button). Closing the laptop lid
  ends the recording cleanly — it transcribes and uploads on wake.
- **Notes** appear automatically; `↻ Regenerate` re-runs the AI on a meeting.
- **Ask**: chat at the top of any meeting, or the "Ask" tab to query every
  meeting at once.
- **Share**: opens a read-only link for one meeting — no account needed on the
  other end, revocable with **Unshare**.
- **Language**: the EN/RU toggle translates notes on demand and remembers the
  choice. The generation language is `SUMMARY_LANGUAGE` in `dashboard/.env`.
- **Backups**: everything lives in `~/quill-data/` on the server (recordings +
  `quill.db`). Back up that one directory.

## Facts worth knowing before you debug

- Audio never leaves the Mac for transcription: Whisper runs on-device. The
  server only receives the finished transcript and audio files.
- A provisional `meta.json` is written when capture starts and atomically
  finalized on stop. A hard power loss therefore remains discoverable by the
  next-launch recovery scan; CAF does not need a finalization pass.
- `afconvert` and `whisper-cli` use file-backed diagnostics rather than bounded
  pipes. Whisper has a 30-minute watchdog (override with
  `QUILL_WHISPER_TIMEOUT_SECONDS`); a timeout is killed, moved behind later
  sessions, and retried once, so one bad recording cannot wedge the queue.
- `quill-sync` is catch-up by design — every run uploads *everything* not yet
  synced, so an offline laptop self-heals. Run it by hand any time.
- Ingest is idempotent; re-uploading a session does not duplicate it. Deleting a
  meeting in the UI tombstones it, so a later sync can't resurrect it.
- The dashboard has no user accounts — one shared password (or none). Share
  links are unguessable tokens scoped to a single meeting.
- Model calls are serialized on the server; a long summary never blocks
  interactive chat (separate queues).
