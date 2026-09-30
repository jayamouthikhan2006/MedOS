import {
  $, $$, esc, icon, api, createLive, initChrome, toast, toastError, modal, confirmDialog, drawer, debounce,
  prio, tokenChip, LEVELS, LEVEL_LABEL, LEVEL_TARGET, fmtDur, fmtClock, serverNow, ageSex, sourceLabel, hydrateIcons,
} from "./core.js";
import { voiceCapabilities, preferredModes, listen, MODE_LABEL } from "./voice.js";

const store = createLive();
initChrome(store);

// ================================================================== summary stats
function renderStats(s) {
  const st = s.stats, by = st.waiting_by_level;
  const urgent = by.critical + by.high;
  const avg = st.avg_wait_today ?? st.avg_wait_now;
  $("#stats").innerHTML = [
    stat("users", "Waiting now", st.queue_length, "", urgent ? `${urgent} critical or high` : "No urgent cases waiting", urgent > 0 && by.critical > 0),
    stat("stetho", "With a doctor", st.in_consultation, "", `${st.completed_today} seen today`),
    stat("check", "Doctors free", st.doctors_available, `of ${st.doctors_on_duty} on duty`, st.doctors_available ? "Next patient goes in now" : "All doctors are busy"),
    stat("clock", "Average wait", avg == null ? "—" : Math.round(avg / 60), avg == null ? "" : "min", "From arrival to seeing a doctor"),
  ].join("");
}
function stat(ic, label, value, unit, hint, alert = false) {
  return `<div class="stat ${alert ? "alert" : ""}"><span class="label">${icon(ic)}${label}</span>
    <span class="value">${value}${unit ? `<small>${unit}</small>` : ""}</span><span class="hint">${hint}</span></div>`;
}

// ================================================================== registration form
const F = {
  name: $("#f-name"), age: $("#f-age"), phone: $("#f-phone"), symptoms: $("#f-symptoms"),
  temp: $("#v-temp"), spo2: $("#v-spo2"), pulse: $("#v-pulse"), bp: $("#v-bp"), pain: $("#v-pain"),
  preg: $("#f-preg"), override: $("#f-override"),
};
let sex = "", source = "reception";
const form = $("#reg");

$$("#f-sex button").forEach((b) => b.addEventListener("click", () => {
  sex = sex === b.dataset.v ? "" : b.dataset.v;
  $$("#f-sex button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.v === sex)));
  preview();
}));
F.pain.addEventListener("input", () => { $("#pain-val").textContent = F.pain.value < 0 ? "not asked" : `${F.pain.value} out of 10`; });

function vitals() {
  const v = {};
  if (F.temp.value) v.temp = F.temp.value;
  if (F.spo2.value) v.spo2 = F.spo2.value;
  if (F.pulse.value) v.pulse = F.pulse.value;
  if (F.bp.value) v.bp_sys = F.bp.value;
  if (+F.pain.value >= 0) v.pain = F.pain.value;
  return v;
}
function payload() {
  return { name: F.name.value.trim(), age: F.age.value.trim(), sex, phone: F.phone.value.trim(), symptoms: F.symptoms.value.trim(),
    vitals: vitals(), pregnant: F.preg.checked, level_override: F.override.value || undefined, source };
}
function fill(el, value) {
  if (value == null || value === "") return;
  el.value = value; el.classList.remove("filled"); void el.offsetWidth; el.classList.add("filled");
}
function resetForm() {
  form.reset(); sex = ""; source = "reception"; aiOpinion = null;
  $$("#f-sex button").forEach((x) => x.setAttribute("aria-pressed", "false"));
  $("#pain-val").textContent = "not asked";
  $("#transcript").classList.add("hidden");
  $("#parser-chip").classList.add("hidden");
  $("#sym-err").classList.add("hidden");
  renderTriage(null);
}

// ------------------------------------------------------------------ live priority preview
let lastTriage = null, aiOpinion = null;
const preview = debounce(async () => {
  const p = payload();
  aiOpinion = null;
  if (!p.symptoms && !Object.keys(p.vitals).length) return renderTriage(null);
  try { renderTriage(await api("triage/preview", { method: "POST", body: p })); } catch { /* keep last */ }
}, 250);
form.addEventListener("input", (e) => { if (e.target !== F.override) { preview(); if (e.target === F.symptoms && F.symptoms.value.trim()) $("#sym-err").classList.add("hidden"); } });
F.override.addEventListener("change", () => renderTriage(lastTriage));

function renderTriage(t) {
  lastTriage = t;
  const box = $("#triage");
  if (!t) {
    box.dataset.level = "";
    box.innerHTML = `<p class="muted">The priority appears here as soon as you type the symptoms.</p>`;
    return;
  }
  const chosen = F.override.value;
  box.dataset.level = chosen || t.level;
  const flags = new Set(t.red_flags || []);
  box.innerHTML = `
    <div class="row wrap gap-8">${prio(t.level, { lg: true })}<span class="text-2 strong">${LEVEL_TARGET[t.level]}</span></div>
    <p class="mt-8"><b>${esc(t.primary_condition || "")}</b>${t.department ? `<span class="muted"> · ${esc(t.department)}</span>` : ""}</p>
    ${flags.size ? `<div class="row wrap gap-4 mt-8">${[...flags].map((r) => `<span class="badge warn">${icon("octagon")}${esc(r)}</span>`).join("")}</div>` : ""}
    <details class="why mt-8"><summary>Why this priority</summary><ul class="reasons">${(t.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("")}</ul></details>
    ${chosen && chosen !== t.level ? `<p class="t-small mt-8"><b>You chose ${LEVEL_LABEL[chosen]}.</b> That will be used instead.</p>` : ""}
    <div class="mt-16" id="ai-box">${aiOpinion ? aiHtml(aiOpinion) : `<button type="button" class="btn btn-sm" id="ask-ai">${icon("spark")}Ask AI for a second opinion</button>`}</div>`;
  const ask = $("#ask-ai"); if (ask) ask.addEventListener("click", askAi);
}
function aiHtml(a) {
  if (a.loading) return `<div class="ai-box row gap-8"><div class="spinner"></div><span>The AI is reading the case. This takes about 5 seconds.</span></div>`;
  if (a.error) return `<div class="ai-box"><p class="error-text">${esc(a.error)}</p></div>`;
  const higher = a.level && lastTriage && LEVELS.indexOf(a.level) < LEVELS.indexOf(lastTriage.level);
  return `<div class="ai-box">
    <div class="ai-title">${icon("spark")}AI second opinion: ${LEVEL_LABEL[a.level] || "no opinion"}</div>
    <p class="t-small mt-8">${esc(a.summary || "")}</p>
    <p class="t-small muted mt-4">${esc(a.reasoning || "")}</p>
    ${higher ? `<button type="button" class="btn btn-sm mt-8" id="use-ai">Use ${LEVEL_LABEL[a.level]}</button>` : `<p class="t-small muted mt-4">It agrees with the automatic priority or rates it lower. The automatic priority stays.</p>`}
  </div>`;
}
async function askAi() {
  aiOpinion = { loading: true }; $("#ai-box").innerHTML = aiHtml(aiOpinion);
  try { aiOpinion = (await api("triage/ai", { method: "POST", body: payload() })).ai; }
  catch (e) { aiOpinion = { error: e.message }; }
  $("#ai-box").innerHTML = aiHtml(aiOpinion);
  const use = $("#use-ai");
  if (use) use.addEventListener("click", () => { F.override.value = aiOpinion.level; renderTriage(lastTriage); toast(`Priority set to ${LEVEL_LABEL[aiOpinion.level]}`); });
}
renderTriage(null);

// ------------------------------------------------------------------ submit
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const p = payload();
  if (!p.symptoms) { $("#sym-err").classList.remove("hidden"); F.symptoms.focus(); return; }
  const btn = $("#reg-btn"); btn.disabled = true;
  const label = btn.lastChild.textContent; btn.lastChild.textContent = "Registering…";
  try { const r = await api("patients", { method: "POST", body: p }); resetForm(); showTicket(r); }
  catch (err) { toastError(err); }
  finally { btn.disabled = false; btn.lastChild.textContent = label; }
});

function showTicket(r) {
  const p = r.patient;
  const url = `${location.origin}/p/${p.code}`;
  const called = r.status === "in_consultation";
  $("#slip").innerHTML = `
    <div class="s-h">${esc(document.querySelector(".brand span span")?.textContent || "")}</div>
    <div class="s-tok">${esc(p.token)}</div>
    <div class="s-row"><span>Priority</span><b>${LEVEL_LABEL[p.level]}</b></div>
    <div class="s-row"><span>Arrived</span><b>${fmtClock(p.arrived_at)}</b></div>
    <div class="s-row"><span>${called ? "Go to" : "Place in queue"}</span><b>${called ? esc(r.room || "") : r.position}</b></div>
    <img class="s-qr" src="/api/qr?data=${encodeURIComponent(url)}" alt="">
    <div class="s-f">Scan to follow your place in the queue<br>${esc(url)}</div>`;
  modal({
    title: `Registered: token ${p.token}`,
    body: `<div class="ticket">
        ${tokenChip(p.token, p.level)}
        <div class="grow">
          <p class="strong">${esc(p.name || "Patient")}</p>
          <div class="row wrap gap-8 mt-4">${prio(p.level)}<span class="t-small muted">${LEVEL_TARGET[p.level]}</span></div>
          <dl class="kv mt-16">${called ? `<dt>Go to</dt><dd>${esc(r.room || "the desk")} now</dd><dt>Doctor</dt><dd>${esc(r.doctor || "")}</dd>`
            : `<dt>Place in queue</dt><dd>${r.position}</dd><dt>Expected wait</dt><dd>${r.eta == null ? "No doctor on duty yet" : fmtDur(r.eta)}</dd>`}</dl>
        </div>
        <img class="ticket-qr" src="/api/qr?data=${encodeURIComponent(url)}" alt="QR code for following this token" onerror="this.remove()">
      </div>
      <p class="t-small muted mt-16">Give the patient the printed slip. Scanning the code shows their place in the queue on their phone.</p>`,
    actions: [{ label: "Print slip", icon: "print", onClick: () => { window.print(); return false; } }, { label: "Done", kind: "primary" }],
  });
}

// ================================================================== voice
let mode = null, session = null;
(async () => {
  const caps = await voiceCapabilities();
  const modes = preferredModes(caps);
  const seg = $("#voice-src");
  if (!modes.length) {
    $("#voice-btn").disabled = true;
    seg.remove();
    $("#voice-help").textContent = "Voice needs a microphone: plug a USB microphone into the Pi, or open MedOS over HTTPS on this device.";
    return;
  }
  mode = modes[0];
  if (modes.length > 1) {
    seg.innerHTML = modes.map((m) => `<button type="button" data-m="${m}" aria-pressed="${m === mode}">${MODE_LABEL[m]}</button>`).join("");
    seg.addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; mode = b.dataset.m; $$("button", seg).forEach((x) => x.setAttribute("aria-pressed", String(x === b))); });
  } else seg.remove();
})();

$("#voice-btn").addEventListener("click", async () => {
  const btn = $("#voice-btn"), tr = $("#transcript");
  if (session) { session.stop(); return; }
  tr.classList.remove("hidden");
  tr.innerHTML = `<span class="rec-dot" aria-hidden="true"></span><span class="muted">Listening. Speak now, then pause when you're done.</span>`;
  btn.classList.add("recording"); btn.lastChild.textContent = "Stop listening";
  session = listen({ mode, lang: store.state?.settings?.voice_lang || "en-IN", onPartial: (t) => { tr.innerHTML = `<span class="rec-dot" aria-hidden="true"></span><span>${esc(t)}</span>`; } });
  try {
    const r = await session.promise;
    tr.innerHTML = `${icon("check")}<span>Heard: “${esc(r.text)}”</span>`;
    const f = r.fields || {};
    fill(F.name, f.name); fill(F.age, f.age); fill(F.phone, f.phone); fill(F.symptoms, f.symptoms || r.text);
    if (["male", "female", "other"].includes(f.sex)) { sex = f.sex; $$("#f-sex button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.v === sex))); }
    if (f.pregnant) F.preg.checked = true;
    source = "voice";
    const chip = $("#parser-chip");
    chip.className = `badge ${r.parser === "ai" ? "info" : "teal"}`;
    chip.innerHTML = r.parser === "ai" ? `${icon("spark")}Filled in by AI` : `${icon("mic")}Filled in from speech`;
    preview();
  } catch (e) {
    tr.innerHTML = `${icon("info")}<span class="error-text">${esc(e.message)}</span>`;
  } finally {
    session = null; btn.classList.remove("recording"); btn.lastChild.textContent = "Fill in by voice";
  }
});

// ================================================================== emergency hold button
(function holdButton() {
  const b = $("#emergency-hold");
  let t = null;
  const start = (e) => {
    if (e.type === "keydown" && (e.repeat || (e.key !== "Enter" && e.key !== " "))) return;
    e.preventDefault(); b.classList.add("holding"); t = setTimeout(fire, 900);
  };
  const cancel = () => { clearTimeout(t); b.classList.remove("holding"); };
  async function fire() {
    b.classList.remove("holding");
    try {
      const p = await api("emergency", { method: "POST", body: { source: "reception" } });
      toast(`Emergency raised: token ${p.token} is ${p.status === "in_consultation" ? "going to a doctor now" : "first in line"}.`, "alarm", 6000);
      emergencyDetails(p);
    } catch (e) { toastError(e); }
  }
  b.addEventListener("pointerdown", start); b.addEventListener("keydown", start);
  ["pointerup", "pointerleave", "pointercancel", "keyup", "blur"].forEach((ev) => b.addEventListener(ev, cancel));
  b.addEventListener("contextmenu", (e) => e.preventDefault());
})();

function emergencyDetails(p) {
  modal({
    title: `Emergency token ${p.token}: add details`,
    body: `<p class="text-2">The patient is already first. Add what you know now, or do it later from the queue.</p>
      <div class="fields mt-16">
        <div class="field span-4"><label for="em-name">Name</label><input class="input" id="em-name"></div>
        <div class="field span-2"><label for="em-age">Age</label><input class="input" id="em-age" inputmode="numeric"></div>
        <div class="field span-6"><label for="em-sym">What happened</label><textarea class="textarea" id="em-sym"></textarea></div>
      </div>`,
    actions: [{ label: "Later", kind: "ghost", value: false }, { label: "Save details", kind: "primary", onClick: async (m) => {
      const body = {};
      const n = $("#em-name", m).value.trim(), a = $("#em-age", m).value.trim(), s = $("#em-sym", m).value.trim();
      if (n) body.name = n; if (a) body.age = a; if (s) body.symptoms = s;
      if (Object.keys(body).length) { await api(`patients/${p.id}`, { method: "PATCH", body }); toast("Details saved"); }
    } }],
  });
}

// ================================================================== patient lists
let tab = "waiting", filter = "all", query = "";
const known = new Set();
let firstRender = true;

$$(".q-tabs button").forEach((b) => b.addEventListener("click", () => selectTab(b.dataset.tab)));
function selectTab(name) {
  tab = name;
  $$(".q-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  ["waiting", "consulting", "done"].forEach((t) => { $(`#p-${t}`).hidden = t !== name; });
  if (name === "done") loadDone();
}
$("#q-search").addEventListener("input", (e) => { query = e.target.value.trim().toLowerCase(); renderQueue(store.state); });
$("#q-filter").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; filter = b.dataset.f; renderQueue(store.state); });

function flagsHtml(p) {
  const f = [];
  if (p.emergency) f.push(`<span class="badge warn">${icon("siren")}Emergency</span>`);
  if (p.preempted) f.push(`<span class="badge warn">${icon("refresh")}Paused for an emergency, goes next</span>`);
  if (p.level_source === "ai") f.push(`<span class="badge info">${icon("spark")}Priority raised by AI</span>`);
  if (p.level_source === "manual" && !p.emergency) f.push(`<span class="badge">Priority set by staff</span>`);
  if (p.ai_status === "pending") f.push(`<span class="badge info"><span class="spinner" style="width:12px;height:12px;border-width:2px"></span>AI checking</span>`);
  return f.length ? `<div class="flags">${f.join("")}</div>` : "";
}

function renderQueue(s) {
  if (!s) return;
  const by = s.stats.waiting_by_level;
  $("#q-filter").innerHTML = [["all", "All", s.queue.length], ...LEVELS.map((l) => [l, LEVEL_LABEL[l], by[l]])]
    .map(([k, label, n]) => `<button type="button" data-f="${k}" aria-pressed="${filter === k}">${label} <span class="muted num">${n}</span></button>`).join("");
  let q = s.queue;
  if (filter !== "all") q = q.filter((p) => p.level === filter);
  if (query) q = q.filter((p) => (p.name || "").toLowerCase().includes(query) || p.token.includes(query));
  const now = serverNow();
  const head = `<div class="list-head" aria-hidden="true"><span>#</span><span>Token</span><span>Patient and complaint</span><span>Priority</span><span>Waiting</span><span>Doctor in</span><span></span></div>`;
  const rows = q.map((p) => `
    <div class="prow ${p.emergency ? "emergency" : ""} ${!firstRender && !known.has(p.id) ? "fresh" : ""}" data-level="${p.level}" data-id="${p.id}" role="button" tabindex="0" aria-label="Token ${esc(p.token)}, ${esc(p.name || "unnamed")}, ${LEVEL_LABEL[p.level]} priority. Open details.">
      <span class="pos">${p.position}</span>
      <span class="tok-cell">${tokenChip(p.token, p.level)}</span>
      <span class="who-cell"><b>${esc(p.name || "Name not given")}</b><span class="line">${esc([ageSex(p), p.primary_condition].filter(Boolean).join(" · "))}</span>${flagsHtml(p)}</span>
      <span class="prio-cell">${prio(p.level)}</span>
      <span class="wait-cell time">${fmtDur(now - p.arrived_at, { short: true })}<span>waiting</span></span>
      <span class="eta-cell time">${p.eta == null ? "—" : p.position === 1 && p.eta < 45 ? "Next" : "~" + fmtDur(p.eta, { short: true })}<span>${p.eta == null ? "no doctor on duty" : "expected"}</span></span>
      <span class="act"><span class="btn btn-sm btn-ghost">Details</span></span>
    </div>`).join("");
  $("#queue").innerHTML = q.length ? head + rows : `<div class="list-empty">${icon("users")}<b>${s.queue.length ? "No one matches" : "No one is waiting"}</b><span>${s.queue.length ? "Clear the search or choose All." : "New patients appear here in the order they'll be seen."}</span></div>`;
  s.queue.forEach((p) => known.add(p.id));
  firstRender = false;
}

function renderConsulting(s) {
  const now = serverNow();
  const c = s.consulting;
  $("#consulting").innerHTML = c.length ? `<div class="list-head cons" aria-hidden="true"><span>Token</span><span>Patient</span><span>Doctor and room</span><span>In room</span></div>` + c.map((p) => `
    <div class="prow cons" data-level="${p.level}" data-id="${p.id}" role="button" tabindex="0" aria-label="Token ${esc(p.token)} with ${esc(p.doctor || "a doctor")}. Open details.">
      <span class="tok-cell">${tokenChip(p.token, p.level)}</span>
      <span class="who-cell"><b>${esc(p.name || "Name not given")}</b><span class="line">${prio(p.level)} ${esc(p.primary_condition || "")}</span></span>
      <span class="who-cell"><b>${esc(p.doctor || "")}</b><span class="line">${esc(p.room || "Consultation desk")}</span></span>
      <span class="time">${fmtDur(now - p.called_at, { short: true })}<span>since called</span></span>
    </div>`).join("") : `<div class="list-empty">${icon("stetho")}<b>No one is with a doctor</b><span>Patients move here when a doctor calls them.</span></div>`;
}

let doneRows = [];
const loadDone = debounce(async () => {
  try { doneRows = await api("patients"); } catch { return; }
  const done = doneRows.filter((p) => ["completed", "cancelled", "no_show"].includes(p.status));
  $("#c-done").textContent = done.length;
  const label = { completed: (p) => p.outcome || "Treated", cancelled: () => "Left before being seen", no_show: () => "Did not come when called" };
  $("#done").innerHTML = done.length ? `<div class="list-head done" aria-hidden="true"><span>Token</span><span>Patient</span><span>Priority</span><span>Waited</span><span>Result</span></div>` + done.map((p) => `
    <div class="prow done" data-level="${p.level}">
      <span class="tok-cell">${tokenChip(p.token, p.level)}</span>
      <span class="who-cell"><b>${esc(p.name || "Name not given")}</b><span class="line">${esc(p.doctor || "")}${p.completed_at ? " · " + fmtClock(p.completed_at) : ""}</span></span>
      <span>${prio(p.level)}</span>
      <span class="time">${p.wait_seconds == null ? "—" : fmtDur(p.wait_seconds, { short: true })}</span>
      <span class="t-small">${esc(label[p.status](p))}</span>
    </div>`).join("") : `<div class="list-empty">${icon("clipboard")}<b>No finished visits yet today</b></div>`;
}, 400);

function render(s) {
  if (!s) return;
  renderStats(s);
  $("#c-waiting").textContent = s.queue.length;
  $("#c-consulting").textContent = s.consulting.length;
  renderQueue(s);
  renderConsulting(s);
  if (tab === "done") loadDone(); else $("#c-done").textContent = s.stats.completed_today + s.stats.left_today;
}
store.subscribe(render);
setInterval(() => render(store.state), 30000);

// ================================================================== patient details panel
function openRow(e) {
  const row = e.target.closest(".prow[data-id]"); if (!row) return;
  if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return;
  e.preventDefault();
  const id = +row.dataset.id;
  const p = [...store.state.queue, ...store.state.consulting].find((x) => x.id === id);
  if (p) openPatient(p);
}
["click", "keydown"].forEach((ev) => { $("#queue").addEventListener(ev, openRow); $("#consulting").addEventListener(ev, openRow); });

function openPatient(p) {
  const v = p.vitals || {};
  const waiting = p.status === "waiting";
  const vit = [["Temperature", v.temp ? `${v.temp}°F` : null], ["Oxygen (SpO₂)", v.spo2 ? `${v.spo2}%` : null], ["Pulse", v.pulse ? `${v.pulse} per min` : null], ["Blood pressure", v.bp_sys ? `${v.bp_sys} systolic` : null], ["Pain", v.pain != null ? `${v.pain} out of 10` : null]].filter((x) => x[1]);
  const sc = p.score;
  const where = waiting
    ? `Place <b>${p.position}</b> in the queue · waiting ${fmtDur(serverNow() - p.arrived_at)} · ${p.eta == null ? "no doctor on duty" : "expected in about " + fmtDur(p.eta)}`
    : `With <b>${esc(p.doctor || "a doctor")}</b> in ${esc(p.room || "the consultation desk")} since ${fmtClock(p.called_at)}`;
  const d = drawer({
    title: p.name || "Name not given",
    subtitle: esc([ageSex(p), p.phone].filter(Boolean).join(" · ")),
    head: tokenChip(p.token, p.level),
    body: `
      <section class="stack-8">
        <div class="row wrap gap-8">${prio(p.level, { lg: true })}<span class="strong text-2">${LEVEL_TARGET[p.level]}</span></div>
        <p class="t-small text-2">${where}</p>
        ${flagsHtml(p)}
      </section>
      <section><h3 class="t-h3">Symptoms</h3><blockquote class="quote mt-8">${esc(p.symptoms || "Not recorded")}</blockquote></section>
      ${vit.length ? `<section><h3 class="t-h3">Vital signs</h3><dl class="kv mt-8">${vit.map(([k, val]) => `<dt>${k}</dt><dd>${esc(val)}</dd>`).join("")}</dl></section>` : ""}
      <section><h3 class="t-h3">Why this priority</h3><p class="t-small mt-4"><b>${esc(p.primary_condition || "")}</b></p><ul class="reasons mt-4">${(p.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
        ${sc ? `<details class="why mt-8"><summary>How the queue order is worked out</summary>
          <dl class="kv mt-8"><dt>Priority points</dt><dd>${Math.round(sc.base)}</dd><dt>Waiting bonus</dt><dd>+${Math.round(sc.aging)}</dd>
          ${sc.preempt ? `<dt>Paused for emergency</dt><dd>+${Math.round(sc.preempt)}</dd>` : ""}${sc.emergency ? `<dt>Emergency</dt><dd>+${Math.round(sc.emergency)}</dd>` : ""}
          <dt>Total</dt><dd>${Math.round(sc.total)}</dd></dl>
          <p class="helper mt-8">Higher totals see a doctor first. The waiting bonus grows every minute, so nobody waits forever, but it can never lift anyone above a Critical patient.</p></details>` : ""}
      </section>
      ${p.ai_status === "done" && p.ai_level ? `<section class="ai-box"><div class="ai-title">${icon("spark")}AI second opinion: ${LEVEL_LABEL[p.ai_level]}</div><p class="t-small mt-8">${esc(p.ai_summary || "")}</p><p class="t-small muted mt-4">${esc(p.ai_reasoning || "")}</p></section>`
        : p.ai_status === "error" ? `<p class="t-small muted">The AI couldn't review this case: ${esc(p.ai_reasoning || "")}</p>` : ""}
      ${waiting ? `<details class="disclose"><summary><span data-icon="edit"></span>Edit details or change priority<span class="chev" data-icon="chevronDown"></span></summary>
        <div class="disclose-body fields">
          <div class="field span-4"><label for="e-name">Name</label><input class="input" id="e-name" value="${esc(p.name || "")}"></div>
          <div class="field span-2"><label for="e-age">Age</label><input class="input" id="e-age" inputmode="numeric" value="${esc(p.age ?? "")}"></div>
          <div class="field span-6"><label for="e-phone">Phone</label><input class="input" id="e-phone" type="tel" value="${esc(p.phone || "")}"></div>
          <div class="field span-6"><label for="e-sym">Symptoms</label><textarea class="textarea" id="e-sym">${esc(p.symptoms || "")}</textarea></div>
          <div class="field span-2"><label for="e-temp">Temp °F</label><input class="input" id="e-temp" value="${esc(v.temp ?? "")}"></div>
          <div class="field span-2"><label for="e-spo2">SpO₂ %</label><input class="input" id="e-spo2" value="${esc(v.spo2 ?? "")}"></div>
          <div class="field span-2"><label for="e-pulse">Pulse</label><input class="input" id="e-pulse" value="${esc(v.pulse ?? "")}"></div>
          <div class="field span-6"><label for="e-level">Priority</label><select class="select" id="e-level"><option value="">Keep ${LEVEL_LABEL[p.level]}</option>${LEVELS.map((l) => `<option value="${l}">Change to ${LEVEL_LABEL[l]}</option>`).join("")}</select></div>
          <div class="span-6 row wrap gap-8"><button class="btn btn-primary" type="button" data-save>${icon("check")}Save and re-check priority</button><button class="btn" type="button" data-ai>${icon("spark")}Ask AI again</button></div>
        </div></details>` : ""}
      <p class="t-small muted">Registered at ${fmtClock(p.arrived_at)} by ${esc(sourceLabel(p.source))}. Patient's tracking link: <a href="/p/${esc(p.code)}" target="_blank" rel="noopener">/p/${esc(p.code)}</a></p>`,
    actions: waiting ? [
      ...(p.emergency ? [] : [{ label: "Mark as emergency", icon: "siren", kind: "danger-outline", onClick: async () => {
        if (!(await confirmDialog(`Make token ${p.token} an emergency?`, "They go straight to the front. If every doctor is busy, the least urgent consultation is paused. The red light and buzzer switch on.", { confirm: "Mark as emergency", danger: true }))) return false;
        await api(`patients/${p.id}/emergency`, { method: "POST" }); toast(`Token ${p.token} is now first in line`, "alarm");
      } }]),
      { label: "Remove from queue", icon: "x", kind: "ghost", onClick: async () => {
        if (!(await confirmDialog(`Remove token ${p.token}?`, "Use this when the patient left without being seen. It can't be undone.", { confirm: "Remove", danger: true }))) return false;
        await api(`patients/${p.id}/cancel`, { method: "POST", body: { reason: "Left without being seen" } }); toast(`Token ${p.token} removed from the queue`);
      } },
    ] : [],
  });
  hydrateIcons(d.el);
  const save = d.el.querySelector("[data-save]");
  if (save) save.addEventListener("click", async () => {
    const q = (id) => d.el.querySelector(id).value;
    const body = { name: q("#e-name"), age: q("#e-age"), phone: q("#e-phone"), symptoms: q("#e-sym"),
      vitals: { ...v, temp: q("#e-temp"), spo2: q("#e-spo2"), pulse: q("#e-pulse") } };
    if (q("#e-level")) body.level_override = q("#e-level");
    save.disabled = true;
    try { const r = await api(`patients/${p.id}`, { method: "PATCH", body }); toast(`Saved. Token ${r.token} is ${LEVEL_LABEL[r.level]} priority.`); d.close(); }
    catch (err) { toastError(err); save.disabled = false; }
  });
  const ai = d.el.querySelector("[data-ai]");
  if (ai) ai.addEventListener("click", async () => { try { await api(`patients/${p.id}/ai`, { method: "POST" }); toast("Sent to the AI for review. The result appears on the queue."); } catch (err) { toastError(err); } });
}

// Keyboard: "/" finds a patient, "n" starts a new registration
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, select, [contenteditable], .drawer, .modal")) return;
  if (e.key === "/") { e.preventDefault(); selectTab("waiting"); $("#q-search").focus(); }
  if (e.key === "n") { e.preventDefault(); F.name.focus(); }
});
