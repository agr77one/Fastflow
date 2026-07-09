# Dashboard History Visibility Plan (hidden vs exposed views)

Date: 2026-07-09
Target release: **Unreleased** after `v2.2.0`
Status: IMPLEMENTED + VALIDATED locally — pending commit

## Goal

Give the History tab clear visibility with two explicit view modes:

- **Telemetry view** (default, privacy-safe): what/when/how-fast, no captured text.
- **Exposed view**: reveals captured request/result text for entries that
  actually stored it.

Plus surface the text-storage setting *inside* the History tab so users
understand why text is or isn't present, and can change it in context.

## Implementation status (2026-07-09)

Implemented:

- `recent_history` passes through `input_text` and `output_text` only when those
  fields exist on the jsonl row.
- History tab defaults to **Telemetry** on every load.
- History tab has an **Exposed** view for stored request/result text and shows
  "not stored" for rows captured while redacted.
- History tab surfaces the redacted/visible write-time capture policy with an
  inline toggle using existing daemon actions.
- Config tab storage checkbox has a reusable keyboard-accessible help marker.
- `SPEC.md`, README, CHANGELOG, and tests updated.

Verified:

- `python -m py_compile scripts\ffp_telemetry.py scripts\ffp_daemon.py`
- `node --check scripts\ui\web\app.js`
- `python -m ruff check scripts tests` → all checks passed
- `python -m pytest tests -q` → 347 passed
- `AutoHotkey64.exe /ErrorStdOut tests\test_parse_mode.ahk`
- `AutoHotkey64.exe /ErrorStdOut tests\test_classify_clipboard.ahk`

## Current state (verified in code, 2026-07-09)

- **Setting: `history_store_text`** (boolean, default `false`), a WRITE-time flag.
  - Config UI: `#cfg-store-text` checkbox "Store selected text (off = redacted)"
    (`scripts/ui/web/index.html:338`), saved via `apply_config_patch`
    (`app.js:861`); whitelisted in `filter_config_patch` (`ffp_config.py:504`).
  - Tray menu: "🙈 Redacted" / visible (`tray.ahk`), daemon actions
    `set_history_visible` / `set_history_redacted` / `toggle_history_text` /
    `history_text_status`.
  - Overview shows read-only state (`app.js:249`).
- **Write behavior** (`grammar_fix.py` ~1048-1057): every run always stores
  telemetry (`mode`, `input_chars`, `output_chars`, `elapsed_seconds`,
  `tok_per_sec`, `completion_tokens`, `timestamp`). Only when
  `HISTORY_STORE_TEXT` is true does it also store `input_text` and
  `output_text`.
- **History tab** (`app.js` `loadHistory` → `recent_history` action):
  renders a Telemetry table by default and an Exposed table on request. Stored
  `input_text`/`output_text` is visible only in Exposed view; missing fields show
  "not stored".
- Privacy invariant **V6**: history text redacted by default
  (`history_store_text` false).

**Net:** the storage toggle is now surfaced where its effect is visible, and
the view toggle remains independent from the write-time storage policy.

## Key design constraint (must be honest about this)

Redaction is decided at WRITE time. Therefore:

- The **exposed view can only reveal text that was already stored.** Rows
  captured while redacted have no text on disk — toggling the view (or the
  setting) later does **not** retroactively reveal them.
- The view toggle is a **display preference**; the storage setting is a
  **capture policy**. They are independent and must never be conflated:
  switching to exposed view must not change storage; changing storage must not
  alter existing rows.

So each entry falls into one of three display states:
1. **Telemetry-only** (captured while redacted) → exposed view shows "— (not
   stored)".
2. **Text stored** (captured while visible) → exposed view shows the text.
3. Future: **result stored** (if output-text storage is added) → exposed view
   shows request + result.

## Proposed UX

History tab gains:

1. **A view switch** (segmented control / two tabs): **Telemetry** (default) and
   **Exposed**. Default is always Telemetry on load — privacy-first display.
2. **A storage-state banner** at the top of the tab:
   - Redacted: "Text storage: **Redacted** — new runs store telemetry only.
     [Change in Config]" (or an inline toggle).
   - Visible: "Text storage: **Visible** — new runs store your captured text.
     [Change]".
   This is where the answer to "where is the setting?" lives — brought to the
   tab where its effect is felt.
3. **Exposed view rendering:** each row expands (or adds columns) to show the
   captured request text; rows without stored text show a muted "— (not
   stored — captured while redacted)". If output-text storage is added, show the
   result too.
4. **Exposed-view affordance:** because it renders potentially sensitive text on
   screen, gate the first switch with a one-line in-page notice (not a blocking
   confirm) — "Showing stored request text." No native confirm (V5).

Telemetry view = today's 7-column table, unchanged.

## Inline setting help (hover "read me")

Problem: the Config label **"Store selected text (off = redacted)"** is easy to
miss and doesn't explain what it does — a user can lose track of what it means
or where it is.

Add a small, reusable **help affordance** next to settings: an unobtrusive info
marker (e.g. `ⓘ`) after the label that reveals a short explanation on
**hover and keyboard focus**. Ship it on the storage checkbox first; make the
pattern reusable so other cryptic Config settings can adopt it later.

Design (CSP- and V5-safe):

- A `<span class="help" tabindex="0" role="img" aria-label="What is this?">ⓘ</span>`
  followed by a `<span class="help-text" role="tooltip">…</span>`, both built via
  `createElement` + `textContent` (no innerHTML; content is static copy).
- Reveal purely with CSS on `:hover` and `:focus-within` (keyboard-accessible),
  positioned as a small popover; no JS needed to show/hide. Link with
  `aria-describedby` for screen readers.
- Keep a plain `title` attribute as a no-CSS fallback, but the styled popover is
  the primary affordance (native `title` is slow and unstyled).
- No `alert`/`confirm`/`prompt`; no external assets (CSP `default-src 'self'`).

Copy for the storage setting (exact text to render):

> **Store selected text.** Controls whether the exact text you send to a hotkey
> is saved in History. **Off (default, "redacted"):** only telemetry is kept —
> mode, character counts, timing, tokens — your text is never written to disk.
> **On ("visible"):** the captured request text and generated result text are
> also saved so you can re-read them in History's Exposed view. This is a capture
> policy for *new* runs; it never reveals or hides text already recorded.
> Everything stays on your machine.

This help marker is the Config-tab half; the History-tab storage banner (above)
is the same explanation surfaced where the effect is felt. Both should read
consistently.

## Backend changes (implemented)

- `recent_history` passes through `input_text` and `output_text` when those
  fields are present on a row. No redaction is re-applied at read time because
  redaction already happened at write time.
- A per-entry `has_text` / display flag was not added; the frontend checks field
  presence.
- Generated output text is stored under the same `HISTORY_STORE_TEXT` guard as
  input text.
- All hotkey modes write to the same history file; the `mode` column keeps them
  distinguishable.

## Frontend changes

- `index.html`: History tab gains the view switch, the storage-state banner, and
  an exposed-view container. All DOM via createElement/textContent (V5); no
  innerHTML.
- `index.html` + `styles.css`: add a **reusable help marker** (`ⓘ` + tooltip
  popover, `.help` / `.help-text`, CSS `:hover`/`:focus-within` reveal) and apply
  it to the Config **"Store selected text"** checkbox first. Component is generic
  so other settings can adopt it later.
- `app.js`:
  - `loadHistory` fetches once; render function takes a `view` arg
    (telemetry|exposed).
  - Telemetry render = current table.
  - Exposed render = table/rows with request (and result) text, "— (not stored)"
    for telemetry-only rows.
  - Banner reads `history_store_text` from `config_snapshot` (or
    `history_text_status`) and offers an inline change (calls
    `set_history_visible` / `set_history_redacted`, then refreshes the banner and
    Overview).
  - Default view = telemetry on every tab load.

## Privacy invariants to add (SPEC §V)

- V(new): History **view** toggle is display-only — never changes
  `history_store_text` and never reveals text that was not stored at write time.
- V(new): default History **view** is telemetry (text hidden) on load, even when
  storage is visible.
- Keep V6 (redacted by default) unchanged.

## Tests

- Backend: `recent_history` returns `input_text` for rows written under visible
  mode and omits it for rows written under redacted mode (no read-time reveal).
- If output-text storage is added: stored under visible mode, absent under
  redacted.
- Frontend (`node --check` + a small DOM-logic unit if feasible): exposed render
  shows text when present, "not stored" marker when absent; view toggle does not
  call any storage-mutating action; default view is telemetry.
- Help marker: content is set via `textContent` (no innerHTML), reveals on hover
  AND focus (keyboard-accessible), and is linked via `aria-describedby`; the copy
  matches the History banner wording.
- Config: `history_store_text` still whitelisted, still defaults false.

## Resolved implementation choices

1. **Store output text too:** yes, under the existing `history_store_text` guard.
   No separate output-text flag was added.
2. **Row-level vs global reveal:** global Exposed view with Telemetry as the
   default on every load.
3. **Retention/limit:** unchanged. This batch does not add clear-history or
   max-row controls.
4. **Version:** tracked as Unreleased after `v2.2.0`.

## Rollout order

1. Backend: `recent_history` text passthrough; `output_text` already stored
   under the visible flag; tests.
2. Frontend: view switch + storage banner + exposed render; default telemetry;
   reusable help marker (`.help`/`.help-text` in `styles.css`) on the Config
   "Store selected text" checkbox.
3. Gates: `node --check` app.js, pytest, ruff.
4. SPEC: add the two view invariants; note the History tab now surfaces the
   storage setting.
5. Docs: README History bullet — mention the two views and where storage is set.
