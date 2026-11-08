"""Dashboard API: turning a recorded meeting folder into a stored transcript."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ..config import numbered_speaker_label
from ..meetings.recorder import META_FILENAME, read_meeting_meta
from ..meetings.transcribe import (
    MAX_DETECTION_S,
    MIX_FILENAME,
    SPEAKER_FIRST_SYSTEM,
    SPEAKER_OWN,
    TRACK_NAMES,
    convert_track,
    format_meeting,
    load_meeting,
    merge_words,
    mix_tracks,
    track_offset,
    track_shifts,
)
from ..transcription.file_transcribe import MAX_CHUNK_MS, NO_SPEAKER_LABELS
from ..transcription.speaker_fallback import speaker_detection_job
from .api_base import DashboardState, logger
from .file_jobs import FileJob


def _declared_track_files(folder: Path) -> list[str]:
    """The track file names `meeting.json` lists (also unreadable ones), `<name>.wav` where it names none."""
    try:
        tracks = read_meeting_meta(folder).get("tracks") or {}
    except (OSError, ValueError):
        tracks = {}
    names = {f"{name}.wav" for name in TRACK_NAMES}
    names.update(str(info["file"]) for info in tracks.values() if isinstance(info, dict) and info.get("file"))
    return sorted(names)


class MeetingTranscriptMixin(DashboardState):
    """The transcription job of a meeting and the clean-up of its audio files afterwards."""

    def start_meeting_transcription(self, folder: str | Path) -> dict:
        """Transcribe a recorded meeting folder in the background (the same job registry uploads use)."""
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        folder = Path(folder)
        job = self._file_jobs.start(folder.name, lambda job: self.transcribe_meeting(folder, job=job))
        if job is None:
            return {"ok": False, "busy": True, "error": "Another file is still being transcribed"}
        return {"ok": True, "job_id": job.id}

    def transcribe_meeting(self, folder: str | Path, *, job: FileJob | None = None) -> dict:
        """Transcribe both tracks of a meeting folder, merge them into one transcript with numbered speakers, mix
        `meeting.wav` and save the result as a `kind="meeting"` row (blocking). No upload size limit applies.
        """
        folder = Path(folder)
        progress = job.update if job else None
        logger.info("Meeting transcription started: %s", folder.name)
        if progress:
            progress("converting")
        try:
            meeting = load_meeting(folder)
        except Exception as exc:
            logger.error("Loading meeting %s failed: %s", folder, exc)
            return {"ok": False, "error": f"Could not load the meeting: {exc}"}
        system = meeting.tracks.get("system")
        wants_speakers = system is not None and system.frames / system.sample_rate <= MAX_DETECTION_S
        # A model without speaker detection runs this meeting on Parakeet (see `speaker_fallback`); the container,
        # if the job started it, stays up until the transcript is stored.
        with speaker_detection_job(self._config.inference, wanted=wants_speakers, what="meeting") as speakers:
            return self._transcribe_loaded_meeting(folder, meeting, speakers, job, progress)

    def _transcribe_loaded_meeting(self, folder: Path, meeting, speakers, job, progress) -> dict:
        notes = [speakers.notice, *meeting.notes] if speakers.notice else list(meeting.notes)
        store_audio = self._config.meeting.store_audio  # read once: a switch mid-job applies from the next one
        if not meeting.tracks:
            return {"ok": False, "error": "The meeting has no readable audio track"}

        words: dict[str, list] = {}
        plain: dict[str, str] = {}
        incomplete: list[str] = []
        confidences: list[float] = []
        tracks_16k: dict[str, Path] = {}
        levels: dict[str, list] = {}  # each track's loudness steps, for lining the tracks up
        chunk_s = MAX_CHUNK_MS // 1000
        total_chunks = sum(max(1, -(-t.frames // t.sample_rate // chunk_s)) for t in meeting.tracks.values())
        done_chunks = 0
        with tempfile.TemporaryDirectory(prefix="nexusvox-meeting-") as tmp:
            for name, track in meeting.tracks.items():
                label = TRACK_NAMES[name]
                try:
                    chunks = convert_track(track, Path(tmp) / f"{name}.wav", levels.setdefault(name, []))
                except Exception as exc:
                    logger.error("Audio conversion failed for meeting %s: %s", folder, exc)
                    return {"ok": False, "error": f"Audio conversion failed: {exc}"}
                tracks_16k[name] = Path(tmp) / f"{name}.wav"

                own_chunks = len(chunks)

                def track_progress(stage, current=0, total=0, base=done_chunks, own_chunks=own_chunks):
                    if progress:
                        # Speaker detection starts once the track's chunks are done: the bar stays where it was.
                        progress(stage, base + (own_chunks if stage == "diarizing" else current), total_chunks)

                # The system track holds everyone else, so it runs through speaker detection like an upload.
                # The whole track goes to the server in one request and is held a second time as one WAV, so a
                # very long meeting skips it (everyone on the system audio is then one speaker).
                detect_speakers = name == "system" and track.frames / track.sample_rate <= MAX_DETECTION_S
                if name == "system" and not detect_speakers:
                    notes.append(
                        f"{label}: The recording is too long for speaker detection; everyone on it is shown as "
                        f"{numbered_speaker_label(SPEAKER_FIRST_SYSTEM)}."
                    )

                try:
                    result = self._transcribe_wav_chunks(
                        chunks,
                        word_timestamps=True,
                        diarize=detect_speakers,
                        on_progress=track_progress,
                        inference=speakers.config,
                    )
                except Exception as exc:
                    logger.error("Transcription failed for meeting %s: %s", folder, exc)
                    return {"ok": False, "error": f"Transcription failed: {exc}"}
                done_chunks += len(chunks)
                del chunks  # one track's chunks at a time
                alone = f"everyone on it is shown as {numbered_speaker_label(SPEAKER_FIRST_SYSTEM)}."
                notes.extend(f"{label}: {n.replace(NO_SPEAKER_LABELS, alone)}" for n in result.notes)
                words[name] = list(result.words)
                detected = any(w.speaker is not None for w in result.words)
                if detect_speakers and not detected and not any(alone in n for n in notes):
                    notes.append(f"{label}: Speaker detection gave no speakers; {alone}")
                # Detection labels `text` "Speaker N: ..."; the meeting writes its own labels from the words.
                plain[name] = result.plain_text or result.text
                if result.confidence is not None:
                    confidences.append(result.confidence)
                # The server skips a word whose timing it cannot read; the labelled text is built from timed words.
                if len(result.words) < len(plain[name].split()):
                    incomplete.append(label)

            # The tracks are lined up (one of them delayed), in the words and in the mix alike.
            try:
                offset, measured = track_offset(meeting, levels.get("mic"), levels.get("system"))
            except Exception as exc:  # the transcription is done: never lose it over the alignment
                logger.exception("Lining up the tracks of meeting %s failed: %s", folder, exc)
                notes.append(f"The tracks could not be lined up from the audio ({exc}); the stream delays are used.")
                offset, measured = track_offset(meeting)
            mic_shift, system_shift = track_shifts(offset)
            logger.info(
                "Meeting %s: system track is %+.3f s late against the microphone (%s)", folder.name, offset, measured
            )

            if incomplete:
                # Interleaving needs every word timed: a gap would vanish from the merged text. Keep all the words.
                notes.append(
                    f"Word timing is missing or incomplete for {' and '.join(incomplete)}, so the meeting is "
                    "not interleaved by time: each track's text follows the other."
                )
                if any(w.speaker is not None for w in words.get("system", [])):
                    notes.append(
                        "The detected speakers could not be used without the word timing; everyone on the system "
                        f"audio is shown as {numbered_speaker_label(SPEAKER_FIRST_SYSTEM)}."
                    )
                own_label = self._config.meeting.own_voice_label
                text = "\n\n".join(
                    f"{label}: {plain[n]}"
                    for n, label in (("mic", own_label), ("system", numbered_speaker_label(SPEAKER_FIRST_SYSTEM)))
                    if plain.get(n)
                )
                merged = []
                if self._config.meeting.leave_dictation_out and meeting.dictations:
                    notes.append(
                        "Dictation could not be left out of the transcript because the word timing is missing."
                    )
            else:
                leave_out = self._config.meeting.leave_dictation_out
                merged = merge_words(
                    words.get("mic", []),
                    words.get("system", []),
                    meeting.dictations if leave_out else None,
                    system_offset=offset,
                )
                text = format_meeting(merged, self._config.meeting.own_voice_label)

            if progress:
                progress("saving")
            # Without a mix to keep there is nothing to write: the length comes from the tracks.
            mix_path: Path | None = None
            seconds = max(t.frames / t.sample_rate for t in meeting.tracks.values())
            if store_audio:
                try:
                    mix_path = folder / MIX_FILENAME
                    seconds = mix_tracks(
                        list(tracks_16k.values()),
                        mix_path,
                        [system_shift if n == "system" else mic_shift for n in tracks_16k],
                    )
                except Exception as exc:
                    logger.exception("Mixing meeting %s failed: %s", folder, exc)
                    notes.append(f"The playable mix could not be written, so the recorded tracks were kept: {exc}")
                    mix_path = None

        result = self._store_file_transcription(
            folder.name,
            notes,
            progress,
            text=text,
            duration_ms=round(seconds * 1000),
            confidence=sum(confidences) / len(confidences) if confidences else None,
            words=merged,
            speaker_names={SPEAKER_OWN: self._config.meeting.own_voice_label},
            kind="meeting",
            model=speakers.config.model,
            audio_path=self._stored_audio_path(mix_path) if mix_path else None,
        )
        # The recorded tracks are the only audio there is when the mix could not be written: keep them then.
        if result.get("ok") and (mix_path is not None or not store_audio):
            doomed = [*_declared_track_files(folder), *([] if store_audio else [MIX_FILENAME])]
            self._discard_meeting_files(folder, doomed)
        return result

    def _discard_meeting_files(self, folder: Path, names: list[str]) -> None:
        """Delete files of a stored meeting's folder. The names are written to `meeting.json` first and dropped
        from it once all are gone, so a locked file or a crash is finished by `sweep_meeting_leftovers`. Only
        plain files directly inside the folder are ever deleted, whatever `meeting.json` names."""
        meta_path = folder / META_FILENAME
        meta: dict | None = None
        try:
            meta = read_meeting_meta(folder)
            meta["discard"] = names
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except (OSError, ValueError) as exc:
            logger.warning("Could not note the leftover files of %s: %s", folder.name, exc)
        failed = False
        for name in names:
            path = folder / name
            if name == META_FILENAME or path.resolve().parent != folder.resolve():
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:  # a player or scanner holds it; the sweep at the next start tries again
                failed = True
                logger.warning("Could not delete %s after transcribing the meeting: %s", path, exc)
        if meta is not None and not failed:
            try:
                del meta["discard"]
                meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            except OSError as exc:
                logger.warning("Could not update %s: %s", meta_path, exc)

    def sweep_meeting_leftovers(self) -> None:
        """Finish the deletion of audio files a stored meeting still lists in `meeting.json` (a locked file or
        a quit between the save and the delete). Runs at start; never raises."""
        try:
            base = self._meetings_dir()
            if self._db is None or not base.is_dir():
                return
            for name in self._db.meeting_folder_names():
                folder = self._meeting_folder(name)
                if folder is None:
                    continue
                try:
                    left = read_meeting_meta(folder).get("discard")
                except (OSError, ValueError):
                    continue
                if isinstance(left, list) and left:
                    self._discard_meeting_files(folder, [str(n) for n in left])
        except Exception:
            logger.exception("Sweeping the leftover meeting audio failed")

    def _stored_audio_path(self, path: Path) -> str:
        """`path` relative to the database folder when it lies inside it (like recordings), else absolute."""
        resolved = path.resolve()
        try:
            return str(resolved.relative_to(self._db_dir()))
        except ValueError:
            return str(resolved)
