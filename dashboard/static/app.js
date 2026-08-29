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
const esc = (t) => t.replace(/[&<>"']/g, (c) => ({
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
  activeIdx: -1, follow: true, suppressScroll: false,
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
      if (sync.follow && !audio.paused) {
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
  if (sync.suppressScroll || !sync.follow) return;
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
    document.querySelector(".topnav").classList.add("hidden");
    document.querySelector(".top-search").classList.add("hidden");
    sharedView(SHARE_TOKEN);
    return;
  }
  $("#global-search").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.value.trim())
      location.hash = `#/search/${encodeURIComponent(e.target.value.trim())}`;
  });
  route();
});

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
  const turns = [];
  for (const seg of s.segments) {
    seg.speaker = seg.speaker === "me" ? "me" : "them";
    seg.start_ms = +seg.start_ms || 0; seg.end_ms = +seg.end_ms || 0;
    const last = turns[turns.length - 1];
    if (last && last.speaker === seg.speaker && seg.start_ms - last.end < 4000) {
      last.segs.push(seg); last.end = seg.end_ms;
    } else turns.push({ speaker: seg.speaker, start: seg.start_ms, end: seg.end_ms, segs: [seg] });
  }
  const durationMs = Math.max(s.duration_s * 1000, 1);
  const L = (en, ru) => (lang === "ru" ? ru : en);
  view.innerHTML = `
  <div class="meet-head">
    <h1>${esc(s.title || s.id)}</h1>
    <div class="meet-meta"><span>${d.day} ${d.year}, ${d.time}</span><span class="sep">·</span>
      <span>${fmtDur(s.duration_s)}</span></div>
  </div>
  <div class="meet-grid">
    <div>
      ${s.actions.length ? `<div class="panel"><h2>${L("Action items", "Задачи")}</h2>
        ${s.actions.map((a) => `<div class="action-item ${a.done ? "done" : ""}">
          <input type="checkbox" disabled ${a.done ? "checked" : ""}>
          <span class="at">${esc(a.text)}${a.assignee ? `<span class="assignee">@${esc(a.assignee)}</span>` : ""}</span>
        </div>`).join("")}</div>` : ""}
      <div class="panel"><h2>${L("Overview", "Обзор")}</h2>
        <div class="overview">${md(s.overview_md || "")}</div></div>
      ${(s.outline || []).length ? `<div class="panel"><h2>${L("Chapters", "Главы")}</h2>
        ${s.outline.map((o) => `<div class="outline-item" data-ms="${o.ms}">
          <span class="ts">${fmt(o.ms)}</span><span>${esc(o.label)}</span></div>`).join("")}</div>` : ""}
    </div>
    <div class="transcript-panel">
      <div class="tr-toolbar"><h2>${L("Transcript", "Транскрипт")}</h2></div>
      <div class="tr-body">
        <div class="turns" style="padding-left:22px">
          ${turns.map((t) => `
            <div class="turn ${t.speaker}">
              <div class="turn-gutter">
                <span class="turn-speaker">${t.speaker === "me" ? L("Me", "Я") : L("Guest", "Собеседник")}</span>
                <span class="turn-ts" data-ms="${t.start}">${fmt(t.start)}</span>
              </div>
              <div class="turn-text">${t.segs.map((g) => `<span class="seg" data-ms="${g.start_ms}" data-idx="${g.idx}">${esc(g.text)}</span>`).join(" ")}</div>
            </div>`).join("")}
        </div>
      </div>
    </div>
  </div>`;
  if (tracks.length) {
    player.audioBase = `/api/shared/${token}/audio`;
    player.load(s.id, tracks);
  }
  sync.segments = s.segments;
  sync.els = [...view.querySelectorAll(".seg")].sort((a, b) => a.dataset.idx - b.dataset.idx);
  sync.durationMs = durationMs;
  view.addEventListener("click", (e) => {
    const el = e.target.closest("[data-ms]");
    if (el) { player.seek(+el.dataset.ms); tickOnce(); }
  });
}

function setNav(name) {
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("active", a.dataset.nav === name));
}

let navGen = 0;
async function route() {
  const gen = ++navGen;
  const h = location.hash || "#/";
  sync.segments = []; sync.els = []; sync.activeIdx = -1; resetFollow();
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
  const snippet = (s.overview_md || "").replace(/[#*-]/g, "").slice(0, 220);
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

async function meetingView(id, gen) {
  setNav("");
  const wantLang = localStorage.getItem("quill_lang") || "en";
  const s = await api(`/api/sessions/${encodeURIComponent(id)}?lang=${wantLang}`);
  if (stale(gen)) return;
  const lang = s.lang; // server truth: "ru" only when translation is actually applied
  const d = dateParts(s.started_at);
  if (s.ai_status === "transcribing" || s.ai_status === "transcription_failed") {
    const failed = s.ai_status === "transcription_failed";
    view.innerHTML = `
      <div class="meet-head">
        <a class="meet-back" href="#/">← Meetings</a>
        <h1>${esc(s.title || s.id)}</h1>
        <div class="meet-meta">
          <span>${d.day} ${d.year}, ${d.time}</span><span class="sep">·</span>
          <span>${fmtDur(s.duration_s)}</span><span class="sep">·</span>
          <span class="ai-badge ${s.ai_status}">${esc(aiLabel(s))}</span>
        </div>
      </div>
      <div class="panel local-pipeline">
        <h2>${failed ? "Local transcription needs attention" : "Transcribing on your Mac…"}</h2>
        <p>${failed
          ? esc(s.ai_error || "The local transcription process failed. Quill will preserve the recording for recovery.")
          : "The recording is finalized and safe. The dashboard will fill in automatically when the local transcript arrives."}</p>
      </div>`;
    if (!failed) pollAI(s.id, s.ai_status);
    return;
  }
  const tracks = [s.has_audio_mixed && "mixed", s.has_audio_system && "system", s.has_audio_mic && "mic"].filter(Boolean);

  // group segments into speaker turns
  const turns = [];
  for (const seg of s.segments) {
    const last = turns[turns.length - 1];
    if (last && last.speaker === seg.speaker && seg.start_ms - last.end < 4000) {
      last.segs.push(seg); last.end = seg.end_ms;
    } else turns.push({ speaker: seg.speaker, start: seg.start_ms, end: seg.end_ms, segs: [seg] });
  }
  const durationMs = Math.max(s.duration_s * 1000, 1);

  view.innerHTML = `
  <div class="meet-head">
    <a class="meet-back" href="#/">← Meetings</a>
    <h1>${esc(s.title || s.id)}</h1>
    <div class="meet-meta">
      <span>${d.day} ${d.year}, ${d.time}</span><span class="sep">·</span>
      <span>${fmtDur(s.duration_s)}</span><span class="sep">·</span>
      ${(s.tags || []).map((t) => `<span class="chip tag">${esc(t)}</span>`).join(" ")}
      <span style="flex:1"></span>
      ${s.ai_status !== "done" ? `<span class="ai-badge ${s.ai_status}">${esc(aiLabel(s))}</span>` : ""}
      <div class="lang-toggle" id="lang-toggle" title="Language of AI notes">
        <button data-lang="en" class="${lang === "en" ? "active" : ""}">EN</button>
        <button data-lang="ru" class="${lang === "ru" ? "active" : ""}">RU</button>
      </div>
      <button class="btn" id="btn-share">${lang === "ru" ? "Поделиться" : "Share"}</button>
      <button class="btn hidden" id="btn-unshare">Unshare</button>
      <button class="btn" id="btn-regen">↻ Regenerate</button>
      <button class="btn" id="btn-del" title="Delete this meeting everywhere on the server">Delete</button>
    </div>
  </div>
  <div class="meet-grid">
    <div>
      <div class="panel chat-panel chat-hero">
        <h2>${lang === "ru" ? "Спросить об этой встрече" : "Ask about this meeting"}</h2>
        <div class="chat-log" id="chat-log"></div>
        <div class="chat-suggest" id="chat-suggest">
          ${(lang === "ru"
            ? ["Что решили?", "Какие следующие шаги?", "О чём договорились по деньгам?"]
            : ["What was decided?", "What are the next steps?", "What did we agree on?"])
            .map((t) => `<button class="suggest-chip">${t}</button>`).join("")}
        </div>
        <form class="chat-form" id="chat-form">
          <input id="chat-input" placeholder="${lang === "ru" ? "Спросите что угодно о разговоре…" : "Ask anything about this call…"}" autocomplete="off">
          <button class="send-btn" title="Ask">↑</button>
        </form>
      </div>
      <div class="panel" id="p-actions"><h2>${lang === "ru" ? "Задачи" : "Action items"}</h2>${actionsHTML(s.actions)}</div>
      <div class="panel"><h2>${lang === "ru" ? "Обзор" : "Overview"}</h2>
        <div class="overview">${s.overview_md ? md(s.overview_md) : `<p style="color:var(--paper-dim)">${esc(aiLabel(s))}</p>`}</div>
        ${(s.keywords || []).length ? `<div class="kw-row">${s.keywords.map((k) => `<span class="chip">${esc(k)}</span>`).join("")}</div>` : ""}
      </div>
      ${(s.outline || []).length ? `<div class="panel"><h2>${lang === "ru" ? "Главы" : "Chapters"}</h2>
        ${s.outline.map((o) => `<div class="outline-item" data-ms="${o.ms}">
          <span class="ts">${fmt(o.ms)}</span><span>${esc(o.label)}</span></div>`).join("")}
      </div>` : ""}
    </div>
    <div class="transcript-panel">
      <div class="tr-toolbar">
        <h2>Transcript</h2>
        <div class="tr-search">
          <input id="tr-q" placeholder="Find in transcript" autocomplete="off">
          <span class="cnt" id="tr-cnt"></span>
          <button id="tr-prev" title="Previous (Shift+Enter)">↑</button>
          <button id="tr-next" title="Next (Enter)">↓</button>
        </div>
      </div>
      <div class="tr-body">
        <div class="time-rail" id="rail">
          <div class="rail-line"></div>
          <div class="rail-needle" id="rail-needle" style="top:0"></div>
        </div>
        <div class="turns" id="turns">
          ${turns.map((t) => `
            <div class="turn ${t.speaker}">
              <div class="turn-gutter">
                <span class="turn-speaker">${t.speaker === "me" ? "Me" : "Them"}</span>
                <span class="turn-ts" data-ms="${t.start}">${fmt(t.start)}</span>
              </div>
              <div class="turn-text">
                ${t.segs.map((g) => `<span class="seg" data-ms="${g.start_ms}" data-idx="${g.idx}">${esc(g.text)}</span>`).join(" ")}
              </div>
            </div>`).join("")}
        </div>
      </div>
    </div>
  </div>`;

  // player + sync wiring
  if (tracks.length) player.load(s.id, tracks);
  else player.unload();
  sync.segments = s.segments;
  sync.els = [...view.querySelectorAll(".seg")].sort((a, b) => a.dataset.idx - b.dataset.idx);
  sync.durationMs = durationMs;
  sync.railNeedle = $("#rail-needle");

  const rail = $("#rail");
  requestAnimationFrame(() => {
    sync.railHeight = rail.clientHeight;
    // minute ticks + chapter dots
    const minutes = Math.floor(durationMs / 60000);
    for (let m = 1; m <= minutes; m++) {
      const t = document.createElement("div");
      t.className = "rail-tick";
      t.style.top = `${(m * 60000 / durationMs) * sync.railHeight}px`;
      rail.appendChild(t);
    }
    for (const o of s.outline || []) {
      const c = document.createElement("div");
      c.className = "rail-chapter";
      c.title = o.label;
      c.style.top = `${(o.ms / durationMs) * sync.railHeight}px`;
      c.dataset.ms = o.ms;
      rail.appendChild(c);
    }
  });
  rail.addEventListener("click", (e) => {
    const ms = e.target.dataset.ms
      ? +e.target.dataset.ms
      : ((e.clientY - rail.getBoundingClientRect().top) / sync.railHeight) * durationMs;
    player.seek(ms, true); resetFollow(); tickOnce();
  });

  view.addEventListener("click", (e) => {
    const el = e.target.closest("[data-ms]");
    if (el && !el.classList.contains("rail-chapter")) {
      player.seek(+el.dataset.ms); resetFollow(); tickOnce();
    }
  });

  // deep-link seek (#/m/{id}?t=ms)
  const tParam = new URLSearchParams(location.hash.split("?")[1] || "").get("t");
  if (tParam) setTimeout(() => { player.seek(+tParam, false); tickOnce();
    sync.els[findSegment(+tParam)]?.scrollIntoView({ block: "center" }); }, 300);

  // actions
  $("#p-actions").addEventListener("change", async (e) => {
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

  api(`/api/sessions/${encodeURIComponent(s.id)}/share`).then(({ token }) => {
    if (token) $("#btn-unshare").classList.remove("hidden");
  });
  $("#btn-share").addEventListener("click", async () => {
    const { url } = await post(`/api/sessions/${encodeURIComponent(s.id)}/share?lang=${lang}`);
    $("#btn-unshare").classList.remove("hidden");  // token is live regardless of clipboard
    try {
      await navigator.clipboard.writeText(url);
      $("#btn-share").textContent = lang === "ru" ? "Ссылка скопирована ✓" : "Link copied ✓";
      setTimeout(() => { $("#btn-share").textContent = lang === "ru" ? "Поделиться" : "Share"; }, 2500);
    } catch {
      prompt(lang === "ru" ? "Скопируйте ссылку:" : "Copy the link:", url);
    }
  });
  $("#btn-unshare").addEventListener("click", async () => {
    await api(`/api/sessions/${encodeURIComponent(s.id)}/share`, { method: "DELETE" });
    $("#btn-unshare").classList.add("hidden");
    alert(lang === "ru" ? "Ссылка отозвана — больше не работает." : "Link revoked — it no longer works.");
  });
  $("#btn-del").addEventListener("click", async () => {
    if (!confirm(`Delete "${s.title || s.id}" from the dashboard? The original stays on the Mac.`)) return;
    await api(`/api/sessions/${encodeURIComponent(s.id)}`, { method: "DELETE" });
    location.hash = "#/";
  });

  // language toggle — server truth for state; auto-translate when needed
  const runTranslate = async (btn) => {
    const orig = btn.textContent;
    btn.textContent = "…";
    $("#lang-toggle").style.pointerEvents = "none";
    try {
      const r = await post(`/api/sessions/${encodeURIComponent(s.id)}/translate?lang=ru`);
      if (r.job_id) {
        let done = false;
        for (let i = 0; i < 200; i++) {
          await new Promise((res) => setTimeout(res, 2000));
          const jb = await api(`/api/jobs/${r.job_id}`);
          if (jb.status === "done") { done = true; break; }
          if (jb.status === "failed") throw new Error(jb.error || "translation failed");
        }
        if (!done) throw new Error("translation timed out — try again");
      }
      return true;
    } catch (err) {
      alert("Перевод не удался: " + err.message);
      localStorage.setItem("quill_lang", "en");
      return false;
    } finally {
      btn.textContent = orig;
      const lt = $("#lang-toggle");
      if (lt) lt.style.pointerEvents = "";
    }
  };
  const rerenderIfHere = () => {
    if (location.hash.includes(encodeURIComponent(s.id))) meetingView(s.id);
  };
  $("#lang-toggle").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-lang]");
    if (!b) return;
    const want = b.dataset.lang;
    localStorage.setItem("quill_lang", want);
    if (want === "ru" && !s.lang_ready.ru) {
      if (await runTranslate(b)) rerenderIfHere();
      return;
    }
    if (want !== lang) rerenderIfHere();
  });
  // Sticky RU preference: meeting not translated yet — start it now, silently.
  if (wantLang === "ru" && !s.lang_ready.ru && s.ai_status === "done") {
    const ruBtn = $('#lang-toggle [data-lang="ru"]');
    runTranslate(ruBtn).then((ok) => { if (ok) rerenderIfHere(); });
  }

  $("#chat-suggest")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".suggest-chip");
    if (!chip) return;
    $("#chat-input").value = chip.textContent;
    $("#chat-form").requestSubmit();
  });

  // regenerate
  $("#btn-regen").addEventListener("click", async () => {
    await post(`/api/sessions/${encodeURIComponent(s.id)}/regenerate`);
    $("#btn-regen").textContent = "AI working…";
    pollAI(s.id, "pending");
  });
  if (s.ai_status === "pending" || s.ai_status === "running"
      || (s.ai_status === "failed" && s.ai_retry_at)) {
    pollAI(s.id, s.ai_status);
  }

  // in-transcript search
  wireTranscriptSearch();

  // chat
  const log = $("#chat-log");
  api(`/api/chat/session?session_id=${encodeURIComponent(s.id)}`).then(({ messages }) => {
    for (const m of messages) addMsg(log, m.role, m.content);
    if (messages.length) $("#chat-suggest")?.classList.add("hidden");
  });
  $("#chat-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("#chat-input"), btn = e.target.querySelector("button");
    const q = input.value.trim();
    if (!q || btn.disabled) return;
    input.value = ""; btn.disabled = true;
    addMsg(log, "user", q);
    const think = addMsg(log, "assistant thinking", "thinking…");
    try {
      const { answer } = await postJob(`/api/sessions/${encodeURIComponent(s.id)}/chat`, { question: q });
      think.remove(); addMsg(log, "assistant", answer);
    } catch (err) { think.textContent = "Failed: " + err.message; }
    btn.disabled = false;
  });
}

function aiLabel(s) {
  return s.ai_status === "transcribing" ? "Transcribing on Mac…"
    : s.ai_status === "transcription_failed" ? "Local transcription failed"
    : s.ai_status === "failed" && s.ai_retry_at
    ? `AI paused: ${s.ai_error || "temporary failure"} — automatic retry scheduled`
    : s.ai_status === "failed" ? `AI failed: ${s.ai_error || "unknown"} — hit Regenerate`
    : s.ai_status === "running" ? "AI is reading the meeting…"
    : s.ai_status === "pending" ? "AI queued…" : "";
}

function aiBadgeText(s) {
  return s.ai_status === "transcribing" ? "Transcribing on Mac…"
    : s.ai_status === "transcription_failed" ? "Transcription failed"
    : s.ai_status === "failed" && s.ai_retry_at ? "AI retrying…"
    : s.ai_status === "failed" ? "AI failed"
    : "AI working…";
}

function actionsHTML(actions) {
  if (!actions?.length) return `<p style="color:var(--paper-dim);font-size:13.5px">None found.</p>`;
  return actions.map((a) => `
    <label class="action-item ${a.done ? "done" : ""}">
      <input type="checkbox" data-action-id="${a.id}" ${a.done ? "checked" : ""}>
      <span class="at">${esc(a.text)}
        ${a.assignee ? `<span class="assignee">@${esc(a.assignee)}</span>` : ""}
        ${a.source_ms != null ? `<span class="src" data-ms="${a.source_ms}">${fmt(a.source_ms)}</span>` : ""}
      </span>
    </label>`).join("");
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
