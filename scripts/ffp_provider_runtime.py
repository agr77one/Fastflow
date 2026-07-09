"""Provider-aware runtime operations for local LLM backends."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable

import ffp_flm_server
import ffp_provider_status
from subprocess_util import run_hidden

OLLAMA_SUGGESTED_MODELS = ["llama3.2:3b", "qwen2.5:3b", "gemma3:4b"]
LMSTUDIO_SUGGESTED_MODELS = [
    "qwen2.5-3b-instruct",
    "qwen2.5-7b-instruct",
    "llama-3.1-8b-instruct",
]
LEMONADE_SUGGESTED_MODELS = [
    "Qwen2.5-3B-Instruct-NPU",
    "Llama-3.2-3B-Instruct-Hybrid",
    "Qwen3-4B-Hybrid",
    "Gemma-4-E2B-it-GGUF",
    "Qwen2.5-7B-Instruct-NPU",
    "Qwen3.5-4B-GGUF",
]
OPENAI_COMPAT_PROVIDERS = frozenset({"lmstudio", "lemonade"})
MODEL_SUGGESTIONS = {
    "ollama": OLLAMA_SUGGESTED_MODELS,
    "lmstudio": LMSTUDIO_SUGGESTED_MODELS,
    "lemonade": LEMONADE_SUGGESTED_MODELS,
}


def normalize_provider(provider: str) -> str:
    key = str(provider or "fastflowlm").strip().lower()
    return key if key in ffp_provider_status.PROVIDERS else "fastflowlm"


def provider_cli(provider: str) -> str:
    provider = normalize_provider(provider)
    status = ffp_provider_status.provider_status(provider)
    spec = ffp_provider_status.PROVIDERS[provider]
    if provider in {"lmstudio", "lemonade"} and status.get("cli_path"):
        return str(status.get("cli_path"))
    return spec.cli


def openai_url(base_url: str, endpoint: str) -> str:
    """Build an OpenAI-compatible URL from either a server root or */v1 base."""
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        base = "http://127.0.0.1:52625"
    endpoint = str(endpoint or "").strip().lstrip("/")
    if endpoint.startswith("v1/"):
        endpoint = endpoint[3:]
    lowered = base.lower()
    if lowered.endswith("/v1") or lowered.endswith("/api/v1"):
        return base + "/" + endpoint
    return base + "/v1/" + endpoint


def is_provider_reachable(provider: str, base_url: str) -> bool:
    provider = normalize_provider(provider)
    if provider == "fastflowlm":
        return ffp_flm_server.is_flm_server_reachable(base_url)
    return ffp_provider_status.is_reachable(base_url)


def _ollama_tags(base_url: str) -> tuple[list[str], str]:
    url = str(base_url or "http://127.0.0.1:11434").rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError as exc:
        return [], f"Ollama API unreachable: {exc.reason}"
    except TimeoutError:
        return [], "Ollama API timed out"
    except Exception as exc:
        return [], f"Ollama model list failed: {exc}"
    models = []
    for entry in payload.get("models") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("model") or "").strip()
        if name:
            models.append(name)
    return models, ""


def _openai_models(base_url: str, provider_label: str = "OpenAI-compatible provider") -> tuple[list[str], str]:
    url = openai_url(base_url, "models")
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError as exc:
        return [], f"{provider_label} API unreachable: {exc.reason}"
    except TimeoutError:
        return [], f"{provider_label} API timed out"
    except Exception as exc:
        return [], f"{provider_label} model list failed: {exc}"
    models = []
    for entry in payload.get("data") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("id") or "").strip()
        if name:
            models.append(name)
    return models, ""


def _lmstudio_ls(runner: Callable | None = None) -> tuple[list[str], str]:
    cli = provider_cli("lmstudio")
    run = runner or run_hidden
    try:
        result = run([cli, "ls", "--json"], timeout=20, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return [], "LM Studio CLI not found"
    except Exception as exc:
        return [], f"LM Studio model list failed: {exc}"
    output = (result.stdout or "").strip()
    if result.returncode != 0:
        detail = ((result.stderr or "") + "\n" + output).strip()
        return [], f"LM Studio model list failed: {detail or result.returncode}"
    try:
        payload = json.loads(output or "[]")
    except ValueError as exc:
        return [], f"LM Studio model list was not JSON: {exc}"
    models = []
    for entry in payload if isinstance(payload, list) else []:
        if not isinstance(entry, dict) or entry.get("type") not in (None, "llm"):
            continue
        name = str(entry.get("modelKey") or entry.get("indexedModelIdentifier") or "").strip()
        if name:
            models.append(name)
    return models, ""


def list_models(provider: str, filter_kind: str, model: str, no_window: int, base_url: str) -> dict:
    provider = normalize_provider(provider)
    if filter_kind not in {"installed", "not-installed", "all"}:
        return {"error": f"bad filter: {filter_kind}", "models": [], "active": model, "provider": provider}
    if provider == "fastflowlm":
        out = ffp_flm_server.flm_list(filter_kind, model, no_window)
        out["provider"] = provider
        return out

    suggested = list(MODEL_SUGGESTIONS.get(provider, []))
    if provider == "lmstudio":
        installed, error = _lmstudio_ls()
        if filter_kind == "installed":
            models = installed
        elif filter_kind == "not-installed":
            models = [name for name in suggested if name not in installed]
        else:
            models = installed + [name for name in suggested if name not in installed]
        out = {"models": models, "active": model, "provider": provider}
        if error:
            out["error"] = error
        return out

    if not ffp_provider_status.is_reachable(base_url):
        if filter_kind == "installed":
            models = []
        elif filter_kind == "not-installed":
            models = suggested
        else:
            models = suggested
        return {
            "models": models,
            "active": model,
            "provider": provider,
            "error": f"{ffp_provider_status.PROVIDERS[provider].label} API unreachable",
        }

    if provider == "ollama":
        installed, error = _ollama_tags(base_url)
    elif provider in OPENAI_COMPAT_PROVIDERS:
        installed, error = _openai_models(base_url, ffp_provider_status.PROVIDERS[provider].label)
    else:
        installed, error = [], f"model listing is not supported for {provider}"
    suggested = [name for name in suggested if name not in installed]
    if filter_kind == "installed":
        models = installed
    elif filter_kind == "not-installed":
        models = suggested
    else:
        models = installed + suggested
    out = {"models": models, "active": model, "provider": provider}
    if error:
        out["error"] = error
    return out


def pull_command(provider: str, model: str) -> list[str]:
    provider = normalize_provider(provider)
    cli = provider_cli(provider)
    if provider == "lmstudio":
        return [cli, "get", model, "--gguf", "-y"]
    if provider == "lemonade":
        return [cli, "pull", model]
    if provider == "ollama":
        return [cli, "pull", model]
    return [cli, "pull", model]


def remove_command(provider: str, model: str) -> list[str]:
    provider = normalize_provider(provider)
    cli = provider_cli(provider)
    if provider == "ollama":
        return [cli, "rm", model]
    if provider == "fastflowlm":
        return [cli, "remove", model]
    raise RuntimeError(f"Removing models is not supported for {ffp_provider_status.PROVIDERS[provider].label}.")


def pull_model(provider: str, model: str, no_window: int, *, timeout: int = 900) -> str:
    provider = normalize_provider(provider)
    name = str(model or "").strip()
    if not name:
        raise ValueError("model name is empty")
    command = pull_command(provider, name)
    result = run_hidden(command, timeout=timeout, creationflags=no_window)
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        raise RuntimeError(f"{command[0]} pull failed (exit {result.returncode}):\n{output.strip()}")
    return output.strip() or f"pulled {name}"


def remove_model(provider: str, model: str, no_window: int, *, timeout: int = 60) -> str:
    provider = normalize_provider(provider)
    name = str(model or "").strip()
    if not name:
        raise ValueError("model name is empty")
    command = remove_command(provider, name)
    result = run_hidden(command, timeout=timeout, creationflags=no_window)
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        raise RuntimeError(f"{command[0]} remove failed (exit {result.returncode}):\n{output.strip()}")
    return output.strip() or f"removed {name}"
