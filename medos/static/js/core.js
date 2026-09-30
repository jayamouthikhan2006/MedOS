// MedOS shared client: API, live updates, formatting, icons, priority badges, dialogs, sound.

export const $ = (sel, el = document) => el.querySelector(sel);
export const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));

export function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ------------------------------------------------------------------ icons (one outline family, 2px stroke)
const P = {
  home: '<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10v10h13V10"/><path d="M10 20v-5h4v5"/>',
  desk: '<rect x="5.5" y="4" width="13" height="17" rx="2"/><path d="M9 4.5h6V7H9z"/><path d="M9 12h6M9 16h4"/>',
  stetho: '<path d="M6 3v6a4 4 0 0 0 8 0V3"/><path d="M10 13v1.5a5.5 5.5 0 0 0 11 0V12"/><circle cx="21" cy="10" r="1.8"/>',
  shield: '<path d="M12 3 5 6v5c0 4.6 3 8.4 7 10 4-1.6 7-5.4 7-10V6z"/><path d="m9 12 2 2 4-4"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c1-3.5 3.6-5.5 6.5-5.5s5.5 2 6.5 5.5"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.8c1.8.8 3 2.6 3.5 5.2"/>',
  tv: '<rect x="3" y="4.5" width="18" height="12.5" rx="2"/><path d="M8 21h8M12 17v4"/>',
  kiosk: '<rect x="6" y="2.5" width="12" height="19" rx="2"/><path d="M10.5 18.5h3"/>',
  lab: '<path d="M9 3h6M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2h12.4a1.5 1.5 0 0 0 1.3-2L14 9V3"/><path d="M7 15h10"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  siren: '<path d="M7 18v-6a5 5 0 0 1 10 0v6"/><path d="M4 21h16M5 18h14"/><path d="M12 3v2M4.5 6.5l1.4 1.4M19.5 6.5l-1.4 1.4"/>',
  octagon: '<path d="M7.9 2h8.2L22 7.9v8.2L16.1 22H7.9L2 16.1V7.9z"/><path d="M12 8v5M12 16.5v.5"/>',
  chevronsUp: '<path d="m7 12 5-5 5 5M7 18l5-5 5 5"/>',
  dash: '<path d="M6 12h12"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  chevronRight: '<path d="m9 6 6 6-6 6"/>',
  bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  door: '<path d="M5 21V4a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v17"/><path d="M3 21h18"/><circle cx="15" cy="12" r="1"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/><rect x="9.5" y="9.5" width="5" height="5"/>',
  chip: '<rect x="7" y="7" width="10" height="10" rx="1.5"/><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/>',
  spark: '<path d="M12 3l1.8 4.9L19 9.7l-5.2 1.8L12 16.5l-1.8-5L5 9.7l5.2-1.8z"/><path d="M19 15l.7 1.8 1.8.7-1.8.7L19 20l-.7-1.8-1.8-.7 1.8-.7z"/>',
  sliders: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  print: '<path d="M7 9V3h10v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M7 14h10v7H7z"/>',
  bell: '<path d="M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 21h4"/>',
  volume: '<path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
  terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M13 15h4"/>',
  wifi: '<path d="M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0"/><circle cx="12" cy="19.5" r=".9"/>',
  thermo: '<path d="M14 14.8V5a2 2 0 0 0-4 0v9.8a4 4 0 1 0 4 0z"/>',
  pulse: '<path d="M3 12h4l2-5 4 10 2-5h6"/>',
  drop: '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>',
  gauge: '<path d="M4 17a8 8 0 1 1 16 0"/><path d="m12 17 4-5"/>',
  logout: '<path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h11"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  coffee: '<path d="M4 8h12v6a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z"/><path d="M16 10h2a2.5 2.5 0 0 1 0 5h-2M8 2.5V5M12 2.5V5"/>',
  power: '<path d="M12 3v9"/><path d="M6.3 6.3a8 8 0 1 0 11.4 0"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  send: '<path d="M4 12 20 4l-6 16-3-7z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
  chart: '<path d="M4 20V4M4 20h16"/><path d="M8 16v-4M12 16V8M16 16v-6"/>',
  play: '<path d="M7 4.5v15l12-7.5z"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  led: '<path d="M9 17V9a3 3 0 0 1 6 0v8"/><path d="M7 17h10M10 17v4M14 17v4"/><path d="M5 5l1.5 1.5M19 5l-1.5 1.5M12 2v1.5"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  arrowLeft: '<path d="M19 12H5M11 6l-6 6 6 6"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
  clipboard: '<rect x="5.5" y="4" width="13" height="17" rx="2"/><path d="M9 4.5h6V7H9z"/><path d="m9 14 2 2 4-4"/>',
  qr: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><path d="M14 14h3v3h-3zM17 17h4v4h-4M20 14h1"/>',
};

export function icon(name, cls = "") {
  return `<svg class="${cls}" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${P[name] || ""}</svg>`;
}
export function hydrateIcons(root = document) {
  $$("[data-icon]", root).forEach((el) => {
    if (!el.dataset.iconDone) { el.insertAdjacentHTML("afterbegin", icon(el.dataset.icon)); el.dataset.iconDone = "1"; }
  });
}

// ------------------------------------------------------------------ priorities
export const LEVELS = ["critical", "high", "medium", "low"];
export const LEVEL_LABEL = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };
export const LEVEL_TARGET = { critical: "Seen immediately", high: "Seen within 15 min", medium: "Seen within 1 hour", low: "Seen within 2 hours" };
const LEVEL_ICON = { critical: "octagon", high: "chevronsUp", medium: "dash", low: "chevronDown" };
export const LEVEL_COLOR = { critical: "var(--crit)", high: "var(--high)", medium: "var(--med)", low: "var(--low)" };

/** Priority badge: colour + icon + word, so it never relies on colour alone. */
export function prio(level, { lg = false, text } = {}) {
  if (!LEVELS.includes(level)) return "";
  return `<span class="prio ${lg ? "lg" : ""}" data-level="${level}" title="${LEVEL_TARGET[level]}">${icon(LEVEL_ICON[level])}${esc(text || LEVEL_LABEL[level])}</span>`;
}
export const levelChip = (lvl, text) => prio(lvl, { text });
export function tokenChip(token, level, caption = "Token") {
  return `<span class="token" data-level="${esc(level || "")}"><span>${esc(caption)}</span><b>${esc(token)}</b></span>`;
}

// ------------------------------------------------------------------ time & formatting
let clockOffset = 0;
export const serverNow = () => Date.now() / 1000 + clockOffset;
export function syncClock(serverTime) { if (serverTime) clockOffset = serverTime - Date.now() / 1000; }

export function fmtDur(sec, { short = false } = {}) {
  if (sec == null || isNaN(sec)) return "—";
  sec = Math.max(0, sec);
  const m = Math.round(sec / 60);
  if (sec < 45) return short ? "now" : "under a minute";
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60), r = m % 60;
  return r ? `${h} h ${String(r).padStart(2, "0")} min` : `${h} h`;
}
export function fmtClock(ts, withSec = false) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", ...(withSec ? { second: "2-digit" } : {}), hour12: false });
}
export function fmtBytes(b) {
  if (b == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(b < 10 && i ? 1 : 0)} ${u[i]}`;
}
export const SOURCE_LABEL = { gpio: "the emergency button", reception: "reception", kiosk: "the kiosk", doctor: "a doctor", admin: "admin", voice: "voice" };
export const sourceLabel = (s) => SOURCE_LABEL[s] || String(s || "").replace("emergency-", "emergency, ");
export const sexLabel = (s) => ({ male: "Male", female: "Female", other: "Other" }[s] || "");
export const sexShort = (s) => ({ male: "M", female: "F", other: "X" }[s] || "");
export function ageSex(p) { return [p.age ? `${p.age} yrs` : null, sexLabel(p.sex)].filter(Boolean).join(" · "); }

// ------------------------------------------------------------------ API
export class ApiError extends Error { constructor(message, status) { super(message); this.status = status; } }
export async function api(path, { method = "GET", body, raw = false, headers = {} } = {}) {
  const opts = { method, headers: { ...headers }, credentials: "same-origin" };
  if (body !== undefined) {
    if (body instanceof Blob || body instanceof ArrayBuffer) opts.body = body;
    else { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  }
  let res;
  try { res = await fetch(path.startsWith("/") ? path : "/api/" + path, opts); }
  catch { throw new ApiError("Can't reach the MedOS server. Check the network connection.", 0); }
  if (raw) return res;
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    if (res.status === 401 && !location.pathname.startsWith("/login")) location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
    throw new ApiError((data && data.error) || `Request failed (${res.status})`, res.status);
  }
  return data;
}

// ------------------------------------------------------------------ live store (SSE + refetch)
export function createLive({ url = "/api/state", onEvent } = {}) {
  const subs = new Set();
  const store = { state: null, connected: false, subscribe(fn) { subs.add(fn); if (store.state) fn(store.state, null); return () => subs.delete(fn); }, refresh };
  let timer = null, es = null, pollTimer = null, failures = 0, inflight = false, again = false;
  async function refresh(reason) {
    if (inflight) { again = true; return; }
    inflight = true;
    try {
      const s = await api(url);
      syncClock(s.server_time);
      store.state = s;
      subs.forEach((fn) => fn(s, reason));
      document.dispatchEvent(new CustomEvent("medos:updated"));
    } catch (e) { if (e.status !== 401) console.warn(e); }
    finally { inflight = false; if (again) { again = false; schedule("again"); } }
  }
  function schedule(reason) { clearTimeout(timer); timer = setTimeout(() => refresh(reason), 120); }
  function setConnected(on) { store.connected = on; document.dispatchEvent(new CustomEvent("medos:live", { detail: on })); }
  function connect() {
    if (!window.EventSource) return startPolling();
    es = new EventSource("/api/stream");
    es.addEventListener("hello", () => { failures = 0; setConnected(true); stopPolling(); refresh("hello"); });
    ["queue", "doctors", "rooms", "called", "emergency", "alert_ack", "preempted", "settings", "tick", "log", "voice"].forEach((k) => es.addEventListener(k, (ev) => {
      let data = {}; try { data = JSON.parse(ev.data); } catch { /* ignore */ }
      if (onEvent) onEvent(k, data);
      document.dispatchEvent(new CustomEvent("medos:event", { detail: { kind: k, data } }));
      if (k !== "log" && k !== "voice") schedule(k);
    }));
    es.onerror = () => {
      setConnected(false); failures++;
      if (failures > 3) { es.close(); startPolling(); setTimeout(() => { failures = 0; connect(); }, 15000); }
    };
  }
  function startPolling() { if (!pollTimer) pollTimer = setInterval(() => refresh("poll"), 4000); }
  function stopPolling() { clearInterval(pollTimer); pollTimer = null; }
  connect(); refresh("initial");
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh("visible"); });
  return store;
}

// ------------------------------------------------------------------ toasts
function toastHost() {
  let h = $(".toasts");
  if (!h) { h = document.createElement("div"); h.className = "toasts"; h.setAttribute("role", "status"); h.setAttribute("aria-live", "polite"); document.body.appendChild(h); }
  return h;
}
export function toast(message, kind = "ok", ms = 4000) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `${icon(kind === "error" ? "info" : kind === "alarm" ? "siren" : "check")}<div>${esc(message)}</div>`;
  toastHost().appendChild(el);
  setTimeout(() => { el.style.transition = "opacity .25s"; el.style.opacity = "0"; setTimeout(() => el.remove(), 260); }, ms);
}
export function toastError(e) { toast(e && e.message ? e.message : String(e), "error", 5500); }

// ------------------------------------------------------------------ modal & drawer
function overlay(layer, { dismissible = true } = {}) {
  return new Promise((resolve) => {
    const scrim = document.createElement("div"); scrim.className = "scrim";
    const prev = document.activeElement;
    const close = (v) => { scrim.remove(); layer.remove(); document.removeEventListener("keydown", onKey); if (prev && prev.focus) prev.focus(); resolve(v); };
    const onKey = (e) => { if (e.key === "Escape" && dismissible) close(null); };
    if (dismissible) scrim.addEventListener("click", () => close(null));
    layer.addEventListener("click", (e) => { if (e.target === layer && dismissible) close(null); });
    document.addEventListener("keydown", onKey);
    document.body.append(scrim, layer);
    layer._close = close;
  });
}
function wireActions(root, actions) {
  root.addEventListener("click", async (e) => {
    if (e.target.closest("[data-close]")) return root._close(null);
    const b = e.target.closest("[data-act]"); if (!b) return;
    const a = actions[+b.dataset.act];
    if (a.onClick) {
      b.disabled = true;
      try { const r = await a.onClick(root); if (r !== false) root._close(r === undefined ? a.value ?? true : r); }
      catch (err) { toastError(err); } finally { b.disabled = false; }
    } else root._close(a.value ?? true);
  });
}
const actionButtons = (actions) => actions.map((a, i) => a.spacer ? `<span class="grow"></span>`
  : `<button type="button" class="btn ${a.kind ? "btn-" + a.kind : ""}" data-act="${i}">${a.icon ? icon(a.icon) : ""}${esc(a.label)}</button>`).join("");

/** Centered dialog. actions: [{label, kind, icon, value, onClick(root) -> value|false}] */
export function modal({ title, body = "", actions = [], wide = false, onOpen, dismissible = true }) {
  const wrap = document.createElement("div");
  wrap.className = "modal-wrap";
  wrap.innerHTML = `<div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true" aria-labelledby="modal-title">
    <div class="modal-head"><h2 id="modal-title">${esc(title)}</h2>${dismissible ? `<button class="icon-btn" data-close aria-label="Close">${icon("x")}</button>` : ""}</div>
    <div class="modal-body">${body}</div>
    ${actions.length ? `<div class="modal-foot">${actionButtons(actions)}</div>` : ""}
  </div>`;
  const p = overlay(wrap, { dismissible });
  wireActions(wrap, actions);
  const m = wrap.querySelector(".modal");
  if (onOpen) onOpen(m, wrap._close);
  const f = m.querySelector("[autofocus], input, textarea, select, .btn-primary, .btn-danger");
  if (f) f.focus();
  return p;
}
export function confirmDialog(title, message, { confirm = "Confirm", danger = false } = {}) {
  return modal({ title, body: `<p class="text-2">${esc(message)}</p>`, actions: [{ label: "Cancel", value: false, kind: "ghost" }, { label: confirm, value: true, kind: danger ? "danger" : "primary" }] });
}

/** Side panel for details and actions. Returns {el, close, promise}. */
export function drawer({ title, subtitle = "", head = "", body = "", actions = [] }) {
  const el = document.createElement("aside");
  el.className = "drawer";
  el.setAttribute("role", "dialog"); el.setAttribute("aria-modal", "true"); el.setAttribute("aria-labelledby", "drawer-title");
  el.innerHTML = `<div class="drawer-head">${head}<div class="grow"><h2 id="drawer-title">${esc(title)}</h2>${subtitle ? `<p class="muted t-small">${subtitle}</p>` : ""}</div>
      <button class="icon-btn" data-close aria-label="Close panel">${icon("x")}</button></div>
    <div class="drawer-body">${body}</div>
    ${actions.length ? `<div class="drawer-foot">${actionButtons(actions)}</div>` : ""}`;
  const promise = overlay(el);
  wireActions(el, actions);
  el.querySelector("[data-close]").focus();
  return { el, close: (v) => el._close(v), promise };
}

// ------------------------------------------------------------------ sound
let actx = null;
export function audio() {
  if (!actx) { try { actx = new (window.AudioContext || window.webkitAudioContext)(); } catch { return null; } }
  if (actx.state === "suspended") actx.resume();
  return actx;
}
function tone(freq, start, dur, type = "sine", gain = 0.18) {
  const a = audio(); if (!a) return;
  const o = a.createOscillator(), g = a.createGain();
  o.type = type; o.frequency.value = freq;
  g.gain.setValueAtTime(0, a.currentTime + start);
  g.gain.linearRampToValueAtTime(gain, a.currentTime + start + 0.02);
  g.gain.exponentialRampToValueAtTime(0.0001, a.currentTime + start + dur);
  o.connect(g).connect(a.destination);
  o.start(a.currentTime + start); o.stop(a.currentTime + start + dur + 0.05);
}
export function chime() { tone(659, 0, 0.5); tone(880, 0.22, 0.7); tone(1319, 0.44, 0.9, "sine", 0.12); }
export function softPing() { tone(988, 0, 0.35, "sine", 0.12); }
let sirenTimer = null;
export function siren(on) {
  clearInterval(sirenTimer); sirenTimer = null;
  if (!on) return;
  const burst = () => { tone(880, 0, 0.22, "square", 0.07); tone(660, 0.25, 0.22, "square", 0.07); };
  burst(); let n = 0;
  sirenTimer = setInterval(() => { burst(); if (++n > 8) { clearInterval(sirenTimer); sirenTimer = null; } }, 1100);
}
export function speak(text, lang = "en-IN") {
  if (!("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = lang; u.rate = 0.92;
  const v = speechSynthesis.getVoices().find((x) => x.lang === lang) || speechSynthesis.getVoices().find((x) => x.lang && x.lang.startsWith("en"));
  if (v) u.voice = v;
  speechSynthesis.speak(u);
}
export function spokenToken(t) { return String(t).split("").join(" "); }

// ------------------------------------------------------------------ theme, clock, live pill, nav
export function toggleTheme() {
  const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("medos-theme", t); } catch { /* private mode */ }
  document.dispatchEvent(new CustomEvent("medos:theme", { detail: t }));
  return t;
}
export function startClock(el) {
  const tick = () => { el.textContent = new Date(serverNow() * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }); };
  tick(); setInterval(tick, 5000);
}
export function bindLive() {
  let on = false, last = null;
  const paint = () => $$("[data-live]").forEach((pill) => {
    pill.classList.toggle("on", on); pill.classList.toggle("off", !on);
    const t = pill.querySelector("[data-live-text]");
    if (t) t.textContent = on ? `Live${last ? " · " + fmtClock(last) : ""}` : "Reconnecting…";
    pill.title = on ? "Receiving live updates" : "Lost connection to MedOS. Retrying.";
  });
  document.addEventListener("medos:live", (e) => { on = e.detail; paint(); });
  document.addEventListener("medos:updated", () => { last = serverNow(); paint(); });
  paint();
}
function bindNav() {
  const btn = $("#menu-btn"), side = $("#sidebar");
  if (!btn || !side) return;
  let scrim = null;
  const set = (open) => {
    side.classList.toggle("open", open); btn.setAttribute("aria-expanded", String(open));
    if (open) { scrim = document.createElement("div"); scrim.className = "nav-scrim"; scrim.onclick = () => set(false); document.body.appendChild(scrim); side.querySelector("a").focus(); }
    else if (scrim) { scrim.remove(); scrim = null; }
  };
  btn.addEventListener("click", () => set(!side.classList.contains("open")));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && side.classList.contains("open")) { set(false); btn.focus(); } });
}
function bindThemeToggle() {
  const tt = $("#theme-toggle"); if (!tt) return;
  const paint = () => { const dark = document.documentElement.dataset.theme === "dark"; tt.innerHTML = icon(dark ? "sun" : "moon") + (dark ? "Light mode" : "Dark mode"); };
  paint(); tt.addEventListener("click", () => { toggleTheme(); paint(); });
}

// Minimal markdown for AI text: "- " bullets and short heading lines.
export function aiText(text) {
  const lines = String(text || "").replace(/\*\*/g, "").split(/\n+/).map((l) => l.trim()).filter(Boolean);
  let html = "", inList = false;
  for (const l of lines) {
    if (/^[-*•]\s+/.test(l)) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${esc(l.replace(/^[-*•]\s+/, ""))}</li>`; }
    else { if (inList) { html += "</ul>"; inList = false; } const h = l.replace(/^#+\s*/, "").replace(/:$/, ""); html += h.length < 40 ? `<h4>${esc(h)}</h4>` : `<p>${esc(l)}</p>`; }
  }
  if (inList) html += "</ul>";
  return html;
}
export function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// Emergency banner shared by staff pages.
export function bindAlarm(store) {
  const bar = $("#alarm"); if (!bar) return;
  let lastIds = "", soundOn = false;
  store.subscribe((s) => {
    const alerts = s.alerts || [];
    bar.classList.toggle("on", alerts.length > 0);
    if (!alerts.length) { siren(false); lastIds = ""; return; }
    const a = alerts[0];
    const p = [...(s.queue || []), ...(s.consulting || [])].find((x) => x.id === a.patient_id);
    const where = p && p.status === "in_consultation" ? `is with ${p.doctor || "a doctor"}${p.room ? " in " + p.room : ""}` : "is first in the queue";
    bar.querySelector(".msg").innerHTML = `<b>Emergency: Token ${esc(p ? p.token : "")} ${esc(where)}.</b> Raised from ${esc(sourceLabel(a.source))} at ${fmtClock(a.ts)}${alerts.length > 1 ? ` · ${alerts.length} active alerts` : ""}.`;
    const ids = alerts.map((x) => x.id).join(",");
    if (ids !== lastIds) { lastIds = ids; if (soundOn) siren(true); }
  });
  const ackBtn = bar.querySelector("[data-ack]");
  if (ackBtn) ackBtn.addEventListener("click", async () => {
    try { await api("alerts/ack_all", { method: "POST" }); siren(false); toast("Alert acknowledged. The LED and buzzer are off."); } catch (e) { toastError(e); }
  });
  document.addEventListener("pointerdown", () => { soundOn = true; audio(); }, { once: true });
}

export function initChrome(store) {
  hydrateIcons();
  bindNav();
  bindThemeToggle();
  bindLive();
  if (store) bindAlarm(store);
}
