/* Company page (#/company/<SYMBOL>): overview, results with a trend chart, full statements, ratios, peers.
   Registers into window.Pages; uses the helpers app.js exposes on window.App. The server owns all numbers. */
(() => {
"use strict";
const A = () => window.App;
const $ = (s, r = document) => r.querySelector(s);
const esc = s => A().esc(s);
const icon = (n, c) => A().icon(n, c);
const toast = m => A().toast(m);
const api = (p, o) => A().api(p, o);
const root = () => document.getElementById("company");
const Pages = (window.Pages = window.Pages || {});

const TABS = [["overview", "Overview"], ["results", "Results"], ["statements", "Statements"], ["ratios", "Ratios"], ["peers", "Peers"]];
const STMT = [["income", "Profit & loss"], ["balance", "Balance sheet"], ["cashflow", "Cash flow"]];
let S = null;          // everything about the open company
let tok = 0;

/* ---------------------------------------------------------------- formatting */
const grp = (v, d = 2) => Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const dash = "—";
const cls = v => (v > 0 ? "pos" : v < 0 ? "neg" : "");
function fv(v, unit) {
  if (v == null) return dash;
  if (unit === "%") return grp(v) + "%";
  if (unit === "x") return grp(v) + "x";
  if (unit === "INR crore") return "₹" + grp(v, Math.abs(v) >= 100 ? 0 : 2) + " Cr";
  if (unit === "INR") return "₹" + grp(v);
  return grp(v);
}
function cellFor(kind, v) {
  if (v == null) return dash;
  if (kind === "count") return Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 });
  if (kind === "rate") return grp(v, 1) + "%";
  return grp(v);
}
const initials = s => esc(String(s).slice(0, 2));
const ordinal = n => `${n}${n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th")}`;
const link = sym => `#/company/${encodeURIComponent(sym)}`;

/* -------------------------------------------------------------------- charts */
function resultsChart(rows) {
  const pts = rows.filter(r => r.revenue != null || r.net_income != null).slice(-10);
  if (!pts.length) return `<div class="co-empty-chart">No results to chart.</div>`;
  const W = 760, H = 280, L = 62, R = 46, T = 34, B = 40, iw = W - L - R, ih = H - T - B;
  const maxV = Math.max(...pts.map(p => Math.max(p.revenue || 0, p.net_income || 0)), 1);
  const nice = v => { const p = Math.pow(10, Math.floor(Math.log10(v))); const m = v / p; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p; };
  const top = nice(maxV * 1.05), maxO = Math.max(...pts.map(p => p.opm || 0), 1) * 1.25;
  const y = v => T + ih - (v / top) * ih, yo = v => T + ih - (v / maxO) * ih;
  const slot = iw / pts.length, bw = Math.min(26, slot * 0.34);
  let g = "";
  for (let i = 0; i <= 4; i++) {
    const v = (top / 4) * i;
    g += `<line class="gl" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text class="ax" x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${grp(v, 0)}</text>`;
  }
  let bars = "", line = "", dots = "", labels = "";
  pts.forEach((p, i) => {
    const cx = L + slot * (i + 0.5);
    if (p.revenue != null) bars += `<rect class="b-rev" x="${cx - bw - 1}" y="${y(p.revenue)}" width="${bw}" height="${Math.max(0, T + ih - y(p.revenue))}" rx="3"><title>${esc(p.label)} revenue ₹${grp(p.revenue)} Cr</title></rect>`;
    if (p.net_income != null) bars += `<rect class="${p.net_income < 0 ? "b-neg" : "b-ni"}" x="${cx + 1}" y="${p.net_income < 0 ? T + ih : y(p.net_income)}" width="${bw}" height="${Math.abs(T + ih - y(Math.abs(p.net_income)))}" rx="3"><title>${esc(p.label)} net profit ₹${grp(p.net_income)} Cr</title></rect>`;
    if (p.opm != null) { dots += `<circle class="dot" cx="${cx}" cy="${yo(p.opm)}" r="3.5"><title>${esc(p.label)} operating margin ${grp(p.opm)}%</title></circle>`; line += `${line ? "L" : "M"}${cx},${yo(p.opm)}`; }
    labels += `<text class="ax" x="${cx}" y="${H - 14}" text-anchor="middle">${esc(p.label)}</text>`;
  });
  const ro = [0, 0.5, 1].map(f => `<text class="ax o" x="${W - R + 8}" y="${yo(maxO * f) + 4}">${grp(maxO * f, 0)}%</text>`).join("");
  return `<svg class="co-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Revenue, net profit and operating margin by period">${g}${bars}<path class="ln" d="${line}"/>${dots}${labels}${ro}
    <g class="lg" transform="translate(${L},12)"><rect class="b-rev" width="10" height="10" rx="2"/><text x="16" y="9">Revenue (₹ Cr)</text><rect class="b-ni" x="124" width="10" height="10" rx="2"/><text x="140" y="9">Net profit (₹ Cr)</text><circle class="dot" cx="262" cy="5" r="4"/><text x="272" y="9">Operating margin (right axis)</text></g></svg>`;
}

/* --------------------------------------------------------------------- shell */
const topbar = (name, extra = "") => `<header class="topbar"><span class="crumb-icon">${icon("chart")}</span><span class="crumb-sep">${icon("chevR")}</span><b class="crumb-title">${esc(name)}</b>${extra}</header>`;
const frame = (name, body) => `${topbar(name)}<div class="co-scroll"><div class="co-page">${body}</div></div>`;

function searchBox() {
  return `<div class="co-find"><label class="ws-search">${icon("search")}<input id="coQ" placeholder="Search a company by symbol or name" autocomplete="off"></label><div class="ws-pick" id="coPick" hidden></div></div>`;
}
function wireSearch() {
  const input = $("#coQ", root()), list = $("#coPick", root()); if (!input) return;
  let t = 0;
  input.addEventListener("input", () => {
    clearTimeout(t); const q = input.value.trim();
    if (!q) { list.hidden = true; return; }
    t = setTimeout(async () => {
      try {
        const hits = await api("/symbols/search?q=" + encodeURIComponent(q));
        list.innerHTML = hits.map(h => `<a class="co-hit" href="${link(h.symbol)}"><i class="ws-av" style="background:${A().colorOf(h.symbol)}">${initials(h.symbol)}</i><span><b>${esc(h.symbol)}</b><small>${esc(h.name)}</small></span></a>`).join("") || `<div class="ws-none">No NSE stock matches "${esc(q)}".</div>`;
        list.hidden = false;
      } catch { list.hidden = true; }
    }, 180);
  });
  input.addEventListener("keydown", e => { if (e.key === "Enter") { const a = list.querySelector("a"); if (a) location.hash = a.getAttribute("href"); } });
}

async function open(arg) {
  const t = ++tok;
  if (!arg) { S = null; root().innerHTML = frame("Company", `<h1 class="co-h">Company research</h1><p class="co-sub">Search for any NSE stock to see its results, statements, ratios and peers.</p>${searchBox()}`); wireSearch(); return; }
  const sym = decodeURIComponent(arg).toUpperCase();
  S = { sym, d: null, tab: "overview", rperiod: "annual", res: {}, kind: "income", speriod: "annual", stm: {}, filter: "", peers: null, busy: "", err: null };
  root().innerHTML = frame(sym, `<div class="ws-state">Loading ${esc(sym)}…</div>`);
  try { const d = await api(`/company/${encodeURIComponent(sym)}`); if (t !== tok) return; S.d = d; }
  catch (e) { if (t === tok) root().innerHTML = frame(sym, `<div class="ws-state err">${esc(e.message)}</div>${searchBox()}`), wireSearch(); return; }
  render();
}

function priceBlock(d) {
  const p = d.price; if (p.value == null) return `<div class="co-price"><div class="co-px">${dash}</div><small>No price on record</small></div>`;
  const mode = p.mode === "live" ? ["live", "Live"] : p.mode === "mixed" ? ["mixed", "Partly live"] : ["eod", "End of day"];
  return `<div class="co-price"><div class="co-px">₹${grp(p.value)}</div><div class="${cls(p.change_pct)}">${p.change != null ? (p.change > 0 ? "+" : "") + grp(p.change) : ""} ${p.change_pct != null ? "(" + (p.change_pct > 0 ? "+" : "") + grp(p.change_pct) + "%)" : ""}</div>
    <small><span class="ws-mode ${mode[0]}"><i></i>${mode[1]}</span> ${esc(p.as_of)}</small></div>`;
}

function render() {
  const d = S.d, has = d.coverage.has_data;
  root().innerHTML = frame(d.name, `
    <div class="co-head"><div class="co-id"><i class="ws-av big" style="background:${A().colorOf(d.symbol)}">${initials(d.symbol)}</i>
      <div><h1 class="co-h">${esc(d.name)}</h1><p class="co-sub">${esc(d.symbol)}${d.sector ? " · " + esc(d.sector) : ""}${d.industry ? " · " + esc(d.industry) : ""}</p></div></div>${priceBlock(d)}</div>
    <div class="co-actions"><button class="ws-btn" data-watch>${icon("star")}${d.in_watchlist ? "In watchlist" : "Add to watchlist"}</button>
      <button class="ws-btn" data-alert>${icon("bell")}New alert</button><button class="ws-btn" data-ask>${icon("chat")}Ask in chat</button>${searchBox()}</div>
    ${d.summary.length ? `<div class="co-summary">${d.summary.map(l => `<span>${esc(l)}</span>`).join("")}</div>` : ""}
    <div class="ws-tabs co-tabs">${TABS.map(([k, l]) => `<button data-tab="${k}" class="${S.tab === k ? "on" : ""}">${l}</button>`).join("")}</div>
    <div id="coBody">${has || S.tab === "overview" ? "" : noData()}</div>`);
  wireSearch(); renderTab();
}

function noData() {
  const c = S.d.coverage, busy = S.busy === "refresh";
  return `<div class="ws-empty"><h3>No financial statements stored for ${esc(S.sym)} yet</h3>
    <p>${c.error ? "The last attempt failed: " + esc(c.error) + " " : ""}Load its annual and quarterly income statement, balance sheet and cash flow from Yahoo Finance. This takes a few seconds.</p>
    <button class="ws-btn dark" data-refresh ${busy ? "disabled" : ""}>${icon("refresh")}${busy ? "Fetching from Yahoo Finance…" : "Load statements now"}</button>${S.err ? `<p class="co-err">${esc(S.err)}</p>` : ""}</div>`;
}

function renderTab() {
  const el = $("#coBody", root()); if (!el) return;
  if (!S.d.coverage.has_data) { el.innerHTML = S.tab === "overview" ? overviewHTML() : noData(); return; }
  ({ overview: () => (el.innerHTML = overviewHTML()), results: loadResults, statements: loadStatements, ratios: () => (el.innerHTML = ratiosHTML()), peers: loadPeers })[S.tab]();
}

/* ------------------------------------------------------------------ overview */
const KEY = ["market_cap", "pe", "pb", "roe", "roce", "op_margin", "net_margin", "debt_equity"];
function ratioCard(i) {
  return `<div class="co-card" title="${esc(i.value == null ? i.reason : i.formula)}"><small>${esc(i.label)}</small><b>${fv(i.value, i.unit)}</b><span>${i.value == null ? esc(i.reason || "") : esc([i.period, i.basis].filter(Boolean).join(" · "))}</span></div>`;
}
function coverageBox() {
  const c = S.d.coverage, busy = S.busy === "refresh";
  return `<div class="co-cov"><div><b>Data stored:</b> ${c.annual_periods} annual and ${c.quarterly_periods} quarterly periods · ${esc(c.source || "an earlier, partial load")} · fetched ${esc(c.fetched_at || "(date not recorded)")}${c.quarterly_periods === 0 || !c.statements.includes("balance (annual)") ? " · <b>incomplete: refresh to load all statements</b>" : ""}
    ${c.error ? `<br><span class="co-err">Last fetch reported: ${esc(c.error)}</span>` : ""}</div>
    <button class="ws-btn" data-refresh ${busy ? "disabled" : ""}>${icon("refresh")}${busy ? "Fetching…" : "Refresh from Yahoo Finance"}</button>${S.err ? `<p class="co-err">${esc(S.err)}</p>` : ""}</div>`;
}
function overviewHTML() {
  const d = S.d;
  if (!d.coverage.has_data) return noData();
  const by = d.ratios.by_key;
  return `<div class="co-cards">${KEY.map(k => ratioCard(by[k])).join("")}</div>
    <h3 class="co-h3">Revenue and profit</h3><div class="co-chartbox">${resultsChart(d.results.rows)}</div>${coverageBox()}
    <p class="ws-disclaimer">Figures are computed from the statements above (hover a card for its formula). Research only, not investment advice.</p>`;
}

/* ------------------------------------------------------------------- results */
async function loadResults() {
  const el = $("#coBody", root()), p = S.rperiod;
  if (!S.res[p]) { el.innerHTML = `<div class="ws-state">Loading results…</div>`; try { S.res[p] = await api(`/company/${encodeURIComponent(S.sym)}/results?period=${p}`); } catch (e) { el.innerHTML = `<div class="ws-state err">${esc(e.message)}</div>`; return; } }
  const r = S.res[p], q = p === "quarterly";
  const cols = [...r.rows, ...(r.ttm ? [{ ...r.ttm, ttm: true }] : [])];
  const row = (label, key, f = v => grp(v), colorize = false) => `<tr><th>${label}</th>${cols.map(c => `<td class="${colorize ? cls(c[key]) : ""}">${c[key] == null ? dash : f(c[key])}</td>`).join("")}</tr>`;
  const pct = v => (v > 0 && v !== null ? "+" : "") + grp(v) + "%";
  $("#coBody", root()).innerHTML = `<div class="co-tools"><div class="ws-pills">${[["annual", "Annual"], ["quarterly", "Quarterly"]].map(([k, l]) => `<button data-rperiod="${k}" class="${p === k ? "on" : ""}">${l}</button>`).join("")}</div><small>₹ crore unless stated</small></div>
    ${r.rows.length ? `<div class="co-chartbox">${resultsChart(r.rows)}</div>
    <div class="co-tablewrap"><table class="co-table"><thead><tr><th></th>${cols.map(c => `<th class="${c.ttm ? "ttm" : ""}">${esc(c.label)}</th>`).join("")}</tr></thead><tbody>
      ${row("Revenue", "revenue")}${row("Operating profit", "operating_income")}${row("Net profit", "net_income")}${row("EPS (₹)", "eps")}
      ${row("Operating margin", "opm", v => grp(v) + "%")}${row("Net margin", "npm", v => grp(v) + "%")}
      ${row("Revenue growth, year on year", "revenue_yoy", pct, true)}${row("Net profit growth, year on year", "net_income_yoy", pct, true)}
      ${q ? row("Revenue growth, quarter on quarter", "revenue_qoq", pct, true) + row("Net profit growth, quarter on quarter", "net_income_qoq", pct, true) : ""}
    </tbody></table></div>${q && !r.ttm ? `<p class="co-note">TTM needs four consecutive quarters; they are not all stored.</p>` : ""}`
      : `<div class="ws-empty"><p>No ${p} results are stored for ${esc(S.sym)}.</p></div>`}
    <p class="ws-disclaimer">Source: ${esc(S.d.coverage.source || "")}. Growth is matched to the same period a year earlier by date.</p>`;
}

/* ---------------------------------------------------------------- statements */
async function loadStatements() {
  const el = $("#coBody", root()), key = `${S.kind}|${S.speriod}`;
  if (!S.stm[key]) { el.innerHTML = `<div class="ws-state">Loading statement…</div>`; try { S.stm[key] = await api(`/company/${encodeURIComponent(S.sym)}/statements?kind=${S.kind}&period=${S.speriod}`); } catch (e) { el.innerHTML = `<div class="ws-state err">${esc(e.message)}</div>`; return; } }
  const t = S.stm[key], f = S.filter.trim().toLowerCase();
  const rows = t.rows.filter(r => !f || r.label.toLowerCase().includes(f));
  el.innerHTML = `<div class="co-tools"><div class="ws-pills">${STMT.map(([k, l]) => `<button data-kind="${k}" class="${S.kind === k ? "on" : ""}">${l}</button>`).join("")}</div>
      <div class="ws-pills">${[["annual", "Annual"], ["quarterly", "Quarterly"]].map(([k, l]) => `<button data-speriod="${k}" class="${S.speriod === k ? "on" : ""}">${l}</button>`).join("")}</div>
      <label class="ws-search co-filter">${icon("search")}<input id="coFilter" placeholder="Filter line items" value="${esc(S.filter)}" autocomplete="off"></label></div>
    ${t.rows.length ? `<div class="co-tablewrap tall"><table class="co-table stmt"><thead><tr><th>₹ crore</th>${t.periods.map(p => `<th>${esc(p.label)}</th>`).join("")}</tr></thead><tbody id="coRows">${stmtRows(rows, t)}</tbody></table></div>
      <p class="co-note">${t.rows.length} lines as reported by the data provider. Per-share lines are in ₹, share counts are as reported, rates are in %.</p>`
      : `<div class="ws-empty"><p>No ${S.speriod} ${esc(STMT.find(x => x[0] === S.kind)[1].toLowerCase())} is stored for ${esc(S.sym)}.</p></div>`}`;
  const inp = $("#coFilter", el); if (inp && S.focusFilter) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); S.focusFilter = false; }
}
const stmtRows = (rows, t) => rows.map(r => `<tr><th>${esc(r.label)}</th>${t.periods.map(p => { const v = r.values[p.period]; return `<td class="${v < 0 ? "neg" : ""}">${cellFor(r.kind, v)}</td>`; }).join("")}</tr>`).join("")
  || `<tr><td colspan="${t.periods.length + 1}" class="ws-none">No line item matches.</td></tr>`;

/* ------------------------------------------------------------------- ratios */
function ratiosHTML() {
  const v = S.d.ratios;
  return `<p class="co-sub">Every figure is computed from the stored statements${v.price ? ` and the price of ₹${grp(v.price)}` : ""}. Open a card to see its formula and inputs.</p>
    ${v.groups.map(g => `<h3 class="co-h3">${esc(g.name)}</h3><div class="co-cards wide">${g.items.map(i => `<details class="co-card ${i.value == null ? "na" : ""}"><summary><small>${esc(i.label)}</small><b>${fv(i.value, i.unit)}</b>
      <span>${i.value == null ? esc(i.reason) : esc([i.period, i.basis].filter(Boolean).join(" · "))}</span></summary>
      <div class="co-fx"><code>${esc(i.formula)}</code>${i.inputs.length ? `<ul>${i.inputs.map(n => `<li>${esc(n.name)}: <b>${n.value == null ? dash : n.unit === "INR crore" ? "₹" + grp(n.value) + " Cr" : n.unit === "INR" ? "₹" + grp(n.value) : grp(n.value, 0)}</b></li>`).join("")}</ul>` : ""}</div></details>`).join("")}</div>`).join("")}
    <p class="ws-disclaimer">Banks and financial companies do not get ratios that are not meaningful for them. Research only, not investment advice.</p>`;
}

/* --------------------------------------------------------------------- peers */
async function loadPeers() {
  const el = $("#coBody", root());
  if (!S.peers) { el.innerHTML = `<div class="ws-state">Finding peers…</div>`; try { S.peers = await api(`/company/${encodeURIComponent(S.sym)}/peers`); } catch (e) { el.innerHTML = `<div class="ws-state err">${esc(e.message)}</div>`; return; } }
  const p = S.peers, ms = p.metrics;
  const value = (r, m) => r[m.key] == null ? dash : m.key === "market_cap" ? "₹" + grp(r[m.key], 0) + " Cr" : m.unit === "%" ? grp(r[m.key]) + "%" : m.unit === "x" ? grp(r[m.key]) + "x" : grp(r[m.key]);
  const best = {}; ms.forEach(m => { if (!m.better) return; const vals = [p.subject, ...p.peers].map(r => r && r[m.key]).filter(v => v != null); if (vals.length >= 3) best[m.key] = m.better === "high" ? Math.max(...vals) : Math.min(...vals); });
  const tr = (r, c = "") => `<tr class="${c}"><th>${c === "me" ? `<b>${esc(r.symbol)}</b><small>${esc(r.name)} · this company</small>` : `<a href="${link(r.symbol)}"><b>${esc(r.symbol)}</b><small>${esc(r.name)}</small></a>`}</th>
    ${ms.map(m => `<td class="${best[m.key] != null && r[m.key] === best[m.key] ? "best" : ""}">${value(r, m)}</td>`).join("")}</tr>`;
  el.innerHTML = !p.subject ? `<div class="ws-empty"><p>${esc((p.notes || [])[0] || "No comparison available.")}</p></div>` : `
    <p class="co-sub">${p.group.name ? `Compared with ${p.peers.length} stock${p.peers.length === 1 ? "" : "s"} in <b>${esc(p.group.name)}</b> (${esc(p.group.taxonomy)}) that have statements stored, closest in size first.` : ""}</p>
    ${p.peers.length ? `<div class="co-tablewrap"><table class="co-table peers"><thead><tr><th>Company</th>${ms.map(m => `<th title="${m.better ? (m.better === "high" ? "Higher is better" : "Lower is better") : ""}">${esc(m.label)}</th>`).join("")}</tr></thead><tbody>
      ${tr(p.subject, "me")}${p.peers.map(r => tr(r)).join("")}
      <tr class="med"><th>Peer median</th>${ms.map(m => `<td>${p.median && p.median[m.key] != null ? value(p.median, m) : dash}</td>`).join("")}</tr>
      <tr class="rank"><th>${esc(p.symbol)}'s rank</th>${ms.map(m => `<td>${p.rank[m.key] ? ordinal(p.rank[m.key].rank) + " of " + p.rank[m.key].of : ""}</td>`).join("")}</tr></tbody></table></div>
      <p class="co-note">Green marks the best value in a column where one direction is better. Prices: ${esc(p.price_source.source || "")}${p.price_source.as_of ? ", " + esc(p.price_source.as_of) : ""}.</p>` : ""}
    ${(p.notes || []).map(n => `<div class="cnote">⚠ ${esc(n)}</div>`).join("")}`;
}

/* -------------------------------------------------------------------- events */
async function refresh() {
  S.busy = "refresh"; S.err = null; renderTab();
  try {
    await api(`/company/${encodeURIComponent(S.sym)}/refresh`, { method: "POST" });
    S.res = {}; S.stm = {}; S.peers = null; S.busy = "";
    S.d = await api(`/company/${encodeURIComponent(S.sym)}`); toast("Statements loaded"); render();
  } catch (e) { S.busy = ""; S.err = e.message; if (S.d.coverage.has_data) { toast(e.message); } render(); }
}
function onClick(e) {
  const t = e.target;
  if (!S) return;
  const tab = t.closest("[data-tab]"); if (tab) { S.tab = tab.dataset.tab; document.querySelectorAll("#company .co-tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === S.tab)); renderTab(); return; }
  if (t.closest("[data-refresh]")) { refresh(); return; }
  const rp = t.closest("[data-rperiod]"); if (rp) { S.rperiod = rp.dataset.rperiod; loadResults(); return; }
  const k = t.closest("[data-kind]"); if (k) { S.kind = k.dataset.kind; loadStatements(); return; }
  const sp = t.closest("[data-speriod]"); if (sp) { S.speriod = sp.dataset.speriod; loadStatements(); return; }
  if (t.closest("[data-watch]")) {
    api(`/watchlist/${encodeURIComponent(S.sym)}`, { method: S.d.in_watchlist ? "DELETE" : "POST" }).then(() => { S.d.in_watchlist = !S.d.in_watchlist; toast(S.d.in_watchlist ? `${S.sym} added to your watchlist` : `${S.sym} removed from your watchlist`); render(); }).catch(x => toast(x.message));
    return;
  }
  if (t.closest("[data-alert]")) { window.WS?.openAlertDialog({ symbol: S.sym }); return; }
  if (t.closest("[data-ask]")) { A().ask(`Summarise ${S.sym}'s financial performance and how it compares with its peers`); return; }
}
function onInput(e) {
  if (e.target.id === "coFilter" && S) { S.filter = e.target.value; const t = S.stm[`${S.kind}|${S.speriod}`]; const body = $("#coRows", root()); if (t && body) body.innerHTML = stmtRows(t.rows.filter(r => !S.filter.trim() || r.label.toLowerCase().includes(S.filter.trim().toLowerCase())), t); }
}
let wired = false;
function wire() { if (wired) return; wired = true; const r = root(); r.addEventListener("click", onClick); r.addEventListener("input", onInput); document.addEventListener("click", e => { const l = $("#coPick"); if (l && !l.contains(e.target) && e.target.id !== "coQ") l.hidden = true; }); }

Pages.company = { show(arg) { wire(); open(arg); }, hide() { tok++; } };
})();