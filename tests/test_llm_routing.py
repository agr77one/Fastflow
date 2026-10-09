"""Long selections must never come back shorter than they went in (SPEC V75; B63).

Regression: `call_flm` split a long selection into chunks but only ever processed the first
three, so grammar mode returned ~3.6k characters for a 10k selection -- and the hotkey replaces
the user's selection with the output, so the rest of their text simply vanished.
"""

from __future__ import annotations

import ffp_config
import ffp_llm_client as llm

ROUTING = {"enabled": True, "long_threshold_chars": 1400, "chunk_size_chars": 1200, "min_chunk_chars": 700}
USAGE = {"prompt_tokens": 0, "completion_tokens": 0}


def _runtime():
    return llm.LlmRuntimeConfig("http://x", "m", 100, False, dict(ROUTING), [], ffp_config.DEFAULT_CONFIG["modes"], None)


def _text(n_words):
    return " ".join(f"word{i:04d}" for i in range(n_words))      # unique, so loss is detectable


def _run(mode, text, api):
    out, _secs, _model, _strategy = llm.call_flm(_runtime(), mode, text, api, lambda: True, lambda force: "", dict(USAGE))
    return out


def _echo(model, system_prompt, user, max_tokens, timeout):
    return user, model


def test_grammar_returns_every_chunk_of_a_long_selection():
    text = _text(1200)                                           # ~10.8k chars -> 9+ chunks
    out = _run("grammar", text, _echo)
    assert "word0000" in out and "word1199" in out
    assert len(out) >= len(text) - 20                            # only chunk-joining whitespace may differ


def test_grammar_passes_unreached_chunks_through_when_a_call_fails():
    text = _text(1200)
    calls = []

    def flaky(model, system_prompt, user, max_tokens, timeout):
        calls.append(user)
        if len(calls) == 3:
            raise RuntimeError("model hiccup")
        return user.upper(), model                               # "corrected" text is visibly different

    out = _run("grammar", text, flaky)
    assert "WORD0000" in out                                     # the first chunks were processed
    assert "word1199" in out                                     # the tail survived, uncorrected
    assert len(out) >= len(text) - 20


def test_grammar_keeps_the_remainder_when_not_even_one_chunk_succeeds():
    text = _text(1200)
    state = {"n": 0}

    def fails_then_fallback(model, system_prompt, user, max_tokens, timeout):
        state["n"] += 1
        if state["n"] <= 1:                                      # the first chunk call fails...
            raise RuntimeError("boom")
        return user.upper(), model                               # ...the short fallback call works

    out = _run("grammar", text, fails_then_fallback)
    assert "word1199" in out and len(out) >= len(text) - 20


def test_summarising_modes_say_when_part_of_the_selection_was_not_used():
    text = _text(3000)                                           # ~27k chars, far past 8 chunks
    out = _run("summarize", text, _echo)
    assert "(Note: only the first" in out and "characters of the selection were used.)" in out


def test_no_note_when_the_whole_selection_was_covered():
    out = _run("summarize", _text(300), _echo)                   # ~2.7k chars -> 3 chunks, all used
    assert "(Note:" not in out


def test_short_input_is_untouched_by_routing():
    out = _run("grammar", "just a short sentence", _echo)
    assert out == "just a short sentence"


# ---------- the reply budget must fit the text it rewrites ------------------------------

def test_rewrite_modes_get_a_reply_budget_that_fits_their_input():
    rt = _runtime()
    for chars in (351, 700, 1000, 1200):
        _model, budget, _strategy = llm.select_runtime(rt, "grammar", "x" * chars)
        assert budget >= chars / 3, f"{chars} chars of grammar input cannot fit in {budget} tokens"


def test_summarising_modes_keep_their_short_budget():
    _model, budget, _strategy = llm.select_runtime(_runtime(), "summarize", "x" * 1000)
    assert budget == 220


def test_short_grammar_input_budget_is_unchanged():
    assert llm.select_runtime(_runtime(), "grammar", "x" * 100)[1] == 160


def test_each_grammar_chunk_is_given_room_for_its_whole_output():
    budgets = []

    def api(model, system_prompt, user, max_tokens, timeout):
        budgets.append((len(user), max_tokens))
        return user, model

    _run("grammar", _text(600), api)
    assert len(budgets) > 1
    assert all(tokens >= chars / 3 for chars, tokens in budgets), budgets
