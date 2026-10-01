import {
  $, $$, esc, icon, api, createLive, initChrome, toast, toastError, confirmDialog, prio, tokenChip,
  LEVEL_LABEL, LEVEL_TARGET, fmtDur, fmtClock, serverNow, ageSex, aiText, chime, siren, hydrateIcons,
} from "./core.js";

const KEY = "medos-doctor-id";
let myId = null;
try { myId = +localStorage.getItem(KEY) || null; } catch { /* private mode */ }

const store = createLive({
  onEvent(kind, data) {
    if (!myId) return;
    if (kind === "called" && data.doctor_id === myId) {
      chime();
      if (!data.recall) toast(`Token ${data.token} is on the way to your room`, data.emergency ? "alarm" : "ok");
    }
    if (kind === "preempted" && data.doctor_id === myId) {
      siren(true); setTimeout(() => siren(false), 3500);
      const n = $("#preempt");
      n.innerHTML = `${icon("siren")}<div class="grow"><b>Emergency: token ${esc(data.token)} is coming to you now.</b><br>Token ${esc(data.victim_token)} has been paused and will be seen next by the first free doctor.</div><button class="btn btn-sm" type="button">Understood</button>`;
      n.classList.remove("hidden");
      n.querySelector("button").onclick = () => { n.classList.add("hidden"); siren(false); };
    }
  },
});
initChrome(store);

const STATUS_TEXT = { available: "Available", busy: "With a patient", break: "On a break", off_duty: "Off duty" };
const initials = (n) => n.replace(/^Dr\.?\s*/i, "").split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

// ------------------------------------------------------------------ choose doctor
function showPicker(s) {
  $("#work").classList.add("hidden");
  $("#picker").classList.remove("hidden");
  $("#doc-list").innerHTML = s.doctors.map((d) => `
    <button class="doc-card" type="button" data-id="${d.id}">
      <span class="avatar" aria-hidden="true">${esc(initials(d.name))}</span>
      <span class="grow"><b>${esc(d.name)}</b><span>${esc(d.specialty)} · ${esc(d.room || "No room")}</span></span>
      <span class="badge"><span class="dot ${d.status}"></span>${STATUS_TEXT[d.status]}</span>
    </button>`).join("") || `<div class="empty">${icon("users")}<b>No doctors yet</b><span>An admin can add doctors in Admin, Doctors and rooms.</span></div>`;
}
$("#doc-list").addEventListener("click", (e) => {
  const b = e.target.closest("[data-id]"); if (!b) return;
  myId = +b.dataset.id;
  try { localStorage.setItem(KEY, myId); } catch { /* ignore */ }
  lastKey = null; render(store.state);
});
$("#doc-switch").addEventListener("click", (e) => {
  const b = e.target.closest("[data-id]"); if (!b) return;
  myId = +b.dataset.id; try { localStorage.setItem(KEY, myId); } catch { /* ignore */ }
  lastKey = null; render(store.state);
});
$("#switch-doc").addEventListener("click", () => { myId = null; try { localStorage.removeItem(KEY); } catch { /* ignore */ } showPicker(store.state); });

// ------------------------------------------------------------------ workspace
let lastKey = null;
const drafts = {}, medDrafts = {};
let outcome = "Prescribed";

function render(s) {
  if (!s) return;
  const d = myId && s.doctors.find((x) => x.id === myId);
  if (!d) return showPicker(s);
  $("#doc-switch").innerHTML = s.doctors.map((x) => `<button type="button" class="doc-tab" data-id="${x.id}" aria-pressed="${x.id === myId}">
      <span class="dot ${x.status}" aria-hidden="true"></span><span><b>${esc(x.name)}</b><small>${x.current ? `With token ${esc(x.current.token)} · ${esc(x.current.name || "")}` : STATUS_TEXT[x.status]}</small></span></button>`).join("");
  $("#picker").classList.add("hidden");
  $("#work").classList.remove("hidden");
  $("#d-avatar").textContent = initials(d.name);
  $("#d-name").textContent = d.name;
  $("#d-sub").textContent = `${d.specialty} · ${d.room || "No room assigned"}`;
  $$("#d-status button").forEach((b) => {
    const on = b.dataset.s === d.status || (d.status === "busy" && b.dataset.s === "available");
    b.setAttribute("aria-pressed", String(on));
    b.disabled = d.status === "busy" && b.dataset.s !== "available";
    b.title = b.disabled ? "Finish the current consultation first" : "";
  });

  const cur = s.consulting.find((p) => p.doctor_id === d.id);
  const key = `${d.status}:${cur ? cur.id + ":" + cur.ai_status + ":" + cur.level : ""}:${s.settings.auto_dispatch}:${s.queue.length ? 1 : 0}`;
  if (key !== lastKey) { lastKey = key; renderCurrent(d, cur, s); }
  tickTimers(cur);

  $("#q-len").textContent = s.queue.length ? `${s.queue.length} waiting` : "";
  $("#upnext").innerHTML = s.queue.slice(0, 6).map((p, i) => `
    <div class="next-item ${i === 0 ? "first" : ""}">
      ${tokenChip(p.token, p.level)}
      <div style="min-width:0">${i === 0 ? `<span class="next-tag">Next${p.eta != null ? `, in about ${fmtDur(p.eta, { short: true })}` : ""}</span>` : ""}
        <b>${esc(p.name || "Name not given")}</b>
        <span class="line">${prio(p.level)}<span>${esc(p.primary_condition || "")}</span></span>
        <span class="line">Waiting ${fmtDur(serverNow() - p.arrived_at, { short: true })}</span></div>
    </div>`).join("") || `<div class="empty">${icon("check")}<b>No one is waiting</b></div>`;
  $("#day").innerHTML = `<div><b>${d.seen_today}</b><span>patients seen</span></div>
    <div><b>${d.avg_consult ? Math.round(d.avg_consult / 60) : "—"}</b><span>min per patient</span></div>
    <div><b>${s.stats.queue_length}</b><span>waiting now</span></div>`;
}

function vitalsHtml(v) {
  const cells = [["Temperature", v.temp ? `${v.temp}°${+v.temp < 50 ? "C" : "F"}` : null, "thermo"], ["Oxygen", v.spo2 ? `${v.spo2}%` : null, "drop"],
    ["Pulse", v.pulse ? `${v.pulse}/min` : null, "pulse"], ["Blood pressure", v.bp_sys ? `${v.bp_sys}` : null, "gauge"], ["Pain", v.pain != null ? `${v.pain}/10` : null, "bolt"]]
    .filter((c) => c[1]);
  if (!cells.length) return `<p class="muted">Not measured at the desk.</p>`;
  return `<div class="vitals-grid">${cells.map(([k, val, ic]) => `<div class="vital"><span>${icon(ic)}${k}</span><b>${esc(val)}</b></div>`).join("")}</div>`;
}

function renderCurrent(d, p, s) {
  const box = $("#current");
  if (!p) {
    let title, text, btn = "";
    if (d.status === "available") {
      title = "You're available";
      text = s.settings.auto_dispatch
        ? (s.queue.length ? "The most urgent waiting patient is being sent to you." : "No one is waiting right now. The next patient will be sent to you automatically.")
        : "Automatic assignment is off. Call the next patient when you're ready.";
      if (!s.settings.auto_dispatch && s.queue.length) btn = `<button class="btn btn-primary btn-lg" id="call-next" type="button">${icon("play")}Call next patient</button>`;
    } else {
      title = d.status === "break" ? "You're on a break" : "You're off duty";
      text = "No patients are sent to you. Switch to Available when you're ready.";
      btn = `<button class="btn btn-primary btn-lg" id="go-available" type="button">${icon("check")}I'm available</button>`;
    }
    box.innerHTML = `<div class="status-empty">${icon(d.status === "available" ? "stetho" : d.status === "break" ? "coffee" : "power")}<b>${title}</b><p>${text}</p>${btn}</div>`;
    const cn = $("#call-next"); if (cn) cn.onclick = () => act(`doctors/${d.id}/call_next`);
    const ga = $("#go-available"); if (ga) ga.onclick = () => setStatus("available");
    return;
  }
  const flags = [];
  if (p.emergency) flags.push(`<span class="badge warn">${icon("siren")}Emergency</span>`);
  if (p.preempted) flags.push(`<span class="badge warn">${icon("refresh")}Resumed after an emergency</span>`);
  if (p.level_source === "ai") flags.push(`<span class="badge info">${icon("spark")}Priority raised by AI</span>`);
  (p.red_flags || []).forEach((r) => flags.push(`<span class="badge warn">${icon("octagon")}${esc(r)}</span>`));
  box.innerHTML = `
    <div class="cur-head">
      ${tokenChip(p.token, p.level)}
      <div class="grow" style="min-width:0">
        <p class="t-small muted">Current patient</p>
        <h2>${esc(p.name || "Name not given")}</h2>
        <p class="text-2">${esc([ageSex(p), p.pregnant ? "Pregnant" : null, p.phone].filter(Boolean).join(" · "))}</p>
        <div class="row wrap gap-8 mt-8">${prio(p.level)}<span class="t-small text-2">${esc(p.primary_condition || "")}</span></div>
      </div>
      <div class="cur-timer"><span>In your room</span><b id="in-room">0:00</b><span id="waited"></span></div>
    </div>
    <div class="cur-body">
      ${flags.length ? `<div class="row wrap gap-4">${flags.join("")}</div>` : ""}
      <section><h3>Reason for visit</h3><blockquote class="quote">${esc(p.symptoms || "Not recorded")}</blockquote></section>
      <section><h3>Vital signs</h3>${vitalsHtml(p.vitals || {})}</section>
      <section><h3>Why this priority</h3><ul class="reasons">${(p.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
        ${p.ai_summary ? `<p class="t-small mt-8"><b>AI second opinion:</b> ${esc(p.ai_summary)}</p>` : ""}</section>
      <section class="ai-box">
        <div class="row between wrap gap-8"><span class="ai-title">${icon("spark")}Pre-consultation brief</span>
          <button class="btn btn-sm" type="button" id="gen-brief">${p.ai_brief ? "Write again" : "Write brief with AI"}</button></div>
        <div class="prose mt-8" id="brief-body">${p.ai_brief ? aiText(p.ai_brief) : `<p class="t-small">A short summary, questions to ask and dangerous causes to rule out. Decision support only; takes about 6 seconds.</p>`}</div>
      </section>
      <section class="field"><label for="meds">Medicines to dispense <span class="muted">(optional)</span></label><textarea class="textarea" id="meds" aria-describedby="meds-help" placeholder="Paracetamol 500 mg, 1 tablet 3 times a day for 3 days">${esc(medDrafts[p.id] || "")}</textarea>
        <p class="helper" id="meds-help">When you finish, this goes to the pharmacy queue. The pharmacy prepares orders in the order they arrive.</p></section>
      <section class="field"><label for="notes">Consultation notes</label><textarea class="textarea" id="notes" placeholder="Findings, treatment and advice">${esc(drafts[p.id] || "")}</textarea></section>
      <fieldset><legend class="label">Outcome</legend>
        <div class="outcomes mt-8" id="outcome">${["Discharged", "Prescribed", "Admitted", "Referred", "Follow-up"].map((o) => `<label><input type="radio" name="outcome" value="${o}" ${o === outcome ? "checked" : ""}><span>${o}</span></label>`).join("")}</div>
      </fieldset>
    </div>
    <div class="cur-foot">
      <button class="btn btn-primary btn-lg" type="button" id="finish">${icon("check")}Finish consultation</button>
      <button class="btn btn-lg" type="button" id="finish-break">${icon("coffee")}Finish and take a break</button>
      <span class="grow"></span>
      <button class="btn btn-ghost" type="button" id="recall">${icon("volume")}Call again</button>
      <button class="btn btn-ghost" type="button" id="noshow">${icon("x")}Patient didn't come</button>
    </div>`;
  $("#notes").addEventListener("input", (e) => { drafts[p.id] = e.target.value; });
  $("#meds").addEventListener("input", (e) => { medDrafts[p.id] = e.target.value; });
  $("#outcome").addEventListener("change", (e) => { outcome = e.target.value; });
  $("#gen-brief").onclick = async () => {
    const btn = $("#gen-brief"); btn.disabled = true;
    $("#brief-body").innerHTML = `<div class="row gap-8"><div class="spinner"></div><span class="t-small">The AI is writing the brief…</span></div>`;
    try { const r = await api(`patients/${p.id}/brief`, { method: "POST" }); $("#brief-body").innerHTML = aiText(r.brief); btn.textContent = "Write again"; }
    catch (e) { $("#brief-body").innerHTML = `<p class="error-text">${esc(e.message)}</p>`; }
    finally { btn.disabled = false; }
  };
  const finish = async (next) => {
    try {
      await api(`doctors/${d.id}/finish`, { method: "POST", body: { outcome, notes: $("#notes").value, medicines: $("#meds").value, next_status: next } });
      const sent = $("#meds").value.trim();
      delete drafts[p.id]; delete medDrafts[p.id];
      toast(`Token ${p.token} finished: ${outcome}${sent ? ". Prescription sent to the pharmacy." : ""}`);
    } catch (e) { toastError(e); }
  };
  $("#finish").onclick = () => finish("available");
  $("#finish-break").onclick = () => finish("break");
  $("#recall").onclick = () => act(`doctors/${d.id}/recall`, `Token ${p.token} called again on the waiting-room screen`);
  $("#noshow").onclick = async () => {
    if (await confirmDialog(`Token ${p.token} didn't come?`, "They're marked as not attending and the next patient is sent to you.", { confirm: "Mark as not attending", danger: true })) act(`doctors/${d.id}/no_show`);
  };
  hydrateIcons(box);
}

function tickTimers(p) {
  const el = $("#in-room"); if (!el || !p) return;
  const sec = Math.max(0, serverNow() - p.called_at);
  el.textContent = `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}`;
  const w = $("#waited"); if (w) w.textContent = `Waited ${fmtDur(p.wait_seconds, { short: true })} · arrived ${fmtClock(p.arrived_at)}`;
}
setInterval(() => { const s = store.state; if (s && myId) tickTimers(s.consulting.find((p) => p.doctor_id === myId)); }, 1000);

async function act(path, ok) { try { await api(path, { method: "POST" }); if (ok) toast(ok); } catch (e) { toastError(e); } }
async function setStatus(status) {
  try { await api(`doctors/${myId}/status`, { method: "POST", body: { status } }); toast(`You're now ${STATUS_TEXT[status].toLowerCase()}`); } catch (e) { toastError(e); }
}
$("#d-status").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b || b.disabled || b.getAttribute("aria-pressed") === "true") return; setStatus(b.dataset.s); });
store.subscribe(render);
