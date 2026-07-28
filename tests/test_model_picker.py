"""Model-picker regressions (2.4.1): MoE sizing, never-hide, and active-model health.

Three real defects motivated these, all reproduced from a live FLM 0.9.45 catalog:

1. ``qwen3.6-moe:35b-a3b`` was sized as a 35B model (it is 35B total but ~3B
   ACTIVE) and therefore tagged unfit on a 32 GB machine.
2. The dashboard then *silently dropped* every ``fits == "no"`` candidate, so the
   model was invisible in the picker and had to be pulled by hand.
3. An FLM upgrade can invalidate an already-pulled model (0.9.45 rejects weights
   stamped for 0.9.43) and FLM reports it as NOT installed. Nothing surfaced
   that, so the next hotkey failed with an opaque provider error.
"""
from __future__ import annotations

import json
import types

import ffp_flm_server
import ffp_hardware
import pytest

# A 32 GB Ryzen AI laptop: Windows reports ~23.6 GB after an 8 GB iGPU carve-out
# of the same DIMMs, so the carve-out is counted back in.
HW_32GB = {"ram_gb": 23.6, "vram_gb": 8.0}

# Real footprints from `flm list --json` on FLM 0.9.45.
FLM_REAL = [
    {"name": "llama3.2:3b", "footprint_gb": 2.7, "installed": True},
    {"name": "qwen3.5:4b", "footprint_gb": 5.2, "installed": False, "flm_min_version": "0.9.45"},
    {"name": "gemma4-it:e4b", "footprint_gb": 9.1, "installed": True},
    {"name": "gpt-oss:20b", "footprint_gb": 14.0, "installed": False},
    {"name": "qwen3.6-moe:35b-a3b", "footprint_gb": 24.3, "installed": True, "flm_min_version": "0.9.45"},
]


# ---------- MoE parameter parsing ------------------------------------------------------

@pytest.mark.parametrize(("name", "total", "active"), [
    ("qwen3.6-moe:35b-a3b", 35.0, 3.0),   # the model that went missing
    ("qwen3-moe:30b-a3b", 30.0, 3.0),
    ("qwen3.5:4b", 4.0, None),            # ordinary dense tag
    ("gemma4-it:e4b", 4.0, None),
    ("llama3.2:1b", 1.0, None),           # 'llama3' must not read as a<N>b
    ("gemma3:4b", 4.0, None),
    ("gpt-oss:20b", 20.0, None),
    ("embed-gemma:300m", 0.3, None),
])
def test_parse_total_and_active_params(name, total, active):
    assert ffp_hardware.parse_params_b(name) == total
    assert ffp_hardware.parse_active_params_b(name) == active


def test_effective_params_prefers_active_for_moe():
    # The whole point: a 35B MoE performs like a ~3B model, so the budget check
    # must see 3, not 35.
    assert ffp_hardware.effective_params_b("qwen3.6-moe:35b-a3b") == 3.0
    assert ffp_hardware.effective_params_b("qwen3.5:4b") == 4.0
    assert ffp_hardware.effective_params_b("translategemma") is None


def test_active_params_ignored_when_not_smaller_than_total():
    # Guard against misreading a tag whose 'a<N>b' is not an MoE active count.
    assert ffp_hardware.parse_active_params_b("weird:3b-a9b") is None


# ---------- footprint-driven fit -------------------------------------------------------

def test_usable_memory_subtracts_os_headroom():
    assert ffp_hardware.usable_memory_gb(HW_32GB) == 25.6
    assert ffp_hardware.usable_memory_gb({"ram_gb": 8, "vram_gb": 0}) == 2.0  # floored


def test_footprint_beats_name_parsing():
    out = ffp_hardware.recommend_models("fastflowlm", FLM_REAL, HW_32GB)
    fits = {m["name"]: m["fits"] for m in out["models"]}
    # gemma4-it:e4b reads as "4B" by name but is really 8B / 9.1 GB — and still
    # fits. gpt-oss:20b would have been "no" under the old 4.5B param budget.
    assert fits["gemma4-it:e4b"] == "yes"
    assert fits["gpt-oss:20b"] == "yes"
    # The MoE is genuinely near the limit: honest "tight", not hidden.
    assert fits["qwen3.6-moe:35b-a3b"] == "tight"
    assert out["usable_memory_gb"] == 25.6


def test_footprint_over_usable_is_no_not_dropped():
    out = ffp_hardware.recommend_models(
        "fastflowlm", [{"name": "huge:400b", "footprint_gb": 240.0}], HW_32GB
    )
    assert len(out["models"]) == 1
    assert out["models"][0]["fits"] == "no"
    assert "exceeds" in out["models"][0]["fit_reason"]


def test_no_candidate_is_ever_dropped():
    # The invariant behind the bug: recommend_models returns EVERY candidate so
    # the UI cannot silently hide one.
    out = ffp_hardware.recommend_models("fastflowlm", FLM_REAL, HW_32GB)
    assert [m["name"] for m in out["models"]] == [c["name"] for c in FLM_REAL]
    assert all(m["fit_reason"] for m in out["models"])


def test_metadata_is_forwarded_for_the_ui():
    out = ffp_hardware.recommend_models("fastflowlm", FLM_REAL, HW_32GB)
    moe = next(m for m in out["models"] if m["name"] == "qwen3.6-moe:35b-a3b")
    assert moe["installed"] is True
    assert moe["flm_min_version"] == "0.9.45"
    assert moe["active_params_b"] == 3.0
    assert moe["footprint_gb"] == 24.3


def test_legacy_tuple_candidates_still_supported():
    # Back-compat: the Ollama catalog is (name, params_b) tuples with no footprint.
    out = ffp_hardware.recommend_models(
        "fastflowlm", [("qwen3.5:4b", 4.0), ("mystery", None)], HW_32GB
    )
    fits = {m["name"]: m["fits"] for m in out["models"]}
    assert fits["qwen3.5:4b"] == "yes"
    assert fits["mystery"] == "unknown"


def test_moe_fits_by_active_params_when_no_footprint():
    # Without a footprint the MoE must still be judged on its 3B active count.
    out = ffp_hardware.recommend_models(
        "fastflowlm", [("qwen3.6-moe:35b-a3b", 35.0)], HW_32GB
    )
    assert out["models"][0]["fits"] == "yes"
    assert "active" in out["models"][0]["fit_reason"]


# ---------- flm_list carries the catalog metadata --------------------------------------

SAMPLE = json.dumps({
    "models": [
        {"model": "qwen3.5:4b", "installed": False, "footprint": 5.2,
         "flm_min_version": "0.9.45", "default_context_length": 32768,
         "details": {"parameter_size": "4B"}},
        {"model": "llama3.2:3b", "installed": True, "footprint": 2.7,
         "flm_min_version": "0.9.21", "details": {"parameter_size": "3B"}},
    ]
})


def _fake_run(stdout):
    def _run(argv, **kwargs):
        return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    return _run


def test_flm_list_exposes_footprint_and_min_version(monkeypatch):
    monkeypatch.setattr(ffp_flm_server, "run_hidden", _fake_run(SAMPLE))
    out = ffp_flm_server.flm_list("all", "qwen3.5:4b", 0)
    by_name = {d["name"]: d for d in out["details"]}
    assert by_name["qwen3.5:4b"]["footprint_gb"] == 5.2
    assert by_name["qwen3.5:4b"]["flm_min_version"] == "0.9.45"
    assert by_name["qwen3.5:4b"]["installed"] is False
    assert by_name["qwen3.5:4b"]["parameter_size"] == "4B"
    assert by_name["qwen3.5:4b"]["context_length"] == 32768
    # Absent in the payload -> reported as None rather than invented.
    assert by_name["llama3.2:3b"]["context_length"] is None


def test_flm_list_details_are_unfiltered(monkeypatch):
    # details must cover the whole catalog even when models[] is filtered, so a
    # caller can tell "absent from catalog" from "present but not installed".
    monkeypatch.setattr(ffp_flm_server, "run_hidden", _fake_run(SAMPLE))
    out = ffp_flm_server.flm_list("installed", "x", 0)
    assert out["models"] == ["llama3.2:3b"]
    assert {d["name"] for d in out["details"]} == {"qwen3.5:4b", "llama3.2:3b"}


def test_flm_list_tolerates_missing_footprint(monkeypatch):
    payload = json.dumps({"models": [{"model": "x:1b", "installed": True}]})
    monkeypatch.setattr(ffp_flm_server, "run_hidden", _fake_run(payload))
    out = ffp_flm_server.flm_list("all", "x:1b", 0)
    assert out["details"][0]["footprint_gb"] is None
