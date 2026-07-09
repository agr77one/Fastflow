# Local LLM Provider Benchmark README

Date: 2026-07-09

This README captures the current proof of concept for whether Flowkey should
drop FastFlowLM (FLM) and use Ollama, LM Studio, or Lemonade on the AMD NPU
instead. It includes setup steps, exact benchmark commands, artifacts, measured
results, anomalies, and the current decision.

## Decision (corrected by the July 9 truncation audit)

Do not drop FLM. The outcome is the rerun plan's ROUTING gate, not replacement.

The July 9 "replace-FLM gate PASS" for Lemonade `Qwen2.5-3B-Instruct-NPU` was
subsequently INVALIDATED for the long-context/meetings workload: a direct
needle probe proved the runtime silently truncates input to roughly the last
2-3k tokens while still reporting the full `prompt_tokens` count (see "July 9
Truncation Audit" below). Its short-task results (grammar `40/40`, prompt
`45/50`, both days) remain valid — it is an approved candidate for
grammar/prompt routing only. FLM remains the only tested runtime that actually
processes an 8k-token meeting transcript on this machine.

The corrected rerun shows:

- FLM `qwen3.5:4b` remains the proven incumbent/fallback because it passes short
  prompt/grammar quality and the long-context meeting workload in the same
  session: `49/50` prompt passes, `40/40` grammar passes, and `5/5` at roughly
  8k prompt tokens.
- Lemonade `Qwen2.5-3B-Instruct-NPU` is a strong SHORT-TASK candidate:
  `40/40` grammar and `45/50` prompt XML on both July 8 and July 9. Its
  "much faster calibrated 8k TTFT" and `15/15` long-context passes were
  artifacts of silent keep-last input truncation (~2-3k effective window)
  that the uniform-filler transcript could not detect — the July 9 needle
  probe invalidated the long-context clauses of the gate. Its one consistent
  prompt miss is narrow: `prompt_plan` is recoverable with deterministic tag
  repair or a stricter retry prompt.
- Lemonade `Qwen3-4B-Hybrid`, retested with thinking disabled, is now a strong
  short prompt-mode candidate: `40/40` grammar and `50/50` prompt XML. It is not
  a global replacement because its long-context route returns empty visible
  output after roughly 2.1k prompt tokens.
- The Lemonade NPU-only rerun with FLM fully stopped confirmed the same
  effective-context problem across all four tested Lemonade models:
  `Qwen2.5-3B-Instruct-NPU`, `Qwen3-4B-Hybrid`,
  `Qwen2.5-7B-Instruct-NPU`, and `Phi-4-mini-instruct-NPU` all retrieved the
  start needle at 1.5k target tokens and lost it at 2k. Reloading with
  `--ctx-size 8192` did not improve any row.
- Ollama `llama3.2:3b` is a useful small CPU fallback. It is faster than FLM for
  grammar, but it scored `0/50` on prompt XML.
- The completed Llama Matrix A rows did not change the routing decision:
  every Llama 3.2 1B/3B provider cell scored `0/50` on prompt XML, including
  the exact Lemonade `Llama-3.2-1B-Instruct-NPU` cell added after the initial
  Hybrid substitution.
- LM Studio is the fastest tested path for short local grammar work. Qwen2.5 3B
  and 7B both failed prompt XML completely under the current system prompt.
  Qwen2.5 7B is repairable: deterministic label-to-XML conversion fixes all
  `49/49` timed prompt near-misses without another model call.
- Lemonade NPU works, but Qwen2.5 7B, Phi-4-mini, and Mistral 7B missed the
  quick-quality promotion gate under the current Flowkey prompts.

Practical recommendation (corrected):

- Keep FLM as the production default and the ONLY meetings/long-context route.
- Offer Lemonade `Qwen2.5-3B-Instruct-NPU` as an opt-in short-task route
  (grammar/prompt). The deterministic repair path for its known `prompt_plan`
  miss is implemented, unit-tested, and validated through the app path.
- Any provider switch must enforce NPU exclusivity: FLM serving concurrently
  hard-fails Lemonade inference (HTTP 500, wedged until model reload).
- Treat Lemonade `Qwen3-4B-Hybrid` as a short-task-only candidate, pending a
  long-context fix or a workload-specific route that excludes meetings.
- Keep `ollama` wired as a portable CPU fallback.
- Keep `lmstudio` wired as an experimental fast local provider. LM Studio
  Qwen2.5 7B can be a supported opt-in prompt route, but not a default or
  automatic replacement.
- Keep `lemonade` wired as the AMD NPU short-task path, but keep every tested
  Lemonade model out of meeting/long-context routing until a future runtime or
  model configuration proves 8k start-needle retrieval.

## July 9 Truncation Audit

A post-gate audit caught the long-context result before it reached the product.

The tell was in the artifacts themselves: on BOTH days, growing the prompt from
`4042` to `8022` tokens cost zero additional prefill (TTFT ~2.83s for every 4k
AND 8k run), while FLM's TTFT scaled honestly (3.9s → 11.4s → 22.7s). Real
prefill is never free, so a live needle-in-haystack probe was run against
`Qwen2.5-3B-Instruct-NPU` (unique code planted in the transcript, model asked
to retrieve it):

| Probe | Server prompt tokens | Needle found |
|---|---:|---|
| needle at START, ~1k tokens | 1003 | yes |
| needle at START, ~3.5k tokens | 3353 | **no** |
| needle at START, ~8k tokens | 7583 | **no** |
| needle at END, ~8k tokens | 7583 | yes |

That is keep-last input truncation with an effective window of roughly 2-3k
tokens, while `usage.prompt_tokens` still reports the full submitted count.
The benchmark's synthetic transcript was uniform repeated filler, so a digest
of the surviving tail was indistinguishable from a digest of the whole — the
quality checker was structurally blind to truncation, and "5/5 at 8k" passed.

Consequences:

- The `qwen25_longctx_quality` and `qwen25_longctx_sizes` gate clauses are
  INVALID; per the rerun plan's own rule (a context cap below 8k disqualifies
  the meetings workload), Lemonade Qwen2.5-3B-NPU is not meetings-eligible.
- The initial audit showed the same ~2-3k effective-context limitation in the
  first two Lemonade models: Qwen3-4B-Hybrid fails loudly (empty output past
  ~2.1k), and Qwen2.5-3B-NPU fails silently (truncation). The NPU-only ladder
  later expanded this to all four tested Lemonade models.
- Short-task results are unaffected (those prompts fit the window easily).

Side finding — NPU exclusivity: with `flm serve` running, every Lemonade
inference failed HTTP 500 (`RyzenAI DynamicDispatch ... Failed to submit`) and
the backend stayed wedged after FLM stopped, recovering only after a model
unload+reload. FLM and Lemonade cannot serve concurrently on this NPU.

Fixes landed with this audit:

- Probe artifact:
  `data/benchmarks/context_truncation_probe_lemonade_qwen2.5-3b-instruct-npu_20260709.json`
- Harness: `tools/provider_bench.py` now plants `ZEBRA-7741` at the transcript
  start and `OTTER-3305` at the end; `check_longctx_contract` requires both to
  be quoted and reports `truncation_suspected` (end found, start missing).
  Silent truncation is now a scoring failure. Unit-tested.
- Follow-up plan: `docs/lemonade-npu-only-bench-plan.md` (Lemonade-only
  isolation benching with FLM verified off, context-window ladder, and
  context-limit investigation).

## Why Bigger Models Were Tested

The point was not that bigger is automatically better. `llama3.2:3b` is the
right small baseline because it is easy to install and fast enough on CPU.

Bigger models were tested because Flowkey's prompt mode is not just a chat
completion. It requires this exact XML-like scaffold:

```text
<task>
<context>
<constraints>
<output_format>
```

Small and fast models often produced Markdown headings or prose instead. The
rerun confirmed this:

- Lemonade Qwen2.5 3B NPU: `45/50` prompt XML passes.
- Lemonade Qwen3 4B Hybrid, thinking disabled: `50/50` prompt XML passes.
- Ollama `llama3.2:3b`: `0/50` prompt XML passes.
- FLM/Ollama/LM Studio/Lemonade Llama 3.2 1B cells: all `0/50` prompt XML
  passes. The exact Lemonade NPU cell scored `25/40` grammar and `0/50`
  prompt.
- FLM/Ollama/LM Studio Llama 3.2 3B cells: all `0/50` prompt XML passes.
- LM Studio Qwen2.5 3B: `0/50` prompt XML passes.
- LM Studio Qwen2.5 7B: `0/49` timed prompt XML passes, but `49/49` near
  misses and `49/49` pass after deterministic label-to-XML repair.
- Lemonade Phi-4-mini NPU quick cell: `14/20` prompt XML passes.
- Lemonade Qwen2.5 7B NPU quick cell: `0/20` prompt XML passes.
- Lemonade Mistral 7B NPU quick cell: `0/20` prompt XML passes.
- FLM `qwen3.5:4b`: `49/50` prompt XML passes.

So bigger did not reliably solve the contract problem. The best short all-around
candidate remains the 3B Lemonade NPU Qwen2.5 model. It does not replace FLM
for meetings because the NPU-only needle ladder later proved a sub-2k effective
start-of-input ceiling. The best short prompt-only candidate is Qwen3 4B Hybrid
with thinking disabled.

## Ollama And The NPU

The AMD Ollama playbook is useful for installing Ollama, checking AMD software
updates, pulling a model, and calling the Ollama REST API at
`http://localhost:11434`. It is not the same as the Lemonade NPU path used in
the benchmark. On this machine, the observed Ollama runtime reported
`100% CPU` in `ollama ps`; no Ollama NPU cell was available or measured.

So the direct answer is: using only Ollama does not currently give Flowkey the
AMD NPU acceleration measured here. The measured NPU alternatives are FLM and
Lemonade.

## Machine Snapshot

Machine:

| Field | Value |
|---|---|
| Manufacturer/model | `LENOVO 21TB000AUS` |
| CPU | `AMD Ryzen AI 7 PRO 350 w/ Radeon 860M` |
| Cores/logical processors | `8 / 16` |
| RAM | `25386729472` bytes, about 23.6 GiB usable |
| NPU | `NPU Compute Accelerator Device`, status `OK` |

Tool versions:

| Tool | Version |
|---|---|
| FastFlowLM | `0.9.43` |
| Ollama | `0.30.7` |
| LM Studio CLI | commit `9902c3a` |
| LM Studio app | `0.4.19+2` |
| Lemonade Server | `10.9.0` |
| Lemonade Ryzen AI backend | `ryzenai-llm:npu v1.7.0` |

Reference docs:

- AMD Ollama getting started playbook: `https://developer.amd.com/playbooks/ollama-getting-started/`
- LM Studio CLI docs: `https://lmstudio.ai/docs/cli`
- LM Studio OpenAI-compatible server docs: `https://lmstudio.ai/docs/developer/openai-compat`
- Lemonade OpenAI API docs: `https://lemonade-server.ai/docs/api/openai/`
- AMD Lemonade getting started playbook: `https://developer.amd.com/playbooks/lemonade-getting-started/`

## Implementation State

The provider wiring from the first POC added support for:

- `fastflowlm`
- `ollama`
- `lmstudio`
- `lemonade`

Important provider defaults:

| Provider | Base URL | Default model | Auth |
|---|---|---|---|
| FLM | `http://127.0.0.1:52625` | `qwen3.5:4b` | none |
| Ollama | `http://127.0.0.1:11434` | `llama3.2:3b` | none |
| LM Studio | `http://127.0.0.1:1234` | `qwen2.5-3b-instruct` | none |
| Lemonade | `http://127.0.0.1:13305/api/v1` | `Qwen2.5-3B-Instruct-NPU` | bearer `lemonade` |

Key provider implementation files:

- `scripts/ffp_config.py`
- `scripts/ffp_provider_runtime.py`
- `scripts/ffp_provider_status.py`
- `scripts/ffp_benchmark.py`
- `scripts/ffp_chat.py`
- `scripts/grammar_fix.py`
- `scripts/ffp_daemon.py`
- `scripts/ffp_pull.py`
- `scripts/first_run.py`
- `scripts/ui/web/app.js`
- `scripts/ui/web/index.html`

New reproducible benchmark harness:

- `tools/provider_bench.py`
- `tools/lemonade_context_ladder.py`
- `tests/test_provider_bench.py`

Prompt-mode output repair is now implemented in:

- `scripts/ffp_llm_client.py`
- `tests/test_prompt_output_repair.py`

The repair layer handles only deterministic benchmark-proven near-misses:

- Qwen2.5 malformed prompt output with a stray `</context>` and no opening
  `<context>`.
- LM Studio label-style prompt output using `Task:`, `Context:`,
  `Constraints:`, and `Output format:` instead of XML tags.

Harness validation:

```powershell
python -m pytest tests\test_provider_bench.py -q
python -m py_compile tools\provider_bench.py tools\lemonade_context_ladder.py
```

Result:

```text
15 passed
```

Current app-path prompt-repair validation:

```powershell
python -m pytest tests\test_grammar_fix.py tests\test_prompt_output_repair.py tests\test_provider_bench.py -q
python -m py_compile scripts\ffp_llm_client.py scripts\grammar_fix.py tools\provider_bench.py
```

Result:

```text
53 passed
```

Earlier full suite result from the provider-wiring POC:

```powershell
python -m pytest tests -q
```

Result:

```text
342 passed
```

## Benchmark Method

The July 8 rerun uses the committed harness, not the ad-hoc POC script.

Per full cell:

- 8 fixed grammar prompts.
- 10 fixed prompt-conversion prompts.
- 1 discarded warmup per case.
- 5 timed runs per case.
- Median, min, and max reported.
- Output quality scored automatically.
- Memory guard recorded per run.
- Prompt XML pass requires all four required tags in order, no empty sections,
  no thinking residue, and no Markdown fence.
- Grammar pass requires no preamble, length within +/-30 percent, and the
  control sentence essentially unchanged.

Lemonade Matrix-B quick-quality cell:

- 8 grammar prompts.
- 10 prompt-conversion prompts.
- 1 discarded warmup per case.
- 2 timed runs per case.
- Full 5-run speed pass only if prompt quality is promising.

Memory guard:

- A run is flagged if available RAM drops below 3 GiB.
- Paging was also manually checked with `\Memory\Pages/sec`.

Environment controls used:

- AC power confirmed.
- Flowkey AutoHotkey tray process stopped.
- Flowkey Python daemon stopped.
- FLM server stopped when not under test.
- LM Studio API server stopped when not under test.
- Ollama model/service stopped when not under test.
- Lemonade model/server stopped when not under test, except for the Lemonade cell.

## Setup And Preflight Commands

Version checks:

```powershell
flm version --json
ollama --version
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" --version
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" --version
```

Hardware and memory checks:

```powershell
Get-CimInstance Win32_ComputerSystem
Get-CimInstance Win32_Processor
Get-CimInstance Win32_Battery
Get-Counter '\Memory\Available MBytes','\Memory\Pages/sec' -SampleInterval 1 -MaxSamples 3
```

Provider process audit:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match 'Flowkey|AutoHotkey|pythonw|flm|ollama|LM Studio|llama|lemonade|ryzenai' } |
  Select-Object ProcessId,Name,CommandLine |
  Format-List
```

Stop Flowkey/FLM for benchmarking:

```powershell
Stop-Process -Id <AutoHotkey64 pid>,<pythonw daemon pid>,<flm pid> -Force
```

Stop LM Studio:

```powershell
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" unload --all
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" server stop
```

Stop Ollama model/service:

```powershell
ollama stop llama3.2:3b
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match 'ollama' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Stop Lemonade model:

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload all
```

## Model Inventory Observed

Ollama installed:

| Model | Size | Notes |
|---|---:|---|
| `llama3.2:3b` | 2.0 GB disk, 2.6 GB loaded by `ollama ps` | `100% CPU` |
| `qwen2.5:3b` | 1.9 GB disk, 2.2 GB loaded by `ollama ps` | `100% CPU` |
| `llama3.2:1b` | 1.3 GB disk | July 8 Matrix A tested; `0/50` prompt XML |

LM Studio installed:

| Model | File size | Runtime reported by LM Studio |
|---|---:|---:|
| `llama-3.2-1b-instruct` | 1.32 GB | 1.23 GiB |
| `llama-3.2-3b-instruct` | 2.02 GB | 1.88 GiB |
| `qwen2.5-3b-instruct` | 1.93 GB | 1.80 GiB |
| `qwen2.5-7b-instruct` | 4.68 GB | 4.36 GiB |

Lemonade downloaded before/at rerun:

| Model | Size GB | Notes |
|---|---:|---|
| `Qwen2.5-3B-Instruct-NPU` | 4.10 | Matrix A short-task tested; old calibrated long-context later invalidated; NPU-only ladder loses start at 2k |
| `Qwen2.5-7B-Instruct-NPU` | 8.22 | July 8 quick quality tested; failed prompt gate; NPU-only ladder loses start at 2k |
| `Phi-4-mini-instruct-NPU` | 5.21 | July 8 quick quality tested; failed quick gate; NPU-only ladder loses start at 2k |
| `Llama-3.2-1B-Instruct-NPU` | 1.96 | exact Matrix A NPU cell tested after catalog re-check |
| `Llama-3.2-1B-Instruct-Hybrid` | 1.89 | full Matrix A substitution tested before exact `-NPU` cell was pulled |
| `Qwen3-4B-Hybrid` | 5.17 | retested with thinking disabled; short mode passed; NPU-only ladder loses start at 2k and end control fails at 2k+ |
| `Mistral-7B-Instruct-v0.3-NPU` | 8.09 | July 8 quick quality tested; failed prompt gate |
| `CodeLlama-7b-Instruct-hf-NPU` | 7.03 | downloaded, excluded from headline |
| `DeepSeek-R1-Distill-Qwen-7B-NPU` | 8.26 | downloaded, reasoning model, excluded |
| `chatglm3-6b-NPU` | 6.55 | downloaded, excluded as older/weak contract fit |

Still not run from the rerun plan:

- Matrix A Lemonade Llama 3B; the rerun plan correctly lists no Lemonade 3B cell
- optional Matrix B stretch `Meta-Llama-3.1-8B-Instruct-NPU`, intentionally
  skipped for this batch after the Llama matrix failures and RAM-risk review

No longer pending: second-day reproducibility for Lemonade
`Qwen2.5-3B-Instruct-NPU` completed on 2026-07-09. The old evaluator marked the
replace-FLM gate as passed, but the July 9 truncation audit and NPU-only ladder
voided the long-context clauses. Treat the old gate artifact as historical.

## July 8 Corrected Rerun Artifacts

| Artifact | Status | Notes |
|---|---|---|
| `data/benchmarks/rerun_fastflowlm_qwen3.5-4b_turbo_20260708.json` | valid | FLM incumbent, turbo mode |
| `data/benchmarks/rerun_ollama_llama3.2-3b_20260708_memfix.json` | valid | corrected Ollama RSS tracking |
| `data/benchmarks/rerun_lmstudio_qwen2.5-3b-instruct_20260708_cleanmem.json` | valid | clean-memory LM Studio 3B rerun |
| `data/benchmarks/rerun_lmstudio_qwen2.5-7b-instruct_20260708.json` | valid with one timeout | 1 timed prompt run timed out |
| `data/benchmarks/rerun_lmstudio_qwen2.5-7b-instruct_output-repair_20260708.json` | diagnostic | deterministic label-to-XML repair passes 49/49 timed prompt near-misses |
| `data/benchmarks/app_route_prompt_repair_validation_20260708.json` | app-route validation | `grammar_fix.call_flm` validation for Lemonade Qwen2.5 `prompt_plan` and LM Studio Qwen2.5 7B prompt repair |
| `data/benchmarks/rerun_fastflowlm_qwen2.5-it-3b_turbo_20260708.json` | valid | Matrix A Qwen2.5 FLM cell |
| `data/benchmarks/rerun_ollama_qwen2.5-3b_20260708.json` | valid | Matrix A Qwen2.5 Ollama CPU cell |
| `data/benchmarks/rerun_lemonade_qwen2.5-3b-instruct-npu_20260708.json` | valid | Matrix A Qwen2.5 Lemonade NPU cell |
| `data/benchmarks/rerun_lemonade_qwen2.5-3b-instruct-npu_prompt-plan-repair_20260708.json` | diagnostic | deterministic repair and strict retry both fix the `prompt_plan` miss |
| `data/benchmarks/rerun_lemonade_qwen2.5-3b-instruct-npu_longctx_calibrated_20260708.json` | valid | calibrated Matrix C candidate long-context |
| `data/benchmarks/rerun_fastflowlm_qwen3.5-4b_turbo_longctx_calibrated_20260708.json` | valid | calibrated Matrix C FLM incumbent long-context |
| `data/benchmarks/rerun_fastflowlm_llama3.2-1b_turbo_20260708.json` | valid | Matrix A Llama 1B FLM cell |
| `data/benchmarks/rerun_ollama_llama3.2-1b_20260708.json` | valid | Matrix A Llama 1B Ollama CPU cell |
| `data/benchmarks/rerun_lmstudio_llama-3.2-1b-instruct_20260708.json` | valid | Matrix A Llama 1B LM Studio cell |
| `data/benchmarks/rerun_lemonade_llama3.2-1b-instruct-hybrid_20260708.json` | valid with model substitution | Lemonade Hybrid 1B substitution run before exact `-NPU` cell was pulled |
| `data/benchmarks/rerun_lemonade_llama3.2-1b-instruct-npu_20260708.json` | valid | exact Lemonade Llama 1B NPU cell; added after catalog re-check |
| `data/benchmarks/rerun_fastflowlm_llama3.2-3b_turbo_20260708.json` | valid | Matrix A Llama 3B FLM cell |
| `data/benchmarks/rerun_lmstudio_llama-3.2-3b-instruct_20260708.json` | valid | Matrix A Llama 3B LM Studio cell |
| `data/benchmarks/rerun_lemonade_qwen2.5-7b-instruct-npu_quick_20260708.json` | quick-quality only | failed prompt gate, no full 5-run pass |
| `data/benchmarks/rerun_lemonade_phi-4-mini-instruct-npu_quick_20260708.json` | quick-quality only | failed quick gate, no full 5-run pass |
| `data/benchmarks/rerun_lemonade_qwen3-4b-hybrid_no-think_quick_20260708.json` | superseded by full run | passed quick gate and was promoted |
| `data/benchmarks/rerun_lemonade_qwen3-4b-hybrid_no-think_20260708.json` | valid | full short-task Qwen3 Hybrid retest with thinking disabled |
| `data/benchmarks/rerun_lemonade_qwen3-4b-hybrid_no-think_longctx_20260708.json` | valid with workload failure | 4k/8k returned empty scored output |
| `data/benchmarks/rerun_lemonade_qwen3-4b-hybrid_no-think_context-threshold_probe_20260708.json` | diagnostic | direct API shows empty visible output starts between 2055 and 2255 prompt tokens |
| `data/benchmarks/rerun_lemonade_qwen2.5-3b-instruct-npu_longctx_20260708.json` | anomaly | superseded; prompt-size calibration only reached about 6.1k tokens for the 8k label |
| `data/benchmarks/rerun_lemonade_mistral-7b-instruct-v0.3-npu_quick_20260708.json` | quick-quality only | failed prompt gate, no full 5-run pass |
| `data/benchmarks/rerun_lmstudio_qwen2.5-3b-instruct_20260708.json` | anomaly | memory guard fired during initial run |
| `data/benchmarks/rerun_ollama_llama3.2-3b_20260708.json` | superseded | RSS did not include `llama-server.exe` |

## July 9 Second-Day Rerun Artifacts

| Artifact | Status | Notes |
|---|---|---|
| `data/benchmarks/second_day_lemonade_qwen2.5-3b-instruct-npu_20260709.json` | valid | second-day Qwen2.5 short grammar/prompt rerun |
| `data/benchmarks/second_day_lemonade_qwen2.5-3b-instruct-npu_longctx_calibrated_20260709.json` | old-harness result, longctx invalid | second-day Qwen2.5 calibrated long-context rerun; later proved truncated |
| `data/benchmarks/second_day_lemonade_qwen3-4b-hybrid_no-think_20260709.json` | informational | second-day Qwen3 short grammar/prompt rerun with thinking disabled |
| `data/benchmarks/second_day_lemonade_qwen2.5-3b-instruct-npu_gate_20260709.json` | superseded gate record | Old evaluator marked Qwen2.5 replace-FLM gate passed; longctx clauses later voided |
| `data/benchmarks/second_day_lemonade_qwen2.5-3b-instruct-npu_gate_20260709.md` | superseded gate report | Markdown rendering of the old gate result |

## July 9 Second-Day Gate Result (longctx clauses later INVALIDATED)

Qwen2.5 replace-FLM gate: **PASS as evaluated** — superseded by the July 9
Truncation Audit: the `qwen25_longctx_quality` and `qwen25_longctx_sizes` rows
passed on truncated input and are void. Note also that the evaluator never
checked the rerun plan's two speed-vs-FLM clauses (grammar ≤ FLM same day;
8k TTFT ≤ 1.5× FLM); the grammar-speed clause happens to hold on July 8 data,
the 8k TTFT clause is moot.

| Gate | Scope | Result | Observed | Required |
|---|---|---|---|---|
| `qwen25_grammar` | blocking | PASS | `40/40`, rate `1.000`, guard `0` | `>= 0.875` |
| `qwen25_prompt` | blocking | PASS | `45/50`, rate `0.900`, guard `0`, failed case `prompt_plan` | `>= 0.900` |
| `qwen25_longctx_quality` | blocking | PASS | `15/15`, rate `1.000`, guard `0` | all timed long-context runs pass |
| `qwen25_longctx_sizes` | blocking | PASS | `longctx_1000`, `longctx_4000`, `longctx_8000` | all required sizes present |
| `memory_guard` | blocking | PASS | grammar `0`, prompt `0`, long-context `0` | `<= 0` total guard violations |
| `qwen3_grammar` | informational | PASS | `40/40`, rate `1.000`, guard `0` | `>= 0.875` |
| `qwen3_prompt` | informational | PASS | `50/50`, rate `1.000`, guard `0` | `>= 0.900` |

Full cells used 1 warmup plus 5 timed runs per case.

| Provider | Model | Runtime | Task | Pass rate | Median s | TTFT median | Decode TPS median | Prefill TPS median | Median completion tokens | Median prompt tokens | Peak RSS MB | Memory guard | Failed cases |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Lemonade | `Qwen2.5-3B-Instruct-NPU` | NPU | grammar | 40/40 | 1.697 | 0.436 | 17.0723 | 155.0 | 14.0 | 66 | 3733 | no | none |
| Lemonade | `Qwen2.5-3B-Instruct-NPU` | NPU | prompt | 45/50 | 5.462 | 0.514 | 18.3023 | 260.8 | 79.0 | 132 | 3775 | no | `prompt_plan` |
| Lemonade | `Qwen2.5-3B-Instruct-NPU` | NPU | longctx | 15/15 | 14.823 | 2.704 | 18.0708 | 1489.9 | 212.0 | 4042 | 4561 | no | none |
| Lemonade | `Qwen3-4B-Hybrid` | NPU+iGPU hybrid, thinking disabled | grammar | 40/40 | 3.101 | 0.517 | 8.5276 | 136.0 | 17.5 | 68 | 5987 | no | none |
| Lemonade | `Qwen3-4B-Hybrid` | NPU+iGPU hybrid, thinking disabled | prompt | 50/50 | 13.516 | 0.774 | 9.0875 | 175.9 | 111.5 | 135 | 6102 | no | none |

## July 9 Lemonade NPU-Only Rerun

This is the execution of `docs/lemonade-npu-only-bench-plan.md`. It answers the
specific replacement question under the strict condition that FLM is fully off:
no `flm` process, port `52625` closed, Flowkey daemon stopped so it cannot
respawn FLM, and exactly one Lemonade model loaded at a time.

Result: no tested Lemonade model is meetings-eligible. Every model lost the
start-of-input needle at the 2k target. Reloading with `--ctx-size 8192` did not
raise the ceiling. Because Phase 1/2 never reached the 8k start-needle gate,
Phase 3 long-context and short-task reruns were correctly skipped by the plan.

### NPU-Only Controls

| Check | Observed result |
|---|---|
| Flowkey AutoHotkey/daemon stopped | yes |
| FLM process count | `0` before measurements and between models |
| FLM port `52625` | closed / `False` before measurements and between models |
| LM Studio server | stopped |
| Lemonade server | running on `13305`, version `10.9.0` |
| Lemonade loaded models before each model | none, then one model loaded |
| Lemonade loaded models after the run | none |
| Lemonade backend | `ryzenai-llm:npu v1.7.0` installed |
| Memory guard | no violation in any ladder artifact |

### NPU-Only Commands

Preflight and isolation:

```powershell
# Stop Flowkey so it cannot respawn FLM.
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match 'AutoHotkey|pythonw' -and $_.CommandLine -match 'grammarFix|ffp_daemon' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# Stop FLM and verify it is gone.
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match '^flm' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match '^flm' } |
  Select-Object ProcessId,Name

(Test-NetConnection 127.0.0.1 -Port 52625 -WarningAction SilentlyContinue).TcpTestSucceeded

# Stop other provider services.
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" unload --all
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" server stop
ollama stop qwen2.5:3b
ollama stop llama3.2:3b
ollama stop llama3.2:1b

# Reset Lemonade and record backend state.
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload all
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" status
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" --version
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" backends --all
```

Phase 1 default-context ladder, repeated for each model:

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload all
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" load <MODEL>

python tools\lemonade_context_ladder.py `
  --model <MODEL> `
  --quant <ryzenai-llm-npu-or-ryzenai-llm-hybrid> `
  --timeout 300 `
  --out data\benchmarks\lemonade_<model>_context_ladder_20260709.json

& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload all
```

Models run in Phase 1:

```powershell
Qwen2.5-3B-Instruct-NPU
Qwen3-4B-Hybrid
Qwen2.5-7B-Instruct-NPU
Phi-4-mini-instruct-NPU
```

Phase 2 context-remedy investigation:

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" load --help
```

The only relevant option exposed was:

```text
--ctx-size SIZE [-1]        Context size for the model
```

Then each model was reloaded with `--ctx-size 8192` and the same ladder was
rerun:

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload all
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" load --ctx-size 8192 <MODEL>

python tools\lemonade_context_ladder.py `
  --model <MODEL> `
  --quant <ryzenai-llm-npu-or-ryzenai-llm-hybrid> `
  --timeout 300 `
  --out data\benchmarks\lemonade_<model>_context_ladder_ctx8192_20260709.json

& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload all
```

No same-weight `-Hybrid` variants were already downloaded for Qwen2.5 3B,
Qwen2.5 7B, or Phi-4-mini, so the plan's "reuse already-downloaded; do not
re-pull unless missing" rule left those Hybrid checks skipped. The already
downloaded `Qwen3-4B-Hybrid` was tested.

Phase 3 was skipped:

```text
Reason: no Phase 1 or Phase 2 row reached the 8k start-needle gate.
```

### Phase 1 Default Ladder Summary

| Model | Runtime | Largest start needle found | First start needle lost | End-control failures | 8k start gate | TTFT fingerprint | Max RSS GiB | Min available MB | Meetings verdict |
|---|---|---:|---:|---|---|---|---:|---:|---|
| `Qwen2.5-3B-Instruct-NPU` | NPU | 1500 | 2000 | none | fail | flat after 2k | 5.51 | 8698 | no - silent keep-last truncation |
| `Qwen3-4B-Hybrid` | NPU+iGPU hybrid | 1500 | 2000 | 2000, 2500, 3000, 4000, 6000, 8000 | fail | flat after 2k | 6.91 | 7480 | no - loud empty-output failure |
| `Qwen2.5-7B-Instruct-NPU` | NPU | 1500 | 2000 | 1000, 1500, 2500, 4000, 6000, 8000 | fail | flat after 2k | 8.36 | 6066 | no - loses start and unreliable end retrieval |
| `Phi-4-mini-instruct-NPU` | NPU | 1500 | 2000 | none | fail | flat after 2k | 8.24 | 6098 | no - silent keep-last truncation |

### Phase 1 Default Ladder Detail

| Model | Position | Found sizes | Lost sizes | 8k reported prompt tokens | 8k TTFT s |
|---|---|---|---|---:|---:|
| `Qwen2.5-3B-Instruct-NPU` | start | 1000, 1500 | 2000, 2500, 3000, 4000, 6000, 8000 | 9447 | 2.728 |
| `Qwen2.5-3B-Instruct-NPU` | end | 1000, 1500, 2000, 2500, 3000, 4000, 6000, 8000 | none | 9447 | 2.870 |
| `Qwen3-4B-Hybrid` | start | 1000, 1500 | 2000, 2500, 3000, 4000, 6000, 8000 | 9450 | 5.742 |
| `Qwen3-4B-Hybrid` | end | 1000, 1500 | 2000, 2500, 3000, 4000, 6000, 8000 | 9450 | 5.586 |
| `Qwen2.5-7B-Instruct-NPU` | start | 1000, 1500 | 2000, 2500, 3000, 4000, 6000, 8000 | 9447 | 4.577 |
| `Qwen2.5-7B-Instruct-NPU` | end | 2000, 3000 | 1000, 1500, 2500, 4000, 6000, 8000 | 9447 | 4.661 |
| `Phi-4-mini-instruct-NPU` | start | 1000, 1500 | 2000, 2500, 3000, 4000, 6000, 8000 | 8763 | 3.356 |
| `Phi-4-mini-instruct-NPU` | end | 1000, 1500, 2000, 2500, 3000, 4000, 6000, 8000 | none | 8763 | 3.045 |

The critical fingerprint is not only "needle missing." It is needle missing
while Lemonade still reports thousands of submitted `prompt_tokens` and TTFT
stays nearly flat from 2k through 8k. That is the same truncation pattern found
in the original audit.

### Phase 2 `--ctx-size 8192` Ladder Summary

| Model | Largest start needle found | First start needle lost | End-control failures | 8k start gate | 8k start TTFT s | 8k end TTFT s | Outcome |
|---|---:|---:|---|---|---:|---:|---|
| `Qwen2.5-3B-Instruct-NPU` | 1500 | 2000 | none | fail | 3.008 | 2.948 | no improvement |
| `Qwen3-4B-Hybrid` | 1500 | 2000 | 2000, 2500, 3000, 4000, 6000, 8000 | fail | 5.653 | 5.632 | no improvement |
| `Qwen2.5-7B-Instruct-NPU` | 1500 | 2000 | 1000, 1500, 2500, 4000, 6000, 8000 | fail | 4.989 | 4.599 | no improvement |
| `Phi-4-mini-instruct-NPU` | 1500 | 2000 | none | fail | 3.091 | 3.096 | no improvement |

### NPU-Only Artifact Index

| Artifact | Status | Notes |
|---|---|---|
| `tools/lemonade_context_ladder.py` | harness | standalone start/end needle ladder used for Phase 1 and Phase 2 |
| `data/benchmarks/lemonade_qwen2.5-3b-instruct-npu_context_ladder_20260709.json` | valid failure | default load; first start loss at 2k |
| `data/benchmarks/lemonade_qwen3-4b-hybrid_context_ladder_20260709.json` | valid failure | default load; first start loss at 2k, end control fails at 2k+ |
| `data/benchmarks/lemonade_qwen2.5-7b-instruct-npu_context_ladder_20260709.json` | valid failure | default load; first start loss at 2k, noisy end retrieval |
| `data/benchmarks/lemonade_phi-4-mini-instruct-npu_context_ladder_20260709.json` | valid failure | default load; first start loss at 2k |
| `data/benchmarks/lemonade_qwen2.5-3b-instruct-npu_context_ladder_ctx8192_20260709.json` | valid failure | `--ctx-size 8192`; unchanged |
| `data/benchmarks/lemonade_qwen3-4b-hybrid_context_ladder_ctx8192_20260709.json` | valid failure | `--ctx-size 8192`; unchanged |
| `data/benchmarks/lemonade_qwen2.5-7b-instruct-npu_context_ladder_ctx8192_20260709.json` | valid failure | `--ctx-size 8192`; unchanged |
| `data/benchmarks/lemonade_phi-4-mini-instruct-npu_context_ladder_ctx8192_20260709.json` | valid failure | `--ctx-size 8192`; unchanged |

### NPU-Only Decision Gates

| Model | Meetings gate | Short-task evidence | Final role |
|---|---|---|---|
| `Qwen2.5-3B-Instruct-NPU` | fail: 8k start needle not retrieved | July 8 and July 9 short runs both `40/40` grammar, `45/50` prompt | opt-in grammar/prompt only |
| `Qwen3-4B-Hybrid` | fail: 8k start needle not retrieved; end control fails at 2k+ | July 8 and July 9 short runs both `40/40` grammar, `50/50` prompt with thinking disabled | opt-in prompt/grammar experiment only |
| `Qwen2.5-7B-Instruct-NPU` | fail: 8k start needle not retrieved | July 8 quick run `16/16` grammar, `0/20` prompt | no current Flowkey route |
| `Phi-4-mini-instruct-NPU` | fail: 8k start needle not retrieved | July 8 quick run `8/16` grammar, `14/20` prompt | no current Flowkey route |

NPU-only conclusion: Lemonade is not faster or smaller in a way that permits
dropping FLM for meetings. It can be faster for some short tasks, but all tested
Lemonade meeting candidates have a sub-2k effective start-of-input ceiling on
this machine. FLM remains the only measured meetings route.

## July 8 Full Rerun Summary

Full cells used 1 warmup plus 5 timed runs per case.

| Provider | Model | Runtime | Task | Pass rate | Median s | Min s | Max s | Median s/token | Median completion tokens | TTFT median | Peak RSS MB | Min available MB | Memory guard |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| FLM | `qwen3.5:4b` | NPU, turbo | grammar | 40/40 | 3.251 | 2.824 | 4.857 | 0.2155 | 15.0 | 1.208 | 7246 | 6931 | no |
| FLM | `qwen3.5:4b` | NPU, turbo | prompt | 49/50 | 16.661 | 12.052 | 25.732 | 0.0859 | 199.5 | 1.329 | 7248 | 6958 | no |
| Ollama | `llama3.2:3b` | CPU | grammar | 35/40 | 2.065 | 1.660 | 3.764 | 0.1186 | 17.5 | n/a | 4728 | 9321 | no |
| Ollama | `llama3.2:3b` | CPU | prompt | 0/50 | 10.564 | 3.691 | 41.530 | 0.0719 | 148.0 | n/a | 7186 | 6630 | no |
| LM Studio | `qwen2.5-3b-instruct` | Vulkan/iGPU | grammar | 35/40 | 1.427 | 1.133 | 3.344 | 0.0875 | 15.0 | n/a | 1627 | 11551 | no |
| LM Studio | `qwen2.5-3b-instruct` | Vulkan/iGPU | prompt | 0/50 | 3.157 | 2.303 | 9.162 | 0.0416 | 76.0 | n/a | 1690 | 11582 | no |
| LM Studio | `qwen2.5-7b-instruct` | Vulkan/iGPU | grammar | 40/40 | 1.493 | 1.262 | 4.164 | 0.1016 | 14.5 | n/a | 5247 | 6968 | no |
| LM Studio | `qwen2.5-7b-instruct` | Vulkan/iGPU | prompt | 0/49 | 7.026 | 2.672 | 11.968 | 0.0816 | 83.0 | n/a | 5297 | 4798 | no |
| Lemonade | `Qwen3-4B-Hybrid` | NPU+iGPU hybrid, thinking disabled | grammar | 40/40 | 3.580 | 2.731 | 5.691 | 0.1869 | 17.5 | 0.510 | 6135 | 4409 | no |
| Lemonade | `Qwen3-4B-Hybrid` | NPU+iGPU hybrid, thinking disabled | prompt | 50/50 | 14.538 | 11.788 | 17.812 | 0.1294 | 111.5 | 0.734 | 6164 | 4366 | no |

## July 8 Matrix A: Qwen2.5 3B Same-Weight Row

This is the keystone runtime comparison from the rerun plan. Quantization still
differs by runtime, but the model family and parameter class are held constant.

| Provider | Model | Runtime | Task | Pass rate | Median s | Min s | Max s | Median s/token | Median completion tokens | TTFT median | Peak RSS MB | Min available MB | Memory guard |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| FLM | `qwen2.5-it:3b` | NPU, turbo | grammar | 40/40 | 2.441 | 2.102 | 3.410 | 0.1642 | 14.0 | 1.081 | 4197 | 8879 | no |
| FLM | `qwen2.5-it:3b` | NPU, turbo | prompt | 33/50 | 5.434 | 3.947 | 9.445 | 0.0638 | 85.5 | 0.964 | 4200 | 9429 | no |
| Ollama | `qwen2.5:3b` | CPU | grammar | 35/40 | 1.743 | 1.531 | 2.836 | 0.1129 | 15.0 | n/a | 2122 | 11703 | no |
| Ollama | `qwen2.5:3b` | CPU | prompt | 0/50 | 5.290 | 2.948 | 46.140 | 0.0755 | 73.5 | n/a | 2334 | 11578 | no |
| LM Studio | `qwen2.5-3b-instruct` | Vulkan/iGPU | grammar | 35/40 | 1.427 | 1.133 | 3.344 | 0.0875 | 15.0 | n/a | 1627 | 11551 | no |
| LM Studio | `qwen2.5-3b-instruct` | Vulkan/iGPU | prompt | 0/50 | 3.157 | 2.303 | 9.162 | 0.0416 | 76.0 | n/a | 1690 | 11582 | no |
| Lemonade | `Qwen2.5-3B-Instruct-NPU` | NPU | grammar | 40/40 | 1.799 | 1.603 | 2.889 | 0.1258 | 14.0 | 0.455 | 3790 | 10169 | no |
| Lemonade | `Qwen2.5-3B-Instruct-NPU` | NPU | prompt | 45/50 | 5.497 | 4.186 | 11.566 | 0.0686 | 79.0 | 0.540 | 3808 | 9784 | no |

Matrix A decision:

- Lemonade Qwen2.5 3B NPU is the only tested Qwen2.5 runtime that passes the
  short-task replacement quality gate: grammar `40/40`, prompt `45/50`.
- The one prompt failure is concentrated in `prompt_plan` (`0/5`, missing
  `<context>`). The targeted diagnostic below shows both deterministic repair
  and strict retry can recover it. The app-route validation below shows the
  implemented deterministic repair fixes it without a second model call.
- Ollama and LM Studio are faster or smaller in some short cases, but both fail
  prompt XML completely for this model family.

Qwen2.5 `prompt_plan` repair/retry diagnostic:

| Policy | Pass rate | Median wall s | Median TTFT s | Median completion tokens | Peak RSS MB | Notes |
|---|---:|---:|---:|---:|---:|---|
| Original prompt | 0/5 | 4.319 | 0.557 | 57 | 3808 | emitted `</context>` without opening `<context>` |
| Deterministic repair | 5/5 | no extra model call | n/a | n/a | n/a | replace first stray `</context>` with `<context>` when no opening context tag exists |
| Strict retry prompt | 5/5 | 11.009 | 0.545 | 173 | 3910 | works, but adds a second generation call |

Interpretation: use deterministic repair first for this specific malformed-tag
case, then fall back to model retry only if repair still fails the contract.
The July 9 second-day gate passed with this repair path available for the known
`prompt_plan` miss.

LM Studio Qwen2.5 7B output-repair diagnostic:

| Policy | Pass rate | Extra model calls | Source median wall s | Notes |
|---|---:|---:|---:|---|
| Original prompt | 0/49 | 0 | 7.026 | used `Task:`, `Context:`, `Constraints:`, `Output format:` labels instead of XML tags |
| Deterministic label-to-XML repair | 49/49 | 0 | unchanged | converts labeled sections to `<task>`, `<context>`, `<constraints>`, `<output_format>` |

Interpretation: LM Studio Qwen2.5 7B remains an experimental route, not a
production prompt replacement. It is the most interesting fast non-NPU prompt
candidate now that the app route validates the repair layer.

App-level prompt-repair validation:

| Provider | Model | Scope | Pass rate | Median wall s | Min s | Max s | Repair runs | Strict retry runs | Notes |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| Lemonade | `Qwen2.5-3B-Instruct-NPU` | `prompt_plan`, 1 warmup + 5 timed app calls | 5/5 | 3.682 | 3.629 | 4.101 | 5/5 | 0/5 | deterministic malformed-context repair; no extra model call |
| LM Studio | `qwen2.5-7b-instruct` | all 10 prompt cases, 1 warmup + 1 timed app call per case | 10/10 | 7.245 | 2.983 | 17.898 | 10/10 | 0/10 | deterministic label-to-XML repair; no extra model call |

The first LM Studio app-route run exposed an anti-echo bug: a complete repaired
prompt could be retried because its `<task>` line overlapped the user's input.
The app now checks complete XML scaffold validity before anti-echo retry, with a
regression test covering that case.

Route decision: keep LM Studio Qwen2.5 7B as a supported experimental opt-in
prompt route. Do not make it the default and do not automatically route prompt
mode to it yet: it is non-NPU, one original full-run prompt call timed out, no
long-context gate was run, and no second-day route validation exists.

Stretch decision: do not pull `Meta-Llama-3.1-8B-Instruct-NPU` in this batch.
All tested Llama 3.2 1B/3B prompt rows failed XML at `0/50`, while the leading
Qwen2.5 candidate already clears the quality and long-context gates pending
second-day reproducibility. Pulling a 9.30 GB Llama stretch model adds RAM/disk
risk without a strong path to changing the current routing decision.

## July 8 Matrix A: Llama 3.2 Rows

The Llama rows were added after the Qwen2.5 keystone row. They answer the
question "why not just use Llama 3.2 3B?" directly: all tested Llama cells failed
the Flowkey prompt XML contract. Quantization differs by runtime as in the
Qwen2.5 row.

### Llama 3.2 1B

The first Llama 1B Lemonade pass used `Llama-3.2-1B-Instruct-Hybrid` as a
substitution. A later catalog re-check exposed `Llama-3.2-1B-Instruct-NPU`, so
the exact NPU cell was pulled and tested too.

| Provider | Model | Runtime | Task | Pass rate | Median s | Min s | Max s | Median s/token | Median completion tokens | TTFT median | Peak RSS MB | Min available MB | Memory guard |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| FLM | `llama3.2:1b` | NPU, turbo | grammar | 36/40 | 1.338 | 1.198 | 1.748 | 0.0917 | 14.0 | 0.472 | 5833 | 7613 | no |
| FLM | `llama3.2:1b` | NPU, turbo | prompt | 0/50 | 2.930 | 1.588 | 14.296 | 0.0268 | 107.0 | 0.464 | 5834 | 7689 | no |
| Ollama | `llama3.2:1b` | CPU | grammar | 29/40 | 1.634 | 1.320 | 3.354 | 0.0943 | 15.5 | n/a | 1536 | 12354 | no |
| Ollama | `llama3.2:1b` | CPU | prompt | 0/50 | 5.906 | 3.842 | 30.586 | 0.0513 | 112.0 | n/a | 1986 | 11269 | no |
| LM Studio | `llama-3.2-1b-instruct` | Vulkan/iGPU | grammar | 30/40 | 0.938 | 0.809 | 1.721 | 0.0568 | 16.5 | n/a | 1870 | 12363 | no |
| LM Studio | `llama-3.2-1b-instruct` | Vulkan/iGPU | prompt | 0/50 | 3.723 | 0.756 | 14.912 | 0.0259 | 149.0 | n/a | 2350 | 11043 | no |
| Lemonade | `Llama-3.2-1B-Instruct-NPU` | NPU | grammar | 25/40 | 1.227 | 0.959 | 1.562 | 0.0589 | 21.0 | 0.198 | 2614 | 10427 | no |
| Lemonade | `Llama-3.2-1B-Instruct-NPU` | NPU | prompt | 0/50 | 3.640 | 2.794 | 9.063 | 0.0274 | 129.0 | 0.235 | 2649 | 10547 | no |
| Lemonade | `Llama-3.2-1B-Instruct-Hybrid` | NPU+iGPU hybrid | grammar | 0/40 | 5.049 | 1.685 | 5.551 | 0.0320 | 160.0 | 0.236 | 2272 | 10356 | no |
| Lemonade | `Llama-3.2-1B-Instruct-Hybrid` | NPU+iGPU hybrid | prompt | 0/50 | 7.462 | 3.905 | 26.092 | 0.0372 | 204.0 | 0.257 | 2278 | 10734 | no |

### Llama 3.2 3B

The plan has no Lemonade 3B Llama cell because Lemonade did not list a
Llama-3.2-3B NPU model.

| Provider | Model | Runtime | Task | Pass rate | Median s | Min s | Max s | Median s/token | Median completion tokens | TTFT median | Peak RSS MB | Min available MB | Memory guard |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| FLM | `llama3.2:3b` | NPU, turbo | grammar | 35/40 | 2.972 | 2.491 | 4.261 | 0.1798 | 16.0 | 1.043 | 10282 | 3456 | no |
| FLM | `llama3.2:3b` | NPU, turbo | prompt | 0/50 | 11.853 | 4.046 | 20.860 | 0.0827 | 141.0 | 1.113 | 10163 | 3293 | no |
| Ollama | `llama3.2:3b` | CPU | grammar | 35/40 | 2.065 | 1.660 | 3.764 | 0.1186 | 17.5 | n/a | 4728 | 9321 | no |
| Ollama | `llama3.2:3b` | CPU | prompt | 0/50 | 10.564 | 3.691 | 41.530 | 0.0719 | 148.0 | n/a | 7186 | 6630 | no |
| LM Studio | `llama-3.2-3b-instruct` | Vulkan/iGPU | grammar | 35/40 | 1.260 | 1.057 | 3.015 | 0.0718 | 17.5 | n/a | 2560 | 10803 | no |
| LM Studio | `llama-3.2-3b-instruct` | Vulkan/iGPU | prompt | 0/50 | 7.695 | 3.498 | 21.294 | 0.0457 | 168.0 | n/a | 3960 | 9155 | no |

Llama Matrix A decision:

- No Llama 3.2 cell is eligible for prompt-mode routing; every prompt row scored
  `0/50`.
- The exact Lemonade Llama 1B NPU cell is faster than the Hybrid substitution,
  but still fails prompt XML completely and misses the grammar quality gate.
- LM Studio Llama 1B was the fastest grammar row in the whole rerun (`0.938s`)
  but grammar quality was only `30/40`.
- FLM Llama 3B had high RSS for a 3B model and left only about `3.3 GB`
  available RAM during prompt runs, so it is not attractive versus Qwen2.5.

## July 8 Matrix C: Calibrated Long-Context

The first Lemonade long-context artifact under-targeted the 8k label, so the
harness was recalibrated and the valid comparison below uses actual prompt-token
counts near 1k/4k/8k.

| Provider | Model | Prompt tokens median | Pass rate | Median wall s | Min s | Max s | TTFT median s | Median s/token | Median completion tokens | Peak RSS MB | Min available MB | Memory guard |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Lemonade | `Qwen2.5-3B-Instruct-NPU` | 1059 | 5/5 | 11.203 | 10.951 | 11.842 | 1.534 | 0.0675 | 166 | 5181 | 8525 | no |
| Lemonade | `Qwen2.5-3B-Instruct-NPU` | 4042 | 5/5 | 15.488 | 15.297 | 15.899 | 2.828 | 0.0704 | 220 | 4585 | 9253 | no |
| Lemonade | `Qwen2.5-3B-Instruct-NPU` | 8022 | 5/5 | 15.162 | 14.966 | 15.394 | 2.831 | 0.0715 | 212 | 4585 | 9282 | no |
| Lemonade | `Qwen3-4B-Hybrid` | 1062 | 5/5 | 17.518 | 17.363 | 17.832 | 2.904 | 0.1485 | 118 | 1377 | 4918 | no |
| Lemonade | `Qwen3-4B-Hybrid` | 4045 | 0/5 | 32.995 | 32.629 | 33.413 | 5.221 | 0.1500 | 220 | 1640 | 4586 | no |
| Lemonade | `Qwen3-4B-Hybrid` | 8025 | 0/5 | 32.845 | 32.271 | 32.998 | 5.126 | 0.1493 | 220 | 1641 | 4637 | no |
| FLM | `qwen3.5:4b` | 1064 | 5/5 | 19.802 | 19.592 | 21.084 | 3.872 | 0.1088 | 188 | 7419 | 5562 | no |
| FLM | `qwen3.5:4b` | 4047 | 5/5 | 29.435 | 26.510 | 33.655 | 11.367 | 0.1498 | 193 | 7889 | 4188 | no |
| FLM | `qwen3.5:4b` | 8027 | 5/5 | 42.170 | 39.011 | 44.091 | 22.697 | 0.2130 | 207 | 7901 | 3669 | no |

Long-context gate (CORRECTED July 9 — see Truncation Audit):

- INVALID: the apparent 8k TTFT win for Lemonade Qwen2.5 3B NPU (`2.831s` vs
  FLM `22.697s`) compared ~2-3k tokens of real prefill against FLM's honest 8k.
  The identical 4k/8k TTFT across both days' artifacts was the tell — prefill
  is never free.
- Lemonade Qwen3 4B Hybrid did not clear the meetings workload despite good
  short-mode quality: the 4k and 8k cells returned empty scored output.
- The Qwen3 Hybrid failure is not a harness scoring artifact. A direct Lemonade
  API threshold probe returned visible content at `2055` prompt tokens and empty
  `message.content` from `2255` prompt tokens onward while still reporting
  nonzero completion tokens.
- CORRECTED: a context cap WAS hit for Lemonade Qwen2.5 3B NPU — silently. The
  July 9 needle probe shows keep-last truncation from roughly 3.3k prompt
  tokens onward. FLM showed honest TTFT scaling through 8k. Both Lemonade
  models on this machine have a ~2-3k effective-context limit: Qwen3 fails
  loudly (empty output), Qwen2.5 fails silently (truncation).
- No memory guard fired for the calibrated long-context cells.
- Per the rerun plan's own rule (a context cap below 8k is disqualifying for
  the meetings workload), Lemonade Qwen2.5 3B NPU is DISQUALIFIED for meetings
  as configured; the routing gate applies instead of replacement.

Qwen3 Hybrid threshold probe:

| Target prompt tokens | Actual prompt tokens | Content length | Completion tokens | TTFT s | Result |
|---:|---:|---:|---:|---:|---|
| 1800 | 1856 | 447 | 80 | 5.330 | visible output |
| 2000 | 2055 | 414 | 80 | 5.382 | visible output |
| 2200 | 2255 | 0 | 80 | 5.366 | empty content |
| 2400 | 2453 | 0 | 80 | 5.564 | empty content |
| 2600 | 2652 | 0 | 80 | 5.175 | empty content |
| 2800 | 2851 | 0 | 80 | 5.129 | empty content |
| 3000 | 3052 | 0 | 80 | 7.799 | empty content |

Interpretation: Qwen3 Hybrid remains eligible only for short prompt/grammar
experiments until Lemonade or the model configuration fixes this visible-output
failure.

Ollama native timing metadata from the corrected run:

| Task | Median native prefill tok/s | Median native decode tok/s |
|---|---:|---:|
| grammar | 1297.60 | 16.58 |
| prompt | 2386.27 | 15.82 |

Ollama process observation:

```text
ollama ps: llama3.2:3b, 2.6 GB loaded, PROCESSOR 100% CPU, context 4096
```

## July 8 Lemonade Matrix B Quick Quality

These were 1-warmup/2-run quick-quality cells. Only Qwen3 Hybrid passed the
quick gate and was promoted to a full 5-run short benchmark.

| Provider | Model | Runtime | Task | Pass rate | Median s | Min s | Max s | Median s/token | Median completion tokens | TTFT median | Peak RSS MB | Min available MB | Memory guard |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Lemonade | `Qwen2.5-7B-Instruct-NPU` | NPU | grammar | 16/16 | 3.025 | 2.566 | 4.707 | 0.2138 | 13.5 | 0.952 | 6626 | 4893 | no |
| Lemonade | `Qwen2.5-7B-Instruct-NPU` | NPU | prompt | 0/20 | 7.967 | 6.111 | 14.868 | 0.1220 | 64.0 | 1.020 | 6660 | 5103 | no |
| Lemonade | `Phi-4-mini-instruct-NPU` | NPU | grammar | 8/16 | 2.555 | 2.425 | 14.136 | 0.1753 | 14.5 | 0.651 | 5746 | 5456 | no |
| Lemonade | `Phi-4-mini-instruct-NPU` | NPU | prompt | 14/20 | 13.607 | 10.493 | 51.561 | 0.0859 | 159.0 | 0.625 | 5782 | 5373 | no |
| Lemonade | `Mistral-7B-Instruct-v0.3-NPU` | NPU | grammar | 10/16 | 3.406 | 2.355 | 6.650 | 0.1512 | 22.5 | 0.796 | 6212 | 7439 | no |
| Lemonade | `Mistral-7B-Instruct-v0.3-NPU` | NPU | prompt | 0/20 | 24.874 | 18.398 | 43.060 | 0.0909 | 271.5 | 0.857 | 6227 | 7488 | no |
| Lemonade | `Qwen3-4B-Hybrid` | NPU+iGPU hybrid, thinking disabled | grammar | 16/16 | 3.109 | 2.605 | 5.764 | 0.1728 | 17.5 | 0.478 | 6036 | 6312 | no |
| Lemonade | `Qwen3-4B-Hybrid` | NPU+iGPU hybrid, thinking disabled | prompt | 20/20 | 16.270 | 11.532 | 19.123 | 0.1345 | 111.5 | 0.792 | 6151 | 5476 | no |

Quick-gate decisions:

- Qwen3 Hybrid advanced to full 5-run short timing because it passed `20/20`
  prompt XML and `16/16` grammar.
- Qwen2.5 7B did not advance: grammar passed, but prompt was `0/20` and all
  prompt runs were missing the required XML tags.
- Phi-4-mini did not advance: prompt was `14/20`, grammar was `8/16`, and the
  prompt median was slower than Qwen2.5 3B.
- Mistral did not advance: prompt was `0/20` and grammar was only `10/16`.

## Exact Rerun Commands

### LM Studio 3B

```powershell
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" server start --port 1234
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" load qwen2.5-3b-instruct --gpu max --context-length 4096 --identifier qwen2.5-3b-instruct -y

python tools\provider_bench.py `
  --provider lmstudio `
  --base-url http://127.0.0.1:1234 `
  --model qwen2.5-3b-instruct `
  --quant Q4_K_M `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 180 `
  --out data\benchmarks\rerun_lmstudio_qwen2.5-3b-instruct_20260708_cleanmem.json
```

### LM Studio 7B

```powershell
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" unload --all
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" load qwen2.5-7b-instruct --gpu max --context-length 4096 --identifier qwen2.5-7b-instruct -y

python tools\provider_bench.py `
  --provider lmstudio `
  --base-url http://127.0.0.1:1234 `
  --model qwen2.5-7b-instruct `
  --quant Q4_K_M `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 240 `
  --out data\benchmarks\rerun_lmstudio_qwen2.5-7b-instruct_20260708.json
```

### LM Studio 7B Output-Repair Diagnostic

```powershell
# Reads the original LM Studio 7B artifact and converts labeled near-miss
# sections into the required XML tags. No model call is made.
@'
import json, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path('tools').resolve()))
import provider_bench as pb

src = Path('data/benchmarks/rerun_lmstudio_qwen2.5-7b-instruct_20260708.json')
out = Path('data/benchmarks/rerun_lmstudio_qwen2.5-7b-instruct_output-repair_20260708.json')
data = json.loads(src.read_text(encoding='utf-8'))

def repair_labeled_prompt(text):
    visible, _had, _unclosed = pb.strip_thinking(text)
    specs = [
        ('task', r'(?:\*\*)?task(?:\*\*)?\s*:\s*'),
        ('context', r'(?:\*\*)?context(?:\*\*)?\s*:\s*'),
        ('constraints', r'(?:\*\*)?constraints(?:\*\*)?\s*:\s*'),
        ('output_format', r'(?:\*\*)?output\s*format(?:\*\*)?\s*:\s*'),
    ]
    matches = []
    cursor = 0
    for tag, pattern in specs:
        match = re.search(pattern, visible[cursor:], flags=re.I)
        if not match:
            return visible
        start = cursor + match.start()
        end = cursor + match.end()
        matches.append((tag, start, end))
        cursor = end
    pieces = {}
    for index, (tag, _start, end) in enumerate(matches):
        next_start = matches[index + 1][1] if index + 1 < len(matches) else len(visible)
        value = visible[end:next_start].strip()
        if tag == 'output_format' and re.fullmatch(r'```[a-zA-Z0-9_-]*\s*```', value, flags=re.S):
            lang = (re.match(r'```([a-zA-Z0-9_-]*)', value).group(1) or 'fenced')
            value = f'{lang} fenced code block'
        else:
            value = re.sub(r'```[a-zA-Z0-9_-]*', '', value).replace('```', '').strip() or value
        pieces[tag] = value
    return '\n'.join(f'<{tag}>\n{pieces[tag]}\n</{tag}>' for tag, _start, _end in matches)

runs = []
for case in data['cases']:
    if case['task'] != 'prompt':
        continue
    for run in case['runs']:
        if run.get('warmup') or run.get('error'):
            continue
        repaired = repair_labeled_prompt(run.get('raw_output') or '')
        runs.append({
            'case_id': case['case_id'],
            'source_run_index': run.get('run_index'),
            'source_contract': run.get('contract'),
            'repaired_output': repaired,
            'repaired_contract': pb.check_prompt_contract(repaired),
        })

artifact = {
    'schema_version': 1,
    'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
    'provider': 'lmstudio',
    'model': data.get('model'),
    'purpose': 'diagnose deterministic output repair for LM Studio Qwen2.5 7B prompt near-misses',
    'source_artifact': str(src),
    'summary': {
        'timed_runs': len(runs),
        'pass_count_after_repair': sum(1 for run in runs if run['repaired_contract'].get('pass')),
        'extra_model_calls': 0,
    },
    'runs': runs,
}
out.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
'@ | python -
```

### FLM Incumbent

```powershell
$p = Start-Process -FilePath flm `
  -ArgumentList @('serve','qwen3.5:4b','--pmode','turbo','--host','127.0.0.1','--port','52625') `
  -WindowStyle Hidden `
  -PassThru

python tools\provider_bench.py `
  --provider fastflowlm `
  --base-url http://127.0.0.1:52625 `
  --model qwen3.5:4b `
  --quant FLM_NPU `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_fastflowlm_qwen3.5-4b_turbo_20260708.json
```

### Ollama `llama3.2:3b`

```powershell
ollama pull llama3.2:3b

python tools\provider_bench.py `
  --provider ollama `
  --base-url http://127.0.0.1:11434 `
  --model llama3.2:3b `
  --quant Q4_0_ollama `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_ollama_llama3.2-3b_20260708_memfix.json
```

### Matrix A Llama 3.2 Rows

```powershell
# Model prep
flm pull llama3.2:1b
flm pull llama3.2:3b
ollama pull llama3.2:1b
ollama pull llama3.2:3b
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" get "https://huggingface.co/lmstudio-community/Llama-3.2-1B-Instruct-GGUF" --gguf -y
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" get "https://huggingface.co/lmstudio-community/Llama-3.2-3B-Instruct-GGUF" --gguf -y

# FLM Llama 1B
$p = Start-Process -FilePath flm `
  -ArgumentList @('serve','llama3.2:1b','--pmode','turbo','--host','127.0.0.1','--port','52625') `
  -WindowStyle Hidden `
  -PassThru

python tools\provider_bench.py `
  --provider fastflowlm `
  --base-url http://127.0.0.1:52625 `
  --model llama3.2:1b `
  --quant FLM_NPU_Q4NX `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_fastflowlm_llama3.2-1b_turbo_20260708.json

Stop-Process -Id $p.Id -Force

# FLM Llama 3B
$p = Start-Process -FilePath flm `
  -ArgumentList @('serve','llama3.2:3b','--pmode','turbo','--host','127.0.0.1','--port','52625') `
  -WindowStyle Hidden `
  -PassThru

python tools\provider_bench.py `
  --provider fastflowlm `
  --base-url http://127.0.0.1:52625 `
  --model llama3.2:3b `
  --quant FLM_NPU_Q4NX `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_fastflowlm_llama3.2-3b_turbo_20260708.json

Stop-Process -Id $p.Id -Force

# Ollama Llama 1B
python tools\provider_bench.py `
  --provider ollama `
  --base-url http://127.0.0.1:11434 `
  --model llama3.2:1b `
  --quant Q8_0_ollama `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_ollama_llama3.2-1b_20260708.json

ollama stop llama3.2:1b

# LM Studio Llama 1B
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" server start --port 1234
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" load llama-3.2-1b-instruct --gpu max --context-length 4096 --identifier llama-3.2-1b-instruct -y

python tools\provider_bench.py `
  --provider lmstudio `
  --base-url http://127.0.0.1:1234 `
  --model llama-3.2-1b-instruct `
  --quant Q8_0_GGUF `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_lmstudio_llama-3.2-1b-instruct_20260708.json

& "$env:USERPROFILE\.lmstudio\bin\lms.exe" unload --all

# LM Studio Llama 3B
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" load llama-3.2-3b-instruct --gpu max --context-length 4096 --identifier llama-3.2-3b-instruct -y

python tools\provider_bench.py `
  --provider lmstudio `
  --base-url http://127.0.0.1:1234 `
  --model llama-3.2-3b-instruct `
  --quant Q4_K_M_GGUF `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_lmstudio_llama-3.2-3b-instruct_20260708.json

& "$env:USERPROFILE\.lmstudio\bin\lms.exe" unload --all

# Lemonade Llama 1B Hybrid substitution
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" load Llama-3.2-1B-Instruct-Hybrid

python tools\provider_bench.py `
  --provider lemonade `
  --base-url http://127.0.0.1:13305/api/v1 `
  --bearer lemonade `
  --model Llama-3.2-1B-Instruct-Hybrid `
  --quant ryzenai-llm-hybrid `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_lemonade_llama3.2-1b-instruct-hybrid_20260708.json

& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload Llama-3.2-1B-Instruct-Hybrid

# Lemonade Llama 1B exact NPU cell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" pull Llama-3.2-1B-Instruct-NPU
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" load Llama-3.2-1B-Instruct-NPU

python tools\provider_bench.py `
  --provider lemonade `
  --base-url http://127.0.0.1:13305/api/v1 `
  --bearer lemonade `
  --model Llama-3.2-1B-Instruct-NPU `
  --quant ryzenai-llm-npu `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_lemonade_llama3.2-1b-instruct-npu_20260708.json

& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload Llama-3.2-1B-Instruct-NPU
```

### Matrix A FLM Qwen2.5 3B

```powershell
flm pull qwen2.5-it:3b

$p = Start-Process -FilePath flm `
  -ArgumentList @('serve','qwen2.5-it:3b','--pmode','turbo','--host','127.0.0.1','--port','52625') `
  -WindowStyle Hidden `
  -PassThru

python tools\provider_bench.py `
  --provider fastflowlm `
  --base-url http://127.0.0.1:52625 `
  --model qwen2.5-it:3b `
  --quant FLM_NPU_Q4NX `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_fastflowlm_qwen2.5-it-3b_turbo_20260708.json
```

### Matrix A Ollama Qwen2.5 3B

```powershell
ollama pull qwen2.5:3b

python tools\provider_bench.py `
  --provider ollama `
  --base-url http://127.0.0.1:11434 `
  --model qwen2.5:3b `
  --quant Q4_K_M_ollama `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_ollama_qwen2.5-3b_20260708.json
```

### Matrix A Lemonade Qwen2.5 3B NPU

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" pull Qwen2.5-3B-Instruct-NPU
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" load Qwen2.5-3B-Instruct-NPU

python tools\provider_bench.py `
  --provider lemonade `
  --base-url http://127.0.0.1:13305/api/v1 `
  --bearer lemonade `
  --model Qwen2.5-3B-Instruct-NPU `
  --quant ryzenai-llm-npu `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_lemonade_qwen2.5-3b-instruct-npu_20260708.json
```

### Qwen2.5 Prompt Plan Repair/Retry Diagnostic

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" load Qwen2.5-3B-Instruct-NPU

# The diagnostic reads the original Matrix A artifact, applies a deterministic
# missing-context-tag repair to the stored failing outputs, then runs a stricter
# 1-warmup/5-run retry prompt for the same prompt_plan case.
@'
import json, re, sys, time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path('tools').resolve()))
import provider_bench as pb

source = json.loads(Path('data/benchmarks/rerun_lemonade_qwen2.5-3b-instruct-npu_20260708.json').read_text(encoding='utf-8'))
source_case = next(c for c in source['cases'] if c['case_id'] == 'prompt_plan')

def repair_missing_context_opening(text):
    visible, _had, _unclosed = pb.strip_thinking(text)
    if '<context' not in visible.lower() and re.search(r'</context\s*>', visible, flags=re.I):
        return re.sub(r'</context\s*>', '<context>', visible, count=1, flags=re.I), True
    return visible, False

repair_runs = []
for run in source_case['runs']:
    if run.get('warmup'):
        continue
    repaired, changed = repair_missing_context_opening(run.get('raw_output') or '')
    repair_runs.append({
        'source_run_index': run.get('run_index'),
        'changed': changed,
        'repaired_output': repaired,
        'repaired_contract': pb.check_prompt_contract(repaired),
    })

strict_system = pb.PROMPT_SYSTEM + (
    '\n\nFailure correction for retry: the answer must contain the literal '
    'opening tags <task>, <context>, <constraints>, and <output_format>, in '
    'that exact order. Do not use a closing tag such as </context> unless the '
    'matching opening tag has already appeared.'
)
case = pb.BenchCase('prompt_plan_strict_retry', 'prompt', strict_system, source_case['user_prompt'], source_case['max_tokens'])
args = SimpleNamespace(
    provider='lemonade',
    base_url='http://127.0.0.1:13305/api/v1',
    bearer='lemonade',
    model='Qwen2.5-3B-Instruct-NPU',
    temperature=0.1,
    timeout=300,
    disable_thinking=False,
)
runs = [pb.timed_run(args, case, i + 1, i == 0, pb.DEFAULT_PROCESS_NAMES['lemonade']) for i in range(6)]

artifact = {
    'schema_version': 1,
    'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
    'provider': 'lemonade',
    'model': 'Qwen2.5-3B-Instruct-NPU',
    'purpose': 'diagnose targeted retry and deterministic repair for prompt_plan',
    'source_artifact': 'data/benchmarks/rerun_lemonade_qwen2.5-3b-instruct-npu_20260708.json',
    'deterministic_repair': {
        'policy': 'If output has </context> but no <context opening tag, replace the first </context> with <context>.',
        'runs': repair_runs,
    },
    'strict_retry': {
        'system_prompt': strict_system,
        'summary': pb.summarize_runs(runs),
        'runs': runs,
    },
}
Path('data/benchmarks/rerun_lemonade_qwen2.5-3b-instruct-npu_prompt-plan-repair_20260708.json').write_text(
    json.dumps(artifact, indent=2, ensure_ascii=False) + '\n',
    encoding='utf-8',
)
'@ | python -
```

### Matrix C Calibrated Long-Context

```powershell
python tools\provider_bench.py `
  --provider lemonade `
  --base-url http://127.0.0.1:13305/api/v1 `
  --bearer lemonade `
  --model Qwen2.5-3B-Instruct-NPU `
  --quant ryzenai-llm-npu `
  --tasks longctx `
  --longctx-sizes 1000,4000,8000 `
  --runs 5 `
  --warmup 1 `
  --timeout 600 `
  --out data\benchmarks\rerun_lemonade_qwen2.5-3b-instruct-npu_longctx_calibrated_20260708.json

python tools\provider_bench.py `
  --provider fastflowlm `
  --base-url http://127.0.0.1:52625 `
  --model qwen3.5:4b `
  --quant FLM_NPU `
  --tasks longctx `
  --longctx-sizes 1000,4000,8000 `
  --runs 5 `
  --warmup 1 `
  --timeout 900 `
  --out data\benchmarks\rerun_fastflowlm_qwen3.5-4b_turbo_longctx_calibrated_20260708.json

& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" load Qwen3-4B-Hybrid

python tools\provider_bench.py `
  --provider lemonade `
  --base-url http://127.0.0.1:13305/api/v1 `
  --bearer lemonade `
  --model Qwen3-4B-Hybrid `
  --quant ryzenai-llm-hybrid `
  --tasks longctx `
  --longctx-sizes 1000,4000,8000 `
  --runs 5 `
  --warmup 1 `
  --timeout 900 `
  --disable-thinking `
  --out data\benchmarks\rerun_lemonade_qwen3-4b-hybrid_no-think_longctx_20260708.json
```

### Lemonade Qwen2.5 7B NPU Quick Quality

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" load Qwen2.5-7B-Instruct-NPU

python tools\provider_bench.py `
  --provider lemonade `
  --base-url http://127.0.0.1:13305/api/v1 `
  --bearer lemonade `
  --model Qwen2.5-7B-Instruct-NPU `
  --quant ryzenai-llm-npu `
  --tasks grammar,prompt `
  --runs 2 `
  --warmup 1 `
  --timeout 600 `
  --out data\benchmarks\rerun_lemonade_qwen2.5-7b-instruct-npu_quick_20260708.json
```

### Lemonade Phi-4 Mini NPU Quick Quality

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" load Phi-4-mini-instruct-NPU

python tools\provider_bench.py `
  --provider lemonade `
  --base-url http://127.0.0.1:13305/api/v1 `
  --bearer lemonade `
  --model Phi-4-mini-instruct-NPU `
  --quant ryzenai-llm-npu `
  --tasks grammar,prompt `
  --runs 2 `
  --warmup 1 `
  --timeout 600 `
  --out data\benchmarks\rerun_lemonade_phi-4-mini-instruct-npu_quick_20260708.json
```

### Lemonade Mistral 7B NPU Quick Quality

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" load Mistral-7B-Instruct-v0.3-NPU

python tools\provider_bench.py `
  --provider lemonade `
  --base-url http://127.0.0.1:13305/api/v1 `
  --bearer lemonade `
  --model Mistral-7B-Instruct-v0.3-NPU `
  --quant ryzenai-llm-npu `
  --tasks grammar,prompt `
  --runs 2 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_lemonade_mistral-7b-instruct-v0.3-npu_quick_20260708.json
```

### Lemonade Qwen3 4B Hybrid With Thinking Disabled

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" load Qwen3-4B-Hybrid

python tools\provider_bench.py `
  --provider lemonade `
  --base-url http://127.0.0.1:13305/api/v1 `
  --bearer lemonade `
  --model Qwen3-4B-Hybrid `
  --quant ryzenai-llm-hybrid `
  --tasks grammar,prompt `
  --runs 2 `
  --warmup 1 `
  --timeout 600 `
  --disable-thinking `
  --out data\benchmarks\rerun_lemonade_qwen3-4b-hybrid_no-think_quick_20260708.json

python tools\provider_bench.py `
  --provider lemonade `
  --base-url http://127.0.0.1:13305/api/v1 `
  --bearer lemonade `
  --model Qwen3-4B-Hybrid `
  --quant ryzenai-llm-hybrid `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 600 `
  --disable-thinking `
  --out data\benchmarks\rerun_lemonade_qwen3-4b-hybrid_no-think_20260708.json
```

### Lemonade Qwen3 Long-Context Threshold Probe

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" load Qwen3-4B-Hybrid

@'
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path('tools').resolve()))
import provider_bench as pb

url = 'http://127.0.0.1:13305/api/v1/chat/completions'
headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer lemonade'}
results = []
for target in [1800, 2000, 2200, 2400, 2600, 2800, 3000]:
    prompt = pb.long_context_prompt(target)
    body = {
        'model': 'Qwen3-4B-Hybrid',
        'messages': [
            {'role': 'system', 'content': pb.LONGCTX_SYSTEM + '\n/no_think'},
            {'role': 'user', 'content': prompt},
        ],
        'temperature': 0.1,
        'max_tokens': 80,
        'stream': False,
        'chat_template_kwargs': {'enable_thinking': False},
    }
    started = time.perf_counter()
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    wall = time.perf_counter() - started
    msg = data.get('choices', [{}])[0].get('message', {})
    content = msg.get('content') or ''
    usage = data.get('usage') or {}
    results.append({
        'target_prompt_tokens': target,
        'wall_seconds': round(wall, 3),
        'prompt_tokens': usage.get('prompt_tokens'),
        'completion_tokens': usage.get('completion_tokens'),
        'ttft_seconds': usage.get('prefill_duration_ttft'),
        'content_length': len(content),
        'content_head': content[:160],
    })

artifact = {
    'schema_version': 1,
    'created_at': datetime.now(timezone.utc).isoformat(),
    'provider': 'lemonade',
    'base_url': 'http://127.0.0.1:13305/api/v1',
    'model': 'Qwen3-4B-Hybrid',
    'quant': 'ryzenai-llm-hybrid',
    'purpose': 'diagnose the long-context empty visible output threshold with thinking disabled',
    'request': {
        'temperature': 0.1,
        'max_tokens': 80,
        'stream': False,
        'disable_thinking': True,
        'system_prompt_suffix': '/no_think',
    },
    'results': results,
}
out = Path('data/benchmarks/rerun_lemonade_qwen3-4b-hybrid_no-think_context-threshold_probe_20260708.json')
out.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
'@ | python -
```

## Original July 7 POC Artifacts

These were useful exploration but not final methodology. Problems:

- n=2 only.
- no warmup.
- different model per provider.
- cold and warm runs averaged together.
- no automated pass-rate scoring.
- Lemonade Qwen3 thinking output was misconfigured.

POC artifacts:

| Artifact | Purpose |
|---|---|
| `data/benchmarks/provider_response_poc_20260707.json` | Original apples-ish response-time POC |
| `data/benchmarks/lemonade_llama3_2_1b_hybrid_probe.json` | Lemonade CLI bench for 1B Hybrid |
| `data/benchmarks/lemonade_qwen3_4b_hybrid_probe.json` | Lemonade CLI bench for Qwen3 4B Hybrid |

POC response-time summary:

| Provider | Model | Task | Avg/median seconds | Quality note |
|---|---|---|---:|---|
| FLM | `qwen3.5:4b` | grammar_short | 5.550 | good |
| FLM | `qwen3.5:4b` | prompt_short | 22.831 | XML present |
| Ollama | `llama3.2:3b` | grammar_short | 4.665 | good enough |
| Ollama | `llama3.2:3b` | prompt_short | 20.349 | failed XML |
| LM Studio | `qwen2.5-3b-instruct` | grammar_short | 2.885 | good |
| LM Studio | `qwen2.5-3b-instruct` | prompt_short | 5.115 | failed XML |
| Lemonade | `Llama-3.2-1B-Instruct-Hybrid` | grammar_short | 3.412 | failed, verbose preamble |
| Lemonade | `Llama-3.2-1B-Instruct-Hybrid` | prompt_short | 13.174 | failed XML |

Lemonade 1B Hybrid CLI bench:

| Scenario | Duration ms | TTFT ms | TPS | Input tokens | Output tokens | Peak memory GB |
|---|---:|---:|---:|---:|---:|---:|
| chat-short | 1853.452 | 879.0 | 19.854 | 21 | 20 | 17.7 |
| chat-long-output | 7966.675 | 277.0 | 33.177 | 37 | 256 | 17.5 |

Lemonade Qwen3 4B Hybrid CLI bench:

| Scenario | Duration ms | TTFT ms | TPS | Input tokens | Output tokens | Peak memory GB |
|---|---:|---:|---:|---:|---:|---:|
| chat-short | 4381.692 | 544.0 | 4.978 | 27 | 20 | 20.3 |
| chat-long-output | 28119.394 | 584.0 | 9.268 | 44 | 256 | 19.6 |

Qwen3 4B direct POC caveat:

- Grammar output was empty because the 160-token budget was consumed by an
  unclosed thinking block.
- That is a configuration error, not a final model-quality result.
- The rerun harness now adds `chat_template_kwargs.enable_thinking=false` and
  `/no_think` automatically for Qwen3-style model names.

## Historical FLM Long-Context Artifacts

These are historical context only. They were not rerun on July 8 and should not
be mixed into replacement gates as same-day data.

| Artifact | Model |
|---|---|
| `data/benchmarks/qwen3-5-4b_1780503503.json` | `qwen3.5:4b` |
| `data/benchmarks/gemma4-it-e4b_1780518283.json` | `gemma4-it:e4b` |
| `data/benchmarks/nanbeige4-1-3b_1780520438.json` | `nanbeige4.1:3b` |
| `data/benchmarks/quality_eval_1780693787.json` | June 5 quality probe for `gemma4-it:e4b`, `nanbeige4.1:3b`, and `qwen3.5:4b` |

June 5 historical quality probe:

| Model | Task | Quality score | Median seconds | Cases |
|---|---|---:|---:|---:|
| `gemma4-it:e4b` | grammar | 17/18 | 4.434 | 2 |
| `gemma4-it:e4b` | prompt | 28/29 | 16.645 | 2 |
| `nanbeige4.1:3b` | grammar | 9/18 | 13.354 | 2 |
| `nanbeige4.1:3b` | prompt | 27/29 | 54.097 | 2 |
| `qwen3.5:4b` | grammar | 17/18 | 4.725 | 2 |
| `qwen3.5:4b` | prompt | 10/29 | 12.146 | 2 |

FLM `qwen3.5:4b` historical context:

| Context k tokens | TTFT seconds | Prefill tok/s | Decode tok/s |
|---:|---:|---:|---:|
| 1 | 3.186803 | 307.64 | 13.75 |
| 2 | 5.286461 | 368.54 | 13.50 |
| 4 | 9.563234 | 406.28 | 13.08 |
| 8 | 18.318630 | 423.67 | 12.15 |
| 16 | 36.763248 | 421.71 | 10.75 |
| 32 | 78.712563 | 393.63 | 8.93 |

FLM `gemma4-it:e4b` historical context:

| Context k tokens | TTFT seconds | Prefill tok/s | Decode tok/s |
|---:|---:|---:|---:|
| 1 | 2.793130 | 351.11 | 12.25 |
| 2 | 4.443133 | 438.98 | 11.83 |
| 4 | 7.257507 | 536.40 | 11.39 |
| 8 | 13.778050 | 565.14 | 10.39 |
| 16 | 28.213831 | 551.09 | 8.87 |
| 32 | 64.997147 | 478.00 | 6.85 |

FLM `nanbeige4.1:3b` historical context:

| Context k tokens | TTFT seconds | Prefill tok/s | Decode tok/s |
|---:|---:|---:|---:|
| 1 | 1.784497 | 561.02 | 22.80 |
| 2 | 3.060991 | 644.03 | 21.71 |
| 4 | 6.089104 | 641.89 | 20.00 |
| 8 | 13.387642 | 581.27 | 17.04 |
| 16 | 34.021706 | 456.45 | 12.94 |
| 32 | 101.137085 | 306.74 | 8.92 |

## Historical Runtime Logs

Sources:

- `data/grammar_fix_history.jsonl`
- `data/prompt_history.jsonl`

`data/grammar_fix_history.jsonl`:

| Mode | Count | Recent count | Recent avg seconds | Recent median | Recent min | Recent max |
|---|---:|---:|---:|---:|---:|---:|
| grammar | 240 | 50 | 4.14 | 3.66 | 1.69 | 10.73 |
| prompt | 43 | 43 | 19.51 | 18.37 | 8.84 | 35.57 |
| translate | 1 | 1 | 2.79 | 2.79 | 2.79 | 2.79 |

`data/prompt_history.jsonl`:

| Mode | Count | Recent count | Recent avg seconds | Recent median | Recent min | Recent max |
|---|---:|---:|---:|---:|---:|---:|
| grammar | 77 | 50 | 4.56 | 3.26 | 1.61 | 22.09 |
| prompt | 1 | 1 | 11.07 | 11.07 | 11.07 | 11.07 |

## Interpretation

### Speed

Fastest grammar medians:

1. LM Studio Llama 3.2 1B: `0.938s`, but grammar quality was `30/40`.
2. Lemonade Llama 3.2 1B NPU: `1.227s`, grammar quality `25/40`.
3. LM Studio Llama 3.2 3B: `1.260s`, grammar quality `35/40`.
4. FLM Llama 3.2 1B: `1.338s`, grammar quality `36/40`.
5. LM Studio Qwen2.5 3B: `1.427s`, but grammar quality was `35/40`.
6. LM Studio Qwen2.5 7B: `1.493s`, grammar quality `40/40`.
7. Ollama Llama 3.2 1B: `1.634s`, grammar quality `29/40`.
8. Ollama Qwen2.5 3B: `1.743s`, grammar quality `35/40`.
9. Lemonade Qwen2.5 3B NPU: `1.799s`, grammar quality `40/40`.
10. Ollama Llama 3.2 3B: `2.065s`, grammar quality `35/40`.
11. FLM Qwen2.5 3B: `2.441s`, grammar quality `40/40`.
12. FLM Llama 3.2 3B: `2.972s`, grammar quality `35/40`.
13. FLM Qwen3.5 4B: `3.251s`, grammar quality `40/40`.
14. Lemonade Qwen3 4B Hybrid: `3.580s`, grammar quality `40/40`.

Fastest prompt medians with passing or near-passing XML quality:

1. Lemonade Qwen2.5 3B NPU: `5.497s`, prompt quality `45/50`.
2. Lemonade Qwen3 4B Hybrid: `14.538s`, prompt quality `50/50`.
3. FLM Qwen3.5 4B: `16.661s`, prompt quality `49/50`.
4. LM Studio Qwen2.5 7B: `7.026s`, prompt quality `0/49`, but `49/49`
   near-misses and `49/49` pass after deterministic repair.

Fast but not contract-safe:

- LM Studio Qwen2.5 3B prompt median was `3.157s`, but prompt quality was
  `0/50`.
- FLM Llama 3.2 1B prompt median was `2.930s`, but prompt quality was `0/50`.
- Lemonade Llama 3.2 1B NPU prompt median was `3.640s`, but prompt quality was
  `0/50`.
- LM Studio Llama 3.2 1B prompt median was `3.723s`, but prompt quality was
  `0/50`.
- Ollama Qwen2.5 3B prompt median was `5.290s`, but prompt quality was `0/50`.
- Ollama Llama 3.2 1B prompt median was `5.906s`, but prompt quality was
  `0/50`.
- LM Studio Llama 3.2 3B prompt median was `7.695s`, but prompt quality was
  `0/50`.
- Ollama Llama 3.2 3B prompt median was `10.564s`, but prompt quality was
  `0/50`.

### Size

Smallest practical local model tested:

- LM Studio Llama 3.2 1B: 1.32 GB on disk, 1.23 GiB loaded, but it failed
  prompt XML and missed grammar quality.
- LM Studio Qwen2.5 3B: 1.93 GB on disk, 1.80 GiB loaded.

Smallest Ollama baseline:

- Ollama `llama3.2:3b`: about 2.0 GB on disk and 2.6 GB shown by `ollama ps`,
  but the actual model runner working set reached about 4.7 to 7.2 GB during
  harness runs.

NPU memory:

- FLM `qwen3.5:4b` process RSS peaked around 7.25 GB.
- Lemonade Qwen2.5 3B NPU peaked around 3.81 GB RSS in the short run and about
  5.18 GB in the long-context run.
- Lemonade Qwen3 4B Hybrid peaked around 6.16 GB RSS in the short run.
- FLM Llama 3.2 3B peaked around 10.28 GB RSS and available RAM dropped near
  `3.3 GB`, which makes it a poor fit despite no formal memory-guard violation.
- Lemonade Llama 3.2 1B Hybrid peaked around 2.28 GB RSS, but quality failed.
- Lemonade Llama 3.2 1B NPU peaked around 2.65 GB RSS, but quality failed.
- Lemonade Qwen2.5 7B NPU quick peaked around 6.66 GB RSS.
- Lemonade Phi-4-mini NPU quick peaked around 5.78 GB RSS.
- Lemonade Mistral 7B NPU quick peaked around 6.23 GB RSS.

### Quality

Flowkey can route grammar mode more aggressively than prompt mode.

Current quality gates:

- Prompt mode replacement requires XML pass rate >= 9/10.
- Grammar replacement requires grammar pass >= 7/8.

FLM, Lemonade Qwen2.5 3B, and Lemonade Qwen3 4B Hybrid passed the short prompt
quality threshold. Every Llama 3.2 cell failed prompt XML at `0/50`, including
the small `llama3.2:3b` baseline. No non-FLM model currently passes the
meetings/long-context gate: the apparent Lemonade Qwen2.5 3B 8k pass was
invalidated by the NPU-only needle ladder. LM Studio 7B is the most interesting
non-NPU near miss because it produced repairable Markdown-style structure in
all timed prompt runs, but the current contract checker correctly rejects it.

## Current Routing Recommendation (corrected July 9)

Production:

- `prompt`: FLM stays the default. Lemonade Qwen2.5-3B-NPU is an approved
  OPT-IN short prompt route (`45/50`, with a validated `prompt_plan` repair),
  not a replacement.
- `meeting/long-context`: FLM only. Lemonade Qwen2.5-3B-NPU is disqualified —
  the July 9 needle probe proved silent keep-last truncation to ~2-3k tokens.
  Qwen3-4B-Hybrid is also disqualified (loud empty output past ~2.1k). FLM is
  the only tested runtime that honestly processes an 8k transcript here.
- `grammar`: Lemonade Qwen2.5-3B-NPU is an approved opt-in route; it passed
  `40/40` and was faster than both FLM Qwen2.5 and FLM Qwen3.5. FLM remains the
  safe default.
- Provider switching must enforce NPU exclusivity (stop FLM when Lemonade
  serves, and vice versa) — concurrent serving hard-fails Lemonade.

Experimental:

- `ollama llama3.2:3b`: CPU fallback and easy install path.
- `llama3.2:1b`/`llama3.2:3b` on any provider: do not use for prompt mode under
  the current Flowkey prompt; every Llama Matrix A prompt row was `0/50`.
- `lmstudio qwen2.5-3b-instruct`: very fast local experimental route, but prompt
  output needs repair or a provider-specific prompt.
- `lmstudio qwen2.5-7b-instruct`: good grammar and fastest repairable prompt
  path so far; label-to-XML repair is implemented, unit-tested, and validated
  through the app route. Supported only as an opt-in experimental prompt route;
  do not make it the default or automatic route because it is non-NPU and has not
  been second-day rerun as a production route.
- `lemonade Qwen2.5-3B-Instruct-NPU`: approved opt-in SHORT-TASK route
  (grammar/prompt), NOT a meetings route — silently truncates long input to
  ~2-3k tokens (July 9 needle probe). The known prompt-plan repair is
  implemented, unit-tested, and app-route validated.
- `lemonade Qwen3-4B-Hybrid`: strong short prompt candidate with thinking
  disabled and a passed informational second-day short rerun; exclude from
  meetings for now.
- `lemonade Qwen2.5-7B-Instruct-NPU`: do not continue under the current prompt;
  prompt quality was `0/20` in quick testing.
- `lemonade Phi-4-mini-instruct-NPU`: do not continue under the current prompt;
  prompt and grammar both missed the quick gate.
- `lemonade Mistral-7B-Instruct-v0.3-NPU`: do not continue for prompt mode under
  current prompt; it failed quick quality.

## Follow-Up Work

The second-day batch and the Lemonade NPU-only ladder are complete. Remaining
work is product wiring and future-provider investigation, not more evidence for
the current replacement decision:

1. Wire Lemonade `Qwen2.5-3B-Instruct-NPU` as an opt-in SHORT-TASK route only
   (grammar/prompt), never meetings, and enforce NPU exclusivity in the switch.
2. Keep FLM as the production default and sole meetings route until a future
   Lemonade runtime/model demonstrably quotes both transcript needles at 8k on
   this machine.
3. Track Lemonade context-limit fixes: retest only after a Lemonade/RyzenAI
   update, a recipe change, or a downloaded same-weight Hybrid variant that
   plausibly changes the context ceiling.
4. Keep LM Studio Qwen2.5 7B opt-in only unless a future second-day route test
   and product decision promotes it.
5. Delete or archive loser Lemonade models after product routing is decided if
   disk pressure matters.

Second-day batch command used:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File tools\run_second_day_provider_batch.ps1 -RunQwen3Short
```

Step-by-step helpers remain available:

```powershell
# Non-benchmark preflight.
pwsh -NoProfile -ExecutionPolicy Bypass -File tools\check_second_day_provider_preflight.ps1 -RunQwen3Short -StrictDateGate

# Qwen2.5 second-day gate: grammar/prompt plus calibrated long-context.
pwsh -NoProfile -ExecutionPolicy Bypass -File tools\run_next_day_provider_rerun.ps1

# Include Qwen3 Hybrid short rerun if still considering short prompt routing.
pwsh -NoProfile -ExecutionPolicy Bypass -File tools\run_next_day_provider_rerun.ps1 -RunQwen3Short

# Evaluate the gate using same-day artifact names.
pwsh -NoProfile -ExecutionPolicy Bypass -File tools\evaluate_second_day_provider_gate.ps1 -RunQwen3Short
```

The helper stops Flowkey/FLM and other provider contaminants, unloads LM Studio,
loads Lemonade, runs `tools/provider_bench.py` with the same 1-warmup/5-run
methodology, writes `second_day_*` artifacts under `data/benchmarks`, unloads
Lemonade, and restores the Flowkey hotkey unless `-NoRestoreFlowkey` is passed.
It refuses live execution on July 8, 2026 or earlier.

The evaluator returns exit code `0` only when Qwen2.5 clears the grammar,
prompt, calibrated long-context, required-size, and memory-guard gates. If Qwen3
short mode was rerun too, pass `--qwen3-short <artifact>` to include it in the
report; Qwen3 still remains excluded from meetings until the long-context
visible-output bug is fixed.

## Cleanup Commands

Unload providers:

```powershell
# Lemonade
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload all

# LM Studio
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" unload --all
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" server stop

# Ollama
ollama stop llama3.2:3b
ollama stop llama3.2:1b

# FLM
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match 'flm' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Restart Flowkey hotkey:

```powershell
Start-Process `
  -FilePath "$PWD\vendor\ahk\AutoHotkey64.exe" `
  -ArgumentList @("$PWD\scripts\grammarFix.ahk") `
  -WindowStyle Hidden
```

## Bottom Line (corrected July 9)

Dropping FLM does not make sense. The outcome is per-workload routing, not
replacement.

Lemonade `Qwen2.5-3B-Instruct-NPU` earned an opt-in SHORT-TASK role: it passes
the short quality thresholds (`40/40` grammar, `45/50` prompt), is faster than
FLM on grammar, stays inside the memory guard, and reproduced across sessions.
But its apparent long-context win was an artifact — the July 9 needle probe
proved silent keep-last truncation to ~2-3k tokens while it reported full
`prompt_tokens`. Per the plan's own rule that disqualifies a sub-8k context cap,
it is NOT a meetings route.

Lemonade `Qwen3-4B-Hybrid` (thinking disabled) has the best short prompt score
(`50/50`) but fails long context loudly (empty output past ~2.1k). The NPU-only
ladder extended that finding: all four tested Lemonade models lose the
start-of-input needle at the 2k target on this machine, including after
`--ctx-size 8192`.

The Llama Matrix A rerun answers "why not just Llama 3.2 3B?": every tested
Llama provider/model row failed prompt XML (`0/50`), so it is not a Flowkey
prompt-mode route.

Near-term path: keep FLM as the default and the ONLY meetings/long-context
route; offer Lemonade Qwen2.5-3B-NPU as an opt-in grammar/prompt route with the
`prompt_plan` repair and NPU-exclusive switching; keep Ollama as a portable CPU
fallback and LM Studio as an opt-in fast prompt route. Before any Lemonade model
is considered for meetings, it must quote both transcript needles at 8k under
the corrected harness. See `docs/lemonade-npu-only-bench-plan.md`.
