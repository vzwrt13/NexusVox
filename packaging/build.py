"""Build the one-folder Windows app with PyInstaller and report its size and build time.

    python packaging/build.py              # build into dist/NexusVox
    python packaging/build.py --clean      # drop PyInstaller's cache first
    python packaging/build.py --check      # only check Python and the installed nexusvox against CI and the checkout
    python packaging/build.py --selftest   # test build in dist/selftest with `--selftest <dir>`; never ship it

Run it with the Python environment that has NexusVox and PyInstaller installed. Install NexusVox as a
regular package (`pip install .`), not editable: the build must contain what a wheel contains.

Before freezing, it replaces the PyAV from PyPI in that environment with one built against an LGPL-only FFmpeg
(`lgpl_av.py`: the PyPI wheel carries the GPL encoders x264 and x265), and after freezing it refuses an app that
still contains a GPL library. Use a dedicated environment for building.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

from lgpl_av import GplError, LgplBuildError, bundle_gpl_parts, ensure_lgpl_av

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "nexusvox.spec"
_VERSION_LINE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


INSTALLER_WORKFLOW = ROOT / ".github" / "workflows" / "installer.yml"
PYTHON_OVERRIDE_ENV = "NEXUSVOX_CI_PYTHON"
_JOB_HEADER = re.compile(r"^  [\w-]+:\s*$")
_STEP_START = re.compile(r"^\s*-\s")
_PYTHON_VALUE = re.compile(r"python-version:\s*(.+)")
INSTALL_COMMAND = "python -m pip install . && python -m pip install --no-deps --force-reinstall ."


def _without_comments(text: str) -> list[str]:
    return [re.sub(r"(^|\s)#.*$", "", line).rstrip() for line in text.splitlines()]


def ci_python_version(workflow: Path = INSTALLER_WORKFLOW) -> str:
    """The major.minor Python the installer workflow builds the app with.

    It is the `python-version` of the `actions/setup-python` step in the job that runs `packaging/build.py`
    (comments skipped; quoted, bare and one-item list forms accepted). Setting `NEXUSVOX_CI_PYTHON` (for example
    `3.12`) replaces the lookup when the workflow uses a form this cannot read.
    """
    override = os.environ.get(PYTHON_OVERRIDE_ENV, "").strip()
    if override:
        return override
    jobs: list[list[str]] = []
    for line in _without_comments(workflow.read_text(encoding="utf-8")):
        if _JOB_HEADER.match(line):
            jobs.append([])
        if jobs:
            jobs[-1].append(line)
    build_jobs = [job for job in jobs if any("packaging/build.py" in line for line in job)]
    if len(build_jobs) != 1:
        raise ValueError(f"expected one job running packaging/build.py in {workflow}, found {len(build_jobs)}")
    job = build_jobs[0]
    for index, line in enumerate(job):
        if "actions/setup-python" not in line:
            continue
        for step_line in job[index : index + 12]:
            if step_line is not line and _STEP_START.match(step_line):
                break
            value = _PYTHON_VALUE.search(step_line)
            if value:
                versions = re.findall(r"\d+\.\d+", value.group(1))
                if "${{" in value.group(1) or len(versions) != 1:
                    raise ValueError(f"python-version is `{value.group(1)}`, not one fixed version")
                return versions[0]
    raise ValueError(f"no setup-python python-version in the build job of {workflow}")


def python_mismatch_error(expected: str, running: tuple[int, int] | None = None) -> str | None:
    """One line when the running Python is not the one CI builds the installer with, else None."""
    running = running or (sys.version_info.major, sys.version_info.minor)
    found = f"{running[0]}.{running[1]}"
    if found == expected:
        return None
    return (
        f"this is Python {found}, but the installer workflow builds with Python {expected} "
        f"(.github/workflows/installer.yml): create the signing venv with Python {expected}"
    )


def checkout_version(root: Path = ROOT) -> str:
    """The version `pyproject.toml` of the checkout declares."""
    with (root / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)["project"]["version"]


def _package_version(init_file: Path) -> str | None:
    match = _VERSION_LINE.search(init_file.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def installed_package_init() -> Path | None:
    """The `nexusvox/__init__.py` the build environment would bundle, without importing it.

    The checkout's own folders are left out of the search: the PyInstaller spec bundles what is installed.
    """
    checkout = {ROOT.resolve(), (ROOT / "src").resolve(), Path(__file__).resolve().parent}
    paths = [p for p in sys.path if p and Path(p).resolve() not in checkout]
    spec = importlib.machinery.PathFinder.find_spec("nexusvox", paths)
    return Path(spec.origin) if spec and spec.origin else None


def editable_install_found() -> bool:
    """True when nexusvox resolves only through an editable install (a .pth entry or a meta-path finder)."""
    try:
        spec = importlib.util.find_spec("nexusvox")
    except (ImportError, ValueError):
        return False
    return bool(spec and spec.origin)


def stale_bundle_error(root: Path = ROOT, installed_init: Path | None = None, editable: bool = False) -> str | None:
    """One line naming the mismatch when the installed nexusvox is not the checkout's, else None."""
    expected = checkout_version(root)
    source_version = _package_version(root / "src" / "nexusvox" / "__init__.py")
    if source_version != expected:
        return f"src/nexusvox/__init__.py says {source_version}, pyproject.toml says {expected}: fix the checkout first"
    if installed_init is None:
        if editable:
            return f"nexusvox is an editable install here, the build needs a regular one: run `{INSTALL_COMMAND}`"
        return f"nexusvox is not installed in this environment: run `{INSTALL_COMMAND}`"
    found = _package_version(installed_init)
    if found != expected:
        return (
            f"the installed nexusvox is {found} ({installed_init.parent}), the checkout is {expected}: "
            f"run `{INSTALL_COMMAND}` and build again"
        )
    return None


def preflight_error() -> str | None:
    """The first reason this environment must not build the release app, as one line, or None."""
    try:
        expected_python = ci_python_version()
    except (OSError, ValueError) as error:
        return f"cannot read the CI Python version ({error}): set {PYTHON_OVERRIDE_ENV}=<major.minor> to override"
    problem = python_mismatch_error(expected_python)
    if problem:
        return problem
    try:
        init = installed_package_init()
        return stale_bundle_error(ROOT, init, editable=init is None and editable_install_found())
    except (OSError, KeyError, ValueError) as error:  # ValueError covers tomllib.TOMLDecodeError
        return f"cannot check the nexusvox version ({type(error).__name__}: {error}): is pyproject.toml intact?"


def folder_size_mb(folder: Path) -> float:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1_000_000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true", help="build in the --selftest mode (never ship that build)")
    parser.add_argument("--clean", action="store_true", help="remove PyInstaller's cache before building")
    parser.add_argument("--check", action="store_true", help="only run the Python and installed-version checks")
    args = parser.parse_args()

    # A self-test build goes to its own folder so it can never be mistaken for the build that ships.
    dist = ROOT / "dist" / "selftest" if args.selftest else ROOT / "dist"
    command = [
        sys.executable, "-m", "PyInstaller", str(SPEC),
        "--noconfirm",
        "--distpath", str(dist),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
    ]  # fmt: skip
    if args.clean:
        command.append("--clean")

    problem = preflight_error()
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 1
    if args.check:
        print("ok: Python and installed nexusvox match the checkout and CI")
        return 0

    try:
        ensure_lgpl_av()
    except GplError as error:
        print(f"error: no PyAV without GPL parts: {error}", file=sys.stderr)
        return 1
    except (LgplBuildError, subprocess.CalledProcessError, OSError) as error:
        print(f"error: building the LGPL PyAV failed: {error}", file=sys.stderr)
        return 1

    env = dict(os.environ)
    if args.selftest:
        env["NEXUSVOX_BUILD_SELFTEST"] = "1"
    started = time.perf_counter()
    result = subprocess.run(command, cwd=ROOT, env=env)
    elapsed = time.perf_counter() - started
    if result.returncode != 0:
        return result.returncode

    app = dist / "NexusVox"
    gpl = bundle_gpl_parts(app)
    if gpl:
        print("error: the app contains GPL libraries:\n  " + "\n  ".join(gpl), file=sys.stderr)
        return 1
    print(f"Build time: {elapsed:.0f} s")
    print(f"Folder size: {folder_size_mb(app):.0f} MB ({app})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
