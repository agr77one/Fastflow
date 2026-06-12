"""Ollama benchmark path: native-metric math, no serve stop/start, history shape."""
from __future__ import annotations

import time

import ffp_benchmark


def _fake_generate_factory(calls: list):
    def fake_generate(base_url, payload, timeout=900):
        calls.append((base_url, payload))
        return {
            "prompt_eval_count": 200,
            "prompt_eval_duration": 2_000_000_000,  # 2 s -> 100 tok/s prefill
            "eval_count": 50,
            "eval_duration": 5_000_000_000,  # 5 s -> 10 tok/s decode
        }

    return fake_generate


def _wait_done(timeout_s: float = 5.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        st = ffp_benchmark.status()
        if st["state"] in ("done", "error"):
            return st
        time.sleep(0.05)
    return ffp_benchmark.status()


def test_run_ollama_bench_computes_metrics_from_native_counters():
    calls: list = []
    rows = ffp_benchmark.run_ollama_bench(
        "m:1b",
        "http://127.0.0.1:11434",
        sizes=(64, 128),
        iterations=2,
        generate=_fake_generate_factory(calls),
    )
    # 1 untimed warmup + 2 sizes x 2 iterations
    assert len(calls) == 5
    assert calls[0][1]["options"]["num_predict"] == 8  # warmup stays tiny
    assert all(c[1]["stream"] is False for c in calls)
    assert len(rows) == 2
    for row in rows:
        assert row["context"] == 200
        assert row["ttft_s"] == 2.0
        assert row["prefill_tps"] == 100.0
        assert row["decode_tps"] == 10.0


def test_run_ollama_bench_handles_missing_counters():
    # A response without timing fields (e.g. older server) must not divide by
    # zero — the row simply carries None metrics.
    rows = ffp_benchmark.run_ollama_bench(
        "m:1b",
        "http://127.0.0.1:11434",
        sizes=(64,),
        iterations=1,
        generate=lambda base, payload, timeout=900: {"response": "ok"},
    )
    assert rows == [
        {
            "context": None,
            "ttft_s": None,
            "prefill_tps": None,
            "decode_tps": None,
            "raw": ["target_tokens=64", "iterations=1", "num_predict=96"],
        }
    ]


def test_start_benchmark_ollama_writes_result_and_skips_serve_control(tmp_path):
    serve_calls: list = []
    out = ffp_benchmark.start_benchmark(
        "m:1b",
        0,
        tmp_path,
        provider="ollama",
        base_url="http://127.0.0.1:11434",
        stop_serve=lambda: serve_calls.append("stop"),
        start_serve=lambda: serve_calls.append("start"),
        generate=_fake_generate_factory([]),
    )
    assert out["ok"] is True
    assert out["provider"] == "ollama"

    st = _wait_done()
    assert st["state"] == "done", st
    # The Ollama bench talks to the running server — it must never stop or
    # (re)start the FastFlowLM serve process.
    assert serve_calls == []

    hist = ffp_benchmark.history(tmp_path)
    assert hist["runs"], hist
    run = hist["runs"][0]
    assert run["provider"] == "ollama"
    assert run["model"] == "m:1b"
    assert run["points"] == 3  # default sizes sweep
    assert run["peak_prefill_tps"] == 100.0
    assert run["peak_decode_tps"] == 10.0
