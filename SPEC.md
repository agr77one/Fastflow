# Flowkey SPEC

Caveman-encoded (compression, not amputation). Paths / ids / action names / numbers / endpoints verbatim.

## §G goals

- G1: local LLM hotkey assistant, Windows. ⊥ cloud, ⊥ analytics, ⊥ telemetry off-machine.
- G2: run on AMD NPU (FastFlowLM) | any CPU/GPU (Ollama). provider ? → auto-fallback to other.
- G3: web dashboard = single home for chat, notes, meetings, config, benchmark, notifications.
- G4: heavy LLM cost (prefill) → pre-compute after-hours, read cached.

## §C context / stack

- front: AHK v2 — `scripts/grammarFix.ahk` + `scripts/lib/*` + `scripts/ui/*`
- daemon: Python stdlib — `scripts/ffp_daemon.py` @ `http://127.0.0.1:52650` (single-instance = bound port)
- LLM: FastFlowLM NPU @ `:52625` | Ollama @ `:11434`, OpenAI-compat `POST /v1/chat/completions`
- dashboard: daemon-served `scripts/ui/web/{index.html,app.js,styles.css}`, CSP `default-src 'self'`
- paths: `scripts/paths.py` → USER_ROOT/{config,data,logs}; `_version.py` = version src of truth
- version: `2.2.0` (prompt_builder release branch); `2.1.0` released (tag on `7835d4b`); repo `agr77one/Fastflow`
- run tree = `flowkey-pub2` (worktree, branch `live`=origin/main). old `FastFlowPrompt_Local_Setup`=1.5.0 stale.

## §I interfaces

- cfg blocks: `enabled`, `llm`, `providers.{fastflowlm,ollama}`, `server`, `routing`, `prompt_builder`, `notes`, `chat`, `modes`, `dictionary`, `notifications`, `meetings`, `hotkeys`
- api: `POST /action/<name>` ! header `X-FFP-API: 1` → 200 `{ok,result,error,elapsed_ms}`
- api: `GET /` → dashboard; `GET /healthz` → `{ok,version,api,actions}`
- action: `config_snapshot` → full cfg; `apply_config_patch {patch}` → merge (whitelist `filter_config_patch`)
- action: `prompt_builder_preview {settings?,sample?}` → deterministic local preview (`⊥` LLM call)
- action: `notify_gate {title,message}` → `{show,reason,category}` (logs); `notifications_log {limit}` → rows
- action: `quill_status` → `{reachable,enabled,server,server_version}`
- action: `quill_search_meetings {query,limit}` → `{meetings:[{id,title,date,duration,participants,url}]}`
- action: `meeting_overview` → `{enabled,reachable,today:{count,minutes},week:{count,minutes}}`
- action: `meeting_process {meeting_id,title,date,url}` → digest rec (writes `meeting_digests.jsonl`)
- action: `meeting_batch_run {max_per_run?}` → `{ok,processed,queued,errors,skipped}`; `meeting_batch_status` → status
- action: `meeting_digest_get {meeting_id}` → `{found,digest_md,...}`; `meeting_digests_list` → `{digests,count}`
- action: `meeting_ask {meeting_id,question}` → `{ok,answer,source,seconds}`
- action: `meeting_actions_list {range:week|month}` → `{range,items:[{id,text,owner,status,...}],counts}`
- action: `meeting_action_set_status {id,status:pending|accepted|rejected}` → `{ok}`
- action: `meeting_week_summary {week_offset}` → `{ok,week_label,meeting_count,summary}`
- mcp: Quill @ `http://127.0.0.1:19532/mcp` — Streamable-HTTP, SSE `data:`, `Mcp-Session-Id` header; init→notifications/initialized→tools/call
- cmd: `flm serve <model> --pmode turbo --host 127.0.0.1 --port 52625`
- data: `data/{meeting_digests,meeting_action_status,meeting_skips,notifications,chat_threads}.jsonl`
- autostart: HKCU Run `FastFlowPrompt` → bundled `AutoHotkey64.exe` + `grammarFix.ahk`; `FlowkeyGitSync` → `sync.ps1`
- sched: Windows task `FlowkeyGitSync` daily 12:00 → `sync.ps1` (ff-only pull, guarded)
- ACTIONS count = 75

## §V invariants

- V1: ∀ `POST /action` → header `X-FFP-API` = API_VERSION `1` | 403
- V2: ∀ req Host ∉ {`127.0.0.1`,`localhost`} → 403 (DNS-rebind defense)
- V3: config patch → only keys ∈ `filter_config_patch` whitelist; rest dropped
- V4: flm/llm/`meetings.mcp_url` ! loopback http/https | reject (SSRF guard)
- V5: dashboard DOM → createElement/textContent only; ⊥ innerHTML; ⊥ native alert/confirm/prompt → use `confirmDialog`
- V6: history text redacted by default (`history_store_text` false)
- V7: notes ops ! contained via `_vault_subpath`; `../` → reject
- V8: notify → daemon `notify_gate` decides+logs; AHK `Notify_Impl` fail-OPEN if daemon unreachable
- V9: notify category `errors` (critical) → bypass DND & quiet_hours; still logged; still honors per-cat disable
- V10: notify master `enabled`=false → mute all incl errors
- V11: scheduler run ⟺ `should_run_batch` = meetings.enabled & in-window & (idle ? idle≥threshold)
- V12: after-hours batch idempotent → skip meeting ∃ cached digest
- V13: `meeting_*` actions ∉ `_WRITE_ACTIONS` → self-lock (`_io_lock`/`_batch_lock`), separate files ∴ long batch ⊥ block config/notify writes
- V14: NPU prefill ∝ context (~17s @ ~7k tok) ∴ pre-compute digests after-hours; ask grounds on cached digest
- V15: action-item id = `sha1(meeting_id|norm(text))[:16]` → stable across re-list ∴ status persists
- V16: week = Monday 00:00 local; month = 1st 00:00 local
- V17: builtin mode prompts locked from patching (only `tone.preset` patchable)
- V18: version ∀ ∈ {`_version.py`,`pyproject.toml`,`installer/installer.iss`,`README.md`} equal; CI smoke fails on drift
- V19: `main` branch-protected (ruleset 17344133) → land via PR + ruleset toggle; ⊥ direct push
- V20: change gates ! pass: `ruff check scripts tests`, `pytest`, `node --check scripts/ui/web/app.js`, AHK parse-check (PowerShell `/ErrorStdOut`)
- V21: local data (config/data/logs/vendor/certs) ∈ `.gitignore` ∴ pull moves code only, never user data
- V22: `sync.ps1` ∃ uncommitted tracked changes → skip pull (⊥ clobber un-pushed WIP)
- V23: meeting `NoContentError` & age ≥ 2d → skip-marker (`meeting_skips.jsonl`) ∴ ⊥ re-queue ever; age < 2d → retry (Quill transcript may still sync); non-content errors ⊥ skip-mark. batch errors → `daemon.log` only (⊥ UI panel, per user)
- V24: prompt_builder default cfg ⇒ `CLAUDE_PROMPT_SYSTEM_PROMPT` identity; non-default prompt validation target-aware (XML only when effective structure=xml); built-in `modes.prompt.system_prompt` still locked

## §T tasks

```
id|status|task|cites
T1|x|web dashboard = home (chat/notes/config/bench/notifications/meetings)|V5
T2|x|Ollama provider + auto-fallback + hw-aware model sizing|G2
T3|x|notifications gate+log+panel (`ffp_notifications`)|V8,V9,V10
T4|x|Quill meetings + after-hours digest scheduler (`ffp_quill`,`ffp_meetings`)|V11,V12,V14
T5|x|overview meeting hours + action-item board + weekly review|V15,V16
T6|x|git autosync: `sync.ps1` + daily task + autostart→flowkey-pub2|V21,V22
T7|x|2.1.0 released (tag on `7835d4b`); 2.1.1 maintenance batch on `fix/2.1.1-meeting-skip-and-audit`|V18,V19
T8|.|installer clean-VM smoke test|—
T9|.|[AUDIT] dead-code: unused daemon helper + 2 AHK wrappers + stale chat-popup config key + obsolete settings ref in test fixture + deprecated install shims|—
T10|x|[AUDIT-P1] autostart: unify 3 divergent Run keys (daemon/src-installer/pkg-installer) → single HKCU entry|V20,B6
T11|x|[AUDIT] old open_chat default `^+t` in first-run + `grammar_fix` snapshot + web config fallback → all `^!c` (2.1.1)|B5
T12|x|[AUDIT] seed thinner than DEFAULT_CONFIG (deep-merge fills at runtime ∴ harmless) → drift guard freezes known delta in `test_config_seeds` (2.1.1)|—
T13|x|[AUDIT] `bootstrap.cmd` hardcoded stale `1.6.0` in build banner → version-neutral `<version>` (2.1.1)|V18
T14|.|[AUDIT] quality-gate gaps: installer policy drift, autostart reg-name drift, bootstrap output name, README/dashboard tab count|V20
T15|x|[DOCS] dashboard docs: 7 tabs → 8 (added Benchmark) in README (2.1.1)|—
T16|x|[DOCS] autostart docs conflict: main says no machine-wide entry; installer docs+impl still describe it → align on HKCU-only|B6
T17|.|[DOCS] installer layout: build script says flattened, installer.md still shows nested layout|—
T18|.|[DOCS] provider roadmap marks selector/status UX incomplete → update to reflect it exists|—
T19|x|[DOCS] first-run wizard text "chat popup" → "Open chat" + hotkey now `^!c` (2.1.1)|B5
T20|x|[DOCS] daemon log location — audited 2.1.1: no stale ref in README/docs; nothing to change|—
T21|x|prompt_builder cfg + claude_code identity + generic_chat adapter + dashboard controls/preview|V17,V24
```

## §B bugs

```
id|date|cause|fix
B1|2026-06|"Run batch now" ran vs last-saved cfg not form → "disabled"|autosave meetings patch before `meeting_batch_run`
B2|2026-06|bench history blank row from 0-point result file|skip `rows==[]` in `ffp_benchmark.history`
B3|2026-06|autostart → stale tree / empty `flowkey-public`|repoint HKCU Run → flowkey-pub2 + bundled AHK
B4|2026-06|install launch: AHK called `.py`, shipped only `.exe`|flatten bundle to {app} + AHK→exe bridge (PR #19)
B5|2026-06|`Ctrl+Shift+T` open_chat collided w/ browser reopen-tab|default → `^!c`; tray label = configured hotkey
B6|2026-07|3 divergent autostart Run keys: daemon HKCU\Run\FastFlowPrompt, `install.ps1` HKCU\Run\Flowkey (different name!), `installer.iss` optional HKLM\Run\Flowkey → toggle blind to other 2, could double-launch|unify on HKCU\Run\FastFlowPrompt everywhere; drop installer.iss HKLM task; uninstall now cleans the HKCU value; guarded by `test_installer_autostart.py`
B7|2026-07|"Run batch now" → "0 of 5, 5 errors" ∀ run: 5 Quill stub recordings (⊥ minutes ⊥ transcript) re-queued forever ∵ idempotency = digest_exists only|typed `NoContentError` → skip store `meeting_skips.jsonl` (age ≥ 2d guard) + `skipped` count in result; queue checks digest ∧ skip; reasons stay in daemon.log|V23
```
