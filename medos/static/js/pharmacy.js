// Pharmacy: a strict FCFS queue. The only way to start work is "Start next order", which takes the head.
import { $, esc, icon, api, createLive, initChrome, toast, toastError, confirmDialog, tokenChip, fmtDur, fmtClock, serverNow, hydrateIcons } from "./core.js";

const live = createLive({ url: "/api/state" });   // keeps the alarm bar and live pill working
initChrome(live);

let st = null;
async function load() {
  try { st = await api("pharmacy/state"); render(); } catch (e) { if (e.status !== 401) toastError(e); }
}
document.addEventListener("medos:event", (e) => { if (["pharmacy", "queue", "tick"].includes(e.detail.kind)) load(); });

function who(o) {
  return `<span class="tok-cell">${o.token ? tokenChip(o.token, "") : `<span class="token"><span>Walk-in</span><b>#${o.id}</b></span>`}</span>`;
}
function stat(ic, label, value, unit, hint) {
  return `<div class="stat"><span class="label">${icon(ic)}${label}</span><span class="value">${value}${unit ? `<small>${unit}</small>` : ""}</span><span class="hint">${hint}</span></div>`;
}

function render() {
  const now = serverNow();
  $("#ph-stats").innerHTML =
    stat("list", "Waiting", st.waiting.length, "", st.waiting.length ? `Last one ready in about ${Math.round(st.waiting.at(-1).eta_min + st.prep_min)} min` : "No one is waiting") +
    stat("pill", "Being prepared", st.preparing.length, "", `About ${st.prep_min} min per order`) +
    stat("check", "Ready to collect", st.ready.length, "", "Patients see their token on the screen") +
    stat("clock", "Average wait", st.avg_wait_min == null ? "—" : Math.round(st.avg_wait_min), st.avg_wait_min == null ? "" : "min", `${st.served_today} orders started today`);
  $("#start-next").disabled = !st.waiting.length;

  $("#ph-queue").innerHTML = st.waiting.length ? `<div class="list-head ph" aria-hidden="true"><span>#</span><span>Token</span><span>Patient and medicines</span><span>Waiting</span><span>Ready in</span><span></span></div>` +
    st.waiting.map((o, i) => `<div class="prow ph ${i === 0 ? "first" : ""}">
      <span class="pos">${o.position}</span>${who(o)}
      <span class="who-cell"><b>${esc(o.patient_name || "Walk-in patient")}${i === 0 ? ` <span class="badge teal">Next</span>` : ""}</b><span class="line" title="${esc(o.items)}">${esc(o.items)}</span><span class="line">${o.prescribed_by ? "Prescribed by " + esc(o.prescribed_by) + " · " : ""}arrived ${fmtClock(o.created_at)}</span></span>
      <span class="time">${fmtDur(now - o.created_at, { short: true })}<span>waiting</span></span>
      <span class="time">~${Math.round(o.eta_min + st.prep_min)} min<span>expected</span></span>
      <span><button class="btn btn-sm btn-ghost" type="button" data-cancel="${o.id}" aria-label="Cancel order ${o.id}">${icon("x")}Cancel</button></span>
    </div>`).join("") : `<div class="list-empty">${icon("pill")}<b>No prescriptions waiting</b><span>Orders appear here when a doctor finishes a consultation with medicines.</span></div>`;

  $("#ph-prep").innerHTML = st.preparing.length ? st.preparing.map((o) => `<div class="prow ph2">
      ${who(o)}<span class="who-cell"><b>${esc(o.patient_name || "Walk-in patient")}</b><span class="line" title="${esc(o.items)}">${esc(o.items)}</span></span>
      <span class="time">${fmtDur(now - o.started_at, { short: true })}<span>preparing</span></span>
      <button class="btn btn-primary btn-sm" type="button" data-ready="${o.id}">${icon("check")}Ready</button></div>`).join("")
    : `<div class="list-empty"><span>Nothing being prepared. Press "Start next order".</span></div>`;

  $("#ph-ready").innerHTML = st.ready.length ? st.ready.map((o) => `<div class="prow ph2">
      ${who(o)}<span class="who-cell"><b>${esc(o.patient_name || "Walk-in patient")}</b><span class="line">ready since ${fmtClock(o.ready_at)}</span></span>
      <span></span><button class="btn btn-sm" type="button" data-collected="${o.id}">${icon("check")}Collected</button></div>`).join("")
    : `<div class="list-empty"><span>No medicines waiting to be collected.</span></div>`;

  $("#ph-done").innerHTML = st.done.length ? st.done.slice(0, 10).map((o) => `<div class="prow ph2 done-row">
      ${who(o)}<span class="who-cell"><b>${esc(o.patient_name || "Walk-in patient")}</b><span class="line">${o.status === "cancelled" ? "Cancelled" : "Collected " + fmtClock(o.collected_at)}</span></span><span></span><span></span></div>`).join("")
    : `<div class="list-empty"><span>Nothing collected yet today.</span></div>`;
}

$("#start-next").addEventListener("click", async (e) => {
  const b = e.currentTarget; b.disabled = true;
  try { const o = await api("pharmacy/next", { method: "POST" }); toast(`Preparing ${o.token ? "token " + o.token : "order #" + o.id}`); } catch (err) { toastError(err); }
  load();
});
document.addEventListener("click", async (e) => {
  const r = e.target.closest("[data-ready]"), c = e.target.closest("[data-collected]"), x = e.target.closest("[data-cancel]");
  try {
    if (r) { await api(`pharmacy/orders/${r.dataset.ready}/status`, { method: "POST", body: { status: "ready" } }); toast("Marked ready. The token now shows on the waiting-room screen."); }
    if (c) { await api(`pharmacy/orders/${c.dataset.collected}/status`, { method: "POST", body: { status: "collected" } }); toast("Collected"); }
    if (x && await confirmDialog("Cancel this order?", "It is removed from the pharmacy queue.", { confirm: "Cancel order", danger: true })) {
      await api(`pharmacy/orders/${x.dataset.cancel}/status`, { method: "POST", body: { status: "cancelled" } }); toast("Order cancelled");
    }
  } catch (err) { toastError(err); }
  if (r || c || x) load();
});
$("#ph-add").addEventListener("submit", async (e) => {
  e.preventDefault();
  const items = $("#ph-items").value.trim();
  if (!items) { $("#ph-items").focus(); return toast("List the medicines first", "error"); }
  try {
    const o = await api("pharmacy/orders", { method: "POST", body: { items, token: $("#ph-token").value.trim(), name: $("#ph-name").value.trim() } });
    e.target.reset(); toast(`Added at the end of the queue (order #${o.id})`); load();
  } catch (err) { toastError(err); }
});
hydrateIcons();
load();
setInterval(() => { if (st) render(); }, 30000);
