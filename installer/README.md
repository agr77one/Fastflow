# installer/

Everything needed to turn this project into a signed Windows installer that non-developers can double-click.

## Files

```
installer/
├── build.ps1              ← end-to-end orchestrator (run this)
├── fastflowprompt.spec    ← PyInstaller spec (3 exes, onedir, MERGE-dedup)
├── installer.iss          ← Inno Setup 6.x script (per-machine, admin)
├── sign.ps1               ← cert generation + signtool wrapper
├── certs/                 ← .pfx and .cer (gitignored)
└── README.md              ← this file
```

Sibling folders consumed by these scripts:

```
./
├── scripts/               ← Python + AHK source
├── setup/defaults/        ← seed config shipped read-only
├── vendor/ahk/            ← AutoHotkey v2 portable (downloaded)
├── vendor/flm/            ← flm-setup.exe (downloaded)
├── dist/FastFlowPrompt/   ← PyInstaller output (build artifact)
└── out/                   ← signed installer .exe (build artifact)
```

## Prerequisites

Easiest path: run the bootstrap script. It detects what's missing and
installs only those via `winget` (built into Windows 10 1809+ and Windows 11):

```powershell
.\installer\bootstrap.ps1
# Or in one shot — install prereqs AND build the installer:
.\installer\bootstrap.ps1 -Build
```

Manual prerequisites (only needed if `winget` isn't available):

- Windows 10/11 x64
- Python 3.11+ with `pyinstaller` (`pip install pyinstaller`)
- [Inno Setup 6.x](https://jrsoftware.org/isdl.php) — `iscc.exe` on PATH
- Windows 10/11 SDK (for `signtool.exe`) — only if signing

## One-time setup: generate the signing cert

```powershell
cd installer
$env:FFP_SIGN_PFX_PASSWORD = "ChangeMe!"
.\sign.ps1 -GenerateCert
```

This drops `certs\fastflowprompt.pfx` (private) and `certs\fastflowprompt.cer`
(public). The `.pfx` is gitignored — never commit it. The `.cer` is what end
users can optionally import into their Trusted Publishers store to silence
SmartScreen on first launch.

## Building a release

```powershell
# From anywhere — build.ps1 self-anchors to the project root
.\installer\build.ps1 -BundleAhk -BundleFlm -Sign
```

Steps the script runs:

1. Read `scripts\_version.py` → derive version (e.g. `2.1.0`)
2. Generate `file_version_info.txt` for the Win32 VERSIONINFO resource
3. Download `vendor\ahk\AutoHotkey64.exe` if missing (`-BundleAhk`)
4. Download `vendor\flm\flm-setup.exe` if missing (`-BundleFlm`)
5. Run `pyinstaller --clean --noconfirm fastflowprompt.spec` → `dist\FastFlowPrompt\`
6. Run `iscc installer.iss` → `out\Flowkey-Setup-<version>.exe`
7. Run `sign.ps1` against the output (`-Sign`)

Debug flags:

- `-SkipPyInstaller` — reuse the existing `dist\` tree
- `-SkipInno` — stop after PyInstaller

## What the installer does (per-machine, admin)

1. Installs the PyInstaller bundle, AHK runtime, and AHK source scripts to
   `C:\Program Files\FastFlowPrompt\` (read-only).
2. Chain-installs FastFlowLM by running the vendored `flm-setup.exe` silently —
   only if FLM isn't already on the machine. Drops a marker so the uninstaller
   knows whether to chain-remove FLM later.
3. (Optional task) Creates a per-machine HKLM `Run` entry so AHK starts on
   every login for every user. Cleaned up on uninstall.
4. (Optional task) Creates a desktop shortcut.
5. Offers to launch the first-run wizard (`ffp-first-run.exe`) on completion.

## What the uninstaller does

1. Kills `ffp-daemon.exe` and the AHK process running
   `grammarFix.ahk` so file removal doesn't fail on in-use binaries.
2. Chain-uninstalls FastFlowLM via its `QuietUninstallString` — but only if
   we set the `.flm_installed_by_us` marker. Users who already had FLM keep
   it.
3. Asks (default = No) whether to wipe per-user data at
   `%LOCALAPPDATA%\FastFlowPrompt\`. The user can decline and keep their
   notes / config / logs across reinstalls.
4. Removes the per-user `HKCU\...\Run\FastFlowPrompt` autostart entry, if one was set via the dashboard's "Launch Flowkey when I sign in" toggle. The installer itself never sets machine-wide (HKLM) autostart — that's a single per-user entry the daemon owns.

## End-user SmartScreen note

Because the installer is self-signed, end users will see Windows Defender
SmartScreen on first launch:

> Windows protected your PC → More info → Run anyway

To silence this permanently, ship them the `.cer` alongside the installer and
have them run (admin PowerShell):

```powershell
Import-Certificate -FilePath fastflowprompt.cer -CertStoreLocation Cert:\LocalMachine\TrustedPublisher
```

For a real-world public release, swap the self-signed cert for an OV (~$200/yr)
or EV (~$400/yr) code-signing certificate. `sign.ps1` accepts any `.pfx` via
the `-PfxPath` parameter — no other changes needed.

## Layout produced on a clean install

```
C:\Program Files\FastFlowPrompt\           (read-only, admin-installed)
├── Flowkey\                        PyInstaller bundle
│   ├── ffp-daemon.exe
│   ├── ffp-grammar-fix.exe
│   ├── ffp-first-run.exe
│   ├── _internal\
│   └── setup\defaults\
├── ahk\
│   └── AutoHotkey64.exe
└── scripts\
    ├── grammarFix.ahk
    ├── lib\
    └── ui\

%LOCALAPPDATA%\FastFlowPrompt\             (per-user, writable)
├── config\
├── data\
└── logs\

C:\Program Files\FastFlowLM\               (FLM, separately installed by chain)
└── flm.exe, *.dll, model_list.json, ...
```

End users never need to know any of this layout — they download one `.exe`,
double-click, accept the SmartScreen prompt, finish the wizard, and the hotkeys
are live globally.
