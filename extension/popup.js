"use strict";

const $ = (id) => document.getElementById(id);
const DASHBOARD = "http://127.0.0.1:52650/#meetings";

function note(text) { $("note").textContent = text; }

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function ask(tab, msg) {
  try {
    return await chrome.tabs.sendMessage(tab.id, msg);
  } catch {
    return null;                      // not a Meet tab, or the page loaded before the extension
  }
}

async function refresh() {
  chrome.runtime.sendMessage({ type: "daemon-health" }, (h) => {
    const pill = $("daemon");
    pill.textContent = h && h.ok ? `connected · v${h.version}` : "Flowkey not running";
    pill.className = `pill ${h && h.ok ? "ok" : "bad"}`;
  });
  const { pendingPushes = [], lastError } = await chrome.storage.local.get(["pendingPushes", "lastError"]);
  $("retry").hidden = pendingPushes.length === 0;
  $("retry").textContent = `Send queued (${pendingPushes.length})`;
  if (pendingPushes.length && lastError) note(`Waiting for Flowkey — ${lastError}`);

  const tab = await activeTab();
  const onMeet = tab && /^https:\/\/meet\.google\.com\//.test(tab.url || "");
  const st = onMeet ? await ask(tab, { type: "status" }) : null;
  if (!onMeet) {
    $("tab-status").textContent = "Open a Google Meet call to capture it.";
    $("tab-details").hidden = true;
    return;
  }
  if (!st) {
    $("tab-status").textContent = "Reload this Meet tab once so Flowkey can attach to it.";
    $("tab-details").hidden = true;
    return;
  }
  $("tab-status").textContent = st.inCall ? "Capturing this call." : "Not in a call yet — capture starts when you join.";
  $("tab-details").hidden = false;
  $("t-title").textContent = st.title || st.code || "–";
  $("t-captions").textContent = st.captions ? "on ✓" : "off — turn on CC in Meet";
  $("t-segments").textContent = `${st.segments} caption turns`;
  $("t-speakers").textContent = st.speakers.length ? st.speakers.join(", ") : "–";
}

document.addEventListener("DOMContentLoaded", async () => {
  const opts = await chrome.storage.local.get({ enabled: true, autoCaptions: true });
  $("opt-enabled").checked = opts.enabled;
  $("opt-captions").checked = opts.autoCaptions;
  $("opt-enabled").addEventListener("change", (e) => chrome.storage.local.set({ enabled: e.target.checked }));
  $("opt-captions").addEventListener("change", (e) => chrome.storage.local.set({ autoCaptions: e.target.checked }));
  $("open-dashboard").addEventListener("click", () => chrome.tabs.create({ url: DASHBOARD }));
  $("retry").addEventListener("click", () => chrome.runtime.sendMessage({ type: "retry" }, () => refresh()));
  $("diagnostics").addEventListener("click", async () => {
    const tab = await activeTab();
    const d = tab ? await ask(tab, { type: "diagnostics" }) : null;
    if (!d) { note("Open the Meet tab first."); return; }
    await navigator.clipboard.writeText(d.text);
    note("Diagnostics copied (page structure only, no caption text).");
  });
  refresh();
  setInterval(refresh, 2000);
});
