# Setup (manual)

> Prefer to have an agent do this? Point it at **[AGENTS.md](AGENTS.md)**.
> Prefer scripts? `install/mac.sh`, `install/server.sh`, `install/link.sh` do
> everything below. This document is the same install spelled out, for when you
> want to understand or adjust each step.

Three parts: the **recorder** (your Mac), the **server** (any always-on Linux
box), and the **link** between them. Budget ~30 minutes for the first install.

## What you need

- A **Mac on macOS 15+** (Apple Silicon recommended) — records and transcribes.
  Needs **Xcode Command Line Tools** (`xcode-select --install`) and **Homebrew**.
- A **Linux server you can SSH into** (Debian/Ubuntu assumed) — hosts the
  dashboard. A €5 VPS is plenty; it does no transcription. ~2 GB free disk for a
  year of meetings. Passwordless SSH from the Mac (`ssh-copy-id user@host`).
- A **ChatGPT subscription** with the [Codex CLI](https://developers.openai.com/codex/cli)
  — powers summaries and chat at no per-token cost. (Any OpenAI-compatible CLI
  can be swapped in at `app/ai.py`.)

---

## Part 1 — Recorder (Mac)

```bash
brew install whisper-cpp ffmpeg
git clone https://github.com/mks044/quill-meetings.git ~/quill-meetings
cd ~/quill-meetings/recorder && swift build -c release
```

Ship it as an app bundle so macOS attributes permissions to it and it can start
at login:

```bash
bash docs/make-app.sh          # builds + installs ~/Applications/quill.app
open ~/Applications/quill.app
```

Then, **once**: System Settings → Privacy & Security → enable **quill** under
both **Microphone** and **Screen & System Audio Recording**.

A feather appears in the menu bar. Click it → Start recording. On stop, quill
transcribes on-device (first run downloads a ~1.6 GB Whisper model) and writes
`~/Recordings/<session>/` containing `transcript.json`, `transcript.md`, and the
two audio tracks.

> **If the menu-bar icon never appears** (a full menu bar hides new items behind
> the notch), quill also toggles on `SIGUSR1`. Make a Desktop double-click
> button:
> ```bash
> printf '#!/bin/zsh\nkill -USR1 $(pgrep -x quill)\n' > ~/Desktop/Record.command
> chmod +x ~/Desktop/Record.command
> ```

---

## Part 2 — Dashboard (server)

```bash
sudo apt update && sudo apt install -y python3 python3-venv git rsync curl openssl nodejs npm
npm i -g @openai/codex@latest && codex login --device-auth   # AI notes run on this
git clone https://github.com/mks044/quill-meetings.git ~/quill-meetings
cd ~/quill-meetings/dashboard
cp .env.example .env      # then edit it — see below
```

Edit `.env`:

- `CODEX_BIN` — absolute path to your `codex` binary (`which codex`).
  Run `codex login` once as this user; verify with `codex login status`.
- `QUILL_PASSWORD` — leave **empty** if the dashboard will only ever be reached
  over a VPN/LAN. Set it if the dashboard will be reachable from the internet;
  then `QUILL_SECRET` is required: `openssl rand -hex 24`.

Install the service:

```bash
mkdir -p ~/quill-data/sessions ~/.config/systemd/user
bash deploy/deploy.sh          # venv, deps, systemd unit, health check
loginctl enable-linger $USER   # survive logout/reboot
```

`deploy.sh` prints `deploy OK @ <commit>` when the service answers. The
dashboard now listens on **:7435**.

> The bundled systemd unit sets `PATH=%h/.local/bin` — Codex is a Node program
> and won't be found without it. Adjust if your `codex` lives elsewhere.

**Reaching it.** Simplest is a private network (Tailscale/WireGuard) and
`http://<server>:7435` — no password needed. To reach it from anywhere, put TLS
in front (Caddy is two lines) and set a password:

```caddy
quill.example.com {
    reverse_proxy 127.0.0.1:7435
}
```

Optionally add a clean public hostname with the included Vercel rewrite: edit
`dashboard/vercel-proxy/vercel.json` to point at your HTTPS origin, then
`cd dashboard/vercel-proxy && vercel deploy --prod`. Keep the origin HTTPS — the proxy hop
carries your transcripts.

---

## Part 3 — Link them

On the **Mac**, give it passwordless SSH to the server and tell it where to send
recordings:

```bash
ssh-copy-id user@your-server         # if you haven't already
mkdir -p ~/.config/quill-dash
cat > ~/.config/quill-dash/sync.conf <<'EOF'
REMOTE="user@your-server"
REMOTE_DIR="/home/user/quill-data/sessions"
DASH_URL="http://localhost:7435"     # dashboard as seen FROM the server
EOF

cp ~/quill-meetings/dashboard/mac/quill-sync ~/.local/bin/quill-sync
chmod +x ~/.local/bin/quill-sync
```

Fire it automatically when a recording ends:

```bash
mkdir -p ~/.config/quill
cat > ~/.config/quill/config.json <<'EOF'
{ "on_stop": "/Users/YOURNAME/.local/bin/quill-sync" }
EOF
```

Test the whole chain: record 20 seconds, stop, then open the dashboard. The
meeting appears within seconds and the AI notes fill in a minute or two later.
Run `quill-sync` by hand any time — it re-syncs everything not yet uploaded, so
a laptop that was offline catches up on its own.

---

## Day-to-day

- **Recording**: click the feather (or the Desktop button). Closing the lid ends
  the session cleanly — it transcribes and syncs on wake.
- **Notes** land automatically. `↻ Regenerate` re-runs the AI on a meeting.
- **Sharing**: open a meeting → **Share** → the link is copied. The recipient
  sees only that meeting, read-only, no password. **Unshare** kills the link.
- **Language**: the EN/RU toggle translates the notes on demand and remembers
  your choice. Change the generation language with `SUMMARY_LANGUAGE` in `.env`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Recordings are silent | System Audio Recording permission not granted to the app bundle |
| Meeting never appears | Check its local `transcribe.log`, then `~/.local/state/quill-sync.log`; run `quill-sync` manually only after `transcript.json` exists. Sync continues past other failed meetings and retries them next run. |
| Transcription timed out | Quill kills it, processes later sessions, then retries once; override the 30-minute watchdog with `QUILL_WHISPER_TIMEOUT_SECONDS` only for unusually slow hardware |
| "AI failed" on a meeting | `codex login status` on the server; `journalctl --user -u quill-dash -n 50` |
| AI failed with a model error | Your Codex CLI is old: `npm i -g @openai/codex@latest` |
| Login loop over plain HTTP | Cookies are `Secure` — use HTTPS, or drop the password on a private network |
| Transcript in the wrong language | Whisper auto-detects per track; verify the mic track actually captured speech |

## Layout

```
recorder/            macOS menu-bar recorder + on-device Whisper (Swift)
dashboard/app/       FastAPI service — ingest, AI, search, chat, sharing
dashboard/static/    the SPA (no build step)
dashboard/mac/       quill-sync — the Mac-side uploader
dashboard/deploy/    deploy.sh + generated systemd unit
install/             mac.sh · server.sh · link.sh
AGENTS.md            machine-executable install spec
```

Data lives in `~/quill-data/` — `sessions/<id>/` files plus `quill.db`. Both are
yours; back up that one directory and you have everything.
