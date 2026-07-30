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

function attachHelpMarker(containerId, text) {
  const wrap = $(containerId);
  if (!wrap || wrap.childElementCount > 0) return;
  const tipId = `${containerId}-tooltip`;
  const marker = document.createElement("span");
  marker.className = "help";
  marker.tabIndex = 0;
  marker.setAttribute("role", "img");
  marker.setAttribute("aria-label", "What is this?");
  marker.setAttribute("aria-describedby", tipId);
  marker.title = text;
  marker.textContent = "i";
  const tip = document.createElement("span");
  tip.className = "help-text";
  tip.id = tipId;
  tip.setAttribute("role", "tooltip");
  tip.textContent = text;
  wrap.append(marker, tip);
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
const PROMPT_BUILDER_DEFAULTS = {
  prompt_version: "v2",
  target_agent: "claude_code",
  detail_level: "concise",
  action_mode: "implement",
  structure: "agent_default",
  include_acceptance_criteria: false,
  include_verification: false,
  include_output_format: true,
  preserve_user_constraints: true,
  allow_user_suffix: true,
  user_suffix: "",
};
const HISTORY_STORE_HELP_TEXT = "Store selected text. Controls whether the exact text you send to a hotkey is saved in History. Off (default, redacted): only telemetry is kept - mode, character counts, timing, tokens - your text is never written to disk. On (visible): the captured request and generated result text are also saved so you can re-read them in History's Exposed view. This is a capture policy for new runs; it never reveals or hides text already recorded. Everything stays on your machine.";

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
  $("cfg-warm-on-start").disabled = !isFlm;
  $("cfg-keep-warm").disabled = !isFlm;
  $("warm-model-note").hidden = !isFlm;
  // The card covers both the installed list and the pull form (each has its own
  // subhead), so the heading is just "Models — <provider>".
  $("models-title").textContent = `Models — ${PROVIDER_LABELS[sel]}`;
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
  try {
    const mo = await action("meeting_overview");
    if (mo.reachable) {
      setText("ov-mtg-today", (mo.today.minutes / 60).toFixed(1));
      setText("ov-mtg-week", (mo.week.minutes / 60).toFixed(1));
      $("ov-mtg-detail").textContent = `${mo.today.count} today · ${mo.week.count} this week`;
    } else {
      setText("ov-mtg-today", "–");
      setText("ov-mtg-week", "–");
      $("ov-mtg-detail").textContent = mo.enabled ? "Quill not reachable" : "Quill integration off (enable in Config)";
    }
  } catch {
    $("ov-mtg-detail").textContent = "";
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
  // Render only hours that actually had activity — zero-activity hours are
  // dropped instead of shown as empty bars (cleaner for sparse usage).
  const active = [];
  buckets.forEach((count, hour) => { if (count > 0) active.push([hour, count]); });
  if (!active.length) {
    const note = document.createElement("span");
    note.className = "muted small";
    note.textContent = "No activity yet.";
    chart.append(note);
    return;
  }
  const max = Math.max(1, ...active.map(([, count]) => count));
  for (const [hour, count] of active) {
    const bar = document.createElement("div");
    bar.className = "bar";
    bar.style.height = `${Math.max(2, Math.round((count / max) * 100))}%`;
    bar.title = `${String(hour).padStart(2, "0")}:00 — ${count}`;
    chart.append(bar);
    const tick = document.createElement("span");
    tick.textContent = String(hour).padStart(2, "0");
    axis.append(tick);
  }
}

// ---- History ---------------------------------------------------------------

const HISTORY_TELEMETRY_HEADERS = ["When", "Mode", "In", "Out", "Latency", "tok/s", "Tokens"];
const HISTORY_EXPOSED_HEADERS = ["When", "Mode", "Request", "Result", "Latency"];
let historyEntries = [];
let historyView = "telemetry";
let historyStoreText = false;

function historyTime(e) {
  return String(e.timestamp || e.ts || "-").slice(0, 19).replace("T", " ");
}

function historyLatency(e) {
  const value = e.elapsed_seconds ?? e.api_time;
  return value === undefined || value === null || value === "" ? "-" : `${value}s`;
}

function setHistoryColumns(headers) {
  const head = $("history-head");
  head.replaceChildren();
  const tr = document.createElement("tr");
  for (const label of headers) {
    const th = document.createElement("th");
    th.textContent = label;
    tr.append(th);
  }
  head.append(tr);
}

function renderHistoryTable(headers, rows, textColumns = []) {
  setHistoryColumns(headers);
  const body = $("history-body");
  body.replaceChildren();
  for (const cells of rows) {
    const tr = document.createElement("tr");
    cells.forEach((cell, index) => {
      const td = document.createElement("td");
      td.textContent = cell;
      if (textColumns.includes(index)) td.classList.add("history-text-cell");
      tr.append(td);
    });
    body.append(tr);
  }
  $("history-empty").hidden = rows.length > 0;
}

function storedHistoryText(e, key) {
  if (Object.prototype.hasOwnProperty.call(e, key)) {
    const text = String(e[key] ?? "");
    if (text.trim()) return text;
  }
  return "- (not stored - captured while redacted)";
}

function updateHistoryViewButtons() {
  const telemetry = historyView === "telemetry";
  $("history-view-telemetry").classList.toggle("active", telemetry);
  $("history-view-exposed").classList.toggle("active", !telemetry);
  $("history-view-telemetry").setAttribute("aria-pressed", telemetry ? "true" : "false");
  $("history-view-exposed").setAttribute("aria-pressed", telemetry ? "false" : "true");
  $("history-exposed-note").hidden = telemetry;
  $("history-table").classList.toggle("history-exposed", !telemetry);
}

function renderHistoryStorageBanner() {
  const copy = $("history-storage-copy");
  const button = $("history-storage-action");
  if (historyStoreText) {
    copy.textContent = "Text storage: Visible - new runs store captured request and result text.";
    button.textContent = "Switch to redacted";
    button.dataset.target = "redacted";
  } else {
    copy.textContent = "Text storage: Redacted - new runs store telemetry only.";
    button.textContent = "Store new text";
    button.dataset.target = "visible";
  }
}

function renderHistory() {
  updateHistoryViewButtons();
  if (historyView === "exposed") {
    const rows = historyEntries.map((e) => [
      historyTime(e),
      e.mode || "?",
      storedHistoryText(e, "input_text"),
      storedHistoryText(e, "output_text"),
      historyLatency(e),
    ]);
    renderHistoryTable(HISTORY_EXPOSED_HEADERS, rows, [2, 3]);
    return;
  }
  const rows = historyEntries.map((e) => [
    historyTime(e),
    e.mode || "?",
    e.input_chars ?? "?",
    e.output_chars ?? "?",
    historyLatency(e),
    e.tok_per_sec ?? "-",
    e.completion_tokens ?? "-",
  ]);
  renderHistoryTable(HISTORY_TELEMETRY_HEADERS, rows);
}

function setHistoryView(view) {
  historyView = view === "exposed" ? "exposed" : "telemetry";
  renderHistory();
}

async function setHistoryStorageFromBanner() {
  const target = $("history-storage-action").dataset.target === "visible" ? "visible" : "redacted";
  $("history-storage-action").disabled = true;
  try {
    await action(target === "visible" ? "set_history_visible" : "set_history_redacted");
    historyStoreText = target === "visible";
    $("cfg-store-text").checked = historyStoreText;
    renderHistoryStorageBanner();
    loadOverview();
  } catch (e) {
    $("history-storage-copy").textContent = `Text storage change failed: ${e.message}`;
  } finally {
    $("history-storage-action").disabled = false;
  }
}

async function loadHistory() {
  historyView = "telemetry";
  try {
    const [entries, cfg] = await Promise.all([
      action("recent_history", { limit: 50 }),
      action("config_snapshot"),
    ]);
    historyEntries = Array.isArray(entries) ? entries : [];
    historyStoreText = !!cfg.history_store_text;
    renderHistoryStorageBanner();
    renderHistory();
  } catch (e) {
    historyEntries = [];
    renderHistoryStorageBanner();
    updateHistoryViewButtons();
    renderHistoryTable(HISTORY_TELEMETRY_HEADERS, [[`History unavailable: ${e.message}`, "", "", "", "", "", ""]]);
    $("history-empty").hidden = true;
  }
}

// ---- Notes -----------------------------------------------------------------

const NOTE_VIEW_META = {
  board: ["Vision board", "Arrange what matters"],
  all: ["All notes", "Everything you have captured"],
  task: ["Tasks", "Things ready for action"],
  idea: ["Ideas", "Possibilities worth growing"],
  link: ["Links", "Useful places and references"],
  read_later: ["Read later", "A quiet queue for focused reading"],
  pinned: ["Pinned", "Keep the important things visible"],
  archived: ["Archive", "Notes kept out of the daily flow"],
  trashed: ["Trash", "Recover or permanently remove notes"],
};

const NOTE_KIND_META = {
  note: ["✦", "Note"],
  task: ["✓", "Task"],
  idea: ["◇", "Idea"],
  link: ["↗", "Link"],
  read_later: ["◷", "Read later"],
};

let notesCategories = [];
let notesSearchTimer = null;
let draggedNoteId = "";
let notesState = {
  view: "board",
  query: "",
  category: "",
  tag: "",
  results: [],
  facets: { counts: {}, categories: [], tags: [] },
  board: null,
  current: null,
};

function notesQueryArgs() {
  const kind = ["task", "idea", "link", "read_later"].includes(notesState.view)
    ? notesState.view
    : "";
  let status = "";
  if (notesState.view === "board") status = "active";
  if (notesState.view === "archived") status = "archived";
  if (notesState.view === "trashed") status = "trashed";
  return {
    query: notesState.query,
    kind,
    status,
    category: notesState.category,
    tag: notesState.tag,
    sort: notesState.view === "task" ? "due" : "updated",
    limit: 200,
  };
}

function visibleNotes() {
  let notes = [...notesState.results];
  if (!["archived", "trashed", "board"].includes(notesState.view)) {
    notes = notes.filter((note) => note.status !== "archived");
  }
  if (notesState.view === "pinned") {
    notes = notes.filter((note) => note.pinned);
  }
  return notes;
}

function setNotesStatus(message, good = true) {
  setStatus("notes-status", message || "", good);
}

function setEditorStatus(message, good = true) {
  setStatus("ne-status", message || "", good);
}

function renderNotesViewButtons() {
  document.querySelectorAll("[data-notes-view]").forEach((button) => {
    const active = button.dataset.notesView === notesState.view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
}

function makeFacetButton(label, active, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `notes-facet-btn${active ? " active" : ""}`;
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

function renderNotesFacets() {
  const categoryList = $("notes-category-list");
  categoryList.replaceChildren();
  categoryList.append(makeFacetButton(
    "All categories",
    !notesState.category,
    () => {
      notesState.category = "";
      loadNotes(false);
    },
  ));
  for (const category of notesState.facets.categories || []) {
    categoryList.append(makeFacetButton(
      category,
      notesState.category === category,
      () => {
        notesState.category = notesState.category === category ? "" : category;
        loadNotes(false);
      },
    ));
  }

  const tagList = $("notes-tag-list");
  tagList.replaceChildren();
  for (const tag of (notesState.facets.tags || []).slice(0, 18)) {
    tagList.append(makeFacetButton(
      `#${tag}`,
      notesState.tag === tag,
      () => {
        notesState.tag = notesState.tag === tag ? "" : tag;
        loadNotes(false);
      },
    ));
  }
  if (!(notesState.facets.tags || []).length) {
    const empty = document.createElement("span");
    empty.className = "muted small";
    empty.textContent = "Tags appear as you add them.";
    tagList.append(empty);
  }
}

function formatNoteDate(value) {
  if (!value) return "";
  const raw = String(value);
  const date = new Date(/^\d{4}-\d{2}-\d{2}$/.test(raw) ? `${raw}T12:00:00` : raw);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ").slice(0, 16);
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function noteKindMeta(kind) {
  return NOTE_KIND_META[kind] || NOTE_KIND_META.note;
}

function createNoteCard(note, options = {}) {
  const card = document.createElement("article");
  const color = ["yellow", "peach", "pink", "violet", "blue", "mint", "slate"].includes(note.color)
    ? note.color
    : "yellow";
  card.className = `note-card note-color-${color}`;
  card.dataset.noteId = note.note_id;
  card.tabIndex = 0;
  card.setAttribute("aria-label", `${noteKindMeta(note.kind)[1]}: ${note.title || "Untitled note"}`);

  const top = document.createElement("div");
  top.className = "note-card-top";
  const kind = document.createElement("span");
  kind.className = "note-kind";
  kind.textContent = `${noteKindMeta(note.kind)[0]} ${noteKindMeta(note.kind)[1]}`;
  top.append(kind);
  if (note.pinned) {
    const pin = document.createElement("span");
    pin.className = "note-pin";
    pin.title = "Pinned";
    pin.textContent = "⌖";
    top.append(pin);
  }
  card.append(top);

  const heading = document.createElement("h3");
  heading.textContent = note.title || "Untitled note";
  card.append(heading);
  if (note.excerpt) {
    const excerpt = document.createElement("p");
    excerpt.className = "note-card-excerpt";
    excerpt.textContent = note.excerpt;
    card.append(excerpt);
  }

  const chips = document.createElement("div");
  chips.className = "note-card-chips";
  if (note.kind === "task" && note.status === "done") {
    const done = document.createElement("span");
    done.className = "note-chip done";
    done.textContent = "Completed";
    chips.append(done);
  }
  if (note.due) {
    const due = document.createElement("span");
    due.className = "note-chip";
    due.textContent = `Due ${formatNoteDate(note.due)}`;
    chips.append(due);
  }
  if (note.category) {
    const category = document.createElement("span");
    category.className = "note-chip";
    category.textContent = note.category;
    chips.append(category);
  }
  for (const tag of (note.tags || []).slice(0, 2)) {
    const chip = document.createElement("span");
    chip.className = "note-chip";
    chip.textContent = `#${tag}`;
    chips.append(chip);
  }
  if (chips.childElementCount) card.append(chips);

  const footer = document.createElement("footer");
  const updated = document.createElement("span");
  updated.textContent = formatNoteDate(note.updated || note.created);
  footer.append(updated);
  if (/^https?:\/\//i.test(note.source || "")) {
    const source = document.createElement("a");
    source.href = note.source;
    source.target = "_blank";
    source.rel = "noopener";
    source.textContent = "Open source ↗";
    source.addEventListener("click", (event) => event.stopPropagation());
    footer.append(source);
  }
  if (options.unplaced) {
    const add = document.createElement("button");
    add.type = "button";
    add.className = "note-card-action";
    add.textContent = "Add to board";
    add.addEventListener("click", (event) => {
      event.stopPropagation();
      moveNoteOnBoard(note.note_id, firstBoardSectionId());
    });
    footer.append(add);
  }
  card.append(footer);

  const open = () => openNoteEditor(note.note_id);
  card.addEventListener("click", open);
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open();
    }
  });
  if (options.draggable) {
    card.draggable = true;
    card.addEventListener("dragstart", (event) => {
      draggedNoteId = note.note_id;
      card.classList.add("dragging");
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", note.note_id);
      }
    });
    card.addEventListener("dragend", () => {
      draggedNoteId = "";
      card.classList.remove("dragging");
    });
  }
  return card;
}

function renderNoteGrid(notes) {
  const grid = $("notes-card-grid");
  grid.replaceChildren();
  for (const note of notes) grid.append(createNoteCard(note));
}

function firstBoardSectionId() {
  return notesState.board?.sections?.[0]?.id || "now";
}

function populateBoardSectionSelect(selected = "") {
  const select = $("ne-board-section");
  select.replaceChildren();
  for (const section of notesState.board?.sections || []) {
    const option = document.createElement("option");
    option.value = section.id;
    option.textContent = section.title;
    option.selected = section.id === selected;
    select.append(option);
  }
  if (!select.value && select.options.length) select.value = select.options[0].value;
}

function placementFor(noteId) {
  return (notesState.board?.placements || []).find((item) => item.note_id === noteId) || null;
}

function normalizeBoardOrders() {
  const bySection = new Map();
  for (const placement of notesState.board?.placements || []) {
    if (!bySection.has(placement.section_id)) bySection.set(placement.section_id, []);
    bySection.get(placement.section_id).push(placement);
  }
  for (const placements of bySection.values()) {
    placements.sort((a, b) => Number(a.order || 0) - Number(b.order || 0));
    placements.forEach((placement, index) => { placement.order = index; });
  }
}

async function saveNotesBoard() {
  if (!notesState.board) return false;
  normalizeBoardOrders();
  try {
    const result = await action("notes_board_save", {
      revision: notesState.board.revision,
      board: notesState.board,
    });
    if (!result.ok) {
      if (result.board) notesState.board = result.board;
      setNotesStatus(result.error || "Board could not be saved.", false);
      return false;
    }
    notesState.board = result.board;
    return true;
  } catch (error) {
    setNotesStatus(`Board save failed: ${error.message}`, false);
    return false;
  }
}

async function moveNoteOnBoard(noteId, sectionId) {
  if (!notesState.board) return;
  const placements = notesState.board.placements || [];
  notesState.board.placements = placements.filter((item) => item.note_id !== noteId);
  const order = notesState.board.placements.filter((item) => item.section_id === sectionId).length;
  notesState.board.placements.push({
    note_id: noteId,
    section_id: sectionId || firstBoardSectionId(),
    order,
    size: "medium",
  });
  if (await saveNotesBoard()) renderNotesWorkspace();
}

async function removeNoteFromBoard(noteId) {
  if (!notesState.board) return;
  const before = notesState.board.placements || [];
  notesState.board.placements = before.filter((item) => item.note_id !== noteId);
  if (before.length !== notesState.board.placements.length) await saveNotesBoard();
}

async function renameBoardSection(sectionId, title) {
  const section = (notesState.board?.sections || []).find((item) => item.id === sectionId);
  if (!section) return;
  section.title = String(title || "Section").trim().slice(0, 60) || "Section";
  await saveNotesBoard();
}

async function removeBoardSection(sectionId) {
  const sections = notesState.board?.sections || [];
  if (sections.length <= 1) {
    setNotesStatus("The board needs at least one section.", false);
    return;
  }
  const section = sections.find((item) => item.id === sectionId);
  if (!(await confirmDialog(`Remove the '${section?.title || "section"}' section? Its notes will move to the first section.`, "Remove section"))) {
    return;
  }
  notesState.board.sections = sections.filter((item) => item.id !== sectionId);
  const fallback = firstBoardSectionId();
  for (const placement of notesState.board.placements || []) {
    if (placement.section_id === sectionId) placement.section_id = fallback;
  }
  if (await saveNotesBoard()) renderNotesWorkspace();
}

function renderNotesBoard(notes) {
  const board = $("notes-board");
  board.replaceChildren();
  const noteMap = new Map(notes.map((note) => [note.note_id, note]));
  const placedIds = new Set();
  for (const section of notesState.board?.sections || []) {
    const column = document.createElement("section");
    column.className = "notes-board-column";
    column.dataset.sectionId = section.id;

    const header = document.createElement("div");
    header.className = "notes-board-column-head";
    const name = document.createElement("input");
    name.type = "text";
    name.value = section.title;
    name.maxLength = 60;
    name.setAttribute("aria-label", "Board section name");
    name.addEventListener("change", () => renameBoardSection(section.id, name.value));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn btn-icon";
    remove.title = "Remove section";
    remove.setAttribute("aria-label", `Remove ${section.title} section`);
    remove.textContent = "×";
    remove.addEventListener("click", () => removeBoardSection(section.id));
    header.append(name, remove);
    column.append(header);

    const dropzone = document.createElement("div");
    dropzone.className = "notes-board-dropzone";
    dropzone.dataset.sectionId = section.id;
    dropzone.addEventListener("dragover", (event) => {
      event.preventDefault();
      dropzone.classList.add("drag-over");
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    });
    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag-over"));
    dropzone.addEventListener("drop", (event) => {
      event.preventDefault();
      dropzone.classList.remove("drag-over");
      const noteId = event.dataTransfer?.getData("text/plain") || draggedNoteId;
      if (noteId) moveNoteOnBoard(noteId, section.id);
    });

    const placements = (notesState.board?.placements || [])
      .filter((item) => item.section_id === section.id)
      .sort((a, b) => Number(a.order || 0) - Number(b.order || 0));
    for (const placement of placements) {
      const note = noteMap.get(placement.note_id);
      if (!note) continue;
      placedIds.add(note.note_id);
      dropzone.append(createNoteCard(note, { draggable: true }));
    }
    if (!dropzone.childElementCount) {
      const hint = document.createElement("p");
      hint.className = "notes-drop-hint";
      hint.textContent = "Drag a note here";
      dropzone.append(hint);
    }
    column.append(dropzone);
    board.append(column);
  }

  const unplaced = notes.filter((note) => !placedIds.has(note.note_id));
  const unplacedGrid = $("notes-unplaced-grid");
  unplacedGrid.replaceChildren();
  for (const note of unplaced) unplacedGrid.append(createNoteCard(note, { unplaced: true }));
  $("notes-unplaced").hidden = unplaced.length === 0;
}

function renderNotesWorkspace() {
  const meta = NOTE_VIEW_META[notesState.view] || NOTE_VIEW_META.all;
  $("notes-view-title").textContent = meta[0];
  $("notes-view-kicker").textContent = meta[1];
  const notes = visibleNotes();
  $("notes-count").textContent = `(${notes.length})`;
  renderNotesViewButtons();
  renderNotesFacets();
  const boardMode = notesState.view === "board";
  $("notes-board-view").hidden = !boardMode;
  $("notes-card-grid").hidden = boardMode;
  $("board-add-section").hidden = !boardMode;
  if (boardMode) renderNotesBoard(notes);
  else renderNoteGrid(notes);
  const isEmpty = notes.length === 0;
  $("notes-empty").hidden = !isEmpty;
  if (isEmpty) {
    $("notes-board-view").hidden = true;
    $("notes-card-grid").hidden = true;
  }
  populateBoardSectionSelect(placementFor(notesState.current?.note_id)?.section_id || "");
}

function setNotesView(view) {
  if (!NOTE_VIEW_META[view]) return;
  notesState.view = view;
  loadNotes(false);
}

function setEditorOpen(open) {
  $("note-editor").hidden = !open;
  $("notes-layout").classList.toggle("editor-open", open);
}

function populateEditorCategories(selected = INBOX_PLACEHOLDER) {
  const categories = [
    "inbox",
    ...notesCategories,
    ...(notesState.facets.categories || []),
  ].filter((value, index, all) => value && all.indexOf(value) === index);
  const select = $("ne-category");
  select.replaceChildren();
  for (const category of categories) {
    const option = document.createElement("option");
    option.value = category;
    option.textContent = category;
    option.selected = category === selected;
    select.append(option);
  }
}

const INBOX_PLACEHOLDER = "inbox";

function setEditorReadOnly(trashed) {
  for (const id of [
    "ne-title", "ne-kind", "ne-status-select", "ne-body", "ne-category",
    "ne-color", "ne-tags", "ne-due", "ne-source", "ne-pinned",
    "ne-on-board", "ne-board-section",
  ]) {
    $(id).disabled = trashed;
  }
  $("ne-save").hidden = trashed;
  $("ne-organize").disabled = trashed || !notesState.current?.note_id;
  $("ne-archive").hidden = trashed || notesState.current?.status === "archived";
  $("ne-trash").hidden = trashed;
  $("ne-restore").hidden = !trashed;
  $("ne-delete").hidden = !trashed;
}

function fillNoteEditor(note) {
  const isNew = !note;
  const current = note || {
    title: "",
    body: "",
    kind: "note",
    status: "active",
    category: "inbox",
    tags: [],
    color: "yellow",
    due: "",
    source: "",
    pinned: false,
    summary: "",
  };
  $("ne-kicker").textContent = isNew ? "Quick capture" : noteKindMeta(current.kind)[1];
  $("ne-heading").textContent = isNew ? "New note" : "Edit note";
  $("ne-title").value = current.title || "";
  $("ne-body").value = current.body || "";
  $("ne-kind").value = current.kind || "note";
  $("ne-status-select").value = current.status === "trashed" ? "active" : (current.status || "active");
  populateEditorCategories(current.category || "inbox");
  $("ne-color").value = current.color || "yellow";
  $("ne-tags").value = (current.tags || []).join(", ");
  $("ne-due").value = (current.due || "").slice(0, 10);
  $("ne-source").value = current.source || "";
  $("ne-pinned").checked = !!current.pinned;
  const placement = current.note_id ? placementFor(current.note_id) : null;
  $("ne-on-board").checked = !!placement || (isNew && notesState.view === "board");
  populateBoardSectionSelect(placement?.section_id || firstBoardSectionId());
  $("ne-board-row").hidden = !$("ne-on-board").checked;
  $("ne-summary").hidden = !current.summary;
  $("ne-summary").textContent = current.summary ? `Local AI summary · ${current.summary}` : "";
  $("ne-save").textContent = isNew ? "Create note" : "Save note";
  setEditorReadOnly(current.status === "trashed");
  setEditorStatus("");
}

function openNewNote(prefill = "") {
  notesState.current = null;
  fillNoteEditor(null);
  const captured = String(prefill || "");
  $("ne-body").value = captured;
  if (/^https?:\/\/\S+$/i.test(captured.trim())) {
    $("ne-kind").value = "read_later";
    $("ne-source").value = captured.trim();
  }
  setEditorOpen(true);
  $("ne-body").focus();
}

async function openNoteEditor(noteId) {
  try {
    const note = await action("note_get", { note_id: noteId });
    if (!note.ok) {
      setNotesStatus(note.error || "Note not found.", false);
      return;
    }
    notesState.current = note;
    fillNoteEditor(note);
    setEditorOpen(true);
  } catch (error) {
    setNotesStatus(`Open failed: ${error.message}`, false);
  }
}

function closeNoteEditor() {
  notesState.current = null;
  setEditorOpen(false);
}

function editorNoteFields() {
  return {
    title: $("ne-title").value.trim(),
    body: $("ne-body").value,
    kind: $("ne-kind").value,
    status: $("ne-status-select").value,
    category: $("ne-category").value || "inbox",
    tags: $("ne-tags").value,
    color: $("ne-color").value,
    due: $("ne-due").value,
    source: $("ne-source").value.trim(),
    pinned: $("ne-pinned").checked,
  };
}

async function syncEditorBoardPlacement(noteId) {
  if (!notesState.board) return;
  if ($("ne-on-board").checked) {
    const sectionId = $("ne-board-section").value || firstBoardSectionId();
    const existing = placementFor(noteId);
    if (existing) existing.section_id = sectionId;
    else {
      notesState.board.placements.push({
        note_id: noteId,
        section_id: sectionId,
        order: notesState.board.placements.filter((item) => item.section_id === sectionId).length,
        size: "medium",
      });
    }
    await saveNotesBoard();
  } else {
    await removeNoteFromBoard(noteId);
  }
}

async function saveNoteEditor() {
  const fields = editorNoteFields();
  if (!fields.title && !fields.body.trim() && !fields.source) {
    setEditorStatus("Write something before saving.", false);
    return;
  }
  setEditorStatus("Saving…");
  try {
    let saved;
    if (notesState.current?.note_id) {
      saved = await action("note_update", {
        note_id: notesState.current.note_id,
        revision: notesState.current.revision,
        patch: fields,
      });
    } else {
      saved = await action("note_create", { ...fields, captured_via: "dashboard" });
    }
    if (!saved.ok) {
      if (saved.conflict && saved.note) {
        notesState.current = saved.note;
        fillNoteEditor(saved.note);
      }
      setEditorStatus(saved.error || "Save failed.", false);
      return;
    }
    await syncEditorBoardPlacement(saved.note_id);
    notesState.current = saved;
    await loadNotes(false);
    fillNoteEditor(saved);
    setEditorStatus("Saved.");
  } catch (error) {
    setEditorStatus(`Save failed: ${error.message}`, false);
  }
}

async function organizeCurrentNote() {
  if (!notesState.current?.note_id) {
    setEditorStatus("Save the note before organizing it.", false);
    return;
  }
  const button = $("ne-organize");
  button.disabled = true;
  setEditorStatus("Organizing with the local model…");
  try {
    const organized = await action("note_organize", {
      note_id: notesState.current.note_id,
      revision: notesState.current.revision,
    });
    if (!organized.ok) {
      if (organized.conflict && organized.note) {
        notesState.current = organized.note;
        fillNoteEditor(organized.note);
      }
      setEditorStatus(organized.error || "Organize failed.", false);
      return;
    }
    notesState.current = organized;
    await loadNotes(false);
    fillNoteEditor(organized);
    const suggestion = organized.suggested_category;
    const detail = organized.category === "inbox"
      && suggestion && suggestion !== "inbox"
      ? ` Kept in Inbox; suggested ${suggestion}.`
      : ` Filed in ${organized.category}.`;
    setEditorStatus(`Organized.${detail}`);
  } catch (error) {
    setEditorStatus(`Organize failed: ${error.message}`, false);
  } finally {
    button.disabled = !notesState.current?.note_id
      || notesState.current?.status === "trashed";
  }
}

async function archiveCurrentNote() {
  if (!notesState.current?.note_id) return;
  try {
    const result = await action("note_archive", {
      note_id: notesState.current.note_id,
      revision: notesState.current.revision,
    });
    if (!result.ok) {
      setEditorStatus(result.error || "Archive failed.", false);
      return;
    }
    await removeNoteFromBoard(result.note_id);
    closeNoteEditor();
    await loadNotes(false);
  } catch (error) {
    setEditorStatus(`Archive failed: ${error.message}`, false);
  }
}

async function trashCurrentNote() {
  if (!notesState.current?.note_id) {
    closeNoteEditor();
    return;
  }
  if (!(await confirmDialog("Move this note to Trash? You can restore it later.", "Move to Trash"))) return;
  try {
    const noteId = notesState.current.note_id;
    const result = await action("note_trash", { note_id: noteId });
    if (!result.ok) {
      setEditorStatus(result.error || "Move to Trash failed.", false);
      return;
    }
    await removeNoteFromBoard(noteId);
    closeNoteEditor();
    await loadNotes(false);
  } catch (error) {
    setEditorStatus(`Move to Trash failed: ${error.message}`, false);
  }
}

async function restoreCurrentNote() {
  if (!notesState.current?.note_id) return;
  try {
    const result = await action("note_restore", { note_id: notesState.current.note_id });
    if (!result.ok) {
      setEditorStatus(result.error || "Restore failed.", false);
      return;
    }
    notesState.current = result;
    await loadNotes(false);
    fillNoteEditor(result);
    setEditorStatus("Restored.");
  } catch (error) {
    setEditorStatus(`Restore failed: ${error.message}`, false);
  }
}

async function permanentlyDeleteCurrentNote() {
  if (!notesState.current?.note_id) return;
  if (!(await confirmDialog("Permanently delete this note? This cannot be undone.", "Delete forever"))) return;
  try {
    const result = await action("note_delete", {
      note_id: notesState.current.note_id,
      permanent: true,
    });
    if (!result.ok) {
      setEditorStatus(result.error || "Delete failed.", false);
      return;
    }
    closeNoteEditor();
    await loadNotes(false);
  } catch (error) {
    setEditorStatus(`Delete failed: ${error.message}`, false);
  }
}

async function addBoardSection() {
  const input = $("board-section-title");
  const title = input.value.trim();
  if (!title || !notesState.board) return;
  const base = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "section";
  const existing = new Set(notesState.board.sections.map((section) => section.id));
  let sectionId = base;
  let suffix = 2;
  while (existing.has(sectionId)) {
    sectionId = `${base}-${suffix}`;
    suffix += 1;
  }
  notesState.board.sections.push({ id: sectionId, title: title.slice(0, 60) });
  input.value = "";
  if (await saveNotesBoard()) renderNotesWorkspace();
}

async function loadNotes(takeStaged = true) {
  setNotesStatus("Loading…");
  try {
    const [feed, boardResult, cfg] = await Promise.all([
      action("notes_query", notesQueryArgs()),
      action("notes_board_get"),
      action("config_snapshot"),
    ]);
    notesState.results = feed.results || [];
    notesState.facets = feed.facets || { counts: {}, categories: [], tags: [] };
    notesState.board = boardResult.board;
    notesCategories = (cfg.notes || {}).categories || [];
    renderNotesWorkspace();
    setNotesStatus("");
    if (takeStaged) {
      const staged = await action("note_take_staged");
      if (staged.staged) openNewNote(staged.text || "");
    }
  } catch (error) {
    setNotesStatus(`Notes unavailable: ${error.message}`, false);
    notesState.results = [];
    renderNotesWorkspace();
  }
}

function populateNotesConfig(notes) {
  const cfg = notes || {};
  $("notes-vault").value = cfg.vault_dir || "";
  notesCategories = [...new Set((cfg.categories || [])
    .map(normalizeConfigCategory)
    .filter(Boolean))]
    .sort((a, b) => a.localeCompare(b));
  renderNotesCategoryManager();
  $("notes-fetch-timeout").value = cfg.fetch_timeout_seconds ?? 8;
  $("notes-max-chars").value = cfg.max_extracted_chars ?? 2000;
  $("notes-low-conf").checked = cfg.low_confidence_to_inbox !== false;
  $("notes-allow-new").checked = cfg.allow_new_categories !== false;
  $("notes-gen-title").checked = cfg.generate_title !== false;
  $("notes-gen-summary").checked = cfg.generate_summary !== false;
}

function normalizeConfigCategory(value) {
  const parts = String(value || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\/+|\/+$/g, "")
    .split("/");
  if (parts.length < 1 || parts.length > 2) return "";
  const normalized = parts.map((part) => part
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 32)
    .replace(/-+$/g, ""));
  if (normalized.some((part) => !part)) return "";
  const category = normalized.join("/");
  return category === "inbox" || category.length > 65 ? "" : category;
}

function renderNotesCategoryManager() {
  const list = $("notes-categories");
  list.replaceChildren();
  for (const category of notesCategories) {
    const row = document.createElement("div");
    row.className = "config-category";
    row.setAttribute("role", "listitem");
    const label = document.createElement("span");
    label.textContent = category;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "config-category-remove";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove ${category}`);
    remove.addEventListener("click", () => {
      notesCategories = notesCategories.filter((item) => item !== category);
      renderNotesCategoryManager();
    });
    row.append(label, remove);
    list.append(row);
  }
}

function addNotesCategory() {
  const input = $("notes-category-new");
  const category = normalizeConfigCategory(input.value);
  if (!category) {
    setStatus(
      "config-status",
      "⚠ Use one safe folder or parent/child pair, such as research or work/technical.",
      false,
    );
    return;
  }
  if (!notesCategories.includes(category)) notesCategories.push(category);
  notesCategories.sort((a, b) => a.localeCompare(b));
  input.value = "";
  renderNotesCategoryManager();
  setStatus("config-status", "");
}

function notesConfigPatch() {
  return {
    vault_dir: $("notes-vault").value.trim(),
    categories: [...notesCategories],
    fetch_timeout_seconds: Number($("notes-fetch-timeout").value) || 8,
    max_extracted_chars: Number($("notes-max-chars").value) || 2000,
    low_confidence_to_inbox: $("notes-low-conf").checked,
    allow_new_categories: $("notes-allow-new").checked,
    generate_title: $("notes-gen-title").checked,
    generate_summary: $("notes-gen-summary").checked,
  };
}

// ---- Config ----------------------------------------------------------------

function populatePromptBuilder(pb) {
  const cfg = { ...PROMPT_BUILDER_DEFAULTS, ...(pb || {}) };
  $("pb-version").value = cfg.prompt_version;
  $("pb-target").value = cfg.target_agent;
  $("pb-action").value = cfg.action_mode;
  $("pb-detail").value = cfg.detail_level;
  $("pb-structure").value = cfg.structure;
  $("pb-acceptance").checked = !!cfg.include_acceptance_criteria;
  $("pb-verification").checked = !!cfg.include_verification;
  $("pb-output").checked = cfg.include_output_format !== false;
  $("pb-preserve").checked = cfg.preserve_user_constraints !== false;
  $("pb-allow-suffix").checked = cfg.allow_user_suffix !== false;
  $("pb-suffix").value = cfg.user_suffix || "";
  $("pb-suffix").disabled = !$("pb-allow-suffix").checked;
  updatePromptBuilderHint();
}

function promptBuilderPatch() {
  const suffix = $("pb-allow-suffix").checked ? $("pb-suffix").value.trim().slice(0, 500) : "";
  return {
    prompt_version: $("pb-version").value || "v2",
    target_agent: $("pb-target").value || "claude_code",
    detail_level: $("pb-detail").value || "concise",
    action_mode: $("pb-action").value || "implement",
    structure: $("pb-structure").value || "agent_default",
    include_acceptance_criteria: $("pb-acceptance").checked,
    include_verification: $("pb-verification").checked,
    include_output_format: $("pb-output").checked,
    preserve_user_constraints: $("pb-preserve").checked,
    allow_user_suffix: $("pb-allow-suffix").checked,
    user_suffix: suffix,
  };
}

function updatePromptBuilderHint() {
  const override = ($("pb-structure").value || "agent_default") !== "agent_default";
  $("pb-structure-note").hidden = !override;
  $("pb-suffix").disabled = !$("pb-allow-suffix").checked;
  const remaining = 500 - ($("pb-suffix").value || "").length;
  setStatus("pb-status", remaining < 0 ? "Suffix is over 500 characters; save will trim it." : "");
}

async function previewPromptBuilder() {
  const preview = $("pb-preview");
  preview.hidden = true;
  setStatus("pb-status", "Rendering preview…");
  try {
    const result = await action("prompt_builder_preview", {
      sample: "Refactor the selected code, fix edge cases, and run the relevant tests.",
      settings: promptBuilderPatch(),
    });
    preview.textContent = result.output || "";
    preview.hidden = false;
    const status = result.valid ? `Preview: ${result.target_agent}/${result.structure}` : `Preview has issues: ${(result.errors || []).join(", ")}`;
    setStatus("pb-status", status, !!result.valid);
  } catch (e) {
    setStatus("pb-status", `Preview failed: ${e.message}`, false);
  }
}

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
    $("hk-in-chat").value = hk.open_chat || "^!c";
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
    const server = cfg.server || {};
    const perf = server.performance_mode || "balanced";
    document.querySelectorAll('input[name="perf"]').forEach((r) => (r.checked = r.value === perf));
    $("cfg-warm-on-start").checked = server.warm_on_start !== false;
    $("cfg-keep-warm").value = server.keep_warm_minutes ?? 15;
    $("cfg-store-text").checked = !!cfg.history_store_text;
    const routing = cfg.routing || {};
    $("cfg-routing").checked = routing.enabled !== false;
    $("cfg-long-thr").value = routing.long_threshold_chars ?? 1400;
    $("cfg-chunk-size").value = routing.chunk_size_chars ?? 1200;
    $("cfg-min-chunk").value = routing.min_chunk_chars ?? 700;
    populatePromptBuilder(cfg.prompt_builder || {});
    const tone = (cfg.tone || {}).preset || "formal";
    document.querySelectorAll('input[name="tone"]').forEach((r) => (r.checked = r.value === tone));
    populateNotesConfig(cfg.notes || {});
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
// Full candidate list [{name, fits, fit_reason, footprint_gb, installed, ...}].
let modelOptions = [];
// Name selected in the installed-models list (replaces the old <select>.value).
let selectedModel = "";

// Mirrors ffp_hardware.parse_params_b: 'qwen3.5:4b' -> 4, 'mistral:7b' -> 7.
// NOTE: total params. MoE tags ('...35b-a3b') are 35 here; the daemon supplies
// the authoritative fit via model_recommendations, so this is only a fallback
// for a free-typed name the catalog has never heard of.
function parseParamsB(name) {
  const m = /(\d+(?:\.\d+)?)\s*b\b/i.exec(name || "");
  return m ? parseFloat(m[1]) : null;
}

// 'qwen3.6-moe:35b-a3b' -> 3 (active params); null when the tag isn't MoE.
function parseActiveParamsB(name) {
  const m = /\ba(\d+(?:\.\d+)?)\s*b\b/i.exec(name || "");
  if (!m) return null;
  const active = parseFloat(m[1]);
  const total = parseParamsB(name);
  return total !== null && active < total ? active : null;
}

function renderInstalledModels(names, active, errorMessage) {
  const list = $("models-list");
  list.replaceChildren();
  if (errorMessage) {
    const li = document.createElement("li");
    li.className = "combo-empty";
    li.textContent = `Could not list models: ${errorMessage}`;
    list.append(li);
    return;
  }
  if (!names.length) selectedModel = "";
  if (!names.includes(selectedModel)) selectedModel = active && names.includes(active) ? active : (names[0] || "");
  for (const name of names) {
    const li = document.createElement("li");
    li.className = "model-row";
    li.setAttribute("role", "option");
    li.setAttribute("aria-selected", String(name === selectedModel));
    const label = document.createElement("span");
    label.textContent = name;
    li.append(label);
    if (name === active) {
      const tag = document.createElement("span");
      tag.className = "tag active";
      tag.textContent = "★ active";
      li.append(tag);
    }
    li.addEventListener("click", () => {
      selectedModel = name;
      for (const row of list.children) {
        row.setAttribute("aria-selected", String(row === li));
      }
    });
    list.append(li);
  }
}

function renderModelAlert(health) {
  const el = $("model-alert");
  if (!health || health.status !== "not_installed") {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  // A provider upgrade can invalidate an already-pulled model (FLM 0.9.45
  // rejects weights stamped for 0.9.43) and then reports it as not installed.
  // Without this the next hotkey just fails with an opaque provider error.
  el.hidden = false;
  el.textContent =
    `⚠ Active model "${health.model}" is not installed for the current ` +
    `${health.provider} version. Hotkeys and chat will fail until you ` +
    `re-download it below (or pick another installed model).`;
}

async function loadModels() {
  let installedNames = [];
  let activeName = "";
  try {
    const installed = await action("models_installed");
    installedNames = installed.models || [];
    activeName = installed.active || "";
    renderInstalledModels(installedNames, activeName, "");
  } catch (e) {
    renderInstalledModels([], "", e.message);
  }

  // Hardware-aware fit info. Every candidate is listed — oversized ones are
  // shown with a warning rather than hidden, because silently filtering them
  // made a catalog model invisible and forced users to pull it by hand.
  let rec = null;
  try {
    rec = await action("model_recommendations");
    modelBudget = rec.budget || null;
    const usable = rec.usable_memory_gb;
    $("hw-summary").textContent = modelBudget
      ? `This machine: ${modelBudget.summary}` + (usable ? ` (~${usable} GB usable for weights).` : ".")
      : "";
    renderModelAlert(rec.active_model);
  } catch {
    modelBudget = null;
    $("hw-summary").textContent = "";
    renderModelAlert(null);
  }

  const installedSet = new Set(installedNames);
  if (rec && (rec.models || []).length) {
    modelOptions = rec.models.filter((m) => !installedSet.has(m.name));
  } else {
    try {
      const avail = await action("models_not_installed");
      modelOptions = (avail.models || []).map((name) => ({ name, fits: "unknown", fit_reason: "" }));
    } catch {
      modelOptions = [];
    }
  }
  renderComboOptions();
}

// ---- Pull combobox (replaces <datalist>, which browsers refuse to style) ----

let comboIndex = -1;

function fitLabel(m) {
  if (m.fits === "tight") return "tight";
  if (m.fits === "no") return "too large";
  return "";
}

function renderComboOptions() {
  const box = $("pull-options");
  const query = $("pull-name").value.trim().toLowerCase();
  const matches = modelOptions.filter((m) => !query || m.name.toLowerCase().includes(query));
  box.replaceChildren();
  comboIndex = -1;
  if (!matches.length) {
    const li = document.createElement("li");
    li.className = "combo-empty";
    li.textContent = modelOptions.length ? "No match — any name can still be typed." : "No suggestions available.";
    box.append(li);
    return;
  }
  for (const m of matches) {
    const li = document.createElement("li");
    li.className = "combo-option" + (m.fits === "no" ? " oversized" : "");
    li.setAttribute("role", "option");
    li.dataset.name = m.name;
    const label = document.createElement("span");
    label.textContent = m.name;
    li.append(label);
    const tag = fitLabel(m);
    if (tag) {
      const fit = document.createElement("span");
      fit.className = `fit ${m.fits}`;
      fit.textContent = tag;
      if (m.fit_reason) fit.title = m.fit_reason;
      li.append(fit);
    } else if (m.footprint_gb) {
      const fit = document.createElement("span");
      fit.className = "fit";
      fit.textContent = `${m.footprint_gb} GB`;
      if (m.fit_reason) fit.title = m.fit_reason;
      li.append(fit);
    }
    li.addEventListener("mousedown", (e) => {
      e.preventDefault(); // keep focus in the input so blur doesn't race the click
      $("pull-name").value = m.name;
      closeCombo();
    });
    box.append(li);
  }
}

function openCombo() {
  renderComboOptions();
  $("pull-options").hidden = false;
  $("pull-name").setAttribute("aria-expanded", "true");
}

function closeCombo() {
  $("pull-options").hidden = true;
  $("pull-name").setAttribute("aria-expanded", "false");
  comboIndex = -1;
}

function moveCombo(delta) {
  const rows = [...$("pull-options").querySelectorAll(".combo-option")];
  if (!rows.length) return;
  if ($("pull-options").hidden) openCombo();
  comboIndex = (comboIndex + delta + rows.length) % rows.length;
  rows.forEach((r, i) => r.classList.toggle("highlighted", i === comboIndex));
  rows[comboIndex].scrollIntoView({ block: "nearest" });
}

function commitCombo() {
  const rows = [...$("pull-options").querySelectorAll(".combo-option")];
  if (comboIndex >= 0 && rows[comboIndex]) {
    $("pull-name").value = rows[comboIndex].dataset.name || "";
    closeCombo();
    return true;
  }
  return false;
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
  const notesPatch = notesConfigPatch();
  if (notesPatch.categories.length === 0) {
    setStatus("config-status", "⚠ Notes categories cannot be empty.", false);
    return;
  }
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
    server: {
      performance_mode: perf ? perf.value : "balanced",
      warm_on_start: $("cfg-warm-on-start").checked,
      keep_warm_minutes: Math.max(0, Math.min(Number($("cfg-keep-warm").value) || 0, 1440)),
    },
    routing: {
      enabled: $("cfg-routing").checked,
      long_threshold_chars: Number($("cfg-long-thr").value) || 1400,
      chunk_size_chars: Number($("cfg-chunk-size").value) || 1200,
      min_chunk_chars: Number($("cfg-min-chunk").value) || 700,
    },
    prompt_builder: promptBuilderPatch(),
    modes: { tone: { preset: tone ? tone.value : "formal" } },
    hotkeys,
    notes: notesPatch,
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
  const name = selectedModel;
  if (!name) {
    setStatus("config-status", "Pick an installed model first.", false);
    return;
  }
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
  const name = selectedModel;
  if (!name) {
    setStatus("config-status", "Pick an installed model first.", false);
    return;
  }
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
  closeCombo();
  // Prefer the daemon's authoritative verdict (it knows the real footprint and
  // MoE active-param count); fall back to tag parsing for an unknown name.
  const known = modelOptions.find((m) => m.name === name);
  if (known && known.fits === "no") {
    const msg = `'${name}' is likely too big for this machine — ${known.fit_reason}. Download anyway?`;
    if (!(await confirmDialog(msg, "Download anyway"))) return;
  } else if (!known) {
    const effective = parseActiveParamsB(name) ?? parseParamsB(name);
    if (modelBudget && effective && effective > modelBudget.max_params_b * 1.5) {
      const msg = `'${name}' looks like a ${effective}B model — likely too big for this machine (${modelBudget.summary}). Download anyway?`;
      if (!(await confirmDialog(msg, "Download anyway"))) return;
    }
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

// Parse one SSE frame (text between blank-line separators) into {event, data}.
// Returns null for frames without a JSON data payload.
function parseSseFrame(frame) {
  let event = "message";
  const dataLines = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (!dataLines.length) return null;
  try { return { event, data: JSON.parse(dataLines.join("\n")) }; }
  catch (e) { return null; }
}

// Stream chat_send_stream: call onDelta(text) per token chunk; resolve with the
// terminal metadata ({thread_id, title, notes_used, error}). Throws only when the
// stream can't be opened (old daemon / transport) so the caller can fall back to
// the one-shot chat_send action; a mid-stream failure resolves with .error set.
async function streamChat(args, onDelta) {
  const res = await fetch("/action/chat_send_stream", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8", "X-FFP-API": API_HEADER },
    body: JSON.stringify({ args }),
  });
  if (!res.ok || !res.body) throw new Error(`stream unavailable (HTTP ${res.status})`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const result = { thread_id: args.thread_id, title: "", notes_used: [], error: "" };
  let buf = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buf.indexOf("\n\n")) >= 0) {
        const evt = parseSseFrame(buf.slice(0, sep));
        buf = buf.slice(sep + 2);
        if (!evt) continue;
        if (evt.event === "done" || evt.event === "error") {
          Object.assign(result, evt.data);
          if (evt.event === "error") result.error = evt.data.error || "stream failed";
        } else if (evt.data && typeof evt.data.delta === "string") {
          onDelta(evt.data.delta);
        }
      }
    }
  } catch (e) {
    // Reader broke after the stream opened (and the turn was already persisted
    // server-side) — surface a partial, never re-send via the fallback path.
    result.error = result.error || e.message || "stream interrupted";
  }
  return result;
}

async function sendChat() {
  const input = $("chat-input");
  const message = input.value.trim();
  if (!message) return;
  const btn = $("chat-send");
  btn.disabled = true;
  setStatus("chat-status", "Thinking…");
  // Optimistically show the user's message; the reply streams in below it.
  const box = $("chat-transcript");
  const userDiv = document.createElement("div");
  userDiv.className = "chat-msg chat-msg-user";
  userDiv.textContent = message;
  box.append(userDiv);
  $("chat-placeholder").hidden = true;
  box.scrollTop = box.scrollHeight;
  input.value = "";
  const useNotes = $("chat-use-notes").checked;

  // Assistant bubble, filled incrementally as tokens arrive.
  const reply = document.createElement("div");
  reply.className = "chat-msg chat-msg-assistant";
  box.append(reply);
  const showNotes = (notes) =>
    setStatus("chat-status", notes && notes.length ? `📚 Grounded in: ${notes.join(", ")}` : "");

  try {
    const out = await streamChat({ thread_id: chatThreadId, message, use_notes: useNotes }, (delta) => {
      reply.textContent += delta;
      box.scrollTop = box.scrollHeight;
    });
    chatThreadId = out.thread_id || chatThreadId;
    if (!reply.textContent) reply.textContent = "(no reply)";
    if (out.error) setStatus("chat-status", `Send failed: ${out.error}`, false);
    else showNotes(out.notes_used);
    loadChatThreads();
  } catch (streamErr) {
    // Streaming endpoint unavailable — fall back to the one-shot JSON action.
    try {
      const res = await action("chat_send", { thread_id: chatThreadId, message, use_notes: useNotes });
      chatThreadId = res.thread_id || chatThreadId;
      reply.textContent = res.reply || "(no reply)";
      box.scrollTop = box.scrollHeight;
      showNotes(res.notes_used);
      loadChatThreads();
    } catch (e) {
      if (!reply.textContent) reply.remove();
      setStatus("chat-status", `Send failed: ${e.message}`, false);
    }
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
let mtgOffset = 0;
let renderedMeetingIds = new Set();
const MTG_PAGE = 30;

async function loadMeetings() {
  const st = $("mtg-status");
  try {
    const s = await action("quill_status");
    if (!s.enabled) {
      st.textContent = "Quill integration is off — enable it in Config › Meetings.";
      st.className = "muted small bad";
    } else if (s.reachable) {
      st.textContent = `Quill ${s.server_version || ""} connected`.trim();
      st.className = "muted small ok";
    } else {
      st.textContent = "Quill not reachable — make sure Quill is running.";
      st.className = "muted small bad";
    }
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
  loadActionItems();
}

async function searchMeetings() {
  mtgOffset = 0;
  renderedMeetingIds = new Set();
  $("mtg-results").replaceChildren();
  $("mtg-header-count").textContent = "";
  $("mtg-count").textContent = "";
  $("mtg-load-more").hidden = true;
  await _fetchMeetingsPage();
}

async function loadMoreMeetings() {
  await _fetchMeetingsPage();
}

async function _fetchMeetingsPage() {
  const q = $("mtg-query").value.trim();
  const body = $("mtg-results");
  try {
    const r = await action("quill_search_meetings", { query: q, limit: MTG_PAGE, offset: mtgOffset });
    const meetings = r.meetings || [];
    for (const m of meetings) {
      if (!m.id || renderedMeetingIds.has(m.id)) continue;
      renderedMeetingIds.add(m.id);
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
    const total = renderedMeetingIds.size;
    $("mtg-empty").hidden = total > 0;
    const hasMore = meetings.length === MTG_PAGE;
    const countLabel = total ? `${total}${hasMore ? "+" : ""} meeting${total === 1 ? "" : "s"}` : "";
    $("mtg-count").textContent = countLabel;
    $("mtg-header-count").textContent = countLabel ? `(${countLabel})` : "";
    $("mtg-load-more").hidden = !hasMore;
    if (hasMore) mtgOffset += MTG_PAGE;
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

const _QUALITY_LABELS = {
  low_substance: "⚠ low substance",
  social_filler: "⚠ social filler",
  trivial_meeting: "⚠ short meeting",
  too_short: "⚠ digest too short",
};

function _showQuality(quality) {
  const el = $("mtg-quality");
  if (!quality || quality.ok || !quality.flags?.length) { el.hidden = true; return; }
  el.textContent = quality.flags.map((f) => _QUALITY_LABELS[f] || f).join(" · ");
  el.hidden = false;
}

async function loadDigest() {
  const body = $("mtg-digest");
  body.textContent = "Loading…";
  $("mtg-process-status").textContent = "";
  $("mtg-quality").hidden = true;
  try {
    const d = await action("meeting_digest_get", { meeting_id: currentMeeting.id });
    if (d.found) {
      body.textContent = d.digest_md || "(empty digest)";
      const strictLabel = d.strict ? " · strict" : "";
      $("mtg-process-status").textContent = `cached ${(d.processed_at || "").replace("T", " ")} · ${d.source} · ${d.seconds}s${strictLabel}`;
      _showQuality(d.quality);
    } else {
      body.textContent = "Not processed yet. Click 'Process now' to generate a summary + action items on the local model, or wait for the after-hours batch.";
    }
  } catch (e) {
    body.textContent = `Failed: ${e.message}`;
  }
}

async function processMeetingNow() {
  if (!currentMeeting) return;
  $("mtg-process-status").textContent = "Processing on the local model… (first token can take ~15s on a full transcript)";
  $("mtg-quality").hidden = true;
  try {
    const r = await action("meeting_process", {
      meeting_id: currentMeeting.id, title: currentMeeting.title, date: currentMeeting.date, url: currentMeeting.url,
    });
    $("mtg-digest").textContent = r.digest_md || "(empty)";
    $("mtg-process-status").textContent = `done · ${r.source} · ${r.seconds}s`;
    _showQuality(r.quality);
    digestIds.add(currentMeeting.id);
  } catch (e) {
    $("mtg-process-status").textContent = `⚠ ${e.message}`;
  }
}

async function redigestMeeting() {
  if (!currentMeeting) return;
  $("mtg-process-status").textContent = "Re-digesting with strict prompt… (can take ~15s)";
  $("mtg-quality").hidden = true;
  try {
    const r = await action("meeting_redigest", {
      meeting_id: currentMeeting.id, title: currentMeeting.title, date: currentMeeting.date, url: currentMeeting.url,
    });
    $("mtg-digest").textContent = r.digest_md || "(empty)";
    $("mtg-process-status").textContent = `strict · ${r.source} · ${r.seconds}s`;
    _showQuality(r.quality);
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
  // Persist the current settings first (incl. the Enable toggle) so "Run now"
  // reflects what's on screen — otherwise it runs against the last-saved config.
  s.textContent = "Saving settings…";
  try {
    await action("apply_config_patch", { patch: { meetings: meetingsPatch() } });
  } catch (e) {
    s.textContent = `⚠ couldn't save settings: ${e.message}`;
    return;
  }
  s.textContent = "Running… (this processes on the local model; may take a while)";
  try {
    const r = await action("meeting_batch_run", {});
    s.textContent = r.ok
      ? `processed ${r.processed} of ${r.queued} queued`
        + (r.skipped ? `, ${r.skipped} skipped (no content)` : "")
        + (r.errors && r.errors.length ? `, ${r.errors.length} errors (details in logs/daemon.log)` : "")
      : `⚠ ${r.error}`;
  } catch (e) {
    s.textContent = `⚠ ${e.message}`;
  }
}

// Action-items review board (week/month) — sourced from cached digests; status
// is persisted server-side. Purely local (no Quill call needed).
async function loadActionItems() {
  const range = (document.querySelector('input[name="mtg-range"]:checked') || {}).value || "week";
  try {
    renderActionItems(await action("meeting_actions_list", { range }));
  } catch (e) {
    $("mtg-actions-list").replaceChildren();
    $("mtg-actions-empty").hidden = true;
    $("mtg-actions-counts").textContent = `(unavailable: ${e.message})`;
  }
}

function renderActionItems(data) {
  const box = $("mtg-actions-list");
  box.replaceChildren();
  const items = data.items || [];
  $("mtg-actions-empty").hidden = items.length > 0;
  const c = data.counts || {};
  $("mtg-actions-counts").textContent = items.length
    ? `(${c.pending || 0} pending · ${c.accepted || 0} accepted · ${c.rejected || 0} rejected)`
    : "";
  for (const it of items) {
    const row = document.createElement("div");
    row.className = `action-row status-${it.status}`;
    const main = document.createElement("div");
    main.className = "action-main";
    const txt = document.createElement("div");
    txt.className = "action-text";
    txt.textContent = (it.owner ? `[${it.owner}] ` : "") + it.text;
    const meta = document.createElement("div");
    meta.className = "muted small";
    meta.textContent = `${it.meeting_title || "meeting"} · ${String(it.date || "").slice(0, 10)}`;
    main.append(txt, meta);
    const btns = document.createElement("div");
    btns.className = "action-btns";
    const badge = document.createElement("span");
    badge.className = "action-badge";
    badge.textContent = it.status;
    const mk = (label, status, title) => {
      const b = document.createElement("button");
      b.className = "btn";
      b.textContent = label;
      b.title = title;
      b.addEventListener("click", () => setActionStatus(it.id, status));
      return b;
    };
    btns.append(badge, mk("✓", "accepted", "Accept"), mk("✗", "rejected", "Reject"), mk("↺", "pending", "Mark pending"));
    row.append(main, btns);
    box.append(row);
  }
}

async function setActionStatus(id, status) {
  try {
    await action("meeting_action_set_status", { id, status });
  } catch {
    /* a reload reflects the true state */
  }
  loadActionItems();
}

async function generateWeekSummary() {
  const offset = Number($("mtg-week-sel").value) || 0;
  const st = $("mtg-week-status");
  const out = $("mtg-week-output");
  st.textContent = "Generating on the local model…";
  out.textContent = "";
  try {
    const r = await action("meeting_week_summary", { week_offset: offset });
    if (r.meeting_count === 0) {
      out.textContent = "No processed meetings in that week.";
      st.textContent = r.week_label || "";
    } else {
      out.textContent = r.summary || "(empty)";
      st.textContent = `${r.week_label} · ${r.meeting_count} meeting${r.meeting_count === 1 ? "" : "s"}`;
    }
  } catch (e) {
    st.textContent = `⚠ ${e.message}`;
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
  attachHelpMarker("history-store-help", HISTORY_STORE_HELP_TEXT);
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
  $("note-query").addEventListener("input", (event) => {
    notesState.query = event.target.value.trim();
    clearTimeout(notesSearchTimer);
    notesSearchTimer = setTimeout(() => loadNotes(false), 180);
  });
  $("note-query").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      clearTimeout(notesSearchTimer);
      notesState.query = event.target.value.trim();
      loadNotes(false);
    }
  });
  $("notes-view-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-notes-view]");
    if (button) setNotesView(button.dataset.notesView);
  });
  $("note-new").addEventListener("click", () => openNewNote());
  $("notes-empty-new").addEventListener("click", () => openNewNote());
  $("ne-close").addEventListener("click", closeNoteEditor);
  $("ne-save").addEventListener("click", saveNoteEditor);
  $("ne-organize").addEventListener("click", organizeCurrentNote);
  $("ne-archive").addEventListener("click", archiveCurrentNote);
  $("ne-trash").addEventListener("click", trashCurrentNote);
  $("ne-restore").addEventListener("click", restoreCurrentNote);
  $("ne-delete").addEventListener("click", permanentlyDeleteCurrentNote);
  $("ne-on-board").addEventListener("change", () => {
    $("ne-board-row").hidden = !$("ne-on-board").checked;
  });
  $("ne-body").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      saveNoteEditor();
    }
  });
  $("board-section-add").addEventListener("click", addBoardSection);
  $("board-section-title").addEventListener("keydown", (event) => {
    if (event.key === "Enter") addBoardSection();
  });
  $("history-view-telemetry").addEventListener("click", () => setHistoryView("telemetry"));
  $("history-view-exposed").addEventListener("click", () => setHistoryView("exposed"));
  $("history-storage-action").addEventListener("click", setHistoryStorageFromBanner);
  $("mtg-search-btn").addEventListener("click", searchMeetings);
  $("mtg-query").addEventListener("keydown", (e) => { if (e.key === "Enter") searchMeetings(); });
  $("mtg-load-more").addEventListener("click", loadMoreMeetings);
  $("mtg-redigest").addEventListener("click", redigestMeeting);
  $("mtg-results").addEventListener("click", (e) => {
    const row = e.target.closest(".mtg-row");
    if (row) openMeeting(row);
  });
  $("mtg-close").addEventListener("click", () => { $("mtg-reader").hidden = true; });
  $("mtg-process").addEventListener("click", processMeetingNow);
  $("mtg-ask-btn").addEventListener("click", askMeeting);
  $("mtg-ask-input").addEventListener("keydown", (e) => { if (e.key === "Enter") askMeeting(); });
  $("mtg-run-now").addEventListener("click", runBatchNow);
  document.querySelectorAll('input[name="mtg-range"]').forEach((r) => r.addEventListener("change", loadActionItems));
  $("mtg-week-gen").addEventListener("click", generateWeekSummary);
  $("config-save").addEventListener("click", saveConfig);
  $("config-revert").addEventListener("click", loadConfig);
  $("notes-category-add").addEventListener("click", addNotesCategory);
  $("notes-category-new").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addNotesCategory();
    }
  });
  $("cm-select").addEventListener("change", fillCustomModeForm);
  $("cm-save").addEventListener("click", saveCustomMode);
  $("cm-delete").addEventListener("click", deleteCustomMode);
  $("pb-preview-btn").addEventListener("click", previewPromptBuilder);
  $("pb-structure").addEventListener("change", updatePromptBuilderHint);
  $("pb-allow-suffix").addEventListener("change", updatePromptBuilderHint);
  $("pb-suffix").addEventListener("input", updatePromptBuilderHint);
  $("model-set-active").addEventListener("click", setActiveModel);
  $("model-remove").addEventListener("click", removeModel);
  $("pull-btn").addEventListener("click", pullModel);
  // Pull combobox: typing filters, ▾ toggles, arrows/Enter/Escape navigate.
  $("pull-name").addEventListener("input", openCombo);
  $("pull-name").addEventListener("focus", openCombo);
  $("pull-name").addEventListener("blur", () => setTimeout(closeCombo, 120));
  $("pull-name").addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); moveCombo(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); moveCombo(-1); }
    else if (e.key === "Escape") { closeCombo(); }
    else if (e.key === "Enter") {
      // Enter picks the highlighted row; with nothing highlighted it submits.
      if (commitCombo()) e.preventDefault();
      else pullModel();
    }
  });
  $("pull-toggle").addEventListener("mousedown", (e) => {
    e.preventDefault();
    if ($("pull-options").hidden) { $("pull-name").focus(); openCombo(); }
    else closeCombo();
  });
  $("cfg-provider").addEventListener("change", onProviderChanged);
  $("provider-start").addEventListener("click", startProviderServer);
  $("flm-check").addEventListener("click", () => loadFlmVersion(true));
  $("bench-run").addEventListener("click", runBenchmark);
  refreshHealth();
  switchTab(tabFromHash() || "overview");
  setInterval(refreshHealth, 10000);
});
