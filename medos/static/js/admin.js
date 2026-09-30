import {
  $, $$, esc, icon, api, createLive, initChrome, toast, toastError, modal, confirmDialog, debounce, prio, tokenChip,
  LEVELS, LEVEL_LABEL, LEVEL_COLOR, fmtDur, fmtClock, fmtBytes, serverNow, aiText, hydrateIcons, sourceLabel,
} from "./core.js";

const store = createLive({
  onEvent(kind, data) {
    if (kind === "log") pushLog(data);
    if (["queue", "doctors", "called"].includes(kind)) refreshAnalytics();
    if (["emergency", "alert_ack"].includes(kind) && tab === "devices") setTimeout(loadDevices, 300);
  },
});
initChrome(store);

const STATUS_TEXT = { available: "Available", busy: "With a patient", break: "On a break", off_duty: "Off duty" };

// ================================================================== tabs
const TABS = ["overview", "staff", "protocol", "devices", "ai", "system", "settings"];
let tab = null;
function showTab(name) {
  if (!TABS.includes(name)) name = "overview";
  tab = name;
  $$("#tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  TABS.forEach((t) => { $(`#tab-${t}`).hidden = t !== name; });
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  if (name === "protocol") loadConditions();
  if (name === "devices") loadDevices();
  if (name === "ai") loadAi();
  if (name === "system") { loadSystem(); loadLog(); }
  if (name === "settings") loadSettings();
  if (store.state) render(store.state);
}
$("#tabs").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) showTab(b.dataset.tab); });
window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));

// ================================================================== overview
function stat(ic, label, value, unit, hint, alert = false) {
  return `<div class="stat ${alert ? "alert" : ""}"><span class="label">${icon(ic)}${label}</span>
    <span class="value">${value}${unit ? `<small>${unit}</small>` : ""}</span><span class="hint">${hint}</span></div>`;
}
function renderOverview(s) {
  const st = s.stats, by = st.waiting_by_level;
  const avg = st.avg_wait_today ?? st.avg_wait_now;
  $("#kpis").innerHTML =
    stat("stetho", "Doctors available", st.doctors_available, `of ${st.doctors_on_duty} on duty`, `${st.doctors_busy} with patients, ${st.doctors_total - st.doctors_on_duty} off duty or on a break`) +
    stat("door", "Rooms available", st.rooms_available, `of ${st.rooms_total}`, `${st.in_consultation} in use right now`) +
    stat("users", "Queue length", st.queue_length, "", LEVELS.filter((l) => by[l]).map((l) => `${by[l]} ${LEVEL_LABEL[l].toLowerCase()}`).join(", ") || "No one is waiting") +
    stat("clock", "Average waiting time", avg == null ? "—" : Math.round(avg / 60), avg == null ? "" : "min", st.longest_wait_now ? `Longest wait right now: ${fmtDur(st.longest_wait_now)}` : "From arrival to seeing a doctor") +
    stat("siren", "Emergency alerts", st.alerts_active, "active", `${st.emergencies_today} raised today`, st.alerts_active > 0);

  $("#alerts-box").innerHTML = s.alerts.length ? `<section class="card alert-list" aria-label="Active emergency alerts">${s.alerts.map((a) => {
    const p = [...s.queue, ...s.consulting].find((x) => x.id === a.patient_id);
    const where = p ? (p.status === "in_consultation" ? `with ${esc(p.doctor)} in ${esc(p.room || "the desk")}` : "first in the queue") : "";
    return `<div class="alert-row">${icon("octagon")}<div class="grow"><b>Emergency: token ${esc(p ? p.token : "")} is ${where}</b><div class="t-small">Raised from ${esc(sourceLabel(a.source))} at ${fmtClock(a.ts)}</div></div>
      <button class="btn btn-sm" type="button" data-ack="${a.id}">${icon("check")}Acknowledge</button></div>`;
  }).join("")}</section>` : "";

  $("#rooms-sum").textContent = `${st.rooms_available} free of ${st.rooms_total} open`;
  $("#floor").innerHTML = s.rooms.map((r) => {
    const occ = r.occupied;
    const state = r.status !== "open" ? r.status : occ ? "occupied" : "free";
    return `<div class="room ${state}"><span class="r-name">${esc(r.name)}</span><span class="r-kind">${esc(r.kind)}${r.doctor ? ` · ${esc(r.doctor)}` : ""}</span>
      <span class="r-state">${occ ? `${tokenChip(occ.token, occ.level)}<span class="t-small text-2">${fmtDur(serverNow() - occ.since, { short: true })}</span>`
        : state === "free" ? `${icon("check")}Free` : state === "cleaning" ? "Being cleaned" : "Closed"}</span></div>`;
  }).join("");

  $("#docs-sum").textContent = `${st.doctors_on_duty} on duty`;
  $("#doc-rows").innerHTML = s.doctors.map((d) => `<div class="person">
    <span class="dot ${d.status}" aria-hidden="true"></span>
    <span class="grow"><b>${esc(d.name)}</b><span>${esc(d.specialty)} · ${esc(d.room || "no room")} · ${d.seen_today} seen today</span></span>
    ${d.current ? `<span class="row gap-8"><span class="t-small muted">With</span>${tokenChip(d.current.token, d.current.level)}</span>` : `<span class="badge">${STATUS_TEXT[d.status]}</span>`}
  </div>`).join("") || `<div class="empty">${icon("users")}<b>No doctors yet</b></div>`;
}
$("#alerts-box").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-ack]"); if (!b) return;
  try { await api(`alerts/${b.dataset.ack}/ack`, { method: "POST" }); toast("Alert acknowledged. The LED and buzzer are off."); } catch (err) { toastError(err); }
});

let analytics = null;
const refreshAnalytics = debounce(async () => { try { analytics = await api("analytics"); renderAnalytics(); } catch { /* ignore */ } }, 800);
function renderAnalytics() {
  const a = analytics; if (!a) return;
  const hours = a.arrivals_by_hour, now = new Date().getHours();
  let from = hours.findIndex((n) => n > 0); if (from < 0) from = Math.max(0, now - 8);
  from = Math.min(from, Math.max(0, now - 8));
  const slice = hours.slice(from, now + 1), max = Math.max(1, ...slice);
  $("#arrivals").innerHTML = slice.map((n, i) => `<div class="bar ${from + i === now ? "now" : ""}" style="height:${(n / max) * 100}%" title="${String(from + i).padStart(2, "0")}:00, ${n} arrival${n === 1 ? "" : "s"}"></div>`).join("");
  $("#arr-axis").innerHTML = slice.map((_, i) => `<span>${(from + i) % 2 === 0 || slice.length < 10 ? String(from + i).padStart(2, "0") + ":00" : ""}</span>`).join("");
  $("#arr-total").textContent = `${a.registered} registered, ${a.completed} seen`;
  const w = a.avg_wait_by_level_min, mx = a.max_wait_by_level_min;
  const top = Math.max(5, ...LEVELS.map((l) => mx[l] || 0));
  $("#wait-by-level").innerHTML = `<div class="hbars">${LEVELS.map((l) => `<div class="hbar">
      ${prio(l)}
      <div class="hb-track" role="img" aria-label="${LEVEL_LABEL[l]}: average ${w[l] ?? "no data"} minutes, longest ${mx[l] ?? "no data"} minutes"><div class="hb-max" style="width:${((mx[l] || 0) / top) * 100}%;background:${LEVEL_COLOR[l]}"></div><div class="hb-avg" style="width:${((w[l] || 0) / top) * 100}%;background:${LEVEL_COLOR[l]}"></div></div>
      <span class="num">${w[l] == null ? "no visits yet" : `${w[l]} avg`}${mx[l] ? `, ${mx[l]} max` : ""}</span></div>`).join("")}</div>
    <div class="legend mt-16"><span><i style="background:var(--text-2)"></i>Solid bar: average wait</span><span><i style="background:var(--text-2);opacity:.3"></i>Faded bar: longest wait</span></div>`;
}

// ================================================================== activity log
const LOG_NAMES = { ARRIVE: "Arrived", DISPATCH: "Sent to doctor", AGING: "Moved up", PREEMPT: "Paused", INTERRUPT: "Emergency", AI: "AI", COMPLETE: "Finished",
  ALERT: "Alert", DOCTOR: "Doctor", ADMIN: "Admin", SYSTEM: "System", CANCEL: "Left", NO_SHOW: "No-show", RETRIAGE: "Re-triaged", HARDWARE: "Device", ERROR: "Error" };
let logLines = [];
const logHtml = (l) => `<div class="ln"><span class="t">${fmtClock(l.ts, true)}</span><span class="k ${esc(l.kind)}">${esc(LOG_NAMES[l.kind] || l.kind)}</span><span>${esc(l.message)}</span></div>`;
function pushLog(l) {
  logLines.unshift(l); logLines = logLines.slice(0, 200);
  $("#ov-log").innerHTML = logLines.slice(0, 10).map(logHtml).join("");
  const kind = $("#log-kind").value;
  if (tab === "system" && (!kind || kind === l.kind)) $("#log").insertAdjacentHTML("afterbegin", logHtml(l));
}
async function loadLog() {
  const kind = $("#log-kind").value;
  const rows = await api(`events?limit=200${kind ? "&kind=" + kind : ""}`);
  if (!kind) logLines = rows;
  $("#log").innerHTML = rows.map(logHtml).join("") || `<p class="muted">Nothing logged yet.</p>`;
  $("#ov-log").innerHTML = logLines.slice(0, 10).map(logHtml).join("") || `<p class="muted">Nothing has happened yet today.</p>`;
}
$("#log-kind").addEventListener("change", loadLog);

// ================================================================== doctors and rooms
function renderStaff(s) {
  if (!document.activeElement.closest("#doc-table")) {
    const roomOpts = (sel) => `<option value="">No room</option>` + s.rooms.map((r) => `<option value="${r.id}" ${r.id === sel ? "selected" : ""}>${esc(r.name)}</option>`).join("");
    $("#doc-table").innerHTML = `<thead><tr><th scope="col">Name</th><th scope="col">Specialty</th><th scope="col">Room</th><th scope="col">Status</th><th scope="col">Seen today</th><th scope="col"><span class="sr-only">Remove</span></th></tr></thead><tbody>${s.doctors.map((d) => `
      <tr data-id="${d.id}">
        <td><input class="input" data-f="name" value="${esc(d.name)}" aria-label="Name"></td>
        <td><input class="input" data-f="specialty" value="${esc(d.specialty)}" aria-label="Specialty of ${esc(d.name)}"></td>
        <td><select class="select" data-f="room_id" aria-label="Room of ${esc(d.name)}">${roomOpts(d.room_id)}</select></td>
        <td class="nowrap"><span class="badge"><span class="dot ${d.status}"></span>${STATUS_TEXT[d.status]}${d.current ? `, token ${esc(d.current.token)}` : ""}</span></td>
        <td class="num">${d.seen_today}</td>
        <td><button class="btn btn-sm btn-ghost" type="button" data-del="${d.id}">${icon("trash")}Remove</button></td>
      </tr>`).join("")}</tbody>`;
  }
  if (!document.activeElement.closest("#room-table")) {
    $("#room-table").innerHTML = `<thead><tr><th scope="col">Room</th><th scope="col">Type</th><th scope="col">Can be used?</th><th scope="col">Right now</th><th scope="col">Doctor</th><th scope="col"><span class="sr-only">Delete</span></th></tr></thead><tbody>${s.rooms.map((r) => `
      <tr data-id="${r.id}">
        <td><input class="input" data-f="name" value="${esc(r.name)}" aria-label="Room name"></td>
        <td><select class="select" data-f="kind" aria-label="Type of ${esc(r.name)}">${[["consultation", "Consultation"], ["emergency", "Emergency"], ["procedure", "Procedure"]].map(([k, l]) => `<option value="${k}" ${k === r.kind ? "selected" : ""}>${l}</option>`).join("")}</select></td>
        <td><select class="select" data-f="status" aria-label="Status of ${esc(r.name)}">${[["open", "Open"], ["cleaning", "Being cleaned"], ["closed", "Closed"]].map(([k, l]) => `<option value="${k}" ${k === r.status ? "selected" : ""}>${l}</option>`).join("")}</select></td>
        <td>${r.occupied ? `${tokenChip(r.occupied.token, r.occupied.level)}` : `<span class="muted">Free</span>`}</td>
        <td>${esc(r.doctor || "—")}</td>
        <td><button class="btn btn-sm btn-ghost" type="button" data-del="${r.id}">${icon("trash")}Delete</button></td>
      </tr>`).join("")}</tbody>`;
  }
}
$("#doc-table").addEventListener("change", async (e) => {
  const f = e.target.dataset.f; if (!f) return;
  const id = e.target.closest("tr").dataset.id;
  try { await api(`doctors/${id}`, { method: "PATCH", body: { [f]: f === "room_id" ? (+e.target.value || null) : e.target.value } }); toast("Saved"); } catch (err) { toastError(err); }
});
$("#doc-table").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-del]"); if (!b) return;
  const d = store.state.doctors.find((x) => x.id === +b.dataset.del);
  if (await confirmDialog(`Remove ${d.name}?`, "Their past consultations stay in the records.", { confirm: "Remove doctor", danger: true })) {
    try { await api(`doctors/${d.id}`, { method: "DELETE" }); toast(`${d.name} removed`); } catch (err) { toastError(err); }
  }
});
$("#add-doc").addEventListener("click", () => modal({
  title: "Add a doctor",
  body: `<div class="fields"><div class="field span-6"><label for="nd-name">Name</label><input class="input" id="nd-name" placeholder="Dr. Meera Nair" autofocus></div>
    <div class="field span-3"><label for="nd-spec">Specialty</label><input class="input" id="nd-spec" value="General Medicine"></div>
    <div class="field span-3"><label for="nd-room">Room</label><select class="select" id="nd-room"><option value="">No room</option>${store.state.rooms.map((r) => `<option value="${r.id}">${esc(r.name)}</option>`).join("")}</select></div></div>
    <p class="helper mt-16">New doctors start off duty. They switch to Available from the Doctor screen.</p>`,
  actions: [{ label: "Cancel", kind: "ghost", value: false }, { label: "Add doctor", kind: "primary", onClick: async (m) => {
    await api("doctors", { method: "POST", body: { name: $("#nd-name", m).value, specialty: $("#nd-spec", m).value, room_id: +$("#nd-room", m).value || null } });
    toast("Doctor added");
  } }],
}));
$("#room-table").addEventListener("change", async (e) => {
  const f = e.target.dataset.f; if (!f) return;
  try { await api(`rooms/${e.target.closest("tr").dataset.id}`, { method: "PATCH", body: { [f]: e.target.value } }); toast("Saved"); } catch (err) { toastError(err); }
});
$("#room-table").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-del]"); if (!b) return;
  const r = store.state.rooms.find((x) => x.id === +b.dataset.del);
  if (await confirmDialog(`Delete ${r.name}?`, "Doctors assigned to it will have no room until you choose another.", { confirm: "Delete room", danger: true })) {
    try { await api(`rooms/${r.id}`, { method: "DELETE" }); toast(`${r.name} deleted`); } catch (err) { toastError(err); }
  }
});
$("#add-room").addEventListener("click", () => modal({
  title: "Add a room",
  body: `<div class="fields"><div class="field span-4"><label for="nr-name">Name</label><input class="input" id="nr-name" placeholder="Room 4" autofocus></div>
    <div class="field span-2"><label for="nr-kind">Type</label><select class="select" id="nr-kind"><option value="consultation">Consultation</option><option value="emergency">Emergency</option><option value="procedure">Procedure</option></select></div></div>`,
  actions: [{ label: "Cancel", kind: "ghost", value: false }, { label: "Add room", kind: "primary", onClick: async (m) => {
    await api("rooms", { method: "POST", body: { name: $("#nr-name", m).value, kind: $("#nr-kind", m).value } }); toast("Room added");
  } }],
}));

// ================================================================== triage rules
let conditions = [];
async function loadConditions() {
  conditions = await api("conditions");
  $("#cond-table").innerHTML = `<thead><tr><th scope="col">Rank</th><th scope="col">Condition</th><th scope="col">Priority</th><th scope="col">Reason</th><th scope="col">Keywords</th><th scope="col">Red flag</th><th scope="col">In use</th><th scope="col"><span class="sr-only">Delete</span></th></tr></thead><tbody>${conditions.map((c) => `
    <tr data-id="${c.id}" data-level="${c.level}" class="${c.active ? "" : "off"}">
      <td><input class="input num rank" data-f="rank" value="${c.rank}" inputmode="numeric" aria-label="Rank of ${esc(c.name)}"></td>
      <td><input class="input" data-f="name" value="${esc(c.name)}" aria-label="Condition name"></td>
      <td><select class="select" data-f="level" aria-label="Priority of ${esc(c.name)}">${LEVELS.map((l) => `<option value="${l}" ${l === c.level ? "selected" : ""}>${LEVEL_LABEL[l]}</option>`).join("")}</select></td>
      <td><input class="input" data-f="reason" value="${esc(c.reason || "")}" aria-label="Reason for ${esc(c.name)}"></td>
      <td><textarea class="textarea kw" data-f="keywords" aria-label="Keywords for ${esc(c.name)}">${esc(c.keywords)}</textarea></td>
      <td><label class="switch"><input type="checkbox" data-f="red_flag" ${c.red_flag ? "checked" : ""} aria-label="${esc(c.name)} is a red flag"><span class="track"></span></label></td>
      <td><label class="switch"><input type="checkbox" data-f="active" ${c.active ? "checked" : ""} aria-label="${esc(c.name)} is in use"><span class="track"></span></label></td>
      <td><button class="icon-btn" type="button" data-del="${c.id}" aria-label="Delete ${esc(c.name)}">${icon("trash")}</button></td>
    </tr>`).join("")}</tbody>`;
}
$("#cond-table").addEventListener("change", async (e) => {
  const f = e.target.dataset.f; if (!f) return;
  const tr = e.target.closest("tr");
  const v = e.target.type === "checkbox" ? e.target.checked : e.target.value;
  try {
    await api(`conditions/${tr.dataset.id}`, { method: "PATCH", body: { [f]: v } });
    if (f === "level") tr.dataset.level = v;
    if (f === "active") tr.classList.toggle("off", !v);
    toast("Rule updated"); testProtocol();
  } catch (err) { toastError(err); }
});
$("#cond-table").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-del]"); if (!b) return;
  const c = conditions.find((x) => x.id === +b.dataset.del);
  if (await confirmDialog(`Delete “${c.name}”?`, "New patients won't be matched against it. Switching it off instead keeps it for later.", { confirm: "Delete", danger: true })) {
    await api(`conditions/${c.id}`, { method: "DELETE" }); loadConditions();
  }
});
$("#add-cond").addEventListener("click", async () => {
  await api("conditions", { method: "POST", body: { name: "New condition", rank: 15, level: "low", keywords: "", reason: "" } });
  await loadConditions();
  const row = $$("#cond-table tbody tr").find((r) => $("input[data-f=name]", r).value === "New condition");
  if (row) { row.scrollIntoView({ block: "center" }); $("input[data-f=name]", row).select(); }
});
$("#reset-cond").addEventListener("click", async () => {
  if (await confirmDialog("Restore the default rules?", "Your edits are replaced by the ten red flags and the original 15 conditions.", { confirm: "Restore defaults", danger: true })) {
    try { await api("conditions/reset", { method: "POST" }); await loadConditions(); toast("Default rules restored"); } catch (err) { toastError(err); }
  }
});
const testProtocol = debounce(async () => {
  const text = $("#proto-test").value.trim();
  if (!text) { $("#proto-result").innerHTML = ""; return; }
  const t = await api("triage/preview", { method: "POST", body: { symptoms: text } });
  $("#proto-result").innerHTML = `<div class="triage" data-level="${t.level}"><div class="row wrap gap-8">${prio(t.level, { lg: true })}</div>
    <p class="mt-8"><b>${esc(t.primary_condition)}</b></p>
    ${t.conditions.length ? `<p class="t-small muted mt-8">Matched: ${t.conditions.map((c) => `${esc(c.name)} (“${esc(c.keyword)}”)`).join(", ")}</p>` : ""}</div>`;
}, 250);
$("#proto-test").addEventListener("input", testProtocol);

// ================================================================== devices
let devTimer = null;
async function loadDevices() {
  clearTimeout(devTimer);
  if (tab !== "devices") return;
  try { const sys = await api("system"); renderDevices(sys.hardware, sys.voice); } catch { /* ignore */ }
  devTimer = setTimeout(loadDevices, 1500);
}
function pinSvg() {
  const used = { 9: "k-btn", 11: "k-btn", 13: "k-led", 14: "k-led", 15: "k-buz", 20: "k-buz" };
  const names = { 9: "GND", 11: "GPIO17", 13: "GPIO27", 14: "GND", 15: "GPIO22", 20: "GND" };
  let pins = "";
  for (let n = 1; n <= 20; n++) {
    const col = Math.floor((n - 1) / 2), top = n % 2 === 1, x = 36 + col * 40, y = top ? 44 : 84, u = used[n];
    pins += `<g class="pin ${u || ""}"><circle cx="${x}" cy="${y}" r="11"/><text x="${x}" y="${y + 4}" text-anchor="middle">${n}</text>${u ? `<text class="pn" x="${x}" y="${top ? 20 : 116}" text-anchor="middle">${names[n]}</text>` : ""}</g>`;
  }
  return `<svg class="header-svg" viewBox="0 0 420 128" role="img" aria-label="Raspberry Pi GPIO pins 1 to 20. Button on pins 9 and 11, LED on 13 and 14, buzzer on 15 and 20."><rect x="12" y="26" width="396" height="76" rx="8" class="hdr"/>${pins}</svg>`;
}
function renderDevices(hw, voice) {
  if (!hw) return;
  $("#hw-mode").innerHTML = hw.mode === "gpio" ? `<span class="badge ok">${icon("check")}Connected to the Pi's pins</span>` : `<span class="badge">${icon("info")}Simulated (not on a Pi)</span>`;
  $("#hw-diagram").innerHTML = `${pinSvg()}
    <div class="circuit k-btn"><span class="c-icon ${hw.button_pressed ? "on" : ""}">${icon("bolt")}</span><div class="grow"><b>Emergency push button</b><div class="t-small muted">GPIO17 (pin 11) to the button, other leg to GND (pin 9). Pressing it raises an emergency instantly.</div></div>
      <div class="c-state"><b class="num">${hw.interrupts}</b><span class="t-small muted">presses</span></div></div>
    <div class="circuit k-led"><span class="c-led ${hw.led}" aria-hidden="true"></span><div class="grow"><b>Red warning light (LED)</b><div class="t-small muted">GPIO27 (pin 13) through a 330 Ω resistor to the LED, then GND (pin 14). Blinks until someone acknowledges the alert.</div></div>
      <div class="c-state"><b>${hw.led === "off" ? "Off" : hw.led === "on" ? "On" : "Blinking"}</b></div></div>
    <div class="circuit k-buz"><span class="c-icon ${hw.buzzer !== "off" ? "on" : ""}">${icon("volume")}</span><div class="grow"><b>Buzzer</b><div class="t-small muted">GPIO22 (pin 15) to buzzer +, buzzer − to GND (pin 20). Sounds for 20 seconds, then goes quiet.</div></div>
      <div class="c-state"><b>${hw.buzzer === "off" ? "Quiet" : "Beeping"}</b></div></div>`;
  $("#hw-state").innerHTML = `
    <p class="t-small text-2">${hw.mode === "gpio" ? "These buttons drive the real pins on the Pi." : hw.error ? `The pins failed to start: ${esc(hw.error)}` : "This computer isn't a Raspberry Pi, so the pins are simulated. Everything else works the same."}</p>
    <div class="stack-8">
      <button class="btn" type="button" data-test="led">${icon("led")}Blink the light</button>
      <button class="btn" type="button" data-test="buzzer">${icon("volume")}Beep the buzzer</button>
      <button class="btn btn-danger-outline" type="button" data-test="button">${icon("bolt")}Press the emergency button</button>
    </div>
    <p class="helper">${hw.last_press ? `Button last pressed at ${fmtClock(hw.last_press, true)}.` : "The button hasn't been pressed since MedOS started."}</p>`;
  $("#voice-state").innerHTML = `<dl class="kv">
    <dt>Offline speech model</dt><dd>${voice.server_stt ? "Installed" : "Not installed"}</dd>
    <dt>USB microphone on the Pi</dt><dd>${voice.pi_mic ? "Ready" : "Not found"}</dd>
    <dt>Model in memory</dt><dd>${voice.model_loaded ? "Loaded" : "Loads when first used"}</dd>
    <dt>Voice registrations</dt><dd class="num">${voice.transcriptions}</dd></dl>
    ${voice.server_stt ? "" : `<p class="helper mt-8">Run deploy/install.sh on the Pi to install it. Browsers with speech recognition can still register by voice.</p>`}`;
}
$("#hw-state").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-test]"); if (!b) return;
  if (b.dataset.test === "button" && !(await confirmDialog("Press the emergency button?", "This raises a real emergency: an emergency patient goes to the front, the alarm starts, and a consultation may be paused.", { confirm: "Raise emergency", danger: true }))) return;
  try { await api("hardware/test", { method: "POST", body: { what: b.dataset.test } }); setTimeout(loadDevices, 250); } catch (err) { toastError(err); }
});

// ================================================================== AI
async function loadAi(force = false) {
  const llm = await api(`llm/status${force ? "?force=1" : ""}`);
  const s = await api("settings");
  $("#llm-badge").innerHTML = !llm.enabled ? `<span class="badge">Switched off</span>` : llm.model_ready ? `<span class="badge ok">${icon("check")}Connected</span>`
    : `<span class="badge warn">${icon("info")}${llm.online ? "Model missing" : "Can't connect"}</span>`;
  const models = [...new Set([...(llm.models || []), s.llm_model])];
  $("#llm-form").innerHTML = `
    <label class="switch"><input type="checkbox" data-s="llm_enabled" ${s.llm_enabled ? "checked" : ""}><span class="track"></span><span class="sw-text"><b>Use AI features</b><span>Second opinions, voice form filling, briefs and reports</span></span></label>
    <div class="fields">
      <div class="field span-4"><label for="llm-url">Ollama address</label><input class="input" id="llm-url" data-s="llm_url" value="${esc(s.llm_url)}"></div>
      <div class="field span-2"><label for="llm-model">Model</label><select class="select" id="llm-model" data-s="llm_model">${models.map((m) => `<option ${m === s.llm_model ? "selected" : ""}>${esc(m)}</option>`).join("")}</select></div>
    </div>
    <label class="switch"><input type="checkbox" data-s="ai_triage" ${s.ai_triage ? "checked" : ""}><span class="track"></span><span class="sw-text"><b>Second opinion for every new patient</b><span>Runs in the background and can only raise a priority</span></span></label>
    ${llm.error ? `<p class="error-text">${esc(llm.error)}</p>` : ""}
    <div class="mini-stats">
      <div><span>Requests</span><b>${llm.calls}</b></div><div><span>Failed</span><b>${llm.failures}</b></div>
      <div><span>Last reply</span><b>${llm.last_latency ? llm.last_latency + " s" : "—"}</b></div><div><span>Waiting for AI</span><b>${llm.worker.queued}</b></div>
    </div>
    <div class="row wrap gap-8"><button class="btn" type="button" id="llm-test">${icon("send")}Test connection</button><button class="btn" type="button" id="llm-find">${icon("wifi")}Find on network</button></div>
    <div id="llm-found"></div>`;
  $("#llm-test").onclick = async (e) => {
    e.currentTarget.disabled = true;
    try { const r = await api("llm/test", { method: "POST" }); toast(`The AI replied in ${r.latency} s: “${r.reply}”`); loadAi(true); } catch (err) { toastError(err); loadAi(true); }
  };
  $("#llm-find").onclick = async (e) => {
    const b = e.currentTarget; b.disabled = true;
    $("#llm-found").innerHTML = `<div class="row gap-8 t-small"><div class="spinner"></div>Looking for Ollama on the local network. This takes about 10 seconds.</div>`;
    try {
      const r = await api("llm/discover", { method: "POST" });
      $("#llm-found").innerHTML = r.servers.length ? r.servers.map((sv) => `<div class="found"><div class="grow"><b class="mono">${esc(sv.url)}</b><div class="t-small muted">${esc(sv.models.join(", ") || "no models")}</div></div><button class="btn btn-sm" type="button" data-use="${esc(sv.url)}">Use this</button></div>`).join("")
        : `<p class="t-small muted">No Ollama server answered. Check the laptop steps on the left.</p>`;
    } catch (err) { toastError(err); } finally { b.disabled = false; }
  };
}
$("#llm-form").addEventListener("change", async (e) => {
  const k = e.target.dataset.s; if (!k) return;
  try { await api("settings", { method: "PATCH", body: { [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value } }); toast("Saved"); loadAi(true); } catch (err) { toastError(err); }
});
$("#llm-form").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-use]"); if (!b) return;
  await api("settings", { method: "PATCH", body: { llm_url: b.dataset.use } }); toast("AI server saved"); loadAi(true);
});
$("#gen-report").addEventListener("click", async (e) => {
  const b = e.currentTarget; b.disabled = true;
  $("#report").innerHTML = `<div class="row gap-8 t-small"><div class="spinner"></div>The AI is writing the handover…</div>`;
  try { $("#report").innerHTML = aiText((await api("llm/report", { method: "POST" })).report); } catch (err) { $("#report").innerHTML = `<p class="error-text">${esc(err.message)}</p>`; } finally { b.disabled = false; }
});
$("#ask").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#ask-q").value.trim(); if (!q) return $("#ask-q").focus();
  $("#ask-a").innerHTML = `<div class="row gap-8 t-small"><div class="spinner"></div>Thinking…</div>`;
  try { $("#ask-a").innerHTML = aiText((await api("llm/ask", { method: "POST", body: { question: q } })).answer); } catch (err) { $("#ask-a").innerHTML = `<p class="error-text">${esc(err.message)}</p>`; }
});

// ================================================================== system
let sysTimer = null;
async function loadSystem() {
  clearTimeout(sysTimer);
  if (tab !== "system") return;
  try { renderSystem(await api("system")); } catch { /* ignore */ }
  sysTimer = setTimeout(loadSystem, 3000);
}
function meter(label, value, max, text, warn) {
  const pct = max ? Math.min(100, (value / max) * 100) : 0;
  return `<div class="meter ${warn ? "warn" : ""}"><div class="row between"><span class="t-small strong text-2">${label}</span><b class="num">${text}</b></div><div class="m-track" role="img" aria-label="${label} ${text}"><div style="width:${pct}%"></div></div></div>`;
}
function renderSystem(r) {
  const s = r.system;
  $("#sys-model").textContent = s.model || s.platform || "";
  $("#sys-strip").innerHTML = [
    meter("Processor", s.cpu_percent || 0, 100, s.cpu_percent == null ? "—" : `${Math.round(s.cpu_percent)}%`, s.cpu_percent > 85),
    meter("Memory", s.mem_used || 0, s.mem_total || 1, s.mem_total ? `${fmtBytes(s.mem_used)} of ${fmtBytes(s.mem_total)}` : "—", s.mem_percent > 85),
    meter("Temperature", s.cpu_temp || 0, 85, s.cpu_temp == null ? "not available" : `${s.cpu_temp}°C`, s.cpu_temp > 75),
    meter("MedOS uses", s.rss || 0, s.mem_total || 1, fmtBytes(s.rss)),
    `<div class="meter"><div class="row between"><span class="t-small strong text-2">Running for</span><b>${fmtDur(s.uptime)}</b></div><span class="t-small muted">Database ${fmtBytes(s.db_size)}</span></div>`,
  ].join("");
  const L = r.lock, S = r.scheduler, B = r.bus, A = r.ai, H = r.hardware || {};
  const concepts = [
    ["Dynamic priority scheduling", `${S.dispatches} patients sent to doctors by priority, not arrival order`, "chart"],
    ["Aging", `Every ${S.tick_seconds} s waiting patients gain ${S.aging_rate} points per minute (up to ${S.aging_cap})`, "clock"],
    ["Pre-emption", `${S.preemptions} consultations paused for emergencies (mode: ${S.preemption})`, "refresh"],
    ["Interrupt handling", `${S.interrupts} emergency interrupts, ${H.interrupts ?? 0} from the hardware button`, "bolt"],
    ["Process synchronisation", `Queue lock taken ${L.acquisitions} times, ${L.contended} had to wait (longest ${L.max_wait_ms} ms)`, "lock"],
    ["Producer and consumer", `AI queue: ${A.queued} waiting, ${A.processed} done, ${A.failed} failed`, "spark"],
    ["Inter-process communication", `${B.clients} screens receiving live updates, ${B.published} updates sent`, "wifi"],
    ["Memory management", `MedOS uses ${fmtBytes(s.rss)}; the speech model is ${r.voice.model_loaded ? "loaded" : "loaded only when needed"}`, "cpu"],
  ];
  $("#concepts").innerHTML = concepts.map(([t, d, ic]) => `<div class="concept">${icon(ic)}<div><b>${t}</b><span>${d}</span></div></div>`).join("");
  $("#thr-count").textContent = `${r.threads.length} running`;
  $("#threads").innerHTML = `<thead><tr><th scope="col">Thread</th><th scope="col">What it does</th></tr></thead><tbody>${r.threads.map((t) => `<tr><td class="mono nowrap">${esc(t.name)}</td><td class="t-small">${esc(t.role)}</td></tr>`).join("")}</tbody>`;
}

// ================================================================== settings
async function loadSettings() {
  const s = await api("settings");
  const num = (k, label, hint, step = 1) => `<div class="field"><label for="s-${k}">${label}</label><input class="input num" id="s-${k}" data-s="${k}" type="number" step="${step}" min="0" value="${esc(s[k])}">${hint ? `<p class="helper">${hint}</p>` : ""}</div>`;
  const sw = (k, label, hint) => `<label class="switch"><input type="checkbox" data-s="${k}" ${s[k] ? "checked" : ""}><span class="track"></span><span class="sw-text"><b>${label}</b><span>${hint}</span></span></label>`;
  $("#settings").innerHTML = settingsHtml(s, num, sw);
  bindSettings();
}
function settingsHtml(s, num, sw) {
  return `
    <section class="card"><div class="card-head"><h2>Hospital</h2></div><div class="card-body stack">
      <div class="field"><label for="s-hn">Hospital name</label><input class="input" id="s-hn" data-s="hospital_name" value="${esc(s.hospital_name)}"><p class="helper">Shown on every screen and token slip.</p></div>
      <div class="field"><label for="s-dm">Message on the waiting-room screen</label><input class="input" id="s-dm" data-s="display_message" value="${esc(s.display_message)}"></div>
      ${sw("announce", "Announce calls aloud", "The waiting-room screen says the token and room")}
      <div class="field"><label for="s-vl">Language for speech</label><select class="select" id="s-vl" data-s="voice_lang">${[["en-IN", "English (India)"], ["en-US", "English (US)"], ["en-GB", "English (UK)"], ["ta-IN", "Tamil"], ["hi-IN", "Hindi"]].map(([v, l]) => `<option value="${v}" ${v === s.voice_lang ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    </div></section>
    <section class="card"><div class="card-head"><h2>Who goes next</h2></div><div class="card-body stack">
      ${sw("auto_dispatch", "Send patients to doctors automatically", "The most urgent patient goes to the next free doctor")}
      <div class="field"><span class="label" id="pre-label">When every doctor is busy</span>
        <div class="segmented block" id="s-pre" role="group" aria-labelledby="pre-label">${[["off", "Never interrupt"], ["emergency", "For emergencies"], ["critical", "For any critical"]].map(([v, l]) => `<button type="button" data-v="${v}" aria-pressed="${v === s.preemption}">${l}</button>`).join("")}</div>
        <p class="helper">Interrupting pauses the least urgent consultation. That patient goes next once a doctor is free.</p></div>
      <div class="fields">
        <div class="span-3">${num("aging_rate", "Waiting bonus per minute", "Points added for each minute waited", 0.5)}</div>
        <div class="span-3">${num("aging_cap", "Waiting bonus limit", "Keeps Critical patients out of reach", 10)}</div>
        <div class="span-3">${num("tick_seconds", "Re-order the queue every (s)", "")}</div>
        <div class="span-3">${num("notify_ahead", "Alert patients when this many are ahead", "")}</div>
      </div>
    </div></section>
    <section class="card"><div class="card-head"><h2>Expected consultation length</h2></div><div class="card-body stack">
      <p class="helper">Used for waiting-time estimates until three consultations of that priority have finished today.</p>
      <div class="fields">${LEVELS.map((l) => `<div class="span-3">${num("dur_" + l, `${LEVEL_LABEL[l]} (minutes)`, "")}</div>`).join("")}</div>
    </div></section>
    <section class="card"><div class="card-head"><h2>Alarm devices</h2></div><div class="card-body stack">
      ${sw("buzz_on_call", "Short beep when a patient is called", "Uses the buzzer for normal calls too")}
      ${num("alarm_buzzer_seconds", "Buzzer sounds for (seconds)", "The light keeps blinking until someone acknowledges")}
    </div></section>
    <section class="card"><div class="card-head"><h2>Sign-in</h2></div><div class="card-body stack">
      ${sw("require_pin", "Ask for a PIN on staff screens", "Switch off only for a demo on a private network")}
      <div class="fields">
        <div class="field span-3"><label for="pin-staff">New staff PIN</label><input class="input num" id="pin-staff" inputmode="numeric" maxlength="8" autocomplete="new-password"><p class="helper">4 to 8 digits, for reception and doctors</p></div>
        <div class="field span-3"><label for="pin-admin">New admin PIN</label><input class="input num" id="pin-admin" inputmode="numeric" maxlength="8" autocomplete="new-password"><p class="helper">4 to 8 digits</p></div>
      </div>
      <div><button class="btn" id="save-pins" type="button">${icon("lock")}Change PINs</button></div>
    </div></section>
    <section class="card"><div class="card-head"><h2>Data</h2></div><div class="card-body stack">
      <div class="row wrap gap-8">
        <a class="btn" href="/api/export.csv?day=${new Date().toLocaleDateString("en-CA")}">${icon("download")}Download today (CSV)</a>
        <a class="btn" href="/api/export.csv">${icon("download")}Download everything (CSV)</a>
      </div>
      <div class="row wrap gap-8">
        <button class="btn" id="demo-seed" type="button">${icon("users")}Load demo patients</button>
        <button class="btn btn-danger-outline" id="demo-clear" type="button">${icon("trash")}Delete all patient data</button>
      </div>
    </div></section>`;
}
function bindSettings() {
  $("#s-pre").onclick = async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    await saveSetting("preemption", b.dataset.v);
    $$("#s-pre button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  };
  $("#save-pins").onclick = async () => {
    try {
      const st = $("#pin-staff").value.trim(), ad = $("#pin-admin").value.trim();
      if (!st && !ad) return toast("Type a new PIN first", "error");
      if (st) await api("settings/pin", { method: "POST", body: { role: "staff", pin: st } });
      if (ad) await api("settings/pin", { method: "POST", body: { role: "admin", pin: ad } });
      $("#pin-staff").value = $("#pin-admin").value = ""; toast("PINs changed");
    } catch (err) { toastError(err); }
  };
  $("#demo-seed").onclick = async () => { try { await api("demo/seed", { method: "POST" }); toast("Demo patients loaded"); } catch (err) { toastError(err); } };
  $("#demo-clear").onclick = async () => {
    if (await confirmDialog("Delete all patient data?", "Every patient, alert and log line is deleted. Doctors, rooms, rules and settings stay. This can't be undone.", { confirm: "Delete everything", danger: true })) {
      try { await api("demo/clear", { method: "POST" }); toast("Patient data deleted"); } catch (err) { toastError(err); }
    }
  };
}
async function saveSetting(k, v) { try { await api("settings", { method: "PATCH", body: { [k]: v } }); toast("Saved"); } catch (err) { toastError(err); } }
$("#settings").addEventListener("change", (e) => {
  const k = e.target.dataset.s; if (!k) return;
  saveSetting(k, e.target.type === "checkbox" ? e.target.checked : e.target.value);
});

// ================================================================== render loop
function render(s) {
  if (tab === "overview") renderOverview(s);
  if (tab === "staff") renderStaff(s);
  const n = s.alerts.length, ob = $("#tab-btn-overview"), c = ob.querySelector(".count");
  if (n && !c) ob.insertAdjacentHTML("beforeend", `<span class="count alert" aria-label="${n} active alerts">${n}</span>`);
  else if (c) { if (n) c.textContent = n; else c.remove(); }
}
store.subscribe(render);
setInterval(() => { if (tab === "overview" && store.state) renderOverview(store.state); }, 30000);
showTab(location.hash.slice(1) || "overview");
refreshAnalytics();
loadLog();
