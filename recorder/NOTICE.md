# Attribution

This directory is a fork of **[digimata/quill](https://github.com/digimata/quill)**
by digimata, MIT licensed — see `LICENSE`. All credit for the original recorder
(Core Audio process taps, dual-track capture, the menu-bar app) belongs upstream.

Changes made here:

1. **OpenAI Whisper `large-v3-turbo`** via the [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
   CLI replaces Parakeet — ~100 languages with per-track auto-detection, so
   mixed-language calls transcribe correctly.
2. **Silero VAD** — removes silence before decoding, killing Whisper's
   hallucinated phrases over quiet stretches.
3. **Fresh decoder context per window** (`-mc 0`) — prevents quality decay
   across long recordings.
4. **`SIGUSR1` toggles recording** — scriptable control when the menu-bar item
   isn't usable.
5. **Login-item self-registration** (`SMAppService`) when run as a `.app`.
6. **Auto-stop on system sleep** — a closed lid ends the session cleanly instead
   of leaving a phantom recording.

Upstream tracking: the fork's standalone repo is
[mks044/quill](https://github.com/mks044/quill) (branch `whisper`).
