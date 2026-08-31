from __future__ import annotations

import ffp_pull


def test_start_pull_passes_provider_to_runner():
    calls = []

    def runner(provider, model, no_window, on_line, force=False):
        calls.append((provider, model, no_window, force))
        on_line("100%")
        return 0

    out = ffp_pull.start_pull("llama3.2:3b", 0, provider="ollama", runner=runner)
    assert out == {"ok": True, "state": "running", "model": "llama3.2:3b", "provider": "ollama"}

    assert ffp_pull._thread is not None
    ffp_pull._thread.join(timeout=2)
    assert calls == [("ollama", "llama3.2:3b", 0, False)]
    assert ffp_pull.status()["state"] == "done"


def test_b53_force_is_passed_through_to_the_runner():
    seen = {}

    def runner(provider, model, no_window, on_line, force=False):
        seen["force"] = force
        on_line("100%")
        return 0

    ffp_pull.start_pull("qwen3.5:9b", 0, provider="fastflowlm", force=True, runner=runner)
    ffp_pull._thread.join(timeout=2)

    assert seen["force"] is True
    status = ffp_pull.status()
    assert status["state"] == "done"
    assert status["forced"] is True


def test_b55_forced_pull_failure_does_not_claim_removal():
    """`flm pull --force` never deletes first, so a failure must NOT say it did."""
    def runner(provider, model, no_window, on_line, force=False):
        return 1

    ffp_pull.start_pull("qwen3.5:9b", 0, provider="fastflowlm", force=True, runner=runner)
    ffp_pull._thread.join(timeout=2)

    error = ffp_pull.status()["error"]
    assert "exited with code 1" in error
    assert "no longer installed" not in error


def test_b53_non_forced_failure_does_not_claim_removal():
    def runner(provider, model, no_window, on_line, force=False):
        return 1

    ffp_pull.start_pull("qwen3.5:9b", 0, provider="fastflowlm", runner=runner)
    ffp_pull._thread.join(timeout=2)

    error = ffp_pull.status()["error"]
    assert "exited with code 1" in error
    assert "no longer installed" not in error


def test_b53_ollama_force_does_not_remove_first(monkeypatch):
    """`ollama pull` already re-fetches on digest change — don't delete first."""
    removed = []
    popened = []

    class _Proc:
        stdout = iter(["100%\n"])
        returncode = 0

        def wait(self):
            return 0

    monkeypatch.setattr(
        ffp_pull.subprocess, "run",
        lambda argv, **kw: removed.append(argv) or _Proc(),
    )
    monkeypatch.setattr(
        ffp_pull.subprocess, "Popen",
        lambda argv, **kw: popened.append(argv) or _Proc(),
    )

    rc = ffp_pull._default_runner("ollama", "llama3.2:3b", 0, lambda _l: None, force=True)

    assert rc == 0
    assert removed == []                              # no remove for ollama
    assert popened == [["ollama", "pull", "llama3.2:3b"]]


def test_b55_flm_force_uses_the_force_flag_and_never_removes(monkeypatch):
    """`flm pull --force` re-downloads in place; nothing may be deleted first."""
    calls = []

    class _Proc:
        stdout = iter(["100%\n"])
        returncode = 0

        def wait(self):
            return 0

    monkeypatch.setattr(
        ffp_pull.subprocess, "run",
        lambda argv, **kw: calls.append(("run", argv)) or _Proc(),
    )
    monkeypatch.setattr(
        ffp_pull.subprocess, "Popen",
        lambda argv, **kw: calls.append(("popen", argv)) or _Proc(),
    )

    rc = ffp_pull._default_runner("fastflowlm", "qwen3.5:9b", 0, lambda _l: None, force=True)

    assert rc == 0
    assert calls == [("popen", ["flm", "pull", "qwen3.5:9b", "--force"])]


def test_b54_flm_env_repairs_a_systemprofile_model_path(monkeypatch):
    """A machine-scope %USERPROFILE% expands to the SYSTEM profile — repair it."""
    import ffp_flm_server
    bad = "C:\\Windows\\system32\\config\\systemprofile\\.flm"
    monkeypatch.setenv("FLM_MODEL_PATH", bad)

    env = ffp_flm_server.flm_env()

    assert env["FLM_MODEL_PATH"] != bad
    assert "systemprofile" not in env["FLM_MODEL_PATH"].lower()
    assert env["FLM_MODEL_PATH"].endswith(".flm")


def test_b54_flm_env_leaves_a_sane_model_path_alone(monkeypatch):
    import ffp_flm_server
    good = "D:\\models\\.flm"
    monkeypatch.setenv("FLM_MODEL_PATH", good)
    assert ffp_flm_server.flm_env()["FLM_MODEL_PATH"] == good


def test_b54_flm_env_does_not_invent_a_model_path(monkeypatch):
    """Unset means 'let FLM decide' — don't start forcing a value."""
    import ffp_flm_server
    monkeypatch.delenv("FLM_MODEL_PATH", raising=False)
    assert "FLM_MODEL_PATH" not in ffp_flm_server.flm_env()
