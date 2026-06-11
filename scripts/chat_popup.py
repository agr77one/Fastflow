"""Modal chat popup for the local FLM server, with multi-tab conversations.

Config is read from the shared `grammar_hotkey.config.json` under the `chat`
block; `llm_base_url` and `llm_model` fall back to the top-level
`flm_base_url` / `flm_model` so the chat window always talks to the same
endpoint as the grammar/prompt hotkeys.

Conversations are organized as tabs (ttk.Notebook). Each tab is a separate
thread with its own history. Threads are persisted to `chat_threads.jsonl`
sitting next to this script; the file is rewritten on every save so only the
latest snapshot per thread is retained (keeps it small while preserving full
conversation memory across launches).

Stdlib only. Single-instance enforced via a loopback TCP lock.
"""

from __future__ import annotations

import argparse
import json
import logging
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import uuid
from tkinter import scrolledtext, ttk

import ffp_config
import loopback_http
import paths as _paths
from subprocess_util import NO_WINDOW

log = logging.getLogger("ffp.chat")

SHARED_CONFIG_PATH = _paths.CONFIG_FILE
DAEMON_BASE_URL = "http://127.0.0.1:52650"
THREADS_PATH = _paths.CHAT_THREADS_FILE
INGEST_NONCE_PATH = _paths.DATA_DIR / ".chat_ingest_nonce"
MAX_LOADED_THREADS = 20
TITLE_MAX_CHARS = 24

DEFAULTS = {
    "llm_base_url": "http://127.0.0.1:52625",
    "llm_model": "qwen3.5:4b",
    "llm_auth_bearer": "flm",
    "request_timeout_seconds": 240,
    "temperature": 0.3,
    "max_tokens": 1024,
    "context_window_turns": 12,
    "system_prompt": "You are a concise, helpful local assistant.",
    "window": {
        "title": "Local LLM Chat",
        "width": 640,
        "height": 600,
        "topmost": True,
        "single_instance_port": 52640,
    },
}


def build_notes_context_message(query: str, search_fn, max_notes: int = 4) -> tuple[str | None, list[str]]:
    """Build the retrieval-injection system message for notes mode.

    Runs the ranked vault search and formats the top hits as grounding context
    (the model is told to cite note titles in [brackets]). Returns
    (message, titles); (None, []) when nothing matched so the caller can fall
    back to a plain turn. Pure given search_fn — unit-testable without a vault.
    """
    try:
        found = search_fn(query, max_notes)
    except Exception:
        return None, []
    results = (found or {}).get("results") or []
    if not results:
        return None, []
    titles: list[str] = []
    blocks: list[str] = []
    for r in results:
        title = str(r.get("title") or "untitled")
        titles.append(title)
        category = str(r.get("category") or "")
        snippet = str(r.get("snippet") or "").strip()
        blocks.append(f"[{title}] ({category})\n{snippet}")
    message = (
        "The user has a personal notes vault. The saved notes below are "
        "relevant to their next message. Ground your answer in these notes and "
        "cite note titles in [brackets] when you use them. If the notes do not "
        "answer the question, say so briefly instead of guessing.\n\n"
        + "\n\n".join(blocks)
    )
    return message, titles


def retrieve_notes_context(query: str, max_notes: int = 4) -> tuple[str | None, list[str]]:
    """Vault-backed wrapper around build_notes_context_message. The notes
    module (which pulls in grammar_fix/config) is imported lazily so chat
    startup doesn't pay for it when notes mode is never used."""
    try:
        import notes
    except Exception:
        return None, []
    return build_notes_context_message(query, notes.search_notes, max_notes)


def load_config() -> dict:
    """Merge the shared config's `chat` block over DEFAULTS.

    Endpoint + model always come from top-level ``flm_*`` keys (same source as
    the grammar hotkeys and dashboard). The ``chat`` block cannot override them.
    """
    cfg = json.loads(json.dumps(DEFAULTS))
    shared: dict = {}
    if SHARED_CONFIG_PATH.exists():
        try:
            shared = json.loads(SHARED_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("failed to read shared config %s: %s", SHARED_CONFIG_PATH, exc)
            shared = {}

    chat_block = dict((shared.get("chat") or {}) if isinstance(shared, dict) else {})
    # Never let a stale chat.llm_* shadow the live flm_* selection.
    chat_block.pop("llm_model", None)
    chat_block.pop("llm_base_url", None)

    for k, v in chat_block.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v

    llm_block = shared.get("llm") if isinstance(shared.get("llm"), dict) else {}
    cfg["llm_model"] = str(
        llm_block.get("model") or shared.get("flm_model") or cfg.get("llm_model") or DEFAULTS["llm_model"]
    ).strip()
    raw_url = str(
        llm_block.get("base_url") or shared.get("flm_base_url") or cfg.get("llm_base_url") or DEFAULTS["llm_base_url"]
    ).strip()
    cfg["llm_auth_bearer"] = str(
        llm_block.get("auth_bearer") or cfg.get("llm_auth_bearer") or DEFAULTS["llm_auth_bearer"]
    ).strip()
    try:
        cfg["llm_base_url"] = ffp_config.validate_flm_base_url(raw_url)
    except ValueError as exc:
        log.warning("invalid chat llm_base_url, using default: %s", exc)
        cfg["llm_base_url"] = DEFAULTS["llm_base_url"]
    return _overlay_live_flm_settings(cfg)


def _overlay_live_flm_settings(cfg: dict) -> dict:
    """Prefer the daemon's in-memory config (same source as the dashboard)."""
    try:
        payload = loopback_http.json_post(
            DAEMON_BASE_URL + "/action/config_snapshot",
            {"args": {}},
            headers=loopback_http.daemon_headers(),
            timeout=2.0,
        )
        if payload.get("ok") and isinstance(payload.get("result"), dict):
            live = payload["result"]
            model = str(live.get("flm_model") or "").strip()
            url = str(live.get("flm_base_url") or "").strip()
            llm_block = live.get("llm") if isinstance(live.get("llm"), dict) else {}
            model = str(llm_block.get("model") or model).strip()
            url = str(llm_block.get("base_url") or url).strip()
            bearer = str(llm_block.get("auth_bearer") or "").strip()
            if model:
                cfg["llm_model"] = model
            if url:
                cfg["llm_base_url"] = ffp_config.validate_flm_base_url(url)
            if bearer:
                cfg["llm_auth_bearer"] = bearer
    except Exception as exc:
        log.debug("daemon config_snapshot unavailable, using file config: %s", exc)
    return cfg


# ---------- Thread persistence -------------------------------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def load_threads() -> list[dict]:
    """Read chat_threads.jsonl, return latest snapshot per thread, newest first."""
    if not THREADS_PATH.exists():
        return []
    latest: dict[str, dict] = {}
    try:
        with THREADS_PATH.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                tid = row.get("thread_id")
                if not tid:
                    continue
                prev = latest.get(tid)
                if (prev is None) or (str(row.get("updated_at") or "") >= str(prev.get("updated_at") or "")):
                    latest[tid] = row
    except Exception:
        return []
    ordered = sorted(latest.values(), key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return ordered[:MAX_LOADED_THREADS]


def save_threads(threads: list[dict]) -> None:
    """Compact-rewrite: one line per thread, atomic via tmp+replace."""
    try:
        tmp = THREADS_PATH.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for t in threads:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
        tmp.replace(THREADS_PATH)
    except Exception as exc:
        log.warning("failed to save chat threads: %s", exc)


# ---------- Single-instance guard ----------------------------------------------------

def _ensure_ingest_nonce() -> str:
    """Publish a per-instance nonce so only the daemon can inject selections."""
    nonce = secrets.token_hex(16)
    try:
        _paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
        INGEST_NONCE_PATH.write_text(nonce, encoding="utf-8")
    except OSError as exc:
        log.warning("failed to write ingest nonce: %s", exc)
    return nonce


def try_acquire_single_instance(port: int) -> socket.socket | None:
    """Bind a loopback port. Return the socket on success, None if another instance owns it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(4)
        s.setblocking(False)
        return s
    except OSError:
        s.close()
        return None


def ping_existing_instance(port: int) -> bool:
    """Ask the running instance to reload config and surface its window."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5) as c:
            c.sendall(b"RELOAD\nSHOW\n")
        return True
    except OSError:
        return False


# ---------- LLM client ---------------------------------------------------------------

class LLMClient:
    """Minimal OpenAI-compatible chat client for the local FLM endpoint."""

    def __init__(self, cfg: dict):
        self.base_url = str(cfg["llm_base_url"]).rstrip("/")
        self.model = str(cfg["llm_model"])
        self.bearer = str(cfg.get("llm_auth_bearer") or "")
        self.timeout = int(cfg.get("request_timeout_seconds", 240))
        self.temperature = float(cfg.get("temperature", 0.3))
        self.max_tokens = int(cfg.get("max_tokens", 1024))

    def chat(self, messages: list[dict]) -> str:
        """POST /v1/chat/completions and return the assistant content. Raises
        RuntimeError on transport, timeout, parse, or empty-choices errors."""
        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"
        req = urllib.request.Request(
            self.base_url + "/v1/chat/completions",
            data=body, headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.URLError as e:
            raise RuntimeError(f"LLM unreachable at {self.base_url}: {e.reason}") from e
        except TimeoutError:
            raise RuntimeError(f"LLM timed out after {self.timeout}s")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Malformed LLM response: {e}") from e

        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("LLM returned no choices.")
        msg = choices[0].get("message") or {}
        return str(msg.get("content") or "").strip()


# ---------- Conversation tab ---------------------------------------------------------

class ConversationTab:
    """One tab = one thread. Each tab owns its transcript widget, input,
    history list, and thread_id. Tabs are independent contexts."""

    def __init__(self, app: ChatApp, thread: dict | None = None):
        thread = thread or {}
        self.app = app
        self.thread_id = thread.get("thread_id") or uuid.uuid4().hex
        self.history: list[dict] = list(thread.get("history") or [])
        self.title = str(thread.get("title") or "New chat")
        self.inflight = False

        self.frame = ttk.Frame(app.notebook)
        self._build()
        if self.history:
            for msg in self.history:
                tag = "user" if msg.get("role") == "user" else "assistant"
                prefix = "You: " if tag == "user" else "LLM: "
                self._append(tag, f"{prefix}{msg.get('content','')}\n")
        else:
            self._append("meta", "Ctrl+T new tab • Ctrl+W close tab • Enter sends • Shift+Enter newline\n")

    def _build(self) -> None:
        outer = ttk.Frame(self.frame, padding=6)
        outer.pack(fill="both", expand=True)

        self.transcript = scrolledtext.ScrolledText(
            outer, wrap="word", state="disabled", height=18,
            font=("Segoe UI", 10),
        )
        self.transcript.pack(fill="both", expand=True)
        self.transcript.tag_configure("user", foreground="#1a4fb3", font=("Segoe UI", 10, "bold"))
        self.transcript.tag_configure("assistant", foreground="#0a6b3a")
        self.transcript.tag_configure("error", foreground="#a8201a")
        self.transcript.tag_configure("meta", foreground="#777777", font=("Segoe UI", 9, "italic"))

        self.input = tk.Text(outer, height=4, wrap="word", font=("Segoe UI", 10))
        self.input.pack(fill="x", pady=(8, 4))

        # Picker bar — only shown when a selection is ingested via Ctrl+Shift+A.
        # Hidden by default to keep the regular chat UI clean.
        self.picker_frame = ttk.Frame(outer)
        self.picker_visible = False

        bar = ttk.Frame(outer)
        bar.pack(fill="x")
        # Notes mode: ground the next replies in vault notes retrieved for each
        # message (per-call context injection — the retrieved text is never
        # written into the thread history). See SPEC V37 / T31.
        self.notes_mode = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="📚 My notes", variable=self.notes_mode).pack(side="left")
        ttk.Button(bar, text="Clear thread", command=self.on_clear).pack(side="right", padx=(4, 0))
        self.send_btn = ttk.Button(bar, text="Send  (Enter)", command=self.on_send)
        self.send_btn.pack(side="right")

        self.input.bind("<Return>", self._on_enter)

    def ingest_selection(self, text: str, source_app: str = "") -> None:
        """Display a selection as a quoted context block + show the action picker.
        Called when the user pressed Ctrl+Shift+A in another app."""
        text = (text or "").strip()
        if not text:
            return
        # Truncate for transcript display only — full text goes into the question.
        preview = text if len(text) <= 1200 else text[:1200] + " …(truncated for display)"
        quoted = "\n".join("> " + ln for ln in preview.splitlines() or [""])
        hdr = f"📥 Ingested selection from {source_app or 'app'} ({len(text)} chars):\n"
        self._append("meta", hdr + quoted + "\n\n")
        # Keep the full text around so picker buttons can build the prompt.
        self._ingested_text = text
        self._show_picker()
        # Give a short tab title hint.
        if self.title == "New chat":
            preview_title = text.splitlines()[0][:48] if text.splitlines() else text[:48]
            self.title = "Ask: " + preview_title + ("…" if len(preview_title) >= 48 else "")
            self.app.rename_tab(self, self.title)

    def _show_picker(self) -> None:
        for child in self.picker_frame.winfo_children():
            child.destroy()
        ttk.Label(self.picker_frame, text="Quick action:").pack(side="left", padx=(0, 6))
        for label, prompt in [
            ("Summarize", "Summarize the quoted text above as 3 bullet points."),
            ("Explain", "Explain the quoted text above in plain English. Call out one non-obvious edge case if any."),
            ("Improve", "Rewrite the quoted text above to be clearer and more concise. Preserve meaning."),
        ]:
            ttk.Button(
                self.picker_frame, text=label,
                command=lambda p=prompt: self._picker_send(p),
            ).pack(side="left", padx=2)
        ttk.Button(
            self.picker_frame, text="Ask…",
            command=self._picker_focus,
        ).pack(side="left", padx=(8, 0))
        if not self.picker_visible:
            self.picker_frame.pack(fill="x", pady=(4, 0), before=self.input)
            self.picker_visible = True

    def _picker_send(self, prompt: str) -> None:
        # Compose: action prompt + the original ingested selection as a quoted block.
        ingested = getattr(self, "_ingested_text", "") or ""
        quoted = "\n".join("> " + ln for ln in ingested.splitlines() or [""])
        full = f"{prompt}\n\n{quoted}" if ingested else prompt
        self.input.delete("1.0", "end")
        self.input.insert("1.0", full)
        self.on_send()
        self._hide_picker()

    def _picker_focus(self) -> None:
        self.focus_input()
        self._hide_picker()

    def _hide_picker(self) -> None:
        if self.picker_visible:
            try:
                self.picker_frame.pack_forget()
            except tk.TclError:
                pass
            self.picker_visible = False

    def focus_input(self) -> None:
        try:
            self.input.focus_set()
        except tk.TclError:
            pass

    def _on_enter(self, _event):
        self.on_send()
        return "break"

    def on_send(self) -> None:
        if self.inflight:
            return
        self.app.reload_runtime_config()
        text = self.input.get("1.0", "end").strip()
        if not text:
            return
        self.input.delete("1.0", "end")
        self._append("user", f"You: {text}\n")
        self.history.append({"role": "user", "content": text})
        if self.title == "New chat":
            self.title = (text[:TITLE_MAX_CHARS] + "…") if len(text) > TITLE_MAX_CHARS else text
            self.app.rename_tab(self, self.title)
        self._set_busy(True)
        threading.Thread(target=self._worker, args=(list(self.history),), daemon=True).start()

    def _worker(self, history_snapshot: list[dict]) -> None:
        messages: list[dict] = []
        if self.app.system_prompt:
            messages.append({"role": "system", "content": self.app.system_prompt})
        if self.notes_mode.get() and history_snapshot:
            query = str(history_snapshot[-1].get("content") or "")
            context, titles = retrieve_notes_context(query)
            if context:
                messages.append({"role": "system", "content": context})
                self.app.root.after(0, self._append, "meta",
                                    "📚 grounded on: " + ", ".join(titles) + "\n")
            else:
                self.app.root.after(0, self._append, "meta",
                                    "📚 no matching notes — answering without vault context\n")
        # Sliding window: keep last N turn-pairs (one turn = user + assistant).
        # Prevents prompt growth that makes later turns increasingly slow.
        n_turns = self.app.context_window_turns
        if n_turns > 0:
            messages.extend(history_snapshot[-(n_turns * 2):])
        else:
            messages.extend(history_snapshot)
        try:
            reply = self.app.client.chat(messages)
            self.app.root.after(0, self._on_reply, reply, None)
        except Exception as e:
            self.app.root.after(0, self._on_reply, None, str(e))

    def _on_reply(self, reply: str | None, err: str | None) -> None:
        if err:
            self._append("error", f"[error] {err}\n")
        else:
            self.history.append({"role": "assistant", "content": reply or ""})
            self._append("assistant", f"LLM: {reply}\n")
            self.app.persist()
        self._set_busy(False)
        self.focus_input()

    def on_clear(self) -> None:
        """Reset this tab to a fresh context. Same thread_id (so persisted
        snapshot is overwritten on next save) but empty history."""
        self.history.clear()
        self.title = "New chat"
        self.app.rename_tab(self, self.title)
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.configure(state="disabled")
        self._append("meta", "— thread cleared —\n")
        self.app.persist()

    def _append(self, tag: str, text: str) -> None:
        self.transcript.configure(state="normal")
        self.transcript.insert("end", text, tag)
        self.transcript.see("end")
        self.transcript.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        self.inflight = busy
        self.send_btn.configure(state=("disabled" if busy else "normal"),
                                text=("…thinking" if busy else "Send  (Enter)"))

    def to_record(self) -> dict:
        return {
            "thread_id": self.thread_id,
            "title": self.title,
            "updated_at": _now_iso(),
            "history": list(self.history),
        }


# ---------- App ---------------------------------------------------------------------

class ChatApp:
    """Top-level window owning the Notebook, the LLM client, and the
    single-instance listener."""

    def __init__(self, cfg: dict, instance_sock: socket.socket):
        self.cfg = cfg
        self.client = LLMClient(cfg)
        self.system_prompt = str(cfg.get("system_prompt") or "")
        self.context_window_turns = max(0, int(cfg.get("context_window_turns") or 0))
        self.instance_sock = instance_sock
        self.ingest_nonce = _ensure_ingest_nonce()
        self._accept_pause = threading.Event()
        self.show_q: queue.Queue[str] = queue.Queue()
        self.tabs: list[ConversationTab] = []

        win = cfg.get("window") or {}
        self.root = tk.Tk()
        self.root.title(str(win.get("title", "Local LLM Chat")))
        w, h = int(win.get("width", 640)), int(win.get("height", 600))
        self.root.geometry(f"{w}x{h}")
        self.root.minsize(420, 360)
        if win.get("topmost", True):
            self.root.attributes("-topmost", True)
        # X closes the chat process so reopening always picks up the active model.
        self.root.protocol("WM_DELETE_WINDOW", self.on_quit)

        self._build_ui()
        self._bind_keys()
        self._restore_threads()
        self._start_instance_listener()
        self._poll_show_queue()

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=(8, 8, 8, 0))
        top.pack(fill="x")
        self.status_label = ttk.Label(
            top,
            text=f"model: {self.client.model} @ {self.client.base_url}",
            foreground="#666",
        )
        self.status_label.pack(side="left")
        ttk.Button(top, text="× Close tab", command=self.close_current_tab).pack(side="right", padx=(4, 0))
        ttk.Button(top, text="History…", command=self.open_history_picker).pack(side="right", padx=(4, 0))
        ttk.Button(top, text="+ New chat", command=self.new_tab).pack(side="right")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=8)
        self.notebook.enable_traversal()
        self.notebook.bind("<<NotebookTabChanged>>", lambda e: self._focus_current())

    def _bind_keys(self) -> None:
        # bind_all (not bind) so the keystroke fires even when focus is inside
        # a Text widget — Text has built-in Ctrl-letter bindings that would
        # otherwise consume the event. Handlers return "break" to stop the
        # Text widget from also processing the key.
        def wrap(fn):
            def handler(_event):
                fn()
                return "break"
            return handler

        self.root.bind_all("<Escape>", wrap(self.on_hide))
        self.root.bind_all("<Control-q>", wrap(self.on_quit))
        self.root.bind_all("<Control-Q>", wrap(self.on_quit))
        self.root.bind_all("<Control-t>", wrap(self.new_tab))
        self.root.bind_all("<Control-T>", wrap(self.new_tab))
        self.root.bind_all("<Control-w>", wrap(self.close_current_tab))
        self.root.bind_all("<Control-W>", wrap(self.close_current_tab))
        self.root.bind_all("<Control-Tab>", wrap(lambda: self._cycle_tab(1)))
        self.root.bind_all("<Control-Shift-Tab>", wrap(lambda: self._cycle_tab(-1)))
        # Some Windows Tk builds report shift-tab as ISO_Left_Tab.
        self.root.bind_all("<Control-ISO_Left_Tab>", wrap(lambda: self._cycle_tab(-1)))

    def _cycle_tab(self, delta: int) -> None:
        if not self.tabs:
            return
        count = self.notebook.index("end")
        if count <= 0:
            return
        current_idx = self.notebook.index(self.notebook.select())
        self.notebook.select((current_idx + delta) % count)

    def _restore_threads(self) -> None:
        # Always start with a fresh tab. Past threads remain on disk and are
        # reachable via the "History…" picker.
        self.new_tab()

    def _add_tab(self, tab: ConversationTab) -> None:
        self.tabs.append(tab)
        self.notebook.add(tab.frame, text=tab.title)

    def new_tab(self) -> None:
        tab = ConversationTab(self, None)
        self._add_tab(tab)
        self.notebook.select(self.notebook.index("end") - 1)
        tab.focus_input()

    def close_current_tab(self) -> None:
        if not self.tabs:
            return
        idx = self.notebook.index(self.notebook.select())
        tab = self.tabs.pop(idx)
        self.notebook.forget(idx)
        tab.frame.destroy()
        if not self.tabs:
            self.new_tab()

    def rename_tab(self, tab: ConversationTab, title: str) -> None:
        try:
            idx = self.tabs.index(tab)
        except ValueError:
            return
        self.notebook.tab(idx, text=title)

    def persist(self) -> None:
        """Merge currently-open tabs with what's on disk so threads not
        currently loaded in a tab are preserved."""
        merged: dict[str, dict] = {}
        for t in load_threads():
            tid = t.get("thread_id")
            if tid:
                merged[tid] = t
        for tab in self.tabs:
            # Skip empty new-chat tabs the user never typed into.
            if not tab.history:
                continue
            rec = tab.to_record()
            merged[rec["thread_id"]] = rec
        ordered = sorted(merged.values(), key=lambda r: str(r.get("updated_at") or ""), reverse=True)
        save_threads(ordered)

    def open_history_picker(self) -> None:
        threads = load_threads()
        # Hide any thread already open in a tab to avoid duplicate opens.
        open_ids = {t.thread_id for t in self.tabs}
        available = [t for t in threads if t.get("thread_id") not in open_ids]

        dlg = tk.Toplevel(self.root)
        dlg.title("Chat history")
        dlg.transient(self.root)
        dlg.geometry("520x380")
        try:
            dlg.attributes("-topmost", True)
        except tk.TclError:
            pass

        ttk.Label(dlg, text=f"{len(available)} saved thread(s). Double-click or Open to reopen.",
                  foreground="#666").pack(fill="x", padx=10, pady=(10, 4))

        listframe = ttk.Frame(dlg)
        listframe.pack(fill="both", expand=True, padx=10, pady=4)
        scrollbar = ttk.Scrollbar(listframe, orient="vertical")
        listbox = tk.Listbox(listframe, yscrollcommand=scrollbar.set, activestyle="dotbox",
                             font=("Segoe UI", 10))
        scrollbar.config(command=listbox.yview)
        scrollbar.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)
        for t in available:
            updated = str(t.get("updated_at") or "")[:19]
            title = str(t.get("title") or "(untitled)")
            msgs = len(t.get("history") or [])
            listbox.insert("end", f"{updated}  •  {title}  ({msgs} msg)")

        def reopen():
            sel = listbox.curselection()
            if not sel:
                return
            tab = ConversationTab(self, available[sel[0]])
            self._add_tab(tab)
            self.notebook.select(self.notebook.index("end") - 1)
            tab.focus_input()
            dlg.destroy()

        def delete():
            sel = listbox.curselection()
            if not sel:
                return
            removed = available.pop(sel[0])
            listbox.delete(sel[0])
            remaining = [r for r in load_threads() if r.get("thread_id") != removed.get("thread_id")]
            save_threads(remaining)

        bar = ttk.Frame(dlg)
        bar.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(bar, text="Open", command=reopen).pack(side="right", padx=(4, 0))
        ttk.Button(bar, text="Delete", command=delete).pack(side="right", padx=(4, 0))
        ttk.Button(bar, text="Close", command=dlg.destroy).pack(side="right")

        listbox.bind("<Double-Button-1>", lambda e: reopen())
        listbox.bind("<Return>", lambda e: reopen())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        if available:
            listbox.selection_set(0)
            listbox.focus_set()

    def _focus_current(self) -> None:
        try:
            idx = self.notebook.index(self.notebook.select())
            self.tabs[idx].focus_input()
        except (tk.TclError, IndexError):
            pass

    def on_hide(self) -> None:
        try:
            self.root.withdraw()
        except tk.TclError:
            pass

    def on_quit(self) -> None:
        self.persist()
        try:
            self.instance_sock.close()
        except Exception:
            pass
        self.root.destroy()

    def _apply_runtime_config(self, cfg: dict) -> None:
        self.cfg = cfg
        self.client = LLMClient(cfg)
        self.system_prompt = str(cfg.get("system_prompt") or "")
        self.context_window_turns = max(0, int(cfg.get("context_window_turns") or 0))
        if hasattr(self, "status_label"):
            self.status_label.configure(
                text=f"model: {self.client.model} @ {self.client.base_url}"
            )

    def reload_runtime_config(self, *, min_interval: float = 0.0) -> None:
        """Re-read shared config and refresh the status bar + LLM client."""
        now = time.monotonic()
        last = getattr(self, "_last_cfg_reload", 0.0)
        if min_interval > 0 and (now - last) < min_interval:
            return
        self._last_cfg_reload = now
        self._apply_runtime_config(load_config())

    def show(self) -> None:
        self.reload_runtime_config()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._focus_current()

    def _start_instance_listener(self) -> None:
        def loop():
            while True:
                try:
                    conn, _ = self.instance_sock.accept()
                except BlockingIOError:
                    self._accept_pause.wait(0.15)
                    continue
                except OSError:
                    return
                try:
                    # Read up to 64 KiB so an ingest payload with a large
                    # selection fits comfortably. Legacy "SHOW\n" still works.
                    raw = conn.recv(65536)
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass
                self._dispatch_message(raw)
        threading.Thread(target=loop, daemon=True).start()

    def _dispatch_message(self, raw: bytes) -> None:
        """Parse wire messages: JSON ingest payloads, or line-based RELOAD/SHOW."""
        text = (raw or b"").strip().decode("utf-8", errors="replace")
        if not text:
            return
        if text.startswith("{"):
            try:
                msg = json.loads(text)
            except Exception:
                msg = None
            if isinstance(msg, dict) and msg.get("type") == "ingest":
                if str(msg.get("nonce") or "") != self.ingest_nonce:
                    log.warning("rejected ingest: nonce mismatch")
                    return
                self.show_q.put({
                    "type": "ingest",
                    "text": str(msg.get("text") or ""),
                    "source_app": str(msg.get("source_app") or ""),
                })
                return
        handled = False
        for line in text.splitlines():
            cmd = line.strip().upper()
            if cmd == "RELOAD":
                self.show_q.put("reload")
                handled = True
            elif cmd == "QUIT":
                self.show_q.put("quit")
                handled = True
            elif cmd == "SHOW":
                self.show_q.put("show")
                handled = True
        if not handled:
            self.show_q.put("show")

    def _poll_show_queue(self) -> None:
        self.reload_runtime_config(min_interval=2.0)
        try:
            while True:
                item = self.show_q.get_nowait()
                if isinstance(item, dict) and item.get("type") == "ingest":
                    self._handle_ingest(item.get("text", ""), item.get("source_app", ""))
                elif item == "quit":
                    self.on_quit()
                elif item == "reload":
                    self.reload_runtime_config()
                elif item == "show":
                    self.show()
                else:
                    self.show()
        except queue.Empty:
            pass
        self.root.after(150, self._poll_show_queue)

    def _handle_ingest(self, text: str, source_app: str) -> None:
        """Open a fresh tab with the ingested selection + action picker."""
        self.new_tab()
        current = self._current_tab()
        if current is not None:
            current.ingest_selection(text, source_app)
        self.show()

    def _current_tab(self) -> ConversationTab | None:
        idx = self.notebook.index("current") if self.notebook.tabs() else None
        if idx is None or idx < 0 or idx >= len(self.tabs):
            return None
        return self.tabs[idx]

    def run(self) -> None:
        self.root.mainloop()


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return str(pid) in ((result.stdout or "") + (result.stderr or ""))


def _watch_parent_pid(parent_pid: int, app: ChatApp) -> None:
    """Exit chat when the launching grammarFix.ahk process goes away."""
    if parent_pid <= 0:
        return

    def loop() -> None:
        while True:
            time.sleep(5)
            if not _is_pid_alive(parent_pid):
                try:
                    app.root.after(0, app.on_quit)
                except Exception:
                    pass
                return

    threading.Thread(target=loop, daemon=True, name="chat-parent-watch").start()


def main() -> int:
    parser = argparse.ArgumentParser(description="Flowkey local LLM chat popup")
    parser.add_argument("--parent-pid", type=int, default=0,
                        help="exit when this PID disappears (grammarFix.ahk)")
    args = parser.parse_args()

    cfg = load_config()
    port = int((cfg.get("window") or {}).get("single_instance_port", 52640))

    sock = try_acquire_single_instance(port)
    if sock is None:
        ping_existing_instance(port)
        return 0

    app = ChatApp(cfg, sock)
    _watch_parent_pid(args.parent_pid, app)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
