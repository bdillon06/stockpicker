"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const FACTORS = ["trend", "momentum", "breakout", "volume", "rel_strength"];
// Must mirror scoring.DEFAULT_WEIGHTS. When these drifted from the backend, the
// scan (which sends these) and the drawer (which used the backend defaults)
// scored the same stock differently and disagreed on its badge.
const DEFAULT_W = {trend:45, momentum:20, breakout:15, volume:10, rel_strength:10};
let watchSet = new Set();
let lastResults = [];

const fmt = (x, d = 2) => (x == null || Number.isNaN(x)) ? "—" : Number(x).toFixed(d);
const pct = (x) => (x == null || Number.isNaN(x)) ? "—" : (x * 100).toFixed(1) + "%";
const api = async (url, opts) => {
  const r = await fetch(url, opts);
  return r.json();
};

/* ---- tabs ---- */
$$(".tab").forEach(t => t.onclick = () => {
  $$(".tab").forEach(x => x.classList.remove("active"));
  $$(".view").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  $("#" + t.dataset.tab).classList.add("active");
  if (t.dataset.tab === "watch") loadWatch();
});

/* ---- weight sliders ---- */
function buildWeights() {
  const box = $("#weights");
  box.innerHTML = FACTORS.map(f => `
    <label>${f.replace("_", " ")}
      <input type="range" min="0" max="50" value="${DEFAULT_W[f]}" data-f="${f}">
      <span class="wval" id="wv-${f}">${DEFAULT_W[f]}</span>
    </label>`).join("");
  $$("#weights input").forEach(inp => inp.oninput = () =>
    $("#wv-" + inp.dataset.f).textContent = inp.value);
}
function currentWeights() {
  const w = {};
  $$("#weights input").forEach(i => w[i.dataset.f] = Number(i.value) / 100);
  return w;
}

/* ---- status ---- */
async function loadStatus() {
  const s = await api("/api/status");
  const age = s.last_fetch_age_min == null ? "never" :
    s.last_fetch_age_min < 60 ? `${s.last_fetch_age_min}m ago`
      : `${(s.last_fetch_age_min/60).toFixed(1)}h ago`;
  $("#status").innerHTML =
    `${s.cached}/${s.universe_size} cached · ${s.fresh} fresh · updated ${age}<br>` +
    `${s.watchlist} on watchlist`;
}

/* ---- scan ---- */
async function runScan() {
  const btn = $("#scanBtn");
  btn.disabled = true;
  $("#scanMsg").textContent = "Scanning…";
  try {
    const body = {weights: currentWeights(),
                  top_n: Number($("#topN").value),
                  enrich: $("#enrichChk").checked};
    const res = await api("/api/scan",
      {method:"POST", headers:{"Content-Type":"application/json"},
       body: JSON.stringify(body)});
    if (res.message) { $("#scanMsg").textContent = res.message; return; }
    lastResults = res.results || [];
    const moved = res.prev_date
      ? ` · Δ 1d vs ${res.prev_date}`
      : " · Δ 1d baseline set (scan again tomorrow to see movement)";
    // Always show the session the ranking is built on. A silently throttled
    // refresh otherwise looks identical to a fresh scan. Tolerate a few days'
    // gap so weekends and market holidays don't raise a false alarm.
    const asOf = res.as_of ? staleNotice(res.as_of) : "";
    // A throttled catalyst fetch leaves a row on its technical score only; say
    // so rather than let it quietly disagree with its own detail view.
    const missed = (res.unenriched || []).length
      ? ` · <b class="stale">no catalysts for ${res.unenriched.join(", ")}</b>` +
        " (Yahoo throttled — technical score only)"
      : "";
    $("#scanMsg").innerHTML =
      `${asOf} · ${res.scanned} scanned → <b>${res.qualified}</b> passed the EMA ` +
      `filters → showing <b>${res.shown}</b> · enriched top ${res.enriched}` +
      `${missed}${moved}.`;
    renderScan(lastResults.slice(0, Number($("#topN").value)));
  } catch (e) {
    $("#scanMsg").textContent = "Scan failed: " + e;
  } finally { btn.disabled = false; }
}

/* Flag prices that are genuinely behind, without false-alarming on weekends
   and holidays: >4 calendar days stale cannot be explained by a normal close. */
function staleNotice(asOf) {
  const days = Math.floor((Date.now() - new Date(asOf + "T00:00:00")) / 86400000);
  const stale = days > 4;
  return `<b class="${stale ? "stale" : "fresh"}">prices as of ${asOf}</b>` +
    (stale ? ` ⚠️ ${days} days old — the refresh isn't getting through` : "");
}

function bar(v) { return `<div class="bar"><i style="width:${Math.round(v)}%"></i></div>`; }

/* day-to-day rank movement: ▲/▼ spots vs the previous day's snapshot */
function rankDelta(r) {
  const c = r.rank_change;
  if (c == null) return `<span class="delta new" title="First time ranked">NEW</span>`;
  if (c === 0) return `<span class="delta flat" title="No change">–</span>`;
  const up = c > 0, n = Math.abs(c);
  const title = `${up ? "Up" : "Down"} ${n} ${n === 1 ? "spot" : "spots"} since yesterday`;
  return `<span class="delta ${up ? "up" : "down"}" title="${title}">${up ? "▲" : "▼"}${n}</span>`;
}

function renderScan(rows) {
  const tb = $("#scanTable tbody");
  tb.innerHTML = rows.map((r, i) => {
    const ind = r.indicators, fs = r.factor_scores;
    const starOn = watchSet.has(r.ticker) ? "on" : "";
    return `<tr data-tk="${r.ticker}">
      <td data-label="Rank">${i + 1}</td>
      <td class="dcell" data-label="1d">${rankDelta(r)}</td>
      <td data-label="Ticker"><span class="tk">${r.ticker}<small>${r.name || ""}</small></span></td>
      <td class="score" data-label="Score">${fmt(r.score, 0)}</td>
      <td data-label="Signal"><span class="badge ${r.signal.badge}">${r.signal.badge}</span></td>
      <td data-label="RSI">${fmt(ind.rsi, 0)}</td>
      <td data-label="Trend">${bar(fs.trend)}</td>
      <td data-label="Momentum">${bar(fs.momentum)}</td>
      <td data-label="Breakout">${bar(fs.breakout)}</td>
      <td data-label="Vol">${bar(fs.volume)}</td>
      <td data-label="RS">${bar(fs.rel_strength)}</td>
      <td class="starcell"><button class="star ${starOn}" data-star="${r.ticker}">★</button></td>
    </tr>`;
  }).join("");
  $$("#scanTable tbody tr").forEach(tr => tr.onclick = (e) => {
    if (e.target.dataset.star) return;
    openDetail(tr.dataset.tk);
  });
  $$("[data-star]").forEach(b => b.onclick = (e) => {
    e.stopPropagation(); toggleWatch(b.dataset.star, b);
  });
}

/* ---- detail drawer ---- */
async function openDetail(ticker) {
  $("#drawer").classList.remove("hidden");
  $("#drawerBody").innerHTML = `<h2>${ticker}</h2><p class="msg">Loading…</p>`;
  // Score the drawer with the same weights the scan used, so the detail can
  // never contradict the row that was clicked.
  const qs = new URLSearchParams();
  const w = currentWeights();
  FACTORS.forEach(f => qs.set("w_" + f, w[f]));
  const d = await api("/api/stock/" + ticker + "?" + qs.toString());
  if (d.error) { $("#drawerBody").innerHTML = `<h2>${ticker}</h2><p>${d.error}</p>`; return; }
  const ind = d.indicators, lv = d.signal.levels, en = d.enrich || {};
  const cats = (d.catalyst_notes || []).map(n => `<span class="chip">${n}</span>`).join("") || "—";
  const heads = (en.headlines || []).map(h => `<li>📰 ${h}</li>`).join("");
  $("#drawerBody").innerHTML = `
    <h2>${ticker} <span class="badge ${d.signal.badge}">${d.signal.badge}</span></h2>
    <p class="msg">Score <b style="color:var(--accent)">${fmt(d.score,0)}</b>
      (technical ${fmt(d.base_score,0)})</p>
    <div class="chart-wrap">
      <canvas id="emaChart"></canvas>
      <div class="legend">
        <span><i style="background:#e7ecf3"></i>Price</span>
        <span><i style="background:#4f9dff"></i>EMA 13</span>
        <span><i style="background:#e7b94f"></i>EMA 90</span>
        <span><i style="background:#e0556b"></i>EMA 200</span>
      </div>
    </div>
    <div class="lv">
      <div><b>Entry</b>$${fmt(lv.entry)}</div>
      <div><b>Stop</b>$${fmt(lv.stop)}</div>
      <div><b>Target</b>$${fmt(lv.target)}</div>
      <div><b>R:R</b>${fmt(lv.risk_reward,1)}</div>
    </div>
    <h3>Indicators</h3>
    <div class="kv">
      <div><span>Price</span>$${fmt(ind.price)}</div>
      <div><span>RSI(14)</span>${fmt(ind.rsi,0)}</div>
      <div><span>EMA 13 / 90</span>${fmt(ind.ema13)} / ${fmt(ind.ema90)}</div>
      <div><span>EMA 200 ${ind.ema200_slope>0?"↑":"↓"}</span>${fmt(ind.ema200)}</div>
      <div><span>MACD hist</span>${fmt(ind.macd_hist,3)}</div>
      <div><span>Breakout</span>${fmt(ind.breakout,2)}×</div>
      <div><span>Vol surge</span>${fmt(ind.volume_surge,1)}×</div>
      <div><span>5d / 20d ret</span>${pct(ind.ret_5d)} / ${pct(ind.ret_20d)}</div>
      <div><span>Rel. strength</span>${pct(ind.rel_strength)}</div>
      <div><span>ATR</span>$${fmt(ind.atr)}</div>
    </div>
    ${(d.filter_fails || []).length
      ? `<p class="msg"><b class="stale">Fails the EMA filters:</b> ${
          d.filter_fails.join(" · ")}</p>` : ""}
    <h3>Catalysts</h3><div>${cats}</div>
    ${heads ? `<ul class="reasons">${heads}</ul>` : ""}
    <h3>Why</h3>
    <ul class="reasons">${d.signal.reasons.map(r => `<li>• ${r}</li>`).join("")}</ul>
    <p style="margin-top:18px">
      <button class="star ${watchSet.has(ticker)?"on":""}" id="drawerStar">★ watchlist</button></p>`;
  $("#drawerStar").onclick = () => toggleWatch(ticker, $("#drawerStar"));
  if (d.chart) drawEmaChart($("#emaChart"), d.chart);
}

/* ---- EMA price chart (price + 13/90/200 EMA overlay, hover crosshair) ---- */
function fmtDate(d) {
  if (!d) return "";
  const dt = new Date(d + "T00:00:00");
  return isNaN(dt) ? d : dt.toLocaleDateString(undefined,
    {year: "numeric", month: "short", day: "numeric"});
}
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

function drawEmaChart(canvas, chart) {
  const series = [
    {label: "Price",   data: chart.close,  color: "#e7ecf3", w: 1.6},
    {label: "EMA 13",  data: chart.ema13,  color: "#4f9dff", w: 1.4},
    {label: "EMA 90",  data: chart.ema90,  color: "#e7b94f", w: 1.4},
    {label: "EMA 200", data: chart.ema200, color: "#e0556b", w: 1.6},
  ];
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.parentElement.clientWidth || 480, cssH = 220;
  canvas.width = cssW * dpr; canvas.height = cssH * dpr;
  canvas.style.width = cssW + "px"; canvas.style.height = cssH + "px";
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);   // scale once; renders reuse this

  const padL = 52, padR = 10, padT = 10, padB = 18;
  const W = cssW - padL - padR, H = cssH - padT - padB;
  let lo = Infinity, hi = -Infinity;
  for (const s of series) for (const v of s.data)
    if (v != null) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (!isFinite(lo)) { ctx.fillStyle = "#8b97a7";
    ctx.fillText("No chart data", 10, 20); return; }
  const padv = (hi - lo) * 0.06 || 1; lo -= padv; hi += padv;
  const n = chart.close.length;
  const geom = {padL, padT, W, H, lo, hi, n, cssW, cssH};
  const state = {ctx, chart, series, geom};

  renderChart(state, null);
  // map a clientX to a session index (null when outside the plot area)
  const idxAt = (clientX) => {
    const r = canvas.getBoundingClientRect();
    const mx = clientX - r.left;
    if (mx < padL - 4 || mx > padL + W + 4) return null;
    const i = Math.round(((mx - padL) / W) * (n - 1));
    return Math.max(0, Math.min(n - 1, i));
  };
  canvas.onmousemove = (e) => renderChart(state, idxAt(e.clientX));
  canvas.onmouseleave = () => renderChart(state, null);
  const onTouch = (e) => {
    if (!e.touches.length) return;
    e.preventDefault();                       // hold the crosshair, don't scroll
    renderChart(state, idxAt(e.touches[0].clientX));
  };
  canvas.ontouchstart = onTouch;
  canvas.ontouchmove = onTouch;
  canvas.ontouchend = () => renderChart(state, null);
}

function renderChart(state, hoverIdx) {
  const {ctx, chart, series, geom} = state;
  const {padL, padT, W, H, lo, hi, n, cssW, cssH} = geom;
  const x = i => padL + (n <= 1 ? 0 : (i / (n - 1)) * W);
  const y = v => padT + H - ((v - lo) / (hi - lo)) * H;

  ctx.clearRect(0, 0, cssW, cssH);
  ctx.strokeStyle = "#2a313d"; ctx.fillStyle = "#8b97a7";
  ctx.font = "10px sans-serif"; ctx.lineWidth = 1; ctx.textAlign = "left";
  for (let g = 0; g <= 4; g++) {
    const gy = padT + (H * g) / 4, val = hi - ((hi - lo) * g) / 4;
    ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(padL + W, gy); ctx.stroke();
    ctx.fillText("$" + val.toFixed(val < 50 ? 2 : 0), 6, gy + 3);
  }
  for (const s of series) {
    ctx.beginPath(); ctx.strokeStyle = s.color; ctx.lineWidth = s.w;
    let started = false;
    s.data.forEach((v, i) => {
      if (v == null) { started = false; return; }
      const px = x(i), py = y(v);
      if (!started) { ctx.moveTo(px, py); started = true; } else ctx.lineTo(px, py);
    });
    ctx.stroke();
  }
  // date range labels
  ctx.fillStyle = "#8b97a7";
  if (chart.dates) {
    ctx.fillText(fmtDate(chart.dates[0]), padL + 2, cssH - 5);
    ctx.textAlign = "right";
    ctx.fillText(fmtDate(chart.dates[n - 1]), padL + W, cssH - 5);
    ctx.textAlign = "left";
  } else {
    ctx.fillText("last " + n + " sessions", padL + 2, cssH - 5);
  }

  if (hoverIdx == null) return;
  const cx = x(hoverIdx);
  ctx.strokeStyle = "#8b97a7"; ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.moveTo(cx, padT); ctx.lineTo(cx, padT + H); ctx.stroke();
  ctx.setLineDash([]);
  for (const s of series) {
    const v = s.data[hoverIdx]; if (v == null) continue;
    ctx.fillStyle = s.color; ctx.beginPath();
    ctx.arc(cx, y(v), 3, 0, Math.PI * 2); ctx.fill();
  }
  drawTooltip(ctx, geom, chart, series, hoverIdx, cx);
}

function drawTooltip(ctx, geom, chart, series, i, cx) {
  const lines = [{text: chart.dates ? fmtDate(chart.dates[i]) : "Session " + (i + 1),
                  color: "#e7ecf3", bold: true}];
  for (const s of series) {
    const v = s.data[i]; if (v == null) continue;
    lines.push({text: s.label + "   $" + v.toFixed(2), color: s.color});
  }
  let wmax = 0;
  for (const l of lines) {
    ctx.font = (l.bold ? "bold " : "") + "11px sans-serif";
    wmax = Math.max(wmax, ctx.measureText(l.text).width);
  }
  const lh = 15, padBox = 8, boxW = wmax + 18, boxH = lines.length * lh + padBox;
  let bx = cx + 12;
  if (bx + boxW > geom.padL + geom.W) bx = cx - 12 - boxW;
  if (bx < geom.padL) bx = geom.padL + 2;
  const by = geom.padT + 6;
  ctx.fillStyle = "rgba(15,17,22,0.93)"; ctx.strokeStyle = "#2a313d";
  ctx.lineWidth = 1; roundRect(ctx, bx, by, boxW, boxH, 6); ctx.fill(); ctx.stroke();
  let ty = by + padBox + 6;
  for (const l of lines) {
    ctx.font = (l.bold ? "bold " : "") + "11px sans-serif";
    ctx.fillStyle = l.color; ctx.textAlign = "left";
    ctx.fillText(l.text, bx + 9, ty); ty += lh;
  }
}
$("#drawerClose").onclick = () => $("#drawer").classList.add("hidden");
$("#drawer").onclick = (e) => { if (e.target.id === "drawer") $("#drawer").classList.add("hidden"); };

/* ---- watchlist ---- */
async function toggleWatch(ticker, btn) {
  if (watchSet.has(ticker)) {
    await api("/api/watchlist/" + ticker, {method:"DELETE"});
    watchSet.delete(ticker); btn && btn.classList.remove("on");
  } else {
    await api("/api/watchlist", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ticker})});
    watchSet.add(ticker); btn && btn.classList.add("on");
  }
  loadStatus();
}

async function loadWatch() {
  $("#watchMsg").textContent = "Loading…";
  const d = await api("/api/signals");
  const sigs = d.signals || [];
  watchSet = new Set(sigs.map(s => s.ticker));
  if (!sigs.length) { $("#watchList").innerHTML = ""; $("#watchMsg").textContent =
    "No stocks tracked yet. Add some from the Scan tab (★)."; return; }
  $("#watchMsg").textContent = "";
  $("#watchList").innerHTML = sigs.map(s => {
    if (s.error) return `<div class="card"><h3>${s.ticker}</h3><p class="msg">${s.error}</p></div>`;
    const lv = s.signal.levels;
    return `<div class="card">
      <h3>${s.ticker}
        <span><span class="badge ${s.signal.badge}">${s.signal.badge}</span>
        <button class="star on" data-un="${s.ticker}">✕</button></span></h3>
      <div class="msg">Score <b style="color:var(--accent)">${fmt(s.score,0)}</b></div>
      <div class="lv">
        <div><b>Entry</b>$${fmt(lv.entry)}</div>
        <div><b>Stop</b>$${fmt(lv.stop)}</div>
        <div><b>Target</b>$${fmt(lv.target)}</div>
      </div>
      <ul class="reasons">${s.signal.reasons.slice(0,4).map(r=>`<li>• ${r}</li>`).join("")}</ul>
    </div>`;
  }).join("");
  $$("[data-un]").forEach(b => b.onclick = async () => {
    await api("/api/watchlist/" + b.dataset.un, {method:"DELETE"});
    watchSet.delete(b.dataset.un); loadWatch(); loadStatus();
  });
}

/* ---- refresh ---- */
$("#refreshBtn").onclick = async () => {
  const b = $("#refreshBtn"); b.disabled = true; b.textContent = "↻ Refreshing…";
  try { await api("/api/refresh", {method:"POST",
    headers:{"Content-Type":"application/json"}, body:"{}"}); }
  finally { b.disabled = false; b.textContent = "↻ Refresh data"; loadStatus(); }
};

/* ---- ticker lookup ---- */
$("#lookupForm").onsubmit = (e) => {
  e.preventDefault();
  const t = $("#lookupInput").value.trim().toUpperCase();
  if (t) { openDetail(t); $("#lookupInput").blur(); }
};

/* ---- init ---- */
$("#scanBtn").onclick = runScan;
buildWeights();
loadStatus();
api("/api/watchlist").then(d => watchSet = new Set(d.watchlist || []));
