from __future__ import annotations

from tools import prompt_speed_quality_eval as prompt_eval

GOOD_OUTPUT = """<task>
Build the requested CSV validator.
</task>
<context>
The input is a folder of CSV files and a schema.
</context>
<constraints>
- Read every CSV in the folder.
- Validate each row against the schema.
- Report the source file and line for every error.
</constraints>
<output_format>
Return the completed script and focused tests.
</output_format>"""


def _judge(cases):
    return {
        "method": "manual",
        "judgments": {
            f"{style}:{case['name']}": {
                "r3": True,
                "r4": True,
                "invented_requirement": False,
                "notes": "",
            }
            for style in ("v1", "v2")
            for case in cases
        },
    }


def test_fixed_set_covers_required_spread():
    cases = prompt_eval.FIXED_CASES
    categories = {case["category"] for case in cases}

    assert len(cases) >= 12
    assert {"implement", "debug", "review", "refactor", "data", "vague", "long", "trap"} <= categories
    assert sum(bool(case.get("trap")) for case in cases) >= 1
    assert len({case["name"] for case in cases}) == len(cases)


def test_machine_rubric_and_manual_judgment_combine_to_seven():
    result = prompt_eval.score_output(
        GOOD_OUTPUT,
        completion_tokens=92,
        judgment={"r3": True, "r4": True, "invented_requirement": False},
    )

    assert result["rubric"] == {key: True for key in ("r1", "r2", "r3", "r4", "r5", "r6", "r7")}
    assert result["score"] == 7
    assert result["passed"] is True
    assert result["pending_judge"] is False
    assert result["constraint_item_count"] == 3


def test_quality_stays_pending_without_semantic_judge_and_invention_hard_fails():
    pending = prompt_eval.score_output(GOOD_OUTPUT, completion_tokens=92)
    invented = prompt_eval.score_output(
        GOOD_OUTPUT,
        completion_tokens=92,
        judgment={"r3": True, "r4": True, "invented_requirement": True},
    )

    assert pending["score"] is None
    assert pending["pending_judge"] is True
    assert invented["score"] == 7
    assert invented["hard_fail"] is True
    assert invented["passed"] is False


def test_exact_section_and_constraint_count_checks_catch_merged_output():
    merged = GOOD_OUTPUT.replace("</context>\n<constraints>", "")
    too_few = GOOD_OUTPUT.replace(
        "- Validate each row against the schema.\n- Report the source file and line for every error.\n",
        "",
    )

    assert prompt_eval.score_output(merged)["rubric"]["r1"] is False
    result = prompt_eval.score_output(
        too_few,
        judgment={"r3": True, "r4": True, "invented_requirement": False},
    )
    assert result["constraint_item_count"] == 1
    assert result["rubric"]["r3"] is False


def test_usage_metrics_include_units_percentiles_and_per_token_cost():
    result = {
        "usage": {
            "completion_tokens": 100,
            "prefill_duration_ttft": "1600ms",
            "decoding_duration": 8.0,
        },
        "_wall_seconds": 10.0,
    }
    metrics = prompt_eval._sample_metrics(result, measured_wall=99.0)
    distribution = prompt_eval._distribution([1, 2, 3, 4, 5])

    assert metrics["wall_seconds"] == 10.0
    assert metrics["ttft_seconds"] == 1.6
    assert metrics["decode_tokens_per_second"] == 12.5
    assert metrics["seconds_per_output_token"] == 0.1
    assert distribution == {"median": 3.0, "p90": 4.6, "min": 1.0, "max": 5.0}


def test_full_protocol_uses_warmup_plus_five_runs_and_passes_both_gates():
    cases = list(prompt_eval.FIXED_CASES[:2])
    calls = []

    def fake_model(**kwargs):
        calls.append((kwargs["style"], kwargs["user_content"]))
        is_v2 = kwargs["style"] == "v2"
        return {
            "output": GOOD_OUTPUT,
            "model": kwargs["model"],
            "_wall_seconds": 12.0 if is_v2 else 30.0,
            "usage": {
                "prefill_duration_ttft": 1.5,
                "completion_tokens": 100 if is_v2 else 200,
                "decode_duration": 8.0 if is_v2 else 20.0,
            },
        }

    artifact = prompt_eval.run_evaluation(
        fake_model,
        cases=cases,
        runs=5,
        warmups=1,
        judge_data=_judge(cases),
    )

    assert len(calls) == len(cases) * 2 * 6
    assert artifact["summaries"]["v1"]["protocol"]["successful_timed_runs"] == 10
    assert artifact["summaries"]["v2"]["speed"]["wall_seconds"]["median"] == 12.0
    assert artifact["gate"]["speed"]["p50_ratio"] == 0.4
    assert artifact["gate"]["speed"]["passed"] is True
    assert artifact["gate"]["quality"]["passed"] is True
    assert artifact["gate"]["passed"] is True
    assert len(artifact["manual_side_by_side"]) == 2
    v2_sample = artifact["cases"][0]["styles"]["v2"]["samples"][0]
    assert v2_sample["raw_output"] == GOOD_OUTPUT
    assert v2_sample["output"] != v2_sample["raw_output"]
    assert v2_sample["final_output_tokens"] > 0


def test_protocol_rejects_too_few_timed_runs():
    try:
        prompt_eval.run_evaluation(lambda **_kwargs: {}, cases=list(prompt_eval.FIXED_CASES[:1]), runs=4)
    except ValueError as exc:
        assert str(exc) == "runs must be >= 5"
    else:
        raise AssertionError("runs below the invariant must be rejected")


def test_cold_warm_probe_records_first_call_penalty_and_speedup():
    restarted = []
    calls = []

    def fake_model(**kwargs):
        calls.append(kwargs)
        cold = len(calls) == 1
        return {
            "output": GOOD_OUTPUT,
            "model": kwargs["model"],
            "_wall_seconds": 30.0 if cold else 12.0,
            "usage": {
                "prefill_duration_ttft": 18.0 if cold else 1.5,
                "completion_tokens": 100,
                "decode_duration": 8.0,
            },
        }

    result = prompt_eval.run_cold_warm_probe(
        fake_model,
        lambda: restarted.append(True) or "started",
    )

    assert restarted == [True]
    assert len(calls) == 2
    assert result["ok"] is True
    assert result["cold"]["wall_seconds"] == 30.0
    assert result["warm"]["wall_seconds"] == 12.0
    assert result["wall_speedup"] == 2.5
    assert result["ttft_reduction_seconds"] == 16.5


def test_existing_artifact_can_be_judged_without_rerunning_model_calls():
    cases = list(prompt_eval.FIXED_CASES[:1])
    calls = []

    def fake_model(**kwargs):
        calls.append(kwargs["style"])
        return {
            "output": GOOD_OUTPUT,
            "model": kwargs["model"],
            "_wall_seconds": 12.0 if kwargs["style"] == "v2" else 30.0,
            "usage": {"completion_tokens": 100, "decoding_duration": 8.0},
        }

    artifact = prompt_eval.run_evaluation(fake_model, cases=cases, runs=5)
    calls_after_run = len(calls)
    rescored = prompt_eval.rescore_artifact(artifact, _judge(cases))

    assert artifact["gate"]["quality"]["judge_complete"] is False
    assert rescored["gate"]["passed"] is True
    assert rescored["judge"]["method"] == "manual"
    assert len(calls) == calls_after_run


def test_frozen_v1_replay_preserves_five_timed_samples_after_warmup():
    cases = list(prompt_eval.FIXED_CASES[:1])

    def fake_model(**kwargs):
        return {
            "output": GOOD_OUTPUT,
            "model": kwargs["model"],
            "_wall_seconds": 30.0 if kwargs["style"] == "v1" else 12.0,
            "usage": {"completion_tokens": 100, "decoding_duration": 8.0},
        }

    artifact = prompt_eval.run_evaluation(fake_model, cases=cases, runs=5, judge_data=_judge(cases))
    samples = artifact["cases"][0]["styles"]["v1"]["samples"]
    for index, sample in enumerate(samples, start=1):
        sample["wall_seconds"] = float(index)
    replay = prompt_eval.build_v1_replay(artifact)
    kwargs = {
        "style": "v1",
        "user_content": cases[0]["input"],
        "model": "qwen3.5:4b",
    }
    walls = [replay(**kwargs)["_wall_seconds"] for _ in range(6)]

    assert walls == [1.0, 1.0, 2.0, 3.0, 4.0, 5.0]


def test_v1_baseline_export_contains_no_v2_samples():
    cases = list(prompt_eval.FIXED_CASES[:1])

    def fake_model(**kwargs):
        return {
            "output": GOOD_OUTPUT,
            "model": kwargs["model"],
            "_wall_seconds": 12.0 if kwargs["style"] == "v2" else 30.0,
            "usage": {"completion_tokens": 100, "decoding_duration": 8.0},
        }

    artifact = prompt_eval.run_evaluation(fake_model, cases=cases, runs=5, judge_data=_judge(cases))
    baseline = prompt_eval.export_v1_baseline(artifact, "source.json")

    assert baseline["kind"] == "prompt_v1_frozen_baseline"
    assert baseline["source_artifact"] == "source.json"
    assert baseline["summary"] == artifact["summaries"]["v1"]
    assert set(baseline["cases"][0]) == {"name", "category", "trap", "input", "v1"}
    assert "v2" not in baseline["cases"][0]
