# Local LLM Provider Benchmark — Proper Rerun Plan

Date: 2026-07-07
Status: JULY 8 BATCH EXECUTED — second-day reproducibility and optional stretch cells remain
Supersedes the methodology of: `docs/local-llm-provider-poc-readme.md` (POC of 2026-07-07)

## Execution Tracker

Current report: `docs/local-llm-provider-benchmark-readme.md`

Completed in the July 8 batch:

- Harness delivered: `tools/provider_bench.py` plus
  `tests/test_provider_bench.py`.
- Matrix A Qwen2.5 3B row: FLM, Ollama, LM Studio, and Lemonade NPU tested.
- Matrix A Llama 3.2 1B row: FLM, Ollama, LM Studio, Lemonade Hybrid
  substitution, and exact Lemonade `Llama-3.2-1B-Instruct-NPU` tested.
- Matrix A Llama 3.2 3B row: FLM, Ollama, and LM Studio tested. No Lemonade
  3B NPU cell exists in the catalog row for this plan.
- Matrix B quick quality: `Qwen2.5-7B-Instruct-NPU`,
  `Phi-4-mini-instruct-NPU`, `Mistral-7B-Instruct-v0.3-NPU`, and
  `Qwen3-4B-Hybrid` with thinking disabled tested.
- Qwen3 retest: short grammar/prompt retested with thinking disabled.
- Matrix C calibrated long-context: FLM incumbent, Lemonade Qwen2.5 3B NPU,
  and Lemonade Qwen3 4B Hybrid tested.
- Qwen2.5 `prompt_plan` miss diagnosed: deterministic repair and strict retry
  both recover the failing case.
- LM Studio Qwen2.5 7B prompt near-misses diagnosed: deterministic label-to-XML
  repair recovers all timed prompt outputs.
- Product prompt-output repair implemented and unit-tested for the Qwen2.5
  malformed-context case and LM Studio label-to-XML case.
- Clean app-level prompt-repair validation completed:
  Lemonade `Qwen2.5-3B-Instruct-NPU` `prompt_plan` passed `5/5`, and
  LM Studio `qwen2.5-7b-instruct` passed `10/10` prompt cases through
  `grammar_fix.call_flm`.
- LM Studio Qwen2.5 7B route decision made: keep it as a supported experimental
  opt-in fast prompt route, not as a default or automatic replacement.
- Optional `Meta-Llama-3.1-8B-Instruct-NPU` stretch decision made: do not pull
  or test in this batch.
- Next-day rerun helper added: `tools/run_next_day_provider_rerun.ps1`.
  Dry-run validation passed on July 8 with `-DryRun -AllowSameDay`.
- Second-day artifact evaluator added: `tools/evaluate_second_day_provider_rerun.py`.
  Unit tests cover pass/fail gates for prompt quality, long-context coverage, and
  memory guard violations.

Not complete yet:

- Replace-FLM gate is not satisfied because the strongest candidate,
  Lemonade `Qwen2.5-3B-Instruct-NPU`, still needs a second-day rerun.
- Lemonade Qwen3 4B Hybrid passes short prompt mode but is disqualified for
  meetings until its visible-output failure above roughly 2.1k prompt tokens is
  fixed.
- LM Studio Qwen2.5 7B remains experimental; it is approved only as an opt-in
  non-default prompt route after app-level repair validation.

Next batch:

1. On the next calendar day, rerun Lemonade `Qwen2.5-3B-Instruct-NPU`
   grammar/prompt and calibrated long-context:

   ```powershell
   pwsh -NoProfile -ExecutionPolicy Bypass -File tools\run_next_day_provider_rerun.ps1
   ```

2. If short prompt routing is still under consideration, rerun Lemonade
   `Qwen3-4B-Hybrid` grammar/prompt with thinking disabled:

   ```powershell
   pwsh -NoProfile -ExecutionPolicy Bypass -File tools\run_next_day_provider_rerun.ps1 -RunQwen3Short
   ```

3. Keep LM Studio Qwen2.5 7B opt-in only unless a future second-day route test
   and product decision promotes it.
4. Evaluate the new artifacts and use the output as the replace-FLM gate record:

   ```powershell
   python tools\evaluate_second_day_provider_rerun.py `
     --qwen25-short data\benchmarks\second_day_lemonade_qwen2.5-3b-instruct-npu_<yyyymmdd>.json `
     --qwen25-longctx data\benchmarks\second_day_lemonade_qwen2.5-3b-instruct-npu_longctx_calibrated_<yyyymmdd>.json `
     --out data\benchmarks\second_day_lemonade_qwen2.5-3b-instruct-npu_gate_<yyyymmdd>.json `
     --markdown-out data\benchmarks\second_day_lemonade_qwen2.5-3b-instruct-npu_gate_<yyyymmdd>.md
   ```

## Why a rerun

The first POC reached a defensible bottom line (keep FLM as production default) but
four methodology problems mean its comparative numbers can't be trusted as final:

1. **Model ≠ provider.** Every runtime ran a different model (FLM qwen3.5:4b vs
   Ollama llama3.2:3b vs LM Studio qwen2.5-3b vs Lemonade 1B/4B), so the "quality"
   column compared models, not providers. XML-contract adherence is a model
   property; the runtime just executes weights.
2. **n=2, no warmup, cold-start contamination.** The raw artifact shows FLM
   prefilling at 16.5 tok/s on run 1 (steady state is 300–420 tok/s) — FLM was
   measured semi-cold in the very test used to call it slow. Every provider's
   "avg" was (cold+warm)/2.
3. **Lemonade Qwen3-4B "grammar failure" was a harness bug.** Qwen3 is a thinking
   model; the entire 160-token budget went into an unclosed `<think>` block
   (160 completion tokens, 0 visible chars). Thinking was never disabled.
4. **Quality judged on 2 runs of 1 prompt** with subjective labels, unequal output
   lengths in wall-clock comparisons, and mixed memory metrics (model RSS vs
   whole-system peak).

Additionally, the harness that produced `provider_response_poc_20260707.json` was
never committed, so the main benchmark is not reproducible from the repo. This
plan fixes all of the above.

## Goals

- **G1 — Runtime speed, model held constant.** Measure what each runtime
  (FLM/NPU, Ollama/CPU, LM Studio/Vulkan-iGPU, Lemonade/NPU) actually adds or
  costs, by running the *same weights* on multiple runtimes.
- **G2 — Lemonade best-in-class NPU models near the 6B sweet spot.** The first POC
  tested only a 1B Hybrid (too weak) and a 4B thinking model (misconfigured).
  Lemonade's pure-NPU catalog (`-NPU` suffix, `ryzenai-llm:npu` backend) has
  modern instruct models in the 4–9 GB range. Question: **is there a Lemonade NPU
  model around the 6B class that passes Flowkey's XML contract at usable speed?**
- **G3 — Quality as a measured pass rate**, not an eyeballed label: XML-contract
  pass rate over a fixed prompt set; grammar-mode fidelity over a fixed sentence set.
- **G4 — Long-context (meetings) workload.** The heaviest Flowkey workload is now
  ~7k-token meeting-digest prefill. No alternative was tested past ~1.9k context
  in the POC. Measure 1k/4k/8k prompts on every candidate.
- **G5 — A routing decision, not just keep/drop.** FLM wins prefill (~400 tok/s
  NPU) but loses decode (~13 tok/s vs LM Studio ~25). The likely correct outcome
  is per-workload routing (grammar → fastest decoder; meetings → fastest
  prefiller). The rerun must produce the numbers to decide that.

## Test matrix

### Matrix A — same model, different runtimes (G1)

Quantization is not identical across runtimes (GGUF Q4_K_M vs FLM NPU quant vs
Lemonade OGA int4). Record the quant per cell; treat small quality deltas as
quant noise, but speed comparisons remain valid.

| Weights | FLM (NPU) | Ollama (CPU) | LM Studio (Vulkan iGPU) | Lemonade (NPU) |
|---|---|---|---|---|
| Qwen2.5-3B-Instruct | `qwen2.5-it:3b` | `qwen2.5:3b` | `qwen2.5-3b-instruct` (have it) | `Qwen2.5-3B-Instruct-NPU` (4.10 GB) |
| Llama-3.2-1B-Instruct | `llama3.2:1b` | `llama3.2:1b` | `Llama-3.2-1B-Instruct-GGUF` | `Llama-3.2-1B-Instruct-NPU` (1.96 GB) |
| Llama-3.2-3B-Instruct | `llama3.2:3b` | `llama3.2:3b` | `Llama-3.2-3B-Instruct-GGUF` | — (not in NPU catalog) |
| Gemma-3-4B-it (optional) | `gemma3:4b` | `gemma3:4b` | — | `Gemma-3-4b-it-mm-NPU` (6.68 GB) |

The Qwen2.5-3B row is the keystone: it exists on **all four** runtimes.

### Matrix B — Lemonade ~6B-class NPU quality hunt (G2)

From the `Ryzen AI LLM` suggested list, ranked by expected instruction-following
quality in the 4–9 GB band:

| Priority | Model | Size | Why / why not |
|---|---|---:|---|
| 1 | `Qwen2.5-7B-Instruct-NPU` | 8.83 GB | Best expected XML adherence in class; same family as FLM's qwen models |
| 2 | `Phi-4-mini-instruct-NPU` | 5.59 GB | 3.8B params but modern; punches at 7B class; right at the ~6 GB target |
| 3 | `Mistral-7B-Instruct-v0.3-NPU` | 8.09 GB | Solid mainstream 7B instruct |
| 4 (stretch) | `Meta-Llama-3.1-8B-Instruct-NPU` | 9.30 GB | Only if RAM headroom holds (see memory guard) |
| retest | `Qwen3-4B-Hybrid` | 4.8 GB | Redo the POC test **with thinking disabled** — its failure was our bug |

Excluded, with reasons (record in the report so nobody re-litigates):

- `chatglm3-6b-NPU`, `Llama-2-7b-*`, `Qwen1.5-7B-Chat`, `CodeLlama-7b` — 2023-era
  or code-domain; weak instruction following, no chance at the XML contract.
- Base (non-Instruct) variants (`Llama-3.1-8B-NPU`, `Meta-Llama-3-8B-NPU`,
  `Qwen2-7B-NPU`, `Mistral-7B-v0.3-NPU`, `Llama-3.2-1B-NPU`, `Qwen2-1.5B-NPU`) —
  base models don't follow contracts.
- `DeepSeek-R1-Distill-*` — reasoning models; only test in an optional appendix
  with the thinking protocol below, never in the headline table.
- `gpt-oss-20b-NPU` (13.4 GB) — reasoning model AND too close to the 23.6 GiB RAM
  ceiling; guaranteed paging.
- `Qwen2.5-Coder-*`, `Phi-3-mini-*`, `Phi-3.5-mini` — code-domain / superseded by
  Phi-4-mini.

### Matrix C — long-context sweep (G4)

For every candidate that survives Matrix A/B quality gates, plus FLM `qwen3.5:4b`
as the incumbent: synthetic meeting-transcript prompts at **~1k, ~4k, ~8k tokens**
asking for a ~150-token digest. Measure TTFT and total wall time. If a runtime
caps context below 8k (some NPU builds cap at 2k/4k — record the cap), that's a
disqualifying fact for the meetings workload, not a footnote.

## Method (fixes for every POC flaw)

### Runs and statistics

- Per cell: **1 discarded warmup call, then 5 timed runs.** Report **median** and
  min–max. Never average cold and warm runs. n=2 medians are banned.
- All providers benchmarked **in the same session, same day** — no reusing June
  FLM sweeps as the baseline. Re-run FLM fresh alongside the others.
- Record TTFT wherever the API exposes it (FLM and Lemonade return
  `prefill_duration_ttft` in `usage`; LM Studio: read the server log; Ollama:
  make one extra call to native `/api/chat` for `prompt_eval_duration` /
  `eval_duration` — wall-clock still comes from the OpenAI-compat path).

### Thinking-model protocol (fixes POC flaw #3)

Applies to Qwen3/Qwen3.5 on Lemonade and any R1-distill appendix run:

1. First choice: disable thinking — `chat_template_kwargs: {"enable_thinking": false}`
   in the request body, or append `/no_think` to the system prompt (Qwen3's
   documented switch). Verify with a probe that output contains no `<think>`.
2. If thinking can't be disabled: set `max_tokens ≥ 1024`, strip
   `<think>…</think>` before scoring, and report **visible-output latency**
   separately from total latency.
3. A cell where all output went to an unclosed think block is a **config error**,
   not a result. Fix and re-run; never publish it as a model failure.

### Quality scoring (fixes POC flaw #4)

Automated contract checker, committed with the harness:

- **Prompt mode — 10 fixed prompts** (varied: code task, research question, email
  draft, data-analysis ask, meeting recap, vague one-liner, etc.). PASS requires:
  all four tags `<task>` `<context>` `<constraints>` `<output_format>` present in
  order, each section non-empty, no `<think>` residue, not wrapped in a Markdown
  fence. Score = pass rate /10. Markdown-headers-instead-of-tags is a FAIL but
  record it as `near_miss: true` (it's repairable with output validation).
- **Grammar mode — 8 fixed sentences** (subject-verb error, its/it's, run-on,
  comma splice, tense error, typo, one long sentence, and **one already-correct
  control sentence**). PASS requires: no preamble (must not start with "Here
  is"/"I understand"/etc.), output length within ±30% of input, and the control
  sentence returned essentially unchanged (catches over-rewriters). Meaning
  preservation: reviewer spot-check, recorded per cell.
- Wall-clock comparisons must also report **seconds per completion token** so a
  model that answers with 78 tokens doesn't "beat" one that wrote 286.

### Memory metric (one metric, all providers)

- Record **provider process(es) RSS delta** (before load → during generation) via
  `Get-Process | Measure WorkingSet64`, AND system available-memory delta.
- Memory guard: on this 23.6 GiB machine, abort/flag any cell where available
  RAM drops below **3 GB** or `\Memory\Pages/sec` spikes during generation —
  paged results are not valid speed results (this likely tainted the POC's
  Qwen3-4B-Hybrid 4.98 TPS figure at 20.3 GB peak).

### Environment controls (pre-flight checklist — do all of these)

- [ ] AC power; Windows power mode "Best performance".
- [ ] **Quit Flowkey** (tray → Exit) so the daemon can't fire the after-hours
      meeting batch mid-benchmark (its window is 17:00–21:00 — exactly when you'd
      run this), and its FLM server isn't holding the NPU. Run providers manually.
- [ ] **Only one provider server running per measurement.** Stop FLM before
      Lemonade NPU cells and vice versa (both target the same NPU). Stop LM
      Studio/Ollama when not under test.
- [ ] Close Chrome/Teams/Docker; record baseline available RAM.
- [ ] Record versions at run time: `flm version --json`, `ollama --version`,
      `lms version`, `lemonade --version` (or installer version), plus
      `lemonade backends --all` output.
- [ ] FLM performance mode: record it (config said `max` during the POC; meetings
      run `turbo` — pick ONE for the whole session and note it).
- [ ] Lemonade CLI benches: use `--warmup 1 --runs 3` minimum (POC used
      `--warmup 0 --runs 1`).

## Harness (must be committed this time)

The POC's main artifact was produced by an ad-hoc uncommitted script. Deliverable:
**`tools/provider_bench.py`** (stdlib only, like the rest of the repo), reusing
`scripts/ffp_provider_runtime.openai_url()` for endpoint handling.

CLI shape:

```powershell
python tools\provider_bench.py `
  --provider lemonade --base-url http://127.0.0.1:13305/api/v1 --bearer lemonade `
  --model Qwen2.5-7B-Instruct-NPU `
  --tasks grammar,prompt,longctx --runs 5 --warmup 1 `
  --out data\benchmarks\rerun_<provider>_<model>_<yyyymmdd>.json
```

Artifact JSON must embed (POC artifact lacked the first two):

- the **exact prompts** and system prompts used (self-contained repro),
- provider/tool **versions** and quantization/`model_used` identifier,
- per-run: wall seconds, TTFT if available, prompt/completion tokens, visible
  output, think-stripped flag, contract-check result, seconds-per-completion-token,
- memory before/during/after.

The XML-contract checker and grammar checker live in the same file and are unit
tested (`tests/test_provider_bench.py` — checker logic only, no network).

## Execution order

1. **Prep (30 min):** pre-flight checklist; write/commit harness; pull all models
   (sizes ~1.9–8.8 GB each; pull serially, disk is the constraint).
   - FLM: `flm pull qwen2.5-it:3b`, `flm pull llama3.2:1b`, `flm pull llama3.2:3b`
   - Ollama: `ollama pull qwen2.5:3b llama3.2:1b llama3.2:3b`
   - LM Studio: retry `lms get lmstudio-community/Qwen2.5-7B-Instruct-GGUF --gguf -y`
     (POC's ECONNRESET is transient; `lms get` resumes), plus the 1B/3B Llama GGUFs
   - Lemonade: `lemonade pull Qwen2.5-3B-Instruct-NPU`, `Llama-3.2-1B-Instruct-NPU`,
     `Qwen2.5-7B-Instruct-NPU`, `Phi-4-mini-instruct-NPU`, `Mistral-7B-Instruct-v0.3-NPU`
2. **Matrix A** (same-model, 4 runtimes × grammar+prompt): settles the
   runtime-speed question with quality held constant.
3. **Matrix B** (Lemonade 6B-class hunt): quality pass rates first with 2 quick
   runs; only models scoring ≥7/10 XML get the full 5-run speed treatment
   (don't burn an evening timing models that fail the contract).
4. **Qwen3-4B-Hybrid retest** with the thinking protocol — closes out the POC's
   broken cell.
5. **Matrix C** (long-context) for survivors + FLM incumbent.
6. **Report + decision** (template below).

Budget: roughly one evening for A+B, a second session for C and the report.

## Decision gates (agree on these before running)

**Replace-FLM gate** — a candidate provider+model may become the production
default only if ALL hold:

- XML pass rate ≥ 9/10 and grammar pass ≥ 7/8 (incl. the control sentence),
- warm median grammar wall time ≤ FLM's on the same day,
- 8k-context TTFT ≤ 1.5× FLM's, with no context-cap below 8k,
- no memory-guard violations,
- reproducible across two sessions (different days).

**Routing gate** — if a candidate passes quality + short-task speed but fails the
long-context clause, the outcome is **per-workload routing** (grammar/chat →
candidate; meetings/digests → FLM), which the provider wiring from the POC
already supports.

**Status-quo gate** — if nothing passes quality, FLM stays default; record the
best near-miss and what would flip it (e.g. output-repair layer for LM Studio).

## Report template

One table per matrix; every row carries: provider, model, quant, task, median s,
min–max, TTFT ms, s/completion-token, XML or grammar pass rate, peak RSS delta,
memory-guard flag, notes. Plus a short "Decision" section that applies the gates
above verbatim, and a "Anomalies" section for anything discarded (with the reason
— discarded runs are listed, never silently dropped).

## Cleanup

```powershell
# Lemonade
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload <each-model>
# LM Studio
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" unload --all
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" server stop
# Ollama
ollama stop qwen2.5:3b; ollama stop llama3.2:3b; ollama stop llama3.2:1b
# Disk: 5 Lemonade pulls ≈ 30 GB — delete the Matrix-B losers after the report
```

Then restart Flowkey and re-enable anything toggled off in pre-flight.

## Known caveats to carry into the report

- Quantization differs per runtime for "the same" weights — say so under every
  Matrix A table.
- Pure `-NPU` models (OGA, NPU-only) may decode slower than `-Hybrid`
  (NPU prefill + iGPU decode). If a 7B NPU model passes quality but decodes at
  ~5 tok/s, check whether a Hybrid variant of the same weights exists before
  concluding "too slow".
- Ollama cannot use this machine's NPU or (currently) the Radeon 860M iGPU — it
  is structurally CPU-bound here. Its role is portability fallback; don't expect
  the rerun to change that.
- FLM's June sweep numbers (`qwen3-5-4b_1780503503.json` etc.) are historical
  context only — never comparison rows.
