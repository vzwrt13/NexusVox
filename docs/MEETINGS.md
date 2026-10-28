# Meeting capture

NexusVox can record a meeting or call without a bot in it: it captures your microphone and the
system audio (what the other participants say) on your PC, transcribes both and gives you one
transcript with numbered speakers, like an upload with speaker detection. Everything stays on your machine; the audio goes only to the
transcription model you have configured (see the Requirements in the README).

Dictation keeps working while a meeting is recorded. The microphone also records what you dictate, so the
meeting would show it under your own voice; by default NexusVox leaves it out (see below).

## Record a meeting

- **Dashboard:** open the **Meetings** tab and press **Start recording**. A timer and a red indicator
  show that it runs. **Stop recording** ends it and starts the transcription; the progress shows in
  the same tab and the finished meeting appears under *Previous Meetings*.
- **Hotkey:** the meeting hotkey (default `Ctrl+Alt+M`, `[meeting]` in `config.toml`, also editable
  in the Meetings tab) starts and stops a meeting from anywhere. It is separate from the dictation
  hotkey and needs a key plus at least one modifier; a binding that would also start dictation is
  refused, and so is a dictation hotkey that the meeting hotkey would also start. While the consent hint is on, the hotkey opens the Meetings tab to start a meeting, so the
  hint is shown first; stopping always works straight from the hotkey. A short beep confirms start
  and stop, an error beep says it failed.

Each meeting is stored in its own folder `audio/meetings/meeting-YYYYMMDD-HHMMSS/` next to the
database: `mic.wav` and `system.wav` (the two tracks), `meeting.wav` (a playable mix, written after
the transcription) and `meeting.json`. If another file is still being transcribed when you stop, the
recording is kept in its folder, the dashboard says so and lists it under *Recorded, not yet transcribed*, where you can transcribe or delete it. The same list holds a meeting cut short because NexusVox quit, and one whose transcription failed. The hotkey beeps as for a normal stop.

Pick a microphone and an output device in the Meetings tab if the system defaults are not the ones you
use for the call. The output device is only *recorded*, never changed: choose the device the call is
played on.

## Live transcript

While a meeting records, the Meetings tab shows a live transcript: what you say under your own-voice label
(default `Speaker 1`), what everyone else says under `Others`, a few seconds behind the call. It is a preview. Both tracks are
cut into windows of about six seconds (at a pause where possible) and sent to the transcription model one
window at a time; silent windows are not sent. The live text cannot tell the other participants apart (so they are all `Others`; the stored transcript numbers them `Speaker 2`, `Speaker 3`, ...), and has no echo filter and no
dictation filter, so your microphone may repeat what the speakers play and your dictation shows up under your own-voice label.

The transcript made after **Stop** is the stored one and does not depend on the live view. If the model
cannot keep up, the live view skips audio instead of falling behind; the tab then says how much it skipped and
shows the share of time the model spent on live windows. It is **off by default**, because
its load during a whole meeting is not measured yet: switch it on with *Show a live transcript while recording*
in the Meetings tab (`[meeting].live_transcript`); the change applies from the next meeting.

## Speakers

Your microphone is **Speaker 1**. Set **Meetings: name for your own voice** in the Settings tab
(`[meeting].own_voice_label`) to use a name instead; empty means `Speaker 1`. The system audio runs through
the same speaker detection as uploads (Parakeet in its Docker container, up to four voices) and becomes
**Speaker 2**, **Speaker 3**, ... If speaker detection is unavailable (CPU mode, no GPU container, another
model) or fails, everyone on the system audio is **Speaker 2** and the transcript still completes; a note says
why. The labels are stored the same way as for uploads, so the Meetings and Upload tabs, TXT, SRT and VTT
exports and extensions all show the same names (the name of your own voice is stored with the meeting, so it
cannot get lost in an edit of the text). A name such as `Speaker 2` is refused, since another participant gets
that label. Speaker detection sends the whole system audio in one request, so a meeting longer than two hours
skips it and shows everyone on the system audio as Speaker 2, with a note. The setting is read when a meeting is transcribed: *Transcribe*
a meeting recorded earlier to get the new labels; nothing is changed automatically.

## Leave dictation out of the transcript

While a meeting records, NexusVox stores the start and end of every dictation take in `meeting.json`
(`dictations`, seconds from the meeting start), whatever the setting. The **Leave dictation out of the
meeting transcript** switch in the Meetings tab (`[meeting].leave_dictation_out`, on by default) decides
what the transcription does with them: on, the words of your own voice that fall inside a take (plus half a second
on each side) are dropped; off, they stay. The switch is read when the meeting is transcribed, so
*Transcribe* on a meeting recorded earlier uses the current setting. `mic.wav`, `system.wav` and
`meeting.wav` are never changed. If the speech engine returns no word timing, nothing can be removed and
the transcript says so in a note.

## Consent: tell everyone before you record

**Inform all participants before you record.** In Germany, recording the spoken word of others
without their consent can be a criminal offence (§ 201 StGB, violation of the confidentiality of the
spoken word), and other countries have comparable rules, some of them requiring every party to agree.
This is not legal advice; you are responsible for using the feature lawfully.

NexusVox shows this reminder before each recording from the dashboard. *Don't show this again* (or the
*Show the consent hint* toggle in the Meetings tab, `[meeting].consent_hint`) turns it off.

## Quality: use a headset

- **Headset or earphones.** With speakers, the microphone also picks up the other participants and
  your transcript gets their words twice. NexusVox removes runs of such echo from your own voice, but a headset
  avoids it at the source and gives clean separation.
- **Bluetooth headsets drop in quality** while the microphone is in use: Windows switches them to the
  hands-free profile, which is narrow-band and sounds muffled, and the transcription suffers. Use the
  PC's own microphone, a wired headset or a USB headset for meetings, or a Bluetooth headset together
  with another microphone.
- Silence on the system track is normal: Windows delivers no audio while nothing plays, and NexusVox
  pads it so both tracks stay as long as the meeting.

## Delete a meeting

Every entry in the Upload history and every meeting in the Meetings tab has a **Delete** button with a
confirmation in the page. It removes the entry and its audio: for an upload its stored audio, for a
meeting the whole folder (`mic.wav`, `system.wav`, `meeting.wav`, `meeting.json`).
