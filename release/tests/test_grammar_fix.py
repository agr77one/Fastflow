from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def test_load_config_returns_defaults_when_file_missing(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")

    cfg = grammar_fix.load_config()

    assert cfg["enabled"] is True
    assert cfg["flm_model"] == "qwen3.5:4b"
    assert cfg["server"]["performance_mode"] == "balanced"
    assert cfg["history_store_text"] is False


def test_load_config_merges_nested_sections(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.CONFIG_PATH.write_text(
        json.dumps(
            {
                "flm_model": "custom:model",
                "server": {"auto_start": False},
                "routing": {"chunk_size_chars": 900},
                "dictionary": {"protected_words": ["Flowkey"]},
                "modes": {"grammar": {"shortcut": "Ctrl+Alt+G"}},
            }
        ),
        encoding="utf-8",
    )

    cfg = grammar_fix.load_config()

    assert cfg["flm_model"] == "custom:model"
    assert cfg["server"]["auto_start"] is False
    assert cfg["server"]["performance_mode"] == "balanced"
    assert cfg["routing"]["chunk_size_chars"] == 900
    assert cfg["dictionary"]["protected_words"] == ["Flowkey"]
    assert cfg["modes"]["grammar"]["shortcut"] == "Ctrl+Alt+G"
    assert "prompt" in cfg["modes"]


def test_save_config_writes_utf8_json_with_newline(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    payload = {"message": "hello 🙂", "server": {"auto_start": True}}

    grammar_fix.save_config(payload)

    raw = grammar_fix.CONFIG_PATH.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert json.loads(raw)["message"] == "hello 🙂"


@pytest.mark.parametrize(
    ("shortcut", "expected"),
    [
        ("Ctrl+Shift+G", "^+g"),
        ("Alt+N", "!n"),
        ("Win+T", "#t"),
        ("", ""),
        ("Ctrl + Shift + A", "^+a"),
    ],
)
def test_shortcut_to_ahk_translates_variants(fresh_modules, shortcut, expected):
    grammar_fix = fresh_modules("grammar_fix")

    assert grammar_fix.shortcut_to_ahk(shortcut) == expected


def test_normalize_output_cleans_smart_punctuation_and_spacing(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")

    value = grammar_fix.normalize_output(' “Hello” — world  \n  next\tline ')

    assert value == '"Hello" - world\nnext line'


def test_append_history_writes_jsonl_line(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    entry = {"mode": "grammar", "elapsed_seconds": 0.2}

    grammar_fix.append_history(entry)

    rows = grammar_fix.HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0]) == entry


def test_split_chunks_returns_single_chunk_when_short(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")

    assert grammar_fix._split_chunks("short text", 50) == ["short text"]


def test_split_chunks_prefers_newline_boundaries(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.ROUTING_CFG["min_chunk_chars"] = 20
    text = ("alpha " * 20).strip() + "\n" + ("beta " * 20).strip()

    chunks = grammar_fix._split_chunks(text, 120)

    assert len(chunks) == 2
    assert chunks[0].endswith("alpha")
    assert chunks[1].startswith("beta")


def test_split_chunks_merges_tiny_trailing_chunk(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.ROUTING_CFG["min_chunk_chars"] = 50
    text = ("alpha " * 30) + "tail"

    chunks = grammar_fix._split_chunks(text, 80)

    assert len(chunks) == 2
    assert chunks[-1].endswith("tail")


@pytest.mark.parametrize(
    ("mode", "text", "strategy"),
    [
        ("prompt", "tiny", "prompt_short"),
        ("prompt", "x" * 600, "prompt_medium"),
        ("grammar", "x" * 800, "grammar_medium"),
        ("grammar", "x" * 1500, "grammar_long"),
    ],
)
def test_select_runtime_picks_expected_strategy(fresh_modules, mode, text, strategy):
    grammar_fix = fresh_modules("grammar_fix")

    _, _, selected = grammar_fix._select_runtime(mode, text)

    assert selected == strategy


def test_dict_protect_returns_original_when_no_words(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.PROTECTED_WORDS = []

    masked, mapping = grammar_fix._dict_protect("Flowkey")

    assert masked == "Flowkey"
    assert mapping == {}


def test_dict_protect_and_restore_round_trip(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.PROTECTED_WORDS = ["Flowkey", "LLM"]

    masked, mapping = grammar_fix._dict_protect("Flowkey helps every LLM user.")
    restored = grammar_fix._dict_restore(masked, mapping)

    assert "__FFPDICT0__" in masked
    assert "__FFPDICT1__" in masked
    assert restored == "Flowkey helps every LLM user."


def test_dict_protect_preserves_first_match_casing(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.PROTECTED_WORDS = ["fastflowprompt"]

    masked, mapping = grammar_fix._dict_protect("FASTFLOWPROMPT and fastflowprompt")

    assert mapping["__FFPDICT0__"] == "FASTFLOWPROMPT"
    assert grammar_fix._dict_restore(masked, mapping).startswith("FASTFLOWPROMPT")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.2.3", (1, 2, 3)),
        ("1.2", (1, 2, 0)),
        ("v2.5.0-beta1", (2, 5, 1)),
    ],
)
def test_version_tuple_parses_and_pads(fresh_modules, value, expected):
    grammar_fix = fresh_modules("grammar_fix")

    assert grammar_fix._version_tuple(value) == expected


def test_deep_merge_updates_nested_dicts_in_place(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    dst = {"server": {"auto_start": True, "mode": "balanced"}, "enabled": True}

    grammar_fix._deep_merge(dst, {"server": {"mode": "max"}, "enabled": False})

    assert dst == {"server": {"auto_start": True, "mode": "max"}, "enabled": False}


def test_compute_usage_stats_aggregates_history(fresh_modules, sample_history):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.HISTORY_PATH.write_text(
        "\n".join(json.dumps(row) for row in sample_history) + "\n",
        encoding="utf-8",
    )

    stats = grammar_fix.compute_usage_stats()

    assert stats["total"] == 3
    assert stats["by_mode"] == {"grammar": 2, "prompt": 1}
    assert stats["total_prompt_tokens"] == 66
    assert stats["total_completion_tokens"] == 44
    assert stats["p50_latency_seconds"] == 1.2


def test_compute_usage_stats_ignores_invalid_rows(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.HISTORY_PATH.write_text('{"mode":"grammar","elapsed_seconds":1}\nnot json\n', encoding="utf-8")

    stats = grammar_fix.compute_usage_stats()

    assert stats["total"] == 1
    assert stats["by_mode"] == {"grammar": 1}


def test_compute_dashboard_data_builds_recent_and_hours(fresh_modules, sample_history):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.HISTORY_PATH.write_text(
        "\n".join(json.dumps(row) for row in sample_history) + "\n",
        encoding="utf-8",
    )

    data = grammar_fix.compute_dashboard_data()

    assert data["latencies_recent"] == [0.5, 1.2, 2.0]
    assert "slowest" not in data
    assert data["hour_buckets"][9] == 1
    assert data["hour_buckets"][10] == 2


def test_call_flm_short_grammar_uses_tight_prompt_and_restores_dictionary(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.CONFIG["modes"]["grammar"]["system_prompt"] = "unused"
    grammar_fix.PROTECTED_WORDS = ["Flowkey"]
    monkeypatch.setattr(grammar_fix, "is_flm_server_reachable", lambda: True)
    calls = []

    def fake_call(model, system_prompt, user_content, max_tokens, timeout_seconds):
        calls.append((model, system_prompt, user_content, max_tokens, timeout_seconds))
        return ("__FFPDICT0__ fixed", model)

    monkeypatch.setattr(grammar_fix, "_call_flm_api", fake_call)

    text, elapsed, model, strategy = grammar_fix.call_flm("grammar", "Flowkey ok")

    assert text == "Flowkey fixed"
    assert elapsed >= 0
    assert model == grammar_fix.FLM_MODEL
    assert strategy == "grammar_short"
    assert "Fix grammar and punctuation only." in calls[0][1]


def test_call_flm_prompt_retries_on_near_verbatim_output(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(grammar_fix, "is_flm_server_reachable", lambda: True)
    responses = iter(
        [
            ("Task: Build a plan", grammar_fix.FLM_MODEL),
            ("Create a concrete execution plan with deliverables.", grammar_fix.FLM_MODEL),
        ]
    )
    prompts = []

    def fake_call(model, system_prompt, user_content, max_tokens, timeout_seconds):
        prompts.append(system_prompt)
        return next(responses)

    monkeypatch.setattr(grammar_fix, "_call_flm_api", fake_call)

    text, _, _, strategy = grammar_fix.call_flm("prompt", "Task: Build a plan")

    assert strategy == "prompt_short"
    assert text == "Create a concrete execution plan with deliverables."
    assert any("meta-framing" in prompt for prompt in prompts)


def test_prompt_mode_cli_writes_output_file(fresh_modules, monkeypatch, tmp_path: Path):
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(grammar_fix, "is_flm_server_reachable", lambda: True)

    def fake_call(model, system_prompt, user_content, max_tokens, timeout_seconds):
        # v1.3.0 tightened the prompt-mode system prompt; "Claude-ready" is the
        # remaining signal that the prompt-mode path was selected.
        assert "Claude-ready" in system_prompt
        return ("<task>Refine onboarding email</task>", model)

    monkeypatch.setattr(grammar_fix, "_call_flm_api", fake_call)

    in_path = tmp_path / "in.txt"
    out_path = tmp_path / "out.txt"
    in_path.write_text("refine onboarding email", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grammar_fix.py",
            "--mode",
            "prompt",
            "--input-file",
            str(in_path),
            "--output-file",
            str(out_path),
        ],
    )
    grammar_fix.main()
    assert "<task>" in out_path.read_text(encoding="utf-8")


def test_call_flm_prompt_falls_back_to_force_shape_when_rescue_fails(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(grammar_fix, "is_flm_server_reachable", lambda: True)

    def fake_call(model, system_prompt, user_content, max_tokens, timeout_seconds):
        if "Claude-ready prompt for Anthropic" in system_prompt:
            raise RuntimeError("rescue failed")
        return ("just copy me", model)

    monkeypatch.setattr(grammar_fix, "_call_flm_api", fake_call)

    text, _, _, _ = grammar_fix.call_flm("prompt", "just copy me")

    assert text.startswith("<task>")


def test_read_and_write_output_file_modes(fresh_modules, monkeypatch, tmp_path: Path):
    grammar_fix = fresh_modules("grammar_fix")
    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "output.txt"
    input_path.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(
        grammar_fix.sys,
        "argv",
        ["grammar_fix.py", "--input-file", str(input_path), "--output-file", str(output_path)],
    )

    assert grammar_fix._read_input_text() == "hello"
    grammar_fix._write_output_text("world")
    assert output_path.read_text(encoding="utf-8") == "world"
