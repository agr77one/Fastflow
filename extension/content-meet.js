// Flowkey content script for meet.google.com: while you're in a call, reads Meet's live
// captions (each line already carries the speaker's real name), the participant names
// and the meeting title, and hands them to the background worker, which forwards them
// to the local Flowkey daemon. It only READS the page -- except clicking Meet's own
// "Turn on captions" button when auto-captions is enabled.
//
// Meet's class names are obfuscated and change, so blocks are found by shape: the
// smallest element inside the captions region whose text reads "Name\nwords" (and that
// holds the speaker's avatar). "Copy diagnostics" in the popup reports the region's
// structure -- tags, roles, labels, text LENGTHS, never text -- when Meet changes it.
"use strict";

(() => {
  const POLL_MS = 1000;
  const CAPTION_ATTEMPTS = 8;
  const asm = new FlowkeyCaptions.Assembler();
  const blockKeys = new WeakMap();
  let keySeq = 0;
  let session = null;               // {session_id, code, started_at}
  let captionTries = 0;
  const participants = new Set();
  let settings = { autoCaptions: true, enabled: true };

  chrome.storage.local.get({ autoCaptions: true, enabled: true }, (s) => { settings = s; });
  chrome.storage.onChanged.addListener((changes) => {
    for (const [k, v] of Object.entries(changes)) settings[k] = v.newValue;
  });

  const meetingCode = () => (location.pathname.match(/^\/([a-z]{3,4}-[a-z]{4}-[a-z]{3,4})\b/) || [])[1] || "";

  function inCall() {
    return !!document.querySelector('[aria-label*="Leave call" i]');
  }

  function captionsRegion() {
    return document.querySelector('[role="region"][aria-label*="caption" i]')
      || document.querySelector('[aria-label="Captions"]');
  }

  function turnOnCaptions() {
    const btn = document.querySelector('button[aria-label*="Turn on captions" i]');
    if (btn) btn.click();
    return !!btn;
  }

  function meetingTitle() {
    const attr = document.querySelector("[data-meeting-title]");
    if (attr && attr.getAttribute("data-meeting-title")) return attr.getAttribute("data-meeting-title");
    const t = document.title.replace(/^Meet\s*[-–:]\s*/i, "").trim();
    return t && t !== meetingCode() ? t : "";
  }

  function selfName() {
    const el = document.querySelector("[data-self-name]");
    return el ? el.getAttribute("data-self-name") || "" : "";
  }

  function keyFor(el) {
    let k = blockKeys.get(el);
    if (!k) {
      k = `b${++keySeq}`;
      blockKeys.set(el, k);
    }
    return k;
  }

  // Caption blocks: smallest "Name\nwords" elements; prefer ones holding an avatar <img>
  // (a multi-line caption body would otherwise look like "line1\nline2").
  function readBlocks(region) {
    const shaped = [];
    for (const el of region.querySelectorAll("div")) {
      const parsed = FlowkeyCaptions.splitBlockText(el.innerText);
      if (parsed && parsed.text) shaped.push({ el, parsed });
    }
    const withAvatar = shaped.filter((c) => c.el.querySelector("img"));
    const pool = withAvatar.length ? withAvatar : shaped;
    const minimal = pool.filter((c) => !pool.some((o) => o !== c && c.el.contains(o.el)));
    return minimal.map((c) => ({ key: keyFor(c.el), speaker: c.parsed.speaker, text: c.parsed.text }));
  }

  function readParticipants() {
    for (const el of document.querySelectorAll("[data-participant-id]")) {
      const name = (el.innerText || "").split("\n").map((s) => s.trim()).find((s) => s && s.length <= 60);
      if (name && !/^(you|presentation|\(you\))$/i.test(name)) participants.add(name);
    }
    for (const el of document.querySelectorAll('[role="list"][aria-label*="articipant" i] [role="listitem"][aria-label]')) {
      participants.add(el.getAttribute("aria-label"));
    }
    for (const s of asm.speakers()) if (s && s.toLowerCase() !== "you") participants.add(s);
  }

  function send(msg) {
    try {
      chrome.runtime.sendMessage(msg, () => void chrome.runtime.lastError);
    } catch {
      /* extension reloaded: the page keeps working, this tab just stops capturing */
    }
  }

  function payload(extra = {}) {
    return {
      type: "capture", session_id: session.session_id, platform: "google_meet",
      code: session.code, title: meetingTitle(), url: location.origin + location.pathname,
      started_at: session.started_at, self_name: selfName(), participants: [...participants], ...extra,
    };
  }

  function start() {
    const now = new Date();
    const stamp = now.toISOString().replace(/[-:T]/g, "").slice(0, 14);
    session = { session_id: `meet-${stamp}-${meetingCode() || "call"}`, code: meetingCode(), started_at: now.getTime() };
    captionTries = 0;
    send(payload({ segments: [] }));
  }

  function stop() {
    const segments = asm.flush(true);
    readParticipants();
    send(payload({ segments, ended: true }));
    session = null;
  }

  function tick() {
    if (!settings.enabled) return;
    const live = inCall();
    if (live && !session) start();
    if (!live && session) { stop(); return; }
    if (!session) return;
    const region = captionsRegion();
    if (!region) {
      if (settings.autoCaptions && captionTries < CAPTION_ATTEMPTS) {
        captionTries += 1;
        turnOnCaptions();
      }
      return;
    }
    asm.observe(readBlocks(region));
    readParticipants();
    const segments = asm.flush();
    if (segments.length) send(payload({ segments }));
  }

  // Structure only -- never caption text -- so it is safe to paste into a bug report.
  function diagnostics() {
    const region = captionsRegion();
    const describe = (el, depth) => {
      if (depth > 6) return [];
      const attrs = ["role", "aria-label", "jsname", "data-participant-id"]
        .filter((a) => el.hasAttribute(a)).map((a) => `${a}=${a === "aria-label" ? el.getAttribute(a).slice(0, 30) : "…"}`);
      const cls = (el.className && typeof el.className === "string") ? el.className.split(/\s+/).slice(0, 2).join(".") : "";
      const line = `${"  ".repeat(depth)}<${el.tagName.toLowerCase()}${cls ? "." + cls : ""} ${attrs.join(" ")}> text:${(el.innerText || "").length} img:${el.querySelectorAll("img").length}`;
      return [line, ...[...el.children].slice(0, 8).flatMap((c) => describe(c, depth + 1))];
    };
    return [
      `url-code: ${meetingCode() || "(none)"}  in-call: ${inCall()}  captions-region: ${!!region}`,
      `blocks read: ${region ? readBlocks(region).length : 0}  speakers so far: ${asm.speakers().length}  participants: ${participants.size}`,
      ...(region ? describe(region, 0) : ["(captions region not found — are captions on?)"]),
    ].join("\n");
  }

  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (msg && msg.type === "status") {
      reply({ inCall: !!session, code: meetingCode(), title: meetingTitle(), captions: !!captionsRegion(),
              segments: asm.order.length, speakers: asm.speakers(), participants: [...participants] });
    } else if (msg && msg.type === "diagnostics") {
      reply({ text: diagnostics() });
    }
  });

  window.addEventListener("pagehide", () => { if (session) stop(); });
  setInterval(tick, POLL_MS);
})();
