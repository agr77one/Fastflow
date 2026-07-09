# Prompt Builder (2.2.0) — Finish & Validate Runbook

Date: 2026-07-09
Companion to: `docs/prompt-logic-refinement-plan.md` (design) — this is the
step-by-step to take the implemented-but-unfinished feature to a shipped 2.2.0.

## Execution status (2026-07-09)

Local development/validation complete on branch `feat/2.2.0-prompt-builder`.

Completed from this runbook:

- Step 0: branch created; unrelated `docs/history-visibility-plan.md` remains
  untracked and excluded.
- Step 1: `python -m ruff check scripts tests tools` clean.
- Step 2: added CI-gated pytest coverage for fallback contract safety,
  generic_chat echo detection, and action-mode preservation.
- Step 3: default identity prompt now fails closed when the legacy prompt is
  missing; covered by pytest.
- Step 4: `tools/prompt_builder_eval.py` now supports model-free and live-FLM
  modes. Live FastFlowLM artifact:
  `data\benchmarks\prompt_builder_eval_flm_20260709.json`.
- Step 5: version bumped to `2.2.0`; CHANGELOG/README/SPEC updated; live eval
  result recorded (`claude_code` 2/2, `generic_chat` 10/10), so generic_chat is
  production rather than experimental.
- Step 6: local gates passed:
  `ruff`, `pytest tests -q` (345 passed), bundled Node `--check app.js`,
  `py_compile`, model-free eval, live-FLM eval.

Pending remote/release work after local commit:

- Step 7 remote portion onward: push, PR, CI, ruleset-toggle merge, tag,
  installer build, and GitHub release notes. These require remote credentials/
  release operations and should be run only when ready to publish.

## Original code-review state (before this pass)

Implemented and **uncommitted on `live`** (= `origin/main` `a60f7e0`). Verified
correct: identity drop-in (strict, tested, runtime-wired), prompt-mode-only
gating (locked grammar/summarize/tone untouched), contract gate only on
non-default, target-aware echo, validate→repair→fallback, staged rollout
(claude_code + generic_chat), filtered+re-normalized config patch, V5-clean
dashboard. **341 pytest pass; `node --check` app.js clean.**

Blocking gaps this runbook was written to close:
1. `ruff` not clean — 2 `I001` import-sort errors.
2. Generator quality **unmeasured** — `tools/prompt_builder_eval.py` is model-free
   (validates renderers only); no live-FLM numbers exist.
3. Three CI-gated tests missing (gate-fail fallback, generic_chat echo,
   action-mode preservation in pytest).
4. Minor: identity path depends on `legacy_system_prompt` being present.
5. Version not bumped (still 2.1.1).

## Definition of done

- All V20 gates green: `ruff check scripts tests`, `pytest`,
  `node --check scripts/ui/web/app.js`, AHK parse-check (only if a `.ahk`
  changed — none currently).
- Live-FLM eval recorded: claude_code default reproduces the 2.1.1 baseline
  (it's identity, so this must hold); generic_chat either clears its gate
  (≥ 8/10) → production, or is shipped **experimental** with a UI note and
  claude_code stays the default.
- Version `2.2.0` in the 4 files + `## 2.2.0` CHANGELOG heading + SPEC version
  line; `prompt_builder_preview` confirmed read-only.
- Landed via ruleset-toggle squash-merge PR; tag `v2.2.0`; installer built;
  release notes PATCHed.

---

## Step 0 — Feature branch & scope

The work is loose on `live`. Move only the prompt-builder change onto a branch;
keep the unrelated `docs/history-visibility-plan.md` out.

```powershell
cd C:\Users\ArseniyGrechenkov\Downloads\transfer\transfer\flowkey-pub2
git switch -c feat/2.2.0-prompt-builder live
```

Include in the eventual commit: `scripts/ffp_prompt_builder.py`,
`tests/test_prompt_builder.py`, `tools/prompt_builder_eval.py`, the modified
`scripts/*`, `scripts/ui/web/*`, `config/*`, `setup/defaults/*`, `tests/*`,
`SPEC.md`, `CHANGELOG.md`, `pyproject.toml`, `installer/fastflowprompt.spec`,
and `docs/prompt-logic-refinement-plan.md`. **Exclude**
`docs/history-visibility-plan.md` (separate feature).

## Step 1 — Lint fix

```powershell
python -m ruff check --fix scripts tests tools
python -m ruff check scripts tests    # must print "All checks passed!"
```

Acceptance: `ruff check scripts tests` clean. (`tools/` is outside the CI gate
but fix it too for hygiene.)

## Step 2 — Add the three CI-gated tests (`tests/test_prompt_builder.py`)

All model-free (no LLM), so they run in the normal `pytest` gate.

1. **Deterministic fallback always satisfies its own adapter contract** (covers
   the gate-fail branch's safety):
   ```python
   def test_fallback_shape_passes_its_own_contract():
       import ffp_prompt_builder as pb
       for cfg in ({"target_agent": "claude_code", "action_mode": "review"},
                   {"target_agent": "generic_chat", "include_verification": True}):
           s = pb.PromptBuilderSettings.from_config(cfg)
           intent = pb.resolve_intent(s, "sample request")
           shaped = pb.render_fallback(s, intent, "sample request")
           assert pb.validate(shaped, s).valid, cfg
   ```

2. **generic_chat conversion is not misread as an echo** (covers the
   target-aware `has_target_structure` coupling):
   ```python
   def test_generic_markdown_conversion_not_flagged_as_echo():
       import ffp_prompt_builder as pb, grammar_fix
       s = pb.PromptBuilderSettings.from_config({"target_agent": "generic_chat"})
       good_md = "## Task\nBuild X.\n## Context\n- repo Y\n## Constraints\n- keep scoped"
       assert grammar_fix.is_weak_prompt_echo("build x for repo y", good_md, s) is False
       echo = "build x for repo y"  # reformatted input, no structure
       assert grammar_fix.is_weak_prompt_echo("build x for repo y", echo, s) is True
   ```

3. **action_mode is preserved** (review ⇒ no edit order; plan ⇒ no code edits):
   ```python
   def test_action_mode_review_and_plan_do_not_order_edits():
       import ffp_prompt_builder as pb
       for mode in ("review", "plan"):
           s = pb.PromptBuilderSettings.from_config({"target_agent": "generic_chat", "action_mode": mode})
           text = pb.render_fallback(s, pb.resolve_intent(s, "the PR"), "the PR")
           assert "Implement the requested change" not in text
   ```
   (Confirm the exact forbidden/required strings against
   `ffp_prompt_builder._action_instruction`; `tools/prompt_builder_eval.py`
   already encodes these strings — reuse them.)

Run: `python -m pytest tests/test_prompt_builder.py -q` → all pass.

## Step 3 — Identity hardening (minor)

`build_system_prompt` returns identity only when `legacy_system_prompt` is
truthy. Add a guard/test so a missing mode prompt can't silently synthesize:

- In `tests/test_prompt_builder.py`, assert that with default settings and an
  EMPTY `legacy_system_prompt`, the result is still safe (either raises or
  returns empty so the caller's `if not system_prompt: raise` at
  `ffp_llm_client.py:298` fires). Document the dependency in a one-line comment
  on `build_system_prompt`.

## Step 4 — Measure generator quality (the Phase 5 gate)

### 4a. Model-free eval (must already pass)
```powershell
python tools\prompt_builder_eval.py           # text table; expect all valid=True
python tools\prompt_builder_eval.py --json     # capture if you want an artifact
```

### 4b. Add a live-FLM mode to `tools/prompt_builder_eval.py`
Add an opt-in path (`--live --base-url <url> [--model <m>] [--bearer <b>]`) that,
per case, sends `build_system_prompt(settings, intent, legacy_system_prompt=…)`
+ the case input to the local OpenAI-compatible endpoint, then runs
`validate()` (and the `must_contain`/`must_not_contain` checks) on the **model
output** — not the fallback. Emit per-adapter pass counts. Keep the default
model-free path unchanged.

### 4c. Run it against FastFlowLM
```powershell
# start FLM (default provider/model)
Start-Process -FilePath flm -ArgumentList @('serve','qwen3.5:4b','--pmode','turbo','--host','127.0.0.1','--port','52625') -WindowStyle Hidden
Start-Sleep -Seconds 12
python tools\prompt_builder_eval.py --live --base-url http://127.0.0.1:52625 --model qwen3.5:4b --json `
  --out data\benchmarks\prompt_builder_eval_flm_<yyyymmdd>.json
```

Acceptance gates (record the numbers in the artifact + CHANGELOG/plan):
- **claude_code cases** ≥ the 2.1.1 baseline. (Default is identity, so a drop
  here means something else regressed — investigate before shipping.)
- **generic_chat cases** ≥ 8/10 → ship generic_chat as production.
- generic_chat < 8/10 → keep it, but label it **experimental** in the dashboard
  (a note that non-Claude targets may need retries) and keep claude_code the
  default. Do NOT block the release on it — the default path is unaffected.

## Step 5 — Version bump to 2.2.0 + docs

Bump all four (V18 — CI smoke fails on drift):
- `scripts/_version.py` → `__version__ = "2.2.0"`
- `pyproject.toml` → `version = "2.2.0"`
- `installer/installer.iss` → `#define AppVersion    "2.2.0"`
- `README.md` → `Current version: `2.2.0`` (and add a one-line Prompt-builder
  note under the Config/dashboard section)

Also:
- `CHANGELOG.md`: move the "Prompt builder settings…" bullet from `## Unreleased`
  into a new `## 2.2.0` section with a short headline; note generic_chat's
  production/experimental status per Step 4.
- `SPEC.md`: update the version line to `2.2.0`; confirm V24 + the
  `prompt_builder` cfg block + `prompt_builder_preview` action + `ACTIONS count`
  already present (they are).
- Confirm `prompt_builder_preview` is **not** in `_WRITE_ACTIONS`
  (`scripts/ffp_daemon.py:821`) — it's a deterministic read.
- Cosmetic version strings in `installer/README.md` / `installer/sign.ps1` if
  present.

## Step 6 — Validate (full gate run)

```powershell
python -m ruff check scripts tests                       # All checks passed!
python -m pytest tests -q                                # all pass
# JS (no node on this box — use VS Code's Electron as node):
$env:ELECTRON_RUN_AS_NODE=1
$p = Start-Process "$env:LOCALAPPDATA\Programs\Microsoft VS Code\Code.exe" `
  -ArgumentList '--check','scripts\ui\web\app.js' -Wait -PassThru -NoNewWindow `
  -RedirectStandardError "$env:TEMP\pb.err" -RedirectStandardOutput "$env:TEMP\pb.out"
"exit=$($p.ExitCode)"                                    # exit=0
```
AHK parse-check only if a `.ahk` was touched (none in this change).

Also spot-check the drop-in by hand: with default config,
`build_system_prompt(default, intent, legacy=CLAUDE_PROMPT_SYSTEM_PROMPT)` ==
`CLAUDE_PROMPT_SYSTEM_PROMPT` (already asserted by
`test_default_system_prompt_is_identity`).

## Step 7 — Commit & push (tooling gotchas baked in)

```bash
git add -A                       # review with: git status --short  (exclude history-visibility-plan.md if undesired)
git commit -m "2.2.0: prompt-builder settings for prompt mode (claude_code + generic_chat)"
```
Push — GCM only offers interactive auth (hangs non-interactive), so supply the
token and REDACT it:
```bash
export GIT_TERMINAL_PROMPT=0
TOKEN=$(printf 'protocol=https\nhost=github.com\nusername=agr77one\n\n' | git credential fill 2>/dev/null | sed -n 's/^password=//p')
git push "https://agr77one:${TOKEN}@github.com/agr77one/Fastflow.git" \
  feat/2.2.0-prompt-builder:feat/2.2.0-prompt-builder 2>&1 | sed "s/${TOKEN}/[REDACTED]/g"
```
Verify: `git ls-remote --heads origin feat/2.2.0-prompt-builder` (repo is public,
read works anonymously) and `origin/main` still `a60f7e0`.

## Step 8 — PR + CI + ruleset-toggle squash-merge

- Open PR base=`main`, head=`feat/2.2.0-prompt-builder` (title/body ≈ CHANGELOG).
- Wait for all CI checks green (Python 3.11/3.12/3.13, AutoHotkey syntax,
  Web dashboard JS).
- Merge with the ruleset toggle (ruleset id **17344133** "Protect main"):
  GET ruleset → PUT `enforcement=disabled` → `PUT /pulls/<n>/merge`
  (`merge_method=squash`, **full 40-char** head sha) → **always** PUT
  `enforcement=active` → delete branch. Do the GET/PUT JSON in **PowerShell**
  (Invoke-RestMethod + ConvertTo-Json) — Git-Bash `/tmp` paths aren't readable by
  native Windows Python. Restore enforcement even if the merge fails.

## Step 9 — Tag, build, release notes

```bash
git fetch origin --quiet
git switch live && git merge --ff-only origin/main       # live -> new main
git tag -a v2.2.0 origin/main -m "Flowkey 2.2.0 — prompt builder"
git push "https://agr77one:${TOKEN}@github.com/agr77one/Fastflow.git" v2.2.0 2>&1 | sed "s/${TOKEN}/[REDACTED]/g"
```
The tag triggers `release-installer.yml` → `Flowkey-Setup-2.2.0.exe` + a GitHub
Release with a GENERIC body. Monitor the run to completion, verify the asset,
then **PATCH the release body** with real notes:
`Invoke-RestMethod PATCH /repos/agr77one/Fastflow/releases/{id}` from PowerShell
(build the JSON with ConvertTo-Json; token in a header var, redacted).

## Step 10 — Post-release

- `git switch live` stays at the new main (already ff-merged).
- Update memory `flowkey-release-flow` current version → 2.2.0.
- Optionally start the codex/cursor adapters as 2.2.x (only after generic_chat
  is proven), per the design doc's staged rollout.

## Gotchas reference (bit us before — don't re-learn)

- **No `node` on this machine** → JS check via VS Code Electron
  (`ELECTRON_RUN_AS_NODE=1 … Code.exe --check`).
- **git-credential-manager is interactive-only** → non-interactive push hangs;
  fetch token via `git credential fill`, push to
  `https://agr77one:<token>@github.com/...`, redact from all output.
- **Git-Bash `/tmp` / `mktemp -d` paths aren't readable by native Windows
  Python** → do GitHub API calls + JSON in PowerShell (Invoke-RestMethod /
  ConvertTo-Json), not bash+python.
- **PR merge API** requires the full 40-char head sha.
- **Ruleset toggle** must always re-enable enforcement (even on failure).
