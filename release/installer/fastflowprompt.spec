# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Flowkey v1.4.0.

Build:

    pip install pyinstaller
    pyinstaller --clean --noconfirm fastflowprompt.spec

Output: dist/FastFlowPrompt/  (onedir, ~25 MB after merge dedupe)

Produces four executables sharing one runtime tree:

    dist/FastFlowPrompt/
    ├── ffp-daemon.exe        windowed, long-running action server
    ├── ffp-grammar-fix.exe   console, AHK subprocess fallback
    ├── ffp-chat.exe          windowed, chat popup
    ├── ffp-first-run.exe     windowed, first-run wizard
    ├── _internal/            shared Python runtime + dlls + py-modules
    └── setup/defaults/       seed config (read-only)

Design:

- onedir (not onefile) — faster cold start, fewer AV false positives.
- console=False everywhere except ffp-grammar-fix.exe (AHK reads stdout).
- All flat py-modules from pyproject.toml are listed as hiddenimports.
- MERGE() across the four Analysis objects so shared deps land once on disk.
- setup/defaults/ ships as datas so paths.CONFIG_SEED_FILE resolves at
  {APP_DIR}/setup/defaults/grammar_hotkey.config.json at runtime.
"""

import os

block_cipher = None

# SPECPATH is provided by PyInstaller and points at the dir containing this
# spec file (release/installer/). Anchor all paths to release/ root (one
# level up) so the spec works regardless of pyinstaller's CWD.
_RELEASE_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SCRIPTS_DIR = os.path.join(_RELEASE_ROOT, "scripts")
_icon_candidate = os.path.join(_RELEASE_ROOT, "setup", "logo.ico")
ICON_PATH = _icon_candidate if os.path.exists(_icon_candidate) else None
VERSION_FILE = None                  # set to "file_version_info.txt" once generated

HIDDEN_IMPORTS = [
    "_version",
    "ffp_config",
    "ffp_flm_server",
    "ffp_llm_client",
    "ffp_telemetry",
    "ffp_updater",
    "loopback_http",
    "paths",
    "grammar_fix",
    "chat_popup",
    "ffp_daemon",
    "first_run",
    "install",
    "notes",
    "subprocess_util",
]

DATAS = [
    (os.path.join(_RELEASE_ROOT, "setup", "defaults"), "setup/defaults"),
]

EXCLUDES = [
    "tkinter", "test", "unittest", "pydoc", "doctest",
    "lib2to3", "pip", "setuptools", "wheel",
]


def _analysis(script_name: str) -> "Analysis":
    return Analysis(
        [os.path.join(SCRIPTS_DIR, script_name)],
        pathex=[SCRIPTS_DIR],
        binaries=[],
        datas=DATAS,
        hiddenimports=HIDDEN_IMPORTS,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=EXCLUDES,
        win_no_prefer_redirects=False,
        win_private_assemblies=False,
        cipher=block_cipher,
        noarchive=False,
    )


a_daemon  = _analysis("ffp_daemon.py")
a_grammar = _analysis("grammar_fix.py")
a_chat    = _analysis("chat_popup.py")
a_wizard  = _analysis("first_run.py")

# Dedupe shared deps across all four bundles.
MERGE(
    (a_daemon,  "ffp_daemon",  "ffp-daemon"),
    (a_grammar, "grammar_fix", "ffp-grammar-fix"),
    (a_chat,    "chat_popup",  "ffp-chat"),
    (a_wizard,  "first_run",   "ffp-first-run"),
)

pyz_daemon  = PYZ(a_daemon.pure,  a_daemon.zipped_data,  cipher=block_cipher)
pyz_grammar = PYZ(a_grammar.pure, a_grammar.zipped_data, cipher=block_cipher)
pyz_chat    = PYZ(a_chat.pure,    a_chat.zipped_data,    cipher=block_cipher)
pyz_wizard  = PYZ(a_wizard.pure,  a_wizard.zipped_data,  cipher=block_cipher)


def _exe(pyz, analysis, name, console):
    return EXE(
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,                  # UPX trips Defender — leave off
        console=console,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON_PATH,
        version=VERSION_FILE,
    )


exe_daemon  = _exe(pyz_daemon,  a_daemon,  "ffp-daemon",      console=False)
exe_grammar = _exe(pyz_grammar, a_grammar, "ffp-grammar-fix", console=True)
exe_chat    = _exe(pyz_chat,    a_chat,    "ffp-chat",        console=False)
exe_wizard  = _exe(pyz_wizard,  a_wizard,  "ffp-first-run",   console=False)

COLLECT(
    exe_daemon,  a_daemon.binaries,  a_daemon.zipfiles,  a_daemon.datas,
    exe_grammar, a_grammar.binaries, a_grammar.zipfiles, a_grammar.datas,
    exe_chat,    a_chat.binaries,    a_chat.zipfiles,    a_chat.datas,
    exe_wizard,  a_wizard.binaries,  a_wizard.zipfiles,  a_wizard.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FastFlowPrompt",
)
