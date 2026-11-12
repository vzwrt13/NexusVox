# Signing a release

The Windows installer (`NexusVox-Setup.exe`) and the `NexusVox.exe` inside it are signed with the company's
**Certum Standard Code Signing in the Cloud** certificate (organisation validated, SimplySign) and carry an RFC 3161
timestamp, so the signatures stay valid after the certificate expires.

Signing happens **on the maintainer's PC, not in CI**: SimplySign asks for a one-time code from a phone at every
login, and the private key never leaves Certum. CI builds the app and an unsigned installer; the signed installer
replaces it on the release page.

**Never print, read, copy or commit certificate secrets, PINs or QR codes.** The script picks the certificate by its
public thumbprint and passes nothing secret on a command line.

## One-time setup

- SimplySign Desktop installed and set up with your Certum account.
- The Windows SDK signing tools (`signtool.exe`, found automatically under `Windows Kits\10\bin\*\x64`; or set `SIGNTOOL`).
- [Inno Setup](https://jrsoftware.org/isinfo.php) 6 (the version in `INNO_SETUP_VERSION` of `.github/workflows/installer.yml`).
- A Python environment with NexusVox installed **as a regular package** (`pip install .`, not editable) and PyInstaller.
  To get the same stub as CI, build PyInstaller's bootloader from source:
  `$env:PYINSTALLER_COMPILE_BOOTLOADER = "1"; pip install --no-binary pyinstaller "pyinstaller>=6,<7"` (needs the
  Visual Studio C++ build tools; without the variable the source package installs the shared prebuilt stub).
- Optional: set `NEXUSVOX_SIGN_THUMBPRINT` to the certificate's SHA-1 thumbprint (public data; SimplySign Desktop or
  `Get-ChildItem Cert:\CurrentUser\My` shows it). Without it the script uses the only code-signing certificate in the
  store and asks for `-Thumbprint` if there are several.

## Release steps

Do the normal release first ([.github/CLAUDE.md](../.github/CLAUDE.md#releasing)): version bump, pull request, annotated
tag. The tag run publishes the release with an **unsigned** `NexusVox-Setup.exe`. Then, on your PC, from a checkout of
the tag:

1. **Log in to SimplySign Desktop** (phone one-time code). The certificate now shows up as a virtual smart card.
2. Build the app: `python packaging/build.py --clean`
3. Run the script:

   ```powershell
   powershell -File packaging\sign_release.ps1
   ```

   It signs `dist\NexusVox\NexusVox.exe`, builds the installer from the signed exe (`packaging/build_installer.py`),
   signs `dist\installer\NexusVox-Setup.exe` and runs `signtool verify /pa /v` on both. Parameters: `-Thumbprint`,
   `-TimestampUrl` (default `http://time.certum.pl`), `-SignTool`, `-Python`; `Get-Help .\packaging\sign_release.ps1`
   lists the rest.
4. Check the result yourself:

   ```powershell
   signtool verify /pa /v dist\installer\NexusVox-Setup.exe
   signtool verify /pa /v dist\NexusVox\NexusVox.exe
   ```

   Both must end with `Successfully verified`, show **NightShift AI GmbH** as the signer and list a timestamp.
5. Replace the unsigned installer on the release (same file name, the website links to it):

   ```powershell
   gh release upload v1.0.0 dist\installer\NexusVox-Setup.exe --clobber
   ```

6. Log out of SimplySign Desktop.

### Test the script without the real certificate

`powershell -File packaging\test_sign_release.ps1` creates a throw-away self-signed certificate in memory, runs the
whole flow on stand-in files and checks the order (exe signed, installer built, installer signed). It never touches
SimplySign, the certificate store or `dist\`. `pytest tests/test_installer_packaging.py` runs it on Windows when
`signtool` is installed. Use only that certificate for experiments with the script.

## VirusTotal

Upload the signed installer to VirusTotal **only after the maintainer agrees**: an upload shares the file with
antivirus vendors and cannot be undone. When the maintainer agrees, put the report link and the number of engines
that flag the file into the release notes (`release-notes/v<version>.md`), and report a false positive to the
vendor that flags it. Do not upload other builds under the same version.

## If the certificate is not ready: ship unsigned

An unsigned 1.0 is acceptable; a delayed 1.0 for a certificate is not. Skip steps 1 to 5, publish the installer CI
built, and say so in the release notes: *"This installer is not signed yet. Windows SmartScreen shows it as from an
unknown publisher."* Sign from the next release on.

## What users see: SmartScreen

A valid signature gives the SmartScreen and UAC dialogs a verified publisher name. It does **not** give instant
SmartScreen reputation: reputation builds with downloads of the same file, so a fresh release can still show the blue
"Windows protected your PC" dialog, signed or not. The user-facing steps are in the
[README](../README.md#windows-installer); in short: click **More info**, then **Run anyway**. Never change the file
name (`NexusVox-Setup.exe`, no version) and do not re-upload different builds under one version: reputation is tracked
per file.
