// Flowkey: turns Google Meet's live-caption DOM into finished, speaker-named segments.
//
// Pure logic, no DOM access: content-meet.js reads the page and hands this module plain
// {key, speaker, text} snapshots; tests/js/captions_test.js drives it with fakes.
//
// How Meet behaves (and why this is shaped the way it is):
// - each speaker turn is one caption block whose text Meet *edits in place* as
//   recognition firms up ("we will ship" -> "We'll ship Friday."), so a block is only
//   final once it stops changing or disappears;
// - a long turn scrolls: older words drop off the front of the block while new ones
//   arrive, so the visible text is a sliding window over what was said;
// - a block's speaker line and text line arrive together as "Name\nwords...".
"use strict";

const FlowkeyCaptions = (() => {
  const SETTLE_MS = 4000;          // unchanged this long -> safe to send
  const MIN_OVERLAP = 12;          // chars a scrolled window must share to be stitched
  const NAME_MAX = 60;

  // "Dana Smith\nWe'll ship it Friday." -> {speaker, text}. The first short line without
  // sentence punctuation is the name; everything after it is the caption.
  function splitBlockText(raw) {
    const lines = String(raw || "").split(/\n+/).map((l) => l.trim()).filter(Boolean);
    if (lines.length < 2) return null;
    const first = lines[0];
    if (first.length > NAME_MAX || /[.!?]$/.test(first)) return null;
    return { speaker: first, text: lines.slice(1).join(" ") };
  }

  // Combine what we had with what is visible now. Cases, in order:
  //   grew:      "We will"            -> "We will ship"           => new
  //   revised:   "we will ship"       -> "We'll ship Friday."     => new (same start region)
  //   scrolled:  "a b c d e f g h"    -> "e f g h i j"            => stitch on the overlap
  //   shrank:    "a b c d e f"        -> "c d e f"                => keep old (front dropped)
  function merge(oldText, newText) {
    const a = String(oldText || "");
    const b = String(newText || "");
    if (!a) return b;
    if (!b || a === b) return a;
    if (b.startsWith(a) || b.length >= a.length && b.slice(0, 8).toLowerCase() === a.slice(0, 8).toLowerCase()) return b;
    if (a.endsWith(b) || a.includes(b)) return a;
    // Scrolled window: the longest suffix of `a` that is a prefix of `b`.
    const max = Math.min(a.length, b.length);
    for (let k = max; k >= MIN_OVERLAP; k--) {
      if (a.endsWith(b.slice(0, k))) return a + b.slice(k);
    }
    // A revision near the end of a scrolled window: anchor on b's opening words.
    const anchor = b.slice(0, MIN_OVERLAP * 2);
    const at = a.lastIndexOf(anchor);
    if (at >= 0) return a.slice(0, at) + b;
    return b.length >= a.length * 0.6 ? b : a + " " + b;
  }

  class Assembler {
    constructor({ settleMs = SETTLE_MS, now = () => Date.now(), idPrefix = "s" } = {}) {
      this.settleMs = settleMs;
      this.now = now;
      this.idPrefix = idPrefix;
      this.seq = 0;
      this.byKey = new Map();      // block key -> segment
      this.order = [];             // all segments, in start order
    }

    // `blocks`: what is visible right now, [{key, speaker, text}], key stable per block.
    observe(blocks) {
      const t = this.now();
      const seen = new Set();
      for (const b of blocks || []) {
        if (!b || !b.key || !b.text) continue;
        seen.add(b.key);
        let seg = this.byKey.get(b.key);
        if (!seg) {
          seg = { id: `${this.idPrefix}${++this.seq}`, speaker: b.speaker || "", text: "",
                  t0: t, t1: t, changedAt: t, sentText: null, gone: false };
          this.byKey.set(b.key, seg);
          this.order.push(seg);
        }
        if (b.speaker && !seg.speaker) seg.speaker = b.speaker;
        const merged = merge(seg.text, b.text);
        if (merged !== seg.text) {
          seg.text = merged;
          seg.t1 = t;
          seg.changedAt = t;
        }
      }
      for (const [key, seg] of this.byKey) {
        if (!seen.has(key) && !seg.gone) seg.gone = true;   // scrolled away = final
      }
    }

    // Segments ready to send: settled or gone, and changed since last sent. The same
    // id is re-sent when Meet revises a block later; the receiver upserts by id.
    flush(force = false) {
      const t = this.now();
      const out = [];
      for (const seg of this.order) {
        const ready = force || seg.gone || t - seg.changedAt >= this.settleMs;
        if (ready && seg.text && seg.text !== seg.sentText) {
          seg.sentText = seg.text;
          out.push({ id: seg.id, speaker: seg.speaker, text: seg.text, t0: seg.t0, t1: seg.t1 });
        }
      }
      return out;
    }

    speakers() {
      return [...new Set(this.order.map((s) => s.speaker).filter(Boolean))];
    }
  }

  return { Assembler, merge, splitBlockText };
})();

if (typeof module !== "undefined") module.exports = FlowkeyCaptions;
