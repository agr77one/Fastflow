# vendor/

Third-party runtimes shipped inside the installer. None of the binaries belong
in source control — `installer/build.ps1` downloads them on demand.

## Contents

| dir        | what                                  | source                                                                  | license      |
|------------|---------------------------------------|-------------------------------------------------------------------------|--------------|
| `ahk/`     | AutoHotkey v2 64-bit interpreter      | <https://www.autohotkey.com/download/ahk-v2.zip>                        | GPLv2        |
| `flm/`     | FastFlowLM official setup wrapper     | <https://github.com/FastFlowLM/FastFlowLM/releases/latest>              | see FLM site |

## Refresh

```powershell
# Vendor or refresh both runtimes, from the repository root
.\installer\build.ps1 -BundleAhk -BundleFlm
```

The build script is idempotent — it skips a download if the file is already
present. Delete `vendor/ahk/AutoHotkey64.exe` or `vendor/flm/flm-setup.msi`
to force a fresh pull (e.g. when picking up a new upstream version).

## Pinning

The installer is *not* pinned to a specific AHK or FLM version — each build
fetches whatever `latest` resolves to upstream. If you need reproducible
builds, drop the binaries in by hand and skip the flags.
