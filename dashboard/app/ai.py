"""AI layer: everything goes through the Codex CLI (Sol) on the box —
flat-rate ChatGPT subscription, no per-token billing.

Invocation contract: `codex exec` with the prompt on stdin (argv has a size
limit long meetings exceed), `-s read-only`, `--ephemeral` (no session history
left behind), `--output-last-message` to capture just the answer, and `-C` set
to an empty scratch directory — Codex reads AGENTS.md/CLAUDE.md from its
working directory upward, so running inside this repo would feed the installer
instructions into every summary. Interactive chat and background summaries hold
separate locks so a queued summary never blocks a question.
"""

import asyncio
import json
import re
import subprocess
import tempfile
from pathlib import Path

from . import config

_summary_lock = asyncio.Lock()   # background artifact generation
_chat_lock = asyncio.Lock()      # interactive chat/ask — never behind summaries

# Neutral working directory for every codex run: no AGENTS.md/CLAUDE.md, no
# repo, nothing for the model to pick up as instructions.
_CODEX_CWD = Path(tempfile.gettempdir()) / "quill-codex-cwd"


class AIError(Exception):
    pass


def _run_codex(prompt: str, effort: str, schema: dict | None = None) -> str:
    _CODEX_CWD.mkdir(mode=0o700, parents=True, exist_ok=True)
    out = tempfile.NamedTemporaryFile(
        mode="r", suffix=".txt", delete=False, prefix="quill-codex-")
    out_path = Path(out.name)
    out.close()
    cmd = [
        config.CODEX_BIN, "exec",
        "-m", config.CODEX_MODEL,
        "-c", f"model_reasoning_effort={effort}",
        "-s", "read-only",
        "--skip-git-repo-check",
        "--ephemeral",
        "-C", str(_CODEX_CWD),
        "-o", str(out_path),
    ]
    schema_path = None
    if schema is not None:
        sf = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, prefix="quill-schema-")
        json.dump(schema, sf)
        sf.close()
        schema_path = Path(sf.name)
        cmd += ["--output-schema", str(schema_path)]
    # Prompt goes via stdin ("-" sentinel): argv has a ~128 KiB per-arg limit
    # that long meetings exceed, and stdin closes at EOF (no hang).
    cmd.append("-")

    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=config.CODEX_TIMEOUT_S)
        answer = out_path.read_text().strip() if out_path.exists() else ""
        if proc.returncode != 0 and not answer:
            raise AIError(f"codex exit {proc.returncode}: {proc.stderr[-500:]}")
        if not answer:
            raise AIError("codex returned empty output")
        return answer
    except subprocess.TimeoutExpired:
        raise AIError(f"codex timed out after {config.CODEX_TIMEOUT_S}s")
    finally:
        out_path.unlink(missing_ok=True)
        if schema_path:
            schema_path.unlink(missing_ok=True)


async def run_codex(prompt: str, effort: str, schema: dict | None = None,
                    interactive: bool = False) -> str:
    lock = _chat_lock if interactive else _summary_lock
    async with lock:
        return await asyncio.to_thread(_run_codex, prompt, effort, schema)


def _parse_json(text: str) -> dict:
    """Codex normally honors --output-schema, but guard against fenced or
    prefixed output anyway."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise AIError(f"unparseable AI output: {text[:200]}")
        return json.loads(m.group(0))


# ---------------------------------------------------------------- artifacts

ARTIFACTS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "overview_md", "summary", "outline", "keywords", "tags", "actions"],
    "properties": {
        "title": {"type": "string"},
        "overview_md": {"type": "string"},
        "summary": {
            "type": "object",
            "additionalProperties": False,
            "required": ["brief", "decisions", "open_questions"],
            "properties": {
                "brief": {"type": "string"},
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["text", "source_ms"],
                        "properties": {
                            "text": {"type": "string"},
                            "source_ms": {"type": ["integer", "null"]},
                        },
                    },
                },
                "open_questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["text", "source_ms"],
                        "properties": {
                            "text": {"type": "string"},
                            "source_ms": {"type": ["integer", "null"]},
                        },
                    },
                },
            },
        },
        "outline": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ms", "label"],
                "properties": {"ms": {"type": "integer"}, "label": {"type": "string"}},
            },
        },
        "keywords": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "assignee", "source_ms"],
                "properties": {
                    "text": {"type": "string"},
                    "assignee": {"type": ["string", "null"]},
                    "source_ms": {"type": ["integer", "null"]},
                },
            },
        },
    },
}


def _speaker_context(speaker_labels: dict | None) -> str:
    labels = speaker_labels or {}
    me = str(labels.get("me") or "me").strip() or "me"
    them = str(labels.get("them") or "them").strip() or "them"
    return (f'{json.dumps(me, ensure_ascii=False)} (me role) = the operator '
            f'on the microphone; {json.dumps(them, ensure_ascii=False)} '
            f'(them role) = the other/system-audio side of the call')


def transcript_block(segments, max_chars: int = 120_000,
                     speaker_labels: dict | None = None) -> str:
    lines = []
    labels = speaker_labels or {}
    for s in segments:
        ts = _fmt_ms(s["start_ms"])
        role = "me" if s["speaker"] == "me" else "them"
        label = str(labels.get(role) or "").strip()
        speaker = f"{label} ({role})" if label else role
        lines.append(f"[{ts}] {speaker}: {s['text']}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        # Keep head and tail; meetings rarely exceed this, but never send
        # unbounded input.
        half = max_chars // 2
        text = text[:half] + "\n[... middle truncated ...]\n" + text[-half:]
    return text


def _fmt_ms(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


CHUNK_CHARS = 90_000


async def _summarize_chunk(idx: int, total: int, text: str,
                           speaker_labels: dict | None = None) -> str:
    prompt = f"""Summarize part {idx}/{total} of a meeting transcript into dense notes:
every decision, number, name, commitment, and question, each with its [m:ss] timestamp.
Keep quoted phrases verbatim in their original language. Bullets only.
Speaker mapping: {_speaker_context(speaker_labels)}.

{text}"""
    return await run_codex(prompt, "medium")


async def generate_artifacts(session_id: str, started_at: str, segments,
                             speaker_labels: dict | None = None) -> dict:
    prompt = f"""You are the analysis engine of a private meeting-notes system.
Below is the transcript of one recording (id {session_id}, started {started_at}).
Speakers: {_speaker_context(speaker_labels)}.
Timestamps are [m:ss] from recording start.

Produce a JSON object with:
- title: short specific title for the meeting (max 60 chars, no date, in {config.SUMMARY_LANGUAGE})
- summary: a structured high-signal layer in {config.SUMMARY_LANGUAGE}:
  - brief: one or two direct sentences (30-60 words total) saying what happened, the
    outcome, and why it matters. Lead with the result, not "the meeting discussed".
  - decisions: 0-8 explicit decisions or settled choices only. Each has text and
    source_ms: the supporting transcript timestamp converted from m:ss to
    milliseconds. Do not turn observations, ideas, or general advice into decisions.
  - open_questions: 0-8 genuinely unresolved questions, blockers, or choices.
    Each has text and source_ms in milliseconds. Do not invent uncertainty where
    the call resolved it.
- overview_md: the readable detailed notes in {config.SUMMARY_LANGUAGE}. Use 3-8
  topic-labeled H3 sections (### Topic) with 2-5 tight bullets each. Put the most
  consequential topics first. Preserve exact names, numbers, constraints, and
  reasoning, but merge repetition and omit small talk. Keep quoted phrases verbatim
  in their original language. No preamble, generic recap, conclusion, or duplicated
  action-item section.
- outline: 3-10 chapter markers [{{"ms": <start of the moment in milliseconds>, "label": "<short chapter label>"}}]
  using the transcript timestamps (convert m:ss to ms).
- keywords: 3-8 short topical keywords.
- tags: 1-3 short categorical tags. Prefer these when they fit, else invent one:
  {{tag_vocabulary}}.
- actions: action items stated or implied. Each: text (imperative, specific),
  assignee ("me" if it is the operator's own task, else the person's name,
  null if unclear),
  source_ms (millisecond timestamp of the moment it came from, null if unclear).
  Only real commitments — no invented tasks. Empty array if none.

Transcript:
{{transcript}}

Answer with the JSON object only."""
    prompt = prompt.replace("{tag_vocabulary}", config.TAG_VOCABULARY)
    full = transcript_block(
        segments, max_chars=10_000_000, speaker_labels=speaker_labels)
    if len(full) > CHUNK_CHARS:
        lines = full.split("\n")
        chunks, cur = [], []
        n = 0
        for ln in lines:
            cur.append(ln); n += len(ln) + 1
            if n >= CHUNK_CHARS:
                chunks.append("\n".join(cur)); cur = []; n = 0
        if cur:
            chunks.append("\n".join(cur))
        parts = []
        for i, ch in enumerate(chunks, 1):
            parts.append(await _summarize_chunk(
                i, len(chunks), ch, speaker_labels=speaker_labels))
        digest = "\n\n".join(
            f"--- part {i} notes ---\n{p}" for i, p in enumerate(parts, 1))
        prompt = prompt.replace(
            "{transcript}",
            "The meeting was long; below are dense timestamped notes per part "
            "(compiled from the full transcript):\n\n" + digest)
    else:
        prompt = prompt.replace("{transcript}", full)
    raw = await run_codex(prompt, config.SUMMARY_EFFORT, ARTIFACTS_SCHEMA)
    art = _parse_json(raw)
    for key in ("title", "overview_md"):
        if not isinstance(art.get(key), str) or not art[key].strip():
            raise AIError(f"AI artifacts missing {key}")
    summary = art.get("summary")
    if not isinstance(summary, dict) or not str(summary.get("brief", "")).strip():
        raise AIError("AI artifacts missing summary brief")
    return art


# ---------------------------------------------------------------- chat

async def chat_session(segments, history, question: str,
                       speaker_labels: dict | None = None) -> str:
    convo = "\n".join(
        f"{m['role']}: {m['content']}" for m in history[-10:])
    prompt = f"""You are the assistant of a private meeting-notes system, answering
questions about ONE meeting. Speaker mapping: {_speaker_context(speaker_labels)}.
Answer in the language of the question. Be direct and specific; cite moments as
[m:ss] timestamps taken from the transcript lines you used. If the transcript
doesn't contain the answer, say so plainly.

Transcript:
{transcript_block(segments, speaker_labels=speaker_labels)}

Prior conversation (may be empty):
{convo}

Question: {question}

Answer:"""
    return await run_codex(prompt, config.CHAT_EFFORT, interactive=True)


async def chat_global(context_blocks: list[str], history, question: str) -> str:
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in history[-10:])
    ctx = "\n\n".join(context_blocks) if context_blocks else "(no matching meetings found)"
    prompt = f"""You are the assistant of a private meeting-notes system, answering
questions across ALL of the operator's recorded meetings. Below are the most
relevant excerpts retrieved for this question. A name followed by “(me)” is the
operator; a name followed by “(them)” is the other/system-audio side. Unnamed
roles remain “me” and “them”.
Answer in the language of the question. Be direct; name which meeting(s) (title
and date) each claim comes from, with [m:ss] moments where useful. If the
retrieved context can't answer, say what's missing.

Retrieved context:
{ctx}

Prior conversation (may be empty):
{convo}

Question: {question}

Answer:"""
    return await run_codex(prompt, config.CHAT_EFFORT, interactive=True)


# ---------------------------------------------------------------- translation

TRANSLATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "overview_md", "summary", "outline", "keywords", "actions"],
    "properties": {
        "title": {"type": "string"},
        "overview_md": {"type": "string"},
        "summary": ARTIFACTS_SCHEMA["properties"]["summary"],
        "outline": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ms", "label"],
                "properties": {"ms": {"type": "integer"}, "label": {"type": "string"}},
            },
        },
        "keywords": {"type": "array", "items": {"type": "string"}},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "text"],
                "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
            },
        },
    },
}


async def translate_artifacts(target_lang: str, title: str, overview_md: str,
                              summary: dict,
                              outline: list[dict], keywords: list[str],
                              actions: list[dict]) -> dict:
    """Translate the AI artifacts of one meeting. Items carry stable keys
    (chapter `ms`, action `id`) that MUST come back intact — identity is
    validated by key set, never by position."""
    payload = json.dumps({
        "title": title,
        "overview_md": overview_md,
        "summary": summary,
        "outline": outline,
        "keywords": keywords,
        "actions": actions,
    }, ensure_ascii=False)
    prompt = f"""Translate every text value in this meeting-notes JSON into {target_lang}.
Rules: natural business {target_lang}, not word-for-word; keep markdown structure
(### headings, bullets, **bold**) intact in overview_md; keep numbers, names,
and product terms as-is; quoted phrases already in {target_lang} stay verbatim.
Keep every "source_ms", every "ms", and every "id" EXACTLY as given — they are
keys, not content. Return JSON with the same shape: title, overview_md, summary
(brief, decisions, open_questions), outline (same ms values), keywords, actions
(same id values).

{payload}"""
    raw = await run_codex(prompt, "medium", TRANSLATION_SCHEMA, interactive=True)
    out = _parse_json(raw)
    if {o["ms"] for o in out.get("outline", [])} != {o["ms"] for o in outline}:
        raise AIError("translation lost chapter identity (ms mismatch)")
    if {x["id"] for x in out.get("actions", [])} != {x["id"] for x in actions}:
        raise AIError("translation lost action identity (id mismatch)")
    for key in ("decisions", "open_questions"):
        before = [item.get("source_ms") for item in summary.get(key, [])]
        after = [item.get("source_ms") for item in out.get("summary", {}).get(key, [])]
        if before != after:
            raise AIError(f"translation lost summary identity ({key} timestamp mismatch)")
    return out
