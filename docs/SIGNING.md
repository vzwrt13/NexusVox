# Signing a release

The Windows installer (`NexusVox-Setup.exe`), the uninstaller inside it (`unins000.exe`) and the `NexusVox.exe` it
installs are signed with the company's **Certum Standard Code Signing in the Cloud** certificate (organisation
validated, SimplySign) and carry an RFC 3161 timestamp, so the signatures stay valid after the certificate expires.

Signing happens **on the maintainer's PC, not in CI**: SimplySign asks for a one-time code from a phone at every
login, and the private key never leaves Certum. A tag run creates a **draft** release with the installer CI built
(unsigned); the signed installer built on your PC replaces it, and only then is the draft published. Nobody can download
the unsigned file in between.

**Never print, read, copy or commit certificate secrets, PINs or QR codes.** The script picks the certificate by its
public thumbprint and passes nothing secret on a command line.

## One-time setup

- SimplySign Desktop installed and set up with your Certum account.
- The Windows SDK signing tools (`signtool.exe`, found automatically under `Windows Kits\10\bin\*\x64`; or set `SIGNTOOL`).
- [Inno Setup](https://jrsoftware.org/isinfo.php) 6, the version in `INNO_SETUP_VERSION` of `.github/workflows/installer.yml`.
- Visual Studio C++ build tools, and a Python environment with NexusVox installed **as a regular package**
  (`pip install .`, not editable) and PyInstaller with a bootloader built from source, as in CI:

  ```powershell
  $env:PYINSTALLER_COMPILE_BOOTLOADER = "1"
  pip install --no-cache-dir --force-reinstall --no-binary pyinstaller "pyinstaller>=6,<7"
  python packaging\check_bootloader.py
  ```

  `--no-cache-dir` matters: pip otherwise reuses a cached PyInstaller wheel, which carries the shared prebuilt
  bootloader that antivirus engines flag. `check_bootloader.py` compares the installed bootloaders with the ones PyPI
  publishes and exits non-zero when they are identical. Run it again after any PyInstaller reinstall.
- Optional: set `NEXUSVOX_SIGN_THUMBPRINT` to the certificate's SHA-1 thumbprint (public data; SimplySign Desktop or
  `Get-ChildItem Cert:\CurrentUser\My` shows it; spaces and the invisible characters the Windows dialog adds are
  ignored). Without it the script uses the only code-signing certificate in the store and asks for `-Thumbprint` if
  there are several.

## Release steps

Do the normal release first ([.github/CLAUDE.md](../.github/CLAUDE.md#releasing)): version bump, pull request, annotated
tag. The tag run creates the draft release. Then, on your PC, from a checkout of the tag:

1. **Log in to SimplySign Desktop** (phone one-time code). The certificate now shows up as a virtual smart card.
2. `python packaging\check_bootloader.py`, then build the app: `python packaging\build.py --clean`
3. Run the script:

   ```powershell
   powershell -File packaging\sign_release.ps1
   ```

   It signs `dist\NexusVox\NexusVox.exe`, builds the installer from the signed exe (`packaging/build_installer.py`;
   Inno Setup signs the embedded uninstaller and the setup with the same `signtool` command), signs
   `dist\installer\NexusVox-Setup.exe` again so the result never depends on the build, and runs `signtool verify /pa /v`
   on both files. Parameters: `-Thumbprint`, `-TimestampUrl` (default `http://time.certum.pl`), `-SignTool`, `-Python`;
   `Get-Help .\packaging\sign_release.ps1` lists the rest.
4. Check the result yourself:

   ```powershell
   signtool verify /pa /v dist\installer\NexusVox-Setup.exe
   signtool verify /pa /v dist\NexusVox\NexusVox.exe
   ```

   Both must end with `Successfully verified`, show **NightShift AI GmbH** as the signer and list a timestamp.
5. **Install it once.** The shipped installer is the one built on your PC, not the one CI built and tested. Install
   `dist\installer\NexusVox-Setup.exe` over your current copy, start NexusVox, then check the Digital Signatures tab of
   `unins000.exe` in the install folder. If anything differs from CI's tested installer, stop here.
6. Replace the unsigned installer on the draft (same file name, the website links to it) and publish it:

   ```powershell
   gh release upload v1.0.0 dist\installer\NexusVox-Setup.exe --clobber
   gh release edit v1.0.0 --draft=false
   ```

7. Log out of SimplySign Desktop.

### Test the script without the real certificate

`powershell -File packaging\test_sign_release.ps1` creates a throw-away self-signed certificate in memory, runs the
whole flow on stand-in files and checks the order (exe signed, installer built, installer signed) and the guards. It
never touches SimplySign, the certificate store or `dist\`. `pytest tests/test_installer_packaging.py` runs it on Windows
when `signtool` is installed. Use only that certificate for experiments with the script.

## VirusTotal

Upload the signed installer to VirusTotal **only after the maintainer agrees**: an upload shares the file with
antivirus vendors and cannot be undone. When the maintainer agrees, put the report link and the number of engines
that flag the file into the release notes (`release-notes/v<version>.md`), and report a false positive to the
vendor that flags it. Do not upload other builds under the same version.

## If the certificate is not ready: ship unsigned

An unsigned 1.0 is acceptable; a delayed 1.0 for a certificate is not. Skip steps 1 to 6 and publish the draft as CI
built it (`gh release edit v1.0.0 --draft=false`), and say so in the release notes: *"This installer is not signed yet.
Windows SmartScreen shows it as from an unknown publisher."* Sign from the next release on.

## What users see: SmartScreen

A valid signature gives the SmartScreen and UAC dialogs a verified publisher name. It does **not** give instant
SmartScreen reputation: reputation builds with downloads of the same file, so a fresh release can still show the blue
"Windows protected your PC" dialog, signed or not. The user-facing steps are in the
[README](../README.md#windows-installer); in short: click **More info**, then **Run anyway**. Never change the file
name (`NexusVox-Setup.exe`, no version) and never publish two different builds under one version: reputation is tracked
per file, which is why the release stays a draft until the signed installer is on it.

Not signed: the DLL and `.pyd` files inside the app folder (only `NexusVox.exe`, the uninstaller and the setup are).
