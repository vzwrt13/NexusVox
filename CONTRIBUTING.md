# Contributing to NexusVox

Thanks for your interest. NexusVox is a local speech-to-text tool for Windows, published as a
reference implementation rather than a product.

Please know what you are walking into: the project is **not under active development**. It
works and it is finished for its purpose, but no new features are planned, and the maintainer
does not watch the repository daily. Issues and pull requests are read and welcome, but a
reply can take a while, and a change that is not clearly a fix may simply not be merged. If
that is fine with you, everything below applies.

## Before you start

- **Running NexusVox requires Windows 11.** Text injection uses Win32 `SendInput`/`WM_PASTE` and the hotkey listener uses pynput's win32 hooks; neither is portable.
- **The test suite is not Windows-bound** and runs on Linux too, so you can work on the transcription, dashboard, database, and config layers from any platform — you just can't exercise dictation end to end.
- For bugs, please include your OS, Python version, the model you selected, and whether you are on the CPU or GPU path.

## Development setup

```bash
git clone <your-fork>
cd NexusVox
git config core.hooksPath .githooks
python -m venv .venv
.venv\Scripts\activate          # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Python 3.11 or newer. On first run, `config.toml` is created in the working directory from the template at `src/nexusvox/config.example.toml`.

Optional: [ffmpeg](https://ffmpeg.org/) on your `PATH`, needed only for uploading non-WAV audio files in the dashboard. Not required for the test suite.

The GPU backends (Voxtral, Cohere, Parakeet) additionally need Docker and an NVIDIA GPU — see [GettingStarted.md](GettingStarted.md). Most contributions don't need them: the Whisper models run in-process on CPU.

## Before you push

Run what CI runs:

```bash
ruff check .
ruff format --check .
pytest
```

`ruff format .` and `ruff check --fix .` apply the safe fixes automatically.

Ruff is pinned to an exact version in the dev extra and in CI, so `pip install -e ".[dev]"` gives you the same one the pipeline uses. A different version will disagree about formatting.

**New code should come with tests.** [docs/TESTING.md](docs/TESTING.md) explains the fixtures, the in-memory SQLite setup, and how to write one. The suite runs in about two seconds — there's no reason to skip it.

## Branches and pull requests

`main` is stable and always releasable. Work happens on branches:

- `feature/short-description` for new features
- `fix/short-description` for bug fixes

Lowercase, hyphens, short. Open a pull request against `main` when it's ready, and describe *why* the change is needed rather than restating the diff.

Nothing is pushed straight to `main` — not features, not one-line fixes, not typo corrections
in the README. Everything goes through a branch and a pull request, so that every change has a
diff someone can look at and a CI run attached to it.

Two hooks in `.githooks/` enforce this locally. `pre-commit` runs two checks: it refuses a
commit whose staged changes look like they contain a credential, and it refuses a commit made
while `main` is checked out. `pre-push` refuses a push that updates or deletes `main` on the
remote. They are plain POSIX shell and behave identically on macOS, Linux and Git Bash under
Windows. Git does not pick up a hooks directory on its own, so activate it once per clone --
`git config core.hooksPath .githooks`, as in the setup above.

The branch check steps aside where blocking would do damage rather than good: a detached HEAD,
and a merge, rebase, cherry-pick or revert that git is in the middle of. Creating `main` on an
empty remote is allowed, so cloning and pushing a fresh fork works without ceremony. The
credential check has no such exemptions -- a merge commit can leak a token just as easily as an
ordinary one.

For a genuine emergency, set `GIT_ALLOW_PROTECTED=1` or `GIT_ALLOW_SECRET=1` for that one
command; the hooks print the exact syntax for your shell when they block you. They are separate
variables on purpose, so silencing one does not silence the other.

If the credential check ever fires on something real, do not simply unstage it. A credential
that reached your working tree should be treated as compromised and rotated.

CI runs a second, independent pass: a `Secret scan` job scans the whole commit history with
[gitleaks](https://github.com/gitleaks/gitleaks), pinned to an exact version and verified by
checksum before it runs. It uses roughly 150 curated rules against the hook's dozen, so it
catches token formats the hook does not know.

Client-side hooks are a guard rail, not a wall: `--no-verify` skips them, and a fresh clone
without the `core.hooksPath` line has no hooks at all. CI is a backstop rather than a
preventative -- by the time it reports, the commit exists. Branch protection and GitHub's push
protection are the enforcement that cannot be talked out of; the hooks and the CI job exist to
catch the honest mistake earlier and more cheaply.

CI runs on every push and pull request — lint on Linux, then the test suite across Linux and Windows on Python 3.11, 3.12, and 3.13. Please merge only when all checks are green. Branch protection is not enabled yet, so this is convention rather than something the repo enforces for you.


## Language

Everything in this repository is written in English — code, comments, docstrings, log
messages, user-facing strings, documentation, commit messages, branch names, and pull
request descriptions. Please keep it that way regardless of the language you think in.

## Commit messages

Write a short imperative subject line ("Add startup health check", not "Added" or "Adding"). If the change needs justification, put it in the body — what was wrong and why this fixes it.

## Where to help

Bug fixes and documentation corrections are the most likely things to be merged, because they
need no product decision. For anything larger — a new feature, a new transcription backend, a
change to how injection or the hotkey works — open an issue first and wait for a reply before
you write code. Given the maintenance level described above, that is not bureaucracy; it is
how you avoid spending an evening on a change that will not land.

## Security

Found something with security impact? Do not open a public issue — the private
channels are in [SECURITY.md](SECURITY.md). It also describes what NexusVox does on
your machine and where the trust boundary sits, which is worth reading before you
decide whether a finding is a bug or intended behaviour.

## Licensing

NexusVox is licensed under the GNU Affero General Public License v3.0 or later. By contributing, you agree that your contributions are licensed under the same terms. There is currently no CLA to sign — if that ever changes, it will be stated here before it applies to anyone.

Note that the speech recognition models are covered by their own licenses, listed in the model table in [README.md](README.md). If you add a model, document its license there — and don't add one that doesn't declare a license.
