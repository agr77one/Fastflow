# FastFlow Prompt Logic Refinement Plan (rev 2)

Date: 2026-07-09
Target release: **2.2.0** shipped; **2.2.x** adapter follow-up planned
Status: 2.2.0 released (`v2.2.0`); this document now tracks the next prompt-adapter refinement pass

## Implementation status (2026-07-09)

Implemented in this pass:

- Backend prompt-builder module: `scripts/ffp_prompt_builder.py`
  (`PromptBuilderSettings`, `PromptIntent`, `PromptRenderResult`, per-adapter
  deterministic renderers, validators, repair, preview).
- Config: `prompt_builder` defaults, load normalization, patch whitelist,
  suffix clamp, config snapshot inclusion, shipped config/example/default seed
  updates. Built-in `modes.prompt.system_prompt` remains locked.
- Runtime: prompt mode now uses the builder system prompt; default settings keep
  `CLAUDE_PROMPT_SYSTEM_PROMPT` byte-identical. Non-default settings get
  target-aware structure checks, retry prompts, deterministic repair, and
  fallback. `generic_chat` is the first non-Claude shipped adapter.
- Dashboard: Config tab Prompt builder card with target, action, detail,
  structure, acceptance/verification/output toggles, preserve-constraints flag,
  capped suffix, and deterministic no-LLM preview.
- Daemon/API: `prompt_builder_preview {settings?, sample?}` action.
- Eval: `tools/prompt_builder_eval.py` model-free and live-FLM adapter/action
  contract gate for default Claude and shipped generic-chat cases.
- Tests/manifests: new prompt-builder tests, config/runtime/daemon test updates,
  `pyproject.toml` and PyInstaller hidden import updated.
- SPEC/CHANGELOG: `SPEC.md` records `prompt_builder`, the preview action, and
  invariant `V24`; `CHANGELOG.md` records the Unreleased feature.

Verified:

- `python -m py_compile scripts\ffp_prompt_builder.py scripts\ffp_config.py scripts\ffp_llm_client.py scripts\grammar_fix.py scripts\ffp_daemon.py`
- `python -m pytest tests -q` → 345 passed
- JSON parse check for `config/grammar_hotkey.config*.json` and
  `setup/defaults/grammar_hotkey.config*.json`
- `python tools\prompt_builder_eval.py` → all 12 cases valid
- `python tools\prompt_builder_eval.py --live --base-url http://127.0.0.1:52625 --model qwen3.5:4b --bearer flm --json --out data\benchmarks\prompt_builder_eval_flm_20260709.json`
  → `claude_code` 2/2, `generic_chat` 10/10

Follow-up after 2.2.0:

- Codex/Cursor adapters remain 2.2.x follow-up, as planned below.
- Add clearer dashboard help text explaining when to use Claude Code, Codex,
  Cursor, or generic chat output.
- Decide whether extra dashboard knobs are worth the UI complexity after the
  Codex/Cursor eval results are available.

## Revision note (what changed from rev 1, after code review)

Rev 1 was directionally right but had two issues that would bite:

1. **Its proposed defaults did not reproduce current behavior.** Today's prompt
   mode (`CLAUDE_PROMPT_SYSTEM_PROMPT`) emits only `<task>/<context>/
   <constraints>/<output_format>` — no verification, no acceptance criteria.
   Rev 1 defaulted `include_verification` and `include_acceptance_criteria` to
   true, which would silently add sections to every existing user's prompts on
   upgrade and re-invalidate the benchmark numbers. **Fixed:** default settings
   now render byte-identical to 2.1.x (see "Drop-in guarantee").
2. **Generator-capability risk was unquantified.** The local model (FLM
   `qwen3.5:4b`) is the *generator*; `target_agent` only controls the *format of
   what it produces*. The 2026-07 benchmark showed the single XML shape scores
   only ~45–49/50 on the best local models and needs deterministic repair.
   Asking the same 4B model to conditionally hit 4 formats × 5 action modes × 3
   detail levels is strictly harder. **Fixed:** staged adapter rollout
   (claude_code + generic first), realistic per-adapter gates, defined
   gate-fail fallback.

Also resolved rev-1 open questions and defined machine-checkable per-adapter
contracts up front. Full detail below.

## Goal

A prompt-builder layer that turns rough user text into reliable prompts for
coding agents and chat tools, while keeping FastFlow's safety model: built-in
prompts stay protected, user customization is explicit, bounded, and stored as
settings. **FastFlow's prompt mode generates a copy-paste-ready prompt** that the
user pastes into their coding agent; the local model is the *generator*, and
`target_agent` selects the output *format*.

## Current State (verified against code, 2026-07-09)

- Built-in prompt mode is Claude-specific: `ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT`
  steers the generator to emit `<task>`, `<context>`, `<constraints>`,
  `<output_format>` (task = one sentence; constraints testable; no invented
  requirements).
- Built-in mode prompts are locked: `_enforce_builtin_mode_prompts()` restores
  code defaults on load; `filter_config_patch()` rejects edits to any mode in
  `BUILTIN_MODE_IDS = {grammar, prompt, summarize, explain, tone}`.
- Runtime lives in `scripts/ffp_llm_client.py`: prompt mode gets larger output
  budgets, anti-echo retries, and three XML-hardcoded helpers —
  `has_prompt_structure()` (keys off `<task>` etc.), `is_weak_prompt_echo()`
  (early-returns "not echo" *because XML is present* — line 168), and
  deterministic `force_prompt_shape()` (renders Claude XML).
- A shipped repair layer (`ffp_llm_client` + `tests/test_prompt_output_repair.py`)
  fixes Claude-XML near-misses (label-to-XML, malformed `<context>`).
- The benchmark contract checker `tools/provider_bench.check_prompt_contract()`
  is XML-only and is *also* the provider-quality gate — its numbers assume XML.
- Dashboard customization exists only for non-built-in custom modes (`prefix:`
  commands). No tuning of built-in prompt style, target agent, detail, or action.

## Product Decision

Do not replace prompt mode with one universal static prompt. Add a small
prompt-builder engine:

1. Resolve intent from **settings + lightweight code heuristics** (never an LLM
   classification call — see "Intent resolution").
2. Apply safe, bounded user settings.
3. Select an agent adapter.
4. Produce (a) a generator **system prompt** and (b) a deterministic **fallback
   renderer** for that adapter.
5. Validate the model output against the adapter's contract; repair or fall back.

The internal structure is semantic, not literal:

```text
Task / Outcome · Context · Constraints · Acceptance criteria · Verification · Output expectations
```

Adapters render that structure differently per target. `universal` is an
internal model only — not a user-facing target until adapters are proven.

## Drop-in guarantee (non-regression — the hard gate for 2.2.0)

**With default settings, the builder returns `CLAUDE_PROMPT_SYSTEM_PROMPT`
verbatim (identity path).** Synthesis only happens when settings diverge from
default. This makes the drop-in ironclad and the regression test trivial:

- `test_prompt_builder_default_is_identity`: default `PromptBuilderSettings` →
  generator system prompt `== ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT`, and the
  claude_code deterministic fallback `== force_prompt_shape()` current output.
- Re-run the existing prompt benchmark on the default path; pass rate must not
  drop vs 2.1.1. **2.2.0 does not ship if this regresses.**

Consequently the drop-in defaults are:

```json
{
  "prompt_builder": {
    "target_agent": "claude_code",
    "detail_level": "balanced",
    "action_mode": "implement",
    "structure": "agent_default",
    "include_acceptance_criteria": false,
    "include_verification": false,
    "include_output_format": true,
    "preserve_user_constraints": true,
    "allow_user_suffix": true,
    "user_suffix": ""
  }
}
```

`include_verification`/`include_acceptance_criteria` default **false** to match
2.1.x. `include_output_format` defaults **true** because the current prompt has
`<output_format>`. Turning verification/acceptance on is an explicit, opt-in
richer prompt (and re-baselines quality for that config only).

## Staged rollout (adapters)

Because each new target format multiplies the local generator's failure surface:

- **2.2.0 ships:** `claude_code` (identity default) + `generic_chat`. Full
  `action_mode`, `detail_level`, toggles, capped `user_suffix`, deterministic
  preview, dashboard card.
- **2.2.x follow-up (after measurement):** `codex` + `cursor` adapters, each
  gated on clearing its own contract eval *with its repair path* before ship.
- `universal` stays internal.

Log clearly in the UI which targets are available; do not expose codex/cursor
selectors until their adapters land.

## Agent Differences (unchanged from rev 1 — still accurate)

Claude Code: explicit direct instructions; for tool use, say the action ("edit",
not "suggest"); emphasize outcome, verification, reference files, measurable
targets, final-answer format; XML sections are a good default but must not force
response-format constraints when the user wants code changes.

Codex: `AGENTS.md` holds persistent repo guidance; per-task prompts state
objective + constraints + files/patterns + verification commands; Markdown is
enough (no XML); don't duplicate long-standing rules.

Cursor: User/Team/Project/folder rules + AGENTS.md; reference target files/
symbols/rules; keep the prompt concise and implementation-oriented (editor
already has context).

Generic chat: plain Markdown; more context (no persistent repo rules); for code
tasks give a verification expectation rather than "ask clarifying questions".

Sources: OpenAI Codex AGENTS.md (developers.openai.com/codex/guides/agents-md);
Anthropic prompting best practices
(platform.claude.com/docs/en/build-with-claude/prompt-engineering);
Claude Code prompt library (code.claude.com/docs/en/prompt-library);
Cursor rules (cursor.com/docs/rules).

## 2.2.x refinement decision

Use one internal semantic prompt model, but keep different output adapters. A
single universal prompt shape is useful as a fallback, but it should not be the
primary output for every agent because the agents receive context differently:

- Claude Code: good fit for explicit XML-like sections. Keep the existing
  `<task>/<context>/<constraints>/<output_format>` default for non-regression.
- Codex: good fit for compact Markdown or label sections. Prefer objective,
  constraints, likely files/patterns, verification commands, and final-response
  expectations. Do not duplicate repo-wide rules that belong in `AGENTS.md`.
- Cursor: good fit for shorter editor-context prompts. Prefer direct action,
  focus files/areas, project-rule references, and concise done criteria.
- Generic chat: include more background because it may not have repo memory,
  project rules, or editor context.

Universal semantic core:

```text
Task / Outcome
Context / Source request
Target files or areas
Constraints / Safety rules
Acceptance criteria
Verification
Final response expectations
```

Adapter renderers decide how much of that core to emit and what syntax to use.
The dashboard can expose this as user settings, but it must keep raw built-in
system-prompt editing locked.

## 2.2.x implementation plan

### Phase A — Adapter contracts first

Define the contract before exposing a target in the dashboard:

- `claude_code`: existing XML contract remains the default identity path.
- `generic_chat`: existing Markdown contract remains shipped.
- `codex`: Markdown/label contract requiring `Task:`, `Context:`,
  `Constraints:`, optional `Done when:`, optional `Verification:`, and optional
  `Final response:`. No XML required by default.
- `cursor`: concise contract requiring an imperative first line, `Focus
  files/areas:` or an explicit project-rule reference, optional `Done when:`,
  and a length cap for concise/balanced modes.

Each contract must reject near-verbatim echoes, code fences around the whole
prompt, `<think>` residue, and invented hard limits not present in user input.

### Phase B — Backend changes

- Expand `TARGET_AGENTS` to include `codex` and `cursor` only after their eval
  cases exist.
- Split `effective_structure()` from adapter behavior. Shape (`xml`,
  `markdown`, `checklist`) is not enough; Codex/Cursor need different section
  labels and required fields even when both use Markdown.
- Add adapter-specific target instructions, fallback renderers, validators, and
  label repair paths.
- Keep `claude_code` default byte-identical to `CLAUDE_PROMPT_SYSTEM_PROMPT`.
- Keep `target_agent` orthogonal to LLM provider. FastFlowLM/Ollama generate the
  prompt; Claude/Codex/Cursor describe where the generated prompt will be used.

### Phase C — Dashboard settings

Keep the current safe controls:

- target agent
- action mode: plan / implement / review / debug / explain
- detail level: concise / balanced / detailed
- structure: agent default / Markdown / XML / checklist
- acceptance criteria, verification, output expectations
- preserve user constraints
- capped user suffix
- deterministic preview

Add only high-value controls:

- per-target help text: explain why Claude/Codex/Cursor outputs differ
- optional "verification strictness": off / suggest / require
- optional "context density": minimal / normal / include files-and-risks
- optional saved presets, e.g. "My Codex review prompt" or "Cursor quick fix"

Do not add raw system-prompt editing for built-in `prompt:` mode. Use custom
modes as the full-custom escape hatch.

### Phase D — Eval gates

Extend `tools/prompt_builder_eval.py` before enabling UI options:

- Add at least 10 Codex cases across plan/implement/review/debug/explain.
- Add at least 10 Cursor cases with short editor-context inputs.
- Run deterministic model-free eval: every fallback must pass its own contract.
- Run live local eval on the current preferred short-task model.
- Ship each new adapter only if it reaches at least 8/10 live valid outputs
  with repair enabled and no default Claude regression.

### Phase E — Docs and rollout

- README: list supported targets and explain provider vs target-agent
  separation.
- SPEC: add invariants for Codex/Cursor target-aware validation and locked raw
  system prompt.
- CHANGELOG: add an Unreleased item for the adapters and dashboard help text.
- Keep Codex/Cursor hidden until their eval JSON is committed or attached to the
  release evidence.

## Recommended user-facing copy

Dashboard help text:

```text
Target agent controls where the generated prompt will be pasted. It does not
change the local model used to generate it. Claude Code uses XML-style sections,
Codex prefers compact task/context/verification instructions and repo rules from
AGENTS.md, Cursor prefers shorter prompts that reference editor context and
project rules, and Generic chat includes more standalone context.
```

Universal fallback note:

```text
Most coding agents understand Task / Context / Constraints / Done when /
Verification / Final response. Agent default is usually better because each tool
already gets different persistent context.
```

## 2.2.x plan — review refinements (2026-07-09)

The Phase A–E plan above is sound and follows the staged-rollout discipline that
made `generic_chat` succeed. Four specifics to pin before building, so the plan
is build-ready:

**1. Version & sequencing.** Ship one adapter per release, each behind its own
eval-gated PR — do NOT bundle codex + cursor. Suggested: **`codex` in 2.2.2**
(closest to the proven generic_chat Markdown path), **`cursor` in 2.2.3**.
Each stays hidden from the dashboard `target_agent` selector until its live-eval
JSON is committed (Phase D). `universal` stays internal.

**2. Machine-checkable contracts (pin the exact predicates, like generic_chat).**
A prose contract isn't testable. Define per adapter, in `ffp_prompt_builder`:

- `codex` valid = contains `Task:` AND `Context:` AND `Constraints:`; `Done when:`
  required iff `include_acceptance_criteria`; `Verification:` required iff
  `include_verification`; `Final response:` required iff `include_output_format`;
  no XML tags; not a Markdown-fenced whole; not a near-verbatim echo; no
  `<think>` residue.
- `cursor` valid = first non-empty line is imperative (starts with a verb, not a
  heading); contains `Focus files/areas:` OR an explicit "project rules"/rule
  reference; `Done when:` required iff acceptance; visible length ≤ **1200 chars**
  for `concise`/`balanced` (no cap for `detailed`); not an echo.

**3. Gate-fail path is per-adapter, never silent.** On contract failure:
model output → `repair_output(target)` → re-validate → if still invalid,
`render_fallback(target)` (the adapter's own deterministic shape), which by
construction passes its contract (assert this in `test_fallback_shape_passes_its_own_contract`,
extended to codex/cursor). The claude_code identity prompt remains the ultimate
safe default only for the claude_code target — do not cross-fall-back codex →
claude_code (that would paste XML into a Codex prompt).

**4. Reuse the existing dispatch, don't fork it.** `effective_structure()`,
`validate()`, `render_fallback()`, `repair_output()`, and `has_target_structure()`
already dispatch on `target_agent`; add codex/cursor branches inside them rather
than new parallel code paths. Phase B's "split shape from adapter behavior" =
give each `target_agent` its own required-section set even when two share
`markdown` shape.

**Effort:** each adapter is ~1 focused PR (contract + renderer + validator +
repair + ~10 eval cases + dashboard unhide + docs). Gate to ship: ≥ 8/10 live on
the preferred model with repair, and zero claude_code default regression.

## Settings

Add a `prompt_builder` config block (defaults above). Allowed values:

| Setting | Values | Purpose |
|---|---|---|
| `target_agent` | `claude_code`, `generic_chat` (2.2.0); `codex`, `cursor` (2.2.x) | Rendering adapter / output format |
| `detail_level` | `concise`, `balanced`, `detailed` | How much context/acceptance detail is generated |
| `action_mode` | `plan`, `implement`, `review`, `debug`, `explain` | Prevents "review" prompts requesting edits, and vice versa |
| `structure` | `agent_default`, `markdown`, `xml`, `checklist` | Override shape without editing the locked system prompt |
| `include_acceptance_criteria` | boolean (default false) | Adds measurable done conditions |
| `include_verification` | boolean (default false) | Adds test/build/run expectation |
| `include_output_format` | boolean (default true) | Adds final response/report expectations |
| `user_suffix` | string, capped ≤ 500 chars | Safe power-user style preferences |

**`structure` precedence:** `agent_default` → use the adapter's native shape
(claude_code→xml, generic_chat→markdown). An explicit non-default `structure`
overrides the adapter shape; the UI must warn that overriding the native shape
can lower quality on local models (e.g. `target_agent=codex` + `structure=xml`
forces XML the Codex adapter would not otherwise request).

Never allow arbitrary edits to the built-in prompt-mode system prompt through
dashboard settings. Custom modes remain the escape hatch for fully custom prompts.

## Intent resolution (code, NOT an LLM call)

`action_mode` and `target_agent` come from settings. Optional lightweight,
in-process keyword heuristics may *auto-suggest* an action mode (e.g. leading
"review"/"audit" → `review`; "plan"/"design" → `plan`) — deterministic, no model
round-trip, so prompt-mode latency is unchanged. There is **no second LLM call**
for classification (that would double NPU prefill cost and add a failure point).

## Per-adapter output contracts (machine-checkable — define before building)

These are the exact validators; "N/10 valid" is meaningless without them.

- **claude_code** (= existing `check_prompt_contract`): `<task>`, `<context>`,
  `<constraints>`, `<output_format>` present, in order, non-empty; no `<think>`
  residue; not wrapped in a Markdown fence. If `include_verification` on, a
  `<verification>` section is also required.
- **generic_chat**: Markdown headings `## Task`, `## Context`, `## Constraints`
  present; `## Output`/`## Acceptance criteria`/`## Verification` required only
  when their toggle is on; not a near-verbatim echo of the input.
- **codex** (2.2.x): `Task:`, `Context:`, `Constraints:` present; `Done when:`
  required iff acceptance on; `Final response:` required iff output_format on.
- **cursor** (2.2.x): an imperative action line; a `Focus files/areas:` or
  explicit project-rule reference; total length under a concise cap; not an echo.

All adapters additionally reuse the shared anti-echo check.

## Implementation Plan

### Phase 1 — Internal model (`scripts/ffp_prompt_builder.py`)

- `PromptBuilderSettings`, `PromptIntent`, `PromptRenderResult` dataclasses.
- `build_system_prompt(settings, intent) -> str` — **identity for default
  claude_code**; synthesized otherwise.
- `render_fallback(settings, intent, cleaned_input) -> str` — per-adapter
  deterministic shape (generalizes today's `force_prompt_shape`).
- `contract_for(target_agent) -> validator` and `validate(text, settings)`.
- Keep user-provided constraints separate from generated constraints
  (`preserve_user_constraints`); clamp `user_suffix`.
- Pure/stdlib; no network; unit-testable without a model.

### Phase 2 — Config (`scripts/ffp_config.py`)

- Add `prompt_builder` defaults (above).
- `_PATCH_PROMPT_BUILDER_KEYS` + enum validation + `user_suffix` clamp, wired
  into `filter_config_patch()`.
- Include in `build_config_snapshot()`.
- Keep `modes.prompt.system_prompt` locked (unchanged).

Tests: defaults present; invalid enums rejected/normalized; built-in prompt still
unpatchable; `user_suffix` length-capped; **default → identity** (drop-in gate).

### Phase 3 — Runtime integration (`scripts/ffp_llm_client.py`)

- Prompt mode pulls the generator system prompt from the builder (identity for
  default config).
- `has_prompt_structure()` → `has_target_structure(text, target_agent)`.
- `is_weak_prompt_echo()` uses `has_target_structure` (so a valid Markdown
  conversion for generic_chat is not misread as an echo — today's line-168 XML
  shortcut would false-positive on non-XML adapters).
- `force_prompt_shape()` and the **shipped repair layer** both become
  adapter-parameterized (repair is currently Claude-XML-only).
- Anti-echo preserved across adapters.
- **Gate-fail behavior:** model output fails contract → deterministic repair →
  still failing → fall back to the claude_code system prompt (safe default) and
  log; never emit an unvalidated shape.

Tests: claude_code still emits/validates XML; generic_chat validates Markdown and
does not fail for lack of XML; echo/reformat still triggers rescue on every
adapter; gate-fail falls back cleanly.

### Phase 4 — Dashboard settings (`index.html` + `app.js`)

- "Prompt builder" card in Config: target agent (only shipped targets), detail
  level, action mode, structure, the three include toggles, capped `user_suffix`
  textarea.
- **Deterministic preview** button: renders a local sample through
  `ffp_prompt_builder` with no LLM call.
- Guardrails: note custom modes remain for fully custom prompts; these are
  presets, not raw system-prompt edits; warn when `structure` overrides the
  adapter default; safe defaults preselected (claude_code / balanced / implement
  / agent_default / verification off).
- DOM via createElement/textContent; no native alert/confirm/prompt (V5).

### Phase 5 — Eval and quality gates (`tools/prompt_builder_eval.py` — NEW, not provider_bench)

Keep `provider_bench.py` unchanged (its needle-safe longctx + XML contract just
stabilized and its numbers assume XML). New eval reuses the contract primitives.

Inputs: review request, implement request, plan-only request, debug request,
generic-chat request (codex/cursor inputs added with those adapters).

Score per adapter contract: required sections present, action mode preserved
(review ⇒ no edit instruction; plan ⇒ no code edits), no invented constraints,
output format not in conflict with task, not a near-verbatim echo.

Gates:
- **Drop-in (blocking for 2.2.0):** default claude_code path pass rate ≥ 2.1.1
  baseline (no regression).
- claude_code adapter: ≥ current measured baseline.
- generic_chat adapter: ≥ 8/10 (Markdown is easier for the local model).
- codex / cursor (2.2.x): each ≥ 8/10 **with its repair path** before ship; set
  the exact bar when measured.
- No mode may force "exactly N sections" or word limits unless the user asked for
  a short answer rather than implementation.

## Best default behavior

1. Coding-agent work → implementation prompt, not response-format-only.
2. Review → findings-first, no edit instruction unless user says "fix".
3. Planning → plan-only, forbid code edits.
4. claude_code → XML by default.
5. codex → Markdown task/context/constraints/verify; mention repo `AGENTS.md`
   only when project conventions matter.
6. cursor → shorter; reference project rules / target files.
7. Always preserve user-stated constraints; never invent word limits, section
   counts, or output formats the user didn't request.
8. Always include verification for implementation/debug prompts *when the
   verification toggle is on* (off by default for drop-in; on is the richer
   opt-in).

## Rollout order

1. Backend `ffp_prompt_builder` + deterministic renderers + per-adapter
   contracts + **identity default**.
2. Unit tests incl. the drop-in identity gate.
3. Wire runtime prompt mode to the builder (claude_code + generic_chat);
   adapter-aware echo/repair/fallback.
4. Dashboard card + deterministic preview.
5. `tools/prompt_builder_eval.py`; run against the current preferred short-task
   provider; confirm no drop-in regression.
6. Ship 2.2.0 (version in the 4 files + CHANGELOG; SPEC `prompt_builder` block +
   invariants for the identity guarantee and target-aware validation).
7. Only after measured non-regression: add codex + cursor adapters as 2.2.x.
8. Update `docs/local-llm-provider-benchmark-readme.md` only if measured quality
   changes.

## Resolved open questions (were open in rev 1)

- **Default target = `claude_code`**, not universal — matches current behavior
  and the primary tool; universal-by-default would worsen the drop-in problem.
- **No per-provider target.** `target_agent` = where the user pastes the output
  (destination intent); `provider` = which local model generates it. Orthogonal;
  do not link them. (A provider-aware *repair* optimization is possible later —
  explicitly out of scope for 2.2.0.)
- **Preview is deterministic-only** in 2.2.0 (instant, free, no NPU cost). A
  "test with current model" live preview is a later, explicitly opt-in add-on.

## Risks / notes

- Highest-regression-risk area: this rewires the *locked, safety-critical*
  prompt path characterized by the 2026-07 benchmark. The identity default +
  drop-in gate are the guardrail.
- Local 4B generators are the constraint, not the adapters — that is why the
  rollout is staged and gates are per-adapter with repair.
- SPEC additions: a `prompt_builder` cfg block; an invariant that default
  settings ⇒ `CLAUDE_PROMPT_SYSTEM_PROMPT` identity; an invariant that prompt
  validation is target-aware (XML required only for claude_code/xml structure).
