# NexusVox — CI/CD

## Workflows

**CI** (`.github/workflows/ci.yml`) — runs on every push and PR: Ruff lint + format check on Linux/3.11, then pytest across a matrix of `ubuntu-latest` × `windows-latest` and Python 3.11/3.12/3.13 (6 jobs, `fail-fast: false`).

**Release** (`.github/workflows/release.yml`) — runs on version tags (`v*.*.*`): lint, the same test matrix, build `.whl` + `.tar.gz` on Linux/3.11, publish GitHub Release.


## Releasing

1. Update `version` in `pyproject.toml`
2. Commit and push
3. `git tag v0.2.0 && git push origin v0.2.0`
4. Release workflow builds and creates the GitHub Release automatically

## Repository

Branching model: `main` (stable) + `feature/*` / `fix/*` branches merged via PR. Direct commits and pushes to `main` are rejected by the hooks in `.githooks/` (see CONTRIBUTING.md); branch protection replaces them as the authoritative check once the repository is public.
