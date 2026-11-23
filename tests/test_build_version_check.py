"""packaging/build.py refuses to bundle a nexusvox that is not the checkout's version."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
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


def _workflow(tmp_path, text):
    path = tmp_path / "w.yml"
    path.write_text(text)
    return path


JOBS = """\
jobs:
  lint:
    steps:
      - uses: actions/setup-python@v7
        with:
          python-version: "3.13"
  installer:
    steps:
      # - uses: actions/setup-python@v7 (old)
      #   python-version: "3.11"
      - uses: actions/setup-python@v7
        with:
          python-version: PIN
      - run: python packaging/build.py
"""


@pytest.mark.parametrize("pin", ['"3.12"', "'3.12'", "3.12", "['3.12']"])
def test_pin_is_read_from_the_build_job_only(tmp_path, pin):
    assert build.ci_python_version(_workflow(tmp_path, JOBS.replace("PIN", pin))) == "3.12"


@pytest.mark.parametrize("pin", ["${{ matrix.python }}", "['3.11', '3.12']"])
def test_unreadable_pin_raises(tmp_path, pin):
    with pytest.raises(ValueError):
        build.ci_python_version(_workflow(tmp_path, JOBS.replace("PIN", pin)))


def test_override_env_replaces_the_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv(build.PYTHON_OVERRIDE_ENV, "3.10")
    assert build.ci_python_version(_workflow(tmp_path, "nothing readable")) == "3.10"


def test_broken_pyproject_is_not_blamed_on_the_workflow(tmp_path, monkeypatch):
    root = _checkout(tmp_path / "repo")
    (root / "pyproject.toml").write_text("this is = = not toml")
    monkeypatch.setattr(build, "ROOT", root)
    monkeypatch.setattr(build, "ci_python_version", lambda: f"{sys.version_info.major}.{sys.version_info.minor}")
    monkeypatch.setattr(build, "installed_package_init", lambda: None)
    message = build.preflight_error()
    assert "nexusvox version" in message and "CI Python" not in message


def test_dynamic_version_gives_one_line_not_a_traceback(tmp_path, monkeypatch):
    root = _checkout(tmp_path / "repo")
    (root / "pyproject.toml").write_text('[project]\nname = "nexusvox"\ndynamic = ["version"]\n')
    monkeypatch.setattr(build, "ROOT", root)
    monkeypatch.setattr(build, "ci_python_version", lambda: f"{sys.version_info.major}.{sys.version_info.minor}")
    monkeypatch.setattr(build, "installed_package_init", lambda: None)
    assert "KeyError" in build.preflight_error()


def test_editable_install_is_named_as_such(tmp_path):
    message = build.stale_bundle_error(_checkout(tmp_path / "repo"), None, editable=True)
    assert "editable" in message and "not installed" not in message


def test_real_installer_workflow_pins_a_python():
    assert build.ci_python_version().count(".") == 1
