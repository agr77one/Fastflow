// Behaviour tests for extension/captions.js: node tests/js/captions_test.js
"use strict";

const assert = require("assert");
const path = require("path");
const C = require(path.join(__dirname, "..", "..", "extension", "captions.js"));

let failures = 0;
function test(name, fn) {
  try {
    fn();
    console.log(`ok   ${name}`);
  } catch (e) {
    failures += 1;
    console.log(`FAIL ${name}\n     ${e.message}`);
  }
}

test("block text splits into speaker and caption", () => {
  assert.deepStrictEqual(C.splitBlockText("Dana Smith\nWe'll ship it Friday."), { speaker: "Dana Smith", text: "We'll ship it Friday." });
  assert.deepStrictEqual(C.splitBlockText("You\nok\nsounds good"), { speaker: "You", text: "ok sounds good" });
  assert.strictEqual(C.splitBlockText("just one line"), null);
  assert.strictEqual(C.splitBlockText("This is a sentence.\nmore"), null);       // not a name
});

test("merge: growth, revision, scroll, shrink", () => {
  assert.strictEqual(C.merge("", "We will"), "We will");
  assert.strictEqual(C.merge("We will", "We will ship"), "We will ship");
  assert.strictEqual(C.merge("we will ship", "We will ship Friday."), "We will ship Friday.");
  const a = "the quick brown fox jumps over the lazy dog and";
  const b = "over the lazy dog and runs into the woods";
  assert.strictEqual(C.merge(a, b), "the quick brown fox jumps over the lazy dog and runs into the woods");
  assert.strictEqual(C.merge("alpha beta gamma delta", "gamma delta"), "alpha beta gamma delta");
});

function clock(start = 1000) {
  let t = start;
  return { now: () => t, tick: (ms) => { t += ms; } };
}

test("a block is sent once it settles, and re-sent with the same id when revised", () => {
  const c = clock();
  const asm = new C.Assembler({ now: c.now, settleMs: 4000 });
  asm.observe([{ key: "k1", speaker: "Dana", text: "We will" }]);
  assert.deepStrictEqual(asm.flush(), []);                                    // still changing
  c.tick(1000);
  asm.observe([{ key: "k1", speaker: "Dana", text: "We will ship Friday." }]);
  c.tick(4500);
  asm.observe([{ key: "k1", speaker: "Dana", text: "We will ship Friday." }]);
  const first = asm.flush();
  assert.strictEqual(first.length, 1);
  assert.strictEqual(first[0].text, "We will ship Friday.");
  assert.strictEqual(first[0].speaker, "Dana");
  assert.deepStrictEqual(asm.flush(), []);                                    // nothing new
  asm.observe([{ key: "k1", speaker: "Dana", text: "We will ship on Friday." }]);
  c.tick(5000);
  const again = asm.flush();
  assert.strictEqual(again.length, 1);
  assert.strictEqual(again[0].id, first[0].id);                               // upsert, not duplicate
});

test("a block that scrolls away is final immediately", () => {
  const c = clock();
  const asm = new C.Assembler({ now: c.now });
  asm.observe([{ key: "k1", speaker: "Dana", text: "first point" }]);
  asm.observe([{ key: "k2", speaker: "Lee", text: "reply" }]);                // k1 gone
  const out = asm.flush();
  assert.deepStrictEqual(out.map((s) => s.speaker), ["Dana"]);
  assert.deepStrictEqual(asm.speakers(), ["Dana", "Lee"]);
});

test("a long scrolling turn is stitched into one segment", () => {
  const c = clock();
  const asm = new C.Assembler({ now: c.now });
  asm.observe([{ key: "k1", speaker: "Dana", text: "so the first thing we need is a plan for the rollout" }]);
  c.tick(800);
  asm.observe([{ key: "k1", speaker: "Dana", text: "a plan for the rollout and then a date for the pilot" }]);
  const out = asm.flush(true);
  assert.strictEqual(out.length, 1);
  assert.strictEqual(out[0].text, "so the first thing we need is a plan for the rollout and then a date for the pilot");
});

test("force flush sends everything left at the end of the call", () => {
  const c = clock();
  const asm = new C.Assembler({ now: c.now });
  asm.observe([{ key: "a", speaker: "A", text: "one" }, { key: "b", speaker: "B", text: "two" }]);
  assert.strictEqual(asm.flush(true).length, 2);
});

if (failures) {
  console.log(`${failures} failed`);
  process.exit(1);
}
