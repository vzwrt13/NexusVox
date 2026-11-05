# Hotkey modes: Hold vs Toggle

## How to end a recording

| Action | Hold (push-to-talk) | Toggle |
|---|---|---|
| Start | Hold the hotkey | Press and release the hotkey once |
| End normally (paste as is) | Release the hotkey | Press the hotkey again |
| End with Polish (rewrites the **selected text**; what you say is the instruction, e.g. "make it shorter") | Select text first. Press the polish key (e.g. `P`) at any moment while holding, then release | Select text first. Hotkey + polish key: stops the recording and rewrites the selection by what you said |
| Paste the last text again | Paste key (`V`) while holding | Hotkey + `V` |
| Command (`[os_control]`) | Command key while holding | Hotkey + command key |
| Marker already held at the start | Counts | Counts, also when you end with the plain hotkey |
| Escape during a recording | Cancels: nothing is pasted, the mic guard is released, the overlay closes | Same |
| Escape with no recording | Reaches the app unchanged | Same |
| Another key joins the chord (Ctrl+Win+Alt+letter, Ctrl+Win+Left/Right) | Discards the recording at once: nothing is transcribed or pasted, the mic guard is released, the overlay closes. The key still reaches the app. The start chime and overlay wait 150 ms (`START_CUE_DELAY_S`), so an immediate discard is silent; audio is captured from the first moment | Unchanged: keys are up while recording, other typing is legitimate |
| After `[hotkey].max_recording_s` (default 600 s) | Stops; what was said so far is processed normally (log line) | Same |

Toggle with a modifier-only binding (the default Ctrl+Win): after the stop chord NexusVox waits about 300 ms
for a marker key before it finishes, so "hotkey + `P`" works without a letter key in the binding. A take under
300 ms in Toggle is dropped with the stop cue (measured to the stop chord, not to the end of the grace window).
Only a plain Escape cancels: Ctrl+Esc, Alt+Esc and Ctrl+Shift+Esc reach Windows (in Hold your own hotkey
modifiers may be down). The cap does not apply to phone recordings, which the phone ends itself. A hotkey press while a phone recording runs plays the error cue.
Paste-last and a polish tap (hotkey held under about 0.6 s, nothing said) also work while the speech server
is unreachable; the error cue plays at once. A polish held longer carried a spoken instruction that cannot be
read without the server, so the selection is left alone (Toggle needs the marker already held).

## Behaviour in detail (state before this change; the gaps below are fixed unless noted)

How the dictation hotkey behaves today in each mode, read from the code (no behaviour was
changed or run for this document; "tested" below means an existing unit test, not a manual try).
The last section lists gaps and a proposed target for each.

Code paths: `hotkey.py` (`HotkeyListener`, runs inside the keyboard hook), `app.py`
(`_on_hotkey_activate/deactivate`, `_transcription_cycle`, `_polish_selection`,
`_paste_last_text`), `mic_guard.py`. Tests: `tests/test_hotkey.py`.

## Vocabulary

- **Binding**: `[hotkey].modifiers` plus an optional `[hotkey].key`. *Modifier-only*
  (default Ctrl+Win) completes on its last modifier going down; with a key, the key
  must come last.
- **Chord press**: the event that completes the binding (`_complete_press`, `hotkey.py:130`).
- **Marker keys**: a key pressed during a recording while every binding modifier is down
  turns the recording into something else (`_handle_marker_key`, `hotkey.py:160`): command
  key (`[os_control]`), paste key (`[hotkey].paste_key`, default `V`), polish key
  (`[hotkey].polish_key`, default off). The first marker wins; the key is swallowed.
- **Menu keys**: with Alt or Win in the binding, a neutral mask key is typed when the chord completes (and for a
  genuinely new Alt/Win press, not for an auto-repeat of a held one), so releasing Ctrl/Shift first leaves no lone
  Alt/Win release. The injector's own modifier release (`_release_all_modifiers`) sends key-ups only for modifiers that
  are still down; a key-up for an Alt that is already up would toggle Notepad's menu bar.

## What starts and stops a recording

| | Hold | Toggle |
|---|---|---|
| Start | Chord press (`hotkey.py:137-140`) | Chord press (same) |
| Stop | First key of the binding going up (`_combo_broken`, `hotkey.py:178`) | Next chord press (`hotkey.py:133-136`); releasing keys does nothing |
| Second press while recording | Not reachable (keys are already down) | Stops the recording |
| Press during transcription | Starts the next take, queued behind the running cycle (`app.py:211-223`, `1051-1062`) | Same; the "held again" overlay check uses `active`, which is true for the whole toggle recording (`app.py:696`) |
| Very short press (stop arrives before the server connection is up) | Cycle sees the stop event, logs "released before recording could start", records nothing, no beep (`app.py:492-505`) | Same path (start press + stop press both before the connection is up) |
| Short press after recording began | Tiny audio goes to the transcriber; an empty transcript is dropped silently (`app.py:592-595`) | Same |
| Escape | Not handled anywhere (no `Escape` in `src/` outside the dashboard JS) | Same |
| Key swallowing | Binding key dead for the whole hold, even after modifiers go up | Binding key types again as soon as it is released |
| Mic guard | Mutes on press, restores on release; watchdog re-arms while keys are physically down (`still_recording`, `hotkey.py:270`) | Mutes on start, restores on the stop press; watchdog re-arms while `_active` is true, i.e. forever |
| Server unreachable at start | `connect()` raises, error cue, `cancel()` clears the intent (`app.py:674-687`) | Same; without `cancel()` the next press would count as a stop |
| Mode changed mid-recording | hold to toggle: keeps recording until the next press | toggle to hold: stops on the next key release |

## What the recording turns into

| Recording kind | How it is chosen | Hold | Toggle | Code |
|---|---|---|---|---|
| Plain dictation | No marker | Transcript goes through nexus commands, assistant, dictionary, voice commands, cleanup, translation, paste | Identical after the stop | `app.py:592-672` |
| Paste last | Paste key during the recording | Press chord, speak or not, tap `V` while holding, release: recording dropped, last text pasted again. A quick tap of the chord with `V` pastes too | Press chord (keys up), re-hold the modifiers, press `V`: with a key binding this works; with a **modifier-only** binding, re-pressing all modifiers is a chord press and *stops* the recording first, so `V` is no longer a marker (it types `Ctrl+Win+V` into the app). Only a `V` pressed during the *starting* hold works | `hotkey.py:160-176`, `app.py:556-561`, `793-806` |
| Polish | Polish key during the recording | Hold chord, speak the instruction, tap the polish key, release. Selection is read after the transcript arrives, rewritten, pasted over the selection | Same marker rule as above: with a key binding, start, speak, re-hold the modifiers + polish key, then press the chord to stop. With a modifier-only binding the polish key has to be pressed in the starting hold, before speaking | `app.py:585-590`, `808-896` |
| Command (`[os_control]`) | Command key during the recording | Same as polish, but `::show` goes out at the key press, the transcript goes to the Controller, `::hide` on empty or failed | Tested in `tests/test_hotkey.py:294` only for the "still holding" case; same modifier-only restriction | `app.py:282-298`, `570-583` |
| Voice commands (`new line`, symbols, numbers, press enter) | Spoken words, no hotkey involvement | Applied in the pipeline | Identical | `pipeline.process_text` |
| Extension command / OS / assistant / flag | Spoken, no hotkey involvement | Run instead of typing | Identical | `app.py:600-610` |
| Meeting recording | Separate `MeetingHotkey` (its own listener, always toggle, exact modifier match) | n/a | n/a | `hotkey.py:305-367`, `app.py:234-259` |

### Polish specifics, same in both modes

Outcome table (all after the stop): nothing selected, terminal without `in_terminals`, model
failure, focus moved, clipboard refused each give the error cue and leave the selection
alone; success pastes over the selection (`app.py:808-896`). A quick tap of the polish chord
(stop before the recording started) polishes with the default instruction
(`app.py:501-504`). Polish and paste key equal in the config: polish is dropped on load.

## Gaps, inconsistencies and bugs, with a proposed target

Status: items 1-7 and 9 are implemented (grace window and marker-ends-toggle, Escape in `hotkey.py`;
`max_recording_s`, the short-take cue, the offline marker path, the per-stop marker snapshot `App._stops` and
the phone-block cue in `app.py`). Item 8 is explained in the dashboard hint. Not done: Escape for the meeting
hotkey, and a stronger overlay cue while a Toggle recording runs.

Severity is a judgement from the code only; none was reproduced on a machine.

1. **Toggle plus a modifier-only binding makes markers awkward (inconsistency).** The marker
   rule requires every binding modifier down (`hotkey.py:172`). In toggle mode the keys are
   up while recording, and pressing them again is a chord press that stops it. So paste,
   polish and command markers only work if pressed in the same hold that started the
   recording, which is before the user has spoken. With a key binding it works.
   *Target:* in toggle mode, a marker should be accepted whenever the modifiers are down,
   and a modifier-only re-press that is followed by a marker within a short grace window
   (~300 ms) should not stop the recording; or, simpler, document that toggle needs a binding
   with a key for markers. Recommended: the grace window, as the cheaper user-visible fix.
2. **Toggle needs three steps for a polish instruction (usability).** Start chord, speak,
   marker chord, stop chord. *Target:* in toggle mode, the marker key itself also stops the
   recording (marker = "mark and finish"), matching the one-gesture hold flow. Recommended.
3. **No cancel (gap, both modes).** Escape does nothing. A hold recording can only be
   abandoned by speaking nothing; a toggle recording only by stopping it, which transcribes
   and pastes. *Target:* Escape during a recording drops it without transcribing and plays
   a short cue, swallowing only that Escape. Needs a decision, since a global Escape hook
   must not eat Escape presses when no recording runs.
4. **Forgotten toggle recording never ends (bug).** There is no maximum length, and the mic
   guard watchdog only checks `_active` in toggle mode (`hotkey.py:270-275`), so other apps
   stay muted indefinitely and audio keeps buffering in memory. *Target:* an optional
   `[hotkey].max_recording_s` (default several minutes) that stops the recording like a
   press, and a stronger overlay cue while a toggle recording runs.
5. **Short presses are silent (inconsistency).** A press released before the recording
   starts, or an empty transcript, gives no cue, unlike every other failure. For a plain
   dictation that is arguably right in hold, but in toggle an accidental double press looks
   like "nothing happened". *Target:* no change for hold; for toggle, a brief neutral cue
   when a take under a minimum length (e.g. 300 ms) is dropped.
6. **Paste and polish do nothing when the server is unreachable (known gap).** The cycle
   connects before it records (`app.py:486`), so a failed connect hits the generic error
   path and the marker is never read. Polish does not need the transcriber when no
   instruction is spoken. *Target:* read the marker before connecting on the failure path
   and run paste-last (and a default-instruction polish) without a server.
7. **`mark` is shared state read after the stop (race, low risk).** `_mark` is reset on the
   next chord press (`hotkey.py:139`), but the cycle reads it after the stop event
   (`app.py:533`). A very fast stop then start could read the next take's reset mark.
   *Target:* capture the mark at the stop callback and hand it to the cycle with the event.
8. **Toggle state can desync after a mode switch or a failed cycle (low).** `cancel()`
   covers failed cycles; a switch toggle to hold mid-recording stops on the next key
   release, which is not obvious from the dashboard. *Target:* show the mode-switch effect
   in the dashboard hint text; no code change.
9. **Phone recording blocks the hotkey silently (design note).** While `[remote_mic]` is
   recording, a chord press is dropped and cancelled (`app.py:213-218`) with no cue.
   *Target:* error cue, so the press is not mistaken for a broken hotkey.

## Open questions for the maintainer

- Is "marker finishes the recording" (item 2) what you want in toggle, or should the stop
  always be an explicit chord press?
- Should Escape cancel in both modes, or only in toggle?
- Default for `max_recording_s` (item 4)?
