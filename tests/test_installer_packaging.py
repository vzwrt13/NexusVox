"""The installer's build helpers (packaging/third_party_notices.py, packaging/build_installer.py)."""

from __future__ import annotations

import importlib.util
import sys
from importlib import metadata
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parent.parent / "packaging"


def _load(name: str):
    """Import a script from packaging/ by path. The folder is not a package, and putting the repository root on
    sys.path would make `import packaging` ambiguous with the PyPA library the scripts use."""
    spec = importlib.util.spec_from_file_location(name, PACKAGING / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(PACKAGING))  # build_installer imports its sibling by name, as it does when run as a script
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(PACKAGING))
    return module


notices = _load("third_party_notices")
build_installer = _load("build_installer")


def make_dist(tmp_path: Path, name: str, files: dict[str, str], requires: tuple[str, ...] = (), extra_meta: str = ""):
    """An installed distribution on disk: `<name>-1.0.dist-info` plus the given files (path -> content)."""
    info = tmp_path / f"{name}-1.0.dist-info"
    info.mkdir()
    lines = [f"Name: {name}", "Version: 1.0", *(f"Requires-Dist: {r}" for r in requires)]
    (info / "METADATA").write_text("\n".join(lines) + "\n" + extra_meta, encoding="utf-8")
    record = [f"{name}-1.0.dist-info/METADATA,,"]
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        record.append(f"{relative},,")
    (info / "RECORD").write_text("\n".join(record), encoding="utf-8")
    return metadata.PathDistribution(info)


@pytest.mark.parametrize(
    "path",
    [
        "pkg-1.0.dist-info/licenses/COPYING.LGPL",
        "pkg-1.0.dist-info/LICENSE.APACHE",
        "pkg-1.0.dist-info/LICENSE.PSF",
        "pkg-1.0.dist-info/licenses/anything-at-all",
        "pkg/LICENSE",
        "pkg-1.0.dist-info/NOTICE",
    ],
)
def test_licence_files_are_found_whatever_their_extension(path):
    assert notices._is_licence_file(metadata.PackagePath(path))


@pytest.mark.parametrize("path", ["pkg/license_utils.py", "pkg/deep/er/LICENSE", "tests/LICENSE", "pkg/COPYING.dll"])
def test_other_files_are_not_licence_texts(path):
    assert not notices._is_licence_file(metadata.PackagePath(path))


def test_every_licence_file_of_a_package_is_kept(tmp_path):
    dist = make_dist(
        tmp_path,
        "pynput",
        {
            "pynput-1.0.dist-info/licenses/COPYING.LGPL": "LGPL text",
            "pynput-1.0.dist-info/licenses/COPYING": "GPL text",
        },
    )
    assert {name for name, _ in notices.licence_texts(dist, overrides=tmp_path / "none")} == {"COPYING.LGPL", "COPYING"}


def test_an_override_stands_in_for_a_wheel_without_a_licence(tmp_path):
    dist = make_dist(tmp_path, "tokenizers", {})
    overrides = tmp_path / "licences"
    overrides.mkdir()
    (overrides / "tokenizers.txt").write_text("Apache text", encoding="utf-8")
    assert notices.licence_texts(dist, overrides=overrides) == [("tokenizers.txt", "Apache text")]
    assert notices.licence_texts(dist, overrides=tmp_path / "none") == []


def test_checked_in_overrides_exist_for_the_packages_that_need_them():
    for name in ("ctranslate2", "tokenizers", "flatbuffers"):
        assert (PACKAGING / "licences" / f"{name}.txt").read_text(encoding="utf-8").strip()


def test_a_required_package_that_is_not_installed_fails_instead_of_vanishing(tmp_path):
    root = make_dist(tmp_path, "root", {}, requires=("ghost>=1",))

    def lookup(name):
        if notices.canonicalize_name(name) == "root":
            return root
        raise metadata.PackageNotFoundError(name)

    with pytest.raises(notices.MissingLicenceError, match="ghost"):
        notices.runtime_distributions("root", lookup=lookup)


def test_requirements_are_followed_by_canonical_name_and_markers_are_honoured(tmp_path):
    dists = {
        "root": make_dist(
            tmp_path, "root", {}, requires=("Some_Dep>=1", 'skipped; sys_platform == "nope"', 'e; extra == "x"')
        ),
        "some-dep": make_dist(tmp_path, "Some_Dep", {}),
    }
    found = notices.runtime_distributions("root", lookup=lambda n: dists[notices.canonicalize_name(n)])
    assert [d.metadata["Name"] for d in found] == ["Some_Dep"]


def test_the_homepage_is_the_homepage_not_just_the_first_url(tmp_path):
    dist = make_dist(
        tmp_path,
        "pkg",
        {},
        extra_meta="Project-URL: Documentation, https://docs.example\nProject-URL: Source, https://src.example\n",
    )
    assert notices.homepage(dist) == "https://src.example"
    bare = make_dist(tmp_path, "bare", {}, extra_meta="Project-URL: Changelog, https://log.example\n")
    assert notices.homepage(bare) == ""


def test_native_libraries_are_named_and_gpl_ones_are_called_out(tmp_path):
    assert notices.identify_binary("libx264-165-0f71.dll") == ("x264", "GPL-2.0-or-later")
    assert notices.identify_binary("libmp3lame-0.dll")[1].startswith("LGPL")
    assert notices.identify_binary("something-new.dll")[0] == "not identified"

    internal = tmp_path / "_internal"
    (internal / "av.libs").mkdir(parents=True)
    (internal / "av.libs" / "libx264-165-abc.dll").write_bytes(b"")
    (internal / "python312.dll").write_bytes(b"")
    section, gpl = notices._binaries_section(tmp_path)
    assert gpl
    assert "libx264-165-abc.dll" in section and "python312.dll" in section


def test_no_gpl_library_means_no_gpl_notice(tmp_path):
    (tmp_path / "_internal").mkdir()
    (tmp_path / "_internal" / "python312.dll").write_bytes(b"")
    assert notices._binaries_section(tmp_path)[1] is False


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.7.0", "0.7.0.0"),
        ("1.0.0rc1", "1.0.0.0"),
        ("1.2", "1.2.0.0"),
        ("1.0.0.dev3", "1.0.0.0"),
        ("2.3.4.5", "2.3.4.5"),
    ],
)
def test_the_installer_gets_a_numeric_only_file_version(version, expected):
    assert build_installer.numeric_version(version) == expected


def test_a_version_without_digits_is_refused():
    with pytest.raises(ValueError):
        build_installer.numeric_version("dev")
