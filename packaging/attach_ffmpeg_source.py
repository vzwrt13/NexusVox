"""Attach the source of the installer's FFmpeg to a GitHub release (the LGPL asks for it).

    python packaging/attach_ffmpeg_source.py v1.0.0      # check, download, verify, upload to the release
    python packaging/attach_ffmpeg_source.py --dry-run   # only download and verify the pinned archives

The installer ships an LGPL FFmpeg that `packaging/lgpl_av.py` pins. Anyone who receives it may ask for that FFmpeg's
source, so every release that carries the installer also carries it: the FFmpeg commit the build was made from, and
the build scripts of BtbN that name the source of every library linked into it (`lgpl_av.SOURCE_ARCHIVES`). The
release workflow runs this right after it creates the draft, so the source is on it before anyone can publish; run it
by hand only to repair a release. Both archives are downloaded from the pinned URL and checked against the pinned
SHA-256 first; nothing is uploaded when a digest differs. The upload is `gh release upload --clobber`, so running it
again replaces the assets and is safe.

With a tag it also refuses when the checkout is not that tag (the pins in it must be the ones the installer was built
with), when the release holds no installer, and when the release is already published (`--allow-published` repairs a
published one). It needs the GitHub CLI, logged in with write access to the repository.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import lgpl_av

INSTALLER = "NexusVox-Setup.exe"


def fetch_sources() -> list[Path]:
    """Download every pinned source archive (reusing a verified copy in build/lgpl-av/) and return the files, named as
    they will be on the release. LgplBuildError when a download fails or does not match its SHA-256."""
    return [lgpl_av.download_source(name, url, sha256) for name, url, sha256 in lgpl_av.SOURCE_ARCHIVES]


def upload_command(tag: str, files: list[Path], gh: str = "gh") -> list[str]:
    """The `gh` call that puts `files` on the release of `tag`, replacing assets of the same name."""
    return [gh, "release", "upload", tag, *map(str, files), "--clobber"]


def _run(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


def release_problems(tag: str, gh: str, allow_published: bool = False) -> list[str]:
    """Why the source must not go on the release of `tag`; empty when it may. The pins are the checkout's, so the
    checkout has to be the tag; the release has to be the one the installer was attached to, and still a draft."""
    problems = []
    try:
        tagged, head = _run(["git", "rev-parse", "--verify", f"{tag}^{{commit}}"]), _run(["git", "rev-parse", "HEAD"])
    except subprocess.CalledProcessError:
        problems.append(f"The tag {tag} is not in this checkout (git fetch --tags)")
    else:
        if tagged != head:
            problems.append(
                f"This checkout ({head[:10]}) is not {tag} ({tagged[:10]}): its pins may be those of another FFmpeg "
                f"than the one the installer of {tag} ships. Check out the tag first"
            )
    try:
        release = json.loads(_run([gh, "release", "view", tag, "--json", "isDraft,assets"]))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return [*problems, f"There is no release for {tag}, or gh cannot read it"]
    if INSTALLER not in {asset["name"] for asset in release["assets"]}:
        problems.append(
            f"The release of {tag} holds no {INSTALLER}: it does not ship the FFmpeg these sources belong to"
        )
    if not release["isDraft"] and not allow_published:
        problems.append(f"The release of {tag} is already published (--allow-published to add the source anyway)")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tag", nargs="?", help="the release tag, e.g. v1.0.0")
    parser.add_argument("--dry-run", action="store_true", help="check, download and verify, but do not upload")
    parser.add_argument("--allow-published", action="store_true", help="also add the source to a published release")
    args = parser.parse_args(argv)
    if args.tag is None and not args.dry_run:
        parser.error("name the release tag (or use --dry-run to only verify the pinned archives)")
    gh = shutil.which("gh")
    try:
        if args.tag is not None:
            if gh is None:
                raise lgpl_av.LgplBuildError("The GitHub CLI (gh) is not installed or not on PATH")
            problems = release_problems(args.tag, gh, args.allow_published)
            if problems:
                raise lgpl_av.LgplBuildError("; ".join(problems))
        files = fetch_sources()
        if args.tag is None:
            print("Verified " + ", ".join(file.name for file in files) + ".")
            return 0
        command = upload_command(args.tag, files, gh)
        if args.dry_run:
            print("Verified. Would run: " + " ".join(command))
            return 0
        subprocess.run(command, check=True)
    except (lgpl_av.LgplBuildError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Attached {', '.join(file.name for file in files)} to {args.tag}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
