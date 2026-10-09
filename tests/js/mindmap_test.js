// Behaviour tests for scripts/ui/web/mindmap.js (SPEC T48-T49), run with plain Node:
//   node tests/js/mindmap_test.js
// The module's pure parts (tree, outline) need no DOM; a few globals are stubbed so the
// file loads outside a browser.
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.join(__dirname, "..", "..", "scripts", "ui", "web", "mindmap.js"), "utf8");
// Just enough DOM for the summary views: nodes that keep children and text.
function fakeNode(tag) {
  return {
    tag, children: [], textContent: "", className: "", title: "",
    append(...kids) { this.children.push(...kids); },
    replaceChildren(...kids) { this.children = kids; },
    text() { return [this.textContent, ...this.children.map((c) => c.text())].join(" "); },
  };
}
const ctx = {
  getComputedStyle: () => ({ getPropertyValue: () => "", fontFamily: "sans-serif" }),
  document: {
    documentElement: {}, body: {},
    createElement: fakeNode,
    createDocumentFragment: () => fakeNode("#fragment"),
  },
};
vm.createContext(ctx);
vm.runInContext(`${src}\nthis.FlowkeyMindMap = FlowkeyMindMap;`, ctx);
const MM = ctx.FlowkeyMindMap;
// Values built inside the sandbox have the sandbox's Array prototype; compare plain copies.
const eq = (actual, expected) => assert.deepStrictEqual(JSON.parse(JSON.stringify(actual)), expected);

const rec = {
  title: "Delivery sync", category: "planning", date: "2026-10-01T10:00:00", length_s: 2161,
  topics: [
    { start_s: 0, label: "Status", gist: "Where things are", theme: "Work", low_value: false },
    { start_s: 332, label: "Timeline", gist: "End of November", theme: "Work", low_value: false },
    { start_s: 2027, label: "Holidays", gist: "Disney trip", theme: "Chat", low_value: true },
  ],
  themes: [
    { label: "Work", topics: [0, 1], low_value: false },
    { label: "Chat", topics: [2], low_value: true },
  ],
  decisions: [{ text: "Ship by Nov 30", start_s: 332 }],
  actions: [
    { text: "Draft timeline", owner: "Justin", owner_confirmed: true, due: null, start_s: 332 },
    { text: "Send notes", owner: "Joseph", owner_confirmed: false, due: "Fri", start_s: 400 },
    { text: "Take the kids to a corn maze", owner: "AG", owner_confirmed: true, due: null, start_s: 2027 },
  ],
  questions: [],
  people: [
    { label: "AG", share_pct: 61, turns: 90, is_self: false, is_mixed: false },
    { label: "Headphones (other speakers)", share_pct: 0, turns: 1, is_self: false, is_mixed: true },
  ],
};

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

test("tree has a branch per non-empty kind, themes under Topics", () => {
  const tree = MM.buildTree(rec, {});
  assert.strictEqual(tree.label, "Delivery sync");
  eq(tree.children.map((b) => b.label), ["Topics", "Decisions", "Actions", "People"]);
  const topics = tree.children[0];
  eq(topics.children.map((t) => t.label), ["Work"]);      // small talk hidden
  eq(topics.children[0].children.map((t) => t.label), ["0:00  Status", "5:32  Timeline"]);
  assert.strictEqual(tree.hiddenSmallTalk, 1);
});

test("small talk can be shown", () => {
  const tree = MM.buildTree(rec, { showSmallTalk: true });
  eq(tree.children[0].children.map((t) => t.label), ["Work", "Chat"]);
  assert.strictEqual(tree.hiddenSmallTalk, 0);
});

test("unconfirmed owners are marked, confirmed ones are not", () => {
  const actions = MM.buildTree(rec, {}).children.find((b) => b.label === "Actions").children;
  assert.strictEqual(actions[0].label, "Draft timeline → Justin");
  assert.strictEqual(actions[1].label, "Send notes → Joseph?");
  assert.strictEqual(actions[1].muted, true);
});

test("items from a small-talk-only section are hidden with it", () => {
  const hidden = MM.buildTree(rec, {}).children.find((b) => b.label === "Actions").children;
  assert.ok(!hidden.some((a) => a.label.includes("corn maze")));
  const shown = MM.buildTree(rec, { showSmallTalk: true }).children.find((b) => b.label === "Actions").children;
  assert.ok(shown.some((a) => a.label.includes("corn maze")));
});

test("every summary view renders", () => {
  for (const view of MM.VIEWS) {
    for (const showSmallTalk of [false, true]) {
      const box = fakeNode("div");
      MM.renderView(box, rec, view, { showSmallTalk });
      assert.ok(box.text().trim().length > 0, `${view} rendered nothing`);
      assert.strictEqual(box.text().includes("corn maze"), showSmallTalk && view !== "overview" && view !== "topics",
        `${view} small-talk filtering (showSmallTalk=${showSmallTalk})`);
    }
  }
});

test("silent mixed channels are left out of People", () => {
  const people = MM.buildTree(rec, {}).children.find((b) => b.label === "People").children;
  eq(people.map((p) => p.label), ["AG · 61%"]);
});

test("no themes: topics hang directly off the branch", () => {
  const tree = MM.buildTree({ ...rec, themes: [], topics: rec.topics.map((t) => ({ ...t, low_value: false })) }, {});
  assert.strictEqual(tree.children[0].children.length, 3);
  assert.ok(tree.children[0].children[0].label.endsWith("Status"));
});

test("markdown outline", () => {
  const md = MM.outline(rec, {});
  assert.ok(md.startsWith("# Delivery sync\n_planning · 2026-10-01 · 36 min_"));
  assert.ok(md.includes("- **Work**\n  - [0:00] Status — Where things are"));
  assert.ok(!md.includes("Holidays"));                                         // hidden small talk
  assert.ok(md.includes("- Send notes → Joseph (unconfirmed) (due Fri)"));
  assert.ok(!md.includes("corn maze"));
  assert.ok(!md.includes("Headphones"));
  assert.ok(!md.includes("## Open questions"));                                // empty sections omitted
});

test("every summary view exists", () => {
  eq([...MM.VIEWS].sort(), ["actions", "overview", "people", "timeline", "topics"]);
});

if (failures) {
  console.log(`${failures} failed`);
  process.exit(1);
}
