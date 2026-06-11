/* SILENTBREAK — POLYGRAPH front end.
   Vanilla JS, zero external requests. Renders exactly the SSE event schema
   served by app/main.py (see BUILD_SPEC section 7). */
"use strict";

/* ---------------------------------------------------------------- helpers */
const $ = (id) => document.getElementById(id);

const els = {
  body: document.body,
  alias: $("mast-alias"),
  mode: $("badge-mode"),
  engine: $("badge-engine"),
  statusTile: $("status-tile"),
  statusValue: $("status-value"),
  restoreNote: $("restore-note"),
  contraStamp: $("contradiction-stamp"),
  strip: $("strip"),
  reportBody: $("report-body"),
  reportClock: $("report-clock"),
  gateIdle: $("gate-idle"),
  gateLive: $("gate-live"),
  plan: $("plan"),
  gateImpact: $("gate-impact"),
  stamp: $("approve-stamp"),
  ringFg: $("ring-fg"),
  reject: $("reject-btn"),
  imprint: $("imprint"),
  imprintMeta: $("imprint-meta"),
  vetoMark: $("veto-mark"),
  ttLines: $("tt-lines"),
  runBtn: $("run-btn"),
  replayBtn: $("replay-btn"),
  reverseBtn: $("reverse-btn"),
  revBar: $("rev-bar"),
  phase: $("phase-readout"),
};

async function post(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty body */ }
  return { ok: res.ok, status: res.status, data };
}

function setPhase(p) {
  els.phase.textContent = "PHASE: " + p.toUpperCase().replace(/_/g, " ");
  els.body.dataset.phase = p;
}

/* --------------------------------------------------------------- teletype */
const TT_MAX = 200;
function tt(line, cls) {
  const div = document.createElement("div");
  div.className = "tt-line" + (cls ? " " + cls : "");
  div.textContent = line;
  els.ttLines.appendChild(div);
  while (els.ttLines.children.length > TT_MAX) els.ttLines.firstChild.remove();
  els.ttLines.scrollTop = els.ttLines.scrollHeight;
}

/* ------------------------------------------------- typewriter (report) */
const typeQueue = [];
let typing = false;

function typeLine(text, cls, speed) {
  typeQueue.push({ text, cls: cls || "", speed: speed || 14 });
  if (!typing) drainTypeQueue();
}

async function drainTypeQueue() {
  typing = true;
  while (typeQueue.length) {
    const job = typeQueue.shift();
    const span = document.createElement("span");
    span.className = "r-line " + job.cls;
    const caret = document.createElement("span");
    caret.className = "type-caret";
    els.reportBody.appendChild(span);
    els.reportBody.appendChild(caret);
    // Catch-up: the deeper the backlog, the faster the keys strike, so the
    // report never lags the action by more than a couple of seconds.
    const delay = Math.max(2, Math.round(job.speed / (1 + typeQueue.length)));
    for (const ch of job.text) {
      span.textContent += ch;
      els.reportBody.scrollTop = els.reportBody.scrollHeight;
      await new Promise((r) => setTimeout(r, delay));
    }
    caret.remove();
    els.reportBody.scrollTop = els.reportBody.scrollHeight;
  }
  typing = false;
}

function typeRule() {
  const div = document.createElement("div");
  div.className = "r-rule";
  els.reportBody.appendChild(div);
}

/* ------------------------------------------------------------ strip chart */
const METRICS = ["null_rate", "avg_amount", "distinct_sku"];
const chart = { points: { null_rate: [], avg_amount: [], distinct_sku: [] }, breaches: [] };
const ctx = els.strip.getContext("2d");

function chartReset() {
  for (const m of METRICS) chart.points[m] = [];
  chart.breaches = [];
  drawChart();
}

function chartAdd(d) {
  if (!chart.points[d.metric]) return;
  const signed = d.baseline_std > 0 ? (d.value - d.baseline_mean) / d.baseline_std : 0;
  chart.points[d.metric].push({ z: signed, breach: d.breach, absZ: d.z });
  if (d.breach) chart.breaches.push({ metric: d.metric, i: chart.points[d.metric].length - 1, z: d.z });
  drawChart();
}

function drawChart() {
  const dpr = window.devicePixelRatio || 1;
  const W = els.strip.clientWidth, H = els.strip.clientHeight;
  if (els.strip.width !== W * dpr) { els.strip.width = W * dpr; els.strip.height = H * dpr; }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  const laneH = H / 3;
  const n = Math.max(...METRICS.map((m) => chart.points[m].length), 0);
  const left = 118, right = 16;
  const step = n > 1 ? Math.min(64, (W - left - right) / (n - 1)) : 0;

  METRICS.forEach((metric, li) => {
    const top = li * laneH, mid = top + laneH / 2;
    const amp = laneH / 2 - 8;            // full deflection at |z| = 8
    const y = (z) => mid - Math.max(-1, Math.min(1, z / 8)) * amp;

    // baseline band (+-2 sigma) and centre rule
    ctx.fillStyle = "rgba(216,207,188,.5)";
    ctx.fillRect(left, y(2), W - left - right, y(-2) - y(2));
    ctx.strokeStyle = "#D8CFBC";
    ctx.beginPath(); ctx.moveTo(left, mid); ctx.lineTo(W - right, mid); ctx.stroke();
    if (li > 0) {
      ctx.strokeStyle = "#14120F";
      ctx.beginPath(); ctx.moveTo(0, top); ctx.lineTo(W, top); ctx.stroke();
    }

    // lane label
    ctx.fillStyle = "#14120F";
    ctx.font = "10px ui-monospace, Menlo, monospace";
    ctx.fillText(metric.toUpperCase(), 14, top + 16);
    ctx.fillStyle = "rgba(20,18,15,.45)";
    ctx.fillText("baseline ±2σ", 14, top + 30);

    // the needle trace, with a faint pen wobble between samples
    const pts = chart.points[metric];
    if (!pts.length) return;
    ctx.strokeStyle = "#14120F";
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    pts.forEach((p, i) => {
      const x = left + i * step, py = y(p.z);
      if (i === 0) { ctx.moveTo(x, py); return; }
      const x0 = left + (i - 1) * step, y0 = y(pts[i - 1].z);
      for (let s = 1; s <= 6; s++) {
        const t = s / 6;
        const wob = p.breach || pts[i - 1].breach ? 0 : Math.sin((i * 7 + s) * 2.1) * 1.4;
        ctx.lineTo(x0 + (x - x0) * t, y0 + (py - y0) * t + wob);
      }
    });
    ctx.stroke();
    ctx.lineWidth = 1;
  });

  // breach annotations: red vertical line + z label
  for (const b of chart.breaches) {
    const li = METRICS.indexOf(b.metric);
    const x = left + b.i * step;
    ctx.strokeStyle = "#A4282A";
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(x, li * laneH + 4); ctx.lineTo(x, (li + 1) * laneH - 4); ctx.stroke();
    ctx.fillStyle = "#A4282A";
    ctx.font = "bold 11px ui-monospace, Menlo, monospace";
    ctx.fillText("z=" + Math.round(b.z), Math.min(x + 6, W - 60), li * laneH + 18);
    ctx.lineWidth = 1;
  }
}
window.addEventListener("resize", drawChart);

/* ----------------------------------------------------------- hold-to-fire */
function holdToFire(btn, ms, onProgress, onFire, onCancel) {
  let t0 = null, raf = null, active = false;

  const tick = () => {
    onProgress(Math.min(1, (performance.now() - t0) / ms));
    if (active) raf = requestAnimationFrame(tick);
  };
  const start = (e) => {
    if (btn.disabled || active) return;
    e.preventDefault();
    active = true; t0 = performance.now();
    btn.classList.add("held");
    raf = requestAnimationFrame(tick);
  };
  const end = () => {
    if (!active) return;
    active = false;
    cancelAnimationFrame(raf);
    btn.classList.remove("held");
    // Arm on elapsed time, never on animation frames (rAF can be throttled).
    if (performance.now() - t0 >= ms) onFire();
    else { onProgress(0); if (onCancel) onCancel(); }
  };
  btn.addEventListener("pointerdown", start);
  btn.addEventListener("pointerup", end);
  btn.addEventListener("pointerleave", end);
  btn.addEventListener("keydown", (e) => {
    if ((e.key === " " || e.key === "Enter") && !e.repeat) start(e);
  });
  btn.addEventListener("keyup", (e) => {
    if (e.key === " " || e.key === "Enter") end();
  });
}

/* -------------------------------------------------------------- run state */
let currentRunId = null;
let source = null;
let runRemediated = false;
let reversedAlready = false;

function resetPanels() {
  chartReset();
  els.reportBody.textContent = "";
  els.ttLines.textContent = "";
  els.gateIdle.hidden = false;
  els.gateLive.hidden = true;
  els.plan.textContent = "";
  els.gateImpact.textContent = "";
  els.imprint.hidden = true;
  els.vetoMark.hidden = true;
  els.stamp.disabled = false;
  els.stamp.classList.remove("armed", "held");
  els.ringFg.style.strokeDashoffset = 490;
  els.reject.disabled = false;
  els.contraStamp.hidden = true;
  els.restoreNote.hidden = true;
  els.statusValue.textContent = "EXAMINATION IN PROGRESS";
  els.reverseBtn.disabled = true;
  els.replayBtn.hidden = true;
  runRemediated = false;
  reversedAlready = false;
  typeQueue.length = 0;
}

function attach(runId) {
  if (source) source.close();
  currentRunId = runId;
  source = new EventSource("/api/events?run_id=" + encodeURIComponent(runId));
  for (const [type, fn] of Object.entries(handlers)) {
    source.addEventListener(type, (e) => {
      let env;
      try { env = JSON.parse(e.data); } catch (_) { return; }
      fn(env.data || {});
    });
  }
  source.onerror = () => { /* EventSource auto-reconnects; buffer replay makes it safe */ };
}

/* --------------------------------------------------------- event handlers */
const handlers = {
  run_started(d) {
    setPhase("detect");
    els.alias.textContent = d.alias;
    els.reportClock.textContent = "DAY " + d.day;
    tt("> examination opened  run=" + d.run_id + "  mode=" + d.mode + "  engine=" + d.engine, "tt-dim");
    tt("> alias " + d.alias + " -> " + (d.alias_target || "?"), "tt-dim");
  },
  engine(d) {
    if (d.name === "adk") {
      els.engine.textContent = "ADK · " + (d.model || "gemini-3.5-flash");
    } else {
      const noKey = (d.reason || "").toLowerCase().includes("key");
      els.engine.textContent = noKey ? "DETERMINISTIC FALLBACK (no Gemini key)" : "DETERMINISTIC";
    }
    if (d.reason) { els.engine.title = d.reason; tt("> engine: " + d.name + " (" + d.reason + ")", "tt-dim"); }
  },
  pipeline_status(d) {
    els.statusValue.textContent = "RUN " + d.run_number + " · " + d.status + " ✓";
  },
  sentinel_check(d) {
    chartAdd(d);
    if (d.breach) {
      tt("! " + d.metric + "=" + Number(d.value).toFixed(3) +
         "  baseline=" + Number(d.baseline_mean).toFixed(3) +
         "  z=" + Number(d.z).toFixed(1) + "  BREACH", "tt-err");
    }
  },
  contradiction(d) {
    setPhase("diagnose");
    els.contraStamp.hidden = false;
    els.contraStamp.classList.add("slam");
    els.statusTile.classList.add("judder");
    typeLine(d.headline, "r-alarm", 16);
    tt("! CONTRADICTION: pipeline says " + d.pipeline_status + ", data says corrupt (" +
       d.metric + " z=" + Math.round(d.z) + ")", "tt-err");
  },
  diagnosis(d) {
    typeRule();
    typeLine(d.sentence, "", 18);
    for (const f of d.removed_fields) typeLine("- " + f, "r-del", 8);
    for (const f of d.added_fields) typeLine("+ " + f, "r-add", 8);
    typeLine("ESTIMATED EXPOSURE: $" + Number(d.estimated_impact_usd).toLocaleString() +
             "  (" + d.nulled_rows.toLocaleString() + " rows, estimated)", "r-alarm", 10);
  },
  awaiting_approval(d) {
    setPhase("awaiting_approval");
    els.gateIdle.hidden = true;
    els.gateLive.hidden = false;
    els.plan.textContent = "";
    for (const step of d.plan) {
      const li = document.createElement("li");
      li.textContent = step;
      els.plan.appendChild(li);
    }
    els.gateImpact.textContent =
      "EXPOSURE $" + Number(d.estimated_impact_usd).toLocaleString() + " (ESTIMATED) · " +
      d.index + " -> " + d.target_index;
    els.stamp.focus();
  },
  approved(d) {
    setPhase("quarantine");
    els.stamp.disabled = true;
    els.reject.disabled = true;
    els.imprint.hidden = false;
    els.imprintMeta.textContent = "TOKEN " + d.token_id + " · SINGLE USE · " +
      new Date().toISOString().slice(11, 19) + "Z";
    tt("> operator stamp registered — token " + d.token_id + " minted (single use)");
  },
  rejected(d) {
    setPhase("vetoed");
    els.stamp.disabled = true;
    els.reject.disabled = true;
    els.vetoMark.hidden = false;
    tt("> operator REJECTED — no mutation performed (" + (d.reason || "vetoed") + ")", "tt-err");
  },
  guardian_action(d) {
    setPhase("quarantine");
    tt("> " + d.detail);
    if (d.step === "alias_flip") typeLine("ACTION. " + d.detail, "", 8);
  },
  verify(d) {
    setPhase("verify");
    tt("> FROM " + d.alias + " | STATS COUNT(*), AVG(amount)   -> target=" + d.target);
    tt("  rows=" + d.row_count + "  null_rate=" + Number(d.null_rate).toFixed(3) +
       "  avg=" + Number(d.avg_amount).toFixed(2) + "  OK");
  },
  teletype(d) { tt(d.line); },
  incident(d) {
    const doc = d.doc || {};
    typeRule();
    for (const line of String(doc.report || "").split("\n")) typeLine(line, "", 6);
    typeLine("FILED " + (doc.incident_id || "") + " · ENGINE " + (doc.engine || "?") +
             " · REPORT BY " + (doc.report_author || "?"), "r-dim", 6);
  },
  reversed(d) {
    els.restoreNote.hidden = false;
    els.reverseBtn.disabled = true;
    reversedAlready = true;
    setPhase("resolved");
  },
  run_complete(d) {
    const phase = { remediated: "resolved", green: "green" }[d.result] || "vetoed";
    setPhase(phase);
    els.runBtn.disabled = false;
    els.replayBtn.hidden = false;
    if (d.result === "remediated") {
      runRemediated = true;
      if (!reversedAlready) els.reverseBtn.disabled = false;
      tt("> examination closed: REMEDIATED in " + d.duration_ms + "ms");
    } else {
      tt("> examination closed: " + d.result.toUpperCase() +
         (d.reason ? " (" + d.reason + ")" : ""), d.result === "green" ? "" : "tt-err");
    }
  },
  error(d) { tt("! ERROR: " + d.message, "tt-err"); },
};

/* ----------------------------------------------------------- wiring: HITL */
holdToFire(
  els.stamp, 1200,
  (p) => {
    els.ringFg.style.strokeDashoffset = String(490 * (1 - p));
    els.stamp.classList.toggle("armed", p >= 1);
  },
  async () => {
    els.stamp.classList.remove("armed");
    const r = await post("/api/approve", { run_id: currentRunId });
    if (!r.ok) tt("! approve failed: " + JSON.stringify(r.data), "tt-err");
  },
  () => els.stamp.classList.remove("armed"),
);

els.reject.addEventListener("click", async () => {
  const r = await post("/api/reject", { run_id: currentRunId });
  if (!r.ok) tt("! reject failed: " + JSON.stringify(r.data), "tt-err");
});

holdToFire(
  els.reverseBtn, 900,
  (p) => {
    els.revBar.style.width = (p * 100).toFixed(0) + "%";
    els.reverseBtn.classList.toggle("armed", p >= 1);
  },
  async () => {
    els.reverseBtn.classList.remove("armed");
    els.revBar.style.width = "0%";
    const r = await post("/api/reverse", { run_id: currentRunId });
    if (!r.ok) tt("! reverse refused: " + JSON.stringify(r.data), "tt-err");
  },
  () => { els.reverseBtn.classList.remove("armed"); els.revBar.style.width = "0%"; },
);

/* ------------------------------------------------------- wiring: footer */
els.runBtn.addEventListener("click", async () => {
  els.runBtn.disabled = true;
  const r = await post("/api/run", {});
  if (r.status === 202) {
    resetPanels();
    attach(r.data.run_id);
  } else if (r.status === 409 && r.data.run_id) {
    attach(r.data.run_id); // a run is already live; watch it
  } else {
    els.runBtn.disabled = false;
    tt("! run refused: " + JSON.stringify(r.data), "tt-err");
  }
});

els.replayBtn.addEventListener("click", () => {
  if (!currentRunId) return;
  const id = currentRunId;
  resetPanels();
  els.runBtn.disabled = true;
  attach(id); // EventBus replays the whole buffer
  els.replayBtn.hidden = false;
});

/* ------------------------------------------------------------------ boot */
(async function boot() {
  drawChart();
  try {
    const res = await fetch("/api/state");
    const s = await res.json();
    els.mode.textContent = s.mode === "mock" ? "MOCK (IN-MEMORY ES)" : s.mode.toUpperCase();
    els.engine.textContent = s.engine === "adk"
      ? "ADK · GEMINI" : "DETERMINISTIC";
    els.alias.textContent = s.alias || "revenue_current";
    if (s.active_run_id) {
      resetPanels();
      attach(s.active_run_id);
    } else {
      els.runBtn.disabled = false;
    }
  } catch (err) {
    tt("! cannot reach /api/state — is the server running? (" + err + ")", "tt-err");
    els.runBtn.disabled = false;
  }
})();
