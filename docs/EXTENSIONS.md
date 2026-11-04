# Extensions

NexusVox can load optional extension packages. An extension is an ordinary Python package that is
installed next to NexusVox (`pip install ...`) and announces itself through an entry point. With none
installed nothing changes: no tab, no export link, no extra step after an upload.

The interface lives in `nexusvox.extensions` and is part of the NexusVox core (PolyForm Noncommercial 1.0.0).

## Registering an extension

Declare an entry point in the `nexusvox.extensions` group in the extension's `pyproject.toml`:

```toml
[project.entry-points."nexusvox.extensions"]
my_extension = "my_package:MyExtension"
```

The entry point names an `Extension` subclass, an instance, or a function that takes no arguments and
returns one. NexusVox loads every extension once, when the app starts, and logs each one it loads.

```python
from nexusvox.extensions import Extension


class MyExtension(Extension):
    name = "my-extension"  # required, unique, shown in logs and results
    version = "1.0.0"
    api_version = 1  # the extension API this package was written against: a literal, see Versioning
```

**A broken extension never stops NexusVox.** One that cannot be imported, has no `name`, reuses a taken
name, targets another `api_version`, fails while registering its panels or targets, or raises inside a hook
is logged and skipped (a hook failure only skips that call).

## Starting without extensions (developers)

To test the plain Free app while an extension package stays installed (for example an editable install in
your development environment), set `NEXUSVOX_NO_EXTENSIONS=1` before starting NexusVox. No entry point of
the `nexusvox.extensions` group is loaded and the log says so once at start. Only the exact value `1`
counts; unset or any other value loads extensions as usual.

```powershell
$env:NEXUSVOX_NO_EXTENSIONS = "1"; python -m nexusvox
```

## Versioning

`nexusvox.extensions.API_VERSION` is an integer. It is bumped when a change breaks existing extensions
(a hook renamed, a field removed or retyped); adding a hook, an optional field or a keyword with a default
keeps it. The dictation, selected-text and command hooks below were added this way: the version stays at 1 and every extension built before them loads and behaves as before. NexusVox loads an extension only when its `api_version` equals the core's, so an extension built
for another version is skipped with a log line instead of misbehaving. Every extension must set
`api_version` itself, as a literal (`api_version = 1`, not `API_VERSION`): there is no default, and an extension
that does not declare one is skipped.

## Hook 1: after a file upload was transcribed

```python
def on_file_transcribed(self, transcript: FileTranscript) -> dict | None: ...
```

Called for every upload in the dashboard's Upload tab, and for every transcribed meeting recording, once the transcript is stored, with a read-only
`FileTranscript`:

| Field | Meaning |
|-------|---------|
| `id` | Row id in the `file_transcriptions` table |
| `text` | The transcript as transcribed (with `Speaker N:` paragraphs when speaker detection ran) |
| `cleaned_text`, `cleanup_level` | The LLM-cleaned text and its level, or `None` when cleanup was off or changed nothing |
| `best_text` | `cleaned_text` if there is one, else `text` |
| `language`, `duration_ms`, `model`, `original_filename`, `created_at` | As stored |
| `words` | Tuple of `Word(word, start, end, speaker)` (seconds); empty when the engine reported no timing |
| `kind` | `"upload"` for a file, `"meeting"` for a two-track meeting recording: `text` then has numbered `Speaker N:` paragraphs, like an upload with speaker detection (speaker 1 is the microphone and may carry the name set under `[meeting].own_voice_label` instead of `Speaker 1`; the system audio is speaker 2 and up), and `words` carry `speaker` 1, 2, ...; `speaker_names` maps a speaker number to the name used in `text` instead of `Speaker N` (a meeting stores `{1: <own voice name>}`; empty for an upload). Added later, without an `API_VERSION` change: a hook that ignores it keeps working |
| `title` | The name the user gave the row in the dashboard, or `None`; `original_filename` stays the file or meeting folder name. A hook that names its output after the row should prefer `title`. Added without an `API_VERSION` change, like `kind` |

The hook runs on the upload's worker thread, after the save: a slow hook delays the end of the job, a
failing one cannot lose the transcript. Return a JSON-serialisable `dict` to add it to the job result under
`extensions["<name>"]`; a `note` string in it is also shown as a note under the upload. Return `None` to add
nothing. An exception, or a return value that is not a JSON-serialisable dict, becomes the note
"Extension <name> failed: ...". The hook does not change the stored transcript; keep your own results
wherever you like.

## Hook 2: dashboard tabs

```python
def dashboard_panels(self) -> list[DashboardPanel]:
    return [DashboardPanel("my-tab", "My Tab", static_dir=Path(__file__).parent / "static", blueprint=bp)]
```

| `DashboardPanel` field | Meaning |
|------------------------|---------|
| `id` | Lower-case letters, digits, `-` and `_`; unique across all extensions (a taken id is skipped) |
| `title` | The tab's label |
| `static_dir` | Absolute path of an existing folder, served at `/ext/<id>/` (use `Path(__file__).parent / "static"`; a relative or missing one drops the panel) |
| `script` | File in `static_dir` the browser loads when the tab is first opened (default `panel.js`) |
| `blueprint` | Optional Flask `Blueprint`, mounted at `/api/ext/<id>/`: the tab's backend |

The script draws into its tab by registering a render function:

```js
NexusVoxExt.registerPanel("my-tab", (container) => {
  container.textContent = "Hello";
  fetch("/api/ext/my-tab/hello").then((r) => r.json()).then(console.log);
});
```

Every dashboard route, yours included, sits behind the core access check (Host, Origin and a session cookie). Same-origin `fetch("/api/ext/...")` from the panel script carries the cookie automatically, so an extension needs no change; requests from other programs or web pages get 403.

The dashboard is local-only (127.0.0.1) and has no CDN: ship what the tab needs in `static_dir`.
Blueprint names must be unique across the app; a blueprint that cannot be mounted is logged and the tab
stays without its backend.

Every tab, an extension's included, opens at the top of the page. A panel that links to a stored transcript (for example from a summary) calls `NexusVoxExt.showTranscript(id)` with the upload id as listed by `GET /api/file-transcriptions`: the dashboard opens the Meetings tab for a meeting and the Upload tab for anything else, scrolls to the card and highlights it. The call returns a promise that resolves with `false` when no such transcript exists.

## Hook 3: export targets

```python
def export_targets(self) -> list[ExportTarget]:
    return [ExportTarget("my-csv", "CSV", ".csv", "text/csv", self._to_csv)]
```

NexusVox itself ships the targets `txt`, `srt` and `vtt`; they are registered first, so an extension cannot reuse those ids. Each target adds a download link to every stored upload in the Upload tab. `export(transcript)` receives a
`FileTranscript` and returns `bytes` or `str`; the file is named after the uploaded file plus `extension`.
`id` follows the same rules as a panel id; `extension` must be a file name suffix such as `.docx` or `.tar.gz` (no slash, no spaces) and `mimetype` a media type, or the target is dropped. The link downloads through the page, so a failing export shows its message in the Upload tab instead of saving an error file. A failing export answers with a 500 and its message; nothing else
is affected. The link points at `GET /api/file-transcriptions/<id>/export/<target id>`.

## Listing what is loaded

`GET /api/extensions` (the built-in `txt`, `srt` and `vtt` exports are always in `exports`) returns `{api_version, extensions, panels, exports, upload_actions}` for the dashboard.

## Availability and upload actions

`ExportTarget.available` and the `upload_actions` hook are additions, not breaking changes: the API version
stays at 1 and extensions that do not use them behave as before. A NexusVox release that predates them ignores the extra field and never calls the hook.
and hook are simply ignored or rejected, so guard with the `nexusvox` version if you must support older ones.

### Marking an export unavailable

`ExportTarget` takes an optional `available(transcription_id) -> bool | str | None`. `True` or `None` offers
the link as usual; `False` (or any other falsy answer such as `0`) shows it greyed out and not clickable; a
string does the same and becomes the tooltip (e.g. `"No summary yet"`). The answers come with the upload list (`exports_unavailable`, a
`{target id: reason}` object per upload in `GET /api/file-transcriptions`), not one request per link, so keep
the check cheap: it runs once per listed upload (up to 50) for each target that has one. A check that raises leaves the link on, so the export itself reports the problem.

```python
ExportTarget(
    "summary",
    "Summary (Markdown)",
    ".md",
    "text/markdown",
    self._export,
    available=lambda tid: True if self._has_summary(tid) else "No summary yet",
)
```

### Hook 4: upload actions

```python
def upload_actions(self) -> list[UploadAction]:
    return [UploadAction("summarise", "Summarise", endpoint="/api/ext/my-tab/summarise/{id}")]
```

Each action adds a button to the result card of every stored upload in the Upload tab.

| `UploadAction` field | Meaning |
|----------------------|---------|
| `id` | Same rules as a panel id; unique across all extensions |
| `label` | The button's text |
| `endpoint` | An absolute path on this server (no scheme, host or backslash), e.g. a route of your panel's blueprint. The button `POST`s to it, every `{id}` replaced by the upload id. It answers JSON: `error` (or `"ok": false`) is shown as an error, `note` or `message` as a note. The list reloads afterwards, so a newly possible export becomes available |
| `panel` | Instead of `endpoint`: the id of one of the same extension's own panels (any other id drops the action). Its script, loaded on the first click, calls `NexusVoxExt.registerUploadAction("<action id>", (upload, button) => ...)`; `upload` is the upload as listed by `GET /api/file-transcriptions` |

Set exactly one of `endpoint` and `panel`; anything else drops the action.

`UploadAction` also takes an optional `available(transcription_id) -> bool | str | None`, with the same answers
as `ExportTarget.available`: `True` or `None` keeps the button, any other falsy answer greys it out, and a string
greys it out with that text as the tooltip. The answers come with the upload list (`actions_unavailable`, an
`{action id: reason}` object per upload), so a button never fails on click for a reason you already know. Keep
the check cheap; one that raises, or does not answer within the 3 second budget of a list, leaves the button on.

```python
UploadAction(
    "speakers",
    "Speakers",
    endpoint="/api/ext/my-tab/speakers/{id}",
    available=lambda tid: True if self._licensed() else "Needs a licence",
)
```

### Showing a pending or failed state

Besides the answers above, `available` may return a small dict. It works the same for `ExportTarget` and
`UploadAction`, and it is additive: the extension API version stays 1, and the old answers keep working.

| Answer | Shown on the row |
|---|---|
| `{"pending": "<label>"}` | The label with a spinner; the export or button stays greyed out. While any row is pending, the dashboard re-fetches the Meetings and Upload lists every few seconds and stops when none is, so a finished export becomes active without a page reload |
| `{"error": "<reason>", "retry": "<action id>"}` | The reason in red and a Retry button that runs the named `UploadAction`. `retry` must be an action your own extension registered; any other id is dropped with a log warning and only the reason is shown. Retry is also hidden while that action is itself greyed out for the row, and disabled while it runs |

```python
def _summary_state(self, tid: int):
    if self._summaries.has(tid):
        return True
    if self._jobs.running(tid):
        return {"pending": "Summarising…"}
    if (reason := self._jobs.failure(tid)) is not None:
        return {"error": reason, "retry": "summarise"}
    return "No summary yet"


ExportTarget("summary", "Summary (Markdown)", ".md", "text/markdown", self._export, available=self._summary_state)
UploadAction("summarise", "Summarise", endpoint="/api/ext/my-tab/summarise/{id}")
```

A dict with neither key greys the row out like `False`. The check is still called once per listed row on each
refresh, so keep it cheap. A refresh in which the check raises or is too slow keeps showing the last `pending`
answer, so a running job never looks finished because of one missed answer; a plain answer (or `True`) ends it.
The dashboard refreshes only the list that has a pending row, and only while the page and that tab are shown.

## Hook 5: change a dictation before it is typed

```python
def on_dictation(self, text: str, app: ActiveApp) -> str | None: ...
```

Called for every dictation that is about to be typed at the cursor, as the last step of the text pipeline:
the dictionary, voice commands, cleanup and translation are done. Return the
text to type instead, or `None` to leave it.

| `ActiveApp` field | Meaning |
|-------------------|---------|
| `process` | The focused app's executable file name, e.g. `Code.exe`; empty when it could not be read |
| `title` | The focused window's title; empty when it could not be read |

- **Order.** Extensions run in the order of their entry-point names (alphabetical); each sees the previous
  one's result.
- **Fallback.** An extension that raises, returns something that is not a string, or does not answer in time is logged and skipped (an `exit` call counts as a failure): the text stays as the step before it left it. All extensions share one budget of 3 seconds per dictation (`nexusvox.extensions.HOOK_TIMEOUT_S`). A timed-out hook keeps running in the background, its result is dropped, and it is not called again until it has returned.
- **Startup.** While the extensions are still loading right after NexusVox starts, a dictation is typed without them.
- **Empty text.** A dictation that types nothing (only "press enter") does not call the hook.
- **What is saved.** The stored transcript is the text as spoken; only what is typed (and what the paste key
  repeats) changes. An edit made by an extension keeps that dictation out of "learn from edits".
- **Not for the phone.** A remote-mic dictation is inserted by the phone, so there is no active app and the hook is not called.
- With no extension installed, dictation is exactly as before.

```python
class AppModes(Extension):
    name = "app-modes"
    api_version = 1

    def on_dictation(self, text, app):
        if app.process.lower() == "slack.exe":
            return text.rstrip(".")  # no full stop at the end of a chat message
        return None
```

## Hook 6: commands on the selected text

```python
def on_selection_command(self, selection: str, command: str, app: ActiveApp) -> str | None: ...
```

Runs on the polish key (`[hotkey].polish_key`): the user selects text, holds the hotkey, taps the polish key and
speaks a command. The core reads the selection (as for polish) and offers the selection and the spoken
`command` (what was said, possibly empty) to the extensions in entry-point order. The first one that returns a
string wins: the core pastes it over the selection, as polish does (Ctrl+Z in the app undoes it), saves the
recording as `[extension] <command>` and the paste key can repeat it. Return `None` for a command the extension
does not know; when no extension answers, the language-model polish runs as before.

The same rules as Hook 5 apply to errors (an extension that fails is skipped). The timeout differs: nobody types
during a selection command, so the hook gets 15 seconds (`SELECTION_COMMAND_TIMEOUT_S`, shared by all extensions)
instead of 3, enough for a local language model. An extension that is slower is skipped and the core polish runs. The core owns
everything around it: nothing selected, a terminal without `[polish].in_terminals`, a password field and a
focus change while the hook worked behave as for polish. Spoken commands must work in English and German; matching them is the extension's job.

```python
class Shout(Extension):
    name = "shout"
    api_version = 1

    def on_selection_command(self, selection, command, app):
        if command.strip().lower() in {"shout", "schrei"}:
            return selection.upper()
        return None
```

## Hook 7: change the text shown and exported for an upload

```python
def display_text(self, transcript: FileTranscript, text: str) -> str | None: ...
```

Called for the transcript texts the core shows or writes for a stored upload or meeting: the transcript and its
cleaned version in the Upload and Meetings lists, the text of the built-in TXT export, and every cue of the
built-in SRT and VTT exports (cue text includes its `Speaker N:` / `Me:` label). `transcript` is the stored row
as for Hook 1; *text* is the one text to change. Return the text to show instead, or `None` to leave it.

- **Stored data is untouched.** Only what is displayed or exported changes; `on_file_transcribed` and other
  extensions' export targets still receive the transcript as stored.
- **Order and fallback.** Extensions run in entry-point order, each seeing the previous result. All texts of one
  list row or one export are handled by an extension in a single call sequence on one helper thread. A text
  your hook raises on, answers with something that is not a string, or answers empty for a non-empty text keeps
  its wording. An extension that is not done in time is skipped for all texts of that call (never for some), and
  until it returns it is not called again, so exports in that window also write the raw text; this is logged.
  The budget is 3 seconds (`HOOK_TIMEOUT_S`) plus 10 ms per text, shared by the whole Upload list and per
  export.
- **Cheap.** The Upload list calls it for the transcript and the cleaned text of each of up to 50 rows, and an
  SRT export for every cue. Keep it a lookup, not a model call.
- **Subtitles.** A changed cue is re-wrapped to the subtitle line width (42 characters); an answer containing
  `-->`, or only whitespace, is dropped and the cue keeps its text. Line breaks you add are not kept.
- **Not covered.** Extension export targets build their own files from the stored transcript.

Added without an `API_VERSION` change: an extension without the hook loads and behaves as before.

```python
class SpeakerNames(Extension):
    name = "speaker-names"
    api_version = 1

    def display_text(self, transcript, text):
        names = self._names.get(transcript.id)  # {1: "Alice", 2: "Bob"}
        if not names:
            return None
        for number, name in names.items():
            text = text.replace(f"Speaker {number}:", f"{name}:")
        return text
```

## Hook 8: take over a spoken command

```python
def on_command(self, text: str, app: ActiveApp) -> tuple[str, bool] | None: ...
```

Called for every dictation before the text pipeline, as the first step of `App._run_command`: before the core's own
commands ("nexus assistant ...", the OS/window commands). The one exception is "nexus flag" (only with
`[experimental].flag_command`): the core handles it first and never offers it to the extensions. *text* is the
transcript as spoken, trimmed; no dictionary, voice command, cleanup or translation has touched it. Return `None`
when the phrase is not yours. Return `(label, opened)` to consume the dictation:

| Answer | What the core does |
|--------|--------------------|
| `None` | Nothing; the next extension is asked, then the core's own commands, then the text is typed as usual |
| `(label, True)` | Saves the dictation under *label* (e.g. `[nexus] open https://example.com`), types nothing |
| `(label, False)` | The same, and plays the error cue: the command was recognised but could not be carried out |

- **Order.** Extensions are asked in entry-point order; the first one that answers with a `(label, opened)` pair wins and the others are not called.
- **Label.** A non-empty string; it is what the dashboard history shows for the dictation. The spoken text is not stored.
- **Fallback.** An extension that raises, answers something that is not a `(non-empty str, bool)` pair, or is too slow is logged and skipped, and the dictation goes on and is typed: a broken extension never loses a dictation. That also means a hook that times out may already have carried out its action, and the phrase is then typed as well. So the hook must be quick: check the phrase, do an action that returns at once (`os.startfile` does), or start a thread of your own and answer `(label, True)`.
- **Budget.** `on_command` and `on_dictation` share the 3 seconds of one dictation (`HOOK_TIMEOUT_S`): what `on_command` takes is missing from `on_dictation`. An extension that does not override `on_command` is not called and costs nothing.
- **Startup.** While the extensions are still loading right after NexusVox starts, a dictation is not offered to any.
- **Not for the phone.** A remote-mic dictation is inserted by the phone and never runs a command; the hook is not called.
- **Speech.** Spoken commands must work in English and German; matching them is the extension's job.
- Added without an `API_VERSION` change: an extension without the hook loads and behaves as before. With no extension installed, dictation is exactly as before.

```python
class OpenSite(Extension):
    name = "open-site"
    api_version = 1

    def on_command(self, text, app):
        if text.strip().lower().rstrip(".") not in {"open my site", "öffne meine seite"}:
            return None
        os.startfile("https://example.com")  # hands the link to the default browser and returns at once
        return ("[open-site] https://example.com", True)
```

## Snippet matching helpers

The core no longer stores or expands snippets; that is an extension's job (the Pro extension does it). The pure
matcher stays importable from `nexusvox.snippets` for extensions that offer the same feature: `Snippet`,
`SnippetOption`, `mark_snippets(text, snippets) -> (text, expansions)`, `expand_snippets(text, expansions)`,
`placeholders_intact(text, expansions)` and `best_option(spoken, options)`. They are plain functions over plain
data (no database, no I/O). An extension calls `mark_snippets` in `on_dictation`, lets its own steps work on the
placeholders, and expands them again before it returns the text. Note that `on_dictation` runs after voice
commands, cleanup and translation, which the core used to protect an expansion from, and that it is not called for a
phone (remote-mic) dictation; an extension that needs either has to ask for a new hook. A database that still holds the `snippets` table of
an earlier NexusVox keeps it untouched; the core never reads or writes it.
