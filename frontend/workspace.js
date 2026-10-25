/* Watchlist, Event calendar and Alerts pages (+ the unread badge in the sidebar).
   Registers into window.Pages; uses the helpers app.js exposes on window.App. The server owns all data. */
(() => {
"use strict";
const A = () => window.App;
const $ = (s, r = document) => r.querySelector(s);
const esc = s => A().esc(s);
const icon = (n, c) => A().icon(n, c);
const toast = m => A().toast(m);
const api = (p, o) => A().api(p, o);
const Pages = (window.Pages = window.Pages || {});

/* ------------------------------------------------------------------ helpers */
const n2 = v => Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const sgn = v => (v > 0 ? "+" : "") + n2(v);
const cls = v => (v > 0 ? "pos" : v < 0 ? "neg" : "");
const safeUrl = u => (/^https?:\/\//i.test(u || "") ? u : "");
const av = sym => `<i class="ws-av" style="background:${A().colorOf(sym)}">${esc(sym.slice(0, 2))}</i>`;
const parseDay = iso => new Date(String(iso).slice(0, 10) + "T00:00:00");
const dayLong = iso => parseDay(iso).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
const dayShort = iso => parseDay(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
function relDay(iso, asOf) {
  const d = Math.round((parseDay(iso) - parseDay(asOf)) / 864e5);
  return d === 0 ? "Today" : d === 1 ? "Tomorrow" : `In ${d} days`;
}
function ago(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!isFinite(s)) return "";
  if (s < 90) return "just now";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}
const topbar = (ic, title, right = "") =>
  `<header class="topbar"><span class="crumb-icon">${icon(ic)}</span><span class="crumb-sep">${icon("chevR")}</span><b class="crumb-title">${esc(title)}</b>${right ? `<span class="ws-right">${right}</span>` : ""}</header>`;
const frame = (ic, title, right, body) =>
  `${topbar(ic, title, right)}<div class="ws-scroll"><div class="ws-page">${body}</div></div>`;
const loading = what => `<div class="ws-state">Loading ${what}…</div>`;
const failed = (msg, retry) => `<div class="ws-state err">${esc(msg)} <button data-retry="${retry}">Retry</button></div>`;
const DISCLAIMER = `<p class="ws-disclaimer">Research only, not investment advice.</p>`;
const $root = id => document.getElementById(id);

/* ------------------------------------------------------------- unread badge */
async function refreshBadge() {
  try {
    const { unread } = await api("/notifications/unread-count");
    const b = $("#alertBadge");
    if (b) { b.textContent = unread > 99 ? "99+" : unread; b.hidden = !unread; }
  } catch { /* the badge is a convenience; stay quiet if the API is unreachable */ }
}
document.addEventListener("DOMContentLoaded", () => { refreshBadge(); setInterval(refreshBadge, 60000); });

/* ------------------------------------------------------------ symbol picker */
function symbolPicker(input, list, onPick) {
  let t = 0;
  const close = () => { list.hidden = true; list.innerHTML = ""; };
  input.addEventListener("input", () => {
    clearTimeout(t);
    const q = input.value.trim();
    if (!q) return close();
    t = setTimeout(async () => {
      try {
        const hits = await api("/symbols/search?q=" + encodeURIComponent(q));
        list.innerHTML = hits.map(h => `<button type="button" data-sym="${esc(h.symbol)}">${av(h.symbol)}<span><b>${esc(h.symbol)}</b><small>${esc(h.name)}</small></span></button>`).join("")
          || `<div class="ws-none">No NSE stock matches "${esc(q)}".</div>`;
        list.hidden = false;
      } catch { close(); }
    }, 180);
  });
  list.addEventListener("click", e => { const b = e.target.closest("[data-sym]"); if (b) { close(); onPick(b.dataset.sym); } });
  input.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); const f = list.querySelector("[data-sym]"); if (f) { close(); onPick(f.dataset.sym); } } });
  document.addEventListener("click", e => { if (!list.contains(e.target) && e.target !== input) close(); });
}

/* =============================================================== WATCHLIST */
const W = { data: null, err: null, timer: null, visible: false };
async function loadWatchlist(silent = false) {
  const el = $root("watchlist");
  if (!W.data && !silent) el.innerHTML = frame("star", "Watchlist", "", loading("watchlist"));
  try { W.data = await api("/watchlist/overview"); W.err = null; } catch (e) { if (!silent) W.err = e.message; }
  clearTimeout(W.timer);
  if (silent && $("#wlBody", el)) renderWatchlistBody(); else renderWatchlist();     // a quiet refresh must not disturb the search box
  if (W.visible && W.data?.refresh_sec) W.timer = setTimeout(() => loadWatchlist(true), W.data.refresh_sec * 1000);
}
const MODE = { live: ["live", "Live"], mixed: ["mixed", "Partly live"], eod: ["eod", "End of day"] };
function modeBadge(d) { const [c, t] = MODE[d.mode] || MODE.eod; return `<span class="ws-mode ${c}" title="${esc(d.source)}"><i></i>${t}</span>`; }
function watchlistBody(d) {
  const eodTag = r => d.mode !== "eod" && r.price_source === "eod" ? `<span class="ws-eod" title="Live price not available; showing the last close">EOD</span>` : "";
  const rows = d.rows.map(r => r.missing
    ? `<tr><td><div class="ws-co">${av(r.symbol)}<span>${esc(r.symbol)}<small>No stored prices for this symbol</small></span></div></td><td class="num" colspan="4">—</td>${rowActions(r.symbol)}</tr>`
    : `<tr><td><div class="ws-co">${av(r.symbol)}<span><a class="co-link" href="#/company/${encodeURIComponent(r.symbol)}">${esc(r.name || r.symbol)}</a><small>${esc(r.symbol)}${r.Sector ? " · " + esc(r.Sector) : ""}</small></span></div></td>
        <td class="num"><b>${r.LTP != null ? n2(r.LTP) : "—"}</b> ${eodTag(r)}</td>
        <td class="num ${cls(r["Day Change %"])}">${r["Day Change %"] != null ? sgn(r["Day Change %"]) + "%" : "—"}</td>
        <td class="num ${cls(r["1M Return %"])}">${r["1M Return %"] != null ? sgn(r["1M Return %"]) + "%" : "—"}</td>
        <td class="num ${cls(r["% From 52W High"])}">${r["% From 52W High"] != null ? sgn(r["% From 52W High"]) + "%" : "—"}</td>${rowActions(r.symbol)}</tr>`).join("");
  return d.rows.length ? `<div class="ws-table"><table><thead><tr><th>Company</th><th class="num">${d.mode === "eod" ? "Last close" : "Price"}</th><th class="num">Day</th><th class="num">1M</th><th class="num">vs 52W high</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="ws-foot">${modeBadge(d)} As of ${esc(d.as_of)} · ${esc(d.source)}. Returns are not adjusted for splits or bonuses.</p>
      ${(d.notes || []).map(n => `<div class="cnote">⚠ ${esc(n)}</div>`).join("")}`
    : `<div class="ws-empty"><h3>Your watchlist is empty</h3><p>Add the stocks you follow. The calendar and your alerts can then cover all of them at once.</p>
        <div class="ws-chips">${["RELIANCE", "TCS", "INFY", "HDFCBANK", "ITC"].map(s => `<button data-add="${s}">${icon("plus")}${s}</button>`).join("")}</div></div>`;
}
function renderWatchlistBody() { const b = $("#wlBody", $root("watchlist")); if (b && W.data) b.innerHTML = watchlistBody(W.data); }
function renderWatchlist() {
  const el = $root("watchlist"), d = W.data;
  if (!d) { el.innerHTML = frame("star", "Watchlist", "", failed(W.err || "Could not load the watchlist.", "watchlist")); return; }
  el.innerHTML = frame("star", "Watchlist", `<button class="ws-btn" data-go-page="#/calendar">${icon("calendar")}Calendar</button><button class="ws-btn dark" data-newalert="">${icon("bell")}New alert</button>`,
    `<h1 class="ws-h">Watchlist</h1><p class="ws-sub">Stocks you follow. Your calendar and "whole watchlist" alerts use this list.</p>
     <div class="ws-add"><label class="ws-search">${icon("search")}<input id="wlQ" placeholder="Add a stock: search by symbol or name" autocomplete="off"></label><div class="ws-pick" id="wlPick" hidden></div></div><div id="wlBody">${watchlistBody(d)}</div>${DISCLAIMER}`);
  symbolPicker($("#wlQ", el), $("#wlPick", el), sym => addToWatchlist(sym));
}
const rowActions = sym => `<td class="ws-act"><button class="ws-ic" data-newalert="${esc(sym)}" title="Create an alert for ${esc(sym)}">${icon("bell")}</button><button class="ws-ic" data-ask="Show me the latest results and upcoming events for ${esc(sym)}" title="Ask about ${esc(sym)}">${icon("chat")}</button><button class="ws-ic" data-rm="${esc(sym)}" title="Remove from watchlist">${icon("x")}</button></td>`;
async function addToWatchlist(sym) {
  try { await api("/watchlist/" + encodeURIComponent(sym), { method: "POST" }); toast(`${sym} added to your watchlist`); await loadWatchlist(); }
  catch (e) { toast(e.message); }
}
Pages.watchlist = { show() { W.visible = true; loadWatchlist(); }, hide() { W.visible = false; clearTimeout(W.timer); } };

/* ================================================================= CALENDAR */
const C = { days: 60, type: "all", data: null, err: null };
async function loadCalendar() {
  const el = $root("calendar");
  el.innerHTML = frame("calendar", "Calendar", "", loading("upcoming events"));
  try { C.data = await api("/calendar?days=" + C.days); C.err = null; } catch (e) { C.err = e.message; C.data = null; }
  renderCalendar();
}
function renderCalendar() {
  const el = $root("calendar"), d = C.data;
  if (!d) { el.innerHTML = frame("calendar", "Calendar", "", failed(C.err || "Could not load the calendar.", "calendar")); return; }
  const want = { all: () => true, results: e => e.type === "results", dividends: e => e.type.startsWith("dividend") }[C.type];
  const events = d.events.filter(want);
  const groups = [];
  for (const e of events) { const g = groups[groups.length - 1]; if (g && g.date === e.date) g.items.push(e); else groups.push({ date: e.date, items: [e] }); }
  const pills = (key, opts) => `<div class="ws-pills">${opts.map(([v, l]) => `<button data-${key}="${v}" class="${String(C[key]) === String(v) ? "on" : ""}">${l}</button>`).join("")}</div>`;
  let body = `<h1 class="ws-h">Event calendar</h1><p class="ws-sub">Upcoming results and dividend dates for the stocks on your watchlist, from ${esc(dayShort(d.from))} to ${esc(dayShort(d.to))}.</p>
    <div class="ws-controls">${pills("type", [["all", "All"], ["results", "Results"], ["dividends", "Dividends"]])}${pills("days", [[30, "30 days"], [60, "60 days"], [90, "90 days"]])}</div>`;
  if (!d.symbols.length) {
    body += `<div class="ws-empty"><h3>Your watchlist is empty</h3><p>The calendar shows dates for the stocks you follow.</p><button class="ws-btn dark" data-go-page="#/watchlist">${icon("star")}Add stocks</button></div>`;
  } else if (!groups.length) {
    body += `<div class="ws-empty"><h3>No ${C.type === "all" ? "" : C.type + " "}dates in this window</h3><p>${d.events.length ? "Try another filter." : "None of your " + d.symbols.length + " stocks has an upcoming date in our data. See the list below."}</p></div>`;
  } else {
    body += groups.map(g => `<section class="ws-day"><h3>${esc(dayLong(g.date))}<span>${esc(relDay(g.date, d.as_of))}</span></h3>${g.items.map(eventRow).join("")}</section>`).join("");
  }
  if (d.unavailable.length) body += `<details class="ws-unavail"><summary>${d.unavailable.length} of ${d.symbols.length} stocks have no date in our data</summary><ul>${d.unavailable.map(u => `<li><b>${esc(u.symbol)}</b> ${esc(u.reason)}</li>`).join("")}</ul></details>`;
  body += (d.notes || []).map(n => `<div class="cnote">⚠ ${esc(n)}</div>`).join("");
  if (d.sources.length) body += `<div class="sources">${d.sources.map(s => `<span class="src">${esc(s.source)}${s.as_of ? " · " + esc(s.as_of) : ""}</span>`).join("")}</div>`;
  el.innerHTML = frame("calendar", "Calendar", `<button class="ws-btn" data-go-page="#/watchlist">${icon("star")}Watchlist</button><button class="ws-btn dark" data-newalert="">${icon("bell")}New alert</button>`, body + DISCLAIMER);
}
function eventRow(e) {
  const win = e.date_end ? ` <small>(expected ${esc(dayShort(e.date))} to ${esc(dayShort(e.date_end))})</small>` : "";
  return `<div class="ws-ev">${av(e.symbol)}<div class="ws-ev-main"><b><a class="co-link" href="#/company/${encodeURIComponent(e.symbol)}">${esc(e.name)}</a></b><small>${esc(e.symbol)}${e.title ? " · " + esc(e.title) : ""}</small></div>
    <span class="ws-pill t-${esc(e.type)}">${esc(e.label)}</span>
    <span class="ws-est ${e.estimated ? "est" : "ok"}" title="${esc(e.source)}">${e.estimated ? "Estimated" : "Confirmed"}</span>${win}
    <span class="sp"></span>${e.type === "results" ? `<button class="ws-ic" data-newalert="${esc(e.symbol)}" data-kind="result" title="Alert me when ${esc(e.symbol)} announces results">${icon("bell")}</button>` : ""}
    <button class="ws-ic" data-ask="What should I check before ${esc(e.symbol)}'s ${e.type === "results" ? "results" : "dividend date"}?" title="Ask about ${esc(e.symbol)}">${icon("chat")}</button></div>`;
}
Pages.calendar = { show() { loadCalendar(); }, hide() {} };

/* =================================================================== ALERTS */
const L = { tab: "inbox", notes: null, alerts: null, err: null, presets: null, live: null };
async function loadAlerts() {
  const el = $root("alerts");
  if (!L.notes && !L.alerts) el.innerHTML = frame("bell", "Alerts", "", loading("alerts"));
  try {
    const [n, a, lv] = await Promise.all([api("/notifications"), api("/alerts"), api("/alerts/live-status").catch(() => null)]);
    L.notes = n; L.alerts = a; L.live = lv; L.err = null;
  } catch (e) { L.err = e.message; }
  renderAlerts(); refreshBadge();
}
function renderAlerts() {
  const el = $root("alerts");
  if (L.err || !L.notes) { el.innerHTML = frame("bell", "Alerts", "", failed(L.err || "Could not load alerts.", "alerts")); return; }
  const unread = L.notes.unread, nRules = L.alerts.items.length;
  const tabs = `<div class="ws-tabs"><button data-atab="inbox" class="${L.tab === "inbox" ? "on" : ""}">Inbox${unread ? `<span>${unread}</span>` : ""}</button><button data-atab="rules" class="${L.tab === "rules" ? "on" : ""}">My alerts<span class="plain">${nRules}</span></button></div>`;
  el.innerHTML = frame("bell", "Alerts", `<button class="ws-btn" data-check title="Run every alert against the data stored right now">${icon("check")}Check now</button><button class="ws-btn dark" data-newalert="">${icon("plus")}New alert</button>`,
    `<h1 class="ws-h">Alerts</h1><p class="ws-sub">Price breakouts and results announcements for the stocks you follow. Close-based alerts and results are checked on end-of-day data and stored filings.</p>${liveBanner()}${tabs}${L.tab === "inbox" ? inboxHTML() : rulesHTML()}${DISCLAIMER}`);
}
function liveBanner() {
  const v = L.live; if (!v) return "";
  const lp = v.last_poll || {};
  const extra = v.state === "on" && lp.at ? ` Last reading ${esc(String(lp.at).slice(11, 16))} IST${lp.errors?.length ? ", with errors: " + esc(lp.errors[0]) : ""}.` : "";
  return `<div class="ws-live s-${esc(v.state)}"><i></i><span><b>Live price checks:</b> ${esc(v.text)}${extra}</span></div>`;
}
function inboxHTML() {
  const items = L.notes.items;
  if (!items.length) return `<div class="ws-empty"><h3>No notifications yet</h3><p>${L.alerts.items.length ? "Your alerts are watching. Notifications appear here when one triggers." : "Create an alert and notifications will appear here when it triggers."}</p>
    <button class="ws-btn dark" data-newalert="">${icon("plus")}New alert</button></div>`;
  return `${L.notes.unread ? `<div class="ws-inbox-bar"><button class="ws-btn" data-readall>Mark all read</button></div>` : ""}` + items.map(n => {
    const url = safeUrl(n.payload?.url);
    return `<article class="ws-note ${n.read ? "" : "unread"}" data-note="${n.id}">
      <span class="ws-note-ic k-${esc(n.kind)}">${icon(n.kind === "result" ? "chat" : "chart")}</span>
      <div class="ws-note-main"><h4>${esc(n.title)}<span class="dot"></span></h4><p>${esc(n.body)}</p>
        <div class="ws-note-meta"><span>${esc(ago(n.created_at))}</span><a class="ws-sym co-link" href="#/company/${encodeURIComponent(n.symbol)}">${esc(n.symbol)}</a>${n.payload?.mode === "live" ? `<span class="ws-tag live" title="${esc(n.payload.source || "")} at ${esc(n.payload.at || "")}">Live price</span>` : n.payload?.mode === "touch" ? `<span class="ws-tag" title="Judged from the day's high or low after the close">Day high/low</span>` : ""}
          ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">Open filing</a>` : ""}
          <button data-ask="${esc(n.kind === "result" ? `Summarise ${n.symbol}'s latest results` : `What changed for ${n.symbol} recently? Show its chart with the 20 and 50 day EMA.`)}">Ask about this</button>
          ${n.read ? "" : `<button data-read="${n.id}">Mark read</button>`}<button data-delnote="${n.id}" class="danger">Delete</button></div></div></article>`;
  }).join("");
}
function rulesHTML() {
  const items = L.alerts.items;
  if (!items.length) return `<div class="ws-empty"><h3>No alerts yet</h3><p>Get a notification when a stock closes above its 52-week high, when moving averages cross, or when a company announces results.</p><button class="ws-btn dark" data-newalert="">${icon("plus")}New alert</button></div>`;
  return items.map(a => {
    const st = a.status, scope = a.symbol ? a.symbol : `Whole watchlist (${L.alerts.watchlist_size})`;
    const detail = a.kind === "result"
      ? `<p class="ws-st">${esc(st.text)}</p>`
      : st.text ? `<p class="ws-st">${esc(st.text)}</p>`
      : `<ul class="ws-stl">${st.items.slice(0, 5).map(i => `<li class="s-${esc(i.state)}"><b>${esc(i.symbol)}</b> ${esc(i.text)}</li>`).join("")}${st.items.length > 5 ? `<li class="muted">and ${st.items.length - 5} more${st.truncated ? " (list capped)" : ""}</li>` : ""}</ul>`;
    return `<article class="ws-rule ${a.active ? "" : "off"}"><div class="ws-rule-head"><span class="ws-note-ic k-${esc(a.kind)}">${icon(a.kind === "result" ? "chat" : "chart")}</span>
      <div class="ws-rule-main"><h4>${esc(a.label)}</h4><small>${esc(scope)}${a.last_checked ? " · checked " + esc(ago(a.last_checked)) : " · not checked yet"}</small></div>
      <label class="ws-switch" title="${a.active ? "Pause" : "Resume"}"><input type="checkbox" data-toggle="${esc(a.id)}" ${a.active ? "checked" : ""}><i></i></label>
      <button class="ws-ic" data-delalert="${esc(a.id)}" title="Delete alert">${icon("trash")}</button></div>${detail}</article>`;
  }).join("");
}
Pages.alerts = { show() { loadAlerts(); }, hide() {}, refresh: loadAlerts };

/* ----------------------------------------------------------- new-alert dialog */
async function openAlertDialog(preset = {}) {
  try { L.presets = await api("/alerts/presets"); } catch (e) { toast(e.message); return; }
  const P = L.presets.breakout, wlSize = (await api("/watchlist").catch(() => [])).length;
  let kind = preset.kind || "breakout";
  A().openModal(`<div class="dlg narrow"><div class="dlg-h">New alert<button class="icon-btn" data-close>${icon("x")}</button></div>
    <form class="fe" id="alForm" onsubmit="return false">
      <div class="ws-kinds"><button type="button" data-kind="breakout">${icon("chart")}<b>Price breakout</b><small>52-week high or low, moving-average cross, price level</small></button>
        <button type="button" data-kind="result">${icon("chat")}<b>Results announced</b><small>When a company files its financial results</small></button></div>
      <label id="alPresetW">Condition<select id="alPreset">${P.map(p => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join("")}</select></label>
      <div class="fe-row" id="alParams"></div>
      <label>Applies to<select id="alScope"><option value="watch">Whole watchlist (${wlSize} stock${wlSize === 1 ? "" : "s"})</option><option value="one" ${preset.symbol ? "selected" : ""}>One stock</option></select></label>
      <div id="alSymW" class="ws-add" hidden><label class="ws-search">${icon("search")}<input id="alSym" placeholder="Symbol or name, e.g. TCS" autocomplete="off" value="${esc(preset.symbol || "")}"></label><div class="ws-pick" id="alPick" hidden></div></div>
      <label id="alExistW" class="ws-check" hidden><input type="checkbox" id="alExist"> Also notify about results already announced</label>
      <p class="fe-hint" id="alHint"></p><p class="fe-err" id="alErr" hidden></p>
      <div class="fe-actions"><span class="sp"></span><button type="button" class="scr-btn" data-close>Cancel</button><button type="submit" class="scr-btn dark" id="alOk">Create alert</button></div></form></div>`);
  const $m = s => $(s, $("#modal"));
  const curPreset = () => P.find(x => x.id === $m("#alPreset").value);
  // Parameter inputs are rebuilt only when the condition changes; other changes must never wipe what was typed.
  const renderParams = () => {
    $m("#alParams").innerHTML = kind === "breakout" ? curPreset().params.map(q => q.type === "choice"
      ? `<label>${esc(q.label)}<select data-p="${q.key}">${q.choices.map(c => `<option value="${esc(c)}" ${c === q.default ? "selected" : ""}>${esc(q.labels?.[c] || c)}</option>`).join("")}</select></label>`
      : `<label>${esc(q.label)}<input data-p="${q.key}" type="number" step="any" min="${q.min ?? ""}" max="${q.max ?? ""}" value="${q.default ?? ""}" placeholder="${q.default ?? "e.g. 4000"}"></label>`).join("") : "";
  };
  const sync = () => {
    $m("#alPresetW").hidden = kind !== "breakout"; $m("#alParams").hidden = kind !== "breakout"; $m("#alExistW").hidden = kind !== "result";
    document.querySelectorAll("#modal .ws-kinds button").forEach(b => b.classList.toggle("on", b.dataset.kind === kind));
    const touch = $m('[data-p="trigger"]')?.value === "touch", lv = L.presets.live || {};
    const liveHint = !touch ? " Judged on the closing price, with end-of-day data that is not adjusted for splits or bonuses."
      : (lv.state === "on" || lv.state === "closed") ? ` Uses live prices from ${lv.source || "Angel One"} during market hours (09:15-15:30 IST). A price can touch your level and fall back before the close.`
      : ` ${lv.text} The alert will still fire after the close if the day's high or low crossed the level.`;
    $m("#alHint").textContent = kind === "breakout" ? curPreset().description + liveHint + " Only moves after you create the alert are counted."
      : "Fires when a title looks like an outcome of results (not a board-meeting notice). Needs filings loaded in the warehouse (python -m scripts.load_announcements).";
    $m("#alSymW").hidden = $m("#alScope").value !== "one";
  };
  document.querySelectorAll("#modal .ws-kinds button").forEach(b => b.onclick = () => { kind = b.dataset.kind; renderParams(); sync(); });
  $m("#alPreset").onchange = () => { renderParams(); sync(); }; $m("#alScope").onchange = sync; $m("#alParams").onchange = sync;
  symbolPicker($m("#alSym"), $m("#alPick"), s => { $m("#alSym").value = s; });
  renderParams(); sync();
  $m("#alForm").onsubmit = async e => {
    e.preventDefault();
    const err = $m("#alErr"); err.hidden = true;
    const symbol = $m("#alScope").value === "one" ? $m("#alSym").value.trim().toUpperCase() : null;
    if ($m("#alScope").value === "one" && !symbol) { err.textContent = "Choose a stock."; err.hidden = false; return; }
    const params = {};
    document.querySelectorAll("#modal [data-p]").forEach(i => { params[i.dataset.p] = i.value; });
    const body = kind === "breakout" ? { kind, preset: $m("#alPreset").value, symbol, params } : { kind, symbol, include_existing: $m("#alExist").checked };
    $m("#alOk").disabled = true;
    try {
      await api("/alerts", { method: "POST", body });
      A().closeModal(); toast("Alert created"); L.tab = "rules";
      if (location.hash.startsWith("#/alerts")) loadAlerts(); else { location.hash = "#/alerts"; }
    } catch (ex) { err.textContent = ex.message; err.hidden = false; $m("#alOk").disabled = false; }
  };
}

/* ------------------------------------------------------------------- events */
function onClick(e) {
  const t = e.target;
  const retry = t.closest("[data-retry]"); if (retry) { ({ watchlist: loadWatchlist, calendar: loadCalendar, alerts: loadAlerts })[retry.dataset.retry]?.(); return; }
  const go = t.closest("[data-go-page]"); if (go) { location.hash = go.dataset.goPage; return; }
  const ask = t.closest("[data-ask]"); if (ask) { A().ask(ask.dataset.ask); return; }
  const na = t.closest("[data-newalert]"); if (na) { openAlertDialog({ symbol: na.dataset.newalert || "", kind: na.dataset.kind || "breakout" }); return; }
  const add = t.closest("[data-add]"); if (add) { addToWatchlist(add.dataset.add); return; }
  const rm = t.closest("[data-rm]");
  if (rm) { api("/watchlist/" + encodeURIComponent(rm.dataset.rm), { method: "DELETE" }).then(loadWatchlist).catch(x => toast(x.message)); return; }
  const ty = t.closest("[data-type]"); if (ty) { C.type = ty.dataset.type; renderCalendar(); return; }
  const dy = t.closest("[data-days]"); if (dy) { C.days = +dy.dataset.days; loadCalendar(); return; }
  const tab = t.closest("[data-atab]"); if (tab) { L.tab = tab.dataset.atab; renderAlerts(); return; }
  if (t.closest("[data-check]")) {
    api("/alerts/check", { method: "POST" }).then(r => { toast(r.live?.status === "error" ? `Live price check failed: ${(r.live.errors || [""])[0]}` : r.errors?.length ? `Checked with ${r.errors.length} error(s)` : `Checked ${r.alerts} alert${r.alerts === 1 ? "" : "s"}: ${r.new} new notification${r.new === 1 ? "" : "s"}`); L.tab = r.new ? "inbox" : L.tab; return loadAlerts(); }).catch(x => toast(x.message));
    return;
  }
  const rd = t.closest("[data-read]"); if (rd) { api("/notifications/read", { method: "POST", body: { ids: [+rd.dataset.read] } }).then(loadAlerts); return; }
  if (t.closest("[data-readall]")) { api("/notifications/read", { method: "POST", body: {} }).then(loadAlerts); return; }
  const dn = t.closest("[data-delnote]"); if (dn) { api("/notifications/" + dn.dataset.delnote, { method: "DELETE" }).then(loadAlerts); return; }
  const da = t.closest("[data-delalert]");
  if (da) { if (confirm("Delete this alert? Notifications it already sent stay in your inbox.")) api("/alerts/" + da.dataset.delalert, { method: "DELETE" }).then(loadAlerts).catch(x => toast(x.message)); return; }
  const note = t.closest("[data-note]");
  if (note && note.classList.contains("unread") && !t.closest("a,button")) api("/notifications/read", { method: "POST", body: { ids: [+note.dataset.note] } }).then(loadAlerts);
}
function onChange(e) {
  const tg = e.target.closest?.("[data-toggle]");
  if (tg) api("/alerts/" + tg.dataset.toggle, { method: "PATCH", body: { active: tg.checked } }).then(loadAlerts).catch(x => { toast(x.message); loadAlerts(); });
}
window.WS = { openAlertDialog };
document.addEventListener("DOMContentLoaded", () => {
  for (const id of ["watchlist", "calendar", "alerts"]) { const r = $root(id); r.addEventListener("click", onClick); r.addEventListener("change", onChange); }
});
})();