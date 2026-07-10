from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "data" / "benchmarks"


def _load(name: str) -> dict:
    return json.loads((BENCH / name).read_text(encoding="utf-8"))


def test_v29_prompt_v2_release_artifact_passes_both_gates():
    artifact = _load("prompt_v2_ab_2026-07-10.json")

    assert artifact["protocol"]["fixed_input_count"] >= 12
    assert artifact["protocol"]["warmups_per_style_input"] >= 1
    assert artifact["protocol"]["timed_runs_per_style_input"] >= 5
    assert artifact["protocol"]["judge_method"] == "manual_gpt5_source_review"
    assert artifact["gate"]["protocol_pass"] is True
    assert artifact["gate"]["speed"]["passed"] is True
    assert artifact["gate"]["quality"]["passed"] is True
    assert artifact["gate"]["passed"] is True
    assert artifact["summaries"]["v2"]["quality"]["invented_requirement_failures"] == 0
    assert artifact["summaries"]["v2"]["quality"]["passed_outputs"] == 12
    assert artifact["cold_warm_probe"]["ok"] is True


def test_release_evidence_has_frozen_baseline_and_complete_judgments():
    artifact = _load("prompt_v2_ab_2026-07-10.json")
    baseline = _load("prompt_v1_frozen_2026-07-10.json")
    judge = _load("prompt_v2_judge_2026-07-10.json")
    cold_warm = _load("prompt_v2_cold_warm_2026-07-10.json")

    assert artifact["protocol"]["reused_v1_from"].endswith("prompt_v1_frozen_2026-07-10.json")
    assert baseline["kind"] == "prompt_v1_frozen_baseline"
    assert baseline["protocol"]["timed_runs_per_style_input"] == 5
    assert len(baseline["cases"]) == 12
    assert len(judge["judgments"]) == 24
    assert cold_warm["ok"] is True
    assert cold_warm["wall_speedup"] > 1
