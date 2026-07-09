from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _default_fastflowlm_provider(monkeypatch):
    import ffp_provider_status

    monkeypatch.setattr(
        ffp_provider_status,
        "providers_status",
        lambda provider, base_url: {
            "active": provider,
            "providers": {
                "fastflowlm": {"available": True},
                "ollama": {"available": False},
            },
            "available": ["fastflowlm"],
        },
    )


def test_load_config_returns_defaults_when_file_missing(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")

    cfg = grammar_fix.load_config()

    assert cfg["enabled"] is True
    assert cfg["llm"]["provider"] == "fastflowlm"
    assert cfg["llm"]["model"] == "qwen3.5:4b"
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
    assert cfg["llm"]["model"] == "custom:model"
    assert cfg["llm"]["timeout_seconds"] == 60
    assert cfg["server"]["auto_start"] is False
    assert cfg["server"]["performance_mode"] == "balanced"
    assert cfg["routing"]["chunk_size_chars"] == 900
    assert cfg["dictionary"]["protected_words"] == ["Flowkey"]
    assert cfg["modes"]["grammar"]["shortcut"] == "Ctrl+Alt+G"
    assert "prompt" in cfg["modes"]


def test_load_config_accepts_new_llm_block(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.CONFIG_PATH.write_text(
        json.dumps(
            {
                "llm": {
                    "provider": "ollama",
                    "base_url": "http://127.0.0.1:11434",
                    "model": "llama3.2:3b",
                    "auth_bearer": "ollama",
                    "timeout_seconds": 120,
                    "auto_start": False,
                }
            }
        ),
        encoding="utf-8",
    )

    cfg = grammar_fix.load_config()

    assert cfg["llm"]["provider"] == "ollama"
    assert cfg["flm_base_url"] == "http://127.0.0.1:11434"
    assert cfg["flm_model"] == "llama3.2:3b"
    assert cfg["flm_timeout_seconds"] == 120


def test_refresh_runtime_config_falls_back_to_ollama_when_fastflowlm_unavailable(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.CONFIG_PATH.write_text(
        json.dumps(
            {
                "llm": {
                    "provider": "fastflowlm",
                    "base_url": "http://127.0.0.1:52625",
                    "model": "qwen3.5:4b",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        grammar_fix.ffp_provider_status,
        "providers_status",
        lambda provider, base_url: {
            "active": provider,
            "providers": {
                "fastflowlm": {"available": False, "reachable": False},
                "ollama": {"available": True, "reachable": True},
            },
            "available": ["ollama"],
        },
    )
    monkeypatch.setattr(
        grammar_fix.ffp_provider_runtime,
        "list_models",
        lambda provider, filter_kind, model, no_window, base_url: {
            "models": ["llama3.1:latest"],
            "active": "llama3.1:latest",
            "provider": provider,
        },
    )

    grammar_fix.refresh_runtime_config()

    assert grammar_fix.CONFIGURED_LLM_PROVIDER == "fastflowlm"
    assert grammar_fix.LLM_PROVIDER == "ollama"
    assert grammar_fix.LLM_BASE_URL == "http://127.0.0.1:11434"
    assert grammar_fix.LLM_MODEL == "llama3.1:latest"


def test_build_config_snapshot_reports_effective_provider_when_falling_back_to_ollama(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.CONFIG_PATH.write_text(
        json.dumps(
            {
                "llm": {
                    "provider": "fastflowlm",
                    "base_url": "http://127.0.0.1:52625",
                    "model": "qwen3.5:4b",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        grammar_fix.ffp_provider_status,
        "providers_status",
        lambda provider, base_url: {
            "active": provider,
            "providers": {
                "fastflowlm": {
                    "label": "FastFlowLM",
                    "base_url": "http://127.0.0.1:52625",
                    "installed": False,
                    "reachable": False,
                    "available": False,
                    "capabilities": {"benchmark": False, "model_management": False, "server_control": False, "update_check": False},
                },
                "ollama": {
                    "label": "Ollama",
                    "base_url": "http://127.0.0.1:11434",
                    "installed": True,
                    "reachable": True,
                    "available": True,
                    "capabilities": {"benchmark": False, "model_management": True, "server_control": False, "update_check": False},
                },
            },
            "available": ["ollama"],
        },
    )
    monkeypatch.setattr(
        grammar_fix.ffp_provider_runtime,
        "list_models",
        lambda provider, filter_kind, model, no_window, base_url: {
            "models": ["llama3.1:latest"],
            "active": "llama3.1:latest",
            "provider": provider,
        },
    )

    snap = grammar_fix.build_config_snapshot()

    assert snap["llm"]["provider"] == "ollama"
    assert snap["llm"]["configured_provider"] == "fastflowlm"
    assert snap["llm"]["base_url"] == "http://127.0.0.1:11434"
    assert snap["llm"]["model"] == "llama3.1:latest"
    assert snap["provider_status"]["active"] == "ollama"
    assert snap["provider_status"]["configured"] == "fastflowlm"
    assert snap["flm_model"] == "llama3.1:latest"


def test_start_llm_server_launches_ollama_when_selected_provider_is_ollama(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.LLM_PROVIDER = "ollama"
    calls = []
    states = iter([False, True])

    class _Proc:
        pid = 1234

    monkeypatch.setattr(grammar_fix.subprocess, "Popen", lambda argv, **kwargs: calls.append((argv, kwargs)) or _Proc())
    monkeypatch.setattr(grammar_fix, "is_llm_server_reachable", lambda: next(states))
    monkeypatch.setattr(grammar_fix.time, "sleep", lambda _secs: None)

    result = grammar_fix.start_llm_server(force_restart=False)

    assert result == "started"
    assert calls
    assert calls[0][0] == ["ollama", "serve"]


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


def test_call_flm_prompt_rejects_prompt_colon_echo(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(grammar_fix, "is_flm_server_reachable", lambda: True)
    echo = "Prompt: Develop an app for Java that plays a game of ducks."

    def fake_call(model, system_prompt, user_content, max_tokens, timeout_seconds):
        return (echo, grammar_fix.FLM_MODEL)

    monkeypatch.setattr(grammar_fix, "_call_flm_api", fake_call)

    text, _, _, _ = grammar_fix.call_flm("prompt", "Develop a app for java that play a game of ducks")

    assert text.startswith("<task>")
    assert not text.lower().startswith("prompt:")


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


def test_call_flm_prompt_rescues_multiline_echo_with_marker_words(fresh_modules, monkeypatch):
    # Regression: a multi-line request whose OWN words include prompt markers
    # ("output", "fix") used to defeat echo protection. looks_like_prompt_text()
    # matched the echoed input, and is_weak_prompt_echo() ignored multi-line
    # text (len(lines) <= 2 gate), so the user got their request back merely
    # reformatted with extra spacing instead of a converted Claude prompt.
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(grammar_fix, "is_flm_server_reachable", lambda: True)
    user_text = (
        "We have a request to update the troubleshooting section.\n"
        "Analyze all other cycle repo output files in this folder.\n"
        "Read only HTML, CSV, and log files.\n"
        "Find the best examples where the fix is clearly seen.\n"
    )

    def fake_call(model, system_prompt, user_content, max_tokens, timeout_seconds):
        # Model echoes the request back with extra spacing no matter which
        # (system / anti-echo / rescue) prompt it is handed.
        echo = "\n\n".join(line for line in user_content.splitlines() if line.strip())
        return (echo, grammar_fix.FLM_MODEL)

    monkeypatch.setattr(grammar_fix, "_call_flm_api", fake_call)

    text, _, _, _ = grammar_fix.call_flm("prompt", user_text)

    # Deterministic fallback must guarantee a real structured prompt.
    assert grammar_fix.ffp_llm_client.has_prompt_structure(text)
    assert "<task>" in text


def test_call_flm_prompt_generic_chat_falls_back_to_markdown(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.CONFIG["prompt_builder"] = {
        "target_agent": "generic_chat",
        "detail_level": "balanced",
        "action_mode": "implement",
        "structure": "agent_default",
        "include_acceptance_criteria": True,
        "include_verification": True,
        "include_output_format": True,
        "preserve_user_constraints": True,
        "allow_user_suffix": True,
        "user_suffix": "",
    }
    grammar_fix.PROMPT_BUILDER_CFG = grammar_fix.CONFIG["prompt_builder"]
    monkeypatch.setattr(grammar_fix, "is_flm_server_reachable", lambda: True)
    prompts = []

    def fake_call(model, system_prompt, user_content, max_tokens, timeout_seconds):
        prompts.append(system_prompt)
        return (user_content, grammar_fix.FLM_MODEL)

    monkeypatch.setattr(grammar_fix, "_call_flm_api", fake_call)

    text, _, _, _ = grammar_fix.call_flm("prompt", "fix the failing config test")

    assert "generic coding/chat assistant" in prompts[0]
    assert text.startswith("## Task")
    settings = grammar_fix.ffp_prompt_builder.PromptBuilderSettings.from_config(
        grammar_fix.PROMPT_BUILDER_CFG
    )
    assert grammar_fix.ffp_prompt_builder.validate(text, settings).valid is True


def test_is_weak_prompt_echo_flags_multiline_reformat(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    llm = grammar_fix.ffp_llm_client
    src = (
        "Update the troubleshooting section.\n"
        "Analyze the output files.\n"
        "Find the best fix examples.\n"
    )
    reformatted = src.replace("\n", "\n\n")
    # No XML structure + heavy reuse → weak echo, even across many lines.
    assert llm.is_weak_prompt_echo(src, reformatted) is True
    # A real converted prompt carries XML scaffold → never weak.
    structured = "<task>Document the fix for the output failures</task>\n<constraints>One page</constraints>"
    assert llm.is_weak_prompt_echo(src, structured) is False
    assert llm.has_prompt_structure(structured) is True


def test_apply_config_patch_updates_flm_model_and_runtime(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(
        grammar_fix,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b", "other:1b"], "active": "qwen3.5:4b"},
    )
    monkeypatch.setattr(grammar_fix, "_warmup_request", lambda model: None)

    result = grammar_fix.apply_config_patch({"flm_model": "other:1b"})

    assert result == "model=other:1b"
    assert grammar_fix.FLM_MODEL == "other:1b"
    saved = json.loads(grammar_fix.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["flm_model"] == "other:1b"


def test_apply_config_patch_rejects_uninstalled_model(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(
        grammar_fix,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b"], "active": "qwen3.5:4b"},
    )

    with pytest.raises(RuntimeError, match="not installed"):
        grammar_fix.apply_config_patch({"flm_model": "missing:7b"})


def test_apply_config_patch_syncs_chat_llm_model(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    cfg = grammar_fix.load_config()
    cfg["chat"] = {"llm_model": "stale:old"}
    grammar_fix.save_config(cfg)
    monkeypatch.setattr(
        grammar_fix,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b", "other:1b"], "active": "qwen3.5:4b"},
    )
    monkeypatch.setattr(grammar_fix, "_warmup_request", lambda model: None)

    grammar_fix.apply_config_patch({"flm_model": "other:1b"})

    saved = json.loads(grammar_fix.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["flm_model"] == "other:1b"
    assert saved["llm"]["model"] == "other:1b"
    assert "llm_model" not in (saved.get("chat") or {})


def test_apply_config_patch_accepts_llm_model_and_mirrors_legacy_keys(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(
        grammar_fix,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b", "other:1b"], "active": "qwen3.5:4b"},
    )
    monkeypatch.setattr(grammar_fix, "_warmup_request", lambda model: None)

    result = grammar_fix.apply_config_patch({"llm": {"model": "other:1b", "timeout_seconds": 90}})

    assert result == "model=other:1b"
    assert grammar_fix.FLM_MODEL == "other:1b"
    assert grammar_fix.LLM_MODEL == "other:1b"
    assert grammar_fix.FLM_TIMEOUT_SECONDS == 90
    saved = json.loads(grammar_fix.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["llm"]["model"] == "other:1b"
    assert saved["flm_model"] == "other:1b"
    assert saved["flm_timeout_seconds"] == 90


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


def test_apply_config_patch_provider_switch_rebuilds_llm_payload(fresh_modules):
    # Regression: switching llm.provider must NOT carry the previous provider's
    # base_url/auth_bearer into the new provider (Ollama inheriting FLM's port
    # silently routed "ollama" traffic to the FLM server, which also answers
    # /api/tags). The llm payload must rebuild from the new provider's profile.
    grammar_fix = fresh_modules("grammar_fix")

    result = grammar_fix.apply_config_patch(
        {
            "llm": {"provider": "ollama", "timeout_seconds": 120},
            "providers": {"ollama": {"base_url": "http://127.0.0.1:11434", "timeout_seconds": 120}},
        }
    )

    assert result == "ok"
    saved = json.loads(grammar_fix.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["llm"]["provider"] == "ollama"
    assert saved["llm"]["base_url"] == "http://127.0.0.1:11434"
    assert saved["llm"]["auth_bearer"] == "ollama"
    assert saved["llm"]["model"] == saved["providers"]["ollama"]["model"]
    # Switching back restores the FastFlowLM payload just as cleanly.
    grammar_fix.apply_config_patch({"llm": {"provider": "fastflowlm"}})
    saved = json.loads(grammar_fix.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["llm"]["base_url"] == "http://127.0.0.1:52625"
    assert saved["llm"]["auth_bearer"] == "flm"
