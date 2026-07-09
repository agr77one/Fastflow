"""First-run wizard for Flowkey.

Six pages walk a new user from a fresh install to a working setup:

  1. Welcome + local provider detection
  2. License accept
  3. Pick a local provider/model and pull it if supported
  4. Preview / rebind the four hotkeys
  5. Warmup test action (smoke-test the LLM pipeline)
  6. Done — open the dashboard

Launched in three ways:

  - by Inno Setup at the end of install ([Run] postinstall)
  - by grammarFix.ahk on startup when `.first_run_done` is missing
  - manually by the user (Settings → Re-run first-run wizard)

On finish, writes paths.MARKER_FIRST_RUN_DONE so subsequent launches skip
the wizard. Stdlib only.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
from tkinter import messagebox, scrolledtext, ttk

import ffp_config
import ffp_provider_status
import paths as _paths
from loopback_http import daemon_headers, json_post

log = logging.getLogger("ffp.first_run")

# ---------------------------------------------------------------------------
# Config + paths
# ---------------------------------------------------------------------------

_paths.ensure_dirs()
_paths.seed_config_if_missing()

CONFIG_PATH = _paths.CONFIG_FILE
EXAMPLE_PATH = _paths.CONFIG_EXAMPLE_FILE
DONE_MARKER = _paths.MARKER_FIRST_RUN_DONE
DAEMON_URL = "http://127.0.0.1:52650"
DEFAULT_FLM_URL = "http://127.0.0.1:52625"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
PROVIDER_ORDER = ["fastflowlm", "ollama", "lmstudio", "lemonade"]
PROVIDER_LABELS = {
    "fastflowlm": "FastFlowLM",
    "ollama": "Ollama",
    "lmstudio": "LM Studio",
    "lemonade": "Lemonade",
}

# Default models surface up-front; user can refresh to see what the provider has locally.
DEFAULT_MODEL_CHOICES = {
    "fastflowlm": ["qwen3.5:4b", "nanbeige4.1:3b", "phi4:14b"],
    "ollama": ["llama3.2:3b", "qwen2.5:3b", "gemma3:4b"],
    "lmstudio": ["qwen2.5-3b-instruct", "qwen2.5-7b-instruct", "llama-3.1-8b-instruct"],
    "lemonade": ["Qwen2.5-3B-Instruct-NPU", "Llama-3.2-3B-Instruct-Hybrid", "Qwen3-4B-Hybrid"],
}

HOTKEY_FIELDS = [
    ("grammar_fix",   "Grammar fix",            "^+g"),
    ("open_chat",     "Open Chat tab",          "^!c"),
    ("capture_note",  "Capture note",           "^!n"),
    ("ask_chat",      "Ask in chat (selection)","^+a"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_config() -> dict:
    return ffp_config.load_config(CONFIG_PATH)


def save_config(cfg: dict) -> None:
    ffp_config.save_config(CONFIG_PATH, cfg)


def _llm_cfg(cfg: dict) -> dict:
    llm = cfg.get("llm")
    if not isinstance(llm, dict):
        llm = {}
        cfg["llm"] = llm
    return llm


def _provider_defaults(provider: str) -> tuple[str, str, str]:
    status = ffp_provider_status.provider_status(provider)
    choices = DEFAULT_MODEL_CHOICES.get(provider, DEFAULT_MODEL_CHOICES["fastflowlm"])
    return (
        str(status.get("base_url") or DEFAULT_FLM_URL),
        str(status.get("default_model") or choices[0]),
        str(status.get("auth_bearer") or "flm"),
    )


def choose_starting_provider(cfg: dict) -> str:
    llm = _llm_cfg(cfg)
    configured = str(llm.get("provider") or "fastflowlm").strip().lower()
    status = ffp_provider_status.providers_status(
        configured,
        str(llm.get("base_url") or cfg.get("flm_base_url") or DEFAULT_FLM_URL),
    )
    providers = status.get("providers") or {}
    active = providers.get(configured) or {}
    if active.get("available"):
        return configured
    for fallback in ("ollama", "lmstudio", "lemonade"):
        if (providers.get(fallback) or {}).get("available"):
            return fallback
    return configured if configured in ffp_provider_status.PROVIDERS else "fastflowlm"


def detect_amd_npu() -> tuple[bool, str]:
    """Probe Windows for an AMD Ryzen AI NPU driver. Returns (found, description).

    Uses Get-PnpDevice via PowerShell — slow but deterministic. We look for
    devices whose FriendlyName contains 'NPU' AND ManufacturerName contains
    'AMD'. False negatives are OK (we just warn, don't block).
    """
    ps = (
        "Get-PnpDevice -Status OK | "
        "Where-Object { $_.FriendlyName -match 'NPU|XDNA|Ryzen AI' } | "
        "Select-Object -First 3 FriendlyName, Manufacturer | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except Exception as exc:
        return False, f"Could not probe device list: {exc}"
    out = (result.stdout or "").strip()
    if not out:
        return False, "No AMD NPU device was detected."
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return False, "Device probe returned unexpected output."
    if isinstance(data, dict):
        data = [data]
    if not data:
        return False, "No AMD NPU device was detected."
    names = ", ".join(str(d.get("FriendlyName") or "?") for d in data)
    return True, names


def detect_local_providers(active_provider: str, base_url: str) -> tuple[bool, str]:
    status = ffp_provider_status.providers_status(active_provider, base_url)
    providers = status.get("providers") or {}
    lines: list[str] = []
    for key in PROVIDER_ORDER:
        item = providers.get(key) or {}
        installed = "installed" if item.get("installed") else "not on PATH"
        reachable = "API reachable" if item.get("reachable") else "API not reachable"
        lines.append(f"{item.get('label') or key}: {installed}, {reachable}")
    available = status.get("available") or []
    if available:
        return True, "Available providers: " + ", ".join(available) + "\n" + "\n".join(lines)
    return False, "No local LLM provider was detected.\n" + "\n".join(lines)


def fetch_models(provider: str, base_url: str) -> list[str]:
    """Ask the local provider for the installed model list. Returns [] on failure."""
    provider = str(provider or "fastflowlm").strip().lower()
    try:
        import ffp_provider_runtime
        return list(ffp_provider_runtime.list_models(provider, "installed", "", 0, base_url).get("models") or [])
    except Exception:
        return []


def start_provider_via_daemon() -> tuple[bool, str]:
    """POST /action/start for the currently configured provider."""
    try:
        payload = json_post(
            DAEMON_URL + "/action/start",
            {"args": {}},
            headers=daemon_headers(),
            timeout=12.0,
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            return False, "Daemon rejected request (missing API header)."
        return False, f"Daemon HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, TypeError) as exc:
        return False, f"Daemon unreachable: {exc}"
    if not payload.get("ok"):
        return False, str(payload.get("error") or "start failed")
    return True, str(payload.get("result") or "started")


def warmup_via_daemon(model: str) -> tuple[bool, str]:
    """POST /action/warmup. Returns (ok, message)."""
    try:
        payload = json_post(
            DAEMON_URL + "/action/warmup",
            {"args": {"model": model}},
            headers=daemon_headers(),
            timeout=20.0,
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            return False, "Daemon rejected request (missing API header)."
        return False, f"Daemon HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, TypeError) as exc:
        return False, f"Daemon unreachable: {exc}"
    if not payload.get("ok"):
        return False, str(payload.get("error") or "warmup failed")
    return True, str(payload.get("result") or "Warmup OK")


def open_dashboard() -> bool:
    """Signal the AHK front-end to open the dashboard via daemon marker file."""
    try:
        payload = json_post(
            DAEMON_URL + "/action/open_dashboard",
            {"args": {}},
            headers=daemon_headers(),
            timeout=2.0,
        )
        return bool(payload.get("ok"))
    except Exception as exc:
        log.warning("open_dashboard failed: %s", exc)
        return False


def load_license_text() -> str:
    """Read the LICENSE shipped at APP_DIR/LICENSE.txt, with fallback."""
    candidates = [
        _paths.APP_DIR / "LICENSE.txt",
        _paths.APP_DIR / "LICENSE",
        _paths.APP_DIR / "release" / "LICENSE",   # dev tree
    ]
    for c in candidates:
        if c.exists():
            try:
                return c.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return (
        "MIT License\n\n"
        "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
        "of this software and associated documentation files (the 'Software'), to deal\n"
        "in the Software without restriction...\n\n"
        "(Full text not bundled — see project repository.)"
    )


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

class WizardApp:
    PAGE_TITLES = [
        "Welcome",
        "License",
        "Model",
        "Hotkeys",
        "Warmup",
        "Done",
    ]

    def __init__(self) -> None:
        self.cfg = ensure_config()
        starting_provider = choose_starting_provider(self.cfg)
        base_url, default_model, auth_bearer = _provider_defaults(starting_provider)
        llm = _llm_cfg(self.cfg)
        if starting_provider != "fastflowlm":
            llm["provider"] = starting_provider
            llm["base_url"] = str(llm.get("base_url") or base_url)
            llm["model"] = str(llm.get("model") or default_model)
            llm["auth_bearer"] = str(llm.get("auth_bearer", auth_bearer))
            llm["timeout_seconds"] = int(llm.get("timeout_seconds") or 120)
            llm["auto_start"] = False
        else:
            llm.setdefault("provider", "fastflowlm")
            llm.setdefault("base_url", base_url)
            llm.setdefault("model", default_model)
            llm.setdefault("auth_bearer", auth_bearer)
            llm.setdefault("timeout_seconds", 60)
            llm.setdefault("auto_start", True)
        self.root = tk.Tk()
        self.root.title("Flowkey — First-Run Setup")
        self.root.geometry("640x520")
        self.root.minsize(560, 460)
        # Closing the window (X) counts as "seen" — write the done marker so the
        # wizard doesn't reappear on every launch. The tray "Re-run wizard" item
        # re-triggers it on demand. See SPEC B16 / V38.
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # State vars
        self.step = 0
        self.var_provider = tk.StringVar(value=str(llm.get("provider") or starting_provider))
        self.var_base_url = tk.StringVar(value=str(llm.get("base_url") or self.cfg.get("flm_base_url") or base_url))
        self.var_model = tk.StringVar(value=str(llm.get("model") or self.cfg.get("flm_model") or default_model))
        self.var_license_accept = tk.BooleanVar(value=False)

        hk = self.cfg.get("hotkeys") or {}
        self.var_hotkeys: dict[str, tk.StringVar] = {
            k: tk.StringVar(value=str(hk.get(k) or default))
            for k, _label, default in HOTKEY_FIELDS
        }

        self.provider_found: bool = False
        self.warmup_ok: bool = False
        self._pull_proc: subprocess.Popen[str] | None = None

        self.frames: list[ttk.Frame] = []
        self._build()
        self._show(0)

    # ---- skeleton ----------------------------------------------------------

    def _build(self) -> None:
        header = ttk.Frame(self.root, padding=(20, 18, 20, 0))
        header.pack(fill="x")
        self.header_title = ttk.Label(header, text="Welcome", font=("Segoe UI", 16, "bold"))
        self.header_title.pack(anchor="w")
        self.subtitle = ttk.Label(header, text="", foreground="#666")
        self.subtitle.pack(anchor="w", pady=(2, 0))

        body = ttk.Frame(self.root, padding=20)
        body.pack(fill="both", expand=True)

        # Build all six pages once; switch visibility on navigation.
        self.frames.append(self._page_welcome(body))
        self.frames.append(self._page_license(body))
        self.frames.append(self._page_model(body))
        self.frames.append(self._page_hotkeys(body))
        self.frames.append(self._page_warmup(body))
        self.frames.append(self._page_done(body))

        bar = ttk.Frame(self.root, padding=(20, 0, 20, 20))
        bar.pack(fill="x")
        self.btn_back = ttk.Button(bar, text="Back", command=self.on_back)
        self.btn_back.pack(side="left")
        ttk.Button(bar, text="Skip", command=self.on_skip).pack(side="left", padx=(8, 0))
        self.btn_next = ttk.Button(bar, text="Next", command=self.on_next)
        self.btn_next.pack(side="right")

    # ---- page 1: welcome + provider check ---------------------------------

    def _page_welcome(self, parent: ttk.Frame) -> ttk.Frame:
        f = ttk.Frame(parent)
        ttk.Label(
            f, wraplength=560, justify="left", foreground="#444",
            text=(
                "Flowkey routes selected text through a local LLM provider such as\n"
                "Ollama or FastFlowLM. No cloud, no telemetry, no API key.\n\n"
                "This wizard walks you through:\n"
                "  • Checking local provider availability\n"
                "  • Accepting the license\n"
                "  • Picking a model\n"
                "  • Reviewing the hotkeys\n"
                "  • Running a warmup test"
            ),
        ).pack(anchor="w")

        sep = ttk.Separator(f, orient="horizontal")
        sep.pack(fill="x", pady=(16, 12))

        ttk.Label(f, text="Provider check", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.lbl_provider = ttk.Label(f, text="Probing for Ollama and FastFlowLM...", foreground="#666",
                                      wraplength=560, justify="left")
        self.lbl_provider.pack(anchor="w", pady=(4, 0))
        self.lbl_npu = ttk.Label(f, text="", foreground="#666", wraplength=560, justify="left")
        self.lbl_npu.pack(anchor="w", pady=(4, 0))
        ttk.Button(f, text="Recheck", command=self._run_provider_check).pack(anchor="w", pady=(10, 0))

        # Kick off the check async so the UI paints first.
        self.root.after(150, self._run_provider_check)
        return f

    def _run_npu_check(self) -> None:
        self._run_provider_check()

    def _run_provider_check(self) -> None:
        self.lbl_provider.configure(text="Probing for Ollama and FastFlowLM...", foreground="#666")
        self.lbl_npu.configure(text="", foreground="#666")
        self.root.update_idletasks()

        def worker() -> None:
            provider_ok, provider_msg = detect_local_providers(self.var_provider.get(), self.var_base_url.get())
            npu_ok, npu_msg = detect_amd_npu()
            self.root.after(0, lambda: self._provider_result(provider_ok, provider_msg, npu_ok, npu_msg))

        threading.Thread(target=worker, daemon=True).start()

    def _provider_result(self, provider_ok: bool, provider_msg: str, npu_ok: bool, npu_msg: str) -> None:
        self.provider_found = provider_ok
        if provider_ok:
            self.lbl_provider.configure(text=f"Local provider support detected.\n{provider_msg}", foreground="#0a6b3a")
        else:
            self.lbl_provider.configure(text=f"{provider_msg}\n\nYou can continue and start or install one later.",
                                        foreground="#a8201a")
        self._npu_result(npu_ok, npu_msg)

    def _npu_result(self, ok: bool, msg: str) -> None:
        self.npu_found = ok
        if ok:
            self.lbl_npu.configure(text=f"Optional FastFlowLM hardware check: found {msg}", foreground="#0a6b3a")
        else:
            self.lbl_npu.configure(text=f"Optional FastFlowLM hardware check: {msg}", foreground="#666")
        return
        if ok:
            self.lbl_npu.configure(text=f"✓ Found: {msg}", foreground="#0a6b3a")
        else:
            self.lbl_npu.configure(
                text=(f"⚠  {msg}\n\nYou can continue — FastFlowLM will surface a clearer error "
                      "if the NPU truly isn't accessible."),
                foreground="#a8201a",
            )

    # ---- page 2: license --------------------------------------------------

    def _page_license(self, parent: ttk.Frame) -> ttk.Frame:
        f = ttk.Frame(parent)
        ttk.Label(f, wraplength=560, justify="left", foreground="#444",
                  text="Review and accept the license before continuing.").pack(anchor="w", pady=(0, 8))

        text = scrolledtext.ScrolledText(f, wrap="word", height=14, font=("Consolas", 9))
        text.insert("1.0", load_license_text())
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)

        ttk.Checkbutton(f, text="I accept the license terms", variable=self.var_license_accept,
                        command=self._refresh_nav).pack(anchor="w", pady=(8, 0))
        return f

    # ---- page 3: model ----------------------------------------------------

    def _page_model(self, parent: ttk.Frame) -> ttk.Frame:
        f = ttk.Frame(parent)
        ttk.Label(f, wraplength=560, justify="left", foreground="#444",
                  text=("Pick a local provider and model. Click Start provider to bring up the local API "
                        "for the selected backend. Then use Refresh to see installed models, or Pull to "
                        "download one by name.")
                  ).pack(anchor="w", pady=(0, 12))

        provider_row = ttk.Frame(f)
        provider_row.pack(fill="x", pady=4)
        ttk.Label(provider_row, text="Provider", width=14).pack(side="left")
        provider_combo = ttk.Combobox(
            provider_row,
            textvariable=self.var_provider,
            values=PROVIDER_ORDER,
            state="readonly",
        )
        provider_combo.pack(side="left", fill="x", expand=True)
        provider_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_provider_changed())

        row1 = ttk.Frame(f)
        row1.pack(fill="x", pady=4)
        ttk.Label(row1, text="Base URL", width=14).pack(side="left")
        ttk.Entry(row1, textvariable=self.var_base_url).pack(side="left", fill="x", expand=True)

        row2 = ttk.Frame(f)
        row2.pack(fill="x", pady=4)
        ttk.Label(row2, text="Model", width=14).pack(side="left")
        self.model_combo = ttk.Combobox(
            row2,
            textvariable=self.var_model,
            values=DEFAULT_MODEL_CHOICES.get(self.var_provider.get(), DEFAULT_MODEL_CHOICES["fastflowlm"]),
        )
        self.model_combo.pack(side="left", fill="x", expand=True)
        ttk.Button(row2, text="Start provider", command=self.on_start_provider).pack(side="left", padx=(4, 0))
        ttk.Button(row2, text="↻ Refresh", command=self.on_refresh_models).pack(side="left", padx=(4, 0))
        ttk.Button(row2, text="↓ Pull",    command=self.on_pull_model).pack(side="left", padx=(4, 0))

        self.model_status = ttk.Label(f, text="", foreground="#666", wraplength=560, justify="left")
        self.model_status.pack(anchor="w", pady=(8, 0))

        self.pull_log = scrolledtext.ScrolledText(f, wrap="word", height=8, font=("Consolas", 9),
                                                  state="disabled")
        self.pull_log.pack(fill="both", expand=True, pady=(8, 0))
        return f

    def on_provider_changed(self) -> None:
        provider = self.var_provider.get().strip().lower()
        base_url, default_model, _auth = _provider_defaults(provider)
        choices = DEFAULT_MODEL_CHOICES.get(provider, DEFAULT_MODEL_CHOICES["fastflowlm"])
        self.var_base_url.set(base_url)
        self.model_combo["values"] = choices
        self.var_model.set(default_model if default_model else choices[0])
        self.model_status.configure(text=f"Provider set to {provider}. Click Refresh to check local models.",
                                    foreground="#666")

    def on_start_provider(self) -> None:
        provider = self.var_provider.get().strip().lower()
        label = PROVIDER_LABELS.get(provider, provider)
        self.model_status.configure(text=f"Starting {label}...", foreground="#666")
        self.root.update_idletasks()

        def worker() -> None:
            ok, msg = start_provider_via_daemon()
            self.root.after(0, lambda: self._start_provider_result(label, ok, msg))

        threading.Thread(target=worker, daemon=True).start()

    def _start_provider_result(self, label: str, ok: bool, msg: str) -> None:
        if ok:
            self.model_status.configure(text=f"{label}: {msg}", foreground="#0a6b3a")
            self.on_refresh_models()
            return
        self.model_status.configure(text=f"{label}: {msg}", foreground="#a8201a")

    def on_refresh_models(self) -> None:
        models = fetch_models(self.var_provider.get(), self.var_base_url.get())
        if not models:
            self.model_status.configure(
                text="No models returned. Start the provider or check the URL, then try Refresh again.",
                foreground="#a8201a",
            )
            return
        self.model_combo["values"] = models
        if self.var_model.get() not in models:
            self.var_model.set(models[0])
        self.model_status.configure(text=f"Found {len(models)} model(s) locally.", foreground="#0a6b3a")

    def on_pull_model(self) -> None:
        model = self.var_model.get().strip()
        if not model:
            self.model_status.configure(text="Enter a model name first.", foreground="#a8201a")
            return
        provider = self.var_provider.get().strip().lower()
        import ffp_provider_runtime
        command = ffp_provider_runtime.pull_command(provider, model)
        self._append_log("$ " + " ".join(command) + "\n")
        self.model_status.configure(text=f"Pulling {model} — this can take a few minutes...",
                                    foreground="#666")

        def worker() -> None:
            try:
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self._pull_proc = proc
            except FileNotFoundError:
                self.root.after(0, lambda: self._append_log(f"ERROR: {command[0]} not found.\n"))
                return
            assert proc.stdout is not None
            pull_timeout_s = 3600
            deadline = time.monotonic() + pull_timeout_s
            # Watchdog: the per-line deadline check below never fires if flm
            # hangs WITHOUT producing output (readline blocks forever). This
            # guarantees the kill regardless of output.
            watchdog = threading.Timer(pull_timeout_s, proc.kill)
            watchdog.daemon = True
            watchdog.start()
            try:
                for line in proc.stdout:
                    self.root.after(0, lambda ln=line: self._append_log(ln))
                    if time.monotonic() >= deadline:
                        proc.kill()
                        self.root.after(0, lambda: self._append_log(
                            f"\nFAILED: pull timed out after {pull_timeout_s // 60} minutes.\n"))
                        return
                try:
                    proc.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    proc.kill()
                    self.root.after(0, lambda: self._append_log(
                        f"\nFAILED: pull timed out after {pull_timeout_s // 60} minutes.\n"))
                    return
            finally:
                watchdog.cancel()
            done = "DONE." if proc.returncode == 0 else f"FAILED (exit {proc.returncode})."
            self.root.after(0, lambda: self._append_log(f"\n{done}\n"))
            if proc.returncode == 0:
                self.root.after(0, lambda: self.model_status.configure(
                    text=f"✓ Pulled {model}.", foreground="#0a6b3a"))
            self._pull_proc = None

        threading.Thread(target=worker, daemon=True).start()

    def _append_log(self, line: str) -> None:
        self.pull_log.configure(state="normal")
        self.pull_log.insert("end", line)
        self.pull_log.see("end")
        self.pull_log.configure(state="disabled")

    # ---- page 4: hotkeys --------------------------------------------------

    def _page_hotkeys(self, parent: ttk.Frame) -> ttk.Frame:
        f = ttk.Frame(parent)
        ttk.Label(f, wraplength=560, justify="left", foreground="#444",
                  text=("These are the default hotkeys. AHK syntax: ^ = Ctrl, ! = Alt, + = Shift, "
                        "# = Win. Examples: ^+g  ^!n  +#a")
                  ).pack(anchor="w", pady=(0, 12))

        grid = ttk.Frame(f)
        grid.pack(fill="x")
        for row, (key, label, _default) in enumerate(HOTKEY_FIELDS):
            ttk.Label(grid, text=label, width=22).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(grid, textvariable=self.var_hotkeys[key], width=12).grid(
                row=row, column=1, sticky="w", padx=(8, 0), pady=4)

        ttk.Label(f, foreground="#666", wraplength=560, justify="left",
                  text=("Tip: you can change these later from the dashboard's Config tab. "
                        "If a hotkey doesn't fire, try a different combo — some keyboards ghost "
                        "Shift + N as a phantom C, and some apps steal Ctrl + Shift + Q.")
                  ).pack(anchor="w", pady=(16, 0))
        return f

    # ---- page 5: warmup ---------------------------------------------------

    def _page_warmup(self, parent: ttk.Frame) -> ttk.Frame:
        f = ttk.Frame(parent)
        ttk.Label(f, wraplength=560, justify="left", foreground="#444",
                  text=("Let's warm up the model. This sends a small test request through the "
                        "daemon -> local LLM provider pipeline and times the round trip. "
                        "First runs are slower because the model loads into memory.")
                  ).pack(anchor="w", pady=(0, 12))

        ttk.Button(f, text="Run warmup", command=self.on_run_warmup).pack(anchor="w")
        self.warmup_status = ttk.Label(f, text="", foreground="#666", wraplength=560, justify="left")
        self.warmup_status.pack(anchor="w", pady=(12, 0))
        return f

    def on_run_warmup(self) -> None:
        self.warmup_status.configure(text="Warming up — this can take 10–30 seconds the first time...",
                                     foreground="#666")
        self.root.update_idletasks()

        def worker() -> None:
            ok, msg = warmup_via_daemon(self.var_model.get().strip())
            self.root.after(0, lambda: self._warmup_result(ok, msg))

        threading.Thread(target=worker, daemon=True).start()

    def _warmup_result(self, ok: bool, msg: str) -> None:
        self.warmup_ok = ok
        if ok:
            self.warmup_status.configure(text=f"✓ {msg}", foreground="#0a6b3a")
        else:
            self.warmup_status.configure(
                text=(f"⚠  {msg}\n\nYou can still finish the wizard and try later from the tray menu."),
                foreground="#a8201a",
            )

    # ---- page 6: done -----------------------------------------------------

    def _page_done(self, parent: ttk.Frame) -> ttk.Frame:
        f = ttk.Frame(parent)
        ttk.Label(f, text="You're set!", font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(f, wraplength=560, justify="left", foreground="#444",
                  text=("Flowkey is ready. Hotkeys are live globally.\n\n"
                        "Try one of these now:\n"
                        f"  • {HOTKEY_FIELDS[0][2]} — grammar-fix selected text\n"
                        f"  • {HOTKEY_FIELDS[1][2]} — open the Chat tab\n"
                        f"  • {HOTKEY_FIELDS[2][2]} — capture the selection as a note\n"
                        f"  • {HOTKEY_FIELDS[3][2]} — ask in chat with the selection attached\n\n"
                        "Right-click the tray icon for the dashboard, diagnostics, and settings.")
                  ).pack(anchor="w")
        ttk.Button(f, text="Open dashboard now", command=self.on_open_dashboard).pack(anchor="w", pady=(16, 0))
        return f

    def on_open_dashboard(self) -> None:
        open_dashboard()

    # ---- navigation -------------------------------------------------------

    def _show(self, idx: int) -> None:
        for i, frame in enumerate(self.frames):
            if i == idx:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        self.step = idx
        self.header_title.configure(text=self.PAGE_TITLES[idx])
        self.subtitle.configure(text=f"Step {idx + 1} of {len(self.frames)}")
        self._refresh_nav()

    def _refresh_nav(self) -> None:
        idx = self.step
        self.btn_back.configure(state=("normal" if idx > 0 else "disabled"))
        is_last = idx == len(self.frames) - 1
        self.btn_next.configure(text=("Finish" if is_last else "Next"))
        # Gate: license must be accepted to move past page 2.
        if idx == 1 and not self.var_license_accept.get():
            self.btn_next.configure(state="disabled")
        else:
            self.btn_next.configure(state="normal")

    def on_back(self) -> None:
        if self.step > 0:
            self._show(self.step - 1)

    def on_next(self) -> None:
        if self.step == 1 and not self.var_license_accept.get():
            messagebox.showwarning("License", "Please accept the license to continue.")
            return
        if self.step < len(self.frames) - 1:
            # Persist incrementally so a crash mid-wizard doesn't lose state.
            self._persist_partial()
            self._show(self.step + 1)
        else:
            self.on_finish()

    def on_skip(self) -> None:
        if messagebox.askyesno(
            "Flowkey",
            "Skip the wizard? You can re-run it later from the dashboard.",
        ):
            self._persist_partial()
            self._mark_done()
            self.root.destroy()

    # ---- persistence ------------------------------------------------------

    def _persist_partial(self) -> None:
        """Save what we have so far. Safe to call from any page."""
        provider = self.var_provider.get().strip().lower()
        fallback_url, default_model, auth_bearer = _provider_defaults(provider)
        raw_url = self.var_base_url.get().strip() or fallback_url
        try:
            self.cfg["flm_base_url"] = ffp_config.validate_flm_base_url(raw_url)
        except ValueError as exc:
            log.warning("wizard ignored non-loopback flm_base_url %r: %s", raw_url, exc)
            self.cfg["flm_base_url"] = fallback_url
        model = self.var_model.get().strip() or default_model
        llm = _llm_cfg(self.cfg)
        llm["provider"] = provider
        llm["base_url"] = self.cfg["flm_base_url"]
        llm["model"] = model
        llm["auth_bearer"] = auth_bearer
        llm["timeout_seconds"] = 120 if provider != "fastflowlm" else int(llm.get("timeout_seconds") or 60)
        llm["auto_start"] = provider == "fastflowlm"
        self.cfg["flm_model"] = model
        self.cfg["flm_timeout_seconds"] = int(llm["timeout_seconds"])
        hk = self.cfg.get("hotkeys") or {}
        for key, _label, _default in HOTKEY_FIELDS:
            hk[key] = self.var_hotkeys[key].get().strip()
        self.cfg["hotkeys"] = hk
        try:
            save_config(self.cfg)
        except OSError as exc:
            log.warning("wizard config save failed: %s", exc)

    def on_finish(self) -> None:
        self._persist_partial()
        self._mark_done()
        # Try to open the dashboard if the daemon is up.
        open_dashboard()
        self.root.destroy()

    def on_close(self) -> None:
        # Dismissing the wizard still marks first-run complete so it stops
        # nagging on every launch (re-openable via the tray menu).
        if self._pull_proc is not None and self._pull_proc.poll() is None:
            try:
                self._pull_proc.kill()
            except OSError as exc:
                log.warning("failed to kill in-flight model pull: %s", exc)
            self._pull_proc = None
        self._mark_done()
        self.root.destroy()

    def _mark_done(self) -> None:
        try:
            DONE_MARKER.parent.mkdir(parents=True, exist_ok=True)
            DONE_MARKER.write_text("1\n", encoding="utf-8")
        except OSError:
            pass

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    # Always run when invoked explicitly (Inno Setup or "Re-run wizard"
    # menu item). Only skip when launched from AHK startup and the marker
    # already exists. The AHK side controls that gate by passing --check.
    if "--check" in sys.argv and DONE_MARKER.exists():
        return 0
    WizardApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
