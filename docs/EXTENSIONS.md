# Extensions

NexusVox can load optional extension packages. An extension is an ordinary Python package that is
installed next to NexusVox (`pip install ...`) and announces itself through an entry point. With none
installed nothing changes: no tab, no export link, no extra step after an upload.

The interface lives in `nexusvox.extensions` and is part of the NexusVox core (AGPL-3.0-or-later).

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

## Versioning

`nexusvox.extensions.API_VERSION` is an integer. It is bumped when a change breaks existing extensions
(a hook renamed, a field removed or retyped); adding a hook, an optional field or a keyword with a default
keeps it. NexusVox loads an extension only when its `api_version` equals the core's, so an extension built
for another version is skipped with a log line instead of misbehaving. Every extension must set
`api_version` itself, as a literal (`api_version = 1`, not `API_VERSION`): there is no default, and an extension
that does not declare one is skipped.

## Hook 1: after a file upload was transcribed

```python
def on_file_transcribed(self, transcript: FileTranscript) -> dict | None: ...
```

Called for every upload in the dashboard's Upload tab once the transcript is stored, with a read-only
`FileTranscript`:

| Field | Meaning |
|-------|---------|
| `id` | Row id in the `file_transcriptions` table |
| `text` | The transcript as transcribed (with `Speaker N:` paragraphs when speaker detection ran) |
| `cleaned_text`, `cleanup_level` | The LLM-cleaned text and its level, or `None` when cleanup was off or changed nothing |
| `best_text` | `cleaned_text` if there is one, else `text` |
| `language`, `duration_ms`, `model`, `original_filename`, `created_at` | As stored |
| `words` | Tuple of `Word(word, start, end, speaker)` (seconds); empty when the engine reported no timing |

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

The dashboard is local-only (127.0.0.1) and has no CDN: ship what the tab needs in `static_dir`.
Blueprint names must be unique across the app; a blueprint that cannot be mounted is logged and the tab
stays without its backend.

## Hook 3: export targets

```python
def export_targets(self) -> list[ExportTarget]:
    return [ExportTarget("my-csv", "CSV", ".csv", "text/csv", self._to_csv)]
```

Each target adds a download link to every stored upload in the Upload tab. `export(transcript)` receives a
`FileTranscript` and returns `bytes` or `str`; the file is named after the uploaded file plus `extension`.
`id` follows the same rules as a panel id; `extension` must be a file name suffix such as `.docx` or `.tar.gz` (no slash, no spaces) and `mimetype` a media type, or the target is dropped. The link downloads through the page, so a failing export shows its message in the Upload tab instead of saving an error file. A failing export answers with a 500 and its message; nothing else
is affected. The link points at `GET /api/file-transcriptions/<id>/export/<target id>`.

## Listing what is loaded

`GET /api/extensions` returns `{api_version, extensions, panels, exports}` for the dashboard.
