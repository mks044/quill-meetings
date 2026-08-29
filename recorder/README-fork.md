# quill (Whisper fork)

> **Fork of [digimata/quill](https://github.com/digimata/quill)** (MIT) with a
> multilingual transcription engine and unattended-recording fixes. All credit
> for the original recorder — Core Audio process taps, dual-track capture, the
> menu-bar app — goes upstream.
>
> **What this fork changes:**
> 1. **OpenAI Whisper `large-v3-turbo`** via the [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
>    CLI replaces Parakeet — ~100 languages with per-track auto-detection, so
>    mixed-language calls (e.g. Russian/English in one sentence) transcribe
>    correctly instead of returning phonetic garbage.
> 2. **Silero VAD** enabled — Whisper hallucinates invented phrases over silence;
>    voice-activity detection removes those stretches before decoding.
> 3. **Fresh decoder context per window** (`-mc 0`) — stops quality decay across
>    long recordings, where an accumulated context drags later windows off-track.
> 4. **`SIGUSR1` toggles recording** — a scriptable control surface for when the
>    menu-bar item is unavailable (hidden behind the notch, or not drawn for a
>    process the window server didn't spawn from your session).
> 5. **Login-item self-registration** (`SMAppService`) when run from a `.app`
>    bundle, so the recorder survives reboots without a LaunchAgent.
> 6. **Auto-stop on system sleep** — closing the lid ends the session cleanly and
>    starts transcription, instead of leaving a phantom "recording" whose audio
>    silently stopped growing.
>
> Pairs with **[quill-dash](https://github.com/mks044/quill-dash)** — a
> self-hosted dashboard that turns these recordings into searchable meeting
> notes with AI summaries, action items, and share links.

---

A minimal, fully local macOS meeting recorder + transcriber. One menu-bar
click records your mic and all system audio as two separate tracks; when you
stop, quill transcribes both on-device and writes a speaker-tagged transcript.
Nothing ever leaves the machine.

Named for the feather. Sibling of [parrot](../parrot/), same skeleton: single
Swift binary, menu-bar tray, no app bundle.

## Install

```sh
cd quill
swift build -c release
sudo cp .build/release/quill /usr/local/bin/quill
quill install --launch-at-login   # optional — runs in the background on login
```

**Requires:** macOS 15+ (Core Audio process taps for system audio — no
virtual device, no kernel extension). Apple Silicon recommended for
transcription speed.

## How to use

1. **Run it** (`quill` in a terminal, or the LaunchAgent).
2. **Click the feather in the menu bar → Start recording.** First use prompts
   for microphone and System Audio Recording permissions. While recording, the
   icon turns red with a running elapsed counter, and macOS shows the purple
   recording indicator.
3. **Click → Stop recording** when the meeting ends. Transcription starts
   automatically (the menu shows progress); a notification fires when the
   transcript is ready.

Each session lands in `~/Recordings/<yyyy.MM.dd-HHmm>/`:

| File | Contents |
|---|---|
| `mic.caf` | your side (default input device, AAC) |
| `system.caf` | everything the Mac played — the other side of the call (AAC) |
| `meta.json` | start/end timestamps, duration, per-track start offsets |
| `transcription.json` | durable queued/transcribing/ready/failed pipeline state |
| `transcript.json` | canonical transcript — engine provenance + timed, speaker-tagged segments |
| `transcript.md` | the same transcript rendered for reading |
| `transcribe.log` | transcription progress/errors for this session |

Two tracks on purpose: speech models do better on clean single-source audio,
and mic-vs-system is free two-party diarization — `me` vs `them` with no
speaker-identification model. CAF on purpose: unlike m4a, it needs no
finalization pass — if the process dies mid-meeting, everything already
written is still readable.

## Transcription

Built in, on-device, automatic. The engine is **OpenAI Whisper
large-v3-turbo** via the [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
CLI (`brew install whisper-cpp`, Metal-accelerated) — multilingual with
per-track language auto-detection, so mixed Russian/English meetings work. The
model (~1.6 GB) downloads once on first transcription into
`~/Library/Application Support/quill/models/`; `quill doctor` tells you
whether it's already cached so you're never downloading after an important
meeting.

Each track is transcribed separately, shifted by its start offset so both
share one clock, and merged by timestamp. Jobs run in a serial queue — you can
start a new recording while the last one transcribes. Unfinished jobs resume
on next launch (the filesystem is the queue: a session with `meta.json` but no
`transcript.json` is pending). Failures append to the session's
`transcribe.log`, move behind later jobs, and retry once. Empty decoder output
is a durable failure rather than an empty successful transcript.

The engine sits behind a small protocol; a Whisper engine (WhisperKit
large-v3-turbo) is planned as the fallback / re-transcription option.

## Config

Optional, at `~/.config/quill/config.json`:

```json
{
  "recordings_dir": "~/Recordings",
  "transcription": { "enabled": true, "engine": "whisper" },
  "on_stop": "my-hook"
}
```

- `recordings_dir` — where sessions land. Resolution order: `--out` flag >
  config > `~/Recordings`.
- `transcription.enabled` — set `false` to just record.
- `mic_voice_processing` — Apple's echo cancellation on the mic (default off).
  Set `true` when recording meetings through the speakers, so playback doesn't
  bleed into the mic track and get transcribed twice as "me". The trade: while
  the voice unit is live, macOS ducks other playback slightly (`.min` ducking
  is configured, but it can't be zeroed). On headphones there's no echo to
  cancel, so raw capture is the better default.
- `on_stop` — shell command spawned with the session directory as its
  argument on durable transcription-state changes (or right after recording if
  transcription is disabled). Hooks must be idempotent. This lets downstream
  systems show processing immediately and ingest the transcript when ready.

## CLI

```sh
quill                        # run the menu-bar daemon (^C to quit)
quill run --out <dir>        # custom recordings root (default ~/Recordings)
quill doctor                 # check permissions, recordings folder, models
quill install --launch-at-login
quill install --uninstall
```

## Stack

- **Swift** — single SPM executable target
- **Core Audio process tap** (`AudioHardwareCreateProcessTap`, macOS 14.2+) —
  system audio capture via a private aggregate device
- **AVAudioEngine** — mic capture
- **AVAudioFile** — streaming AAC encode into CAF
- **whisper.cpp / Whisper large-v3-turbo** — local multilingual transcription (subprocess)
- **NSStatusItem** — the whole UI

## Gotchas

- A global tap records *everything* the Mac plays — notification dings,
  music, all of it. Don't play Spotify during meetings (or ask for a
  per-process picker if it bothers you).
- If recordings come out silent, check System Settings → Privacy & Security →
  Screen & System Audio Recording.
- The binary embeds its Info.plist (`__TEXT,__info_plist`) so TCC can
  attribute permissions to quill itself when running as a LaunchAgent.
