# NexusVox — Dashboard

Rules and pointers only. How the server, the tabs and the endpoints work is in `docs/internals/dashboard.md` and `docs/internals/dashboard-api.md`. Module paths are relative to `src/nexusvox/dashboard/`; paths that start with `tests/` or `docs/` are from the repository root.

## Server And Access

- Flask on `127.0.0.1:47392` in a daemon thread, started on the first tray click; if the port is taken `_bind_server` falls back to `_FALLBACK_PORTS` (47400-47409; 47393 is the remote mic). Static files are bundled (Chart.js, Geist fonts): no CDN, the dashboard works offline.
- `guard.py` installs one `before_request` hook first in `_create_app`, so every route, extension blueprints included, checks Host, Origin, `Sec-Fetch-Site` and the session cookie. Never weaken it or register a route before it. Tests: `tests/test_dashboard_guard.py`; `tests/conftest.py` makes every `app.test_client()` signed in.
- A route reads its JSON body through `_json_object` and a `{enabled}` switch through `_switch` (both in `routes_base.py`); `tests/test_dashboard_routes.py` fails on a route that parses the body itself. Routes go straight on the app with `@app.route`, never a blueprint (it renames endpoints).
- A route behind an `[experimental]` flag is not registered unless the flag is on (read from `api.experimental` at start).

## Module Layout

- `analytics.py` is the one import path of the query functions in `analytics_*.py` (`overview`, `usage`, `accuracy`, `review`, `common`). A new public query goes into its topic module and is re-exported; `tests/test_analytics_exports.py` pins the names.
- `api.py` joins the topic mixins `api_*.py` on `DashboardAPI`; `api_base.py` holds `DashboardState`, which declares what a mixin takes from another. Declare new cross-mixin use there: `tests/test_dashboard_api_contract.py` fails otherwise.
- `routes_*.py` match the mixins. Each has `register(app, api)` and is listed in `_ROUTE_MODULES`; `__init__.py` keeps `_create_app`, `/`, `/api/features` and the server code, because tests patch `_make_server`, `_PORT` and `show_message` there.
- Patch a name in the module that uses it. Names several modules import have a copy in each: use the `no_config_save` fixture for `save_config` and `patch_api(name, value)` for `cuda_available` and `convert_to_wav`.
- `DashboardAPI(extensions=)` defaults to an empty registry, so tests never see installed packages. The database of a test that runs a file job must be a file, not `:memory:`.

## Page Rules

- The JavaScript is one file per tab plus shared helpers, flat in `static/` (`pyproject.toml` packages `dashboard/static/*`). `index.html` loads them in a fixed order, as classic scripts in one global scope; top-level code may use only what its own or an earlier file defines. `tests/test_dashboard_scripts.py` pins the order; tests read the files through `tests/dashboard_js.py`; `scripts.py` (`page_scripts`) is the one reader of the script tags.
- Colour: green = works, yellow = works with a caveat, red = does not work, none = neutral. Set it only through `setStatus(el, state, text)`; never invent a colour or write `status-line` classes by hand. `style.css` tokens are the single place for colours (`tests/test_status_colours.py`).
- No sideways scrolling from 360px up on every tab: wrap rows, use `max-width: 100%` / `min-width: 0`, put wide tables in `.table-wrap`, and leave `overflow-wrap: anywhere` on `main`. There are no UI tests: check in headless Edge at 360, 480, 640, 800 and 1280px with deliberately long names.
- Insert user text with `textContent` or `escapeHtml`. Confirm in the card, never with `confirm()` or another browser dialog.
- Every setting control keeps its element id. A new setting goes into the Settings group a user would look in; only rarely used ones go into Advanced. Settings apply live; only the compute device needs a restart.
- Strings in the page are English (root `CLAUDE.md`); German voice-command aliases are product vocabulary, not UI text.
- `.tab`, `.tab-content` and `data-tab` are what `activateTab` keys on; extension panels register through `NexusVoxExt`.

## Reference

| Topic | File |
|-------|------|
| Server, modules, look, every tab | `docs/internals/dashboard.md` |
| Endpoint list | `docs/internals/dashboard-api.md` |
| Extension contract | `docs/EXTENSIONS.md`, `docs/internals/extensions.md` |
| Docker orchestration (`system/docker_ctl.py`) | `docs/internals/transcription.md` |
