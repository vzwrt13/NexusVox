"""Prepare LibriSpeech test-clean chapters for `scripts/parakeet_long_bench.py`.

LibriSpeech (CC BY 4.0) is read audiobook speech, cut into utterances of one chapter, so consecutive
utterances of a chapter are real continuous reading by one speaker, unlike FLEURS clips that have
nothing to do with each other.

Downloads test-clean (about 350 MB) unless the archive is already there, then picks a few chapters of
different speakers (at least `--min-chapter-s` of audio each, evenly spread over the sorted speaker
list) and writes one folder per chapter in the layout of the FLEURS folders: 16 kHz mono PCM16 WAV
files plus refs.tsv ("<file>\\t<reference>" per line, in reading order). Needs ffmpeg on the PATH.

    python scripts/librispeech_fetch.py benchmarks/librispeech
    python scripts/parakeet_long_bench.py benchmarks/librispeech --langs ls_1089_134686 ... --gap-s 0
"""

from __future__ import annotations

import argparse
import subprocess
import tarfile
import urllib.request
from pathlib import Path

URL = "https://www.openslr.org/resources/12/test-clean.tar.gz"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--chapters", type=int, default=6, help="how many chapters (speakers) to prepare")
    ap.add_argument("--min-chapter-s", type=float, default=240.0, help="skip chapters with less audio than this")
    args = ap.parse_args()

    root = args.out_dir
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "test-clean.tar.gz"
    corpus = root / "LibriSpeech" / "test-clean"
    if not corpus.exists():
        if not archive.exists():
            urllib.request.urlretrieve(URL, archive)
        with tarfile.open(archive) as tar:
            tar.extractall(root, filter="data")

    chapters = []  # (speaker, chapter, dir, utterances [(id, text)], seconds)
    for spk in sorted(corpus.iterdir(), key=lambda p: int(p.name)):
        for chap in sorted(spk.iterdir(), key=lambda p: int(p.name)):
            lines = (chap / f"{spk.name}-{chap.name}.trans.txt").read_text("utf-8").splitlines()
            utts = [ln.split(" ", 1) for ln in lines if ln]
            seconds = sum(_flac_seconds(chap / f"{uid}.flac") for uid, _ in utts)
            if seconds >= args.min_chapter_s:
                chapters.append((spk.name, chap.name, chap, utts, seconds))
    # One chapter per speaker, evenly spread over the sorted speakers.
    seen: set[str] = set()
    per_speaker = [c for c in chapters if not (c[0] in seen or seen.add(c[0]))]
    step = max(len(per_speaker) // args.chapters, 1)
    chosen = per_speaker[::step][: args.chapters]

    for spk, chap, src, utts, seconds in chosen:
        dest = root / f"ls_{spk}_{chap}"
        dest.mkdir(exist_ok=True)
        rows = []
        for uid, text in utts:
            wav = dest / f"{uid}.wav"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(src / f"{uid}.flac"), "-ar", "16000", "-ac", "1", str(wav)],
                check=True,
            )
            rows.append(f"{wav.name}\t{text.lower()}")
        (dest / "refs.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
        print(f"{dest.name}: {len(utts)} utterances, {seconds:.0f} s")


def _flac_seconds(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


if __name__ == "__main__":
    main()
