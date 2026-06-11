from __future__ import annotations

import ffp_pull


def test_start_pull_passes_provider_to_runner():
    calls = []

    def runner(provider, model, no_window, on_line):
        calls.append((provider, model, no_window))
        on_line("100%")
        return 0

    out = ffp_pull.start_pull("llama3.2:3b", 0, provider="ollama", runner=runner)
    assert out == {"ok": True, "state": "running", "model": "llama3.2:3b", "provider": "ollama"}

    assert ffp_pull._thread is not None
    ffp_pull._thread.join(timeout=2)
    assert calls == [("ollama", "llama3.2:3b", 0)]
    assert ffp_pull.status()["state"] == "done"
