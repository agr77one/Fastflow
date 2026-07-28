"""Underspecified-request handling + the R8 echo gate (2.4.3).

Reported 2026-07-28. The request

    develop a app that allows to perfrom a full schedule for my meeting a proper PM would

produced an output that repeated it three times — as <task>, as the only real
<constraints> bullet, and as <output_format> — padded with two scope guards, typos
included. It was structurally valid and invented nothing, so it scored 5/7 on the
machine-checkable rubric with no hard fail and would pass at 7/7 under a lenient
judge. The existing echo detector could never catch it: is_weak_prompt_echo()
returns False as soon as the output carries target structure (V32), and the v2
finalizer always emits structure.

Fixes: detect a source with nothing to decompose and render a clarify shape
instead; normalize typos/article agreement in surfaced text; add R8 (no section
restates the task) as a disqualifying rubric item.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import ffp_prompt_builder as P
import pytest

ROOT = Path(__file__).resolve().parents[1]
VAGUE = "develop a app that allows to perfrom a full schedule for my meeting a proper PM would"
CSV = (
    "build a python script that reads a folder of CSVs, validates rows against a schema, "
    "writes an error report with file and line numbers"
)


def _eval_module():
    spec = importlib.util.spec_from_file_location("ev", ROOT / "tools" / "prompt_speed_quality_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render(source: str) -> str:
    settings = P.PromptBuilderSettings.from_config(None)
    intent = P.resolve_intent(settings, "")
    return P.ground_prompt_v2_output("draft-is-discarded", settings, intent, P._normalize_text(source))


# ---------- language normalization -------------------------------------------------------

@pytest.mark.parametrize(("raw", "expected"), [
    ("develop a app", "develop an app"),
    ("perfrom a schedule", "perform a schedule"),
    ("a app that allows to perfrom", "an app that allows to perform"),
    ("an schedule", "a schedule"),
    ("Perfrom the task", "Perform the task"),          # case preserved
    ("recieve teh data adn store", "receive the data and store"),
])
def test_normalize_source_language(raw, expected):
    assert P.normalize_source_language(raw) == expected


@pytest.mark.parametrize("text", [
    "a user story",       # "user" starts with a consonant sound
    "a unique id",
    "a one-off script",
    "a utility module",
])
def test_article_exceptions_not_broken(text):
    # "a user" must NOT become "an user".
    assert P.normalize_source_language(text) == text


def test_normalization_does_not_touch_ordinary_text():
    assert P.normalize_source_language(CSV) == CSV


# ---------- underspecified detection -----------------------------------------------------

def test_the_reported_input_is_underspecified():
    assert P.is_underspecified(P.normalize_source_language(VAGUE)) is True
    assert len(P.source_clauses(P.normalize_source_language(VAGUE))) == 1


def test_decomposable_request_is_not_underspecified():
    assert P.is_underspecified(CSV) is False
    assert len(P.source_clauses(CSV)) >= 3


def test_source_clauses_excludes_scope_guard_padding():
    # source_clauses reports only real clauses; padding belongs to the renderer.
    clauses = P.source_clauses(P.normalize_source_language(VAGUE))
    for guard in P._SAFE_SCOPE_GUARDS:
        assert guard not in clauses


# ---------- clarify render ---------------------------------------------------------------

def test_clarify_shape_replaces_the_echo():
    out = _render(VAGUE)
    task = out.split("<task>")[1].split("</task>")[0].strip()
    constraints = out.split("<constraints>")[1].split("</constraints>")[0]
    out_fmt = out.split("<output_format>")[1].split("</output_format>")[0].strip()

    # Typos fixed in the surfaced task.
    assert "an app" in task and "perform" in task
    assert "a app" not in out and "perfrom" not in out
    # The request is no longer pasted into constraints or output_format.
    assert "full schedule for my meeting" not in constraints
    assert "full schedule for my meeting" not in out_fmt
    # Scope-guard padding is gone; real unknowns are named instead.
    assert "Preserve all stated requirements." not in constraints
    assert "unspecified" in constraints
    assert "open questions" in out_fmt


def test_clarify_output_still_satisfies_the_v2_contract():
    out = _render(VAGUE)
    settings = P.PromptBuilderSettings.from_config(None)
    result = P.validate(out, settings)
    assert result.valid, result.errors


def test_clarify_invents_no_requirements():
    # Every bullet must assert only that something was NOT stated - naming
    # concrete artifacts (an agenda, a database, a web UI) would be invention.
    out = _render(VAGUE).lower()
    for invented in ("agenda", "attendee", "calendar", "database", "react", "sqlite", "rest api"):
        assert invented not in out


def test_good_request_render_is_unchanged_by_the_clarify_path():
    out = _render(CSV)
    assert "The request states the goal only" not in out      # not the clarify shape
    body = out.split("<constraints>")[1].split("</constraints>")[0]
    items = [line for line in body.splitlines() if line.strip().startswith("-")]
    assert len(items) == 4                                     # real decomposition preserved


# ---------- R8 rubric item ---------------------------------------------------------------

OLD_ECHO = """<task>
Develop a app that allows to perfrom a full schedule for my meeting a proper PM would.
</task>
<context>
No additional context stated.
</context>
<constraints>
- Develop a app that allows to perfrom a full schedule for my meeting a proper PM would.
- Preserve all stated requirements.
- Do not add unstated requirements.
</constraints>
<output_format>
A app that allows to perfrom a full schedule for my meeting a proper PM would.
</output_format>"""

JUDGE_PASS = {"r3": True, "r4": True, "invented_requirement": False}


def test_r8_fails_the_reported_echo():
    ev = _eval_module()
    scored = ev.score_output(OLD_ECHO, judgment=JUDGE_PASS)
    assert scored["rubric"]["r8"] is False
    assert scored["echoed_section"] in ("output_format", "constraints")
    # Disqualifying even with every judged item passing - the old blind spot.
    assert scored["passed"] is False


def test_r8_passes_every_fixed_case():
    ev = _eval_module()
    for case in ev.FIXED_CASES:
        scored = ev.score_output(_render(case["input"]), judgment=JUDGE_PASS)
        assert scored["rubric"]["r8"] is True, f"{case['name']}: {scored['echoed_section']}"


def test_r8_allows_one_restating_bullet_among_distinct_ones():
    # A long request's task comes from its first sentence, so a bullet repeating
    # that sentence is normal. Only a set with NOTHING else is a failure.
    ev = _eval_module()
    text = """<task>
Add a webhook import endpoint to the existing Python service.
</task>
<context>
No additional context stated.
</context>
<constraints>
- Add a webhook import endpoint to the existing Python service.
- Reject malformed JSON with 400.
- Authenticate using the existing X-Webhook-Key middleware.
</constraints>
<output_format>
A webhook endpoint.
</output_format>"""
    assert ev.score_output(text, judgment=JUDGE_PASS)["rubric"]["r8"] is True


def test_r8_ignores_boilerplate_when_deciding():
    ev = _eval_module()
    text = """<task>
Build a reporting tool for quarterly numbers.
</task>
<context>
No additional context stated.
</context>
<constraints>
- Build a reporting tool for quarterly numbers.
- Preserve all stated requirements.
- Leave unspecified choices to the implementer.
</constraints>
<output_format>
A reporting tool.
</output_format>"""
    scored = ev.score_output(text, judgment=JUDGE_PASS)
    assert scored["rubric"]["r8"] is False          # only a restatement + padding
    assert scored["echoed_section"] == "constraints"


def test_fixed_case_set_includes_the_reported_input():
    ev = _eval_module()
    names = {case["name"] for case in ev.FIXED_CASES}
    assert "vague_unparseable_scheduler" in names
    case = next(c for c in ev.FIXED_CASES if c["name"] == "vague_unparseable_scheduler")
    assert case["input"] == VAGUE
