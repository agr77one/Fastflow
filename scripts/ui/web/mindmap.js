// Flowkey meeting mind map + summary views (SPEC T48-T49, V80).
//
// Pure rendering from a stored intel record (ffp_meeting_intel): no network, no model
// calls, no element ids -- app.js passes in the elements and wires the buttons. DOM is
// built with createElement/createElementNS + textContent only (the dashboard CSP
// forbids inline markup), and colours come from the theme's CSS variables so the map
// follows light/dark mode; export bakes the current colours into the SVG.
"use strict";

const FlowkeyMindMap = (() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const ROW = 24;            // px per leaf row
  const COL_GAP = 40;        // px between columns
  const PILL_PAD = 12;
  const LEAF_MAX = 70;       // characters shown on a leaf (full text in the tooltip)
  const BRANCH_VARS = {
    topics: "--accent", decisions: "--ok", actions: "--warn", questions: "--accent-2", people: "--accent-3",
  };

  function mmss(s) {
    if (s === null || s === undefined || s === "") return "";
    const n = Number(s);
    return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`;
  }

  function clip(text, n) {
    const t = String(text || "").trim();
    return t.length <= n ? t : `${t.slice(0, n - 1).trimEnd()}…`;
  }

  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function ownerText(a) {
    if (!a.owner) return "";
    return a.owner_confirmed ? ` → ${a.owner}` : ` → ${a.owner}?`;
  }

  // ---- record -> tree --------------------------------------------------------------

  function visibleTopics(rec, showSmallTalk) {
    return (rec.topics || []).filter((t) => showSmallTalk || !t.low_value);
  }

  function buildTree(rec, opts = {}) {
    const showSmallTalk = !!opts.showSmallTalk;
    const branches = [];
    const topics = visibleTopics(rec, showSmallTalk);
    const leaf = (t) => ({ label: `${mmss(t.start_s)}  ${t.label}`, title: t.gist || t.label });
    if (topics.length) {
      const themes = (rec.themes || []).filter((th) => showSmallTalk || !th.low_value);
      let children;
      if (themes.length) {
        const all = rec.topics || [];
        children = themes.map((th) => ({
          label: th.label,
          kind: "theme",
          muted: th.low_value,
          children: th.topics.map((i) => all[i]).filter(Boolean).map(leaf),
        })).filter((th) => th.children.length);
      } else {
        children = topics.map(leaf);
      }
      branches.push({ label: "Topics", kind: "topics", children });
    }
    const simple = (kind, label, items, fmt) => {
      if (items && items.length) branches.push({ label, kind, children: items.map(fmt) });
    };
    simple("decisions", "Decisions", rec.decisions, (d) => ({ label: d.text, title: d.text }));
    simple("actions", "Actions", rec.actions, (a) => ({
      label: `${a.text}${ownerText(a)}`,
      title: `${a.text}${a.owner ? ` (owner: ${a.owner}${a.owner_confirmed ? "" : " — not confirmed by the transcript"})` : ""}${a.due ? ` · due ${a.due}` : ""}`,
      muted: !!a.owner && !a.owner_confirmed,
    }));
    simple("questions", "Open questions", rec.questions, (q) => ({ label: q.text, title: q.text }));
    simple("people", "People", (rec.people || []).filter((p) => p.share_pct > 0 || !p.is_mixed),
      (p) => ({ label: `${p.label} · ${p.share_pct}%`, title: `${p.turns} turns${p.is_self ? " · you" : ""}` }));
    const hidden = (rec.topics || []).length - topics.length;
    return {
      label: rec.title || rec.meeting_title || "Meeting",
      kind: "root",
      sub: [rec.category, rec.length_s ? `${Math.round(rec.length_s / 60)} min` : ""].filter(Boolean).join(" · "),
      children: branches,
      hiddenSmallTalk: hidden,
    };
  }

  // ---- layout + SVG ----------------------------------------------------------------

  let measureCtx = null;
  function textWidth(text, size, weight) {
    if (!measureCtx) measureCtx = document.createElement("canvas").getContext("2d");
    const family = getComputedStyle(document.body).fontFamily || "sans-serif";
    measureCtx.font = `${weight || 400} ${size}px ${family}`;
    return Math.ceil(measureCtx.measureText(text).width);
  }

  function nodeStyle(node, depth) {
    if (depth === 0) return { size: 14, weight: 700, pill: true };
    if (depth === 1) return { size: 13, weight: 600, pill: true };
    if (node.kind === "theme") return { size: 12.5, weight: 600, pill: false };
    return { size: 12, weight: 400, pill: false };
  }

  function el(name, attrs = {}, text) {
    const node = document.createElementNS(SVG_NS, name);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function layout(tree) {
    // Rows a node occupies: 1 when collapsed or a leaf, else the sum of its children.
    const rows = (n) => (n.collapsed || !n.children || !n.children.length ? 1 : n.children.reduce((s, c) => s + rows(c), 0));
    const colWidth = [];
    const place = (n, depth) => {
      const st = nodeStyle(n, depth);
      const shown = depth >= 2 ? clip(n.label, LEAF_MAX) : n.label;
      n._text = n.collapsed && n.children && n.children.length ? `${shown}  (+${n.children.length})` : shown;
      n._style = st;
      n._w = textWidth(n._text, st.size, st.weight) + (st.pill ? PILL_PAD * 2 : 10);
      colWidth[depth] = Math.max(colWidth[depth] || 0, n._w);
      if (!n.collapsed) (n.children || []).forEach((c) => place(c, depth + 1));
    };
    place(tree, 0);
    let cursor = 0;
    const assignY = (n, depth) => {
      n._depth = depth;
      if (n.collapsed || !n.children || !n.children.length) {
        n._y = cursor * ROW + ROW / 2;
        cursor += 1;
        return;
      }
      n.children.forEach((c, i) => {
        assignY(c, depth + 1);
        if (depth === 0 && i < n.children.length - 1) cursor += 0.6;   // air between branches
      });
      n._y = (n.children[0]._y + n.children[n.children.length - 1]._y) / 2;
    };
    assignY(tree, 0);
    const colX = [16];
    for (let d = 1; d < colWidth.length; d++) colX[d] = colX[d - 1] + colWidth[d - 1] + COL_GAP;
    const width = colX[colX.length - 1] + colWidth[colWidth.length - 1] + 24;
    return { width, height: Math.max(rows(tree), 1) * ROW + (tree.children.length - 1) * 0.6 * ROW + 24, colX };
  }

  function render(svg, tree, onToggle) {
    const { width, height, colX } = layout(tree);
    svg.replaceChildren();
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("width", width);
    svg.setAttribute("height", height);
    const text = cssVar("--text", "#171a21");
    const muted = cssVar("--text-muted", "#677082");
    const surface = cssVar("--surface", "#ffffff");
    const edges = el("g");
    const nodes = el("g");
    svg.append(edges, nodes);
    const offsetY = 12;

    const draw = (n, depth, color) => {
      const x = colX[depth];
      const y = n._y + offsetY;
      const st = n._style;
      const c = depth === 1 ? cssVar(BRANCH_VARS[n.kind] || "--accent", "#5b5bf0") : color;
      const g = el("g", { class: `mm-node mm-d${Math.min(depth, 3)}` });
      if (n.title || n._text !== n.label) g.append(el("title", {}, n.title || n.label));
      if (st.pill) {
        g.append(el("rect", {
          x, y: y - 12, rx: 12, width: n._w, height: 24,
          fill: depth === 0 ? text : c, stroke: "none",
        }));
        g.append(el("text", {
          x: x + n._w / 2, y: y + 4.5, "text-anchor": "middle", "font-size": st.size,
          "font-weight": st.weight, fill: surface,
        }, n._text));
      } else {
        g.append(el("circle", { cx: x + 3, cy: y, r: 3, fill: c }));
        g.append(el("text", {
          x: x + 10, y: y + 4, "font-size": st.size, "font-weight": st.weight,
          fill: n.muted ? muted : depth === 2 && n.kind === "theme" ? c : text,
          "font-style": n.muted && depth >= 2 ? "italic" : "normal",
        }, n._text));
      }
      if (n.children && n.children.length && depth > 0) {
        g.classList.add("mm-toggle");
        g.setAttribute("tabindex", "0");
        g.setAttribute("role", "button");
        g.setAttribute("aria-expanded", String(!n.collapsed));
        const toggle = () => { n.collapsed = !n.collapsed; onToggle(); };
        g.addEventListener("click", toggle);
        g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
      }
      nodes.append(g);
      if (n.collapsed) return;
      for (const child of n.children || []) {
        const x1 = x + n._w;
        const x2 = colX[depth + 1];
        const y2 = child._y + offsetY;
        const mid = (x1 + x2) / 2;
        edges.append(el("path", {
          d: `M${x1},${y} C${mid},${y} ${mid},${y2} ${x2},${y2}`,
          fill: "none", stroke: depth === 0 ? cssVar(BRANCH_VARS[child.kind] || "--accent", c) : c,
          "stroke-width": depth === 0 ? 2 : 1.4, opacity: 0.55,
        }));
        draw(child, depth + 1, depth === 0 ? cssVar(BRANCH_VARS[child.kind] || "--accent", c) : c);
      }
    };
    draw(tree, 0, cssVar("--accent", "#5b5bf0"));
  }

  // A standalone copy: theme colours are already baked into the attributes; add a
  // background and the font so it looks the same outside the dashboard.
  function exportSvg(svg) {
    const copy = svg.cloneNode(true);
    copy.setAttribute("xmlns", SVG_NS);
    copy.setAttribute("font-family", getComputedStyle(document.body).fontFamily || "sans-serif");
    copy.insertBefore(el("rect", { x: 0, y: 0, width: "100%", height: "100%", fill: cssVar("--surface", "#fff") }), copy.firstChild);
    copy.querySelectorAll("[tabindex],[role],[aria-expanded]").forEach((n) => {
      n.removeAttribute("tabindex");
      n.removeAttribute("role");
      n.removeAttribute("aria-expanded");
    });
    return new XMLSerializer().serializeToString(copy);
  }

  // ---- Markdown outline --------------------------------------------------------------

  function outline(rec, opts = {}) {
    const lines = [`# ${rec.title || rec.meeting_title || "Meeting"}`];
    const meta = [rec.category, (rec.date || "").slice(0, 10), rec.length_s ? `${Math.round(rec.length_s / 60)} min` : ""].filter(Boolean);
    if (meta.length) lines.push(`_${meta.join(" · ")}_`);
    const topics = visibleTopics(rec, opts.showSmallTalk);
    if (topics.length) {
      lines.push("", "## Topics");
      const themes = (rec.themes || []).filter((t) => opts.showSmallTalk || !t.low_value);
      const line = (t) => `[${mmss(t.start_s)}] ${t.label}${t.gist ? ` — ${t.gist}` : ""}`;
      if (themes.length) {
        for (const th of themes) {
          lines.push(`- **${th.label}**`);
          for (const i of th.topics) if (rec.topics[i]) lines.push(`  - ${line(rec.topics[i])}`);
        }
      } else {
        topics.forEach((t) => lines.push(`- ${line(t)}`));
      }
    }
    const list = (title, items, fmt) => {
      if (items && items.length) { lines.push("", `## ${title}`); items.forEach((x) => lines.push(`- ${fmt(x)}`)); }
    };
    list("Decisions", rec.decisions, (d) => d.text);
    list("Actions", rec.actions, (a) => `${a.text}${a.owner ? ` → ${a.owner}${a.owner_confirmed ? "" : " (unconfirmed)"}` : ""}${a.due ? ` (due ${a.due})` : ""}`);
    list("Open questions", rec.questions, (q) => q.text);
    list("People", (rec.people || []).filter((p) => !p.is_mixed), (p) => `${p.label} — ${p.share_pct}% of speech`);
    return `${lines.join("\n")}\n`;
  }

  // ---- summary views ("multidimensional summaries") ------------------------------------

  function h(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }

  function ul(items, fmt) {
    const list = h("ul", undefined, "mm-list");
    for (const it of items) {
      const li = h("li");
      fmt(li, it);
      list.append(li);
    }
    return list;
  }

  function empty(text) { return h("p", text, "muted small"); }

  function ownerSpan(a) {
    if (!a.owner) return null;
    const s = h("span", ` → ${a.owner}`, a.owner_confirmed ? "mm-owner" : "mm-owner mm-unconfirmed");
    if (!a.owner_confirmed) s.title = "Owner not confirmed by the transcript";
    return s;
  }

  const VIEWS = {
    overview(rec, opts) {
      const out = document.createDocumentFragment();
      const dl = h("dl", undefined, "mm-dl");
      const row = (k, v) => { dl.append(h("dt", k), h("dd", v)); };
      row("Category", rec.category || "other");
      if (rec.length_s) row("Length", `${Math.round(rec.length_s / 60)} min`);
      row("People", (rec.people || []).filter((p) => !p.is_mixed).map((p) => `${p.label} (${p.share_pct}%)`).join(", ") || "—");
      row("Captured", `${(rec.topics || []).length} topics · ${(rec.decisions || []).length} decisions · ${(rec.actions || []).length} actions · ${(rec.questions || []).length} open questions`);
      out.append(dl);
      const themes = (rec.themes || []).filter((t) => opts.showSmallTalk || !t.low_value);
      if (themes.length) {
        out.append(h("h3", "Themes", "subhead"));
        out.append(ul(themes, (li, th) => {
          li.append(h("strong", th.label), h("span", ` — ${th.topics.length} topic${th.topics.length === 1 ? "" : "s"}`, "muted"));
        }));
      }
      return out;
    },
    topics(rec, opts) {
      const topics = visibleTopics(rec, opts.showSmallTalk);
      if (!topics.length) return empty("No topics captured.");
      const out = document.createDocumentFragment();
      const themes = (rec.themes || []).filter((t) => opts.showSmallTalk || !t.low_value);
      const item = (li, t) => {
        li.append(h("span", mmss(t.start_s), "mm-time"), h("strong", ` ${t.label}`));
        if (t.gist) li.append(h("span", ` — ${t.gist}`, "muted"));
      };
      if (themes.length) {
        for (const th of themes) {
          out.append(h("h3", th.label, "subhead"));
          out.append(ul(th.topics.map((i) => rec.topics[i]).filter(Boolean), item));
        }
      } else {
        out.append(ul(topics, item));
      }
      return out;
    },
    people(rec) {
      const people = (rec.people || []).filter((p) => !p.is_mixed);
      if (!people.length) return empty("No speakers found.");
      const out = document.createDocumentFragment();
      for (const p of people) {
        out.append(h("h3", `${p.label}${p.is_self ? " (you)" : ""} · ${p.share_pct}% of speech · ${p.turns} turns`, "subhead"));
        const mine = (rec.actions || []).filter((a) => a.owner && a.owner.toLowerCase() === p.label.toLowerCase());
        out.append(mine.length ? ul(mine, (li, a) => li.append(h("span", a.text))) : empty("No actions assigned."));
      }
      return out;
    },
    actions(rec) {
      const out = document.createDocumentFragment();
      out.append(h("h3", "Decisions", "subhead"));
      out.append((rec.decisions || []).length
        ? ul(rec.decisions, (li, d) => li.append(h("span", mmss(d.start_s), "mm-time"), h("span", ` ${d.text}`)))
        : empty("No decisions captured."));
      out.append(h("h3", "Actions", "subhead"));
      out.append((rec.actions || []).length
        ? ul(rec.actions, (li, a) => {
          li.append(h("span", mmss(a.start_s), "mm-time"), h("span", ` ${a.text}`));
          const o = ownerSpan(a);
          if (o) li.append(o);
          if (a.due) li.append(h("span", ` · due ${a.due}`, "muted"));
        })
        : empty("No actions captured."));
      out.append(h("h3", "Open questions", "subhead"));
      out.append((rec.questions || []).length
        ? ul(rec.questions, (li, q) => li.append(h("span", mmss(q.start_s), "mm-time"), h("span", ` ${q.text}`)))
        : empty("No open questions captured."));
      return out;
    },
    timeline(rec, opts) {
      const items = [];
      visibleTopics(rec, opts.showSmallTalk).forEach((t) => items.push([t.start_s, "🗂", `${t.label}${t.gist ? ` — ${t.gist}` : ""}`]));
      (rec.decisions || []).forEach((d) => items.push([d.start_s, "✅", d.text]));
      (rec.actions || []).forEach((a) => items.push([a.start_s, "📌", `${a.text}${ownerText(a)}`]));
      (rec.questions || []).forEach((q) => items.push([q.start_s, "❓", q.text]));
      items.sort((a, b) => (a[0] ?? 0) - (b[0] ?? 0));
      if (!items.length) return empty("Nothing captured.");
      return ul(items, (li, [s, icon, text]) => li.append(h("span", mmss(s), "mm-time"), h("span", ` ${icon} ${text}`)));
    },
  };

  function renderView(container, rec, view, opts = {}) {
    container.replaceChildren((VIEWS[view] || VIEWS.overview)(rec, opts));
  }

  return { buildTree, render, exportSvg, outline, renderView, mmss, VIEWS: Object.keys(VIEWS) };
})();
