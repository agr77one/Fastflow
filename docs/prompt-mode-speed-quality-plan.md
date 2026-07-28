# Prompt Mode Speed + Quality Refresh Plan ("prompt v2")

Date: 2026-07-09 (Phases 0–2 shipped in 2.3.0; Phase 3 done post-2.3.0; Phase 4 deferred)
Status: Phases 0–2 SHIPPED (2.3.0). Phase 3 (streaming Chat tab) DONE on
`feat/streaming-chat`. Phase 4 (faster route) trigger not met → deferred.
Target release: **2.3.0** (re-baselines the default `prompt:` output — see Risk)

## Problem

`prompt:` mode (rough text → copy-paste-ready coding-agent prompt) is too slow
and its default output is over-verbose. The goal: **p50 latency ≤ ~15 s** and
**significantly higher prompt quality**, matching the user's experience that an
earlier, leaner prompt tool was "better and faster."

## Measured baseline (2026-07-09, FLM `qwen3.5:4b` NPU, warm)

Live A/B on one representative input ("build a python script that reads a folder
of CSVs, validates rows against a schema, writes an error report with file/line
numbers"):

| Style | Warm wall | Completion tokens | Output size | Notes |
|---|---:|---:|---:|---|
| **Current** (`CLAUDE_PROMPT_SYSTEM_PROMPT`, cap 700) | 30–32 s | 257–271 | ~1350 chars | pads `<context>` with restated background |
| **Lean candidate** (dense, cap 200) | 14–18 s | 109–149 | ~476–768 chars | tighter, more actionable |

Real usage (`data/*history.jsonl`, 46 prompt runs): **median 18.9 s, min 8.8 s,
max 49.8 s, ~207 completion tokens.** Warm TTFT ~1.6 s; cold model load adds
~18 s (one-time).

## Diagnosis

Prompt latency = TTFT (~1.6 s warm) + `completion_tokens ÷ decode_tok_s`
(~13 tok/s on the 4B NPU). So **wall time is almost entirely a function of how
many tokens the model emits.** Two things inflate that:

1. **Verbose system prompt.** The current prompt invites full-paragraph
   `<context>` prose that restates the request — low signal, high token count.
2. **Generous token budget.** Prompt mode caps at **700 / 900 / 1200** tokens
   (`ffp_llm_client.select_runtime`, `strategy=prompt_short/medium/long`), so
   long outputs (and the 49.8 s tail) are allowed to run away.

**Key finding: quality ≠ length.** The lean output kept all four sections but was
more specific and actionable in ~half the tokens. Padding is a *quality*
regression, not just a speed one — which is exactly why the earlier tool felt
better and faster.

## Goals & success criteria

- **Speed:** p50 ≤ 15 s, p90 ≤ 20 s on the default provider/model, warm.
- **Quality:** prompt-v2 quality score ≥ current on a fixed rubric (below), with
  **zero** invented-requirement failures.
- **No token runaway:** hard cap sized to the dense target; no >25 s outliers on
  ordinary inputs.
- Ship only if BOTH the speed gate and the quality gate pass in the A/B eval.

## Levers (ranked by impact ÷ cost)

1. **Denser default system prompt (biggest lever, free, improves quality).**
   Rewrite the built-in prompt to demand high signal + brevity: one imperative
   `<task>` line, `<context>` = only stated facts (no restating the request),
   `<constraints>` = 3–5 concrete testable bullets, `<output_format>` = one line,
   whole prompt ≤ ~160 tokens, no preamble/meta-framing. (The lean candidate
   above, refined so `<constraints>` stays its own section — the raw lean run
   merged constraints into `<context>`, which the eval must catch.)
2. **Lower the prompt-mode token cap.** Replace 700/900/1200 with something like
   **240 / 320 / 420** (short/medium/long) — enough for a complete dense prompt,
   bounds the tail. Cheap; directly caps worst case.
3. **Streaming output (perceived latency).** Stream tokens to the UI so first
   text shows at ~1.6 s TTFT instead of after the full 15 s. Doesn't cut total
   time but transforms felt speed. Larger change (SSE through daemon → dashboard;
   the AHK paste path stays non-streaming). Phase 2 / optional.
4. **Keep the model warm.** Cold load costs ~18 s TTFT once. Ensure FLM stays
   loaded (warmup on daemon start / after idle) so no user hits the cold tax.
5. **Faster-decoding route (optional, measured).** LM Studio iGPU decodes ~25
   tok/s vs FLM ~13 (2026-07 benchmark), so the same output is ~2× faster — but
   smaller/non-NPU models historically miss the XML contract and need repair.
   Only pursue behind the prompt-builder's provider/target split, gated on the
   quality eval; not required to hit the goal (levers 1–2 already do).

Levers **1 + 2 alone** move the measured 30 s case to the ~14–18 s range — enough
to hit the ≤15 s goal. Streaming + warm are polish.

## Candidate prompt v2 (starting point — tune via the eval, do not ship raw)

```
Turn the user's rough request into a tight, high-signal coding-agent prompt.
Emit exactly four sections, each on its own:
<task> one imperative sentence naming the concrete deliverable.
<context> only facts the user stated; never restate the request or invent background.
<constraints> 3-5 concrete, testable bullets drawn only from the request.
<output_format> one line naming the exact output shape.
Be specific and actionable. No preamble, no meta-framing, no filler.
Keep the whole prompt under 160 tokens. Return only the prompt.
```

## Measurement harness ("proper measures" — build this FIRST, it is the gate)

Extend `tools/prompt_builder_eval.py` (or a sibling `tools/prompt_speed_quality_eval.py`)
to A/B **old vs v2** on a fixed input set and emit a JSON artifact. It must
measure BOTH axes; speed alone is not the goal.

### Fixed input set (≥ 12 realistic `prompt:` requests)
Cover the spread: implement, debug, review, refactor, data task, one-liner/vague,
long multi-paragraph, and a "trap" input that tempts invented requirements. Store
them in the harness so runs are comparable over time.

### Speed metrics (per prompt, per input; 1 warmup + ≥ 5 timed)
- wall seconds (median, p90, min–max)
- TTFT (from `usage.prefill_duration_ttft`)
- completion tokens (median)
- decode tok/s (completion_tokens ÷ decode_duration)
- seconds-per-output-token (so a shorter answer doesn't falsely "win")

### Quality rubric (score each output /7; pass = ≥ 6/7)
- R1 all four sections present AND cleanly separated (not merged into one)
- R2 `<task>` is a single imperative sentence
- R3 `<constraints>` = 3–5 concrete, testable items
- R4 `<context>` = only stated facts; no restated request; no filler
- R5 `<output_format>` = one concrete line
- R6 no preamble / meta-framing / `<think>` residue / markdown-fenced whole
- R7 total ≤ target length (≤ ~220 tokens)
- **Hard fail (any score):** an invented requirement the user never stated.

R1/R2/R5/R6/R7 are machine-checkable (reuse `check_prompt_contract` + regex/length).
R3/R4 and the invented-requirement check need a **judge**: an LLM-judge (a
stronger model, or Claude via the API) scoring against the rubric, or a manual
pass for the first baseline. Record which was used.

### Gate (both must hold on the fixed set)
- **Speed:** v2 p50 ≤ 15 s AND ≤ 60% of the current p50.
- **Quality:** v2 median rubric score ≥ current AND v2 invented-requirement
  failures = 0 AND v2 clean-section (R1) pass rate ≥ current.

Commit the artifact (`data/benchmarks/prompt_v2_ab_<date>.json`) as the evidence,
like the 2.2.0 prompt-builder eval.

## Implementation phases (plan only)

- **Phase 0 — Harness + baseline.** Build the A/B eval; record current-prompt
  numbers as the frozen baseline. No product change yet.
- **Phase 1 — Prompt v2 + token cap.** Iterate the dense prompt against the eval
  until it clears both gates; lower the prompt-mode caps. This is the core change
  and likely hits the goal alone.
- **Phase 2 — Warm-model guarantee.** Ensure FLM stays loaded so nobody eats the
  cold-load tax; verify with a cold-vs-warm measurement.
- **Phase 3 (optional) — Streaming. ✅ DONE (post-2.3.0, `feat/streaming-chat`).**
  The surface that actually benefits is the **Chat tab** (replies up to 1024
  tokens, formerly a blank wait) — `prompt:` has no live dashboard surface and the
  AHK paste path pastes at the end, so streaming there is moot. Delivered as a
  `text/event-stream` response over a `fetch()` POST (keeps the `X-FFP-API` CSRF
  gate that `EventSource` cannot send): `ffp_chat.stream_send` generator →
  daemon `_STREAM_ACTIONS`/`_sse_frame`/`_stream_action` → `app.js` fetch-reader,
  with a transparent fallback to the one-shot `chat_send` on any older daemon.
  Measured live on FastFlowLM `qwen3.5:4b`: first token **1.58 s** vs **2.10 s**
  total — text flows in instead of a blank wait. AHK/grammar/prompt paths
  untouched. See SPEC V37 / T27.
- **Phase 4 (optional) — Faster route. ⏸ TRIGGER NOT MET — deferred.** The plan
  gates this on "p50 still > 15 s after phases 1–2," but 2.3.0 put prompt p50 at
  **3.38 s warm** and Phase 3 streaming already delivers the *perceived* speed
  this phase was chasing. Revisit only if a concrete slow surface appears.

  **Feasibility note (2026-07-27, FLM 0.9.45).** The original blocker recorded
  here — "a second runtime can't co-serve the NPU with FastFlowLM (both wedge)" —
  applies to another *NPU* runtime (Lemonade/LM Studio on the NPU). It does **not**
  apply to an NPU + **discrete/iGPU** split, which is different hardware with no
  NPU contention. FLM 0.9.45 adds **per-round KV cache checks**: a conversation
  can alternate FLM-on-NPU and llama.cpp-on-GPU rounds and the NPU now recognises
  its own already-cached earlier rounds instead of re-prefilling the whole
  history from scratch. That makes a hybrid route genuinely practical for the
  first time. It changes the *feasibility*, not the decision — the speed goal is
  already met, so this stays deferred rather than becoming newly attractive.

### Does FLM 0.9.45's KV cache work change anything today? (assessed 2026-07-27)

**For prompt mode and the other hotkeys: no.** Per-round KV cache checks pay off
only when rounds of *one* conversation alternate between backends. Flowkey never
does that:

- `prompt:`, grammar, summarize, explain, tone are **single-shot and stateless** —
  one system+user pair, no prior rounds to reuse. `prompt:` is also decode-bound
  (TTFT ≈1.5 s of a ≈3.4 s total), so prefill is not the cost.
- **Chat** is multi-turn but always replays its 12-turn window to a *single*
  backend — ordinary sequential prefix caching, which FLM already did. The new
  gap-tolerance never triggers.
- **Provider fallback** is all-or-nothing between FastFlowLM and Ollama, which are
  separate servers with separate caches — nothing is shared to reuse.
- **Meetings digests** are one large single-shot prefill.

So there is nothing to change in the prompt pipeline for 0.9.45. The upgrade's
real impact on users was operational, not architectural: it **invalidates
already-pulled models** stamped for an older FLM (see 2.4.1 / SPEC V39, which
added detection for exactly that).

## Integration with the existing prompt-builder (2.2.0)

This lands cleanly on the current design:
- Prompt v2 becomes the new **`claude_code` default** system prompt (and the
  `detail_level=concise` shape). The prompt-builder's synthesis/validate/repair
  path already exists.
- The token-cap change is in `ffp_llm_client.select_runtime`.
- The eval reuses `ffp_prompt_builder.validate` + `check_prompt_contract`.

## Risk & rollback

- **Re-baselines V24.** V24 pins the default to today's `CLAUDE_PROMPT_SYSTEM_PROMPT`
  (identity). Prompt v2 intentionally changes the default output, so V24 must be
  updated to "default = prompt-v2 system prompt" and the drop-in identity test
  re-pointed. This is a deliberate quality/speed improvement, not a silent
  regression — gate it on the A/B eval and record before/after numbers in the
  release notes.
- **Rollback:** the old prompt is one constant; keep it named (e.g.
  `CLAUDE_PROMPT_SYSTEM_PROMPT_V1`) so a config/flag can revert instantly if a
  user prefers the verbose style.
- **Quality is subjective:** the rubric + judge make it measurable, but include a
  manual side-by-side of ~5 outputs in the release evidence so a human confirms
  "better," not just "shorter."

## One-line summary

Prompt mode is slow because it's decode-bound and the default prompt
over-generates (30 s / ~265 tokens). A denser prompt + lower token cap cuts it to
~14–18 s AND tightens quality (measured), so build the A/B speed+quality harness
first, tune prompt v2 until it clears both gates, then ship as 2.3.0 with the old
prompt kept as an instant rollback.
