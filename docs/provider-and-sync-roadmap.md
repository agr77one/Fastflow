# Provider And Sync Roadmap

## Current state

Implemented on the local-LLM side:

- neutral `llm` config block with legacy compatibility
- provider detection for `ollama` and `fastflowlm`
- provider-routed model list / pull / remove actions
- dashboard fallback to the effective local provider instead of stale FLM defaults
- dashboard control path to start Ollama when it is the active provider

Shipped since this roadmap was written (2.0):

- explicit provider selector in Dashboard → Config with per-provider status
  (installed / API reachable / active) and provider-scoped controls
  (start server, disabled-with-reason for unsupported actions)

Still intentionally incomplete:

- dual-provider selection and comparison on one machine (side-by-side)
- multi-device sync and shared memory
- encrypted/shared storage design

## Provider management UX follow-up

### Goal

Make provider choice explicit and easy to recover from:

- `Ollama only`
- `FastFlowLM only`
- `Both installed`

### UI additions — DONE (2.0)

1. ✅ Provider selector on the dashboard Config surface.
2. ✅ Per-provider status rows: installed / API reachable / active-inactive /
   supported actions.
3. ✅ Per-provider actions: start (incl. `ollama serve`); FLM-only controls
   hide/disable when Ollama is active.
4. ✅ Unsupported controls are visibly disabled.

Remaining UI follow-up:

- stop-server where supported; inline "open install/update docs" links.

### Runtime rules

- If configured provider is unavailable and another provider is available, use the effective provider for read paths.
- Do not silently rewrite the user config during fallback.
- Mutating actions should target the active/effective provider unless the UI explicitly asks for another one.

## Runtime and service polish

### Goal

Keep the app responsive even when a local provider is installed but not running.

### Rules

- Reachability checks should fail fast.
- Model-list calls should not block the dashboard waiting on a dead local API.
- Dashboard refresh should reuse provider/model snapshots instead of re-fetching them in multiple tabs.

### Follow-up work

1. Add cached provider snapshot data to the daemon response for the dashboard.
2. Add background refresh for model lists and update checks.
3. Add a dedicated runtime status action for Ollama process + API state if needed.

## Multi-device sync architecture

### Objective

Support multiple devices with shared memory while keeping the app local-first.

### Data classes

1. Device-local only
   - hotkeys
   - local runtime/provider settings
   - window/UI state
   - machine-specific install diagnostics

2. Syncable user data
   - notes metadata and note bodies
   - saved prompts/snippets
   - user dictionary / protected words
   - reusable chat memory summaries
   - optional conversation threads

3. Derived/ephemeral data
   - telemetry counters
   - temporary pull/install progress
   - daemon health state

### Proposed storage model

Use a local-first event log plus materialized views.

- Local append-only event log: `data/sync/events/*.jsonl`
- Materialized state:
  - `data/sync/state/notes.json`
  - `data/sync/state/memory.json`
  - `data/sync/state/devices.json`

Each event should include:

- `event_id`
- `device_id`
- `entity_type`
- `entity_id`
- `op`
- `payload`
- `logical_ts`
- `created_at`

### Sync transport options

Recommended order:

1. File-backed shared folder sync
   - lowest complexity
   - good for same-user multi-PC setups
   - works with OneDrive/Dropbox/network share

2. Small hosted sync service
   - device registration
   - auth
   - remote event exchange

3. Peer-to-peer LAN sync
   - optional future enhancement

### Recommended first implementation

Implement file-backed sync first.

Why:

- matches the current local-first design
- avoids account/auth work early
- easiest to debug
- works well for the user’s stated multi-device memory goal

### Conflict model

Use per-entity merge strategies:

1. Notes
   - last-writer-wins for metadata
   - append-preserving merge for tags where practical
   - duplicate conflict copy if note bodies diverge heavily

2. Dictionary / protected words
   - set union

3. Memory summaries
   - append new summary chunks
   - periodically compact into a merged summary

4. Conversation threads
   - append by message id
   - if parent mismatch, fork thread branch instead of overwriting

### Identity model

Each install gets:

- stable `device_id`
- user-visible `device_name`
- optional `sync_profile_id`

Store in a local file such as `config/device_identity.json`.

### Security model

Phase the security work.

Phase A:
- plain local/shared-folder sync
- no secrets in synced payloads

Phase B:
- optional encrypted payload blobs
- shared key loaded from user secret/config

Phase C:
- hosted account/device auth if remote service is added

## Suggested implementation phases

### Phase 5

- finish dashboard performance work
- complete provider startup UX
- clean first-run/provider wording

### Phase 6

- add explicit provider selection UX
- support both Ollama and FastFlowLM on one PC with side-by-side status

### Phase 7

- add local device identity
- add sync config schema
- scaffold event log + materialized state layer

### Phase 8

- implement shared-folder sync for notes + memory summaries
- add import/export and resync controls

### Phase 9

- conflict review UX
- selective sync toggles
- optional encryption

## Open questions before coding sync

1. What exactly counts as “memory” to sync first:
   - notes only
   - notes + chat summaries
   - full conversations

2. Is shared-folder sync enough for v1, or is a hosted sync endpoint required?

3. Should sync be single-user only at first?

4. What user data must never leave a device by default?

## Recommended next coding step

Build Phase 7 around a file-backed event log and sync only:

- notes
- protected words
- memory summaries

Leave full thread sync and hosted transport for later phases.
