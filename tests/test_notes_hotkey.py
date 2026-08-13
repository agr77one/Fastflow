"""Static contracts for the AutoHotkey Notes quick-capture path."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAMMAR_AHK = (ROOT / "scripts" / "grammarFix.ahk").read_text(encoding="utf-8")
CLIPBOARD_AHK = (ROOT / "scripts" / "lib" / "clipboard.ahk").read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^{re.escape(name)}\([^)]*\) \{{(.*?)^\}}",
        source,
    )
    assert match, f"{name} function not found"
    return match.group(1)


def test_note_hotkey_stages_selection_then_opens_notes_composer():
    body = _function_body(GRAMMAR_AHK, "CaptureNoteImpl")

    assert "CaptureSelectedText" in body
    assert 'RunActionViaDaemon("note_stage_capture"' in body
    assert 'OpenWebDashboard("notes")' in body
    assert body.index('RunActionViaDaemon("note_stage_capture"') < body.index(
        'OpenWebDashboard("notes")'
    )


def test_note_hotkey_never_uses_legacy_immediate_save_or_clipboard_fallback():
    body = _function_body(GRAMMAR_AHK, "CaptureNoteImpl")

    assert "save_note" not in body
    assert "CaptureTextFromSelectionOrClipboard" not in body
    assert "priorClip" not in body


def test_selection_only_helper_restores_clipboard_without_reusing_prior_text():
    body = _function_body(CLIPBOARD_AHK, "CaptureSelectedText")

    assert "ClipboardAll()" in body
    assert "RestoreClipboard(clipSaved)" in body
    assert 'Send("^c")' in body
    assert "priorClip" not in body
    assert 'captureSource := (fromSelection != "") ? "selection" : "none"' in body


def test_no_selection_continues_to_stage_blank_composer():
    body = _function_body(GRAMMAR_AHK, "CaptureNoteImpl")

    assert "selectionFound := CaptureSelectedText" in body
    assert "if !CaptureSelectedText" not in body
    assert '"text":"' in body
    assert 'Notify("Flowkey", "📝 Blank note ready.")' in body
