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
async function browseNotes() {
  const query = $("note-query").value.trim();
  try {
    if (query) {
      const res = await action("note_search", { query, limit: 20 });
      $("notes-col3").textContent = "Snippet";
      const n = fillTable("notes-body", (res.results || []).map((r) => [r.title, r.category, r.snippet || ""]));
      $("notes-count").textContent = `(${res.count} match${res.count === 1 ? "" : "es"})`;
      $("notes-empty").hidden = n > 0;
    } else {
      const res = await action("notes_list", { limit: 20 });
      $("notes-col3").textContent = "Modified";
      const n = fillTable("notes-body", (res.results || []).map((r) => [r.title, r.category, r.modified]));
      $("notes-count").textContent = `(${res.count} total — newest 20)`;
      $("notes-empty").hidden = n > 0;
    }
  } catch (e) {
    fillTable("notes-body", [[`Notes unavailable: ${e.message}`, "", ""]]);
    $("notes-empty").hidden = true;
  }
}

async function loadNotes() {
  browseNotes();
  try {
    const cfg = await action("config_snapshot");
    const notes = cfg.notes || {};
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
  if (!window.confirm(`Delete custom mode '${id}'?`)) return;
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
    setStatus("config-status", "");
  } catch (e) {
    setStatus("config-status", `Load failed: ${e.message}`, false);
  }
  loadServerStatus();
  loadModels();
  loadAutostart();
  if (($("cfg-provider").value || "fastflowlm") === "fastflowlm") loadFlmVersion(false);
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

async function loadModels() {
  const list = $("models-list");
  const pull = $("pull-name");
  list.replaceChildren();
  pull.replaceChildren();
  try {
    const installed = await action("models_installed");
    for (const name of installed.models || []) {
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
  try {
    const avail = await action("models_not_installed");
    for (const name of avail.models || []) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      pull.append(opt);
    }
    if (!pull.children.length) {
      const opt = document.createElement("option");
      opt.textContent = "(no more models available)";
      pull.append(opt);
    }
  } catch {
    /* dropdown stays empty when flm is unreachable */
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
    await action("chat_restart");
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
  if (!window.confirm(`Remove model '${name}' from local storage?`)) return;
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
  const name = $("pull-name").value;
  if (!name || name.startsWith("(")) {
    setText("pull-status", "Pick a model from the dropdown first.");
    return;
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

async function loadBenchmark() {
  // flm bench is FastFlowLM-only; gate on the *effective* provider.
  try {
    const cfg = await action("config_snapshot");
    const active = ((cfg.llm || {}).provider) || "fastflowlm";
    const isFlm = active === "fastflowlm";
    $("bench-run").disabled = !isFlm;
    if (!isFlm) setText("bench-status", `Benchmarking uses flm bench and is FastFlowLM-only — active provider is ${PROVIDER_LABELS[active] || active}.`);
  } catch {
    /* leave the button enabled when the snapshot is unavailable */
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
      r.peak_prefill_tps ?? "-",
      r.peak_decode_tps ?? "-",
      r.points ?? 0,
    ]);
    const n = fillTable("bench-history-body", rows);
    $("bench-empty").hidden = n > 0;
  } catch (e) {
    fillTable("bench-history-body", [[`Benchmark history unavailable: ${e.message}`, "", "", "", ""]]);
  }
}

async function runBenchmark() {
  const model = $("bench-model").value;
  if (!model) {
    setText("bench-status", "Select an installed model first.");
    return;
  }
  const msg = `Benchmark '${model}'?\n\nThis runs flm bench for ~10–20 minutes, stops the server, and saturates the NPU. Hotkeys will be unresponsive until it finishes.`;
  if (!window.confirm(msg)) return;
  try {
    await action("bench_start", { model });
    setText("bench-status", `⏳ Benchmark started for ${model} — this takes 10–20 min…`);
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

// ---- Tabs & refresh --------------------------------------------------------

const TAB_LOADERS = {
  overview: loadOverview,
  telemetry: loadTelemetry,
  history: loadHistory,
  notes: loadNotes,
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

document.addEventListener("DOMContentLoaded", () => {
  $("tabs").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab");
    if (btn) switchTab(btn.dataset.tab);
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
  loadOverview();
  setInterval(refreshHealth, 10000);
});
