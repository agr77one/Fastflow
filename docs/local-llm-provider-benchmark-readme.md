# Local LLM Provider Benchmark README

Date: 2026-07-08

This README captures the current proof of concept for whether Flowkey should
drop FastFlowLM (FLM) and use Ollama, LM Studio, or Lemonade on the AMD NPU
instead. It includes setup steps, exact benchmark commands, artifacts, measured
results, anomalies, and the current decision.

## Decision

Do not drop FLM as the global default yet.

The corrected rerun shows:

- FLM `qwen3.5:4b` is still the safest global default because it passes short
  prompt/grammar quality and the long-context meeting workload in the same
  session: `49/50` prompt passes, `40/40` grammar passes, and `5/5` at roughly
  8k prompt tokens.
- Lemonade `Qwen2.5-3B-Instruct-NPU` is the first serious replacement
  candidate: `40/40` grammar, `45/50` prompt XML, and much faster calibrated
  8k TTFT than the FLM incumbent. It still needs a second-day rerun before
  replacing FLM. Its one consistent prompt miss is narrow: `prompt_plan`
  is recoverable with deterministic tag repair or a stricter retry prompt.
- Lemonade `Qwen3-4B-Hybrid`, retested with thinking disabled, is now a strong
  short prompt-mode candidate: `40/40` grammar and `50/50` prompt XML. It is not
  a global replacement because its long-context route returns empty visible
  output after roughly 2.1k prompt tokens.
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

Practical recommendation:

- Keep `fastflowlm` as the production default.
- Treat Lemonade `Qwen2.5-3B-Instruct-NPU` as the leading replacement candidate,
  pending a second session. The deterministic repair path for its known
  `prompt_plan` miss is now implemented and unit-tested in the app path.
- Treat Lemonade `Qwen3-4B-Hybrid` as a short-task-only candidate, pending a
  long-context fix or a workload-specific route that excludes meetings.
- Keep `ollama` wired as a portable CPU fallback.
- Keep `lmstudio` wired as an experimental fast local provider.
- Keep `lemonade` wired as the AMD NPU experiment path, but do not route
  production Flowkey prompt mode to it yet without the second-session gate.

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

So bigger did not reliably solve the contract problem. The best all-around
candidate remains the 3B Lemonade NPU Qwen2.5 model because it also passed the
8k meeting sweep. The best short prompt-only candidate is Qwen3 4B Hybrid with
thinking disabled.

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
| Lemonade | `http://127.0.0.1:13305/api/v1` | `Qwen3-4B-Hybrid` | bearer `lemonade` |

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
python -m py_compile tools\provider_bench.py
```

Result:

```text
9 passed
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
| `Qwen2.5-3B-Instruct-NPU` | 4.10 | Matrix A and calibrated long-context tested |
| `Qwen2.5-7B-Instruct-NPU` | 8.22 | July 8 quick quality tested; failed prompt gate |
| `Phi-4-mini-instruct-NPU` | 5.21 | July 8 quick quality tested; failed quick gate |
| `Llama-3.2-1B-Instruct-NPU` | 1.96 | exact Matrix A NPU cell tested after catalog re-check |
| `Llama-3.2-1B-Instruct-Hybrid` | 1.89 | full Matrix A substitution tested before exact `-NPU` cell was pulled |
| `Qwen3-4B-Hybrid` | 5.17 | retested with thinking disabled; short mode passed, long-context failed |
| `Mistral-7B-Instruct-v0.3-NPU` | 8.09 | July 8 quick quality tested; failed prompt gate |
| `CodeLlama-7b-Instruct-hf-NPU` | 7.03 | downloaded, excluded from headline |
| `DeepSeek-R1-Distill-Qwen-7B-NPU` | 8.26 | downloaded, reasoning model, excluded |
| `chatglm3-6b-NPU` | 6.55 | downloaded, excluded as older/weak contract fit |

Still not run from the rerun plan:

- Matrix A Lemonade Llama 3B; the rerun plan correctly lists no Lemonade 3B cell
- second-day reproducibility for Lemonade `Qwen2.5-3B-Instruct-NPU`
- optional Matrix B stretch `Meta-Llama-3.1-8B-Instruct-NPU`

## July 8 Corrected Rerun Artifacts

| Artifact | Status | Notes |
|---|---|---|
| `data/benchmarks/rerun_fastflowlm_qwen3.5-4b_turbo_20260708.json` | valid | FLM incumbent, turbo mode |
| `data/benchmarks/rerun_ollama_llama3.2-3b_20260708_memfix.json` | valid | corrected Ollama RSS tracking |
| `data/benchmarks/rerun_lmstudio_qwen2.5-3b-instruct_20260708_cleanmem.json` | valid | clean-memory LM Studio 3B rerun |
| `data/benchmarks/rerun_lmstudio_qwen2.5-7b-instruct_20260708.json` | valid with one timeout | 1 timed prompt run timed out |
| `data/benchmarks/rerun_lmstudio_qwen2.5-7b-instruct_output-repair_20260708.json` | diagnostic | deterministic label-to-XML repair passes 49/49 timed prompt near-misses |
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
  and strict retry can recover it; production still needs the policy implemented
  and covered by tests before routing prompt mode.
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
This does not remove the second-day reproducibility gate.

LM Studio Qwen2.5 7B output-repair diagnostic:

| Policy | Pass rate | Extra model calls | Source median wall s | Notes |
|---|---:|---:|---:|---|
| Original prompt | 0/49 | 0 | 7.026 | used `Task:`, `Context:`, `Constraints:`, `Output format:` labels instead of XML tags |
| Deterministic label-to-XML repair | 49/49 | 0 | unchanged | converts labeled sections to `<task>`, `<context>`, `<constraints>`, `<output_format>` |

Interpretation: LM Studio Qwen2.5 7B remains an experimental route, not a
production prompt replacement. The deterministic output-repair layer is now
implemented and covered in the app path, so the remaining gate is route-level
validation in a clean provider session. It is the most interesting fast non-NPU
prompt candidate.

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

Long-context gate:

- Lemonade Qwen2.5 3B NPU clears the 8k TTFT gate against FLM by a wide margin:
  `2.831s` vs FLM `22.697s`.
- Lemonade Qwen3 4B Hybrid did not clear the meetings workload despite good
  short-mode quality: the 4k and 8k cells returned empty scored output.
- The Qwen3 Hybrid failure is not a harness scoring artifact. A direct Lemonade
  API threshold probe returned visible content at `2055` prompt tokens and empty
  `message.content` from `2255` prompt tokens onward while still reporting
  nonzero completion tokens.
- No context cap was hit for Lemonade Qwen2.5 3B NPU or FLM at roughly 8k
  prompt tokens.
- No memory guard fired for the calibrated long-context cells.
- The replacement decision still remains gated on second-day reproducibility and
  product hardening of the validated `prompt_plan` repair/retry policy.

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
the small `llama3.2:3b` baseline. Lemonade Qwen2.5 3B is the only non-FLM
candidate that also passed the calibrated 8k long-context workload. LM Studio
7B is the most interesting non-NPU near miss because it produced repairable
Markdown-style structure in all timed prompt runs, but the current contract
checker correctly rejects it.

## Current Routing Recommendation

Production:

- `prompt`: keep FLM default until Lemonade Qwen2.5 or Qwen3 passes a second-day
  rerun. Qwen3 has the cleaner short prompt score, but Qwen2.5 has the better
  all-workload profile and a validated recovery path for its `prompt_plan` miss.
- `meeting/long-context`: Lemonade Qwen2.5 is the leading candidate based on the
  calibrated 8k sweep, but do not switch production until the second-session gate
  is satisfied. Do not route meetings to Qwen3 Hybrid until the empty 4k/8k
  output failure is fixed.
- `grammar`: Lemonade Qwen2.5 is the leading candidate; it passed `40/40` and was
  faster than both FLM Qwen2.5 and FLM Qwen3.5.

Experimental:

- `ollama llama3.2:3b`: CPU fallback and easy install path.
- `llama3.2:1b`/`llama3.2:3b` on any provider: do not use for prompt mode under
  the current Flowkey prompt; every Llama Matrix A prompt row was `0/50`.
- `lmstudio qwen2.5-3b-instruct`: very fast local experimental route, but prompt
  output needs repair or a provider-specific prompt.
- `lmstudio qwen2.5-7b-instruct`: good grammar and fastest repairable prompt
  path so far; label-to-XML repair is implemented and unit-tested, but the
  provider route remains experimental until a clean app-level route run is done.
- `lemonade Qwen2.5-3B-Instruct-NPU`: leading replacement candidate, pending
  second session. The known prompt-plan repair is implemented and unit-tested.
- `lemonade Qwen3-4B-Hybrid`: strong short prompt candidate with thinking
  disabled; exclude from meetings for now.
- `lemonade Qwen2.5-7B-Instruct-NPU`: do not continue under the current prompt;
  prompt quality was `0/20` in quick testing.
- `lemonade Phi-4-mini-instruct-NPU`: do not continue under the current prompt;
  prompt and grammar both missed the quick gate.
- `lemonade Mistral-7B-Instruct-v0.3-NPU`: do not continue for prompt mode under
  current prompt; it failed quick quality.

## Remaining Work

The full rerun plan is not complete. Still needed:

1. Rerun Lemonade `Qwen2.5-3B-Instruct-NPU` on a second day/session before a
   production switch.
2. Rerun Lemonade `Qwen3-4B-Hybrid` short mode on a second day if short prompt
   routing is considered.
3. Track or fix Qwen3 Hybrid's visible-output failure above roughly 2.1k prompt
   tokens before using it for meetings.
4. Exercise the implemented deterministic prompt repair in a clean provider
   session before production prompt routing.
5. Run an app-level LM Studio Qwen2.5 7B route validation now that label-to-XML
   output repair is implemented.
6. Decide whether to pull and test optional stretch
   `Meta-Llama-3.1-8B-Instruct-NPU`; it remains catalog-available but was not
   pulled in the July 8 batch.

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

## Bottom Line

Dropping FLM globally does not make sense yet.

Lemonade `Qwen2.5-3B-Instruct-NPU` is now the first credible FLM replacement
candidate. It passes the headline short-task quality threshold, beats FLM on
calibrated 8k TTFT, and stays inside the memory guard. It is not production-ready
until it passes the second-session reproducibility gate and the implemented
`prompt_plan` repair path is exercised in a clean app-level provider run.

Lemonade `Qwen3-4B-Hybrid` with thinking disabled is the best short prompt-mode
score so far (`50/50`), but it is not a global replacement because direct API
probing shows visible long-context output fails between `2055` and `2255` prompt
tokens.

The Llama Matrix A rerun answers the "why not just Llama 3.2 3B?" question:
Llama 3.2 was small and sometimes fast, and the exact Lemonade 1B NPU cell was
also light, but every tested provider/model row failed prompt XML (`0/50`), so
it is not a Flowkey prompt-mode replacement.

The correct near-term path is to keep FLM as default, keep the new provider
wiring, and focus the next rerun on Lemonade Qwen2.5 reproducibility, Qwen3
short-mode reproducibility, LM Studio repair-route validation, and a targeted
fix for Qwen3 long-context output.
