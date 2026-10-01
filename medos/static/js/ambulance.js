// Ambulance desk: SJF-style hospital choice (shortest time to treatment) and dispatch (shortest trip first).
import { $, $$, esc, icon, api, createLive, initChrome, toast, toastError, confirmDialog, prio, LEVEL_TARGET, fmtDur, fmtClock, serverNow, hydrateIcons } from "./core.js";

const live = createLive({ url: "/api/state" });
initChrome(live);

let st = null, kind = "emergency", rec = null, chosen = null;
async function load() {
  try { st = await api("ambulance/state"); render(); } catch (e) { if (e.status !== 401) toastError(e); }
}
document.addEventListener("medos:event", (e) => { if (["ambulance", "tick", "queue"].includes(e.detail.kind)) load(); });

const KIND_BADGE = { emergency: `<span class="badge warn">${icon("siren")}Emergency</span>`, non_emergency: `<span class="badge">Non-emergency</span>` };
function stat(ic, label, value, unit, hint) {
  return `<div class="stat"><span class="label">${icon(ic)}${label}</span><span class="value">${value}${unit ? `<small>${unit}</small>` : ""}</span><span class="hint">${hint}</span></div>`;
}

function render() {
  const free = st.ambulances.filter((a) => a.status === "available").length;
  const er = st.hospitals.filter((h) => h.active && /emergency/i.test(h.specialties) && h.beds_free > 0).length;
  $("#am-stats").innerHTML =
    stat("ambulance", "Ambulances free", free, `of ${st.ambulances.length}`, free ? "Ready to go now" : "All out on trips") +
    stat("list", "Waiting for an ambulance", st.waiting.length, "", st.waiting.length ? `Next one leaves in about ${Math.round(st.waiting[0].dispatch_in_min ?? 0)} min` : "No one waiting") +
    stat("route", "On the way", st.on_trip.length, "", "Mark them arrived at the hospital") +
    stat("hospital", "Emergency departments open", er, `of ${st.hospitals.filter((h) => /emergency/i.test(h.specialties)).length}`, "With at least one free bed");

  const area = $("#am-area");
  if (!area.options.length) area.innerHTML = st.areas.map((a) => `<option>${esc(a)}</option>`).join("");

  $("#am-queue").innerHTML = st.waiting.length ? st.waiting.map((r, i) => `<div class="prow am ${i === 0 ? "first" : ""}" data-level="${r.level}">
      <span class="pos">${r.position}</span>
      <span class="who-cell"><b>#${r.id} ${esc(r.patient_name || "Unknown patient")} ${KIND_BADGE[r.kind]}</b>
        <span class="line">${esc(r.pickup)} → ${esc(r.hospital_name)} · ${esc(r.condition)}</span></span>
      <span class="time">${Math.round(r.job_min)} min<span>trip length</span></span>
      <span class="time">${r.dispatch_in_min == null ? "—" : r.dispatch_in_min < 1 ? "Now" : "~" + Math.round(r.dispatch_in_min) + " min"}<span>waiting ${fmtDur(serverNow() - r.created_at, { short: true })}</span></span>
      <button class="btn btn-sm btn-ghost" type="button" data-cancel="${r.id}">${icon("x")}Cancel</button></div>`).join("")
    : `<div class="list-empty">${icon("ambulance")}<b>No requests waiting</b><span>New requests are sent straight away when an ambulance is free.</span></div>`;

  $("#am-trips").innerHTML = st.on_trip.length ? st.on_trip.map((r) => `<div class="prow am2" data-level="${r.level}">
      <span class="who-cell"><b>${esc(r.ambulance)}: ${esc(r.patient_name || "Unknown patient")} ${KIND_BADGE[r.kind]}</b>
        <span class="line">${esc(r.pickup)} → ${esc(r.hospital_name)} · left at ${fmtClock(r.dispatched_at)}</span></span>
      <span class="time">${Math.round(r.elapsed_min)} of ~${Math.round(r.job_min)} min<span>trip</span></span>
      <span class="row gap-8"><button class="btn btn-sm btn-primary" type="button" data-arrived="${r.id}">${icon("check")}Arrived</button>
      <button class="btn btn-sm btn-ghost" type="button" data-cancel="${r.id}" aria-label="Cancel trip ${r.id}">${icon("x")}</button></span></div>`).join("")
    : `<div class="list-empty"><span>No ambulance is out right now.</span></div>`;

  $("#am-fleet").innerHTML = st.ambulances.map((a) => `<span class="badge ${a.status === "available" ? "ok" : "warn"}">${icon("ambulance")}${esc(a.name)}: ${a.status === "available" ? "free" : "on a trip"}</span>`).join("");

  if (!document.activeElement.closest("#am-hosp")) {
    $("#am-hosp").innerHTML = `<thead><tr><th scope="col">Hospital</th><th scope="col">Specialties</th><th scope="col">Free beds</th><th scope="col">Reported wait (min)</th><th scope="col">Taking patients</th></tr></thead><tbody>${st.hospitals.map((h) => `
      <tr data-id="${h.id}"><td><b>${esc(h.name)}</b>${h.is_self ? ` <span class="badge teal">This hospital</span>` : ""}</td>
        <td class="t-small">${esc(h.specialties)}</td>
        <td><input class="input num" style="width:90px" data-f="beds_free" value="${h.beds_free}" inputmode="numeric" aria-label="Free beds at ${esc(h.name)}"></td>
        <td>${h.is_self ? `<span class="t-small muted">Live from the queue</span>` : `<input class="input num" style="width:90px" data-f="er_wait_min" value="${Math.round(h.er_wait_min)}" inputmode="numeric" aria-label="Reported wait at ${esc(h.name)}">`}</td>
        <td><label class="switch"><input type="checkbox" data-f="active" ${h.active ? "checked" : ""} aria-label="${esc(h.name)} is taking patients"><span class="track"></span></label></td></tr>`).join("")}</tbody>`;
  }
}

// ------------------------------------------------------------------ new request + recommendation
$("#am-kind").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  kind = b.dataset.v;
  $$("#am-kind button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  $("#kind-help").textContent = kind === "emergency" ? "Emergencies are always dispatched before non-emergency trips." : "Planned transfers. Among these, the shortest trip goes first, and long waits move up.";
  if (rec) findBest();
});
function form() {
  return { kind, area: $("#am-area").value, age: $("#am-age").value.trim(), name: $("#am-name").value.trim(), condition: $("#am-cond").value.trim() };
}
async function findBest() {
  const f = form();
  if (!f.condition) { $("#am-cond").focus(); return toast("Describe what happened first", "error"); }
  try { rec = await api("ambulance/recommend", { method: "POST", body: f }); } catch (e) { return toastError(e); }
  chosen = rec.best ? rec.best.id : null;
  renderRec();
}
function renderRec() {
  const r = rec;
  $("#am-rec").innerHTML = `
    <div class="triage" data-level="${r.level}">
      <div class="row wrap gap-8">${prio(r.level, { lg: true })}<span class="strong text-2">${LEVEL_TARGET[r.level]}</span></div>
      <p class="mt-8"><b>${esc(r.primary_condition)}</b> · needs ${esc(r.needs)}</p>
      <p class="t-small muted mt-4">The ambulance takes about ${Math.round(r.pickup_min)} min to reach ${esc(r.area)}.${r.note ? " " + esc(r.note) : ""}</p>
    </div>
    <p class="t-small text-2 mt-16"><b>Shortest time to treatment wins.</b> Time to treatment = drive from ${esc(r.area)} + expected wait at the hospital.</p>
    <div class="hosp-rank mt-8" role="radiogroup" aria-label="Choose a hospital">${r.ranking.map((h) => `
      <label class="hosp-opt ${h.eligible ? "" : "off"} ${rec.best && h.id === rec.best.id ? "best" : ""}">
        <input type="radio" name="hosp" value="${h.id}" ${h.id === chosen ? "checked" : ""} ${h.eligible ? "" : "disabled"}>
        <span class="grow"><b>${esc(h.name)}</b>${rec.best && h.id === rec.best.id ? ` <span class="badge ok">${icon("check")}Fastest</span>` : ""}
          <span class="t-small muted">${h.eligible ? `${Math.round(h.travel_min)} min drive + ${Math.round(h.wait_min)} min wait (${esc(h.wait_source)})` : esc(h.reason)}</span></span>
        <span class="time">${h.eligible ? Math.round(h.total_min) + " min" : "—"}<span>${h.eligible ? "to treatment" : "can't take"}</span></span>
      </label>`).join("")}</div>
    <button class="btn btn-primary btn-lg btn-block mt-16" type="button" id="am-send" ${rec.best ? "" : "disabled"}>${icon("ambulance")}Send ambulance</button>`;
  $$('#am-rec input[name="hosp"]').forEach((i) => i.addEventListener("change", () => { chosen = +i.value; }));
  $("#am-send").onclick = send;
}
async function send() {
  const b = $("#am-send"); b.disabled = true;
  try {
    const r = await api("ambulance/requests", { method: "POST", body: { ...form(), hospital_id: chosen } });
    toast(r.status === "dispatched" ? `Ambulance on its way to ${r.pickup}, then ${r.hospital_name}` : `Request #${r.id} queued. It goes with the next free ambulance.`);
    $("#am-form").reset(); $("#am-rec").innerHTML = ""; rec = null; load();
  } catch (e) { toastError(e); b.disabled = false; }
}
$("#am-form").addEventListener("submit", (e) => { e.preventDefault(); findBest(); });

// ------------------------------------------------------------------ actions
document.addEventListener("click", async (e) => {
  const a = e.target.closest("[data-arrived]"), c = e.target.closest("[data-cancel]");
  try {
    if (a) {
      const r = await api(`ambulance/requests/${a.dataset.arrived}/arrived`, { method: "POST" });
      toast(r.patient_id ? `Arrived. The patient was added to this hospital's queue.` : `Arrived at ${r.hospital_name}`);
    }
    if (c && await confirmDialog("Cancel this ambulance request?", "If an ambulance is on its way it becomes free for the next request.", { confirm: "Cancel request", danger: true })) {
      await api(`ambulance/requests/${c.dataset.cancel}/cancel`, { method: "POST" }); toast("Request cancelled");
    }
  } catch (err) { toastError(err); }
  if (a || c) load();
});
["plus", "minus"].forEach((k) => $(`#fleet-${k}`).addEventListener("click", async () => {
  try { await api("ambulance/fleet", { method: "POST", body: { change: k === "plus" ? 1 : -1 } }); load(); } catch (err) { toastError(err); }
}));
$("#am-hosp").addEventListener("change", async (e) => {
  const f = e.target.dataset.f; if (!f) return;
  const id = e.target.closest("tr").dataset.id;
  try { await api(`ambulance/hospitals/${id}`, { method: "PATCH", body: { [f]: e.target.type === "checkbox" ? e.target.checked : e.target.value } }); toast("Saved"); if (rec) findBest(); } catch (err) { toastError(err); }
});
hydrateIcons();
load();
setInterval(() => { if (st) render(); }, 30000);
