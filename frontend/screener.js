/* Screener pages: home (#/screener) and a single screener (#/screener/<id> | #/screener/new).
   Loaded before app.js; uses the helpers app.js exposes on window.App at call time. Server owns all data. */
(() => {
"use strict";
const A = () => window.App;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => A().esc(s);
const icon = (n, c) => A().icon(n, c);
const toast = m => A().toast(m);
const api = (p, o) => A().api(p, o);
const root = () => $("#screener");
const PAGE = 50;

const ST = { tab: "curated", cat: "all", mine: "all", q: "", curated: null, minelist: null };   // home
let V = null;            // the open screener
let tok = 0;             // guards against out-of-order responses
let saveT = null;
let metricsAt = 0, M = null;   // metric catalog cache

/* ------------------------------------------------------------- formatting */
const n2 = v => Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const sgn = v => (v > 0 ? "+" : "") + n2(v);
const cls = v => (v > 0 ? "pos" : v < 0 ? "neg" : "");
const initial = s => (String(s || "?").trim()[0] || "?").toUpperCase();
const shortDate = iso => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleDateString("en-GB", { day: "numeric", month: "short" }); };
const plural = (n, w) => `${Number(n).toLocaleString("en-IN")} ${w}${n === 1 ? "" : "s"}`;
const metric = k => (M?.metrics || []).find(m => m.key === k);
const label = k => metric(k)?.label || k;

function cell(col, r) {
  const v = r[col.key], text = col.kind === "text";
  if (col.render === "day_change") {
    const c = r["Day Change %"], p = r.LTP;
    if (c == null || p == null) return `<td class="num">—</td>`;
    return `<td class="num"><b>${n2(p)}</b><br><span class="${cls(c)} sub">${r.Change != null ? sgn(r.Change) + " " : ""}(${sgn(c)}%)</span></td>`;
  }
  if (v == null || v === "") return `<td class="${text ? "" : "num"}">—</td>`;
  switch (col.fmt) {
    case "text": return `<td>${esc(v)}</td>`;
    case "pct_signed": return `<td class="num ${cls(v)}">${sgn(v)}%</td>`;
    case "int": return `<td class="num">${Math.round(v).toLocaleString("en-IN")}</td>`;
    default: return `<td class="num">${n2(v)}</td>`;
  }
}
const nameCell = r => `<td><div class="scr-co"><i style="background:${A().colorOf(r.symbol)}">${esc(r.symbol.slice(0, 2))}</i><span><a class="co-link" href="#/company/${encodeURIComponent(r.symbol)}">${esc(r.name || r.symbol)}</a><small>${esc(r.symbol)}</small></span></div></td>`;

/* -------------------------------------------------------------------- home */
async function loadHome(force) {
  const t = ++tok;
  if (force || !ST.curated || !ST.minelist) {
    root().innerHTML = shell(`<div class="scr-state">Loading screeners…</div>`);
    try {
      const [c, m] = await Promise.all([api("/screeners/curated"), api("/screeners/mine")]);
      if (t !== tok) return;
      ST.curated = c; ST.minelist = m;
    } catch (e) { if (t === tok) root().innerHTML = shell(`<div class="scr-state err">Could not load screeners: ${esc(e.message)} <button data-retry-home>Retry</button></div>`); return; }
  }
  renderHome();
}
const topbar = inner => `<header class="topbar"><span class="crumb-icon">${icon("screener")}</span><span class="crumb-sep">${icon("chevR")}</span>${inner}</header>`;
const shell = body => `${topbar(`<b class="crumb-title">Screener</b><button class="scr-btn dark scr-right" data-new>${icon("plus")}Create New</button>`)}<div class="scr-scroll"><div class="scr-page">${body}</div></div>`;

function visibleCards() {
  const q = ST.q.trim().toLowerCase();
  let items = ST.tab === "curated" ? ST.curated.items : ST.minelist.items;
  if (ST.tab === "curated" && ST.cat !== "all") items = items.filter(i => i.category === ST.cat);
  if (ST.tab === "mine" && ST.mine !== "all") items = items.filter(i => (ST.mine === "custom") === (i.source === "custom"));
  return q ? items.filter(i => (i.name + " " + i.description).toLowerCase().includes(q)) : items;
}
function cardHTML(i) {
  const p = i.preview, mine = !i.curated;
  const tag = i.curated ? i.category : ({ custom: "Custom", chat: "From chat", curated: "Strategy based" }[i.source] || "Custom");
  const stack = (p?.symbols || []).map(s => `<i style="background:${A().colorOf(s)}">${esc(s.slice(0, 2))}</i>`).join("");
  return `<article class="scr-card" data-open="${esc(i.id)}" tabindex="0">
    <div class="scr-card-top"><span class="scr-av">${esc(initial(i.name))}</span><span class="sp"></span>
      <button class="scr-ic" data-bell title="Alerts arrive in Phase 2">${icon("bell")}</button>${mine ? `<button class="scr-ic" data-menu="${esc(i.id)}" title="More">${icon("more")}</button>` : ""}</div>
    <h3>${esc(i.name)}</h3><p>${esc(i.description || "")}</p><div class="scr-tags"><span>${esc(tag)}</span></div>
    <div class="scr-card-foot"><span class="scr-stack">${stack}</span><span>${p ? plural(p.total, "stock") : "—"}</span><span class="sp"></span>${mine ? `<span class="muted">Updated ${esc(shortDate(i.updated_at))}</span>` : ""}</div></article>`;
}
function cardsInner() {
  const items = visibleCards();
  if (items.length) return items.map(cardHTML).join("");
  if (ST.tab === "mine" && !ST.minelist.items.length)
    return `<div class="scr-empty"><h3>No screeners yet</h3><p>Create one from scratch, save a curated screener, or open a screener result from chat.</p><button class="scr-btn dark" data-new>${icon("plus")}Create New</button></div>`;
  return `<div class="scr-empty"><p>No screeners match.</p></div>`;
}
function renderHome() {
  const cur = ST.curated, mine = ST.minelist, app = A().state.me?.app_name || "Stock Research";
  const pills = ST.tab === "curated" ? [["all", "All Screeners"], ...cur.categories.map(c => [c, c])] : [["all", "All Screeners"], ["strategy", "Strategy based"], ["custom", "Custom"]];
  const sel = ST.tab === "curated" ? ST.cat : ST.mine;
  root().innerHTML = shell(`<h1 class="scr-h">Stocks Screener</h1><p class="scr-sub">A collection of curated screeners for every investing style.</p>
    <div class="scr-tabs"><button data-tab="curated" class="${ST.tab === "curated" ? "on" : ""}">Curated by ${esc(app)}<span>${cur.items.length}</span></button><button data-tab="mine" class="${ST.tab === "mine" ? "on" : ""}">Created by you<span>${mine.items.length}</span></button></div>
    <div class="scr-filters"><div class="scr-pills">${pills.map(([k, l]) => `<button data-pill="${esc(k)}" class="${k === sel ? "on" : ""}">${esc(l)}</button>`).join("")}</div>
      <label class="scr-search">${icon("search")}<input id="scrQ" placeholder="Search screeners" value="${esc(ST.q)}" autocomplete="off"></label></div>
    <div class="scr-cards" id="scrCards">${cardsInner()}</div>
    <p class="scr-disclaimer">Screens are filters on market data, not recommendations. Prices are live or end of day as labelled. Research only, not investment advice.</p>`);
}

/* ------------------------------------------------------------------ detail */
const freshDef = () => ({ columns: [...(M?.default_columns || ["Sector", "Day Change %", "Turnover (Cr)", "Volume", "Market Cap (Cr)"])], filters: [],
  sort: { metric: "Turnover (Cr)", dir: "desc" }, universe: { include_sme: true } });
const clone = o => JSON.parse(JSON.stringify(o));

async function ensureMetrics() {
  if (M && Date.now() - metricsAt < 300000) return;
  M = await api("/screeners/metrics"); metricsAt = Date.now();
}
async function openDetail(id) {
  const t = ++tok;
  root().innerHTML = `${topbar(`<a class="crumb-link" href="#/screener">Screener</a>`)}<div class="scr-scroll"><div class="scr-page"><div class="scr-state">Loading screener…</div></div></div>`;
  try {
    if (id === "new") {
      await ensureMetrics(); if (t !== tok) return;
      V = { id: null, curated: false, name: "Untitled screener", desc: "", def: freshDef(), res: null, offset: 0, panel: false, wide: false, open: new Set([M.groups[0].name]), rq: "", loading: true };
      render(); await reload(); return;
    }
    const [d] = await Promise.all([api(`/screeners/${encodeURIComponent(id)}?limit=${PAGE}`), ensureMetrics()]);
    if (t !== tok) return;
    const s = d.screener;
    V = { id: s.id, curated: !!s.curated, name: s.name, desc: s.description || "", source: s.source, def: clone(d.results.definition), res: d.results, offset: 0, panel: false, wide: false,
      open: new Set([M.groups[0].name]), rq: "", loading: false };
    render(); scheduleLive();
  } catch (e) { if (t === tok) root().innerHTML = `${topbar(`<a class="crumb-link" href="#/screener">Screener</a>`)}<div class="scr-scroll"><div class="scr-page"><div class="scr-state err">${esc(e.message)} <a href="#/screener">Back to screeners</a></div></div></div>`; }
}
async function reload(quiet = false) {
  const t = ++tok; V.loading = true; V.err = null; if (!quiet) tableDim(true); V.keepY = quiet ? ($("#scrTable")?.scrollTop || 0) : 0;
  try {
    const res = await api("/screeners/run", { method: "POST", body: { definition: V.def, limit: PAGE, offset: V.offset } });
    if (t !== tok) return;
    V.res = res; V.def = clone(res.definition);
  } catch (e) { if (t !== tok) return; V.err = e.message; }
  V.loading = false; renderBody(); scheduleLive();
}
// While live prices are still loading the page shows end-of-day numbers; ask again until they are in. Once live, refresh quietly.
function scheduleLive() {
  clearTimeout(V.liveT);
  const r = V.res; if (!r || V.err) return;
  const warming = r.price_mode === "eod" && Array.isArray(r.live_progress);
  V.tries = warming ? (V.tries || 0) + 1 : 0;
  if (warming && V.tries <= 12) V.liveT = setTimeout(() => { if (!V.stopped) reload(); }, 12000);
  else if (r.price_mode === "live") V.liveT = setTimeout(() => { if (!V.stopped && !document.hidden) reload(true); }, 60000);
}
const owned = () => V.id && !V.curated;
const tableDim = on => $(".scr-grid", root())?.classList.toggle("dim", on);

function edit(mutator) {   // every change to the definition goes through here
  mutator(V.def); V.offset = 0; V.dirty = true;
  if (owned()) { clearTimeout(saveT); saveT = setTimeout(flushSave, 700); }
  reload(); if (V.panel) renderPanelList();
}
async function flushSave() {
  clearTimeout(saveT); saveT = null;
  if (!V || !owned() || !V.dirty) return;
  const { id, def } = V; V.dirty = false;
  try { await api(`/screeners/${id}`, { method: "PATCH", body: { definition: def } }); ST.minelist = null; }
  catch (e) { toast("Could not save changes: " + e.message); }
}

function render() {
  const crumbMid = V.curated ? "Curated" : "My Screeners";
  const saveBtn = owned() ? `<button class="scr-btn dark" disabled title="Changes save automatically">${icon("bookmark", "fill")}Saved</button>` : `<button class="scr-btn dark" data-save>${icon("bookmark")}Save</button>`;
  root().innerHTML = `${topbar(`<a class="crumb-link" href="#/screener">Screener</a><span class="crumb-sep">${icon("chevR")}</span><a class="crumb-link" href="#/screener" data-crumb="${V.curated ? "curated" : "mine"}">${crumbMid}</a><span class="crumb-sep">${icon("chevR")}</span><b class="crumb-title">${esc(V.name)}</b>
      <span class="scr-right scr-top-actions"><button class="scr-btn" disabled title="Alerts arrive in Phase 2">${icon("bell")}Set Alert</button><button class="scr-btn alt" data-share>${icon("send")}Share</button>${saveBtn}<button class="scr-ic" data-pagemenu title="More">${icon("more")}</button></span>`)}
    <div class="scr-body"><div class="scr-scroll"><div class="scr-detail ${V.wide ? "wide" : ""}" id="scrDetail"></div></div><aside class="scr-refine" id="scrRefine" ${V.panel ? "" : "hidden"}></aside></div>`;
  renderBody(); if (V.panel) renderPanel();
}
function chipsHTML() {
  return V.def.filters.map((f, i) => `<span class="scr-chip" data-editf="${i}" title="Edit filter">${esc(label(f.metric))} ${esc(f.op)} ${esc(typeof f.value === "number" ? +f.value.toFixed(4) : f.value)}<button data-delf="${i}" title="Remove filter">${icon("x")}</button></span>`).join("");
}
function gridHTML() {
  const r = V.res;
  if (V.err) return `<div class="scr-state err">${esc(V.err)} <button data-retry>Retry</button></div>`;
  if (!r) return `<div class="scr-state">Running screener…</div>`;
  const s = V.def.sort;
  const th = c => `<th data-sort="${esc(c.key)}" class="${c.kind === "text" ? "" : "num"}"><span class="ar">${s.metric === c.key ? (s.dir === "asc" ? "▲" : "▼") : "⇅"}</span>${esc(c.label)}</th>`;
  const rows = r.rows.map(x => `<tr>${nameCell(x)}${r.columns.slice(1).map(c => cell(c, x)).join("")}</tr>`).join("")
    || `<tr><td colspan="${r.columns.length}" class="scr-none">No stocks match these filters.</td></tr>`;
  return `<table><thead><tr>${r.columns.map(th).join("")}</tr></thead><tbody>${rows}</tbody></table>`;
}
function footHTML() {
  const r = V.res; if (!r) return "";
  const from = r.total ? r.offset + 1 : 0, to = r.offset + r.rows.length;
  return `<span>Showing ${from} - ${to} of ${Number(r.total).toLocaleString("en-IN")} results</span><span class="muted">· ${r.price_mode === "live" ? `<span class="scr-live"><i></i>Live</span> ` : ""}As of ${esc(r.as_of)}</span>
    ${r.price_mode === "live" ? `<button class="scr-ic sm" data-refreshpx title="Refresh prices">${icon("refresh")}</button>` : ""}
    <span class="scr-pager"><button data-page="-1" ${r.offset <= 0 ? "disabled" : ""} title="Previous page">${icon("chevL")}</button><button data-page="1" ${to >= r.total ? "disabled" : ""} title="Next page">${icon("chevR")}</button></span>`;
}
function renderBody() {
  const el = $("#scrDetail"); if (!el) return;
  const r = V.res, keepX = $("#scrTable")?.scrollLeft || 0;     // sorting/paging should not snap a wide table back to the left
  el.innerHTML = `<h1 class="scr-h" id="scrTitle">${esc(V.name)}</h1>${V.desc ? `<p class="scr-sub">${esc(V.desc)}</p>` : ""}
    <div class="scr-grid ${V.loading ? "dim" : ""}"><div class="scr-gbar"><button class="scr-ic" data-addf title="Add filter">${icon("filter")}</button>${chipsHTML()}<button class="scr-chip add" data-addf>${icon("plus")}Filter</button><span class="sp"></span><button class="scr-ic" data-wide title="${V.wide ? "Collapse" : "Expand"}">${icon("expand")}</button></div>
      <div class="scr-table" id="scrTable">${gridHTML()}</div>
      <div class="scr-gfoot"><div class="scr-gtext" id="scrFoot">${footHTML()}</div><button class="scr-btn dark" data-refine>${icon("sliders")}Refine Screener</button></div></div>
    ${(r?.notes || []).map(n => `<div class="cnote">⚠ ${esc(n)}</div>`).join("")}
    ${r?.sources?.length ? `<div class="sources">${r.sources.map(s => `<span class="src">${esc(s.source)}${s.as_of ? " · " + esc(s.as_of) : ""}</span>`).join("")}</div>` : ""}
    <p class="scr-disclaimer">Screens are filters on market data, not recommendations. Prices are live or end of day as labelled. Research only, not investment advice.</p>`;
  const tbl = $("#scrTable"); if (tbl) { tbl.scrollLeft = keepX; if (V.keepY) tbl.scrollTop = V.keepY; V.keepY = 0; }
}

/* ------------------------------------------------------------ refine panel */
function renderPanel() {
  $("#scrRefine").innerHTML = `<div class="rf-h"><b>Refine Screener</b><button class="scr-ic" data-rfclose title="Close">${icon("x")}</button></div>
    <label class="rf-search">${icon("search")}<input id="rfQ" placeholder="Search for a metric" value="${esc(V.rq)}" autocomplete="off"></label>
    <div class="rf-list" id="rfList"></div>
    <label class="rf-foot"><input type="checkbox" id="rfSme" ${V.def.universe.include_sme ? "checked" : ""}> Include SME stocks <small>${M.universe.toLocaleString("en-IN")} stocks in the universe</small></label>`;
  renderPanelList();
}
function renderPanelList() {
  const list = $("#rfList"); if (!list) return;
  const top = list.scrollTop, q = V.rq.trim().toLowerCase(), have = new Set([...V.def.columns, "Company Name"]);
  const filtered = new Set(V.def.filters.map(f => f.metric));
  const html = M.groups.map(g => {
    const items = M.metrics.filter(m => m.group === g.name && (!q || (m.label + " " + m.key + " " + m.desc).toLowerCase().includes(q)));
    if (!items.length) return "";
    const open = q || V.open.has(g.name);
    return `<section class="rf-group ${open ? "open" : ""}"><button class="rf-gh" data-grp="${esc(g.name)}"><span><b>${esc(g.name)}</b><small>${esc(g.description)}</small></span>${icon("chevD", "chev")}</button>
      ${open ? `<div class="rf-items">${items.map(m => {
        const on = have.has(m.key), locked = m.key === "Company Name", none = m.coverage === 0 && !on;
        const part = m.coverage > 0 && m.coverage < M.universe;
        return `<div class="rf-item" title="${esc(m.desc)}"><span class="rf-name">${esc(m.label)}${part ? `<em>${m.coverage.toLocaleString("en-IN")} stocks</em>` : ""}${m.coverage === 0 ? `<em>no data yet</em>` : ""}</span>
          ${locked ? "" : `<button class="scr-ic sm ${filtered.has(m.key) ? "on" : ""}" data-flt="${esc(m.key)}" title="Filter by ${esc(m.label)}">${icon("filter")}</button>`}
          <button class="rf-add ${on ? "on" : ""}" data-col="${esc(m.key)}" ${locked || none ? "disabled" : ""} title="${locked ? "Always shown" : on ? "Click to remove" : none ? "No data loaded" : "Add column"}">${on ? icon("check") + "Added" : icon("plus") + "Add"}</button></div>`;
      }).join("")}</div>` : ""}</section>`;
  }).join("") || `<div class="scr-state">No metrics match "${esc(V.rq)}".</div>`;
  list.innerHTML = html; list.scrollTop = top;
}

/* ----------------------------------------------------------- filter editor */
function filterDialog(idx, preset) {
  const cur = idx != null ? V.def.filters[idx] : null;
  const startKey = cur?.metric || preset || "Day Change %";
  const filterable = M.metrics.filter(m => m.key !== "Company Name");
  A().openModal(`<div class="dlg narrow"><div class="dlg-h">${cur ? "Edit filter" : "Add filter"}<button class="icon-btn" data-close>${icon("x")}</button></div>
    <form class="fe" id="feForm" onsubmit="return false">
      <label>Metric<select id="feM">${M.groups.map(g => `<optgroup label="${esc(g.name)}">${filterable.filter(m => m.group === g.name).map(m => `<option value="${esc(m.key)}" ${m.key === startKey ? "selected" : ""}>${esc(m.label)}</option>`).join("")}</optgroup>`).join("")}</select></label>
      <div class="fe-row"><label>Condition<select id="feOp"></select></label><label>Value<input id="feV" autocomplete="off"></label></div>
      <p class="fe-hint" id="feHint"></p><p class="fe-err" id="feErr" hidden></p>
      <div class="fe-actions">${cur ? `<button type="button" class="scr-btn danger" id="feDel">Remove</button>` : ""}<span class="sp"></span><button type="button" class="scr-btn" data-close>Cancel</button><button type="submit" class="scr-btn dark" id="feOk">${cur ? "Update" : "Apply"}</button></div></form></div>`);
  const m = $("#feM"), op = $("#feOp"), v = $("#feV"), hint = $("#feHint"), err = $("#feErr");
  const sync = (keepOp) => {
    const mt = metric(m.value), text = mt.kind === "text";
    op.innerHTML = mt.ops.map(o => `<option value="${esc(o)}">${esc(o === "contains" ? "contains" : o)}</option>`).join("");
    op.value = keepOp && mt.ops.includes(keepOp) ? keepOp : (text ? "contains" : ">");
    v.type = text ? "text" : "number"; v.step = "any"; v.placeholder = text ? "e.g. Capital Goods" : "e.g. 3";
    hint.textContent = `${mt.desc} Available for ${mt.coverage.toLocaleString("en-IN")} of ${M.universe.toLocaleString("en-IN")} stocks.` + (mt.coverage < M.universe ? " Stocks without a value are left out." : "");
  };
  sync(cur?.op); if (cur) v.value = cur.value;
  m.onchange = () => { sync(); v.value = ""; };
  const apply = () => {
    const mt = metric(m.value), raw = v.value.trim();
    if (!raw || (mt.kind !== "text" && !isFinite(Number(raw)))) { err.textContent = mt.kind === "text" ? "Enter a value." : "Enter a number."; err.hidden = false; return; }
    const f = { metric: m.value, op: op.value, value: mt.kind === "text" ? raw : Number(raw) };
    A().closeModal(); edit(d => { if (cur) d.filters[idx] = f; else d.filters.push(f); });
  };
  $("#feForm").onsubmit = e => { e.preventDefault(); apply(); };
  $("#feDel")?.addEventListener("click", () => { A().closeModal(); edit(d => d.filters.splice(idx, 1)); });
  setTimeout(() => v.focus(), 30);
}

/* ------------------------------------------------------------------- menus */
let menuEl = null;
function closeMenu() { menuEl?.remove(); menuEl = null; }
function openMenu(btn, items) {
  closeMenu();
  menuEl = document.createElement("div"); menuEl.className = "scr-menu";
  menuEl.innerHTML = items.map(([k, l, danger]) => `<button data-mi="${k}" class="${danger ? "danger" : ""}">${esc(l)}</button>`).join("");
  document.body.appendChild(menuEl);
  const r = btn.getBoundingClientRect();
  menuEl.style.top = r.bottom + 6 + "px"; menuEl.style.left = Math.max(8, Math.min(r.right - 190, innerWidth - 198)) + "px";
  return menuEl;
}
async function savedAction(id, act) {
  const item = ST.minelist.items.find(i => i.id === id);
  try {
    if (act === "rename") { const n = prompt("Rename screener", item?.name || ""); if (n && n.trim()) await api(`/screeners/${id}`, { method: "PATCH", body: { name: n.trim() } }); else return; }
    if (act === "duplicate") { await api(`/screeners/${id}/duplicate`, { method: "POST" }); toast("Duplicated"); }
    if (act === "delete") { if (!confirm("Delete this screener?")) return; await api(`/screeners/${id}`, { method: "DELETE" }); toast("Deleted"); }
    ST.minelist = await api("/screeners/mine"); renderHome();
  } catch (e) { toast(e.message); }
}
function pageMenu(btn) {
  const items = [["watch", "Add these stocks to watchlist"]];
  if (owned()) items.unshift(["rename", "Rename"], ["desc", "Edit description"]);
  if (V.id) items.push(["dup", "Save a copy"]);
  if (owned()) items.push(["del", "Delete screener", true]);
  const m = openMenu(btn, items);
  m.onclick = async e => {
    const k = e.target.dataset.mi; if (!k) return; closeMenu();
    try {
      if (k === "watch") { const syms = (V.res?.rows || []).map(r => r.symbol); if (!syms.length) return toast("No stocks to add"); await api("/watchlist", { method: "POST", body: syms }); toast(`Added ${plural(syms.length, "stock")} to your watchlist`); }
      if (k === "rename") { const n = prompt("Rename screener", V.name); if (n && n.trim()) { await api(`/screeners/${V.id}`, { method: "PATCH", body: { name: n.trim() } }); V.name = n.trim(); ST.minelist = null; render(); } }
      if (k === "desc") { const n = prompt("Description", V.desc); if (n !== null) { await api(`/screeners/${V.id}`, { method: "PATCH", body: { description: n.trim() } }); V.desc = n.trim(); ST.minelist = null; renderBody(); } }
      if (k === "dup") { await flushSave(); const c = await api(`/screeners/${V.id}/duplicate`, { method: "POST" }); toast("Saved a copy to Created by you"); ST.minelist = null; location.hash = "#/screener/" + c.id; }
      if (k === "del" && confirm("Delete this screener?")) { await api(`/screeners/${V.id}`, { method: "DELETE" }); ST.minelist = null; ST.tab = "mine"; toast("Deleted"); location.hash = "#/screener"; }
    } catch (err) { toast(err.message); }
  };
}
async function saveCurrent() {
  try {
    const body = { name: V.name, description: V.desc, definition: V.def, source: V.curated ? "curated" : "custom" };
    const s = await api("/screeners", { method: "POST", body });
    ST.minelist = null; toast("Saved to Created by you"); location.replace("#/screener/" + s.id);
  } catch (e) { toast(e.message); }
}

/* ------------------------------------------------------------------ events */
function onClick(e) {
  const t = e.target;
  if (!t.closest(".scr-menu")) closeMenu();
  if (t.closest("[data-new]")) { location.hash = "#/screener/new"; return; }
  if (t.closest("[data-retry-home]")) { loadHome(true); return; }
  if (t.closest("[data-retry]")) { reload(); return; }
  const crumb = t.closest("[data-crumb]"); if (crumb) { ST.tab = crumb.dataset.crumb; ST.cat = "all"; ST.mine = "all"; ST.q = ""; ST.minelist = null; return; }
  const tab = t.closest("[data-tab]"); if (tab) { ST.tab = tab.dataset.tab; ST.q = ""; renderHome(); return; }
  const pill = t.closest("[data-pill]"); if (pill) { if (ST.tab === "curated") ST.cat = pill.dataset.pill; else ST.mine = pill.dataset.pill; renderHome(); return; }
  if (t.closest("[data-bell]")) { e.stopPropagation(); toast("Alerts arrive in Phase 2"); return; }
  const menu = t.closest("[data-menu]");
  if (menu) {
    e.stopPropagation(); const id = menu.dataset.menu;
    openMenu(menu, [["rename", "Rename"], ["duplicate", "Duplicate"], ["delete", "Delete", true]]).onclick = ev => { const k = ev.target.dataset.mi; if (k) { closeMenu(); savedAction(id, k); } };
    return;
  }
  const card = t.closest("[data-open]"); if (card) { location.hash = "#/screener/" + card.dataset.open; return; }
  if (!V) return;
  if (t.closest("[data-share]")) {
    if (!V.id) { toast("Save the screener first to get a link"); return; }
    A().copyText(`${location.origin}/#/screener/${V.id}`).then(() => toast("Screener link copied")); return;
  }
  if (t.closest("[data-save]")) { saveCurrent(); return; }
  if (t.closest("[data-pagemenu]")) { e.stopPropagation(); pageMenu(t.closest("[data-pagemenu]")); return; }
  const del = t.closest("[data-delf]"); if (del) { e.stopPropagation(); edit(d => d.filters.splice(+del.dataset.delf, 1)); return; }
  const ed = t.closest("[data-editf]"); if (ed) { filterDialog(+ed.dataset.editf); return; }
  if (t.closest("[data-addf]")) { filterDialog(null); return; }
  const flt = t.closest("[data-flt]"); if (flt) { const i = V.def.filters.findIndex(f => f.metric === flt.dataset.flt); filterDialog(i >= 0 ? i : null, flt.dataset.flt); return; }
  if (t.closest("[data-refreshpx]")) { reload(); return; }
  if (t.closest("[data-wide]")) { V.wide = !V.wide; $("#scrDetail").classList.toggle("wide", V.wide); return; }
  if (t.closest("[data-refine]")) { V.panel = !V.panel; $("#scrRefine").hidden = !V.panel; if (V.panel) renderPanel(); return; }
  if (t.closest("[data-rfclose]")) { V.panel = false; $("#scrRefine").hidden = true; return; }
  const grp = t.closest("[data-grp]"); if (grp) { const g = grp.dataset.grp; V.open.has(g) ? V.open.delete(g) : V.open.add(g); renderPanelList(); return; }
  const col = t.closest("[data-col]");
  if (col && !col.disabled) { const k = col.dataset.col; edit(d => { const i = d.columns.indexOf(k); i >= 0 ? d.columns.splice(i, 1) : d.columns.push(k); }); return; }
  const sort = t.closest("th[data-sort]");
  if (sort) { const k = sort.dataset.sort; edit(d => { const same = d.sort.metric === k; d.sort = { metric: k, dir: same ? (d.sort.dir === "asc" ? "desc" : "asc") : (metric(k)?.kind === "text" ? "asc" : "desc") }; }); return; }
  const pg = t.closest("[data-page]");
  if (pg && V.res) { V.offset = Math.max(0, V.offset + (+pg.dataset.page) * PAGE); reload(); $(".scr-scroll", root())?.scrollTo?.({ top: 0 }); return; }
}
function onInput(e) {
  if (e.target.id === "scrQ") { ST.q = e.target.value; const c = $("#scrCards"); if (c) c.innerHTML = cardsInner(); }
  if (e.target.id === "rfQ" && V) { V.rq = e.target.value; renderPanelList(); }
}
function onChange(e) {
  if (e.target.id === "rfSme" && V) edit(d => { d.universe.include_sme = e.target.checked; });
}
let wired = false;
function wire() {
  if (wired) return; wired = true;
  const r = root(); r.addEventListener("click", onClick); r.addEventListener("input", onInput); r.addEventListener("change", onChange);
  r.addEventListener("keydown", e => { if (e.key === "Enter" && e.target.matches?.(".scr-card")) location.hash = "#/screener/" + e.target.dataset.open; });
  document.addEventListener("click", e => { if (menuEl && !menuEl.contains(e.target) && !e.target.closest("[data-menu],[data-pagemenu]")) closeMenu(); });
}

/* --------------------------------------------------------------- public API */
(window.Pages = window.Pages || {}).screener = {
  show(id) { wire(); closeMenu(); flushSave(); if (!id) { V = null; loadHome(!ST.minelist); } else openDetail(id); },
  hide() { closeMenu(); flushSave(); tok++; if (V) { V.stopped = true; clearTimeout(V.liveT); } },
};
})();