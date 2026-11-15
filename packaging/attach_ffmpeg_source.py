"""Attach the source of the installer's FFmpeg to a GitHub release (the LGPL asks for it).

    python packaging/attach_ffmpeg_source.py v1.0.0              # download, verify, upload to the release
    python packaging/attach_ffmpeg_source.py v1.0.0 --dry-run    # download and verify, print the upload command

The installer ships an LGPL FFmpeg that `packaging/lgpl_av.py` pins. Anyone who receives it may ask for that FFmpeg's
source, so every release carries it: the FFmpeg commit the build was made from, and the build scripts of BtbN that
name the source of every library linked into it (`lgpl_av.SOURCE_ARCHIVES`). Both are downloaded from the pinned URL
and checked against the pinned SHA-256 first; nothing is uploaded when a digest differs. The upload is
`gh release upload --clobber`, so running it again replaces the assets and is safe. Run it from a checkout of the
release tag (the pins in it are the ones of the installer), after the installer is on the draft and before the draft
is published (docs/SIGNING.md). It needs the GitHub CLI, logged in with write access to the repository.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import lgpl_av


def fetch_sources() -> list[Path]:
    """Download every pinned source archive (reusing a verified copy in build/lgpl-av/) and return the files, named as
    they will be on the release. LgplBuildError when a download fails or does not match its SHA-256."""
    return [lgpl_av._download(url, sha256, name) for name, url, sha256 in lgpl_av.SOURCE_ARCHIVES]


def upload_command(tag: str, files: list[Path], gh: str = "gh") -> list[str]:
    """The `gh` call that puts `files` on the release of `tag`, replacing assets of the same name."""
    return [gh, "release", "upload", tag, *map(str, files), "--clobber"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tag", help="the release tag, e.g. v1.0.0")
    parser.add_argument("--dry-run", action="store_true", help="download and verify, but do not upload")
    args = parser.parse_args(argv)
    try:
        files = fetch_sources()
        gh = shutil.which("gh")
        command = upload_command(args.tag, files, gh or "gh")
        if args.dry_run:
            print("Verified. Would run: " + " ".join(command))
            return 0
        if gh is None:
            raise lgpl_av.LgplBuildError("The GitHub CLI (gh) is not installed or not on PATH")
        subprocess.run(command, check=True)
    except (lgpl_av.LgplBuildError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Attached {', '.join(file.name for file in files)} to {args.tag}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
