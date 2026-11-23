"""packaging/build.py refuses to bundle a nexusvox that is not the checkout's version."""

from __future__ import annotations

from pathlib import Path

from packaging_scripts import load_packaging_script

load_packaging_script("lgpl_av")  # build.py imports it by name
build = load_packaging_script("build")


def _checkout(tmp_path: Path, pyproject: str = "1.0.1", source: str = "1.0.1") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "nexusvox"\nversion = "{pyproject}"\n')
    package = tmp_path / "src" / "nexusvox"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(f'"""Doc."""\n\n__version__ = "{source}"\n')
    return tmp_path


def _installed(tmp_path: Path, version: str) -> Path:
    package = tmp_path / "site-packages" / "nexusvox"
    package.mkdir(parents=True)
    init = package / "__init__.py"
    init.write_text(f'__version__ = "{version}"\n')
    return init


def test_matching_install_passes(tmp_path):
    root = _checkout(tmp_path / "repo")
    assert build.stale_bundle_error(root, _installed(tmp_path, "1.0.1")) is None


def test_stale_install_names_both_versions_and_location(tmp_path):
    root = _checkout(tmp_path / "repo")
    init = _installed(tmp_path, "1.0.0")
    message = build.stale_bundle_error(root, init)
    assert "1.0.0" in message and "1.0.1" in message and str(init.parent) in message
    assert "\n" not in message


def test_missing_install_is_reported(tmp_path):
    assert "not installed" in build.stale_bundle_error(_checkout(tmp_path / "repo"), None)


def test_checkout_that_disagrees_with_itself_is_reported(tmp_path):
    root = _checkout(tmp_path / "repo", pyproject="1.0.2", source="1.0.1")
    assert "fix the checkout" in build.stale_bundle_error(root, _installed(tmp_path, "1.0.2"))


def test_real_checkout_versions_agree():
    assert build.checkout_version() == build._package_version(build.ROOT / "src" / "nexusvox" / "__init__.py")


def test_python_mismatch_names_both_versions():
    message = build.python_mismatch_error("3.12", (3, 11))
    assert "3.11" in message and "3.12" in message and "\n" not in message


def test_python_match_passes():
    assert build.python_mismatch_error("3.12", (3, 12)) is None


def test_ci_python_version_is_read_from_the_workflow(tmp_path):
    workflow = tmp_path / "w.yml"
    workflow.write_text('steps:\n  - uses: actions/setup-python@v7\n    with:\n      python-version: "3.12"\n')
    assert build.ci_python_version(workflow) == "3.12"


def test_real_installer_workflow_pins_a_python():
    assert build.ci_python_version().count(".") == 1
