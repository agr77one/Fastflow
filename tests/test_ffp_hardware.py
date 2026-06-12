"""Hardware detection heuristics: name sizing, per-provider budgets, fit tags."""
from __future__ import annotations

import ffp_hardware


def test_parse_params_b_handles_common_tag_shapes():
    assert ffp_hardware.parse_params_b("qwen3.5:4b") == 4.0
    assert ffp_hardware.parse_params_b("llama3.2:1b") == 1.0
    assert ffp_hardware.parse_params_b("qwen2.5:0.5b") == 0.5
    assert ffp_hardware.parse_params_b("gpt-oss:20b") == 20.0
    assert ffp_hardware.parse_params_b("gemma4-it:e4b") == 4.0
    assert ffp_hardware.parse_params_b("phi4-mini-it:4b") == 4.0
    assert ffp_hardware.parse_params_b("embed-gemma:300m") == 0.3
    assert ffp_hardware.parse_params_b("translategemma") is None
    assert ffp_hardware.parse_params_b("") is None


def test_model_budget_fastflowlm_scales_with_ram():
    # Anchor from real use: 32 GB RAM runs 4B-class models on the NPU.
    assert ffp_hardware.model_budget("fastflowlm", {"ram_gb": 32, "vram_gb": 0})["max_params_b"] == 4.5
    assert ffp_hardware.model_budget("fastflowlm", {"ram_gb": 64, "vram_gb": 0})["max_params_b"] == 9.0
    assert ffp_hardware.model_budget("fastflowlm", {"ram_gb": 96, "vram_gb": 0})["max_params_b"] == 14.0
    assert ffp_hardware.model_budget("fastflowlm", {"ram_gb": 16, "vram_gb": 0})["max_params_b"] == 2.0
    assert ffp_hardware.model_budget("fastflowlm", {"ram_gb": 8, "vram_gb": 0})["max_params_b"] == 1.0
    # Ryzen AI laptops carve iGPU "VRAM" out of the same DIMMs — a 32 GB
    # machine reports 23.6 GB RAM + 8 GB VRAM and must still rate 4B-class.
    carved = ffp_hardware.model_budget("fastflowlm", {"ram_gb": 23.6, "vram_gb": 8.0})
    assert carved["max_params_b"] == 4.5
    assert carved["basis"] == "ram"


def test_model_budget_ollama_prefers_vram_then_ram():
    gpu = ffp_hardware.model_budget("ollama", {"ram_gb": 32, "vram_gb": 8})
    assert gpu["basis"] == "vram"
    assert gpu["max_params_b"] == 9.3  # (8 - 1.5) / 0.7

    cpu = ffp_hardware.model_budget("ollama", {"ram_gb": 32, "vram_gb": 0})
    assert cpu["basis"] == "ram"
    assert cpu["max_params_b"] == 8.0  # RAM would allow more, capped for speed

    small = ffp_hardware.model_budget("ollama", {"ram_gb": 8, "vram_gb": 0})
    assert small["max_params_b"] == 2.9

    # iGPUs reporting a sliver of dedicated VRAM (<5 GB) use the CPU path.
    igpu = ffp_hardware.model_budget("ollama", {"ram_gb": 32, "vram_gb": 0.5})
    assert igpu["basis"] == "ram"


def test_recommend_models_tags_fit_per_candidate():
    hw = {"ram_gb": 32, "vram_gb": 0}
    out = ffp_hardware.recommend_models(
        "fastflowlm",
        [("qwen3.5:4b", 4.0), ("llama3.1:8b", 8.0), ("gpt-oss:20b", 20.0), ("mystery", None)],
        hw,
    )
    tags = {m["name"]: m["fits"] for m in out["models"]}
    # 32 GB NPU budget = 4.5B: 4B fits, 8B way over (>1.5x), 20B no, unknown kept.
    assert tags["qwen3.5:4b"] == "yes"
    assert tags["llama3.1:8b"] == "no"
    assert tags["gpt-oss:20b"] == "no"
    assert tags["mystery"] == "unknown"
    assert out["budget"]["max_params_b"] == 4.5
    assert out["hardware"] == hw

    # 64 GB: 8B fits, 20B still out but 9B-class would be tight on ollama/cpu…
    out64 = ffp_hardware.recommend_models("fastflowlm", [("llama3.1:8b", 8.0)], {"ram_gb": 64, "vram_gb": 0})
    assert out64["models"][0]["fits"] == "yes"


def test_detect_hardware_returns_sane_shape():
    hw = ffp_hardware.detect_hardware()
    assert hw["ram_gb"] > 0  # every CI runner has RAM
    assert hw["vram_gb"] >= 0
    assert isinstance(hw["gpu_name"], str)
