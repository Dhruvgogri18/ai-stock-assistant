/* Stock Research UI. Vanilla JS, no build step.
   Sessions, messages and LLM logs live on the server; the browser keeps no chat state. */
(() => {
"use strict";

/* ------------------------------------------------------------------ icons */
const P = {
  plus: '<path d="M5 12h14M12 5v14"/>',
  search: '<circle cx="11" cy="11" r="7.5"/><path d="m21 21-4.3-4.3"/>',
  globe: '<circle cx="12" cy="12" r="9.5"/><path d="M12 2.5a14 14 0 0 0 0 19 14 14 0 0 0 0-19M2.5 12h19"/>',
  markets: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="m7.5 14.5 3.2-3.2 2.3 2.3 3.5-4"/>',
  screener: '<path d="M2 3h20M21 3v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V3M7 21l5-5 5 5"/>',
  mic: '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3zM19 10v2a7 7 0 0 1-14 0v-2M12 19v3"/>',
  panel: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M9 3v18"/>',
  clip: '<path d="m21.4 11-9.2 9.2a6 6 0 0 1-8.5-8.5l8.6-8.6a4 4 0 1 1 5.7 5.7l-8.6 8.6a2 2 0 0 1-2.8-2.8l8.5-8.5"/>',
  at: '<circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8"/>',
  up: '<path d="m5 12 7-7 7 7M12 19V5"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2.5" class="f"/>',
  send: '<path d="m22 2-7 20-4-9-9-4zM22 2 11 13"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a2 2 0 0 0 3.4 0"/>',
  bookmark: '<path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>',
  copy: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M4 16c-1 0-2-1-2-2V4c0-1 1-2 2-2h10c1 0 2 1 2 2"/>',
  thumbUp: '<path d="M7 10v12M15 5.9 14 10h5.8a2 2 0 0 1 1.9 2.6l-2.3 8a2 2 0 0 1-1.9 1.4H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.8a2 2 0 0 0 1.8-1.1L12 2a3.1 3.1 0 0 1 3 3.9z"/>',
  thumbDown: '<path d="M17 14V2M9 18.1 10 14H4.2a2 2 0 0 1-1.9-2.6l2.3-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.8a2 2 0 0 0-1.8 1.1L12 22a3.1 3.1 0 0 1-3-3.9z"/>',
  arrowR: '<path d="M5 12h14m-7-7 7 7-7 7"/>',
  more: '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  chevR: '<path d="m9 18 6-6-6-6"/>',
  spark: '<path d="M12 2.5l2.3 7.2 7.2 2.3-7.2 2.3L12 21.5l-2.3-7.2L2.5 12l7.2-2.3z" class="f"/>',
  chart: '<path d="M3 3v18h18M7 15l4-4 3 3 5-6"/>',
  expand: '<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>',
  trash: '<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  filter: '<path d="M3 5h18l-7 8v6l-4 2v-8z"/>',
  sliders: '<path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  chevL: '<path d="m15 18-6-6 6-6"/>',
  chevD: '<path d="m6 9 6 6 6-6"/>',
  check: '<path d="m5 12 5 5 9-10"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-3-6.7M21 4v5h-5"/>',
  star: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
};
const icon = (n, cls = "") => `<svg viewBox="0 0 24 24" class="${cls}">${P[n].replace(/class="f"/g, 'style="fill:currentColor;stroke:none"')}</svg>`;
const hydrateIcons = (root = document) => root.querySelectorAll("[data-i]").forEach(el => { if (!el.firstChild || el.dataset.hydrated !== "1") { el.insertAdjacentHTML("afterbegin", icon(el.dataset.i)); el.dataset.hydrated = "1"; } });

/* ---------------------------------------------------------------- helpers */
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function api(path, opts = {}) {
  const r = await fetch("/api" + path, { headers: { "Content-Type": "application/json" }, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined });
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && (data.detail || data.error)) || r.statusText);
  return data;
}
let toastT;
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toastT); toastT = setTimeout(() => t.hidden = true, 2600); }
const fmtMs = ms => ms == null ? "" : ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
const fmtNum = v => v == null || v === "" ? "—" : typeof v === "number" ? v.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : esc(v);

/* --------------------------------------------------------------- markdown */
function inline(s) {
  s = esc(s);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return s;
}
const splitRow = l => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim());
const isSep = l => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
function md(src) {
  const L = String(src || "").replace(/\r/g, "").split("\n"); let i = 0; const out = [];
  const startsBlock = l => /^(#{1,4}\s|>|\s*[-*•]\s|\s*\d+[.)]\s|```|-{3,}\s*$|\s*\|)/.test(l);
  while (i < L.length) {
    const l = L[i];
    if (!l.trim()) { i++; continue; }
    if (l.startsWith("```")) { const buf = []; i++; while (i < L.length && !L[i].startsWith("```")) buf.push(L[i++]); i++; out.push(`<pre><code>${esc(buf.join("\n"))}</code></pre>`); continue; }
    let m;
    if ((m = l.match(/^(#{1,4})\s+(.*)$/))) { const n = Math.min(m[1].length, 3); out.push(`<h${n}>${inline(m[2])}</h${n}>`); i++; continue; }
    if (/^-{3,}\s*$/.test(l) || /^\*{3,}\s*$/.test(l)) { out.push("<hr>"); i++; continue; }
    if (l.includes("|") && i + 1 < L.length && isSep(L[i + 1])) {
      const head = splitRow(l), al = splitRow(L[i + 1]).map(c => c.startsWith(":") && c.endsWith(":") ? "c" : c.endsWith(":") ? "r" : "");
      i += 2; const rows = [];
      while (i < L.length && L[i].includes("|") && L[i].trim()) rows.push(splitRow(L[i++]));
      out.push(`<div class="tbl"><table><thead><tr>${head.map((h, k) => `<th class="${al[k] || ""}">${inline(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${head.map((_, k) => `<td class="${al[k] || ""}">${inline(r[k] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    if (l.startsWith(">")) { const buf = []; while (i < L.length && L[i].startsWith(">")) buf.push(L[i++].replace(/^>\s?/, "")); out.push(`<blockquote>${buf.join("\n").split(/\n{2,}/).map(p => `<p>${inline(p).replace(/\n/g, "<br>")}</p>`).join("")}</blockquote>`); continue; }
    if (/^\s*[-*•]\s/.test(l)) { const buf = []; while (i < L.length && /^\s*[-*•]\s/.test(L[i])) buf.push(L[i++].replace(/^\s*[-*•]\s+/, "")); out.push(`<ul>${buf.map(b => `<li>${inline(b)}</li>`).join("")}</ul>`); continue; }
    if (/^\s*\d+[.)]\s/.test(l)) { const buf = []; while (i < L.length && /^\s*\d+[.)]\s/.test(L[i])) buf.push(L[i++].replace(/^\s*\d+[.)]\s+/, "")); out.push(`<ol>${buf.map(b => `<li>${inline(b)}</li>`).join("")}</ol>`); continue; }
    const buf = [l]; i++;
    while (i < L.length && L[i].trim() && !startsBlock(L[i])) buf.push(L[i++]);
    out.push(`<p>${inline(buf.join("\n")).replace(/\n/g, "<br>")}</p>`);
  }
  return out.join("\n");
}

/* ------------------------------------------------------------------ state */
const S = { me: {}, sessions: [], id: null, title: "", busy: false, symbols: [], messages: [] };
let pollT = null, listening = null;

/* ---------------------------------------------------------------- sidebar */
function groupSessions(list) {
  const recent = list.slice(0, 5), rest = list.slice(5), groups = [["Recent chats", recent]];
  const by = new Map();
  rest.forEach(s => { const k = new Date(s.updated_at).toLocaleString("en-US", { month: "long", year: new Date(s.updated_at).getFullYear() === new Date().getFullYear() ? undefined : "numeric" }); (by.get(k) || by.set(k, []).get(k)).push(s); });
  by.forEach((v, k) => groups.push([k, v]));
  return groups;
}
function renderSidebar() {
  const el = $("#recent");
  if (!S.sessions.length) { el.innerHTML = '<div class="group-label">Recent chats</div><div class="empty-recent">No chats yet. Ask your first question.</div>'; }
  else el.innerHTML = groupSessions(S.sessions).filter(g => g[1].length).map(([label, items]) =>
    `<div class="group-label">${esc(label)}</div>` + items.map(s => `<button class="chat-item ${s.session_id === S.id ? "active" : ""}" data-id="${s.session_id}" title="${esc(s.title)}"><span class="t">${esc(s.title)}</span>${s.session_id === S.id && S.busy ? '<span class="dot"></span>' : ""}</button>`).join("")).join("");
  const name = S.me.name || "You";
  $("#user").innerHTML = `<div class="avatar">${esc(name[0].toUpperCase())}</div><div><b>${esc(name)}</b><small>${S.sessions.length} conversation${S.sessions.length === 1 ? "" : "s"}</small></div>`;
}
async function refreshSessions() { S.sessions = await api("/sessions"); renderSidebar(); }

/* --------------------------------------------------------------- composers */
const PLACEHOLDERS = ["Which sectors are leading", "Compare TCS with its peers", "Stocks with P/E below 25 and ROCE above 15", "Show Reliance's 5-year revenue and profit", "What is driving IT stocks today"];
function composerHTML(placeholder) {
  return `<div class="composer"><textarea rows="1" placeholder="${esc(placeholder)}" aria-label="Message"></textarea>
    <div class="c-row"><div class="c-left"><div class="pill"><button data-act="attach" disabled title="File attachments are not supported yet">${icon("clip")}</button><i></i><button data-act="mention" title="Mention a company">${icon("at")}</button></div></div>
    <div class="c-right"><button class="mic" data-act="mic" title="Voice input">${icon("mic")}</button><button class="send" data-act="send" aria-label="Send">${icon("up")}</button></div></div></div>`;
}
function wireComposer(root, { rotate = false } = {}) {
  root.innerHTML = composerHTML(rotate ? PLACEHOLDERS[0] : "Ask me anything…");
  const ta = $("textarea", root), send = $(".send", root), mic = $(".mic", root);
  const sync = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 200) + "px"; send.classList.toggle("ready", !!ta.value.trim() && !S.busy); };
  ta.addEventListener("input", sync);
  ta.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(ta); } });
  send.addEventListener("click", () => submit(ta));
  $('[data-act="mention"]', root).addEventListener("click", e => mentionMenu(e.currentTarget, ta, sync));
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) mic.style.display = "none";
  else mic.addEventListener("click", () => {
    if (listening) { listening.stop(); return; }
    const r = new SR(); r.lang = "en-IN"; r.interimResults = false;
    r.onresult = e => { ta.value = (ta.value + " " + e.results[0][0].transcript).trim(); sync(); };
    r.onend = () => { listening = null; mic.classList.remove("on"); };
    r.onerror = () => toast("Voice input unavailable");
    listening = r; mic.classList.add("on"); r.start();
  });
  if (rotate) { let k = 0; setInterval(() => { if (!ta.value && document.activeElement !== ta) ta.placeholder = PLACEHOLDERS[++k % PLACEHOLDERS.length]; }, 3500); }
  root._ta = ta; root._sync = sync;
}
function composerBusy(busy) {
  document.querySelectorAll(".send").forEach(b => { b.classList.toggle("busy", busy); b.innerHTML = icon(busy ? "stop" : "up"); });
  document.querySelectorAll(".composer textarea").forEach(t => t.disabled = busy);
  document.querySelectorAll(".composer").forEach(c => { const d = c.parentElement; d._sync && d._sync(); });
}
function mentionMenu(btn, ta, sync) {
  const r = btn.getBoundingClientRect(), pop = $("#popover");
  pop.innerHTML = S.symbols.map(s => `<button data-sym="${s}">${s}</button>`).join("");
  pop.hidden = false; pop.style.left = r.left + "px"; pop.style.top = Math.max(8, r.top - 310) + "px";
  pop.onclick = e => { const s = e.target.dataset.sym; if (s) { ta.value += (ta.value && !ta.value.endsWith(" ") ? " " : "") + s + " "; ta.focus(); sync(); pop.hidden = true; } };
}
document.addEventListener("click", e => { const pop = $("#popover"); if (!pop.hidden && !pop.contains(e.target) && !e.target.closest('[data-act="mention"],#moreBtn')) pop.hidden = true; });

function submit(ta) { const text = ta.value.trim(); if (!text || S.busy) return; ta.value = ""; ta.dispatchEvent(new Event("input")); send(text); }

/* ------------------------------------------------------------------- views */
// Full-page views (screener, watchlist, calendar, alerts) register themselves in window.Pages = { name: {show(arg), hide()} }
// and render into <section id="name">. Chat is the default view.
const PAGE_RE = /^#\/(screener|watchlist|calendar|alerts)(?:\/([\w-]+))?/;
function leaveScreener() {
  document.querySelectorAll("section.page").forEach(s => { if (!s.hidden) window.Pages?.[s.id]?.hide(); s.hidden = true; });
  document.querySelectorAll("[data-go].active").forEach(b => b.classList.remove("active"));
}
function showLanding() {
  S.id = null; S.title = ""; S.messages = [];
  leaveScreener();
  if (PAGE_RE.test(location.hash)) history.replaceState(null, "", "#/");   // otherwise clicking the same nav item again would not navigate
  $("#landing").hidden = false; $("#chat").hidden = true; renderSidebar();
  $("#landingComposer")._ta && $("#landingComposer")._ta.focus();
}
function showChatShell() { leaveScreener(); $("#landing").hidden = true; $("#chat").hidden = false; $("#chatTitle").textContent = S.title; renderSidebar(); }

async function openChat(id) {
  try {
    const d = await api("/session/" + id);
    S.id = id; S.title = d.title; S.messages = d.messages;
    showChatShell();
    const th = $("#thread"); th.innerHTML = "";
    d.messages.forEach(m => th.insertAdjacentHTML("beforeend", m.role === "user" ? userHTML(m.content) : assistantHTML(m)));
    $("#scroll").scrollTop = 0; bindMessageActions();
    $("#chatComposer")._ta && $("#chatComposer")._ta.focus();
    // a turn may still be running (page reload mid-answer)
    const st = await api(`/session/${id}/status`);
    if (st.busy) resumeWaiting(id);
  } catch (e) { toast("Could not open that chat"); location.hash = "#/"; }
}

async function route() {
  const sm = location.hash.match(PAGE_RE);
  if (sm && window.Pages?.[sm[1]]) {
    leaveScreener();
    S.id = null; $("#landing").hidden = true; $("#chat").hidden = true; $("#" + sm[1]).hidden = false;
    document.querySelector(`[data-go="#/${sm[1]}"]`)?.classList.add("active"); renderSidebar();
    window.Pages[sm[1]].show(sm[2] || null);
    if (innerWidth <= 820) $("#app").classList.add("collapsed");
    return;
  }
  const m = location.hash.match(/^#\/c\/([\w-]+)/);
  if (m) { if (m[1] !== S.id || $("#chat").hidden) await openChat(m[1]); } else showLanding();
  if (innerWidth <= 820) $("#app").classList.add("collapsed");
}

/* ----------------------------------------------------------------- render */
const userHTML = t => `<div class="u-msg"><div class="u-bubble">${esc(t)}</div></div>`;
const liveHTML = label => `<div class="a-msg" id="pending"><div class="live"><span class="spark">${icon("spark")}</span><span class="lbl">${esc(label)}</span></div></div>`;

function assistantHTML(m) {
  const stepsHtml = (m.steps || []).map(s => `<div><span class="${s.ok ? "ok" : "bad"}">${s.ok ? "✓" : "✕"}</span>${esc(s.label)}<em>${fmtMs(s.ms)}</em></div>`).join("");
  const u = m.usage || {};
  const meta = `<div class="meta"><span>${u.calls || 0} model call${u.calls === 1 ? "" : "s"} · ${(u.total_tokens || 0).toLocaleString()} tokens · ${fmtMs(m.duration_ms)}</span><a href="/api/session/${S.id}/log" target="_blank" rel="noopener">View LLM log</a></div>`;
  const CHART_TOOLS = ["technicals", "history"];
  const chartsHtml = (m.blocks || []).map((b, i) => CHART_TOOLS.includes(b.tool) ? blockHTML(b, m.id, i) : "").join("");
  const blocks = (m.blocks || []).map((b, i) => CHART_TOOLS.includes(b.tool) ? "" : blockHTML(b, m.id, i)).join("");
  let body = md(m.content);
  if (chartsHtml) {
    const q = body.indexOf("</blockquote>"), h = body.indexOf("</h1>");
    body = q >= 0 ? body.slice(0, q + 13) + chartsHtml + body.slice(q + 13) : h >= 0 ? body.slice(0, h + 5) + chartsHtml + body.slice(h + 5) : chartsHtml + body;
  }
  const srcs = (m.sources || []).map(s => `<span class="src">${esc(s.source)}${s.as_of ? " · " + esc(s.as_of) : ""}</span>`).join("");
  const fu = (m.follow_ups || []).length ? `<div class="also"><h3>Also worth looking into</h3>${m.follow_ups.map(q => `<button data-ask="${esc(q)}"><span>${esc(q)}</span>${icon("arrowR")}</button>`).join("")}</div>` : "";
  const err = m.error && m.error !== "llm_not_configured" ? `<div class="err-note">The model call failed. <button data-retry="1">Retry</button></div>` : "";
  return `<div class="a-msg" data-mid="${m.id}">
    <button class="think" data-think>${icon("spark", "sp")}<span>Thinking completed</span>${icon("chevR", "chev")}</button>
    <div class="steps" hidden>${stepsHtml}${meta}</div>
    <div class="md">${body}</div>${err}${blocks}
    ${srcs ? `<div class="sources">${srcs}</div>` : ""}${fu}
    <div class="actions"><button data-act="copy" title="Copy">${icon("copy")}</button><i></i><button data-act="up" class="${m.feedback === "up" ? "on" : ""}" title="Good answer">${icon("thumbUp")}</button><i></i><button data-act="down" class="${m.feedback === "down" ? "on" : ""}" title="Bad answer">${icon("thumbDown")}</button><i></i><button data-act="share" title="Share">${icon("send")}</button></div></div>`;
}

/* screener + chart blocks */
const COLORS = ["#2563eb", "#7c3aed", "#0891b2", "#16a34a", "#d97706", "#dc2626", "#0f766e", "#be185d"];
const colorOf = s => COLORS[[...s].reduce((a, c) => a + c.charCodeAt(0), 0) % COLORS.length];
function blockHTML(b, mid, idx) {
  const d = b.data || {};
  if ((b.tool === "screener" || b.tool === "market_movers") && Array.isArray(d.rows)) {
    const html = screenerHTML(d, `${mid}-${idx}`);
    screenerStore[`${mid}-${idx}`].src = { tool: b.tool, args: b.args || {} };   // lets "Open Screener" rebuild it as a saved screener
    return html;
  }
  if (b.tool === "technicals" && d.chart && d.chart.close?.length > 1) return techChartHTML(d, `${mid}-${idx}`);
  if (b.tool === "history" && d.chart && d.chart.close?.length > 1)
    return techChartHTML({ chart: { ...d.chart, overlays: [], panels: [] }, title: `${b.args.symbol} · Price`, sub: priceSub(d.chart) }, `${mid}-${idx}`);
  return "";
}
const screenerStore = {};
function screenerHTML(d, key) {
  screenerStore[key] = screenerStore[key] || { d, sort: null, dir: 1 };
  const st = screenerStore[key];
  const hasPx = d.rows.some(r => r["Day Change %"] != null && r.LTP != null);
  const shown = new Set(hasPx ? ["Day Change %", "LTP"] : []);
  const fm = (Array.isArray(d.filters) ? [...new Set(d.filters.map(f => f.metric))] : []).filter(m => !shown.has(m));
  const cols = (d.columns && d.columns.length ? d.columns : fm.length ? fm : hasPx ? ["Turnover (Cr)"] : ["P/E", "ROCE %"]);
  let rows = [...d.rows];
  if (st.sort) rows.sort((a, b) => { const x = a[st.sort], y = b[st.sort]; if (x == null) return 1; if (y == null) return -1; return (typeof x === "string" ? x.localeCompare(y) : x - y) * st.dir; });
  const th = (k, label, num) => `<th data-sort="${esc(k)}" class="${num ? "num" : ""}"><span class="ar">${st.sort === k ? (st.dir > 0 ? "▲" : "▼") : "⇅"}</span>${esc(label)}</th>`;
  const sgn = v => (v > 0 ? "+" : "") + Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const dayCell = r => { const c = r["Day Change %"], ch = r.Change; if (c == null) return `<td class="num">${fmtNum(r.LTP)}</td>`;
    const cls = c > 0 ? "pos" : c < 0 ? "neg" : ""; return `<td class="num"><b>${Number(r.LTP).toLocaleString("en-IN", { minimumFractionDigits: 2 })}</b><br><span class="${cls}" style="font-size:11px">${ch != null ? sgn(ch) + " " : ""}(${sgn(c)}%)</span></td>`; };
  const body = rows.map(r => `<tr><td><div class="co"><i style="background:${colorOf(r.symbol)}">${esc(r.symbol.slice(0, 2))}</i>${esc(r.name || r.symbol)}</div></td><td>${esc(r.symbol)}</td><td>${esc(r.sector || "—")}</td>${hasPx ? dayCell(r) : ""}${cols.map(c => `<td class="num">${fmtNum(r[c])}</td>`).join("")}</tr>`).join("");
  const filt = Array.isArray(d.filters) ? d.filters.map(f => `${f.metric} ${f.op} ${f.value}`).join(" · ") : "";
  const total = d.total_matched ?? rows.length;
  const asof = d.as_of ? ` · As of ${esc(d.as_of)}` : "";
  return `<div class="sc" data-key="${key}">
    <div class="sc-bar"><button class="sc-btn" disabled title="Alerts arrive in Phase 2">${icon("bell")}Set Alert</button><button class="sc-btn" data-sc="save">${icon("bookmark")}Save</button><span class="sp"></span>
      <button class="sc-btn alt" data-sc="share">${icon("send")}Share</button><button class="sc-btn dark" data-sc="open">${icon("screener")}Open Screener</button></div>
    <div class="sc-wrap"><table><thead><tr>${th("name", "Company Name")}${th("symbol", "Symbol")}${th("sector", "Sector")}${hasPx ? th("Day Change %", "Day Change", true) : ""}${cols.map(c => th(c, c, true)).join("")}</tr></thead><tbody>${body || `<tr><td colspan="${3 + (hasPx ? 1 : 0) + cols.length}" style="color:#999">No stocks match these filters.</td></tr>`}</tbody></table></div>
    <div class="sc-foot">Showing ${rows.length ? "1 - " + rows.length : 0} of ${Number(total).toLocaleString("en-IN")} results${filt ? " · " + esc(filt) : ""}${asof}</div></div>`;
}
function priceSub(c) {
  const n = c.close.length, min = Math.min(...c.close), max = Math.max(...c.close), chg = (c.close[n - 1] / c.close[0] - 1) * 100, up = chg >= 0;
  return `<span class="${up ? "pos" : "neg"}">${up ? "+" : ""}${chg.toFixed(1)}%</span> · ${esc(c.dates[0])} → ${esc(c.dates[n - 1])} · High ${fmtNum(max)} · Low ${fmtNum(min)} · Last ${fmtNum(c.close[n - 1])}`;
}
function techSub(s) {
  if (!s) return "";
  const up = (s.window_return_pct ?? 0) >= 0;
  return `<span class="${up ? "pos" : "neg"}">${up ? "+" : ""}${s.window_return_pct}%</span> · ${esc(s.window_start)} → ${esc(s.latest_date)} · High ${fmtNum(s.window_high_close)} · Low ${fmtNum(s.window_low_close)} · Last ${fmtNum(s.latest_close)}`;
}

/* Price chart with optional indicator overlays (EMA/SMA/Bollinger) and panels (RSI/MACD/ATR). Pure SVG. */
const techStore = {};
const OV_COLORS = ["#f5a524", "#16a34a", "#7c3aed", "#0891b2", "#db2777", "#92400e"];
const TW = 680, TL = 56, TR = 12, TT = 8, TMH = 250, TAX = 26, TPH = 92, TGAP = 26;
const axNum = v => Math.abs(v) >= 100 ? Math.round(v).toLocaleString("en-IN") : v.toFixed(Math.abs(v) >= 10 ? 1 : 2);
const axDate = (d, withYear) => { const t = new Date(d + "T00:00:00"); return t.toLocaleDateString("en-GB", withYear ? { month: "short", year: "numeric" } : { day: "2-digit", month: "short" }); };
function niceTicks(min, max, n = 5) {
  const raw = (max - min || 1) / (n - 1), mag = 10 ** Math.floor(Math.log10(raw)), f = raw / mag;
  const step = (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag, out = [];
  for (let v = Math.floor(min / step) * step; v <= Math.ceil(max / step) * step + step * 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}
const linePath = (vals, X, Y) => { let d = "", pen = false; vals.forEach((v, i) => { if (v == null) { pen = false; return; } d += (pen ? "L" : "M") + X(i).toFixed(1) + "," + Y(v).toFixed(1); pen = true; }); return d; };

function techChartHTML(d, key) {
  const c = d.chart, n = c.dates.length, pw = TW - TL - TR, X = i => TL + (n <= 1 ? 0 : i / (n - 1) * pw);
  const series = [{ name: "Close", values: c.close, color: "#2563eb", w: 1.8 }];
  let ci = 0;
  (c.overlays || []).forEach(o => series.push(o.kind === "band" ? { name: o.name, values: o.values, color: "#9ca3af", w: 1, dash: o.dash ? "4 3" : "", band: true }
    : { name: o.name, values: o.values, color: OV_COLORS[ci++ % OV_COLORS.length], w: 1.6 }));
  const panels = c.panels || [];
  techStore[key] = { c, series, panels, n };

  let lo = Infinity, hi = -Infinity;
  series.forEach(s => s.values.forEach(v => { if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }));
  const pad = (hi - lo) * 0.05 || 1, ticks = niceTicks(lo - pad, hi + pad), y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const Y = v => TT + (1 - (v - y0) / (y1 - y0)) * TMH;
  const H = TT + TMH + TAX + panels.length * (TPH + TGAP) + 4;
  let svg = "";
  ticks.forEach(t => { svg += `<line class="g" x1="${TL}" x2="${TW - TR}" y1="${Y(t).toFixed(1)}" y2="${Y(t).toFixed(1)}"/><text x="${TL - 8}" y="${(Y(t) + 3.5).toFixed(1)}" text-anchor="end">${axNum(t)}</text>`; });
  const long = n > 300, cnt = Math.min(7, n);
  [...new Set(Array.from({ length: cnt }, (_, k) => Math.round(k * (n - 1) / Math.max(cnt - 1, 1))))].forEach(i => {
    svg += `<text x="${X(i).toFixed(1)}" y="${TT + TMH + 16}" text-anchor="${i === n - 1 ? "end" : i === 0 ? "start" : "middle"}">${axDate(c.dates[i], long)}</text>`; });
  svg += `<line class="ax" x1="${TL}" x2="${TW - TR}" y1="${TT + TMH}" y2="${TT + TMH}"/>`;

  const up = series.find(s => s.name === "BB Upper"), lw = series.find(s => s.name === "BB Lower");   // shaded Bollinger band
  if (up && lw) { const idx = up.values.map((v, i) => (v != null && lw.values[i] != null) ? i : -1).filter(i => i >= 0);
    if (idx.length > 1) svg += `<polygon points="${idx.map(i => X(i).toFixed(1) + "," + Y(up.values[i]).toFixed(1)).join(" ")} ${[...idx].reverse().map(i => X(i).toFixed(1) + "," + Y(lw.values[i]).toFixed(1)).join(" ")}" fill="#9ca3af" fill-opacity=".10" stroke="none"/>`; }
  [...series].reverse().forEach(s => { svg += `<path d="${linePath(s.values, X, Y)}" stroke="${s.color}" stroke-width="${s.w}" ${s.dash ? `stroke-dasharray="${s.dash}"` : ""} fill="none" stroke-linejoin="round"/>`; });

  panels.forEach((p, k) => {
    const top = TT + TMH + TAX + k * (TPH + TGAP) + 14, bot = top + TPH;
    let vals = p.type === "macd" ? [...p.macd, ...p.signal, ...p.hist] : p.values;
    let a = Math.min(...vals.filter(v => v != null)), b = Math.max(...vals.filter(v => v != null));
    if (p.domain) [a, b] = p.domain; else if (p.type === "macd") { const m = Math.max(Math.abs(a), Math.abs(b)) * 1.1 || 1; a = -m; b = m; } else { const q = (b - a) * 0.08 || 1; a -= q; b += q; }
    const PY = v => top + (1 - (v - a) / (b - a)) * TPH;
    svg += `<text class="ptitle" x="${TL}" y="${top - 6}">${esc(p.name)}</text><rect x="${TL}" y="${top}" width="${pw}" height="${TPH}" fill="none" stroke="#e5e7eb"/>`;
    if (p.domain) p.domain.forEach(v => { svg += `<text x="${TL - 8}" y="${PY(v) + 3.5}" text-anchor="end">${v}</text>`; });
    (p.bands || []).forEach(v => { svg += `<line x1="${TL}" x2="${TW - TR}" y1="${PY(v)}" y2="${PY(v)}" stroke="#f87171" stroke-dasharray="4 3" stroke-opacity=".7"/><text x="${TL - 8}" y="${PY(v) + 3.5}" text-anchor="end">${v}</text>`; });
    if (p.type === "macd") {
      svg += `<line class="ax" x1="${TL}" x2="${TW - TR}" y1="${PY(0)}" y2="${PY(0)}"/><text x="${TL - 8}" y="${PY(0) + 3.5}" text-anchor="end">0</text>`;
      const bw = Math.max(1, pw / n * 0.6);
      p.hist.forEach((v, i) => { if (v != null) svg += `<rect x="${(X(i) - bw / 2).toFixed(1)}" y="${Math.min(PY(v), PY(0)).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(0.5, Math.abs(PY(v) - PY(0))).toFixed(1)}" fill="${v >= 0 ? "#86efac" : "#fca5a5"}" stroke="none"/>`; });
      svg += `<path d="${linePath(p.macd, X, PY)}" stroke="#2563eb" stroke-width="1.4" fill="none"/><path d="${linePath(p.signal, X, PY)}" stroke="#f5a524" stroke-width="1.4" fill="none"/>`;
    } else {
      if (!p.domain) [a, b].forEach(v => svg += `<text x="${TL - 8}" y="${PY(v) + 3.5}" text-anchor="end">${axNum(v)}</text>`);
      svg += `<path d="${linePath(p.values, X, PY)}" stroke="#7c3aed" stroke-width="1.5" fill="none"/>`;
    }
  });
  svg += `<line class="xh" x1="0" x2="0" y1="${TT}" y2="${H - 4}" style="display:none"/>`;

  const legend = [...series.filter(s => !s.band || s.name === "BB Middle"), ...panels.map(p => ({ name: p.name, color: p.type === "macd" ? "#2563eb" : "#7c3aed", panel: true }))]
    .map(s => `<span><i style="border-color:${s.color}"></i>${esc(s.name)}</span>`).join("");
  const warn = (d.data_quality?.warnings || []).map(w => `<div class="cnote">⚠ ${esc(w)}</div>`).join("");
  return `<div class="chart tchart" data-chart="${key}"><h4>${esc(d.title || "Price")}</h4><div class="sub">${d.sub || techSub(d.summary)}</div>
    <svg class="tc" data-key="${key}" viewBox="0 0 ${TW} ${H}">${svg}</svg><div class="legend">${legend}</div>${warn}</div>`;
}

/* ------------------------------------------------------- message actions */
function bindMessageActions() { /* delegated below */ }
$("#thread").addEventListener("click", async e => {
  const t = e.target;
  const think = t.closest("[data-think]"); if (think) { think.classList.toggle("open"); think.nextElementSibling.hidden = !think.nextElementSibling.hidden; return; }
  const ask = t.closest("[data-ask]"); if (ask) { send(ask.dataset.ask); return; }
  if (t.closest("[data-retry]")) { const last = [...S.messages].reverse().find(m => m.role === "user"); if (last) send(last.content); return; }
  const sort = t.closest("th[data-sort]");
  if (sort) { const card = sort.closest(".sc"), st = screenerStore[card.dataset.key], k = sort.dataset.sort; st.dir = st.sort === k ? -st.dir : 1; st.sort = k; card.outerHTML = screenerHTML(st.d, card.dataset.key); return; }
  const sc = t.closest("[data-sc]");
  if (sc) {
    const card = sc.closest(".sc"), st = screenerStore[card.dataset.key];
    if (sc.dataset.sc === "share") { await copyText(["Company\tSymbol\tSector", ...st.d.rows.map(r => `${r.name}\t${r.symbol}\t${r.sector}`)].join("\n")); toast("Table copied"); }
    if (sc.dataset.sc === "save" || sc.dataset.sc === "open") {
      try {
        if (!st.savedId) st.savedId = (await api("/screeners/from-tool", { method: "POST", body: st.src })).id;   // reuse on Save then Open
        if (sc.dataset.sc === "open") location.hash = "#/screener/" + st.savedId;
        else { toast("Saved to Screener · Created by you"); sc.innerHTML = `${icon("bookmark")}Saved`; }
      } catch (e) { toast(e.message || "Could not save this screener"); }
    }
    return;
  }
  const act = t.closest(".actions button"); if (!act) return;
  const msg = act.closest(".a-msg"), mid = +msg.dataset.mid, m = S.messages.find(x => x.id === mid);
  if (act.dataset.act === "copy") { await copyText(msg.querySelector(".md").innerText + (m?.sources?.length ? "\n\nSource: " + m.sources.map(s => `${s.source}${s.as_of ? " (" + s.as_of + ")" : ""}`).join("; ") : "")); toast("Copied"); }
  if (act.dataset.act === "share") shareLink();
  if (act.dataset.act === "up" || act.dataset.act === "down") {
    const val = act.classList.contains("on") ? null : act.dataset.act;
    await api(`/session/${S.id}/feedback`, { method: "POST", body: { message_id: mid, value: val } });
    msg.querySelectorAll('[data-act="up"],[data-act="down"]').forEach(b => b.classList.toggle("on", b.dataset.act === val));
    if (m) m.feedback = val; if (val) toast("Thanks for the feedback");
  }
});
$("#thread").addEventListener("mousemove", e => {
  const tc = e.target.closest("svg.tc");
  if (tc) { techHover(e, tc); return; }
  const svg = e.target.closest(".chart svg"); if (!svg || !svg.dataset.c) return;
  const { d, v } = JSON.parse(svg.dataset.c), r = svg.getBoundingClientRect(), i = Math.max(0, Math.min(v.length - 1, Math.round(((e.clientX - r.left) / r.width) * (v.length - 1))));
  const box = svg.parentElement; let tip = $(".tip", box); if (!tip) { tip = document.createElement("div"); tip.className = "tip"; box.appendChild(tip); }
  const br = box.getBoundingClientRect(); tip.textContent = `${d[i]} · ₹${fmtNum(v[i])}`; tip.style.left = (e.clientX - br.left) + "px"; tip.style.top = (e.clientY - br.top) + "px";
});
$("#thread").addEventListener("mouseleave", () => { document.querySelectorAll(".chart .tip").forEach(t => t.remove()); document.querySelectorAll("svg.tc .xh").forEach(l => l.style.display = "none"); });
function techHover(e, svg) {
  const st = techStore[svg.dataset.key]; if (!st) return;
  const r = svg.getBoundingClientRect(), vx = (e.clientX - r.left) / r.width * TW, pw = TW - TL - TR;
  const i = Math.max(0, Math.min(st.n - 1, Math.round((vx - TL) / pw * (st.n - 1))));
  const x = TL + (st.n <= 1 ? 0 : i / (st.n - 1) * pw), line = $(".xh", svg);
  line.setAttribute("x1", x); line.setAttribute("x2", x); line.style.display = "";
  const rows = st.series.filter(s => s.values[i] != null && !(s.band && s.name !== "BB Middle")).map(s => `<span style="color:${s.color}">●</span> ${esc(s.name)} ${fmtNum(s.values[i])}`);
  st.panels.forEach(p => { if (p.type === "macd") { if (p.macd[i] != null) rows.push(`MACD ${fmtNum(p.macd[i])} · signal ${fmtNum(p.signal[i])} · hist ${fmtNum(p.hist[i])}`); } else if (p.values[i] != null) rows.push(`${esc(p.name)} ${fmtNum(p.values[i])}`); });
  const box = svg.parentElement; let tip = $(".tip", box); if (!tip) { tip = document.createElement("div"); tip.className = "tip"; box.appendChild(tip); }
  const br = box.getBoundingClientRect(); tip.innerHTML = `<b>${esc(st.c.dates[i])}</b><br>${rows.join("<br>")}`;
  tip.style.left = Math.min(Math.max(e.clientX - br.left, 90), br.width - 90) + "px"; tip.style.top = (e.clientY - br.top) + "px";
}
async function copyText(t) { try { await navigator.clipboard.writeText(t); } catch { const a = document.createElement("textarea"); a.value = t; document.body.appendChild(a); a.select(); document.execCommand("copy"); a.remove(); } }
const shareLink = async () => { await copyText(`${location.origin}/#/c/${S.id}`); toast("Chat link copied"); };
function openModal(html) { const m = $("#modal"); m.innerHTML = html; m.hidden = false; hydrateIcons(m); }
$("#modal").addEventListener("click", e => { if (e.target.id === "modal" || e.target.closest("[data-close]")) $("#modal").hidden = true; });

/* ---------------------------------------------------------------- sending */
async function send(text) {
  if (S.busy) return;
  const first = !S.id;
  try {
    if (first) {
      const { session_id } = await api("/session", { method: "POST" });
      S.id = session_id; S.title = text.slice(0, 60); S.messages = [];
      S.sessions.unshift({ session_id, title: S.title, updated_at: new Date().toISOString(), created_at: new Date().toISOString() });
      history.replaceState(null, "", "#/c/" + session_id);
      showChatShell(); $("#thread").innerHTML = "";
    }
    S.busy = true; composerBusy(true); renderSidebar();
    S.messages.push({ role: "user", content: text });
    const th = $("#thread");
    th.insertAdjacentHTML("beforeend", userHTML(text) + liveHTML("Starting…"));
    $("#scroll").scrollTop = $("#scroll").scrollHeight;
    startPolling(S.id);
    const res = await api(`/session/${S.id}/message`, { method: "POST", body: { message: text } });
    finishTurn(res);
  } catch (e) {
    stopPolling(); S.busy = false; composerBusy(false);
    $("#pending")?.remove();
    $("#thread").insertAdjacentHTML("beforeend", `<div class="a-msg"><div class="err-note">${esc(e.message || "Something went wrong")} <button data-retry="1">Retry</button></div></div>`);
    renderSidebar();
  }
}
function finishTurn(res) {
  stopPolling(); S.busy = false; composerBusy(false);
  S.title = res.title; $("#chatTitle").textContent = S.title;
  const m = res.message; S.messages.push(m);
  const pending = $("#pending"); pending.outerHTML = assistantHTML(m);
  const el = $(`.a-msg[data-mid="${m.id}"]`), sc = $("#scroll");
  sc.scrollTop = Math.max(0, el.offsetTop - 120);                   // read from the top of the answer
  const s = S.sessions.find(x => x.session_id === S.id); if (s) { s.title = res.title; s.updated_at = new Date().toISOString(); }
  S.sessions.sort((a, b) => b.updated_at.localeCompare(a.updated_at)); renderSidebar();
  $("#chatComposer")._ta?.focus();
}
function startPolling(id) {
  stopPolling();
  pollT = setInterval(async () => {
    try { const st = await api(`/session/${id}/status`); const l = $("#pending .lbl"); if (l && st.busy && st.label) l.textContent = st.label; } catch { /* ignore */ }
  }, 900);
}
const stopPolling = () => { clearInterval(pollT); pollT = null; };
async function resumeWaiting(id) {   // reload during a running turn: wait for it, then re-open
  S.busy = true; composerBusy(true); $("#thread").insertAdjacentHTML("beforeend", liveHTML("Working on your last question…")); startPolling(id);
  while (true) { await sleep(1500); const st = await api(`/session/${id}/status`).catch(() => ({ busy: false })); if (!st.busy) break; }
  stopPolling(); S.busy = false; composerBusy(false); await refreshSessions(); await openChat(id);
}

/* ------------------------------------------------------------ chrome wiring */
function openSearch() {
  openModal(`<div class="dlg"><input id="q" placeholder="Search chats…" autocomplete="off"><div class="list" id="ql"></div></div>`);
  const q = $("#q"), ql = $("#ql"); let sel = 0, hits = [];
  const draw = () => { hits = S.sessions.filter(s => s.title.toLowerCase().includes(q.value.toLowerCase())).slice(0, 30); sel = Math.min(sel, Math.max(0, hits.length - 1)); ql.innerHTML = hits.map((s, i) => `<button class="${i === sel ? "sel" : ""}" data-id="${s.session_id}">${esc(s.title)}</button>`).join("") || '<div class="empty-recent">No matching chats</div>'; };
  q.addEventListener("input", () => { sel = 0; draw(); });
  q.addEventListener("keydown", e => { if (e.key === "ArrowDown") { sel = Math.min(sel + 1, hits.length - 1); draw(); e.preventDefault(); } if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); draw(); e.preventDefault(); } if (e.key === "Enter" && hits[sel]) go(hits[sel].session_id); if (e.key === "Escape") $("#modal").hidden = true; });
  ql.addEventListener("click", e => { const b = e.target.closest("[data-id]"); if (b) go(b.dataset.id); });
  const go = id => { $("#modal").hidden = true; location.hash = "#/c/" + id; };
  draw(); q.focus();
}
function fillPrompt(text) {
  if (S.id) location.hash = "#/"; else showLanding();
  const c = $("#landingComposer"); c._ta.value = text; c._sync(); c._ta.focus();
}

function wireChrome() {
  $("#collapseBtn").innerHTML = icon("panel"); $("#menuBtn").innerHTML = icon("panel");
  $("#logo").innerHTML = icon("chart");
  const toggle = () => $("#app").classList.toggle("collapsed");
  $("#collapseBtn").onclick = toggle; $("#menuBtn").onclick = toggle;
  $("#main").addEventListener("click", () => { if (innerWidth <= 820 && !$("#app").classList.contains("collapsed")) $("#app").classList.add("collapsed"); });
  $("#newBtn").onclick = () => { if (location.hash === "#/" || !location.hash) showLanding(); else location.hash = "#/"; };
  $("#navSearch").onclick = openSearch;
  document.addEventListener("keydown", e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openSearch(); } if (e.key === "Escape") $("#modal").hidden = true; });
  document.querySelectorAll("[data-prompt]").forEach(b => b.onclick = () => fillPrompt(b.dataset.prompt));
  document.querySelectorAll("[data-go]").forEach(b => b.onclick = () => { location.hash = b.dataset.go; if (innerWidth <= 820) $("#app").classList.add("collapsed"); });
  document.querySelectorAll("[data-soon]").forEach(b => b.onclick = () => toast(b.dataset.soon));
  document.querySelectorAll(".chip").forEach(b => b.onclick = () => { const c = $("#landingComposer"); c._ta.value = b.dataset.fill; c._sync(); c._ta.focus(); });
  $("#recent").addEventListener("click", e => { const b = e.target.closest("[data-id]"); if (b) { location.hash = "#/c/" + b.dataset.id; if (innerWidth <= 820) $("#app").classList.add("collapsed"); } });
  $("#shareBtn").onclick = shareLink;
  $("#moreBtn").onclick = e => {
    const r = e.currentTarget.getBoundingClientRect(), pop = $("#popover");
    pop.innerHTML = `<button data-m="rename">Rename</button><button data-m="log">Open LLM log</button><button class="danger" data-m="delete">Delete chat</button>`;
    pop.hidden = false; pop.style.left = r.left + "px"; pop.style.top = r.bottom + 6 + "px";
    pop.onclick = async ev => {
      const m = ev.target.dataset.m; pop.hidden = true; if (!m) return;
      if (m === "log") window.open(`/api/session/${S.id}/log`, "_blank");
      if (m === "rename") { const t = prompt("Rename chat", S.title); if (t && t.trim()) { await api("/session/" + S.id, { method: "PATCH", body: { title: t.trim() } }); S.title = t.trim(); $("#chatTitle").textContent = S.title; await refreshSessions(); } }
      if (m === "delete" && confirm("Delete this chat? (Its LLM log is kept.)")) { await api("/session/" + S.id, { method: "DELETE" }); await refreshSessions(); location.hash = "#/"; }
    };
  };
}

/* ------------------------------------------------------------------- init */
// Shared helpers for screener.js (kept out of this file so each stays readable).
window.App = { api, esc, icon, hydrateIcons, toast, fmtNum, copyText, openModal, closeModal: () => { $("#modal").hidden = true; }, colorOf, state: S, ask: fillPrompt };
async function init() {
  hydrateIcons(); wireChrome();
  wireComposer($("#landingComposer"), { rotate: true }); wireComposer($("#chatComposer"));
  try { S.me = await api("/me"); } catch { S.me = {}; }
  $("#brandName").textContent = S.me.app_name || "Stock Research"; document.title = S.me.app_name || "Stock Research";
  $("#greeting").textContent = S.me.name ? `What next, ${S.me.name}?` : "What next?";
  api("/symbols").then(s => S.symbols = s).catch(() => {});
  await refreshSessions();
  window.addEventListener("hashchange", route);
  if (innerWidth <= 820) $("#app").classList.add("collapsed");
  await route();
}
init();
})();