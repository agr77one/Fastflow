// Flowkey web dashboard — vanilla JS, no build step. All data comes from the
// local daemon's existing /action/* JSON API; same-origin fetch carries the
// X-FFP-API header (the daemon's CSRF gate, see SPEC V41). All DOM writes use
// textContent/createElement — no innerHTML — so daemon data can never inject
// markup (and the CSP forbids it anyway).
"use strict";

const API_HEADER = "1"; // must match ffp_daemon.API_VERSION

async function action(name, args = {}) {
  const res = await fetch(`/action/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8", "X-FFP-API": API_HEADER },
    body: JSON.stringify({ args }),
  });
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || `action ${name} failed`);
  return data.result;
}

const $ = (id) => document.getElementById(id);

function setText(id, value) {
  $(id).textContent = value === undefined || value === null || value === "" ? "?" : String(value);
}

function setStatus(id, message, ok = true) {
  const el = $(id);
  el.textContent = message;
  el.className = ok ? "ok" : "bad";
}

// In-page confirmation modal. We never use native confirm()/alert()/prompt() —
// they break the dashboard's look and feel. Returns a Promise<boolean>. All DOM
// via createElement/textContent (no innerHTML; CSP-safe).
function confirmDialog(message, okLabel = "Confirm") {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    const box = document.createElement("div");
    box.className = "card modal-box";
    const msg = document.createElement("div");
    msg.className = "modal-msg";
    msg.textContent = message;            // pre-wrap CSS preserves \n
    const row = document.createElement("div");
    row.className = "card-actions modal-actions";
    const cancel = document.createElement("button");
    cancel.className = "btn";
    cancel.textContent = "Cancel";
    const ok = document.createElement("button");
    ok.className = "btn btn-danger";
    ok.textContent = okLabel;
    row.append(cancel, ok);
    box.append(msg, row);
    overlay.append(box);
    document.body.append(overlay);
    const close = (val) => {
      overlay.remove();
      document.removeEventListener("keydown", onKey);
      resolve(val);
    };
    function onKey(e) {
      if (e.key === "Escape") close(false);
      else if (e.key === "Enter") close(true);
    }
    cancel.addEventListener("click", () => close(false));
    ok.addEventListener("click", () => close(true));
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(false); });
    document.addEventListener("keydown", onKey);
    ok.focus();
  });
}

// Mirrors AHK HumanHotkey(): "^+g" -> "Ctrl+Shift+G".
function humanHotkey(hk) {
  if (!hk) return "?";
  const mods = { "^": "Ctrl", "+": "Shift", "!": "Alt", "#": "Win" };
  const parts = [];
  let i = 0;
  while (i < hk.length && mods[hk[i]]) parts.push(mods[hk[i++]]);
  const key = hk.slice(i);
  parts.push(key.length === 1 ? key.toUpperCase() : key);
  return parts.join("+");
}

// Light validity check mirroring the AHK rule: optional ^+!# modifiers then
// exactly ONE key (letter/digit or F1–F24). The running AHK still re-probes on
// reload and toasts if a binding is rejected.
function isValidHotkey(hk) {
  return /^[\^+!#]*([A-Za-z0-9]|[Ff]([1-9]|1[0-9]|2[0-4]))$/.test(hk || "");
}

const PERF_LABELS = { balanced: "🟡 Balanced", max: "🔴 Max throughput" };
const TONE_LABELS = { formal: "🎩 Formal", casual: "👕 Casual", friendly: "🤝 Friendly" };

// ---- LLM providers -----------------------------------------------------------
// The daemon resolves the *effective* provider (configured one, with fallback
// when it's unavailable). The dropdown edits the *configured* provider; the
// status table shows both. Capability gating hides FLM-only controls (runtime
// update check, performance modes, benchmark) when Ollama is selected.

const PROVIDER_LABELS = { fastflowlm: "FastFlowLM", ollama: "Ollama" };
const PROVIDER_DEFAULTS = {
  fastflowlm: { base_url: "http://127.0.0.1:52625", timeout_seconds: 60 },
  ollama: { base_url: "http://127.0.0.1:11434", timeout_seconds: 120 },
};

// Filled by loadConfig() from the config snapshot.
let providerState = { configured: "fastflowlm", active: "fastflowlm", configs: {}, status: null };

function providerProfile(key) {
  const saved = providerState.configs[key] || {};
  const dflt = PROVIDER_DEFAULTS[key] || PROVIDER_DEFAULTS.fastflowlm;
  return {
    base_url: saved.base_url || dflt.base_url,
    timeout_seconds: saved.timeout_seconds || dflt.timeout_seconds,
  };
}

function renderProviderStatus(status) {
  const rows = [];
  for (const key of ["fastflowlm", "ollama"]) {
    const st = ((status || {}).providers || {})[key] || {};
    const marks = [];
    marks.push(st.installed ? "installed ✓" : "not installed");
    marks.push(st.reachable ? "running ✓" : "not running");
    let tag = "";
    if (key === providerState.active) tag = " — active";
    else if (key === providerState.configured) tag = " — configured";
    rows.push([PROVIDER_LABELS[key], `${marks.join(" · ")}${tag}`]);
  }
  fillTable("provider-status-body", rows);
}

function applyProviderCaps() {
  const sel = $("cfg-provider").value || "fastflowlm";
  const isFlm = sel === "fastflowlm";
  $("flm-runtime-block").hidden = !isFlm;
  document.querySelectorAll('input[name="perf"]').forEach((r) => (r.disabled = !isFlm));
  $("perf-note").hidden = isFlm;
  $("models-title").textContent = `Installed models — ${PROVIDER_LABELS[sel]}`;
  $("pull-hint").hidden = isFlm;
  $("pull-name").placeholder = isFlm ? "model name, e.g. qwen3.5:4b" : "model name, e.g. llama3.2:3b";
  const note = $("provider-note");
  if (sel !== providerState.configured) {
    note.textContent = `Switching to ${PROVIDER_LABELS[sel]} — click "Save all settings" to apply.`;
    note.hidden = false;
  } else if (providerState.active !== providerState.configured) {
    note.textContent = `${PROVIDER_LABELS[providerState.configured]} is unavailable — currently running on ${PROVIDER_LABELS[providerState.active]}.`;
    note.hidden = false;
  } else {
    note.hidden = true;
  }
}

function onProviderChanged() {
  const sel = $("cfg-provider").value;
  const profile = providerProfile(sel);
  $("cfg-base-url").value = profile.base_url;
  $("cfg-timeout").value = profile.timeout_seconds;
  applyProviderCaps();
}

async function startProviderServer() {
  const label = PROVIDER_LABELS[providerState.configured] || "server";
  setStatus("provider-start-status", `Starting ${label}…`);
  try {
    const out = await action("start");
    setStatus("provider-start-status", `✅ ${out || "started"}`);
  } catch (e) {
    setStatus("provider-start-status", `⚠ ${e.message}`, false);
  }
  loadServerStatus();
  loadConfig();
}

// ---- Day/night theme ---------------------------------------------------------
// Three modes cycled by the topbar button: auto (follow system) -> light -> dark.
// Manual choice is set as data-theme on <html> (styles.css overrides) and
// persisted in localStorage.

const THEME_KEY = "flowkey-theme";
const THEME_ORDER = ["auto", "light", "dark"];
const THEME_LABELS = { auto: "🌓 Auto", light: "☀️ Day", dark: "🌙 Night" };

function applyTheme(mode) {
  const root = document.documentElement;
  if (mode === "light" || mode === "dark") root.setAttribute("data-theme", mode);
  else root.removeAttribute("data-theme");
  $("theme-btn").textContent = THEME_LABELS[mode] || THEME_LABELS.auto;
}

function cycleTheme() {
  const current = localStorage.getItem(THEME_KEY) || "auto";
  const next = THEME_ORDER[(THEME_ORDER.indexOf(current) + 1) % THEME_ORDER.length];
  localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
}

function fillTable(tbodyId, rows) {
  const body = $(tbodyId);
  body.replaceChildren();
  for (const cells of rows) {
    const tr = document.createElement("tr");
    for (const cell of cells) {
      const td = document.createElement("td");
      td.textContent = cell;
      tr.append(td);
    }
    body.append(tr);
  }
  return rows.length;
}

// ---- Health pill -----------------------------------------------------------

async function refreshHealth() {
  const pill = $("daemon-pill");
  try {
    const res = await fetch("/healthz");
    const data = await res.json();
    pill.textContent = "daemon healthy";
    pill.className = "pill pill-ok";
    setText("app-version", "v" + data.version);
    setText("ov-daemon", "✅ healthy");
    $("ov-daemon").className = "ok";
    setText("ov-version", data.version);
  } catch {
    pill.textContent = "daemon unreachable";
    pill.className = "pill pill-bad";
    setText("ov-daemon", "⚠ not responding");
    $("ov-daemon").className = "bad";
  }
}

// ---- Overview --------------------------------------------------------------

async function loadOverview() {
  try {
    const cfg = await action("config_snapshot");
    const llm = cfg.llm || {};
    const prov = PROVIDER_LABELS[llm.provider] || llm.provider || "?";
    const fellBack = llm.configured_provider && llm.provider !== llm.configured_provider;
    setText("ov-provider", fellBack ? `${prov} (fallback from ${PROVIDER_LABELS[llm.configured_provider]})` : prov);
    setText("ov-model", llm.model || cfg.flm_model);
    setText("ov-url", llm.base_url || cfg.flm_base_url);
    setText("ov-perf", PERF_LABELS[(cfg.server || {}).performance_mode] || (cfg.server || {}).performance_mode);
    setText("ov-tone", TONE_LABELS[(cfg.tone || {}).preset] || (cfg.tone || {}).preset);
    setText("ov-history", cfg.history_store_text ? "Visible (text stored)" : "Redacted (text not stored)");
    setText("ov-vault", (cfg.notes || {}).vault_dir);
    const hk = cfg.hotkeys || {};
    setText("hk-grammar", humanHotkey(hk.grammar_fix));
    setText("hk-chat", humanHotkey(hk.open_chat));
    setText("hk-note", humanHotkey(hk.capture_note));
    setText("hk-ask", humanHotkey(hk.ask_chat));
  } catch (e) {
    setText("ov-model", `error: ${e.message}`);
  }
  try {
    const stats = await action("stats");
    const byMode = stats.by_mode || {};
    setText("ov-total", stats.total ?? 0);
    setText("ov-grammar", byMode.grammar ?? 0);
    setText("ov-prompt", byMode.prompt ?? 0);
  } catch {
    /* totals stay at 0 when history is empty or the action fails */
  }
}

// ---- Telemetry -------------------------------------------------------------

const STAT_ROWS = [
  ["total", "Total requests"],
  ["avg_latency_seconds", "Avg latency (s)"],
  ["p50_latency_seconds", "p50 latency (s)"],
  ["p95_latency_seconds", "p95 latency (s)"],
  ["avg_tok_per_sec", "Avg tok/s"],
  ["p50_tok_per_sec", "p50 tok/s"],
  ["total_prompt_tokens", "Prompt tokens"],
  ["total_completion_tokens", "Completion tokens"],
];

async function loadTelemetry() {
  try {
    const dash = await action("dashboard_data");
    renderHours(dash.hour_buckets || []);
  } catch (e) {
    $("hours-chart").textContent = `Hours data unavailable: ${e.message}`;
  }
  try {
    const stats = await action("stats");
    fillTable("stats-body", STAT_ROWS.filter(([k]) => stats[k] !== undefined).map(([k, label]) => [label, String(stats[k])]));
  } catch (e) {
    fillTable("stats-body", [[`Stats unavailable: ${e.message}`, ""]]);
  }
  loadNotificationsLog();
}

// Category id -> human label. Mirrors ffp_notifications.CATEGORY_LABELS.
const NOTIF_CATEGORY_LABELS = {
  errors: "Errors & warnings",
  clipboard_suggestions: "Clipboard suggestions",
  updates: "Update checks",
  diagnostics: "Diagnostics",
  settings: "Settings changes",
  lifecycle: "App lifecycle",
  action_result: "Action results",
};
// Per-event toggle ids (must match the ntf-cat-* checkbox ids in index.html and
// ffp_notifications.CATEGORIES).
const NOTIF_CATEGORY_IDS = Object.keys(NOTIF_CATEGORY_LABELS);

async function loadNotificationsLog() {
  try {
    const entries = await action("notifications_log", { limit: 50 });
    const rows = entries.map((e) => [
      String(e.ts || "-").slice(0, 19).replace("T", " "),
      NOTIF_CATEGORY_LABELS[e.category] || e.category || "?",
      e.shown ? "shown" : `muted (${e.reason || "?"})`,
      e.message || "",
    ]);
    const n = fillTable("notif-log-body", rows);
    $("notif-log-empty").hidden = n > 0;
  } catch (e) {
    fillTable("notif-log-body", [[`Notifications log unavailable: ${e.message}`, "", "", ""]]);
    $("notif-log-empty").hidden = true;
  }
}

function renderHours(buckets) {
  const chart = $("hours-chart");
  const axis = $("hours-axis");
  chart.replaceChildren();
  axis.replaceChildren();
  const max = Math.max(1, ...buckets);
  buckets.forEach((count, hour) => {
    const bar = document.createElement("div");
    bar.className = count > 0 ? "bar" : "bar empty";
    bar.style.height = `${Math.max(2, Math.round((count / max) * 100))}%`;
    bar.title = `${String(hour).padStart(2, "0")}:00 — ${count}`;
    chart.append(bar);
    const tick = document.createElement("span");
    tick.textContent = hour % 3 === 0 ? String(hour).padStart(2, "0") : "";
    axis.append(tick);
  });
}

// ---- History ---------------------------------------------------------------

async function loadHistory() {
  try {
    const entries = await action("recent_history", { limit: 50 });
    const rows = entries.map((e) => [
      String(e.timestamp || e.ts || "-").slice(0, 19).replace("T", " "),
      e.mode || "?",
      e.input_chars ?? "?",
      e.output_chars ?? "?",
      `${e.elapsed_seconds ?? e.api_time ?? "-"}s`,
      e.tok_per_sec ?? "-",
      e.completion_tokens ?? "-",
    ]);
    const n = fillTable("history-body", rows);
    $("history-empty").hidden = n > 0;
  } catch (e) {
    fillTable("history-body", [[`History unavailable: ${e.message}`, "", "", "", "", "", ""]]);
  }
}

// ---- Notes -----------------------------------------------------------------

// Browse the vault: empty query lists the newest notes (notes_list); a query
// runs the ranked search (note_search) and shows snippets instead of dates.
let notesCategories = [];     // buckets from config, for the reader's Move dropdown
let currentNoteRelpath = "";  // note open in the reader pane

// Render the notes table with clickable rows that open the reader. `col3` maps
// a result row to its third-column text (snippet for search, modified for list).
function renderNotesTable(results, col3) {
  const body = $("notes-body");
  body.replaceChildren();
  for (const r of results) {
    const tr = document.createElement("tr");
    for (const cell of [r.title, r.category, col3(r)]) {
      const td = document.createElement("td");
      td.textContent = cell || "";
      tr.append(td);
    }
    if (r.relpath) {
      tr.classList.add("note-row");
      tr.tabIndex = 0;
      const open = () => openNoteReader(r.relpath);
      tr.addEventListener("click", open);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
    }
    body.append(tr);
  }
  return results.length;
}

async function browseNotes() {
  const query = $("note-query").value.trim();
  try {
    if (query) {
      const res = await action("note_search", { query, limit: 20 });
      $("notes-col3").textContent = "Snippet";
      const n = renderNotesTable(res.results || [], (r) => r.snippet || "");
      $("notes-count").textContent = `(${res.count} match${res.count === 1 ? "" : "es"})`;
      $("notes-empty").hidden = n > 0;
    } else {
      const res = await action("notes_list", { limit: 20 });
      $("notes-col3").textContent = "Modified";
      const n = renderNotesTable(res.results || [], (r) => r.modified);
      $("notes-count").textContent = `(${res.count} total — newest 20)`;
      $("notes-empty").hidden = n > 0;
    }
  } catch (e) {
    fillTable("notes-body", [[`Notes unavailable: ${e.message}`, "", ""]]);
    $("notes-empty").hidden = true;
  }
}

async function openNoteReader(relpath) {
  try {
    const n = await action("note_get", { relpath });
    if (!n.ok) { setStatus("nr-status", n.error || "note not found", false); return; }
    currentNoteRelpath = n.relpath || relpath;
    $("nr-title").textContent = n.title || "(untitled)";
    $("nr-body").textContent = n.body || "";
    const src = $("nr-source");
    if (n.source) { src.textContent = n.source; src.href = n.source; src.hidden = false; }
    else { src.hidden = true; src.removeAttribute("href"); }
    // Bucket dropdown: configured categories + inbox, plus the note's current
    // category if it isn't in the list.
    const cats = [...notesCategories];
    if (!cats.includes("inbox")) cats.push("inbox");
    if (n.category && !cats.includes(n.category)) cats.unshift(n.category);
    const sel = $("nr-bucket");
    sel.replaceChildren();
    for (const c of cats) {
      const o = document.createElement("option");
      o.value = c; o.textContent = c;
      if (c === n.category) o.selected = true;
      sel.append(o);
    }
    setStatus("nr-status", "");
    $("note-reader").hidden = false;
    $("note-reader").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) {
    setStatus("nr-status", `Open failed: ${e.message}`, false);
  }
}

async function moveNoteToBucket() {
  if (!currentNoteRelpath) return;
  const category = $("nr-bucket").value;
  try {
    const res = await action("note_move", { relpath: currentNoteRelpath, category });
    if (!res.ok) { setStatus("nr-status", res.error || "move failed", false); return; }
    currentNoteRelpath = res.relpath || currentNoteRelpath;
    setStatus("nr-status", `Moved to ${res.category}.`);
    browseNotes();
  } catch (e) {
    setStatus("nr-status", `Move failed: ${e.message}`, false);
  }
}

async function deleteCurrentNote() {
  if (!currentNoteRelpath) return;
  if (!(await confirmDialog("Delete this note from the vault?", "Delete"))) return;
  try {
    const res = await action("note_delete", { relpath: currentNoteRelpath });
    if (!res.ok) { setStatus("nr-status", res.error || "delete failed", false); return; }
    $("note-reader").hidden = true;
    currentNoteRelpath = "";
    browseNotes();
  } catch (e) {
    setStatus("nr-status", `Delete failed: ${e.message}`, false);
  }
}

async function loadNotes() {
  browseNotes();
  try {
    const cfg = await action("config_snapshot");
    const notes = cfg.notes || {};
    notesCategories = notes.categories || [];
    $("notes-vault").value = notes.vault_dir || "";
    $("notes-categories").value = (notes.categories || []).join("\n");
    $("notes-fetch-timeout").value = notes.fetch_timeout_seconds ?? 8;
    $("notes-max-chars").value = notes.max_extracted_chars ?? 2000;
    $("notes-low-conf").checked = notes.low_confidence_to_inbox !== false;
    $("notes-gen-title").checked = notes.generate_title !== false;
    $("notes-gen-summary").checked = notes.generate_summary !== false;
    setStatus("notes-status", "");
  } catch (e) {
    setStatus("notes-status", `Load failed: ${e.message}`, false);
  }
}

async function saveNotes() {
  const categories = $("notes-categories").value
    .split("\n")
    .map((line) => line.trim().replace(/^\/+|\/+$/g, ""))
    .filter(Boolean);
  if (categories.length === 0) {
    setStatus("notes-status", "⚠ Category list cannot be empty.", false);
    return;
  }
  const patch = {
    notes: {
      vault_dir: $("notes-vault").value.trim(),
      categories,
      fetch_timeout_seconds: Number($("notes-fetch-timeout").value) || 8,
      max_extracted_chars: Number($("notes-max-chars").value) || 2000,
      low_confidence_to_inbox: $("notes-low-conf").checked,
      generate_title: $("notes-gen-title").checked,
      generate_summary: $("notes-gen-summary").checked,
    },
  };
  try {
    const out = await action("apply_config_patch", { patch });
    setStatus("notes-status", `✅ Saved (${out}).`);
  } catch (e) {
    setStatus("notes-status", `⚠ Save failed: ${e.message}`, false);
  }
}

// ---- Config ----------------------------------------------------------------

// ---- Custom modes ------------------------------------------------------------

let customModes = {}; // id -> {label, system_prompt} (non-builtin only)

function populateCustomModes(modes) {
  customModes = {};
  const select = $("cm-select");
  select.replaceChildren();
  const fresh = document.createElement("option");
  fresh.value = "";
  fresh.textContent = "(new mode…)";
  select.append(fresh);
  for (const [id, m] of Object.entries(modes || {})) {
    if (m.builtin) continue;
    customModes[id] = m;
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = `${id} — ${m.label || id}`;
    select.append(opt);
  }
}

function fillCustomModeForm() {
  const id = $("cm-select").value;
  const m = customModes[id];
  $("cm-id").value = id;
  $("cm-label").value = m ? m.label || "" : "";
  $("cm-prompt").value = m ? m.system_prompt || "" : "";
  setStatus("cm-status", "");
}

async function saveCustomMode() {
  const id = $("cm-id").value.trim().toLowerCase();
  const prompt = $("cm-prompt").value.trim();
  if (!/^[a-z][a-z0-9_]{1,24}$/.test(id)) {
    setStatus("cm-status", "⚠ Id must be 2–25 chars: a-z, 0-9, _ (starts with a letter).", false);
    return;
  }
  if (["grammar", "prompt", "summarize", "explain", "tone"].includes(id)) {
    setStatus("cm-status", `⚠ '${id}' is a built-in mode.`, false);
    return;
  }
  if (!prompt) {
    setStatus("cm-status", "⚠ System prompt cannot be empty.", false);
    return;
  }
  try {
    await action("apply_config_patch", { patch: { modes: { [id]: {
      label: $("cm-label").value.trim() || id,
      system_prompt: prompt,
    } } } });
    setStatus("cm-status", `✅ Saved — usable as '${id}:' within a second.`);
    loadConfig();
  } catch (e) {
    setStatus("cm-status", `⚠ Save failed: ${e.message}`, false);
  }
}

async function deleteCustomMode() {
  const id = $("cm-id").value.trim().toLowerCase();
  if (!customModes[id]) {
    setStatus("cm-status", "⚠ Pick an existing custom mode to delete.", false);
    return;
  }
  if (!(await confirmDialog(`Delete custom mode '${id}'?`, "Delete"))) return;
  try {
    await action("apply_config_patch", { patch: { modes: { [id]: null } } });
    setStatus("cm-status", `✅ Deleted '${id}'.`);
    loadConfig();
  } catch (e) {
    setStatus("cm-status", `⚠ Delete failed: ${e.message}`, false);
  }
}

async function loadConfig() {
  try {
    const cfg = await action("config_snapshot");
    populateCustomModes(cfg.modes);
    const hk = cfg.hotkeys || {};
    $("hk-in-grammar").value = hk.grammar_fix || "^+g";
    $("hk-in-chat").value = hk.open_chat || "^+t";
    $("hk-in-note").value = hk.capture_note || "^!n";
    $("hk-in-ask").value = hk.ask_chat || "^+a";
    const llm = cfg.llm || {};
    providerState = {
      configured: llm.configured_provider || llm.provider || "fastflowlm",
      active: llm.provider || "fastflowlm",
      configs: cfg.provider_configs || {},
      status: cfg.provider_status || null,
    };
    $("cfg-provider").value = providerState.configured;
    const profile = providerProfile(providerState.configured);
    $("cfg-base-url").value = profile.base_url;
    $("cfg-timeout").value = profile.timeout_seconds;
    renderProviderStatus(providerState.status);
    applyProviderCaps();
    const perf = (cfg.server || {}).performance_mode || "balanced";
    document.querySelectorAll('input[name="perf"]').forEach((r) => (r.checked = r.value === perf));
    $("cfg-store-text").checked = !!cfg.history_store_text;
    const routing = cfg.routing || {};
    $("cfg-routing").checked = routing.enabled !== false;
    $("cfg-long-thr").value = routing.long_threshold_chars ?? 1400;
    $("cfg-chunk-size").value = routing.chunk_size_chars ?? 1200;
    $("cfg-min-chunk").value = routing.min_chunk_chars ?? 700;
    const tone = (cfg.tone || {}).preset || "formal";
    document.querySelectorAll('input[name="tone"]').forEach((r) => (r.checked = r.value === tone));
    populateNotifications(cfg.notifications || {});
    populateMeetings(cfg.meetings || {});
    setStatus("config-status", "");
  } catch (e) {
    setStatus("config-status", `Load failed: ${e.message}`, false);
  }
  loadServerStatus();
  loadModels();
  loadAutostart();
  if (($("cfg-provider").value || "fastflowlm") === "fastflowlm") loadFlmVersion(false);
}

// Notifications settings <-> the Config tab inputs. The snapshot from
// build_config_snapshot() always carries every category (defaults merged), so
// older config files render fine.
function populateNotifications(ntf) {
  $("ntf-enabled").checked = ntf.enabled !== false;
  $("ntf-dnd").checked = !!ntf.dnd;
  $("ntf-log").checked = ntf.log_enabled !== false;
  $("ntf-dedupe").value = ntf.dedupe_seconds ?? 5;
  const qh = ntf.quiet_hours || {};
  $("ntf-qh-enabled").checked = !!qh.enabled;
  $("ntf-qh-start").value = qh.start || "22:00";
  $("ntf-qh-end").value = qh.end || "07:00";
  const cats = ntf.categories || {};
  for (const id of NOTIF_CATEGORY_IDS) {
    const el = $(`ntf-cat-${id}`);
    if (el) el.checked = (cats[id] || {}).enabled !== false;
  }
}

function notificationsPatch() {
  const categories = {};
  for (const id of NOTIF_CATEGORY_IDS) {
    const el = $(`ntf-cat-${id}`);
    if (el) categories[id] = { enabled: el.checked };
  }
  const dedupe = Number($("ntf-dedupe").value);
  return {
    enabled: $("ntf-enabled").checked,
    dnd: $("ntf-dnd").checked,
    log_enabled: $("ntf-log").checked,
    dedupe_seconds: Number.isFinite(dedupe) && dedupe >= 0 ? dedupe : 5,
    quiet_hours: {
      enabled: $("ntf-qh-enabled").checked,
      start: $("ntf-qh-start").value || "22:00",
      end: $("ntf-qh-end").value || "07:00",
    },
    categories,
  };
}

async function loadServerStatus() {
  try {
    const raw = await action("status");
    const fields = {};
    for (const m of String(raw).matchAll(/([a-z_]+)=(\S+)/g)) fields[m[1]] = m[2];
    const rows = [
      ["Provider", PROVIDER_LABELS[fields.provider] || fields.provider || "-"],
      ["Reachable", `${fields.reachable === "true" ? "✅" : "❌"} ${fields.reachable ?? "-"}`],
    ];
    if (fields.pid) rows.push(["PID", `${fields.pid}${fields.pid_alive ? ` (alive=${fields.pid_alive})` : ""}`]);
    if (fields.mode) rows.push(["Performance", `${fields.mode === "max" ? "🔴" : "🟡"} ${fields.mode}`]);
    rows.push(["Model", fields.model ?? "-"]);
    fillTable("server-status-body", rows);
  } catch (e) {
    fillTable("server-status-body", [["Status", `unavailable: ${e.message}`]]);
  }
}

// Set by loadModels() from model_recommendations: {max_params_b, summary}.
let modelBudget = null;

// Mirrors ffp_hardware.parse_params_b: 'qwen3.5:4b' -> 4, 'mistral:7b' -> 7.
function parseParamsB(name) {
  const m = /(\d+(?:\.\d+)?)\s*b\b/i.exec(name || "");
  return m ? parseFloat(m[1]) : null;
}

async function loadModels() {
  const list = $("models-list");
  const suggestions = $("pull-suggestions");
  list.replaceChildren();
  suggestions.replaceChildren();
  const installedNames = new Set();
  try {
    const installed = await action("models_installed");
    for (const name of installed.models || []) {
      installedNames.add(name);
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name + (name === installed.active ? "   ★ active" : "");
      opt.selected = name === installed.active;
      list.append(opt);
    }
  } catch (e) {
    const opt = document.createElement("option");
    opt.textContent = `(error: ${e.message})`;
    list.append(opt);
  }
  // Hardware-aware suggestions: detected RAM/VRAM caps the model size, so
  // the datalist only offers models this machine can actually run. The input
  // still accepts any free-typed name (oversized ones get a confirm).
  let rec = null;
  try {
    rec = await action("model_recommendations");
    modelBudget = rec.budget || null;
    $("hw-summary").textContent = modelBudget
      ? `This machine: ${modelBudget.summary}. Larger models are hidden from suggestions.`
      : "";
  } catch {
    modelBudget = null;
    $("hw-summary").textContent = "";
  }
  if (rec && (rec.models || []).length) {
    for (const m of rec.models) {
      if (m.fits === "no" || installedNames.has(m.name)) continue;
      const opt = document.createElement("option");
      opt.value = m.name;
      if (m.fits === "tight") opt.label = `${m.name} — tight fit`;
      suggestions.append(opt);
    }
  } else {
    try {
      const avail = await action("models_not_installed");
      for (const name of avail.models || []) {
        const opt = document.createElement("option");
        opt.value = name;
        suggestions.append(opt);
      }
    } catch {
      /* suggestions stay empty when the provider is unreachable */
    }
  }
}

async function loadAutostart() {
  try {
    const state = await action("get_autostart_state");
    $("cfg-autostart").checked = !!state.enabled;
  } catch {
    /* leave unchecked when the daemon can't read the Run key */
  }
}

async function loadFlmVersion(force) {
  setText("flm-version", "FastFlowLM: checking…");
  try {
    const info = await action("flm_update_check", force ? { force: true } : { cache_only: true });
    const cur = info.current ? `v${info.current}` : "not detected";
    if (!info.current) {
      setText("flm-version", "FastFlowLM: not detected (is flm on PATH?)");
      $("flm-download").hidden = true;
    } else if (info.has_update) {
      setText("flm-version", `FastFlowLM ${cur} → v${info.latest} available.`);
      if (info.release_url) $("flm-download").href = info.release_url;
      $("flm-download").hidden = false;
    } else if (info.latest) {
      setText("flm-version", `FastFlowLM ${cur} — up to date ✓`);
      $("flm-download").hidden = true;
    } else {
      setText("flm-version", `FastFlowLM ${cur} — click 'Check for updates' to compare.`);
      $("flm-download").hidden = true;
    }
  } catch (e) {
    setText("flm-version", `FastFlowLM: check failed (${e.message})`);
  }
}

async function saveConfig() {
  const hotkeys = {
    grammar_fix: $("hk-in-grammar").value.trim(),
    open_chat: $("hk-in-chat").value.trim(),
    capture_note: $("hk-in-note").value.trim(),
    ask_chat: $("hk-in-ask").value.trim(),
  };
  const seen = new Set();
  for (const [name, key] of Object.entries(hotkeys)) {
    if (!isValidHotkey(key)) {
      setStatus("config-status", `⚠ '${key}' isn't a valid shortcut for ${name}. Use ^=Ctrl +=Shift !=Alt #=Win then one key.`, false);
      return;
    }
    if (seen.has(key)) {
      setStatus("config-status", `⚠ Duplicate binding: '${key}' assigned twice.`, false);
      return;
    }
    seen.add(key);
  }
  const perf = document.querySelector('input[name="perf"]:checked');
  const tone = document.querySelector('input[name="tone"]:checked');
  const provider = $("cfg-provider").value || "fastflowlm";
  const timeout = Number($("cfg-timeout").value) || PROVIDER_DEFAULTS[provider].timeout_seconds;
  const patch = {
    llm: { provider, timeout_seconds: timeout },
    providers: {
      [provider]: {
        base_url: $("cfg-base-url").value.trim(),
        timeout_seconds: timeout,
      },
    },
    history_store_text: $("cfg-store-text").checked,
    server: { performance_mode: perf ? perf.value : "balanced" },
    routing: {
      enabled: $("cfg-routing").checked,
      long_threshold_chars: Number($("cfg-long-thr").value) || 1400,
      chunk_size_chars: Number($("cfg-chunk-size").value) || 1200,
      min_chunk_chars: Number($("cfg-min-chunk").value) || 700,
    },
    modes: { tone: { preset: tone ? tone.value : "formal" } },
    hotkeys,
    notifications: notificationsPatch(),
    meetings: meetingsPatch(),
  };
  try {
    await action("apply_config_patch", { patch });
    await action("set_autostart", { enabled: $("cfg-autostart").checked });
    await loadConfig(); // provider switch changes model lists + status
    setStatus("config-status", "✅ Saved — hotkeys reload in the running app within a second.");
  } catch (e) {
    setStatus("config-status", `⚠ Save failed: ${e.message}`, false);
  }
}

async function setActiveModel() {
  const name = $("models-list").value;
  if (!name) return;
  try {
    await action("apply_config_patch", { patch: { llm: { model: name } } });
    setStatus("config-status", `✅ Active model: ${name}`);
    loadModels();
    loadServerStatus();
  } catch (e) {
    setStatus("config-status", `⚠ ${e.message}`, false);
  }
}

async function removeModel() {
  const name = $("models-list").value;
  if (!name) return;
  if (!(await confirmDialog(`Remove model '${name}' from local storage?`, "Remove"))) return;
  setStatus("config-status", `Removing ${name}…`);
  try {
    const out = await action("remove_model", { value: name });
    setStatus("config-status", `✅ ${out || `Removed ${name}.`}`);
  } catch (e) {
    setStatus("config-status", `⚠ ${e.message}`, false);
  }
  loadModels();
}

let pullTimer = null;

async function pullModel() {
  const name = $("pull-name").value.trim();
  if (!name) {
    setText("pull-status", "Type or pick a model name first.");
    return;
  }
  const params = parseParamsB(name);
  if (modelBudget && params && params > modelBudget.max_params_b * 1.5) {
    const msg = `'${name}' looks like a ${params}B model — likely too big for this machine (${modelBudget.summary}). Pull anyway?`;
    if (!(await confirmDialog(msg, "Pull anyway"))) return;
  }
  try {
    const state = await action("pull_start", { model: name });
    if (state.state === "running") {
      setText("pull-status", `Pulling ${name}… 0%`);
      clearInterval(pullTimer);
      pullTimer = setInterval(pollPull, 1000);
    } else {
      setText("pull-status", `⚠ Pull not started: ${state.error || "unknown"}`);
    }
  } catch (e) {
    setText("pull-status", `⚠ Pull not started: ${e.message}`);
  }
}

async function pollPull() {
  try {
    const st = await action("pull_status");
    if (st.state === "running") {
      setText("pull-status", `Pulling ${st.model}… ${Math.round(st.percent || 0)}%`);
    } else {
      clearInterval(pullTimer);
      setText("pull-status", st.state === "done" ? `✅ ${st.model} downloaded.` : `⚠ Pull failed: ${st.error || "unknown"}`);
      loadModels();
    }
  } catch {
    clearInterval(pullTimer);
  }
}

// ---- Benchmark ---------------------------------------------------------------

let benchTimer = null;

let benchProvider = "fastflowlm"; // effective provider, set by loadBenchmark

async function loadBenchmark() {
  // Both providers can benchmark, with different mechanics — adjust the copy.
  try {
    const cfg = await action("config_snapshot");
    benchProvider = ((cfg.llm || {}).provider) || "fastflowlm";
  } catch {
    /* keep the previous provider when the snapshot is unavailable */
  }
  if (benchProvider === "ollama") {
    setText("bench-desc", "Benchmark a model with timed generations against the running Ollama server — three prompt sizes × two passes, using Ollama's native prefill/decode metrics.");
    setText("bench-warn", "Takes ~1–3 minutes on CPU. The server keeps running, but responses will be slow during the run.");
  } else {
    setText("bench-desc", "Benchmark a model with FastFlowLM's flm bench — sweeps 1k–32k context × 8 iterations and records time-to-first-token, prefill speed, and decode speed.");
    setText("bench-warn", "⚠ Takes ~10–20 min and fully saturates the NPU. The server is stopped for the run, so hotkeys will be unresponsive. Best run when idle.");
  }
  const select = $("bench-model");
  select.replaceChildren();
  try {
    const installed = await action("models_installed");
    for (const name of installed.models || []) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      select.append(opt);
    }
  } catch {
    /* dropdown stays empty when flm is unreachable */
  }
  pollBench(true);
  loadBenchHistory();
}

async function loadBenchHistory() {
  try {
    const hist = await action("bench_history");
    const rows = (hist.runs || []).map((r) => [
      String(r.timestamp || "-").slice(0, 19).replace("T", " "),
      r.model || "-",
      PROVIDER_LABELS[r.provider] || r.provider || "FastFlowLM",
      r.peak_prefill_tps ?? "-",
      r.peak_decode_tps ?? "-",
      r.points ?? 0,
    ]);
    const n = fillTable("bench-history-body", rows);
    $("bench-empty").hidden = n > 0;
  } catch (e) {
    fillTable("bench-history-body", [[`Benchmark history unavailable: ${e.message}`, "", "", "", "", ""]]);
  }
}

async function runBenchmark() {
  const model = $("bench-model").value;
  if (!model) {
    setText("bench-status", "Select an installed model first.");
    return;
  }
  const msg = benchProvider === "ollama"
    ? `Benchmark '${model}'?\n\nThis runs timed generations against Ollama for ~1–3 minutes. The server keeps serving, but responses will be slow during the run.`
    : `Benchmark '${model}'?\n\nThis runs flm bench for ~10–20 minutes, stops the server, and saturates the NPU. Hotkeys will be unresponsive until it finishes.`;
  if (!(await confirmDialog(msg, "Run benchmark"))) return;
  try {
    await action("bench_start", { model });
    setText("bench-status", benchProvider === "ollama"
      ? `⏳ Benchmark started for ${model} — this takes a few minutes…`
      : `⏳ Benchmark started for ${model} — this takes 10–20 min…`);
    clearInterval(benchTimer);
    benchTimer = setInterval(() => pollBench(false), 4000);
  } catch (e) {
    setText("bench-status", `⚠ Benchmark not started: ${e.message}`);
  }
}

async function pollBench(initial) {
  try {
    const st = await action("bench_status");
    if (st.state === "running") {
      setText("bench-status", `⏳ ${st.message || "Benchmark running…"}`);
      if (initial) {
        clearInterval(benchTimer);
        benchTimer = setInterval(() => pollBench(false), 4000);
      }
    } else {
      clearInterval(benchTimer);
      if (st.state === "done") setText("bench-status", `✅ ${st.message || "Benchmark complete."}`);
      else if (st.state === "error") setText("bench-status", `⚠ Benchmark failed: ${st.error || "unknown error"}`);
      else setText("bench-status", "Idle.");
      if (!initial) loadBenchHistory();
    }
  } catch {
    clearInterval(benchTimer);
  }
}

// ---- Chat ------------------------------------------------------------------
// Daemon-backed chat (replaces the retired tkinter popup). Threads + send live
// in ffp_chat behind the chat_* actions. All DOM via textContent/createElement.

let chatThreadId = "";

async function loadChat() {
  // Pick up a selection staged by Ctrl+Shift+A (read-and-clear on the daemon).
  try {
    const staged = await action("chat_take_staged");
    if (staged && staged.text) {
      $("chat-input").value = staged.text;
      chatThreadId = ""; // a staged selection starts a fresh conversation
    }
  } catch (e) { /* no staged selection */ }
  await loadChatThreads();
  if (chatThreadId) await openChatThread(chatThreadId);
  else renderTranscript([]);
  $("chat-input").focus();
}

async function loadChatThreads() {
  let threads = [];
  try {
    const res = await action("chat_threads_list");
    threads = (res && res.threads) || [];
  } catch (e) {
    setStatus("chat-status", `Conversations unavailable: ${e.message}`, false);
  }
  const list = $("chat-thread-list");
  list.textContent = "";
  for (const t of threads) {
    const li = document.createElement("li");
    li.className = "thread-item" + (t.thread_id === chatThreadId ? " active" : "");
    const open = document.createElement("button");
    open.className = "thread-open";
    open.textContent = t.title || "New chat";
    open.title = t.updated_at || "";
    open.addEventListener("click", () => openChatThread(t.thread_id));
    const del = document.createElement("button");
    del.className = "thread-del";
    del.textContent = "✕";
    del.title = "Delete conversation";
    del.addEventListener("click", (e) => { e.stopPropagation(); deleteChatThread(t.thread_id); });
    li.append(open, del);
    list.append(li);
  }
  $("chat-threads-empty").hidden = threads.length > 0;
}

async function openChatThread(id) {
  try {
    const t = await action("chat_thread_get", { thread_id: id });
    chatThreadId = t.thread_id || id;
    renderTranscript(t.history || []);
    await loadChatThreads(); // reflect the active thread in the sidebar
  } catch (e) {
    setStatus("chat-status", `Open failed: ${e.message}`, false);
  }
  $("chat-input").focus();
}

function renderTranscript(history) {
  const box = $("chat-transcript");
  box.textContent = "";
  let hasTurns = false;
  for (const m of history) {
    if (m.role !== "user" && m.role !== "assistant") continue; // hide system/grounding
    hasTurns = true;
    const div = document.createElement("div");
    div.className = `chat-msg chat-msg-${m.role}`;
    div.textContent = m.content || "";
    box.append(div);
  }
  $("chat-placeholder").hidden = hasTurns;
  box.scrollTop = box.scrollHeight;
}

function newChat() {
  chatThreadId = "";
  renderTranscript([]);
  $("chat-input").value = "";
  $("chat-input").focus();
  setStatus("chat-status", "");
  loadChatThreads();
}

async function deleteChatThread(id) {
  if (!(await confirmDialog("Delete this conversation?", "Delete"))) return;
  try {
    await action("chat_thread_delete", { thread_id: id });
    if (id === chatThreadId) { chatThreadId = ""; renderTranscript([]); }
    await loadChatThreads();
  } catch (e) {
    setStatus("chat-status", `Delete failed: ${e.message}`, false);
  }
}

async function sendChat() {
  const input = $("chat-input");
  const message = input.value.trim();
  if (!message) return;
  const btn = $("chat-send");
  btn.disabled = true;
  setStatus("chat-status", "Thinking…");
  // Optimistically show the user's message; the reply lands when the model returns.
  const box = $("chat-transcript");
  const userDiv = document.createElement("div");
  userDiv.className = "chat-msg chat-msg-user";
  userDiv.textContent = message;
  box.append(userDiv);
  $("chat-placeholder").hidden = true;
  box.scrollTop = box.scrollHeight;
  input.value = "";
  try {
    const res = await action("chat_send", {
      thread_id: chatThreadId,
      message,
      use_notes: $("chat-use-notes").checked,
    });
    chatThreadId = res.thread_id || chatThreadId;
    const reply = document.createElement("div");
    reply.className = "chat-msg chat-msg-assistant";
    reply.textContent = res.reply || "(no reply)";
    box.append(reply);
    box.scrollTop = box.scrollHeight;
    setStatus("chat-status",
      res.notes_used && res.notes_used.length ? `📚 Grounded in: ${res.notes_used.join(", ")}` : "");
    loadChatThreads();
  } catch (e) {
    setStatus("chat-status", `Send failed: ${e.message}`, false);
  } finally {
    btn.disabled = false;
    input.focus();
  }
}

// ---- Meetings (Quill via MCP; after-hours digests) -------------------------
// Search meetings, read a cached digest (summary / goals / action items), or
// generate one on demand. All data comes from the daemon's quill_*/meeting_*
// actions; the daemon talks MCP to the local Quill app. CSP-safe DOM only.
let currentMeeting = null;
let digestIds = new Set();

async function loadMeetings() {
  const st = $("mtg-status");
  try {
    const s = await action("quill_status");
    st.textContent = s.reachable
      ? `Quill ${s.server_version || ""} connected`.trim()
      : "Quill not reachable — enable it in Config and make sure Quill is running";
    st.className = s.reachable ? "muted small ok" : "muted small bad";
  } catch (e) {
    st.textContent = `status unavailable: ${e.message}`;
    st.className = "muted small bad";
  }
  try {
    const d = await action("meeting_digests_list");
    digestIds = new Set((d.digests || []).map((x) => x.meeting_id));
  } catch {
    digestIds = new Set();
  }
  searchMeetings();
}

async function searchMeetings() {
  const q = $("mtg-query").value.trim();
  const body = $("mtg-results");
  body.replaceChildren();
  try {
    const r = await action("quill_search_meetings", { query: q, limit: 15 });
    const meetings = r.meetings || [];
    $("mtg-empty").hidden = meetings.length > 0;
    for (const m of meetings) {
      const tr = document.createElement("tr");
      tr.className = "mtg-row";
      tr.style.cursor = "pointer";
      tr.dataset.id = m.id;
      tr.dataset.title = m.title || "";
      tr.dataset.date = m.date || "";
      tr.dataset.url = m.url || "";
      const cells = [m.title || "(untitled)", (m.date || "").slice(0, 10), m.participants || "", digestIds.has(m.id) ? "✓" : "—"];
      for (const c of cells) {
        const td = document.createElement("td");
        td.textContent = c;
        tr.append(td);
      }
      body.append(tr);
    }
  } catch (e) {
    $("mtg-empty").hidden = true;
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 4;
    td.textContent = `Search failed: ${e.message}`;
    tr.append(td);
    body.append(tr);
  }
}

function openMeeting(row) {
  currentMeeting = { id: row.dataset.id, title: row.dataset.title, date: row.dataset.date, url: row.dataset.url };
  $("mtg-reader").hidden = false;
  $("mtg-title").textContent = currentMeeting.title || "Meeting";
  $("mtg-meta").textContent = (currentMeeting.date || "").slice(0, 16).replace("T", " ");
  const link = $("mtg-link");
  if (currentMeeting.url) { link.href = currentMeeting.url; link.hidden = false; } else { link.hidden = true; }
  $("mtg-answer").hidden = true;
  $("mtg-ask-input").value = "";
  $("mtg-ask-status").textContent = "";
  loadDigest();
}

async function loadDigest() {
  const body = $("mtg-digest");
  body.textContent = "Loading…";
  $("mtg-process-status").textContent = "";
  try {
    const d = await action("meeting_digest_get", { meeting_id: currentMeeting.id });
    if (d.found) {
      body.textContent = d.digest_md || "(empty digest)";
      $("mtg-process-status").textContent = `cached ${(d.processed_at || "").replace("T", " ")} · ${d.source} · ${d.seconds}s`;
    } else {
      body.textContent = "Not processed yet. Click “Process now” to generate a summary + action items on the local model, or wait for the after-hours batch.";
    }
  } catch (e) {
    body.textContent = `Failed: ${e.message}`;
  }
}

async function processMeetingNow() {
  if (!currentMeeting) return;
  $("mtg-process-status").textContent = "Processing on the local model… (first token can take ~15s on a full transcript)";
  try {
    const r = await action("meeting_process", {
      meeting_id: currentMeeting.id, title: currentMeeting.title, date: currentMeeting.date, url: currentMeeting.url,
    });
    $("mtg-digest").textContent = r.digest_md || "(empty)";
    $("mtg-process-status").textContent = `done · ${r.source} · ${r.seconds}s`;
    digestIds.add(currentMeeting.id);
  } catch (e) {
    $("mtg-process-status").textContent = `⚠ ${e.message}`;
  }
}

async function askMeeting() {
  if (!currentMeeting) return;
  const q = $("mtg-ask-input").value.trim();
  if (!q) return;
  const ans = $("mtg-answer");
  ans.hidden = false;
  ans.textContent = "Thinking on the local model…";
  $("mtg-ask-status").textContent = "";
  try {
    const r = await action("meeting_ask", { meeting_id: currentMeeting.id, question: q });
    if (r.ok) {
      ans.textContent = r.answer || "(no answer)";
      $("mtg-ask-status").textContent = `${r.source} · ${r.seconds}s`;
    } else {
      ans.textContent = `⚠ ${r.error || "failed"}`;
    }
  } catch (e) {
    ans.textContent = `⚠ ${e.message}`;
  }
}

// Meetings settings <-> the Config tab inputs.
function populateMeetings(m) {
  m = m || {};
  $("mtg-enabled").checked = !!m.enabled;
  $("mtg-url").value = m.mcp_url || "http://127.0.0.1:19532/mcp";
  $("mtg-source").value = m.source || "auto";
  $("mtg-maxctx").value = m.max_context_tokens ?? 6000;
  const b = m.batch || {};
  $("mtg-batch-enabled").checked = b.enabled !== false;
  $("mtg-start").value = b.start || "17:00";
  $("mtg-end").value = b.end || "21:00";
  $("mtg-idle").checked = b.only_when_idle !== false;
  $("mtg-idle-min").value = b.idle_minutes ?? 10;
  $("mtg-maxrun").value = b.max_per_run ?? 10;
}

function meetingsPatch() {
  const ctx = Number($("mtg-maxctx").value);
  return {
    enabled: $("mtg-enabled").checked,
    mcp_url: $("mtg-url").value.trim() || "http://127.0.0.1:19532/mcp",
    source: $("mtg-source").value,
    max_context_tokens: Number.isFinite(ctx) && ctx > 0 ? ctx : 6000,
    batch: {
      enabled: $("mtg-batch-enabled").checked,
      start: $("mtg-start").value || "17:00",
      end: $("mtg-end").value || "21:00",
      only_when_idle: $("mtg-idle").checked,
      idle_minutes: Number($("mtg-idle-min").value) || 0,
      max_per_run: Number($("mtg-maxrun").value) || 10,
    },
  };
}

async function runBatchNow() {
  const s = $("mtg-run-status");
  s.textContent = "Running… (this processes on the local model; may take a while)";
  try {
    const r = await action("meeting_batch_run", {});
    s.textContent = r.ok
      ? `processed ${r.processed} of ${r.queued} queued${r.errors && r.errors.length ? `, ${r.errors.length} errors` : ""}`
      : `⚠ ${r.error}`;
  } catch (e) {
    s.textContent = `⚠ ${e.message}`;
  }
}

// ---- Tabs & refresh --------------------------------------------------------

const TAB_LOADERS = {
  overview: loadOverview,
  chat: loadChat,
  telemetry: loadTelemetry,
  history: loadHistory,
  notes: loadNotes,
  meetings: loadMeetings,
  config: loadConfig,
  benchmark: loadBenchmark,
};

let currentTab = "overview";

function switchTab(name) {
  currentTab = name;
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  (TAB_LOADERS[name] || (() => {}))();
}

function refreshAll() {
  refreshHealth();
  (TAB_LOADERS[currentTab] || (() => {}))();
}

// Deep-link support: `/#chat` (or any tab id) selects that tab. Lets a hotkey or
// the tray open the dashboard straight to Chat via daemonBaseUrl + "#chat".
function tabFromHash() {
  const h = (location.hash || "").replace(/^#/, "");
  return TAB_LOADERS[h] ? h : "";
}

document.addEventListener("DOMContentLoaded", () => {
  $("tabs").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab");
    if (btn) { location.hash = btn.dataset.tab; switchTab(btn.dataset.tab); }
  });
  window.addEventListener("hashchange", () => {
    const t = tabFromHash();
    if (t && t !== currentTab) switchTab(t);
  });
  $("chat-send").addEventListener("click", sendChat);
  $("chat-new").addEventListener("click", newChat);
  $("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendChat(); }
  });
  $("refresh-btn").addEventListener("click", refreshAll);
  $("theme-btn").addEventListener("click", cycleTheme);
  applyTheme(localStorage.getItem(THEME_KEY) || "auto");
  $("note-search-btn").addEventListener("click", browseNotes);
  $("note-query").addEventListener("keydown", (e) => {
    if (e.key === "Enter") browseNotes();
  });
  $("notes-save").addEventListener("click", saveNotes);
  $("notes-revert").addEventListener("click", loadNotes);
  $("nr-move").addEventListener("click", moveNoteToBucket);
  $("nr-delete").addEventListener("click", deleteCurrentNote);
  $("nr-close").addEventListener("click", () => { $("note-reader").hidden = true; });
  $("mtg-search-btn").addEventListener("click", searchMeetings);
  $("mtg-query").addEventListener("keydown", (e) => { if (e.key === "Enter") searchMeetings(); });
  $("mtg-results").addEventListener("click", (e) => {
    const row = e.target.closest(".mtg-row");
    if (row) openMeeting(row);
  });
  $("mtg-close").addEventListener("click", () => { $("mtg-reader").hidden = true; });
  $("mtg-process").addEventListener("click", processMeetingNow);
  $("mtg-ask-btn").addEventListener("click", askMeeting);
  $("mtg-ask-input").addEventListener("keydown", (e) => { if (e.key === "Enter") askMeeting(); });
  $("mtg-run-now").addEventListener("click", runBatchNow);
  $("config-save").addEventListener("click", saveConfig);
  $("config-revert").addEventListener("click", loadConfig);
  $("cm-select").addEventListener("change", fillCustomModeForm);
  $("cm-save").addEventListener("click", saveCustomMode);
  $("cm-delete").addEventListener("click", deleteCustomMode);
  $("model-set-active").addEventListener("click", setActiveModel);
  $("model-remove").addEventListener("click", removeModel);
  $("pull-btn").addEventListener("click", pullModel);
  $("cfg-provider").addEventListener("change", onProviderChanged);
  $("provider-start").addEventListener("click", startProviderServer);
  $("flm-check").addEventListener("click", () => loadFlmVersion(true));
  $("bench-run").addEventListener("click", runBenchmark);
  refreshHealth();
  switchTab(tabFromHash() || "overview");
  setInterval(refreshHealth, 10000);
});
