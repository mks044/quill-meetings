"""Environment-driven configuration. Every value has a working default so the
service runs with an empty environment; .env (python-dotenv) overrides."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Where synced quill sessions land (rsync target) and the SQLite database.
DATA_DIR = Path(os.environ.get("QUILL_DATA", str(Path.home() / "quill-data")))
SESSIONS_DIR = DATA_DIR / "sessions"
DB_PATH = DATA_DIR / "quill.db"

PORT = int(os.environ.get("QUILL_PORT", "7435"))

# AI runs on the Codex CLI (flat-rate ChatGPT sub already authed on the box).
CODEX_BIN = os.environ.get("CODEX_BIN", str(Path.home() / ".local/bin/codex"))
CODEX_MODEL = os.environ.get("CODEX_MODEL", "gpt-5.6-sol")
SUMMARY_EFFORT = os.environ.get("SUMMARY_EFFORT", "high")
CHAT_EFFORT = os.environ.get("CHAT_EFFORT", "medium")
CODEX_TIMEOUT_S = int(os.environ.get("CODEX_TIMEOUT_S", "900"))

# Password gate (public deployment). Cookie is HMAC-signed with SECRET.
PASSWORD = os.environ.get("QUILL_PASSWORD", "")
SECRET = os.environ.get("QUILL_SECRET", "")

# Canonical public origin for share links. Optional: when unset, share URLs are
# built from the incoming request's own origin. Set it when the dashboard is
# reached through several hostnames (localhost, tailnet, public proxy) so links
# always point at the one guests can open.
PUBLIC_BASE = os.environ.get("QUILL_PUBLIC_BASE", "").rstrip("/")

# Notes are written in this language regardless of what was spoken; quoted
# phrases stay verbatim in their original language.
SUMMARY_LANGUAGE = os.environ.get("SUMMARY_LANGUAGE", "English")

# Tag vocabulary the AI prefers when labelling meetings. Comma-separated;
# set it to your own projects/clients for tags that match how you work.
TAG_VOCABULARY = os.environ.get(
    "QUILL_TAGS", "work, personal, client, planning, 1:1, interview, call")

# Bind address. 127.0.0.1 by default: safe on a public VPS, and correct behind
# a TLS reverse proxy or an SSH tunnel. Set 0.0.0.0 (or a VPN interface IP)
# only when you intend the port itself to be reachable, and firewall it.
HOST = os.environ.get("QUILL_HOST", "127.0.0.1")
