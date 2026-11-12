"""The installer's build helpers (packaging/third_party_notices.py, packaging/build_installer.py)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import pytest
from packaging_scripts import PACKAGING, load_packaging_script

notices = load_packaging_script("third_party_notices")
build_installer = load_packaging_script("build_installer")


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
    section = notices._binaries_section(tmp_path)
    assert "libx264-165-abc.dll\n    x264 - GPL-2.0-or-later" in section and "python312.dll" in section
    assert notices.bundle_gpl_parts(tmp_path) == [f"{Path('_internal/av.libs/libx264-165-abc.dll')} is a GPL library"]


def test_no_gpl_library_means_no_gpl_notice(tmp_path):
    (tmp_path / "_internal").mkdir()
    (tmp_path / "_internal" / "python312.dll").write_bytes(b"")
    assert notices.bundle_gpl_parts(tmp_path) == []


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


def test_iscc_signs_nothing_without_a_sign_command():
    command = build_installer.iscc_command("ISCC.exe", "1.0.0")
    assert command[0] == "ISCC.exe"
    assert "/DAppVersion=1.0.0" in command
    assert not any(argument.startswith(("/S", "/DSignInstaller")) for argument in command)


def test_iscc_gets_the_sign_command_for_the_setup_and_the_uninstaller():
    command = build_installer.iscc_command("ISCC.exe", "1.0.0", "signtool sign $f")
    assert "/Snexusvox=signtool sign $f" in command  # the sign tool the script's SignTool= directive names
    assert "/DSignInstaller=1" in command
    assert command[-1].endswith("nexusvox.iss")
    iss = (PACKAGING / "nexusvox.iss").read_text(encoding="utf-8")
    assert "SignTool=nexusvox" in iss
    assert "SignedUninstaller=yes" in iss


def test_the_bootloader_check_flags_the_shared_prebuilt_stub():
    check = load_packaging_script("check_bootloader")
    published = {"run.exe": b"shared", "runw.exe": b"shared-w"}
    assert check.identical_stubs({"run.exe": b"shared", "runw.exe": b"shared-w"}, published) == ["run.exe", "runw.exe"]
    assert check.identical_stubs({"run.exe": b"mine", "runw.exe": b"shared-w"}, published) == ["runw.exe"]
    assert check.identical_stubs({"run.exe": b"mine", "runw.exe": b"mine-w"}, published) == []


@pytest.mark.skipif(sys.platform != "win32", reason="signtool and Authenticode are Windows-only")
def test_the_release_signing_script_signs_the_exe_then_the_installer():
    """packaging/sign_release.ps1 with throw-away self-signed certificates: never the real certificate."""
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell not found")
    result = subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PACKAGING / "test_sign_release.ps1")],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode == 77:  # the script's own "signtool.exe (Windows SDK) not installed"
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
