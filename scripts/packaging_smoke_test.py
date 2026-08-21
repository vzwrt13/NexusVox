"""Verify that an installed NexusVox wheel is actually usable.

The test suite runs against the source tree, where `src/` layout assumptions hold
by construction. That is exactly the blind spot this script covers: a wheel can
pass every unit test and still be broken for the person who installs it, because
files the code reaches for are not packaged, or are resolved by a path that only
makes sense in a checkout.

That is not hypothetical. `EXAMPLE_CONFIG_PATH` used to be resolved three levels
above `config.py` -- the repository root in a checkout, and a meaningless path in
site-packages. Installed users silently got no `config.toml` at all, while the
error messages went on telling them to edit it.

Run this against a *clean* environment that has the wheel installed and does not
have the source tree importable, from a working directory outside the repository:

    python -m venv /tmp/smoke
    /tmp/smoke/bin/python -m pip install dist/nexusvox-*.whl
    cd /tmp && /tmp/smoke/bin/python /path/to/scripts/packaging_smoke_test.py
"""

from __future__ import annotations

import pathlib
import sys
import tempfile


def main() -> int:
    import nexusvox
    from nexusvox.config import EXAMPLE_CONFIG_PATH, load_config

    package_dir = pathlib.Path(nexusvox.__file__).resolve().parent
    checks: list[tuple[str, bool, str]] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        checks.append((label, ok, detail))

    # The package must come from the installed environment, not from a source
    # tree that happens to be importable -- otherwise this proves nothing.
    from_source = "src" in package_dir.parts
    check(
        "package imported from the installed environment",
        not from_source,
        f"imported from {package_dir}",
    )

    check(
        "config.example.toml ships inside the package",
        EXAMPLE_CONFIG_PATH.exists(),
        f"expected at {EXAMPLE_CONFIG_PATH}",
    )
    check(
        "config template resolves next to the module",
        EXAMPLE_CONFIG_PATH.parent == package_dir,
        f"resolved to {EXAMPLE_CONFIG_PATH.parent}, package is {package_dir}",
    )

    static = package_dir / "dashboard" / "static"
    for asset in ("index.html", "dashboard.js", "style.css", "chart.min.js"):
        check(f"dashboard asset present: {asset}", (static / asset).is_file())

    # The first run, as a new user experiences it: an empty directory.
    with tempfile.TemporaryDirectory() as tmp:
        target = pathlib.Path(tmp) / "config.toml"
        config = load_config(target)
        check(
            "first run creates config.toml",
            target.exists(),
            "load_config fell back to in-memory defaults and wrote nothing",
        )
        check(
            "created config names a model",
            bool(config.inference.model),
        )
        check(
            "created config is not empty",
            target.exists() and target.stat().st_size > 0,
        )

    width = max(len(label) for label, _, _ in checks)
    failed = 0
    for label, ok, detail in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label.ljust(width)}", end="")
        print(f"  {detail}" if detail and not ok else "")
        if not ok:
            failed += 1

    print()
    if failed:
        print(f"{failed} of {len(checks)} packaging checks failed.")
        return 1
    print(f"All {len(checks)} packaging checks passed. Version {nexusvox.__version__}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
