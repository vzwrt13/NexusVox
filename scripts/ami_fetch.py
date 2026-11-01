"""Turn AMI corpus meeting annotations into plain-text transcripts for `scripts/llm_bench.py --meeting`.

The AMI Meeting Corpus (CC BY 4.0, https://groups.inf.ed.ac.uk/ami/corpus/) holds hand-made word-level
transcripts of real, spontaneous meetings with fillers, false starts and overlapping speakers. This downloads
the public annotation archive (about 23 MB) unless it is already there, merges the four speakers' word files of a
meeting by start time and writes one `<meeting>.txt` per meeting, one line per uninterrupted turn:
`Speaker A: ...`. A meeting longer than `--max-words` is cut at a turn boundary.

    python scripts/ami_fetch.py benchmarks/llm/ami --meetings ES2008a IS1008a TS3003a
"""

from __future__ import annotations

import argparse
import re
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree

URL = "https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip"
SPEAKERS = "ABCDE"


def meeting_words(archive: zipfile.ZipFile, meeting: str) -> list[tuple[float, str, str]]:
    """(start, speaker, token) of every spoken word of the meeting; vocal sounds and gaps are left out."""
    words = []
    for speaker in SPEAKERS:
        name = f"words/{meeting}.{speaker}.words.xml"
        if name not in archive.namelist():
            continue
        for node in ElementTree.fromstring(archive.read(name)):
            if node.tag.endswith("}w") or node.tag == "w":
                start = float(node.get("starttime", "0"))
                words.append((start, speaker, (node.text or "").strip(), node.get("punc") == "true"))
    if not words:
        raise SystemExit(f"no transcript words for {meeting!r} in the archive (check the meeting ID, e.g. ES2008a)")
    words.sort(key=lambda w: w[0])
    return [(start, speaker, (token, punc)) for start, speaker, token, punc in words]


def transcript(words, max_words: int) -> str:
    lines: list[tuple[str, list[str]]] = []
    for _, speaker, (token, _punc) in words:
        if not token:
            continue
        if not lines or lines[-1][0] != speaker:
            lines.append((speaker, []))
        lines[-1][1].append(token)
    out, count = [], 0
    for speaker, tokens in lines:
        text = re.sub(r"\s+([.,?!:;])", r"\1", " ".join(tokens))
        if count + len(tokens) > max_words:
            break
        out.append(f"Speaker {speaker}: {text}")
        count += len(tokens)
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--meetings", nargs="+", default=["ES2008a", "IS1008a", "TS3003a"])
    ap.add_argument("--max-words", type=int, default=5300)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = args.out_dir / "ami_public_manual_1.6.2.zip"
    if not zip_path.exists():
        print("downloading", URL)
        part = zip_path.with_name(zip_path.name + ".part")  # a dropped connection must not leave a truncated zip
        urllib.request.urlretrieve(URL, part)
        part.replace(zip_path)
    with zipfile.ZipFile(zip_path) as archive:
        for meeting in args.meetings:
            text = transcript(meeting_words(archive, meeting), args.max_words)
            if not text:
                raise SystemExit(f"{meeting}: the first turn is longer than --max-words, nothing to write")
            (args.out_dir / f"{meeting}.txt").write_text(text, encoding="utf-8")
            print(meeting, len(text.split()), "words")


if __name__ == "__main__":
    main()
