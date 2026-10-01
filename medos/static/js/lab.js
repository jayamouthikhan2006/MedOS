// Scheduler Lab: a discrete-time simulation of the waiting room under four scheduling policies.
import { $, $$, esc, api, createLive, initChrome, hydrateIcons, prio, LEVELS, LEVEL_LABEL } from "./core.js";

initChrome(createLive({ url: "/api/public/state" }));
hydrateIcons();

const BASE = { critical: 1500, high: 600, medium: 300, low: 0 };
const DUR = { critical: 25, high: 15, medium: 10, low: 7 };
const PREEMPT_FLOOR = 1450, EMERGENCY = 5000;
const ALGOS = [
  { id: "fcfs", name: "First come, first served", color: "var(--muted)" },
  { id: "sjf", name: "Shortest job first", color: "var(--info)" },
  { id: "prio", name: "Priority", color: "var(--high)" },
  { id: "aging", name: "Priority + aging", color: "var(--scrub)" },
  { id: "preempt", name: "Pre-emptive + aging", color: "var(--crit)" },
];
const COLORS = { critical: "var(--crit)", high: "var(--high)", medium: "var(--med)", low: "var(--low)" };

function rng(seed) {
  return () => { seed |= 0; seed = (seed + 0x6d2b79f5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

// ------------------------------------------------------------------ scenarios
function randomPatients(n, win, emergencies, seed) {
  const r = rng(seed);
  const out = [];
  for (let i = 0; i < n; i++) {
    const x = r();
    const level = x < 0.08 ? "critical" : x < 0.28 ? "high" : x < 0.6 ? "medium" : "low";
    const rank = level === "critical" ? 0 : level === "high" ? 1 + Math.floor(r() * 3) : level === "medium" ? 4 + Math.floor(r() * 4) : 8 + Math.floor(r() * 8);
    out.push({ arrival: r() * win, level, rank, base: BASE[level] + (16 - rank) * 10, duration: DUR[level] * (0.7 + r() * 0.6), emergency: false });
  }
  for (let k = 0; k < emergencies; k++) {
    out.push({ arrival: win * (0.25 + r() * 0.6), level: "critical", rank: 0, base: BASE.critical + 160, duration: DUR.critical * (0.8 + r() * 0.4), emergency: true });
  }
  out.sort((a, b) => a.arrival - b.arrival);
  out.forEach((p, i) => { p.token = String(i + 1).padStart(3, "0"); });
  return out;
}

// ------------------------------------------------------------------ simulation
function simulate(patients, { doctors, rate, cap, policy }) {
  const dt = 0.25;
  const jobs = patients.map((p, i) => ({ ...p, id: i, remaining: p.duration, start: null, end: null, preempted: false, waited: 0 }));
  const docs = Array.from({ length: doctors }, (_, i) => ({ id: i, job: null, segStart: 0 }));
  const segments = [];
  let waiting = [], next = 0, t = 0, done = 0, preemptions = 0;
  const score = (j) => {
    if (policy === "fcfs") return -j.arrival;
    if (policy === "sjf") return -j.duration;   // shortest consultation first, ignores how sick they are
    let s = j.base;
    if (policy !== "prio") {
      const aging = Math.min(rate * (t - j.arrival), cap);
      s += aging;
      if (j.preempted) s = Math.max(s, PREEMPT_FLOOR + aging * 0.001);
    }
    if (j.emergency && policy !== "fcfs") s += EMERGENCY;
    return s;
  };
  const pick = () => {
    let best = -1, bs = -Infinity;
    waiting.forEach((j, i) => { const s = score(j); if (s > bs || (s === bs && j.arrival < waiting[best].arrival)) { bs = s; best = i; } });
    return waiting.splice(best, 1)[0];
  };
  const guard = 100000;
  let steps = 0;
  while (done < jobs.length && steps++ < guard) {
    while (next < jobs.length && jobs[next].arrival <= t) waiting.push(jobs[next++]);
    for (const d of docs) {
      if (d.job) {
        d.job.remaining -= dt;
        if (d.job.remaining <= 1e-9) {
          d.job.end = t; segments.push({ doc: d.id, job: d.job, from: d.segStart, to: t }); done++; d.job = null;
        }
      }
    }
    if (policy === "preempt") {
      for (const e of waiting.filter((j) => j.emergency)) {
        if (docs.some((d) => !d.job)) break;
        const victims = docs.filter((d) => d.job && !d.job.emergency && d.job.level !== "critical");
        if (!victims.length) break;
        victims.sort((a, b) => score(a.job) - score(b.job));
        const d = victims[0];
        segments.push({ doc: d.id, job: d.job, from: d.segStart, to: t, split: true });
        d.job.preempted = true; waiting.push(d.job); d.job = null; preemptions++;
        void e;
      }
    }
    for (const d of docs) {
      if (!d.job && waiting.length) {
        const j = pick();
        if (j.start == null) j.start = t;
        d.job = j; d.segStart = t;
      }
    }
    t += dt;
  }
  jobs.forEach((j) => { j.waited = Math.max(0, j.end - j.arrival - j.duration); });
  return { jobs, segments, preemptions, makespan: t };
}

function metrics(res) {
  const by = {}; LEVELS.forEach((l) => (by[l] = []));
  res.jobs.forEach((j) => by[j.level].push(j.waited));
  const avg = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
  const all = res.jobs.map((j) => j.waited);
  const firstWait = res.jobs.map((j) => j.start - j.arrival);
  return {
    avg: avg(all), max: Math.max(...all), byLevel: Object.fromEntries(LEVELS.map((l) => [l, avg(by[l])])),
    critFirst: avg(res.jobs.filter((j) => j.level === "critical").map((j) => j.start - j.arrival)),
    critLate: res.jobs.filter((j) => j.level === "critical" && j.start - j.arrival > 5).length,
    lowMax: by.low.length ? Math.max(...res.jobs.filter((j) => j.level === "low").map((j) => j.waited)) : null,
    preemptions: res.preemptions, makespan: res.makespan, firstWait,
  };
}

// ------------------------------------------------------------------ UI
let source = "random", seed = 42, today = null, results = {}, shown = "aging";
const ctl = ["n", "win", "em", "docs", "rate", "cap"];
const fmt = (v, d = 1) => (v == null || !isFinite(v) ? "—" : v.toFixed(d));

async function run() {
  ctl.forEach((k) => ($(`#${k}-o`).textContent = $(`#${k}`).value));
  const opts = { doctors: +$("#docs").value, rate: +$("#rate").value, cap: +$("#cap").value };
  let patients;
  if (source === "today") {
    if (!today) today = await api("lab/today");
    const ps = today.patients;
    if (!ps.length) { $("#summary").textContent = "No patients registered today yet."; patients = []; }
    const t0 = ps.length ? ps[0].arrival : 0;
    patients = ps.map((p) => ({ token: p.token, arrival: p.arrival - t0, level: p.level, rank: p.rank, base: p.base, emergency: p.emergency, duration: p.duration || DUR[p.level] }));
  } else {
    patients = randomPatients(+$("#n").value, +$("#win").value, +$("#em").value, seed);
  }
  if (!patients.length) return;
  results = {};
  for (const a of ALGOS) {
    const res = simulate(patients, { ...opts, policy: a.id });
    results[a.id] = { res, m: metrics(res) };
  }
  const counts = LEVELS.map((l) => `${patients.filter((p) => p.level === l).length} ${LEVEL_LABEL[l].toLowerCase()}`).join(" · ");
  $("#summary").textContent = `${patients.length} patients (${counts}) · ${opts.doctors} doctor${opts.doctors > 1 ? "s" : ""}`;
  renderTable(); renderChart(); renderPicker(); renderGantt();
}

function renderTable() {
  const rows = [
    ["Average wait, everyone", (m) => m.avg, "min", "low"],
    ["Critical: time to a doctor", (m) => m.critFirst, "min", "low"],
    ["Critical patients waiting over 5 min", (m) => m.critLate, "", "low", 0],
    ["Longest wait, anyone", (m) => m.max, "min", "low"],
    ["Longest wait, low priority", (m) => m.lowMax, "min", "low"],
    ["Consultations paused for emergencies", (m) => m.preemptions, "", null, 0],
  ];
  $("#results").innerHTML = `<thead><tr><th></th>${ALGOS.map((a) => `<th><span class="algo-name" style="justify-content:flex-end"><i style="background:${a.color}"></i>${a.name}</span></th>`).join("")}</tr></thead>
    <tbody>${rows.map(([label, f, unit, better, dec = 1]) => {
      const vals = ALGOS.map((a) => f(results[a.id].m));
      const finite = vals.filter((v) => v != null && isFinite(v));
      const best = better ? Math.min(...finite) : null, worst = better ? Math.max(...finite) : null;
      return `<tr><td>${label}</td>${vals.map((v) => `<td class="num ${better && finite.length > 1 && best !== worst ? (v === best ? "best" : v === worst ? "worst" : "") : ""}">${fmt(v, dec)}${unit && v != null ? ` ${unit}` : ""}</td>`).join("")}</tr>`;
    }).join("")}</tbody>`;
}

function renderChart() {
  const max = Math.max(1, ...ALGOS.flatMap((a) => LEVELS.map((l) => results[a.id].m.byLevel[l] || 0)));
  $("#wchart").innerHTML = LEVELS.map((l) => `<div class="wc-col"><div class="wc-bars">${ALGOS.map((a) => {
    const v = results[a.id].m.byLevel[l];
    return `<div style="height:${((v || 0) / max) * 100}%;background:${a.color}" title="${a.name}: ${fmt(v)} min"><span>${v == null ? "" : Math.round(v)}</span></div>`;
  }).join("")}</div><div class="wc-lbl">${prio(l)}</div></div>`).join("");
  $("#wlegend").innerHTML = ALGOS.map((a) => `<span><i style="background:${a.color}"></i>${a.name}</span>`).join("");
}

function renderPicker() {
  $("#algo-pick").innerHTML = ALGOS.map((a) => `<button type="button" data-a="${a.id}" aria-pressed="${a.id === shown}">${a.name}</button>`).join("");
}
$("#algo-pick").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; shown = b.dataset.a; renderPicker(); renderGantt(); });

function renderGantt() {
  const { res } = results[shown];
  const end = Math.max(...res.segments.map((s) => s.to), 1);
  const docs = +$("#docs").value;
  let html = "";
  for (let d = 0; d < docs; d++) {
    html += `<div class="gantt-row"><span class="t-small strong">Doctor ${d + 1}</span><div class="gantt-lane">${res.segments.filter((s) => s.doc === d).map((s) => {
      const left = (s.from / end) * 100, w = Math.max(0.3, ((s.to - s.from) / end) * 100);
      return `<div class="job ${s.split ? "split" : ""}" data-level="${s.job.level}" style="left:${left}%;width:${w}%" title="Token ${esc(s.job.token)} · ${LEVEL_LABEL[s.job.level]}${s.job.emergency ? " · emergency" : ""} · ${s.from.toFixed(0)}–${s.to.toFixed(0)} min${s.split ? " (interrupted)" : ""}">${w > 3 ? esc(s.job.token) : ""}</div>`;
    }).join("")}</div></div>`;
  }
  const ticks = [];
  const step = end > 180 ? 60 : end > 90 ? 30 : 15;
  for (let m = 0; m <= end; m += step) ticks.push(`<span style="left:${(m / end) * 100}%">${m}′</span>`);
  html += `<div class="gantt-axis"><span></span><div>${ticks.join("")}</div></div>`;
  $("#gantt").innerHTML = html;
  $("#gantt-note").textContent = `Each block is one consultation. Striped blocks were interrupted by an emergency and resumed later. Everyone is seen by minute ${Math.round(end)}.`;
}

$("#src").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  source = b.dataset.v;
  $$("#src button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  $$(".rnd").forEach((el) => el.classList.toggle("hidden", source !== "random"));
  $("#run").classList.toggle("hidden", source !== "random");
  today = null; run();
});
ctl.forEach((k) => $(`#${k}`).addEventListener("input", run));
$("#run").addEventListener("click", () => { seed = Math.floor(Math.random() * 1e9); run(); });
run();
