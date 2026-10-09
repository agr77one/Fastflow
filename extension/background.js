// Flowkey background worker: forwards meeting captures from the content script to the
// local Flowkey daemon. Every push carries the whole session header plus new/revised
// segments, and the daemon upserts -- so a push that fails (Flowkey not running) is
// queued and simply replayed later, in order, with nothing lost or duplicated.
"use strict";

const DAEMON = "http://127.0.0.1:52650";
const API_HEADER = "1";            // must match ffp_daemon.API_VERSION
const QUEUE_KEY = "pendingPushes";
const QUEUE_MAX = 2000;

async function daemon(action, args = {}) {
  const res = await fetch(`${DAEMON}/action/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8", "X-FFP-API": API_HEADER },
    body: JSON.stringify({ args }),
  });
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || `${action} failed`);
  return data.result;
}

let draining = Promise.resolve();

// Pushes are serialized: a capture's header must reach the daemon before its segments,
// and replays must keep their original order.
function push(args) {
  draining = draining.then(async () => {
    const { [QUEUE_KEY]: queue = [] } = await chrome.storage.local.get(QUEUE_KEY);
    if (args) queue.push(args);                    // null = just replay what's queued
    const left = [];
    let failed = false;
    for (const item of queue) {
      if (failed) { left.push(item); continue; }
      try {
        await daemon("capture_push", item);
      } catch (e) {
        failed = true;
        left.push(item);
        await chrome.storage.local.set({ lastError: `${new Date().toLocaleTimeString()}: ${e.message}` });
      }
    }
    await chrome.storage.local.set({ [QUEUE_KEY]: left.slice(-QUEUE_MAX), lastPush: failed ? undefined : Date.now() });
    chrome.action.setBadgeText({ text: left.length ? String(left.length) : "" });
  }).catch(() => {});
  return draining;
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg && msg.type === "capture") {
    const { type, ...args } = msg;
    push(args).then(() => reply({ ok: true }));
    return true;
  }
  if (msg && msg.type === "daemon-health") {
    fetch(`${DAEMON}/healthz`).then((r) => r.json())
      .then((d) => reply({ ok: true, version: d.version }))
      .catch((e) => reply({ ok: false, error: e.message }));
    return true;
  }
  if (msg && msg.type === "retry") {
    push(null).then(() => reply({ ok: true }));
    return true;
  }
  return false;
});
