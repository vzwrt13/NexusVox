"""The scripts a page loads, for the checks that must follow index.html (tests and both packaging checks)."""

import re

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_SCRIPT_SRC = re.compile(r"""<script\b[^>]*?\bsrc=["']([^"']+)["']""", re.I)


def page_scripts(html: str) -> list[str]:
    """Every script `src` of the page in load order, whatever else a tag carries; commented-out tags don't count."""
    return _SCRIPT_SRC.findall(_COMMENT.sub("", html))
