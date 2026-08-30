/* Quill dashboard SPA. Hash routing, no framework.
   Views: library (#/), meeting (#/m/{id}), actions (#/actions), ask (#/ask),
   search (#/search/{q}). Player is a global singleton bound to the open meeting. */

"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const view = $("#view");
const audio = $("#audio");

// ---------------------------------------------------------------- utils

const fmt = (ms) => {
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
           : `${m}:${String(sec).padStart(2, "0")}`;
};
const fmtDur = (sec) => sec >= 3600
  ? `${Math.floor(sec / 3600)}h ${Math.round((sec % 3600) / 60)}m`
  : sec >= 60 ? `${Math.round(sec / 60)} min` : `${Math.round(sec)}s`;
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const dateParts = (iso) => {
  const d = new Date(iso);
  return {
    day: d.toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    year: d.getFullYear(),
    time: d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }),
  };
};
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (r.status === 401) { location.reload(); throw new Error("unauthorized"); }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const post = (path, body) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}) });

// Chat endpoints return {job_id}; poll until done (proxy-timeout-safe).
async function postJob(path, body) {
  const { job_id } = await post(path, body);
  for (let i = 0; i < 1200; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    let j;
    try { j = await api(`/api/jobs/${job_id}`); }
    catch (e) {
      if (String(e.message).includes("unknown job")) throw new Error("server restarted — ask again");
      continue;
    }
    if (j.status === "done") return { answer: j.answer };
    if (j.status === "failed") throw new Error(j.error || "AI failed");
  }
  throw new Error("timed out");
}

// Minimal markdown: ### headings, - bullets, **bold**, paragraphs.
function md(src) {
  const lines = (src || "").split("\n");
  let out = "", inList = false;
  const inline = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  for (const ln of lines) {
    if (/^###\s/.test(ln)) { if (inList) { out += "</ul>"; inList = false; } out += `<h3>${inline(ln.slice(4))}</h3>`; }
    else if (/^[-*]\s/.test(ln)) { if (!inList) { out += "<ul>"; inList = true; } out += `<li>${inline(ln.slice(2))}</li>`; }
    else if (ln.trim() === "") { if (inList) { out += "</ul>"; inList = false; } }
    else { if (inList) { out += "</ul>"; inList = false; } out += `<p>${inline(ln)}</p>`; }
  }
  if (inList) out += "</ul>";
  return out;
}

const copy = (lang, en, ru) => lang === "ru" ? ru : en;

function overviewLead(src, max = 420) {
  const lines = String(src || "").split("\n").map((line) => line.trim());
  const bullets = lines.filter((line) => /^[-*]\s/.test(line))
    .slice(0, 2).map((line) => line.slice(2).replace(/\*\*/g, ""));
  const prose = lines.filter((line) => line && !/^#{1,6}\s|^[-*]\s/.test(line))
    .slice(0, 2).map((line) => line.replace(/\*\*/g, ""));
  const text = (bullets.length ? bullets : prose).join(" ");
  if (text.length <= max) return text;
  const cut = text.slice(0, max + 1).replace(/\s+\S*$/, "");
  return `${cut}…`;
}

function summaryModel(s) {
  const raw = s.summary && typeof s.summary === "object" ? s.summary : {};
  const items = (value) => Array.isArray(value) ? value.filter((item) =>
    item && typeof item === "object" && String(item.text || "").trim()).map((item) => ({
      text: String(item.text).trim(),
      source_ms: item.source_ms != null && Number.isFinite(+item.source_ms) ? +item.source_ms : null,
    })) : [];
  return {
    brief: String(raw.brief || "").trim() || overviewLead(s.overview_md),
    decisions: items(raw.decisions),
    openQuestions: items(raw.open_questions),
  };
}

const mdLine = (value) => String(value || "").replace(/\s*\n\s*/g, " ").trim();

function summaryMarkdown(s, lang) {
  const summary = summaryModel(s);
  const when = new Date(s.started_at).toLocaleString(lang === "ru" ? "ru-RU" : "en-US", {
    year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
  const lines = [`# ${mdLine(s.title || s.id)}`, "", `${when} · ${fmtDur(s.duration_s)}`, ""];
  if (summary.brief) lines.push(`## ${copy(lang, "At a glance", "Главное")}`, "", summary.brief, "");
  if (summary.decisions.length) {
    lines.push(`## ${copy(lang, "Decisions", "Решения")}`, "");
    for (const item of summary.decisions)
      lines.push(`- ${mdLine(item.text)}${item.source_ms != null ? ` (${fmt(item.source_ms)})` : ""}`);
    lines.push("");
  }
  if (s.overview_md) lines.push(`## ${copy(lang, "Detailed notes", "Подробные заметки")}`, "", s.overview_md.trim(), "");
  if (summary.openQuestions.length) {
    lines.push(`## ${copy(lang, "Still open", "Осталось решить")}`, "");
    for (const item of summary.openQuestions)
      lines.push(`- ${mdLine(item.text)}${item.source_ms != null ? ` (${fmt(item.source_ms)})` : ""}`);
    lines.push("");
  }
  if ((s.actions || []).length) {
    lines.push(`## ${copy(lang, "Action items", "Задачи")}`, "");
    for (const action of s.actions) {
      const owner = action.assignee ? ` @${mdLine(action.assignee)}` : "";
      const source = action.source_ms != null ? ` (${fmt(action.source_ms)})` : "";
      lines.push(`- [${action.done ? "x" : " "}] ${mdLine(action.text)}${owner}${source}`);
    }
    lines.push("");
  }
  return lines.join("\n").trim() + "\n";
}

function fullMeetingMarkdown(s, lang) {
  const lines = [summaryMarkdown(s, lang).trim(), "", `## ${copy(lang, "Transcript", "Транскрипт")}`, ""];
  for (const segment of s.segments || []) {
    const speaker = segment.speaker === "me" ? copy(lang, "Me", "Я") : copy(lang, "Guest", "Собеседник");
    lines.push(`[${fmt(segment.start_ms)}] **${speaker}:** ${mdLine(segment.text)}`);
  }
  return lines.join("\n").trim() + "\n";
}

function showToast(message) {
  let region = $("#toast-region");
  if (!region) {
    region = document.createElement("div");
    region.id = "toast-region";
    region.className = "toast-region";
    region.setAttribute("aria-live", "polite");
    document.body.appendChild(region);
  }
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  region.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("visible"));
  setTimeout(() => {
    toast.classList.remove("visible");
    setTimeout(() => toast.remove(), 180);
  }, 2200);
}

async function copyText(text, lang, successMessage) {
  let copied = false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      copied = true;
    }
  } catch { /* fall through to the local textarea path */ }
  if (!copied) {
    const area = document.createElement("textarea");
    area.value = text;
    area.className = "clipboard-buffer";
    area.setAttribute("readonly", "");
    document.body.appendChild(area);
    area.select();
    try { copied = document.execCommand("copy"); } catch { copied = false; }
    area.remove();
  }
  if (copied) showToast(successMessage);
  else prompt(copy(lang, "Copy the Markdown:", "Скопируйте Markdown:"), text);
  return copied;
}

function editRowsHTML(items, kind, lang) {
  const kindLabel = kind === "decision"
    ? copy(lang, "decision", "решение")
    : copy(lang, "open question", "открытый вопрос");
  return items.map((item) => `<div class="note-edit-row" data-source-ms="${item.source_ms ?? ""}">
    <input value="${esc(item.text)}" maxlength="2000" aria-label="${esc(copy(lang, `Edit ${kindLabel}`, `Изменить: ${kindLabel}`))}">
    <span class="edit-source">${item.source_ms != null ? fmt(item.source_ms) : copy(lang, "No source", "Без источника")}</span>
    <button type="button" class="edit-remove" aria-label="${copy(lang, "Remove", "Удалить")}">×</button>
  </div>`).join("");
}

function openNotesEditor(s, lang, onSaved) {
  $("#notes-editor-backdrop")?.remove();
  const summary = summaryModel(s);
  const backdrop = document.createElement("div");
  backdrop.id = "notes-editor-backdrop";
  backdrop.className = "modal-backdrop";
  backdrop.innerHTML = `<div class="notes-editor" role="dialog" aria-modal="true" aria-labelledby="notes-editor-title">
    <form id="notes-editor-form">
      <header class="editor-head">
        <div><div class="section-kicker">${copy(lang, "Owner note", "Заметка владельца")}</div>
          <h2 id="notes-editor-title">${copy(lang, "Edit meeting notes", "Редактировать заметки")}</h2></div>
        <button type="button" class="editor-close" aria-label="${copy(lang, "Close editor", "Закрыть редактор")}">×</button>
      </header>
      <div class="editor-scroll">
        <label class="edit-field"><span>${copy(lang, "Meeting title", "Название встречи")}</span>
          <input id="edit-title" value="${esc(s.title || s.id)}" maxlength="160" required></label>
        <label class="edit-field"><span>${copy(lang, "At a glance", "Главное")}</span>
          <textarea id="edit-brief" maxlength="2000" required>${esc(summary.brief)}</textarea></label>
        <section class="edit-list-section">
          <div class="edit-list-head"><h3>${copy(lang, "Decisions", "Решения")}</h3>
            <button type="button" data-add-row="decisions">+ ${copy(lang, "Add", "Добавить")}</button></div>
          <div class="edit-list" data-edit-list="decisions">${editRowsHTML(summary.decisions, "decision", lang)}</div>
        </section>
        <label class="edit-field"><span>${copy(lang, "Detailed notes (Markdown)", "Подробные заметки (Markdown)")}</span>
          <textarea id="edit-overview" class="edit-overview" maxlength="50000">${esc(s.overview_md || "")}</textarea></label>
        <section class="edit-list-section">
          <div class="edit-list-head"><h3>${copy(lang, "Still open", "Осталось решить")}</h3>
            <button type="button" data-add-row="open_questions">+ ${copy(lang, "Add", "Добавить")}</button></div>
          <div class="edit-list" data-edit-list="open_questions">${editRowsHTML(summary.openQuestions, "open question", lang)}</div>
        </section>
      </div>
      <div class="editor-error" id="editor-error" role="alert"></div>
      <footer class="editor-foot">
        <button type="button" class="btn editor-cancel">${copy(lang, "Cancel", "Отмена")}</button>
        <button type="submit" class="btn primary editor-save">${copy(lang, "Save notes", "Сохранить")}</button>
      </footer>
    </form>
  </div>`;
  document.body.appendChild(backdrop);
  document.body.classList.add("modal-open");
  const form = $("#notes-editor-form", backdrop);
  let dirty = false;
  let saving = false;
  const returnFocus = $("#btn-more") || document.activeElement;

  const close = (force = false) => {
    if (saving) return;
    if (!force && dirty && !confirm(copy(lang, "Discard your unsaved changes?", "Отменить несохранённые изменения?"))) return;
    document.removeEventListener("keydown", onKeydown);
    document.body.classList.remove("modal-open");
    backdrop.remove();
    returnFocus?.focus?.();
  };
  const onKeydown = (event) => {
    if (event.key === "Escape") { close(); return; }
    if (event.key !== "Tab") return;
    const focusable = [...backdrop.querySelectorAll(
      'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'
    )].filter((element) => element.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  };
  document.addEventListener("keydown", onKeydown);
  form.addEventListener("input", () => { dirty = true; });
  backdrop.addEventListener("click", (event) => { if (event.target === backdrop) close(); });
  $(".editor-close", backdrop).addEventListener("click", () => close());
  $(".editor-cancel", backdrop).addEventListener("click", () => close());
  backdrop.addEventListener("click", (event) => {
    const add = event.target.closest("[data-add-row]");
    if (add) {
      const list = $(`[data-edit-list="${add.dataset.addRow}"]`, backdrop);
      list.insertAdjacentHTML("beforeend", editRowsHTML([{ text: "", source_ms: null }], add.dataset.addRow === "decisions" ? "decision" : "open question", lang));
      list.lastElementChild.querySelector("input").focus();
      dirty = true;
      return;
    }
    const remove = event.target.closest(".edit-remove");
    if (remove) { remove.closest(".note-edit-row").remove(); dirty = true; }
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (saving) return;
    const collect = (kind) => [...backdrop.querySelectorAll(`[data-edit-list="${kind}"] .note-edit-row`)]
      .map((row) => ({
        text: row.querySelector("input").value.trim(),
        source_ms: row.dataset.sourceMs === "" ? null : +row.dataset.sourceMs,
      })).filter((item) => item.text);
    const payload = {
      expected_revision: s.notes_revision,
      title: $("#edit-title", backdrop).value.trim(),
      overview_md: $("#edit-overview", backdrop).value.trim(),
      summary: {
        brief: $("#edit-brief", backdrop).value.trim(),
        decisions: collect("decisions"),
        open_questions: collect("open_questions"),
      },
    };
    const save = $(".editor-save", backdrop);
    const error = $("#editor-error", backdrop);
    saving = true; save.disabled = true; error.textContent = "";
    save.textContent = copy(lang, "Saving…", "Сохраняю…");
    try {
      const fresh = await api(`/api/sessions/${encodeURIComponent(s.id)}/notes?lang=${lang}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      dirty = false; saving = false; close(true);
      showToast(copy(lang, "Notes saved", "Заметки сохранены"));
      onSaved(fresh);
    } catch (err) {
      error.textContent = err.message;
      saving = false; save.disabled = false;
      save.textContent = copy(lang, "Save notes", "Сохранить");
    }
  });
  $("#edit-title", backdrop).focus();
}

function sourceListHTML(items, lang) {
  return `<ul class="source-list">${items.map((item) => `<li>
    <span>${esc(item.text)}</span>
    ${item.source_ms != null ? `<button class="source-time" data-jump-ms="${item.source_ms}" title="${copy(lang, "Open transcript at", "Открыть транскрипт на")} ${fmt(item.source_ms)}">${fmt(item.source_ms)}</button>` : ""}
  </li>`).join("")}</ul>`;
}

function summaryDocumentHTML(s, lang, { shared = false } = {}) {
  const summary = summaryModel(s);
  const status = s.ai_status && s.ai_status !== "done" ? `<div class="pipeline-note ${esc(s.ai_status)}">
    <span class="pipeline-dot"></span><span>${esc(aiLabel(s, lang))}</span></div>` : "";
  return `<article class="summary-paper">
    ${status}
    <section class="summary-lead">
      <div class="section-kicker">${copy(lang, "At a glance", "Главное")}</div>
      ${summary.brief
        ? `<p>${esc(summary.brief)}</p>`
        : `<p class="summary-empty">${copy(lang, "The summary is still being prepared.", "Краткое резюме ещё готовится.")}</p>`}
    </section>
    ${summary.decisions.length ? `<section class="summary-section summary-decisions">
      <h2>${copy(lang, "Decisions", "Решения")}</h2>${sourceListHTML(summary.decisions, lang)}
    </section>` : ""}
    ${s.overview_md ? `<section class="summary-section">
      <h2>${copy(lang, "Detailed notes", "Подробные заметки")}</h2>
      <div class="overview">${md(s.overview_md)}</div>
    </section>` : ""}
    ${summary.openQuestions.length ? `<section class="summary-section summary-open">
      <h2>${copy(lang, "Still open", "Осталось решить")}</h2>${sourceListHTML(summary.openQuestions, lang)}
    </section>` : ""}
    ${!shared ? `<button class="ask-launch" data-open-tab="ask">
      <span><b>${copy(lang, "Ask about this meeting", "Спросить об этой встрече")}</b>
      <small>${copy(lang, "Find a decision, quote, number, or next step", "Найти решение, цитату, число или следующий шаг")}</small></span>
      <span aria-hidden="true">→</span>
    </button>` : ""}
  </article>`;
}

function meetingRailHTML(s, lang, { shared = false } = {}) {
  const actions = s.actions || [];
  return `<aside class="meeting-rail">
    <section class="rail-card" id="p-actions">
      <div class="rail-card-head"><h2>${copy(lang, "Action items", "Задачи")}</h2>
        ${actions.length ? `<span>${actions.filter((a) => !a.done).length}</span>` : ""}</div>
      ${actionsHTML(actions, { readonly: shared, lang })}
    </section>
    ${(s.outline || []).length ? `<section class="rail-card">
      <div class="rail-card-head"><h2>${copy(lang, "Timeline", "По ходу встречи")}</h2></div>
      <div class="chapter-list">${s.outline.map((o) => `<button data-jump-ms="${o.ms}">
        <span>${fmt(o.ms)}</span><b>${esc(o.label)}</b></button>`).join("")}</div>
    </section>` : ""}
    ${(s.keywords || []).length ? `<section class="rail-card keywords-card">
      <div class="rail-card-head"><h2>${copy(lang, "Topics", "Темы")}</h2></div>
      <div class="kw-row">${s.keywords.map((k) => `<span class="chip">${esc(k)}</span>`).join("")}</div>
    </section>` : ""}
  </aside>`;
}

function transcriptPanelHTML(turns, lang, { searchable = true } = {}) {
  return `<div class="transcript-panel">
    <div class="tr-toolbar">
      <div><span class="section-kicker">${copy(lang, "Verbatim record", "Дословная запись")}</span>
        <h2>${copy(lang, "Transcript", "Транскрипт")}</h2></div>
      ${searchable ? `<div class="tr-search">
        <label><span class="sr-only">${copy(lang, "Find in transcript", "Найти в транскрипте")}</span>
          <input id="tr-q" placeholder="${copy(lang, "Find in transcript", "Найти в транскрипте")}" autocomplete="off"></label>
        <span class="cnt" id="tr-cnt"></span>
        <button id="tr-prev" title="${copy(lang, "Previous result", "Предыдущий результат")}">↑</button>
        <button id="tr-next" title="${copy(lang, "Next result", "Следующий результат")}">↓</button>
      </div>` : ""}
    </div>
    <div class="tr-body">
      ${searchable ? `<div class="time-rail" id="rail"><div class="rail-line"></div><div class="rail-needle" id="rail-needle" style="top:0"></div></div>` : ""}
      <div class="turns" id="turns">
        ${turns.map((t) => `<div class="turn ${t.speaker}">
          <div class="turn-gutter">
            <span class="turn-speaker">${t.speaker === "me" ? copy(lang, "Me", "Я") : copy(lang, "Guest", "Собеседник")}</span>
            <button class="turn-ts" data-ms="${t.start}">${fmt(t.start)}</button>
          </div>
          <div class="turn-text">${t.segs.map((g) => `<span class="seg" data-ms="${g.start_ms}" data-idx="${g.idx}">${esc(g.text)}</span>`).join(" ")}</div>
        </div>`).join("")}
      </div>
    </div>
  </div>`;
}

// ---------------------------------------------------------------- player

const player = {
  sessionId: null, track: null, tracks: [], speed: 1,
  bar: $("#player-bar"), playBtn: $("#pb-play"), timeEl: $("#pb-time"),
  durEl: $("#pb-dur"), scrub: $("#pb-scrub"), progress: $("#pb-progress"),
  tracksEl: $("#pb-tracks"), speedBtn: $("#pb-speed"),

  load(sessionId, tracks) {
    if (this.sessionId === sessionId && this.track) return;
    this.sessionId = sessionId; this.tracks = tracks;
    this.setTrack(tracks.includes("mixed") ? "mixed" : (tracks.includes("system") ? "system" : tracks[0]), false);
    this.bar.classList.remove("hidden");
    this.renderTracks();
  },
  setTrack(track, keepTime = true) {
    if (!track) return;
    const t = keepTime ? audio.currentTime : 0;
    const wasPlaying = !audio.paused;
    this.track = track;
    const base = this.audioBase || `/api/sessions/${this.sessionId}/audio`;
    audio.src = `${base}/${track}`;
    audio.currentTime = t;
    audio.playbackRate = this.speed;
    if (wasPlaying) audio.play();
    this.renderTracks();
  },
  renderTracks() {
    this.tracksEl.innerHTML = this.tracks.map((t) =>
      `<button class="pb-track ${t === this.track ? "active" : ""}" data-track="${t}">
        ${t === "mixed" ? "Both" : t === "mic" ? "Me (mic)" : "Them (system)"}</button>`).join("");
  },
  seek(ms, play = null) {
    if (!this.sessionId) return;
    audio.currentTime = ms / 1000;
    if (play === true || (play === null && !audio.paused)) audio.play();
  },
  unload() {
    audio.pause(); audio.removeAttribute("src"); audio.load();
    this.sessionId = null; this.track = null; this.tracks = []; this.audioBase = null;
    this.bar.classList.add("hidden");
  },
};

player.playBtn.addEventListener("click", () => audio.paused ? audio.play() : audio.pause());
audio.addEventListener("play", () => { player.playBtn.textContent = "⏸"; startClock(); });
audio.addEventListener("pause", () => { player.playBtn.textContent = "▶"; stopClock(); });
audio.addEventListener("loadedmetadata", () => { player.durEl.textContent = fmt(audio.duration * 1000); });
player.scrub.addEventListener("click", (e) => {
  const r = player.scrub.getBoundingClientRect();
  audio.currentTime = ((e.clientX - r.left) / r.width) * (audio.duration || 0);
});
player.tracksEl.addEventListener("click", (e) => {
  const b = e.target.closest("[data-track]");
  if (b) player.setTrack(b.dataset.track);
});
const SPEEDS = [1, 1.25, 1.5, 2];
player.speedBtn.addEventListener("click", () => {
  player.speed = SPEEDS[(SPEEDS.indexOf(player.speed) + 1) % SPEEDS.length];
  audio.playbackRate = player.speed;
  player.speedBtn.textContent = player.speed + "×";
});
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !/INPUT|TEXTAREA/.test(document.activeElement.tagName) && player.sessionId) {
    e.preventDefault(); audio.paused ? audio.play() : audio.pause();
  }
});

// rAF clock: segment highlight + needle + follow scroll + player bar UI.
let rafId = null;
function startClock() { if (!rafId) tick(); }
function stopClock() { if (rafId) { cancelAnimationFrame(rafId); rafId = null; } tickOnce(); }
function tick() { tickOnce(); rafId = requestAnimationFrame(tick); }

const sync = {
  segments: [], els: [], railNeedle: null, railHeight: 0, durationMs: 0,
  activeIdx: -1, follow: true, suppressScroll: false, visible: false,
};

function tickOnce() {
  const ms = audio.currentTime * 1000;
  player.timeEl.textContent = fmt(ms);
  player.progress.style.width = audio.duration ? `${(audio.currentTime / audio.duration) * 100}%` : "0";
  if (!sync.segments.length) return;
  if (sync.railNeedle && sync.durationMs) {
    sync.railNeedle.style.top = `${Math.min(ms / sync.durationMs, 1) * sync.railHeight}px`;
  }
  const idx = findSegment(ms);
  if (idx !== sync.activeIdx) {
    if (sync.activeIdx >= 0) sync.els[sync.activeIdx]?.classList.remove("active");
    sync.activeIdx = idx;
    if (idx >= 0) {
      sync.els[idx]?.classList.add("active");
      if (sync.visible && sync.follow && !audio.paused) {
        sync.suppressScroll = true;
        sync.els[idx]?.scrollIntoView({ block: "center", behavior: "smooth" });
        setTimeout(() => { sync.suppressScroll = false; }, 600);
      }
    }
  }
}

function findSegment(ms) {
  const segs = sync.segments;
  // fast path: current or next
  if (sync.activeIdx >= 0) {
    const cur = segs[sync.activeIdx];
    if (cur && ms >= cur.start_ms && ms < cur.end_ms) return sync.activeIdx;
    const nxt = segs[sync.activeIdx + 1];
    if (nxt && ms >= nxt.start_ms && ms < nxt.end_ms) return sync.activeIdx + 1;
  }
  let lo = 0, hi = segs.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (segs[mid].start_ms <= ms) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

window.addEventListener("wheel", () => { if (sync.segments.length) breakFollow(); }, { passive: true });
window.addEventListener("touchmove", () => { if (sync.segments.length) breakFollow(); }, { passive: true });
function breakFollow() {
  if (!sync.visible || sync.suppressScroll || !sync.follow) return;
  sync.follow = false;
  if (!$(".follow-pill") && player.sessionId && !audio.paused) {
    const pill = document.createElement("button");
    pill.className = "follow-pill";
    pill.textContent = "↓ Jump to current";
    pill.onclick = () => { sync.follow = true; pill.remove(); tickOnce(); };
    document.body.appendChild(pill);
  }
}
function resetFollow() { sync.follow = true; $(".follow-pill")?.remove(); }

// ---------------------------------------------------------------- router

const SHARE_TOKEN = location.pathname.startsWith("/s/")
  ? location.pathname.slice(3).split("/")[0] : null;

window.addEventListener("hashchange", () => { if (!SHARE_TOKEN) route(); });
window.addEventListener("DOMContentLoaded", () => {
  if (SHARE_TOKEN) {
    document.body.classList.add("share-mode");
    document.querySelector(".topnav").classList.add("hidden");
    document.querySelector(".top-search").classList.add("hidden");
    document.querySelector(".privacy-mark").innerHTML = "<span></span>Read-only share";
    sharedView(SHARE_TOKEN);
    return;
  }
  $("#global-search").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.value.trim())
      location.hash = `#/search/${encodeURIComponent(e.target.value.trim())}`;
  });
  route();
});

function groupTurns(segments) {
  const turns = [];
  for (const raw of segments || []) {
    const seg = {
      ...raw,
      speaker: raw.speaker === "me" ? "me" : "them",
      start_ms: +raw.start_ms || 0,
      end_ms: +raw.end_ms || 0,
    };
    const last = turns[turns.length - 1];
    if (last && last.speaker === seg.speaker && seg.start_ms - last.end < 4000) {
      last.segs.push(seg); last.end = seg.end_ms;
    } else {
      turns.push({ speaker: seg.speaker, start: seg.start_ms, end: seg.end_ms, segs: [seg] });
    }
  }
  return turns;
}

async function sharedView(token) {
  let s;
  try { s = await api(`/api/shared/${token}`); }
  catch (e) {
    view.innerHTML = `<div class="empty-state"><div class="big">Link is no longer active</div><div>Ask the person who shared it for a new one.</div></div>`;
    return;
  }
  const lang = s.lang;
  const d = dateParts(s.started_at);
  const tracks = [s.has_audio_mixed && "mixed", s.has_audio_system && "system", s.has_audio_mic && "mic"].filter(Boolean);
  const turns = groupTurns(s.segments);
  const durationMs = Math.max(s.duration_s * 1000, 1);

  view.innerHTML = `<div class="meeting-page shared-meeting">
    <header class="meeting-header shared-header">
      <div><div class="meeting-eyebrow">${copy(lang, "Shared meeting", "Встреча по ссылке")}</div>
        <h1>${esc(s.title || s.id)}</h1>
        <div class="meet-meta"><span>${d.day} ${d.year}, ${d.time}</span><span>·</span><span>${fmtDur(s.duration_s)}</span></div></div>
      <button class="btn shared-copy" id="shared-copy-summary">${copy(lang, "Copy summary", "Скопировать резюме")}</button>
    </header>
    <nav class="meeting-tabs" role="tablist" aria-label="${copy(lang, "Meeting view", "Раздел встречи")}">
      <button id="shared-tab-summary" role="tab" aria-selected="true" aria-controls="shared-summary" data-meeting-tab="summary">${copy(lang, "Summary", "Резюме")}</button>
      <button id="shared-tab-transcript" role="tab" aria-selected="false" aria-controls="shared-transcript" data-meeting-tab="transcript">${copy(lang, "Transcript", "Транскрипт")}</button>
    </nav>
    <section id="shared-summary" class="meeting-panel" role="tabpanel" aria-labelledby="shared-tab-summary" data-meeting-panel="summary">
      <div class="summary-layout">${summaryDocumentHTML(s, lang, { shared: true })}${meetingRailHTML(s, lang, { shared: true })}</div>
    </section>
    <section id="shared-transcript" class="meeting-panel" role="tabpanel" aria-labelledby="shared-tab-transcript" data-meeting-panel="transcript" hidden>
      ${transcriptPanelHTML(turns, lang, { searchable: false })}
    </section>
  </div>`;

  const setTab = (name) => {
    view.querySelectorAll("[data-meeting-tab]").forEach((button) => {
      const active = button.dataset.meetingTab === name;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    });
    view.querySelectorAll("[data-meeting-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.meetingPanel !== name;
    });
    sync.visible = name === "transcript";
  };

  view.querySelectorAll("[data-meeting-tab]").forEach((button) => {
    button.addEventListener("click", () => setTab(button.dataset.meetingTab));
  });
  $("#shared-copy-summary")?.addEventListener("click", () => copyText(
    summaryMarkdown(s, lang), lang, copy(lang, "Summary copied", "Резюме скопировано")));
  view.addEventListener("click", (e) => {
    const jump = e.target.closest("[data-jump-ms]");
    const moment = jump || e.target.closest("[data-ms]");
    if (!moment) return;
    if (jump) setTab("transcript");
    const ms = +(jump?.dataset.jumpMs ?? moment.dataset.ms);
    player.seek(ms); tickOnce();
    if (jump) setTimeout(() => sync.els[findSegment(ms)]?.scrollIntoView({ block: "center" }), 50);
  });

  if (tracks.length) {
    player.audioBase = `/api/shared/${token}/audio`;
    player.load(s.id, tracks);
  }
  sync.segments = s.segments;
  sync.els = [...view.querySelectorAll(".seg")].sort((a, b) => a.dataset.idx - b.dataset.idx);
  sync.durationMs = durationMs;
  setTab("summary");
}

function setNav(name) {
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("active", a.dataset.nav === name));
}

let navGen = 0;
async function route() {
  const gen = ++navGen;
  const h = location.hash || "#/";
  sync.segments = []; sync.els = []; sync.activeIdx = -1; sync.visible = false; resetFollow();
  const render = (fn) => async (...a) => { const html = await fn(...a); return html; };
  try {
    if (h.startsWith("#/m/")) await meetingView(decodeURIComponent(h.slice(4).split("?")[0]), gen);
    else if (h.startsWith("#/search/")) await searchView(decodeURIComponent(h.slice(9)), gen);
    else if (h === "#/actions") await actionsView(gen);
    else if (h === "#/ask") await askView(gen);
    else await libraryView(gen);
  } catch (err) {
    if (gen === navGen)
      view.innerHTML = `<div class="empty-state"><div class="big">Couldn't load</div><div>${esc(err.message)}</div></div>`;
  }
}
const stale = (gen) => gen !== undefined && gen !== navGen;

// ---------------------------------------------------------------- library

let libTag = "";
async function libraryView(gen) {
  setNav("library");
  const { sessions } = await api("/api/sessions");
  if (stale(gen)) return;
  const tags = [...new Set(sessions.flatMap((s) => s.tags || []))];
  if (!sessions.length) {
    view.innerHTML = `<div class="empty-state">
      <div class="big">No meetings yet</div>
      <div>Record with quill on the Mac — it lands here on stop.</div></div>`;
    return;
  }
  view.innerHTML = `
    <div class="lib-head"><h1>Meetings</h1>
      <span class="lib-count">${sessions.length} recorded</span></div>
    ${tags.length ? `<div class="tag-row">
      <button class="tag-chip ${!libTag ? "active" : ""}" data-tag="">All</button>
      ${tags.map((t) => `<button class="tag-chip ${libTag === t ? "active" : ""}" data-tag="${esc(t)}">${esc(t)}</button>`).join("")}
    </div>` : ""}
    <div id="cards"></div>`;
  $(".tag-row")?.addEventListener("click", (e) => {
    const b = e.target.closest("[data-tag]");
    if (b) { libTag = b.dataset.tag; libraryView(); }
  });
  const list = libTag ? sessions.filter((s) => (s.tags || []).includes(libTag)) : sessions;
  $("#cards").innerHTML = list.map(cardHTML).join("");
  if (sessions.some((s) => s.ai_status === "transcribing")) {
    setTimeout(() => {
      if ((location.hash || "#/") === "#/") route();
    }, 5000);
  }
}

function cardHTML(s) {
  const d = dateParts(s.started_at);
  const aiNote = s.ai_status !== "done"
    ? `<span class="ai-badge ${s.ai_status}">${esc(aiBadgeText(s))}</span>` : "";
  const snippet = summaryModel(s).brief;
  return `<a class="meeting-card" href="#/m/${encodeURIComponent(s.id)}">
    <div class="mc-date"><b>${d.day}</b>${d.year}<br>${d.time}</div>
    <div>
      <div class="mc-title">${esc(s.title || s.id)}</div>
      <div class="mc-snippet">${esc(snippet)}</div>
      <div class="mc-chips">
        ${(s.tags || []).map((t) => `<span class="chip tag">${esc(t)}</span>`).join("")}
        ${(s.keywords || []).slice(0, 4).map((k) => `<span class="chip">${esc(k)}</span>`).join("")}
      </div>
    </div>
    <div class="mc-side">
      <span class="mc-dur">${fmtDur(s.duration_s)}</span>
      ${s.open_actions ? `<span class="mc-actions-open">☐ ${s.open_actions} open</span>` : ""}
      ${aiNote}
    </div></a>`;
}

// ---------------------------------------------------------------- meeting

async function meetingView(id, gen, prefetched = null) {
  setNav("");
  const wantLang = localStorage.getItem("quill_lang") || "en";
  const s = prefetched || await api(`/api/sessions/${encodeURIComponent(id)}?lang=${wantLang}`);
  if (stale(gen)) return;
  const lang = s.lang;
  const d = dateParts(s.started_at);

  if (s.ai_status === "transcribing" || s.ai_status === "transcription_failed") {
    const failed = s.ai_status === "transcription_failed";
    view.innerHTML = `<div class="meeting-page">
      <a class="meet-back" href="#/">← ${copy(lang, "Meetings", "Встречи")}</a>
      <header class="meeting-header processing-header">
        <h1>${esc(s.title || s.id)}</h1>
        <div class="meet-meta"><span>${d.day} ${d.year}, ${d.time}</span><span>·</span><span>${fmtDur(s.duration_s)}</span></div>
      </header>
      <div class="local-pipeline ${failed ? "failed" : ""}">
        <span class="pipeline-orbit" aria-hidden="true"></span>
        <div><div class="section-kicker">${failed ? copy(lang, "Needs attention", "Нужно проверить") : copy(lang, "Recording secured", "Запись сохранена")}</div>
        <h2>${failed ? copy(lang, "Local transcription needs attention", "Локальная расшифровка требует внимания") : copy(lang, "Transcribing on your Mac…", "Расшифровывается на Mac…")}</h2>
        <p>${failed
          ? esc(s.ai_error || "The local transcription process failed. Quill preserved the recording for recovery.")
          : copy(lang, "The recording is finalized and safe. This page will fill in automatically when the transcript arrives.", "Запись завершена и сохранена. Страница заполнится автоматически, когда будет готов транскрипт.")}</p></div>
      </div></div>`;
    if (!failed) pollAI(s.id, s.ai_status);
    return;
  }

  const tracks = [s.has_audio_mixed && "mixed", s.has_audio_system && "system", s.has_audio_mic && "mic"].filter(Boolean);
  const turns = groupTurns(s.segments);
  const durationMs = Math.max(s.duration_s * 1000, 1);
  const query = new URLSearchParams(location.hash.split("?")[1] || "");
  const tParam = query.get("t");
  const requestedTab = query.get("tab");
  const initialTab = tParam ? "transcript"
    : ["summary", "transcript", "ask"].includes(requestedTab) ? requestedTab : "summary";

  view.innerHTML = `<div class="meeting-page">
    <a class="meet-back" href="#/">← ${copy(lang, "Meetings", "Встречи")}</a>
    <header class="meeting-header">
      <div class="meeting-title-row">
        <div class="meeting-title-block">
          <h1>${esc(s.title || s.id)}</h1>
          <div class="meet-meta">
            <span>${d.day} ${d.year}, ${d.time}</span><span>·</span><span>${fmtDur(s.duration_s)}</span>
            ${(s.tags || []).map((tag) => `<span class="chip tag">${esc(tag)}</span>`).join("")}
            ${s.notes_edited ? `<span class="edited-badge">${copy(lang, "Edited", "Изменено")}</span>` : ""}
            ${s.ai_status !== "done" ? `<span class="ai-badge ${s.ai_status}">${esc(aiLabel(s, lang))}</span>` : ""}
          </div>
        </div>
        <div class="meeting-controls">
          <div class="lang-toggle" id="lang-toggle" title="${copy(lang, "Language of AI notes", "Язык заметок")}">
            <button data-lang="en" class="${lang === "en" ? "active" : ""}">EN</button>
            <button data-lang="ru" class="${lang === "ru" ? "active" : ""}">RU</button>
          </div>
          <button class="btn primary" id="btn-share">${copy(lang, "Share", "Поделиться")}</button>
          <div class="more-wrap">
            <button class="icon-btn" id="btn-more" aria-label="${copy(lang, "More meeting actions", "Другие действия")}" aria-expanded="false">•••</button>
            <div class="meeting-menu hidden" id="meeting-menu" role="menu">
              <button id="btn-edit-notes" role="menuitem">✎ ${copy(lang, "Edit notes", "Редактировать заметки")}</button>
              <button id="btn-copy-summary" role="menuitem">⧉ ${copy(lang, "Copy summary", "Скопировать резюме")}</button>
              <button id="btn-copy-full" role="menuitem">⇩ ${copy(lang, "Copy full meeting", "Скопировать всю встречу")}</button>
              <span class="menu-divider" role="separator"></span>
              <button id="btn-regen" role="menuitem">↻ ${copy(lang, "Regenerate summary", "Создать резюме заново")}</button>
              <button class="hidden" id="btn-unshare" role="menuitem">${copy(lang, "Revoke share link", "Отозвать ссылку")}</button>
              <button class="danger" id="btn-del" role="menuitem">${copy(lang, "Delete meeting", "Удалить встречу")}</button>
            </div>
          </div>
        </div>
      </div>
    </header>
    <nav class="meeting-tabs" role="tablist" aria-label="${copy(lang, "Meeting view", "Раздел встречи")}">
      <button id="meeting-tab-summary" role="tab" aria-controls="meeting-summary" data-meeting-tab="summary">${copy(lang, "Summary", "Резюме")}</button>
      <button id="meeting-tab-transcript" role="tab" aria-controls="meeting-transcript" data-meeting-tab="transcript">${copy(lang, "Transcript", "Транскрипт")}</button>
      <button id="meeting-tab-ask" role="tab" aria-controls="meeting-ask" data-meeting-tab="ask">${copy(lang, "Ask", "Спросить")}</button>
    </nav>
    <section id="meeting-summary" class="meeting-panel" role="tabpanel" aria-labelledby="meeting-tab-summary" data-meeting-panel="summary">
      <div class="summary-layout">${summaryDocumentHTML(s, lang)}${meetingRailHTML(s, lang)}</div>
    </section>
    <section id="meeting-transcript" class="meeting-panel" role="tabpanel" aria-labelledby="meeting-tab-transcript" data-meeting-panel="transcript">
      ${transcriptPanelHTML(turns, lang)}
    </section>
    <section id="meeting-ask" class="meeting-panel" role="tabpanel" aria-labelledby="meeting-tab-ask" data-meeting-panel="ask">
      <div class="ask-meeting">
        <div class="ask-intro"><div class="ask-spark" aria-hidden="true">✦</div>
          <div><div class="section-kicker">${copy(lang, "Grounded in this recording", "Ответы по этой записи")}</div>
          <h2>${copy(lang, "Ask anything about the meeting", "Спросите что угодно о встрече")}</h2>
          <p>${copy(lang, "Answers link back to the exact moments Quill used.", "Ответы опираются на точные моменты из разговора.")}</p></div>
        </div>
        <div class="chat-log" id="chat-log"></div>
        <div class="chat-suggest" id="chat-suggest">
          ${(lang === "ru"
            ? ["Что решили?", "Какие следующие шаги?", "О чём договорились по деньгам?"]
            : ["What was decided?", "What are the next steps?", "What did we agree on?"])
            .map((text) => `<button class="suggest-chip">${text}</button>`).join("")}
        </div>
        <form class="chat-form ask-compose" id="chat-form">
          <input id="chat-input" placeholder="${copy(lang, "Ask about a decision, quote, or detail…", "Спросите о решении, цитате или детали…")}" autocomplete="off">
          <button class="send-btn" title="${copy(lang, "Ask", "Спросить")}">↑</button>
        </form>
      </div>
    </section>
  </div>`;

  const page = $(".meeting-page");
  const rail = $("#rail");
  let railBuilt = false;
  const layoutRail = () => requestAnimationFrame(() => {
    if (!rail || rail.clientHeight <= 0) return;
    sync.railHeight = rail.clientHeight;
    if (railBuilt) return;
    railBuilt = true;
    const minutes = Math.floor(durationMs / 60000);
    for (let minute = 1; minute <= minutes; minute++) {
      const tick = document.createElement("div");
      tick.className = "rail-tick";
      tick.style.top = `${(minute * 60000 / durationMs) * sync.railHeight}px`;
      rail.appendChild(tick);
    }
    for (const chapterData of s.outline || []) {
      const chapter = document.createElement("button");
      chapter.className = "rail-chapter";
      chapter.title = chapterData.label;
      chapter.style.top = `${(chapterData.ms / durationMs) * sync.railHeight}px`;
      chapter.dataset.jumpMs = chapterData.ms;
      rail.appendChild(chapter);
    }
  });

  const setTab = (name, updateUrl = true) => {
    page.querySelectorAll("[data-meeting-tab]").forEach((button) => {
      const active = button.dataset.meetingTab === name;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    });
    page.querySelectorAll("[data-meeting-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.meetingPanel !== name;
    });
    sync.visible = name === "transcript";
    if (sync.visible) layoutRail();
    if (updateUrl) {
      const params = new URLSearchParams(location.hash.split("?")[1] || "");
      params.set("tab", name);
      if (name !== "transcript") params.delete("t");
      history.replaceState(null, "", `#/m/${encodeURIComponent(s.id)}?${params}`);
    }
  };

  page.querySelectorAll("[data-meeting-tab]").forEach((button) => {
    button.addEventListener("click", () => setTab(button.dataset.meetingTab));
  });
  $(".meeting-tabs").addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const tabs = [...page.querySelectorAll("[data-meeting-tab]")];
    const current = tabs.indexOf(document.activeElement);
    const next = tabs[(current + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
    e.preventDefault(); next.focus(); next.click();
  });

  if (tracks.length) player.load(s.id, tracks);
  else player.unload();
  sync.segments = s.segments;
  sync.els = [...page.querySelectorAll(".seg")].sort((a, b) => a.dataset.idx - b.dataset.idx);
  sync.durationMs = durationMs;
  sync.railNeedle = $("#rail-needle");

  rail?.addEventListener("click", (e) => {
    const chapter = e.target.closest("[data-jump-ms]");
    const ms = chapter ? +chapter.dataset.jumpMs
      : ((e.clientY - rail.getBoundingClientRect().top) / Math.max(sync.railHeight, 1)) * durationMs;
    player.seek(ms, true); resetFollow(); tickOnce();
  });
  page.addEventListener("click", (e) => {
    const openTab = e.target.closest("[data-open-tab]");
    if (openTab) {
      setTab(openTab.dataset.openTab);
      if (openTab.dataset.openTab === "ask") setTimeout(() => $("#chat-input")?.focus(), 50);
      return;
    }
    const jump = e.target.closest("[data-jump-ms]");
    const moment = jump || e.target.closest("[data-ms]");
    if (moment && !moment.classList.contains("rail-chapter")) {
      const ms = +(jump?.dataset.jumpMs ?? moment.dataset.ms);
      if (jump) setTab("transcript");
      player.seek(ms); resetFollow(); tickOnce();
      if (jump) setTimeout(() => sync.els[findSegment(ms)]?.scrollIntoView({ block: "center" }), 60);
    }
    if (!e.target.closest(".more-wrap")) {
      $("#meeting-menu")?.classList.add("hidden");
      $("#btn-more")?.setAttribute("aria-expanded", "false");
    }
  });

  setTab(initialTab, false);
  if (tParam) setTimeout(() => {
    const ms = +tParam;
    player.seek(ms, false); tickOnce();
    sync.els[findSegment(ms)]?.scrollIntoView({ block: "center" });
  }, 300);

  $("#p-actions")?.addEventListener("change", async (e) => {
    const cb = e.target.closest("input[data-action-id]");
    if (!cb) return;
    cb.disabled = true;
    try {
      await post(`/api/actions/${cb.dataset.actionId}/toggle`, { done: cb.checked });
      cb.closest(".action-item").classList.toggle("done", cb.checked);
    } catch (err) {
      cb.checked = !cb.checked;
      alert("Couldn't save: " + err.message);
    }
    cb.disabled = false;
  });

  const moreButton = $("#btn-more"), meetingMenu = $("#meeting-menu");
  moreButton.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = !meetingMenu.classList.contains("hidden");
    meetingMenu.classList.toggle("hidden", open);
    moreButton.setAttribute("aria-expanded", String(!open));
  });
  meetingMenu.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      meetingMenu.classList.add("hidden");
      moreButton.setAttribute("aria-expanded", "false");
      moreButton.focus();
    }
  });
  $("#btn-edit-notes").addEventListener("click", () => {
    meetingMenu.classList.add("hidden");
    moreButton.setAttribute("aria-expanded", "false");
    openNotesEditor(s, lang, (fresh) => meetingView(s.id, undefined, fresh));
  });
  $("#btn-copy-summary").addEventListener("click", async () => {
    meetingMenu.classList.add("hidden");
    moreButton.setAttribute("aria-expanded", "false");
    await copyText(summaryMarkdown(s, lang), lang,
      copy(lang, "Summary copied", "Резюме скопировано"));
  });
  $("#btn-copy-full").addEventListener("click", async () => {
    meetingMenu.classList.add("hidden");
    moreButton.setAttribute("aria-expanded", "false");
    await copyText(fullMeetingMarkdown(s, lang), lang,
      copy(lang, "Full meeting copied", "Вся встреча скопирована"));
  });

  api(`/api/sessions/${encodeURIComponent(s.id)}/share`).then(({ token }) => {
    if (token) $("#btn-unshare").classList.remove("hidden");
  });
  $("#btn-share").addEventListener("click", async () => {
    const button = $("#btn-share");
    const { url } = await post(`/api/sessions/${encodeURIComponent(s.id)}/share?lang=${lang}`);
    $("#btn-unshare").classList.remove("hidden");
    try {
      await navigator.clipboard.writeText(url);
      button.textContent = copy(lang, "Copied ✓", "Скопировано ✓");
      setTimeout(() => { button.textContent = copy(lang, "Share", "Поделиться"); }, 2500);
    } catch {
      prompt(copy(lang, "Copy the link:", "Скопируйте ссылку:"), url);
    }
  });
  $("#btn-unshare").addEventListener("click", async () => {
    await api(`/api/sessions/${encodeURIComponent(s.id)}/share`, { method: "DELETE" });
    $("#btn-unshare").classList.add("hidden");
    meetingMenu.classList.add("hidden");
  });
  $("#btn-del").addEventListener("click", async () => {
    if (!confirm(`Delete "${s.title || s.id}" from the dashboard? The original stays on the Mac.`)) return;
    await api(`/api/sessions/${encodeURIComponent(s.id)}`, { method: "DELETE" });
    location.hash = "#/";
  });

  const runTranslate = async (button) => {
    const original = button.textContent;
    button.textContent = "…";
    $("#lang-toggle").style.pointerEvents = "none";
    try {
      const result = await post(`/api/sessions/${encodeURIComponent(s.id)}/translate?lang=ru`);
      if (result.job_id) {
        let done = false;
        for (let attempt = 0; attempt < 200; attempt++) {
          await new Promise((resolve) => setTimeout(resolve, 2000));
          const job = await api(`/api/jobs/${result.job_id}`);
          if (job.status === "done") { done = true; break; }
          if (job.status === "failed") throw new Error(job.error || "translation failed");
        }
        if (!done) throw new Error("translation timed out — try again");
      }
      return true;
    } catch (err) {
      alert("Перевод не удался: " + err.message);
      localStorage.setItem("quill_lang", "en");
      return false;
    } finally {
      button.textContent = original;
      const toggle = $("#lang-toggle");
      if (toggle) toggle.style.pointerEvents = "";
    }
  };
  const rerenderIfHere = () => {
    if (location.hash.includes(encodeURIComponent(s.id))) meetingView(s.id);
  };
  $("#lang-toggle").addEventListener("click", async (e) => {
    const button = e.target.closest("[data-lang]");
    if (!button) return;
    const want = button.dataset.lang;
    localStorage.setItem("quill_lang", want);
    if (want === "ru" && !s.lang_ready.ru) {
      if (await runTranslate(button)) rerenderIfHere();
      return;
    }
    if (want !== lang) rerenderIfHere();
  });
  if (wantLang === "ru" && !s.lang_ready.ru && s.ai_status === "done") {
    const ruButton = $('#lang-toggle [data-lang="ru"]');
    runTranslate(ruButton).then((ok) => { if (ok) rerenderIfHere(); });
  }

  $("#btn-regen").addEventListener("click", async () => {
    if (s.notes_edited && !confirm(copy(lang,
      "Regenerating replaces your edited title and notes. Continue?",
      "Пересоздание заменит изменённые название и заметки. Продолжить?"))) return;
    await post(`/api/sessions/${encodeURIComponent(s.id)}/regenerate`);
    $("#btn-regen").textContent = copy(lang, "AI is rebuilding the summary…", "ИИ пересобирает резюме…");
    meetingMenu.classList.add("hidden");
    pollAI(s.id, "pending");
  });
  if (s.ai_status === "pending" || s.ai_status === "running"
      || (s.ai_status === "failed" && s.ai_retry_at)) {
    pollAI(s.id, s.ai_status);
  }

  wireTranscriptSearch();

  $("#chat-suggest")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".suggest-chip");
    if (!chip) return;
    $("#chat-input").value = chip.textContent;
    $("#chat-form").requestSubmit();
  });
  const log = $("#chat-log");
  api(`/api/chat/session?session_id=${encodeURIComponent(s.id)}`).then(({ messages }) => {
    for (const message of messages) addMsg(log, message.role, message.content);
    if (messages.length) $("#chat-suggest")?.classList.add("hidden");
  });
  $("#chat-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("#chat-input"), button = e.target.querySelector("button");
    const question = input.value.trim();
    if (!question || button.disabled) return;
    input.value = ""; button.disabled = true;
    $("#chat-suggest")?.classList.add("hidden");
    addMsg(log, "user", question);
    const thinking = addMsg(log, "assistant thinking", copy(lang, "reading the meeting…", "читаю встречу…"));
    try {
      const { answer } = await postJob(`/api/sessions/${encodeURIComponent(s.id)}/chat`, { question });
      thinking.remove(); addMsg(log, "assistant", answer);
    } catch (err) { thinking.textContent = "Failed: " + err.message; }
    button.disabled = false;
  });
}

function aiLabel(s, lang = "en") {
  return s.ai_status === "transcribing" ? copy(lang, "Transcribing on Mac…", "Расшифровывается на Mac…")
    : s.ai_status === "transcription_failed" ? copy(lang, "Local transcription failed", "Локальная расшифровка не удалась")
    : s.ai_status === "failed" && s.ai_retry_at
    ? copy(lang, `AI paused: ${s.ai_error || "temporary failure"} — automatic retry scheduled`, `ИИ приостановлен: ${s.ai_error || "временная ошибка"} — повтор запланирован`)
    : s.ai_status === "failed" ? copy(lang, `AI failed: ${s.ai_error || "unknown"} — regenerate to retry`, `ИИ не справился: ${s.ai_error || "неизвестно"} — создайте резюме заново`)
    : s.ai_status === "running" ? copy(lang, "AI is reading the meeting…", "ИИ читает встречу…")
    : s.ai_status === "pending" ? copy(lang, "AI summary queued…", "Резюме поставлено в очередь…") : "";
}

function aiBadgeText(s) {
  return s.ai_status === "transcribing" ? "Transcribing on Mac…"
    : s.ai_status === "transcription_failed" ? "Transcription failed"
    : s.ai_status === "failed" && s.ai_retry_at ? "AI retrying…"
    : s.ai_status === "failed" ? "AI failed"
    : "AI working…";
}

function actionsHTML(actions, { readonly = false, lang = "en" } = {}) {
  if (!actions?.length) return `<p class="rail-empty">${copy(lang, "Nothing assigned.", "Задач нет.")}</p>`;
  return actions.map((a) => `
    <div class="action-item ${a.done ? "done" : ""}">
      <input type="checkbox" aria-label="${esc(copy(lang, "Mark complete", "Отметить выполненной"))}: ${esc(a.text)}" ${readonly ? "disabled" : `data-action-id="${a.id}"`} ${a.done ? "checked" : ""}>
      <span class="at">${esc(a.text)}
        ${a.assignee ? `<span class="assignee">@${esc(a.assignee)}</span>` : ""}
        ${a.source_ms != null ? (a.session_id
          ? `<a class="src" href="#/m/${encodeURIComponent(a.session_id)}?t=${a.source_ms}">${fmt(a.source_ms)}</a>`
          : `<button type="button" class="src" data-jump-ms="${a.source_ms}">${fmt(a.source_ms)}</button>`) : ""}
      </span>
    </div>`).join("");
}

async function pollAI(id, initialStatus) {
  let delay = 4000;
  for (let i = 0; i < 400; i++) {
    await new Promise((r) => setTimeout(r, delay));
    delay = Math.min(delay * 1.15, 20000);
    if (!location.hash.includes(encodeURIComponent(id))) return;
    try {
      const s = await api(`/api/sessions/${encodeURIComponent(id)}/status`);
      const terminalFailure = s.ai_status === "failed" && !s.ai_retry_at;
      if (s.ai_status === "done" || terminalFailure || s.ai_status !== initialStatus) {
        route(); return;
      }
    } catch { /* transient — keep polling */ }
  }
}

function wireTranscriptSearch() {
  const input = $("#tr-q"), cnt = $("#tr-cnt");
  let hits = [], cur = -1;
  const paint = () => {
    document.querySelectorAll(".seg.search-hit-current").forEach((el) => el.classList.remove("search-hit-current"));
    document.querySelectorAll(".seg mark").forEach((m) => {
      const p = m.parentNode; p.replaceChild(document.createTextNode(m.textContent), m); p.normalize();
    });
    const q = input.value.trim().toLowerCase();
    hits = [];
    if (q.length < 2) { cnt.textContent = ""; return; }
    for (const el of sync.els) {
      const text = el.textContent, low = text.toLowerCase();
      if (!low.includes(q)) continue;
      hits.push(el);
      el.innerHTML = esc(text).replace(new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"), "<mark>$1</mark>");
    }
    cur = hits.length ? 0 : -1;
    show();
  };
  const show = () => {
    cnt.textContent = hits.length ? `${cur + 1}/${hits.length}` : "0";
    document.querySelectorAll(".seg.search-hit-current").forEach((el) => el.classList.remove("search-hit-current"));
    if (cur >= 0) {
      hits[cur].classList.add("search-hit-current");
      sync.follow = false;
      hits[cur].scrollIntoView({ block: "center" });
    }
  };
  let deb;
  input.addEventListener("input", () => { clearTimeout(deb); deb = setTimeout(paint, 250); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault();
      if (hits.length) { cur = (cur + (e.shiftKey ? -1 : 1) + hits.length) % hits.length; show(); } }
  });
  $("#tr-next").addEventListener("click", () => { if (hits.length) { cur = (cur + 1) % hits.length; show(); } });
  $("#tr-prev").addEventListener("click", () => { if (hits.length) { cur = (cur - 1 + hits.length) % hits.length; show(); } });
}

// ---------------------------------------------------------------- search page

async function searchView(q, gen) {
  setNav("");
  $("#global-search").value = q;
  const { results } = await api(`/api/search?q=${encodeURIComponent(q)}`);
  if (stale(gen)) return;
  view.innerHTML = `<h1 class="page-title">“${esc(q)}” — ${results.length} moment${results.length === 1 ? "" : "s"}</h1>
    ${results.map((r) => `
      <a class="sr-row" href="#/m/${encodeURIComponent(r.session_id)}?t=${r.start_ms}">
        <div class="sr-meta">${esc(r.title || r.session_id)} · ${dateParts(r.started_at).day} · <span class="ts">${fmt(r.start_ms)}</span> · ${r.speaker === "me" ? "Me" : "Them"}</div>
        <div class="sr-text">${esc(r.snip).replace(/\u0001/g, "<mark>").replace(/\u0002/g, "</mark>")}</div>
      </a>`).join("") || `<div class="empty-state"><div class="big">Nothing found</div><div>Try another word — search covers every transcript.</div></div>`}`;
}

// ---------------------------------------------------------------- actions page

async function actionsView(gen) {
  setNav("actions");
  const { actions } = await api("/api/actions?include_done=true");
  if (stale(gen)) return;
  const open = actions.filter((a) => !a.done), done = actions.filter((a) => a.done);
  const bySession = (list) => {
    const g = new Map();
    for (const a of list) {
      if (!g.has(a.session_id)) g.set(a.session_id, { title: a.session_title, items: [] });
      g.get(a.session_id).items.push(a);
    }
    return [...g.entries()];
  };
  const group = ([sid, g]) => `
    <div class="actions-group panel">
      <div class="g-head"><a href="#/m/${encodeURIComponent(sid)}">${esc(g.title || sid)}</a></div>
      ${actionsHTML(g.items)}
    </div>`;
  view.innerHTML = `<h1 class="page-title">Action items</h1>
    ${open.length ? bySession(open).map(group).join("") : `<div class="empty-state"><div class="big">All clear</div><div>Nothing open across your meetings.</div></div>`}
    ${done.length ? `<h2 style="color:var(--paper-faint);font-size:13px;text-transform:uppercase;letter-spacing:.09em;margin:26px 0 12px">Done</h2>${bySession(done).map(group).join("")}` : ""}`;
  view.addEventListener("change", async (e) => {
    const cb = e.target.closest("input[data-action-id]");
    if (!cb) return;
    cb.disabled = true;
    try {
      await post(`/api/actions/${cb.dataset.actionId}/toggle`, { done: cb.checked });
      actionsView();
    } catch (err) {
      cb.checked = !cb.checked; cb.disabled = false;
      alert("Couldn't save: " + err.message);
    }
  });
}

// ---------------------------------------------------------------- ask page

async function askView(gen) {
  setNav("ask");
  if (stale(gen)) return;
  view.innerHTML = `<div class="ask-wrap">
    <h1 class="page-title">Ask across all meetings</h1>
    <div class="panel chat-panel">
      <div class="chat-log" id="ask-log"></div>
      <form class="chat-form" id="ask-form">
        <input id="ask-input" placeholder="What did Drew promise last week? · Что мы решили по бюджету?" autocomplete="off">
        <button class="btn primary">Ask</button>
      </form>
    </div></div>`;
  const log = $("#ask-log");
  api("/api/chat/global").then(({ messages }) => {
    for (const m of messages) addMsg(log, m.role, m.content);
  });
  $("#ask-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("#ask-input"), btn = e.target.querySelector("button");
    const q = input.value.trim();
    if (!q || btn.disabled) return;
    input.value = ""; btn.disabled = true;
    addMsg(log, "user", q);
    const think = addMsg(log, "assistant thinking", "searching your meetings…");
    try {
      const { answer } = await postJob("/api/ask", { question: q });
      think.remove(); addMsg(log, "assistant", answer);
    } catch (err) { think.textContent = "Failed: " + err.message; }
    btn.disabled = false;
  });
}

function addMsg(log, cls, text) {
  const el = document.createElement("div");
  el.className = `chat-msg ${cls}`;
  if (cls.startsWith("assistant") && !cls.includes("thinking")) el.innerHTML = md(text);
  else el.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}
