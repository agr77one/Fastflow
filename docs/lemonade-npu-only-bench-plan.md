# Lemonade NPU-Only Benchmark Plan (FLM completely off)

Date: 2026-07-09
Status: EXECUTED — no tested Lemonade model met the 8k meetings gate
Parent: `docs/local-llm-provider-rerun-plan.md`
Corrects the long-context methodology invalidated by the July 9 truncation audit
(see `docs/local-llm-provider-benchmark-readme.md` → "July 9 Truncation Audit").

## Why this plan exists

Two facts from the July 9 audit force a Lemonade-isolated rerun:

1. **Silent input truncation.** Lemonade `Qwen2.5-3B-Instruct-NPU` kept only the
   last ~2-3k tokens of an 8k prompt while reporting `prompt_tokens: 8022`. The
   old harness used uniform filler, so the truncation was invisible. This makes
   every prior Lemonade long-context "pass" untrustworthy and the effective
   context window unknown.
2. **NPU exclusivity.** With `flm serve` running, every Lemonade call returned
   HTTP 500 (`RyzenAI DynamicDispatch ... Failed to submit`) and the RyzenAI
   backend stayed wedged even after FLM stopped, recovering only on model
   reload. FLM and Lemonade contend for the same NPU. Any benchmark that leaves
   FLM (or Flowkey's daemon, which auto-spawns FLM) alive produces 500s or
   corrupted timings.

The single most important discipline of this plan: **FLM must be verified OFF —
process gone, port 52625 closed, Flowkey daemon stopped so it cannot respawn it
— before any Lemonade measurement, and re-verified between models.**

## Goals

- **L1 — True effective context.** Find the prompt-token length at which each
  Lemonade model stops seeing the START of the input. This is the number that
  decides meetings-eligibility, and it was never measured honestly.
- **L2 — Truncation-safe long-context quality.** Re-run Matrix C with needles at
  both transcript ends. A "pass" requires the model to quote BOTH the opening
  and closing code — a digest of the surviving tail alone fails.
- **L3 — Isolated speed curve.** With FLM off and only one Lemonade model loaded,
  measure honest TTFT vs prompt length (it must scale with input; a flat curve
  is the truncation fingerprint) and decode TPS.
- **L4 — Config remedies.** Determine whether the truncation is a server default
  (context-length flag, KV cache cap, sliding-window) that can be lifted, or a
  hard model/recipe limit. If liftable, re-measure at the raised limit.
- **L5 — Meetings verdict per model.** For each Lemonade NPU model near the
  ~6B class, a clear yes/no on the 7k-token meeting-digest workload, with the
  measured context ceiling on record.

Explicitly OUT of scope: FLM tuning, Ollama, LM Studio, cross-provider speed
tables (that is the parent doc's job). This plan compares Lemonade models to the
meetings *requirement*, not to other providers.

## Models under test

Reuse already-downloaded Lemonade models; do not re-pull unless missing.

| Model | Size GB | Role in this plan |
|---|---:|---|
| `Qwen2.5-3B-Instruct-NPU` | 4.10 | Primary — best short-task profile; measure real context ceiling |
| `Qwen3-4B-Hybrid` | 5.17 | Known loud long-context failure (~2.1k); confirm ceiling with needle harness |
| `Qwen2.5-7B-Instruct-NPU` | 8.22 | ~6B-class; does a bigger Qwen quote both needles at 8k? |
| `Phi-4-mini-instruct-NPU` | 5.21 | ~6B-class; best Matrix B prompt near-miss (14/20) — worth a context check |

Deferred unless the four above leave the question open: `Mistral-7B-Instruct-v0.3-NPU`
(prompt 0/20 in Matrix B), `Meta-Llama-3.1-8B-Instruct-NPU` (not pulled; RAM
risk on 23.6 GiB). Record if skipped and why.

## Preflight — FLM off, hard-verified (do every item, in order)

```powershell
# 1. Stop Flowkey so its daemon cannot respawn FLM mid-run.
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match 'AutoHotkey|pythonw' -and $_.CommandLine -match 'grammarFix|ffp_daemon' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 2. Kill any FLM server.
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match '^flm' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 3. VERIFY FLM is gone AND its port is closed. Both must be empty/false.
Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^flm' } |
  Select-Object ProcessId,Name       # must return nothing
(Test-NetConnection 127.0.0.1 -Port 52625 -WarningAction SilentlyContinue).TcpTestSucceeded  # must be False

# 4. Confirm the Lemonade backend is not already wedged from an earlier FLM clash.
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" status
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload all   # ignore "not loaded"

# 5. Stop the other providers so they don't share CPU/iGPU/RAM.
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" server stop
ollama stop qwen2.5:3b; ollama stop llama3.2:3b; ollama stop llama3.2:1b

# 6. Baseline environment.
Get-CimInstance Win32_Battery | Select-Object BatteryStatus       # 2 = on AC
Get-Counter '\Memory\Available MBytes' -SampleInterval 1 -MaxSamples 3
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" --version
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" backends --all
```

Abort the run if step 3 shows FLM alive or port 52625 open. **Re-run steps 2-4
between every model** (unload the previous model, confirm FLM still absent).

Sanity gate before trusting any timing — one tiny call must return non-empty:

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" load <MODEL>
python -c "import json,urllib.request as u; r=u.urlopen(u.Request('http://127.0.0.1:13305/api/v1/chat/completions', data=json.dumps({'model':'<MODEL>','messages':[{'role':'user','content':'Say OK.'}],'max_tokens':10,'stream':False}).encode(), headers={'Content-Type':'application/json','Authorization':'Bearer lemonade'})); print(json.load(r)['choices'][0]['message']['content'])"
```

If this 500s, the backend is wedged — `unload all`, reload, retry once; if it
still 500s, restart the Lemonade server and re-verify FLM is off.

## Phase 1 — Context-limit ladder (L1) — run FIRST, per model

The cheapest, most decisive test. A needle-retrieval ladder finds the truncation
threshold before spending time on full benchmarks. Use the standalone probe (it
mirrors what the July 9 audit ran), not the full harness, so it is fast:

- Plant a unique code (`ZEBRA-<n>`) at the START of a synthetic transcript, ask
  the model to return just that code.
- Sizes: 1000, 1500, 2000, 2500, 3000, 4000, 6000, 8000 target prompt tokens.
- 1 call per size, `temperature=0`, `max_tokens=40`.
- Record `usage.prompt_tokens`, `ttft`, whether the code came back.
- Repeat once with the needle at the END as a control (end-needle should always
  survive keep-last truncation; if it does NOT, the failure is comprehension,
  not truncation).

Output: `data/benchmarks/lemonade_<model>_context_ladder_<date>.json` with the
first size where the start-needle is lost = **effective context ceiling**. Two
fingerprints to call out in the artifact:

- **Flat TTFT** across rising sizes → truncation.
- **`prompt_tokens` reported == submitted** but needle lost → silent truncation
  (vs a clean 400 error, which would be an honest cap).

A model whose ceiling is < 8k skips Phase 3's meetings verdict as "fails at
Nk" and does not get a full long-context timing run.

## Phase 2 — Config remedy investigation (L4) — only if ceiling < 8k

Before concluding a model can't do meetings, rule out a fixable server setting:

- Check `lemonade` load options for a context-length / max-sequence flag
  (e.g. `--ctx-size`, `--max-length`, or a per-model recipe field). The RyzenAI
  OGA recipe may default to a small window independent of the model's trained
  context.
- Reload with the largest supported context and re-run the Phase 1 ladder.
- Check whether a `-Hybrid` variant of the same weights has a different ceiling
  than the `-NPU` variant (Hybrid does NPU prefill + iGPU decode and may handle
  context differently).
- Record the exact flag/recipe that changed the ceiling, or "no available
  setting raised it" with the commands tried.

This phase is investigation, not benchmarking — document commands and outcomes,
even negative ones (a proven "cannot be raised" is a real result).

## Phase 3 — Truncation-safe long-context + short-task (L2, L3, L5)

Only for models whose Phase 1/2 ceiling reaches ~8k. Use the FIXED harness
(`tools/provider_bench.py` now plants `LONGCTX_OPEN_CODE` at the start and
`LONGCTX_CLOSE_CODE` at the end; `check_longctx_contract` fails unless BOTH are
quoted and flags `truncation_suspected`):

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" load <MODEL>

# Long-context, truncation-safe, honest TTFT curve
python tools\provider_bench.py `
  --provider lemonade `
  --base-url http://127.0.0.1:13305/api/v1 `
  --bearer lemonade `
  --model <MODEL> `
  --quant ryzenai-llm-npu `
  --tasks longctx `
  --longctx-sizes 1000,2000,4000,6000,8000 `
  --runs 5 --warmup 1 --timeout 900 `
  --out data\benchmarks\lemonade_<model>_longctx_needle_<date>.json

# Short-task re-confirm on isolated NPU (grammar/prompt)
python tools\provider_bench.py `
  --provider lemonade `
  --base-url http://127.0.0.1:13305/api/v1 `
  --bearer lemonade `
  --model <MODEL> `
  --quant ryzenai-llm-npu `
  --tasks grammar,prompt `
  --runs 5 --warmup 1 --timeout 300 `
  --out data\benchmarks\lemonade_<model>_short_<date>.json

& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload all
```

Validity checks the artifact must satisfy (else discard and re-run):

- `longctx` cases all report `start_needle_found: true` AND `end_needle_found:
  true` for a pass. Any `truncation_suspected: true` row = documented failure.
- TTFT must rise with prompt length. A flat TTFT across 4k→8k means truncation
  slipped through — investigate before trusting the row.
- Memory guard clean (available RAM ≥ 3 GB, no `\Memory\Pages/sec` spike).

## Method (statistics, environment)

- 1 discarded warmup + 5 timed runs per case; report median + min-max.
- `temperature=0.1` for grammar/prompt, `0.0` for the Phase 1 ladder.
- Exactly one Lemonade model loaded at a time; unload before loading the next.
- FLM re-verified off between models (preflight steps 2-4).
- Record `lemonade --version` and `backends --all` in each artifact.
- One memory metric: RyzenAI/Lemonade process RSS delta + system available MB.

## Decision gates

**Meetings-eligible (per model)** — ALL must hold:

- Phase 1 ceiling ≥ 8000 prompt tokens (start-needle retrieved at 8k).
- Phase 3 `longctx` 8k case: both needles quoted, 5/5, no `truncation_suspected`.
- Honest TTFT scaling (monotonic with prompt length).
- No memory-guard violation at 8k.

**Short-task-eligible (per model)** — grammar ≥ 7/8 and prompt ≥ 9/10 on the
isolated run (re-confirms the Matrix A numbers on a clean NPU).

**Meetings default stays FLM** unless at least one Lemonade model is
meetings-eligible AND its 8k TTFT ≤ 1.5× FLM's honest 8k TTFT (22.7s → ≤ 34s)
measured on the same machine. Until then, the parent doc's routing outcome holds:
Lemonade for short tasks only, FLM for meetings.

## Report

Write findings into `docs/local-llm-provider-benchmark-readme.md` (append a
"Lemonade NPU-Only Rerun" section) and update the Decision/Bottom Line if a
model becomes meetings-eligible. Every model row carries: effective context
ceiling (Phase 1), whether any config remedy raised it (Phase 2), longctx
needle pass rate + honest TTFT curve (Phase 3), short-task pass rates, peak RSS,
and the per-model meetings verdict. List anything skipped with the reason.

## Execution Results (2026-07-09)

The plan was executed with Flowkey/FLM fully stopped and verified off before
Lemonade measurements:

- FLM process count: `0`.
- FLM port `52625`: closed / `False`.
- LM Studio server: stopped.
- Lemonade server: running on port `13305`, version `10.9.0`.
- Lemonade status before/after model runs: no models loaded.
- Memory guard: no ladder artifact violated the 3 GiB available-RAM guard.

Phase 1 default ladder:

| Model | Largest start needle found | First start needle lost | End-control failures | 8k start gate | Verdict |
|---|---:|---:|---|---|---|
| `Qwen2.5-3B-Instruct-NPU` | 1500 | 2000 | none | fail | no meetings |
| `Qwen3-4B-Hybrid` | 1500 | 2000 | 2000, 2500, 3000, 4000, 6000, 8000 | fail | no meetings |
| `Qwen2.5-7B-Instruct-NPU` | 1500 | 2000 | 1000, 1500, 2500, 4000, 6000, 8000 | fail | no meetings |
| `Phi-4-mini-instruct-NPU` | 1500 | 2000 | none | fail | no meetings |

Phase 2:

- `lemonade load --help` exposed `--ctx-size SIZE [-1]`.
- Reloading every model with `--ctx-size 8192` produced the same result:
  largest start needle found `1500`, first start needle lost `2000`.
- No same-weight Hybrid variants were already downloaded for Qwen2.5 3B,
  Qwen2.5 7B, or Phi-4-mini. The already downloaded `Qwen3-4B-Hybrid` was run.

Phase 3:

- Skipped by design because no model reached the Phase 1/2 8k start-needle gate.

Artifacts:

| Artifact | Notes |
|---|---|
| `tools/lemonade_context_ladder.py` | standalone Phase 1/2 ladder harness |
| `data/benchmarks/lemonade_qwen2.5-3b-instruct-npu_context_ladder_20260709.json` | default load |
| `data/benchmarks/lemonade_qwen3-4b-hybrid_context_ladder_20260709.json` | default load |
| `data/benchmarks/lemonade_qwen2.5-7b-instruct-npu_context_ladder_20260709.json` | default load |
| `data/benchmarks/lemonade_phi-4-mini-instruct-npu_context_ladder_20260709.json` | default load |
| `data/benchmarks/lemonade_qwen2.5-3b-instruct-npu_context_ladder_ctx8192_20260709.json` | `--ctx-size 8192` |
| `data/benchmarks/lemonade_qwen3-4b-hybrid_context_ladder_ctx8192_20260709.json` | `--ctx-size 8192` |
| `data/benchmarks/lemonade_qwen2.5-7b-instruct-npu_context_ladder_ctx8192_20260709.json` | `--ctx-size 8192` |
| `data/benchmarks/lemonade_phi-4-mini-instruct-npu_context_ladder_ctx8192_20260709.json` | `--ctx-size 8192` |

Decision:

- Meetings default stays FLM.
- Lemonade `Qwen2.5-3B-Instruct-NPU` remains an opt-in short-task route only.
- Lemonade `Qwen3-4B-Hybrid` remains a short-task experiment only.
- `Qwen2.5-7B-Instruct-NPU` and `Phi-4-mini-instruct-NPU` get no current
  Flowkey route under the tested prompts and context behavior.

## Cleanup

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload all
# Restart Flowkey (this respawns FLM as the default provider).
Start-Process -FilePath "$PWD\vendor\ahk\AutoHotkey64.exe" `
  -ArgumentList @("$PWD\scripts\grammarFix.ahk") -WindowStyle Hidden
# Confirm FLM is back for normal use.
(Test-NetConnection 127.0.0.1 -Port 52625 -WarningAction SilentlyContinue).TcpTestSucceeded  # expect True after warmup
```

## Caveats to carry into the report

- `usage.prompt_tokens` from Lemonade counts the SUBMITTED prompt, not what the
  model attended to — never trust it as proof of context coverage; only the
  start-needle proves coverage.
- `-NPU` (OGA, NPU-only) and `-Hybrid` (NPU prefill + iGPU decode) can differ in
  both context ceiling and decode speed for the same weights — test the exact
  variant that would ship.
- Disk: the four models total ~22 GB already downloaded; deferred models add
  ~8-9 GB each. Delete losers after the report.
- This machine has 23.6 GiB RAM; a 7B-8B NPU model plus OS can approach the
  memory guard — watch available MB during 8k runs.
