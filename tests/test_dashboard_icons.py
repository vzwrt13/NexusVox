"""The dashboard icons referenced from style.css exist and are covered by the package data."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "nexusvox" / "dashboard" / "static"


def test_every_icon_in_the_stylesheet_exists():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    icons = set(re.findall(r'url\("(icons/[^"]+\.svg)"\)', css))
    assert len(icons) == 14
    for icon in icons:
        assert (STATIC / icon).is_file(), icon


def test_icons_folder_is_in_package_data():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "dashboard/static/icons/*" in pyproject["tool"]["setuptools"]["package-data"]["nexusvox"]
