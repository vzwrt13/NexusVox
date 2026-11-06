"""`[meeting]`: speaker labels and the meeting hotkey rules."""

from __future__ import annotations

from dataclasses import dataclass, field

from .hotkey import (
    DEFAULT_MEETING_KEY,
    DEFAULT_MEETING_MODIFIERS,
    LEGACY_MEETING_KEY,
    LEGACY_MEETING_MODIFIERS,
    HotkeyConfig,
    normalize_hotkey_key,
    validate_hotkey,
)

MAX_SPEAKER_LABEL_CHARS = 40


def numbered_speaker_label(number: int) -> str:
    """The label a speaker has when nobody named them: "Speaker 1", "Speaker 2", ..."""
    return f"Speaker {number}"


DEFAULT_OWN_VOICE_LABEL = numbered_speaker_label(1)


def normalize_speaker_label(label: object) -> str:
    """A speaker label on one line (whitespace collapsed, at most 40 characters); the default when nothing is left."""
    text = " ".join(str(label or "").split())[:MAX_SPEAKER_LABEL_CHARS].strip()
    return text or DEFAULT_OWN_VOICE_LABEL


def own_voice_label_problem(label: str) -> str | None:
    """Why `label` cannot name the microphone's speaker, None when it can.

    "Speaker 2", "Speaker 3", ... are the labels the other participants get, so using one would write two
    people under one name.
    """
    folded = label.casefold()
    for number in range(2, 100):
        if folded == numbered_speaker_label(number).casefold():
            return f"{label!r} is the label of another speaker; pick a different name"
    return None


@dataclass
class MeetingConfig:
    """Meeting recording (microphone + system audio) started from the dashboard or its own hotkey.

    The hotkey is a toggle and always needs a key, so it never collides with the dictation hotkey; the
    default Ctrl+Shift+F9 leaves the default dictation chord (Ctrl+Win) untouched, has no Alt (no app menu) and
    avoids Ctrl+Shift+M, the Teams mute shortcut. Empty device names mean the system default devices.
    """

    modifiers: list[str] = field(default_factory=lambda: list(DEFAULT_MEETING_MODIFIERS))
    key: str = DEFAULT_MEETING_KEY
    mic_device: str = ""
    output_device: str = ""
    # Show "Inform all participants before you record" before each recording; off after "don't show again".
    consent_hint: bool = True
    # Drop the Me words spoken inside a dictation take from the meeting transcript (the microphone records
    # dictation too). Applied when the meeting is transcribed; the audio files stay as recorded.
    leave_dictation_out: bool = True
    # The label of the microphone's speaker in a meeting transcript; the other participants are numbered on from 2.
    own_voice_label: str = DEFAULT_OWN_VOICE_LABEL
    # Show a rough transcript of both tracks (your voice and "Others") in the dashboard while the meeting
    # records. It sends the audio to the transcription model in 6 s windows all meeting long, and that load is not
    # measured yet, so it is off until switched on (dashboard Meetings tab).
    live_transcript: bool = False
    # Keep `meeting.wav`, the playable mix, after a successful transcription. The per-track WAVs are always
    # deleted then; off deletes the mix too. Meetings already stored keep their audio.
    store_audio: bool = True


def meeting_hotkey_clash(modifiers: list[str], key: str, dictation: HotkeyConfig) -> str | None:
    """Why a meeting chord and the dictation hotkey cannot both exist, None when they can.

    The dictation listener fires on any chord that holds at least its modifiers (and its key), so a meeting
    chord that contains the dictation chord starts dictation as well.
    """
    chord, dictation_modifiers = set(modifiers), set(dictation.modifiers)
    if not dictation_modifiers <= chord:
        return None
    if dictation.key and normalize_hotkey_key(key) != dictation.key:
        return None
    if chord == dictation_modifiers and normalize_hotkey_key(key) == dictation.key:
        return "That is the dictation hotkey"
    return "That chord would also start dictation; change its modifiers or its key"


def validate_meeting_hotkey(modifiers: list[str], key: str, dictation: HotkeyConfig) -> str | None:
    """Error message for a meeting binding that is unusable or would also trigger dictation."""
    error = validate_hotkey(modifiers, key)
    if error is not None:
        return error
    if not normalize_hotkey_key(key):
        return "The meeting hotkey needs a key"
    if not modifiers:
        return "The meeting hotkey needs at least one modifier"
    return meeting_hotkey_clash(modifiers, key, dictation)


# Tried in order when a hand-edited file leaves no usable meeting hotkey.
_MEETING_HOTKEY_FALLBACKS = (
    (list(DEFAULT_MEETING_MODIFIERS), DEFAULT_MEETING_KEY),
    (list(DEFAULT_MEETING_MODIFIERS), "F10"),
    (list(LEGACY_MEETING_MODIFIERS), DEFAULT_MEETING_KEY),
    (list(LEGACY_MEETING_MODIFIERS), LEGACY_MEETING_KEY),
    (["ctrl", "alt", "win"], "M"),
)


def default_meeting_hotkey(dictation: HotkeyConfig) -> tuple[list[str], str]:
    """The default meeting hotkey, or the first fallback that does not clash with the dictation hotkey.

    When the listed fallbacks all clash, every function key on a few three-modifier chords is tried, so the
    result is valid whenever any such chord can be. Only a dictation hotkey made of all four modifiers and no
    key clashes with every chord; then the last candidate is returned.
    """
    candidates = list(_MEETING_HOTKEY_FALLBACKS)
    for modifiers in (["ctrl", "alt", "win"], ["ctrl", "shift", "alt"], ["ctrl", "shift", "win"]):
        candidates.extend((modifiers, f"F{n}") for n in range(12, 0, -1))
    for modifiers, key in candidates:
        if validate_meeting_hotkey(modifiers, key, dictation) is None:
            return list(modifiers), key
    return ["ctrl", "alt", "win"], "F12"
