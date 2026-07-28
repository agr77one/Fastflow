"""Benchmark memory guard + keep-warm/benchmark mutual exclusion (2.4.2).

Reproduced from a real failure on 2026-07-27: `flm bench qwen3.6-moe:35b-a3b`
died in 4 seconds with

    Failed to submit command to hw queue (0xc01e0200): ... the video memory
    manager could not page-in all of the required allocations ...

Two causes, both fixed here:

1. Nothing checked whether the model could fit before launching. The MoE needs
   ~24.3 GB of weights plus KV cache for the 1k-32k sweep, against ~25.6 GB
   usable on a 32 GB machine — unwinnable, but the user only saw a driver code.
2. The keep-warm thread reloads the active model on its own schedule with no
   knowledge of benchmarks. A bench takes 10-20 min and the default keepalive is
   15 min, so a warm tick lands mid-run and competes for the same memory.
"""
from __future__ import annotations

import ffp_benchmark
import ffp_hardware
import pytest

HW_32GB = {"ram_gb": 23.6, "vram_gb": 8.0}   # usable == 25.6 GB


# ---------- benchmark_fit ---------------------------------------------------------------

def test_rejects_the_model_that_actually_failed():
    fit = ffp_hardware.benchmark_fit("qwen3.6-moe:35b-a3b", 24.3, HW_32GB, available_gb=15.4)
    assert fit["ok"] is False
    assert fit["needed_gb"] == 28.3          # 24.3 weights + 4.0 context headroom
    assert fit["usable_gb"] == 25.6
    # The message must explain, not just refuse.
    for fragment in ("too large to benchmark", "24.3", "25.6", "15.4 GB free"):
        assert fragment in fit["error"]


@pytest.mark.parametrize(("model", "footprint"), [
    ("gpt-oss:20b", 14.0),
    ("gemma4-it:e4b", 9.1),
    ("qwen3.5:4b", 5.2),
    ("llama3.2:3b", 2.7),
])
def test_allows_models_that_fit(model, footprint):
    fit = ffp_hardware.benchmark_fit(model, footprint, HW_32GB)
    assert fit["ok"] is True
    assert fit["error"] == ""


def test_boundary_exactly_at_usable_is_allowed():
    # needed == usable must pass; only strictly-over is refused.
    fit = ffp_hardware.benchmark_fit("edge:1b", 21.6, HW_32GB)      # 21.6 + 4.0 == 25.6
    assert fit["ok"] is True
    over = ffp_hardware.benchmark_fit("edge:1b", 21.7, HW_32GB)     # 25.7 > 25.6
    assert over["ok"] is False


@pytest.mark.parametrize("footprint", [None, 0, 0.0, "", "abc", -3])
def test_unknown_or_bogus_footprint_never_blocks(footprint):
    # Refusing a runnable benchmark is worse than letting the driver decide.
    fit = ffp_hardware.benchmark_fit("mystery:9b", footprint, HW_32GB)
    assert fit["ok"] is True


def test_bigger_machine_allows_the_moe():
    fit = ffp_hardware.benchmark_fit("qwen3.6-moe:35b-a3b", 24.3, {"ram_gb": 64, "vram_gb": 0})
    assert fit["ok"] is True


def test_available_memory_reports_something_sane():
    free = ffp_hardware.available_memory_gb()
    assert free >= 0
    assert free <= ffp_hardware.system_memory_gb() + 1  # never more than installed


# ---------- benchmark run-state signal ---------------------------------------------------

def test_is_running_tracks_job_state(monkeypatch):
    monkeypatch.setitem(ffp_benchmark._job, "state", "idle")
    assert ffp_benchmark.is_running() is False
    monkeypatch.setitem(ffp_benchmark._job, "state", "running")
    assert ffp_benchmark.is_running() is True
    monkeypatch.setitem(ffp_benchmark._job, "state", "done")
    assert ffp_benchmark.is_running() is False
    monkeypatch.setitem(ffp_benchmark._job, "state", "error")
    assert ffp_benchmark.is_running() is False


# ---------- keep-warm defers to a running benchmark --------------------------------------

def test_warmup_skipped_while_benchmark_runs(fresh_modules):
    daemon = fresh_modules("ffp_daemon")
    called = []
    result = daemon._warm_model_once(
        {"llm": {"provider": "fastflowlm"}},
        "idle_interval",
        warm_fn=lambda: called.append("warmed") or "warmed_up",
        benchmark_running_fn=lambda: True,
    )
    assert result == "skipped_benchmark_running"
    assert called == []            # the model was NOT reloaded mid-benchmark


def test_warmup_proceeds_when_no_benchmark(fresh_modules):
    daemon = fresh_modules("ffp_daemon")
    called = []
    result = daemon._warm_model_once(
        {"llm": {"provider": "fastflowlm"}},
        "idle_interval",
        warm_fn=lambda: called.append("warmed") or "warmed_up",
        benchmark_running_fn=lambda: False,
    )
    assert result == "warmed_up"
    assert called == ["warmed"]


def test_benchmark_in_progress_is_false_when_lookup_fails(fresh_modules, monkeypatch):
    # A broken/absent benchmark module must not stop keep-warm from working.
    daemon = fresh_modules("ffp_daemon")
    import ffp_benchmark as b
    monkeypatch.setattr(b, "is_running", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert daemon._benchmark_in_progress() is False


# ---------- daemon precheck wiring -------------------------------------------------------

def test_precheck_refuses_using_catalog_footprint(fresh_modules):
    daemon = fresh_modules("ffp_daemon")
    listing = {"details": [{"name": "qwen3.6-moe:35b-a3b", "footprint_gb": 24.3, "installed": True}]}
    out = daemon._bench_memory_precheck("qwen3.6-moe:35b-a3b", listing)
    # Only assert the verdict shape; the exact threshold is covered above and
    # depends on the CI machine's real memory.
    assert set(out) >= {"ok", "error"}
    if not out["ok"]:
        assert "too large to benchmark" in out["error"]


def test_precheck_allows_model_missing_from_catalog(fresh_modules):
    daemon = fresh_modules("ffp_daemon")
    out = daemon._bench_memory_precheck("ghost:1b", {"details": [{"name": "other:1b", "footprint_gb": 2.0}]})
    assert out["ok"] is True


def test_precheck_allows_when_listing_raises(fresh_modules, monkeypatch):
    daemon = fresh_modules("ffp_daemon")
    import grammar_fix

    def boom(kind):
        raise RuntimeError("flm missing")

    monkeypatch.setattr(grammar_fix, "_provider_list", boom)
    out = daemon._bench_memory_precheck("anything:4b")
    assert out["ok"] is True       # never block on a lookup failure


def test_bench_start_refuses_oversized_model_without_launching(fresh_modules, monkeypatch):
    daemon = fresh_modules("ffp_daemon")
    import grammar_fix
    launched = []
    monkeypatch.setattr(grammar_fix, "LLM_PROVIDER", "fastflowlm", raising=False)
    monkeypatch.setattr(
        daemon, "_bench_memory_precheck",
        lambda model, listing=None: {"ok": False, "error": "'x' is too large to benchmark on this machine: ..."},
    )
    import ffp_benchmark as b
    monkeypatch.setattr(b, "start_benchmark", lambda *a, **k: launched.append(a) or {"ok": True})
    out = daemon._act_bench_start({"model": "qwen3.6-moe:35b-a3b"})
    assert out["ok"] is False
    assert "too large to benchmark" in out["error"]
    assert launched == []          # no thread, no flm subprocess, no driver error


def test_bench_start_proceeds_when_precheck_passes(fresh_modules, monkeypatch):
    daemon = fresh_modules("ffp_daemon")
    import grammar_fix
    launched = []
    monkeypatch.setattr(grammar_fix, "LLM_PROVIDER", "fastflowlm", raising=False)
    monkeypatch.setattr(daemon, "_bench_memory_precheck", lambda model, listing=None: {"ok": True, "error": ""})
    import ffp_benchmark as b
    import ffp_flm_server
    monkeypatch.setattr(ffp_flm_server, "flm_version", lambda nw: "0.9.45")
    monkeypatch.setattr(b, "start_benchmark", lambda *a, **k: launched.append(a[0]) or {"ok": True, "state": "running"})
    out = daemon._act_bench_start({"model": "llama3.2:3b"})
    assert out["ok"] is True
    assert launched == ["llama3.2:3b"]


# ---------- provider errors returned as HTTP 200 -----------------------------------------

def test_openai_call_surfaces_provider_error_body(fresh_modules, monkeypatch):
    # FastFlowLM answers 200 with {"error": "Failed to load <model> model!"} when
    # the weights don't fit. That message must reach the user instead of the
    # generic "Local LLM returned no usable text" that discards the real cause.
    import io
    import json as _json
    grammar_fix = fresh_modules("grammar_fix")

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    payload = _json.dumps({"error": "Failed to load qwen3.6-moe:35b-a3b model!"}).encode()
    monkeypatch.setattr(grammar_fix.urllib.request, "urlopen", lambda *a, **k: FakeResp(payload))
    with pytest.raises(RuntimeError, match="Failed to load qwen3.6-moe:35b-a3b model!"):
        grammar_fix._call_openai_compatible("http://x", "flm", "m", "sys", "user", 32, 5)


def test_openai_call_handles_nested_error_object(fresh_modules, monkeypatch):
    import io
    import json as _json
    grammar_fix = fresh_modules("grammar_fix")

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    payload = _json.dumps({"error": {"message": "model not found", "type": "invalid"}}).encode()
    monkeypatch.setattr(grammar_fix.urllib.request, "urlopen", lambda *a, **k: FakeResp(payload))
    with pytest.raises(RuntimeError, match="model not found"):
        grammar_fix._call_openai_compatible("http://x", "flm", "m", "sys", "user", 32, 5)


def test_openai_call_ignores_error_field_when_choices_present(fresh_modules, monkeypatch):
    # A warning alongside a real completion must not abort the call.
    import io
    import json as _json
    grammar_fix = fresh_modules("grammar_fix")

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    payload = _json.dumps({
        "error": "deprecated parameter",
        "choices": [{"message": {"content": "real answer"}}],
        "model": "m",
    }).encode()
    monkeypatch.setattr(grammar_fix.urllib.request, "urlopen", lambda *a, **k: FakeResp(payload))
    text, model, _usage = grammar_fix._call_openai_compatible("http://x", "flm", "m", "sys", "user", 32, 5)
    assert text == "real answer"


def test_chat_surfaces_provider_error_body(monkeypatch):
    import ffp_chat
    monkeypatch.setattr(ffp_chat, "_default_llm_call", ffp_chat._default_llm_call)
    payload = {"error": "Failed to load big:70b model!"}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            import json as _json
            return _json.dumps(payload).encode()

    monkeypatch.setattr(ffp_chat.urllib.request, "urlopen", lambda *a, **k: FakeResp())
    with pytest.raises(RuntimeError, match="Failed to load big:70b model!"):
        ffp_chat._default_llm_call([{"role": "user", "content": "hi"}])


def test_ollama_bench_skips_the_flm_memory_precheck(fresh_modules, monkeypatch):
    # The guard is about FLM's NPU/weight paging; Ollama benches talk to a
    # running server and must not be gated by it.
    daemon = fresh_modules("ffp_daemon")
    import grammar_fix
    called = []
    monkeypatch.setattr(grammar_fix, "LLM_PROVIDER", "ollama", raising=False)
    monkeypatch.setattr(grammar_fix, "LLM_BASE_URL", "http://127.0.0.1:11434", raising=False)
    monkeypatch.setattr(daemon, "_bench_memory_precheck", lambda *a, **k: called.append(1) or {"ok": False, "error": "no"})
    import ffp_benchmark as b
    monkeypatch.setattr(b, "start_benchmark", lambda *a, **k: {"ok": True, "state": "running"})
    out = daemon._act_bench_start({"model": "llama3.2:3b"})
    assert out["ok"] is True
    assert called == []
