# NexusVox — CI/CD

## Workflows

**CI** (`.github/workflows/ci.yml`) — runs once per PR (`pull_request`) and on every push to `main`: Ruff lint + format check on Linux/3.11, then pytest on `ubuntu-latest` × Python 3.11/3.12/3.13 plus one `windows-latest` job on Python 3.13 (4 jobs, `fail-fast: false`). Windows minutes bill double and are slower, so Windows runs the suite once, for the Win32 tests that skip on Linux; the packaging smoke test still runs on both.

**Release** (`.github/workflows/release.yml`) — runs on version tags (`v*.*.*`): lint, the full test matrix (`ubuntu-latest` × `windows-latest` × Python 3.11/3.12/3.13, as releases are rare), build `.whl` + `.tar.gz` on Linux/3.11, build the Windows installer (the `installer` job calls `installer.yml`), publish GitHub Release with the wheel, the sdist and `NexusVox-Setup.exe`.

**Installer** (`.github/workflows/installer.yml`) — on `windows-latest` / Python 3.12, Inno Setup pinned (`INNO_SETUP_VERSION`; the job installs that version if the runner has another): `pip install .` + PyInstaller, `packaging/build.py` (one-folder app), `packaging/build_installer.py` (writes `THIRD_PARTY_NOTICES.txt` with `packaging/third_party_notices.py` - it fails the build for a bundled package without a licence text, add `packaging/licences/<name>.txt` for such a wheel - then compiles `packaging/nexusvox.iss`, version from `pyproject.toml`), then a check that `NexusVox.exe` and `NexusVox-Setup.exe` carry the logo (some pixel of the extracted icon is the violet of the mark; `packaging/nexusvox.ico` is made by `packaging/make_icon.py` from the kit files in `packaging/icon_sources/` and `src/nexusvox/system/tray_icons/` and used by `nexusvox.spec` and `SetupIconFile`), then one PowerShell script with a log per Setup/uninstaller run: silent install, start of the installed app (waits for "NexusVox started" in the log), an install attempt over the running app (must exit non-zero and say NexusVox is running), an upgrade (old `_internal` files gone, add-ons kept, unticked autostart removes the Run value) and uninstalls that check user data survives unless `/DELETEDATA` / `/DELETEMODELS` are given (it waits for the uninstaller's "Log closed." line). Called by the release workflow; also runs on pull requests that touch `packaging/`, `src/`, the licence files, `pyproject.toml` or either workflow file; a newer push cancels a running pull-request build. Only release runs upload the installer. The installer is unsigned. Its file name is **`NexusVox-Setup.exe` for every version**: the website links to `releases/latest/download/NexusVox-Setup.exe`, so never put a version in it.


## Releasing

1. Update `version` in `pyproject.toml` **and** `__version__` in `src/nexusvox/__init__.py` -- they must agree
2. Merge that through a pull request like any other change
3. Write the tag message to `release-notes/v<version>.md` in the same pull request (first line = release title, then features / fixes / docs and upgrade notes). Tag the merge commit on `main` with an **annotated** tag:

   ```bash
   git tag -a v0.2.0 -F release-notes/v0.2.0.md   # or without -F to use an editor
   git push origin v0.2.0
   ```

4. The release workflow lints, runs the full test matrix, builds the wheel and
   sdist, verifies the built wheel with `scripts/packaging_smoke_test.py`, and
   publishes a GitHub Release with both artifacts and `NexusVox-Setup.exe` attached

**The tag annotation becomes the release page.** Its first line is the release
title, the rest is the body, and GitHub's generated list of merged pull requests
is appended below it. Write the annotation for someone who has never seen the
project: what this version is and what changed, not a restatement of the commit
log -- the appended list already covers that.

A lightweight tag (`git tag v0.2.0` without `-a`) still produces a release, but
it has no annotation, so the release is titled with the bare tag name and has no
body beyond the generated list.

## Repository

Branching model: `main` (stable) + `feature/*` / `fix/*` branches merged via PR. Direct commits and pushes to `main` are rejected by the hooks in `.githooks/` (see CONTRIBUTING.md); branch protection replaces them as the authoritative check once the repository is public.
