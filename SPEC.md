# Flowkey SPEC

Caveman-encoded (compression, not amputation). Paths / ids / action names / numbers / endpoints verbatim.

## §G goals

- G1: local LLM hotkey assistant, Windows. ⊥ cloud, ⊥ analytics, ⊥ telemetry off-machine.
- G2: run on AMD NPU (FastFlowLM) | any CPU/GPU (Ollama). provider ? → auto-fallback to other.
- G3: web dashboard = single home for chat, notes, meetings, config, benchmark, notifications.
- G4: heavy LLM cost (prefill) → pre-compute after-hours, read cached.
- G5: `prompt:` warm p50 ≤ 15s & p90 ≤ 20s; v2 p50 ≤ 60% v1; quality median ≥ v1; invented requirements = 0.

## §C context / stack

- front: AHK v2 — `scripts/grammarFix.ahk` + `scripts/lib/*` + `scripts/ui/*`
- daemon: Python stdlib — `scripts/ffp_daemon.py` @ `http://127.0.0.1:52650` (single-instance = bound port)
- LLM: FastFlowLM NPU @ `:52625` | Ollama @ `:11434`, OpenAI-compat `POST /v1/chat/completions`
- dashboard: daemon-served `scripts/ui/web/{index.html,app.js,styles.css}`, CSP `default-src 'self'`
- paths: `scripts/paths.py` → USER_ROOT/{config,data,logs}; `_version.py` = version src of truth
- version: `2.5.0` = living Notes workspace + vision board; repo `agr77one/Fastflow`
- run tree = `flowkey-pub2` (worktree, branch `live`=origin/main). old `FastFlowPrompt_Local_Setup`=1.5.0 stale.

## §I interfaces

- cfg blocks: `enabled`, `llm`, `providers.{fastflowlm,ollama}`, `server`, `routing`, `prompt_builder`, `notes`, `chat`, `modes`, `dictionary`, `notifications`, `meetings`, `hotkeys`
- api: `POST /action/<name>` ! header `X-FFP-API: 1` → 200 `{ok,result,error,elapsed_ms}`
- api: `GET /` → dashboard; `GET /healthz` → `{ok,version,api,actions}`
- action: `config_snapshot` → full cfg; `apply_config_patch {patch}` → merge (whitelist `filter_config_patch`)
- action: `notes_query {query?,kind?,status?,category?,tag?,sort?,limit?,offset?}` → `{results,count,facets}`
- action: `note_create {title?,body?,kind?,category?,tags?,color?,due?,source?}` → note
- action: `note_update {note_id,revision,patch}` → note | conflict
- action: `note_organize {note_id,revision?}` → note; local LLM may update category/suggested metadata only
- action: `note_trash {note_id}` / `note_restore {note_id}` → note; `note_delete {note_id,permanent:true}` → deleted
- action: `notes_board_get` → `{board,placements}`; `notes_board_save {revision,board}` → board | conflict
- action: `note_stage_capture {text?,source_app?}` / `note_take_staged` → quick-capture payload
- data: note Markdown frontmatter schema v2 → stable `note_id`, `kind`, `status`, `tags`, `color`, `pinned`, `due`, `created`, `updated`, `revision`
- data: `<vault>/.flowkey/board.json` → board sections + placements keyed by `note_id`
- action: `recent_history {limit?}` → newest history rows; `input_text`/`output_text` iff stored @ write-time
- action: `prompt_builder_preview {settings?,sample?}` → deterministic local preview (`⊥` LLM call)
- config: `prompt_builder.prompt_version` ∈ {`v1`,`v2`}; default `v2`; v1 = instant rollback
- config: `server.warm_on_start` bool + `server.keep_warm_minutes` 0..1440; warmup best-effort
- cmd: `python tools/prompt_speed_quality_eval.py [--live] [--cold-warm] [--reuse-v1 PATH] [--rescore PATH] [--export-v1 PATH] [--runs N] [--judge-file PATH] [--out PATH]` → old-vs-v2 JSON
- data: `data/benchmarks/prompt_v2_ab_<date>.json` → speed+quality gate evidence
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
- mcp: Quill @ `http://127.0.0.1:19532/mcp` — Streamable-HTTP, SSE `data:`, `Mcp-Session-Id` header; init→notifications/initialized→tools/call; `get_transcript {meeting_id,include_private_notes:true}`; tool `isError` → typed failure
- cmd: `flm serve <model> --pmode turbo --host 127.0.0.1 --port 52625`
- data: `data/{meeting_digests,meeting_action_status,meeting_skips,notifications,chat_threads}.jsonl`
- autostart: HKCU Run `FastFlowPrompt` → bundled `AutoHotkey64.exe` + `grammarFix.ahk`; `FlowkeyGitSync` → `sync.ps1`
- sched: Windows task `FlowkeyGitSync` daily 12:00 → `sync.ps1` (ff-only pull, guarded)
- ACTIONS count = 86

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
- V20: change gates ! pass: `ruff check scripts tests`, `python -m pytest`, `node --check scripts/ui/web/app.js`, AHK parse-check (PowerShell `/ErrorStdOut`)
- V21: local runtime data (config/data/logs/vendor/certs) ∈ `.gitignore`; exception = sanitized fixed-input `data/benchmarks/prompt_v*.json` release evidence
- V22: `sync.ps1` ∃ uncommitted tracked changes → skip pull (⊥ clobber un-pushed WIP)
- V23: meeting `NoContentError` & age ≥ 2d → skip-marker (`meeting_skips.jsonl`) ∴ ⊥ re-queue ever; age < 2d → retry (Quill transcript may still sync); non-content errors ⊥ skip-mark. batch errors → `daemon.log` only (⊥ UI panel, per user)
- V24: prompt_builder default cfg ⇒ `CLAUDE_PROMPT_SYSTEM_PROMPT_V2`; `prompt_version=v1` ⇒ `CLAUDE_PROMPT_SYSTEM_PROMPT_V1`; non-default validation target-aware; built-in `modes.prompt.system_prompt` still locked
- V25: History view toggle = display-only; ⊥ mutate `history_store_text`; ⊥ reveal text absent from jsonl row
- V26: History tab load default view = Telemetry (text hidden) even when storage visible
- V27: default v2 output → exactly ordered `<task>`,`<context>`,`<constraints>`,`<output_format>`; task = 1 imperative sentence; constraints = 3–5 concrete items; output_format = 1 line; ⊥ preamble/fence/`<think>`; ≤ 220 tokens
- V28: prompt runtime caps short/medium/long = 240/320/420 tokens; retries ! same strategy cap
- V29: A/B gate → ≥12 fixed inputs; 1 warmup + ≥5 timed/style/input; p50/p90/min/max, TTFT, completion tokens, decode tok/s, seconds/output-token; v2 speed gate + median quality ≥v1 + invented=0 + R1 rate ≥v1
- V30: v1 prompt constant retained + config-selectable without built-in prompt patching
- V31: daemon startup + configured idle interval → best-effort FastFlowLM warmup; failure logs only, ⊥ daemon startup failure
- V32: prompt output with valid target structure → ⊥ anti-echo retry solely from line/word overlap
- V33: default v2 surfaced output → source-clause grounding + fixed scope guards only; raw model inventions ⊥ surface
- V34: FastFlowLM force restart → old port observed closed before new spawn; ⊥ return `already_running` from dying instance
- V35: default v2 → exactly 1 short LLM draft call; ⊥ anti-echo/rescue calls; V33 finalizer supplies surfaced 4-section contract
- V36: pre-2.3 prompt_builder identity cfg (⊥ `prompt_version`, legacy default fields) → migrate v2+concise; any custom field → preserve
- V37: `chat_send_stream` streams reply deltas as `text/event-stream` (SSE `data:`/`event:` frames), persists the full-or-partial turn exactly once under the daemon write-lock; CSRF `X-FFP-API` + Host gates still apply; dashboard falls back to `chat_send` iff the stream ⊥ opens (never after tokens arrive); `send()`/AHK/grammar/prompt paths unchanged (⊥ streamed)
- V38: `recommend_models` returns EVERY candidate tagged yes/tight/no/unknown + `fit_reason`; UI ⊥ hide any (oversized → marked + confirm-before-pull). Fit signal precedence: provider `footprint_gb` → effective params (MoE active `a<N>b` < total, else total) vs budget
- V39: configured active model ⊥ installed → `model_recommendations.active_model.status="not_installed"` + dashboard warning naming model/remedy; listing error/exception → `"unknown"` (⊥ claim broken)
- V41: `flm bench` start → refuse iff catalog `footprint_gb` + 32k-context headroom > usable mem, w/ plain-language reason; unknown footprint ∨ lookup failure → allow (⊥ block on a guess)
- V42: benchmark running → keep-warm tick ⊥ warms (`skipped_benchmark_running`); ∵ bench 10-20min > 15min keepalive ∴ collision is the norm
- V43: provider 200-response carrying `error` ∧ ⊥ `choices` → raise that message verbatim (⊥ generic "no usable text"); `error` + `choices` → ignore, return completion
- V44: source ⊥ decomposable (real clauses < 2) → clarify shape: unknowns named, ⊥ request echoed into constraints/output_format, ⊥ scope-guard padding; still ⊥ invention (V33 holds)
- V45: surfaced v2 text → article agreement + fixed typo map normalized (∵ render copies user wording verbatim); ⊥ meaning change
- V46: A/B rubric = 8 items; R8 = ⊥ section restates `<task>` (coverage-of-task ≥ 0.8), boilerplate-excluded set-wise on constraints; R8 false ⇒ disqualifying ∀ other scores; gate pass ≥ 7/8 ∧ R8 ∧ ⊥ invented
- V40: model picker + installed list = app-styled elements (⊥ native `<datalist>`/`<select size>`, ∵ browser chrome ignores page CSS); theme-aware + keyboard-navigable (↑↓/Enter/Esc)
- V47: Notes tab = notes + organization only; note config controls ∈ Config tab
- V48: ∀ note API/UI identity = stable `note_id`; file move/rename ⊥ identity change
- V49: AI enrichment ? fill blank/generated metadata; user-authored body/title ⊥ overwrite
- V50: default remove → Trash, recoverable; permanent delete ! explicit Trash action + confirm
- V51: v1 Markdown → schema v2 migration preserves body + unknown frontmatter + source path; migration backup ∃ before rewrite
- V52: board placement references `note_id`; remove placement ⊥ delete/mutate note
- V53: capture hotkey w/ selection → staged prefill; ⊥ selection → blank composer; stale clipboard ⊥ silent capture
- V54: note writes + board writes atomic; stale `revision` → conflict, ⊥ overwrite
- V55: note card/editor controls keyboard reachable; desktop split workspace + ≤720px stacked layout
- V56: note `due` date-only value renders same calendar day ∀ timezone; ⊥ UTC date shift
- V57: Quill transcript call matches discovered schema (`meeting_id` + `include_private_notes`); MCP JSON-RPC/tool `isError` ∨ validation payload → typed failure, ⊥ LLM input/cache; poisoned legacy digest → ⊥ idempotency hit, eligible reprocess
- V58: model-created note category accepted ⟺ config opt-in ∧ `is_new` ∧ high confidence ∧ safe normalized ≤2-segment slug; accepted category atomically deduped+sorted into cfg; otherwise Inbox + suggestion; V49 holds
- V59: Config → keyboard-accessible section nav + collapsible groups + one selected section visible + sticky Save/Revert; view state local-only persisted; ≤720px responsive
- V60: future Activity workspace may join Telemetry+History + explicit `Save as note`; Notes remains authored knowledge; V6,V25,V26 hold
- V61: note schema-migration write ∈ `_NOTES_LOCK` (double-checked, serialized vs concurrent note writes); migration-write failure → index/lookup fall back to unmigrated read, ⊥ silently drop note
- V62: `ffp_quill` public read fns (`get_minutes`,`get_transcript`,`search_meetings`,`list_recent_meetings`) catch `QuillToolError` → soft-degrade (empty text/list); V57's typed-failure raise stays internal to `call_tool`, ⊥ leaks past the public API

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
T9|x|[AUDIT] dead-code: removed deprecated install shims (`setup/install_release.*`,`bootstrap_release.sh`, superseded by `install.py`); chat-popup key + other residuals already clean in 2.0/2.1 (2.2.1)|—
T10|x|[AUDIT-P1] autostart: unify 3 divergent Run keys (daemon/src-installer/pkg-installer) → single HKCU entry|V20,B6
T11|x|[AUDIT] old open_chat default `^+t` in first-run + `grammar_fix` snapshot + web config fallback → all `^!c` (2.1.1)|B5
T12|x|[AUDIT] seed thinner than DEFAULT_CONFIG (deep-merge fills at runtime ∴ harmless) → drift guard freezes known delta in `test_config_seeds` (2.1.1)|—
T13|x|[AUDIT] `bootstrap.cmd` hardcoded stale `1.6.0` in build banner → version-neutral `<version>` (2.1.1)|V18
T14|x|[AUDIT] quality-gate gaps: tab-count drift now guarded by `test_dashboard_tabs_parity`; autostart reg-name (`test_installer_autostart`) + bootstrap name (T13) already covered (2.2.1)|V20
T15|x|[DOCS] dashboard docs: 7 tabs → 8 (added Benchmark) in README (2.1.1)|—
T16|x|[DOCS] autostart docs conflict: main says no machine-wide entry; installer docs+impl still describe it → align on HKCU-only|B6
T17|x|[DOCS] installer README layout diagram → flattened `{app}` bundle, matches `installer.iss` (2.2.1)|—
T18|x|[DOCS] provider roadmap: selector + per-provider status UX marked shipped (2.0); only side-by-side + sync remain (2.2.1)|—
T19|x|[DOCS] first-run wizard text "chat popup" → "Open chat" + hotkey now `^!c` (2.1.1)|B5
T20|x|[DOCS] daemon log location — audited 2.1.1: no stale ref in README/docs; nothing to change|—
T21|x|prompt_builder cfg + claude_code identity + generic_chat adapter + dashboard controls/preview|V17,V24
T22|x|History Telemetry/Exposed views + inline redacted/visible storage control/help|V5,V6,V25,V26
T23|x|prompt-v2 fixed A/B speed+quality harness + tests|V29
T24|x|prompt-v2 default + v1 rollback selector + 240/320/420 caps|V24,V27,V28,V30,V32
T25|x|FastFlowLM startup+idle keep-warm + cold/warm measurement support|V31
T26|x|2.3.0 release evidence + version/docs rebaseline after A/B gate passes|V18,V29,V33,V34,V35,V36
T27|x|streaming Chat tab (SSE): `ffp_chat.stream_send` + daemon `_STREAM_ACTIONS`/`_sse_frame`/`_stream_action` + `app.js` fetch-reader w/ `chat_send` fallback; live FLM first-token 1.58s|V37
T30|x|clarify shape for underspecified requests + typo/article normalization + R8 echo gate + reported input in fixed set (2.4.3)|V44,V45,V46
T29|x|bench memory guard + keep-warm/bench mutual exclusion + provider-error surfacing (2.4.2)|V41,V42,V43
T28|x|model picker 2.4.1: footprint/MoE sizing + never-hide + active-model health + merged single Models card + app-styled combobox|V38,V39,V40
T31|x|Notes schema v2 repository: stable ids, zero-loss migration, CRUD, Trash, indexed query, atomic board store|V7,V48,V49,V50,V51,V52,V54
T32|x|daemon Notes v2 actions + backward-compatible read/move/delete + staged capture|V1,V2,V3,V7,V48,V50,V53,V54
T33|x|move vault/categories/extraction/LLM Notes settings → Config single-save; Notes tab config-free|V3,V47
T34|x|Notes-only card workspace: composer, editor, smart views, filters, tags, archive/Trash, vision board drag/order|V5,V47,V48,V50,V52,V54,V55,V56
T35|x|capture hotkey → Notes quick composer w/ staged selection or blank body|V53
T36|x|2.5.0 docs/version/migration + full release gates|V18,V20,V47,V48,V49,V50,V51,V52,V53,V54,V55
T37|x|repair Quill transcript schema/error handling + poison-cache retry + re-digest latest|V12,V14,V20,V23,V57
T38|x|guarded local-model category creation + sorted category manager + note organize action|V3,V7,V20,V49,V58
T39|x|compact Config section navigation + collapsible cards + sticky save|V5,V20,V55,V59
T40|.|Activity workspace: merge Telemetry+History, card/detail UI, explicit Save as note|V5,V6,V25,V26,V60
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
B8|2026-07-10|new prompt-v2 eval imports violated Ruff I001/UP035|V20 caught; sort imports + `Callable` from `collections.abc`
B9|2026-07-10|prompt-v2 eval test import block retained extra blank line|V20 caught; normalize import spacing
B10|2026-07-10|stale user-level `pytest.exe` exited 1 + ⊥ diagnostics while active interpreter pytest passed|V20 → interpreter-bound `python -m pytest`
B11|2026-07-10|`node` absent from desktop PowerShell PATH|V20; run bundled workspace `node.exe --check`
B12|2026-07-10|structured v1 rollback output retried ∵ first anti-echo gate ignored target structure|V32
B13|2026-07-10|focused Ruff command accidentally included `scripts/ui/web/app.js`|V20 caught; JS → Node syntax gate only
B14|2026-07-10|warmup test insertion split existing daemon action assertions into wrong test|V20 caught; restore test block boundary
B15|2026-07-10|FastFlowLM usage emits `decoding_duration`; eval parsed only `decode_duration`|V29; accept both aliases
B16|2026-07-10|first live v2 probe omitted closing XML tags + invented libraries/config/files/error behavior|V27,V29; explicit skeleton + anti-invention list
B17|2026-07-10|second live v2 probe stopped after valid `</task>` ∵ descriptive skeleton treated as optional sequence|V27; literal all-tags template + final-tag completion rule
B18|2026-07-10|literal-template probe passed structure but trap/vague inputs gained inferred CLI/browser/file details; CSV got 2 constraints|V29; entailment-only content + exact safe fillers
B19|2026-07-10|entailment wording fixed vague case but CSV/CLI still gained conventional schema/args/I/O details|V29; clause-copy rules + grounded example + final unsupported-detail audit
B20|2026-07-10|3 live prompt-only revisions still invented conventional details on 4B trap inputs|V33; deterministic source-clause grounding before output surfaces
B21|2026-07-10|CLI prompt test pinned prose from first v2 draft after contract-preserving tune|V20 caught; assert stable final-tag rule
B22|2026-07-10|V27 sentence check treated dots/question marks inside identifiers/regex as boundaries|V27; split only terminal punctuation before whitespace/end
B23|2026-07-10|cold probe force-restart returned `already_running` while killed FLM socket still closing; service then vanished|V34
B24|2026-07-10|live v2 p50 11.22s but ratio 61.69% ∵ discarded raw 4-section draft still decoded median 121 tokens|V35; 1 short task draft + deterministic V33 finalizer
B25|2026-07-10|broad V35 patch changed runtime/system branches instead of retry branches|V20 caught; restore + scope conditions by surrounding logic
B26|2026-07-10|default v2 input ≥ routing threshold still made compression subcalls; 1 huge clause could exceed V27|V27,V35; bypass routing + bound grounded sections
B27|2026-07-10|upgraded 2.2 cfg retained `detail_level=balanced` ∴ v1 selector missed new concise-default identity|V24,V30; `prompt_version=v1` authoritative legacy XML path
B28|2026-07-10|2.2 persisted identity cfg lacked `prompt_version` + kept `balanced` ∴ upgrade bypassed v2 path|V36; narrow legacy-identity migration
B29|2026-07-27|`qwen3.6-moe:35b-a3b` invisible in picker: `parse_params_b` read MoE total 35B (⊥ 3B active) → `fits=no` → `app.js` `continue` silently dropped it ∴ manual `flm pull`|V38; footprint-first sizing + MoE active parse + never-hide (marked + confirm)
B30|2026-07-27|FLM 0.9.45 invalidated locally-pulled `qwen3.5:4b` (stamped 0.9.43) → FLM reports ⊥ installed; ⊥ detection ∴ next hotkey failed opaquely (Ollama absent ∴ ⊥ fallback)|V39; `_active_model_health` + dashboard warning
B31|2026-07-27|model picker ⊥ followed app styling ∵ native `<datalist>` popup drawn by browser chrome (page CSS inert) + native `<select size>`|V40; custom app-styled combobox + list
B32|2026-07-27|`app.js` set `models-title` = "Installed models — <provider>" ∴ overwrote merged card heading at runtime|V40 caught in browser; title → "Models — <provider>"
B33|2026-07-27|new CSS block referenced undefined `--muted`/`--card`/`--bad` (pre-existing pattern elsewhere in stylesheet: 6+2+3 refs, never defined ∴ inert)|use defined tokens `--text-muted`/`--surface`/`--warn`; pre-existing refs left for separate cleanup
B38|2026-07-28|vague 1-clause request ("develop a app ... a proper PM would") → deterministic render echoed it as task ∧ sole constraint ∧ output_format + 2 scope guards, typos kept; scored 5/7 machine (7/7 lenient judge), ⊥ hard-fail ∴ gate green on a pure echo. `is_weak_prompt_echo` ⊥ fires ∵ V32 exempts structured output, and V33 finalizer always emits structure|V44,V45,V46; clarify shape + normalization + R8
B39|2026-07-28|self-caught: first R8 metric = coverage-of-candidate ∴ flagged good CSV decomposition (bullets legitimately reuse task words)|invert → coverage-of-TASK
B40|2026-07-28|2nd R8 metric flagged long/debug cases ∵ task = sentence 1 ∴ one bullet legitimately restates it|judge constraints set-wise: fail iff ∅ remains after dropping restatements ∧ boilerplate
B35|2026-07-27|`flm bench qwen3.6-moe:35b-a3b` died 4s in w/ driver `0xc01e0200` (page-in failed): 24.3GB weights + 32k-sweep KV > 25.6GB usable; ⊥ preflight ∴ user saw only a hex code|V41; catalog-footprint preflight w/ explanation
B36|2026-07-27|keep-warm thread ⊥ aware of benchmarks: warms/reloads active model on 15min tick during a 10-20min bench ∴ NPU+mem contention mid-run|V42; `ffp_benchmark.is_running()` gate in `_warm_model_once`
B37|2026-07-27|FLM returns HTTP **200** + `{"error":"Failed to load <model> model!"}`; `_call_openai_compatible`/`ffp_chat` read only `choices` ∴ real cause discarded → "Local LLM returned no usable text"|V43; surface the error body
B34|2026-07-27|self-caught: `_active_model_health` tested membership vs `_provider_list("all")`; ollama "all" = installed+suggested (`ffp_provider_runtime:80`) ∴ never-pulled model → false `installed=True` (⊥ warn)|V39; trust `details` (unfiltered, authoritative) else re-list w/ `installed` filter
B41|2026-07-29|Notes due `2026-08-04` parsed as UTC midnight → EDT displayed Aug 3|V56; parse date-only @ local noon
B42|2026-07-29|Quill `get_transcript` sent `id`; live schema requires `meeting_id`+`include_private_notes`; `call_tool` ignored `isError` ∴ 133-char validation error fed to LLM + cached as digest|V57
B43|2026-07-29|`fresh_modules` teardown popped `notes` after test collection; later daemon-action tests patched a stale module while action-local `import notes` resolved a new module ∴ tests touched the real vault + became order-dependent|rebind `sys.modules["notes"]` to the isolated test module; V20 full-suite gate
B44|2026-08-13|pre-release review: V57's `call_tool` raise (the B42 fix) went ⊥ caught by `get_minutes`/`get_transcript`/`search_meetings`/`list_recent_meetings`; `run_batch`'s pagination loop, `meeting_overview`, and `process_meeting`'s `NoContentError` skip-path all broke on any Quill tool error: scheduled batch could silently no-op (status never updated), Overview widget 500'd instead of `reachable=False`, dead meetings retried forever instead of perma-skip|V62; catch `QuillToolError` in the 4 public read fns, restore module's documented fail-soft contract
B45|2026-08-13|pre-release review: `_ensure_note_schema`'s migration write ran outside `_NOTES_LOCK` ∴ could race a locked `note_update`/`note_organize` on the same file (dueling `uuid4()` note_id, last-writer-wins corruption); `_load_note_index`/`_find_note_path` silently dropped a note from every listing/lookup on migration-write failure (e.g. read-only file)|V61; lock + double-check inside `_ensure_note_schema`; index/lookup fall back to unmigrated read on failure instead of dropping the note
B46|2026-08-13|pre-release review: `trash_note` was the only note-mutating fn ⊥ taking a `revision` param ∴ "Move to Trash" could silently act on a note that changed since the editor loaded it, unlike update/organize/archive; also `_act_note_archive`/`_act_notes_board_save` used a bare `int()` revision parse (raw 500 on bad input) unlike `_act_note_update`/`_act_note_organize`'s guarded parse|V54; add revision param + conflict check to `trash_note` + daemon action + app.js call site; guard the two bare `int()` parses to match the others
```
