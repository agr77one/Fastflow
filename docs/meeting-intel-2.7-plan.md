# Meeting Intelligence Plan ("2.7")

Date: 2026-10-08
Status: PLANNED. The grammar fix (T45) and the dashboard UI rework (T54, T55) shipped in 2.6.0;
the meeting work below targets 2.7.0.
Target release: **2.7.0** (structured meeting record + mind map + summary views + facets);
**2.7.x** (cross-meeting search + speaker labeling)

## Goal

Turn each meeting into a small structured record — who was there, what was discussed,
what was decided, who owes what — and build every meeting view from that one record:
a mind map, several summary "dimensions", and filters in the meeting list. Later, search
across all meetings and let Chat answer with citations.

Inspired by Plaud's notetaker (mind maps, multidimensional summaries, auto speaker
labels), but local-only: Quill transcripts in, FastFlowLM on the NPU, nothing leaves the
machine.

## What we have today (2.5.4)

- `ffp_meetings.process_meeting` → one LLM call → `digest_md` (markdown) in
  `data/meeting_digests.jsonl`, with `coverage`/`truncated` (V73).
- Action items are regex-parsed out of `digest_md` (`extract_action_items`) and get a
  status (`pending/accepted/rejected`).
- `ask()` answers about ONE meeting, grounded in its digest or live content.
- Transcripts are not cached locally; every read goes to Quill MCP.

## Findings that shape the design (bake-off, 2026-10-08)

### Quill transcript format

Plain text, one block per speaker turn:

```
[83s] Bryson:
<what they said>
[85s] Speaker 2:
...
```

- `[Ns]` = seconds from start on every turn → topics can link back to a moment.
- Speaker labels: `AG` / `Mic (AG)` / `Microphone (AG)` (the user), `Speaker N`
  (anonymous remote), or a real name when Quill knows it. Meeting metadata has
  `participants` (`"me, and Bryson"` or `"other speakers"`), `title`, `date`,
  `duration`, `tags`, `url`.
- Typical length 26–35k chars (~8–12k tokens). Some meetings are tiny (4–660 chars,
  or the literal `null`) and must be skipped.
- ⇒ Speaker separation already exists. "Auto speaker labeling" is a **name mapping**
  problem (`Speaker 2` → `Dana`), not audio diarization.

### Models (FLM catalog, NPU)

| Model | Params | Context | Footprint | Decode |
|---|---|---|---|---|
| `qwen3.5:4b` | 4B | 32,768 | 5.0 GB | ~10 tok/s |
| `qwen3.5:9b` | 9B | 32,768 | 8.7 GB | ~8–11 tok/s |
| `gemma4-it:12b` | 12B | 32,768 | 9.4 GB | ~5 tok/s |

Prefill ~220–240 tok/s; a 30k-char meeting costs ~37 s just to read.

### Extraction approach — three rounds on the same 3 real meetings (7k / 30k / 32k chars)

1. **Single pass, JSON.** 4 of 6 runs returned invalid JSON (a missing opening quote,
   stray `}1] ,`). FLM ignores `response_format`. Slow (76–245 s).
2. **Single pass, line format** (`TOPIC: … | …`). Always parsed, but content got worse:
   the model padded every slot to the limit with `- | -`, copied placeholder text
   literally, or (gemma) returned 2 topics for a 30k meeting.
3. **Map-reduce** — split at turn boundaries into ~3.5k-char sections, one small
   line-format call per section, merge in code. **0 failed sections, full coverage,
   47–174 s at 9B, 31–102 s at 4B.** Two lessons from the first run:
   - A "say NONE if nothing" escape hatch made 8/10 real sections return NONE.
     Require ≥1 `TOPIC` per section instead.
   - Ask explicitly for "EVERY decision / task someone agreed to (with owner)".

4B vs 9B on map-reduce:

| Meeting | 4B time | 9B time | topics 4B/9B | decisions 4B/9B | actions 4B/9B |
|---|---|---|---|---|---|
| 7k | 31 s | 47 s | 4 / 3 | 1 / 1 | 3 / 3 |
| 30k | 102 s | 108 s | 13 / 10 | 1 / 5 | 6 / 6 |
| 32k, 8 speakers | 89 s | 174 s | 14 / 17 | 8 / 13 | 10 / 16 |

4B is reliable and fast but weaker: it misattributed an action owner, under-reports
decisions, emits filler ("None stated in this section.") and duplicate topics.
⇒ **4B stays the floor, 9B is the recommended meeting model**, and owners the model
can't ground are shown as unconfirmed.

A topic-clustering call (6 s) grouped 10 section topics into 4 themes with no leftovers;
the rendered mind map is the reference for Phase D.

## Design

### The `intel` record (schema v1)

Stored on the digest row as `intel` (same jsonl; rows without it stay valid).

```
intel = {
  schema: 1, model, built_at, seconds, sections: {total, ok, failed},
  title, category,                       # from the header call
  people: [{label, name|null, share_pct, turns}],          # parser, no LLM
  topics: [{start_s, label, gist, theme, low_value: bool}],
  themes: [{label, topic_idx: [...]}],    # clustering call → mind-map branches
  decisions: [{text, owner|null, start_s}],
  actions:   [{text, owner|null, owner_confirmed: bool, due|null, start_s}],
  questions: [{text, start_s}],
}
```

### Pipeline (`scripts/ffp_meeting_intel.py`, new)

1. `parse_turns(transcript)` → `[(start_s, label, text)]`; speaking share per label.
   Pure code. Minutes-only sources (no turns) fall back to paragraph sections and the
   `participants` string.
2. `sections(turns, ~3500 chars)` at turn boundaries; a single huge turn splits on
   whitespace.
3. Per section: one call, line format, ≥1 `TOPIC`; `DECISION`/`ACTION`/`QUESTION` as
   stated. `max_tokens` ~450. A failed or unparseable section is recorded and skipped —
   never fails the record.
4. Merge in code: drop filler lines (`none`, `n/a`, `none stated…`), dedupe by word
   overlap, merge adjacent same-topic sections, clamp timestamps to the meeting length,
   check every owner against the speakers/participants/names in the transcript
   (`owner_confirmed`).
5. Header call (title + category, ~300 tokens in) and clustering call (topics → ≤6
   themes; unassigned → "Other").
6. Runs in the existing batch after the digest, and on demand per meeting. Backfilling
   ~250 existing meetings costs ~1.5–3 min each, so it only runs inside the batch's idle
   window, newest first, and is resumable.

### Views (dashboard, no LLM at view time)

- **Mind map**: root = title; branches = themes → topics (with `m:ss`), Decisions,
  Actions (→ owner, unconfirmed in a muted style), Open questions, People (share %).
  Collapsible branches, low-value topics hidden by default, export SVG + Markdown outline.
- **Summary dimensions**: Overview (title, category, people, top themes), By topic,
  By person (their turns' topics + their actions), Decisions & actions, Timeline.
- **Facets** in the meeting list: category, attendee, theme/topic text, "has open actions".
- Every item shows the model that built it; "Rebuild with…" re-runs on another model.

## UI rework: dashboard ⇄ tray parity

The tray's right-click menu applies everything instantly. The dashboard either lacks the
control, shows it read-only, or hides it behind "Save all settings".

| Tray menu item | Daemon action | Dashboard today |
|---|---|---|
| Quick toggles › Performance (Balanced / Max) | `set_perf_balanced` / `set_perf_max` | Config › Essentials, needs Save; Overview read-only |
| Quick toggles › Tone (Formal / Casual / Friendly) | `set_tone_*` | Config › Essentials, needs Save; Overview read-only |
| Quick toggles › History text (Visible / Redacted) | `set_history_*` | Config › Essentials checkbox, needs Save; Overview read-only |
| Quick toggles › Start with Windows | `set_autostart` | buried in Config › Models & AI › "LLM provider & server" |
| Quick toggles › Clipboard watcher | none (AHK marker file) | **missing** |
| Server › Warmup / Stop | `warmup` / `stop` | **missing** (only a warm-model note) |
| Server › Check for updates… | `update_check` / `update_apply` | **missing** (only the FLM runtime update check) |
| Run Diagnostics | `doctor` | **missing** |
| Open Chat / Dashboard / Exit | — | Exit **missing** |

### Design

- **Quick controls card** replaces the read-only Overview "Preferences" card: every
  tray toggle, applied instantly with the same daemon actions as the tray, with a toast
  confirming the change. No Save step.
- **Server & app card**: model/server status, Warmup, Stop, Check for updates →
  "Update to x.y.z" (`update_apply`), Run diagnostics (report in a dialog with Copy),
  Exit Flowkey (confirm first).
- **One behaviour per control**: Config › Essentials reuses the same instant controls
  (outside the Save bar) instead of a second, save-gated copy.
- **Two-way sync**:
  - dashboard → tray: after an instant change the daemon drops a `refresh_tray` marker;
    `PollDaemonMarkers` rebuilds the tray menu so its check marks are never stale.
  - tray → dashboard: the dashboard re-reads state on focus/visibility change.
- **Clipboard watcher** gets a daemon action (`get/set_clipboard_watcher`) that writes
  the existing `.clipboard_watcher_on` marker; AHK reconciles its runtime state with
  the marker on the next poll. **Exit** uses an `exit_app` marker the same way.

### Config menu adjustments (proposed)

- Move "Start with Windows" from Models & AI to Essentials.
- New **App** section: version, updates, diagnostics, exit, autostart.
- Meetings section: meeting-intel settings (model with 9B recommended, idle backfill
  on/off, hide small-talk topics) next to the existing digest settings — **moved to T47**:
  they ship with the feature they configure.

Status: T54 and T55 (all but the meeting settings) are done.

## Phases

| Phase | Scope | Release |
|---|---|---|
| A | T45 grammar fix | shipped in 2.6.0 |
| B | transcript parser + sectioner + tests | 2.7.0 |
| C | intel pipeline, record, batch + on-demand + idle backfill, daemon actions | 2.7.0 |
| D | mind-map view + export | 2.7.0 |
| E | summary dimensions + meeting-list facets | 2.7.0 |
| F | model guidance: recommend 9B in the picker, record model, owner confidence | 2.7.0 |
| U1 | Quick controls + Server & app cards; clipboard-watcher/exit actions; two-way tray sync | shipped in 2.6.0 |
| U2 | Config menu adjustments (Essentials, new App section; Meetings intel settings → C) | shipped in 2.6.0 |
| G | cross-meeting search: local transcript cache, SQLite FTS5 index by turn, search box, Chat retrieves top snippets and answers with `[meeting, m:ss, speaker]` citations | 2.7.x |
| H | speaker labeling: map `Speaker N` → name from transcript cues + participants + calendar, with confidence; user confirms, remembered per person | 2.7.x |
| — | docs, version 2.7.0, full gates (V18, V20) | 2.7.0 |

Search (G) needs no new dependency: Python's bundled SQLite has FTS5 (checked: 3.50.4).
`embed-gemma:300m` (0.6 GB) is in the FLM catalog if keyword search proves too literal.

## Proposed invariants

- **V76** building `intel` never blocks or changes the digest; any failure leaves the
  digest row as it was.
- **V77** one failed section ⊥ failed record: `intel.sections` records `ok`/`failed`.
- **V78** every extracted owner either matches a speaker label, participant or name
  present in the transcript, or carries `owner_confirmed: false`.
- **V79** every `start_s` ∈ [0, meeting length]; out-of-range → dropped to `null`.
- **V80** mind map and summary views are rendered from the stored record — no LLM call
  at view time.
- **V81** every tray-menu control has a dashboard equivalent with the same daemon action
  and the same instant semantics; neither surface shows stale state after the other
  changes it.

## Tests

- Parser: the three real label shapes, multi-line turns, empty / `null` / tiny
  transcripts, minutes-only input.
- Sectioner: turn boundaries, a single giant turn, size bounds.
- Section parser: placeholder/filler lines, markdown bullets, missing fields, NONE.
- Merge: dedupe, adjacent merge, owner grounding, timestamp clamp.
- Pipeline with a fake LLM: a failing section, a garbage section, clustering leftovers.
- Daemon actions + ACTIONS count; dashboard static checks (no inline styles, CSP).

## Risks / open questions

- **Category** is unreliable from the model (both models called a 1:1 a "standup").
  Derive from speaker count + calendar first; model as a tiebreaker.
- **Quill deep links**: does the meeting `url` accept a time offset? If not, timestamps
  open the in-app transcript instead.
- **Thinking models**: calls send `chat_template_kwargs.enable_thinking=false`; verify
  per model family that no reasoning tokens eat the budget.
- **NPU contention**: an intel run holds the meeting model for minutes; the hotkey model
  must re-warm after (existing open item).
- Evidence is 3 meetings from one user; decision recall and owner accuracy were judged
  by reading the output, not against a labeled set.
