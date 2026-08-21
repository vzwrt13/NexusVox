# NexusVox — CI/CD

## Workflows

**CI** (`.github/workflows/ci.yml`) — runs on every push and PR: Ruff lint + format check on Linux/3.11, then pytest across a matrix of `ubuntu-latest` × `windows-latest` and Python 3.11/3.12/3.13 (6 jobs, `fail-fast: false`).

**Release** (`.github/workflows/release.yml`) — runs on version tags (`v*.*.*`): lint, the same test matrix, build `.whl` + `.tar.gz` on Linux/3.11, publish GitHub Release.


## Releasing

1. Update `version` in `pyproject.toml` **and** `__version__` in `src/nexusvox/__init__.py` -- they must agree
2. Merge that through a pull request like any other change
3. Tag the merge commit on `main` with an **annotated** tag:

   ```bash
   git tag -a v0.2.0        # opens an editor, or use -F -
   git push origin v0.2.0
   ```

4. The release workflow lints, runs the full test matrix, builds the wheel and
   sdist, verifies the built wheel with `scripts/packaging_smoke_test.py`, and
   publishes a GitHub Release with both artifacts attached

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
