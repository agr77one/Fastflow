# CI workflows

## `release-installer.yml`

Builds the signed Windows installer on every `v*` tag push (and on manual
`workflow_dispatch` runs). Uploads the `.exe` to the GitHub Release.

### One-time secret setup

The job needs two repository secrets to produce a signed installer:

1. **`FFP_SIGN_PFX_B64`** — your code-signing cert as a base64 string.

   ```powershell
   # On the machine that holds the .pfx:
   [Convert]::ToBase64String(
     [IO.File]::ReadAllBytes("release\installer\certs\fastflowprompt.pfx")
   ) | Set-Clipboard
   ```

   Paste into _Settings → Secrets and variables → Actions → New secret_.

2. **`FFP_SIGN_PFX_PASSWORD`** — the password that protects the `.pfx`.

If either secret is missing, the workflow still runs but produces an
**unsigned** installer (SmartScreen will refuse to launch it for most users —
useful for internal builds only).

### How to release

```powershell
git tag v1.4.0
git push origin v1.4.0
```

The workflow does the rest. A draft release appears under
_Releases_ once `softprops/action-gh-release` completes; flip `draft: false`
in the YAML if you want it auto-published (current default).

### Local dry run

To exercise the same build locally without pushing a tag:

```powershell
.\release\installer\build.ps1 -BundleAhk -BundleFlm -Sign
```

Same script the CI calls. If it works locally it'll work in CI.
