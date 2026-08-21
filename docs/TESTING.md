# NexusVox Testing Guide

A beginner-friendly guide to understanding, running, and writing tests for NexusVox.

## What Are Tests and Why Do They Matter?

Tests are small programs that check whether your code works correctly. Each test calls a function with known inputs and verifies the output matches what you expect.

**Why bother?**

- **Catch bugs early** — A test fails immediately when something breaks, before it reaches users.
- **Refactor safely** — When you change code, tests tell you if you accidentally broke something.
- **Document behavior** — Tests show exactly how each function is supposed to work, with real examples.
- **CI guardrail** — Our GitHub Actions pipeline runs the suite on every push and pull request, across Linux and Windows on Python 3.11–3.13. A failing job shows up as a red check on the PR; treat it as blocking even though nothing enforces that mechanically yet.

## Quick Start

```bash
# Run all tests (from the project root)
pytest

# Run with verbose output (shows each test name)
pytest -v

# Stop on first failure (useful when debugging)
pytest -x

# Run a specific test file
pytest tests/test_config.py

# Run a single test function
pytest tests/test_config.py::test_save_config_roundtrip

# Combine: verbose + stop on first failure
pytest -v -x
```

## Reading Test Output

When you run `pytest -v`, you'll see output like this:

```
tests/test_config.py::test_config_dataclass_defaults PASSED
tests/test_config.py::test_load_config_from_valid_toml PASSED
tests/test_db.py::test_flag_nonexistent_id PASSED
```

- **PASSED** (green) — The test worked as expected.
- **FAILED** (red) — Something went wrong. pytest shows you exactly what happened:

```
FAILED tests/test_db.py::test_flag_nonexistent_id
    assert db.flag_transcription(9999) is True
AssertionError: assert False is True
```

This tells you: the function returned `False` when the test expected `True`. The file, line number, and actual vs expected values are all shown.

- **SKIPPED** (yellow) — The test was intentionally skipped (e.g., a required library isn't installed).
- **ERROR** — The test couldn't even run (usually an import error or broken fixture).

### The summary line

```
145 passed in 0.28s
```

Warnings are usually harmless deprecation notices. Focus on passed/failed counts.

## Test File Structure

```
tests/
├── conftest.py                    # Shared fixtures (reusable setup code)
├── test_transcription_result.py   # TranscriptionResult: text + confidence math
├── test_config.py                 # Config loading, saving, defaults
├── test_lang_detect.py            # Language detection (en/de/unknown)
├── test_db.py                     # Database CRUD operations
├── test_analytics.py              # Dashboard analytics queries
├── test_dashboard_api.py          # Flask API endpoint integration tests
├── test_transcriber.py            # Transcriber ABC, factory, and implementations
├── test_docker_ctl.py             # Docker profile start/stop and health checks
├── test_file_transcribe.py        # Audio file upload conversion and transcription
├── test_os_commands.py            # Nexus OS command parsing and dispatch
└── test_voice_commands.py         # Voice command text formatting substitutions
```

### What each file tests

| File | Source Module | What It Verifies |
|------|-------------|-----------------|
| `test_transcription_result.py` | `transcriber.py` | Text accumulation from streaming deltas, confidence calculation from logprobs |
| `test_config.py` | `config.py` | TOML loading with missing files/sections/keys, save/load roundtrip, default values |
| `test_lang_detect.py` | `lang_detect.py` | English detection, German detection, unknown for unsupported languages |
| `test_db.py` | `db.py` | Save, retrieve, flag, correct transcriptions; empty database edge cases |
| `test_analytics.py` | `dashboard/analytics.py` | Overview stats, word counting, time grouping, heatmap shape, date filtering |
| `test_dashboard_api.py` | `dashboard/__init__.py` | All `/api/*` HTTP endpoints return correct status codes and JSON shape |
| `test_transcriber.py` | `transcriber.py` | BaseTranscriber ABC, factory function, VoxtralRealtimeTranscriber, OpenAIHttpTranscriber |
| `test_docker_ctl.py` | `docker_ctl.py` | Docker profile start/stop, health-check waiting |
| `test_file_transcribe.py` | `file_transcribe.py` | Extension detection, WAV passthrough, audio conversion, chunking |
| `test_os_commands.py` | `os_commands.py` | Nexus command parsing, action dispatch, unknown app handling |
| `test_voice_commands.py` | `voice_commands.py` | New line/paragraph/tab/all-caps substitutions, comma interactions, passthrough |

### What we DON'T test (and why)

These modules depend on Windows APIs or real hardware and can't run in a test environment:

- `injector.py` — Win32 SendInput, clipboard access
- `hotkey.py` — pynput keyboard hooks
- `audio.py` — sounddevice microphone capture
- `tray.py` — pystray system tray
- `feedback.py` — sounddevice audio output
- `app.py` — orchestrates all of the above

## Key Concepts

### Fixtures (`conftest.py`)

Fixtures are reusable setup functions. Instead of repeating database setup in every test, you define it once as a fixture:

```python
@pytest.fixture
def db():
    """Fresh in-memory SQLite database for each test."""
    config = DatabaseConfig(path=":memory:")
    return Database(config)
```

Any test that needs a database just adds `db` as a parameter:

```python
def test_save_transcription(db):  # pytest automatically provides `db`
    record = db.save_transcription("hello", "en", 1000)
    assert record.text == "hello"
```

pytest sees the parameter name `db`, finds the matching fixture, runs it, and passes the result. No manual setup needed.

**Our fixtures:**

| Fixture | What It Provides |
|---------|-----------------|
| `db` | Fresh in-memory SQLite `Database` instance |
| `session_factory` | SQLAlchemy session factory (for analytics functions) |
| `sample_transcriptions` | Pre-populated database with 8 varied records |
| `flask_client` | Flask test client for HTTP endpoint testing |

### In-Memory SQLite

Instead of creating a real `.db` file, we use `sqlite:///:memory:`. This means:

- **Isolated** — Each test gets a completely empty database. Tests can't interfere with each other.
- **Fast** — No disk I/O, everything lives in RAM.
- **No cleanup** — The database disappears when the test ends. No leftover files.

### Flask Test Client

Flask provides a built-in test client that simulates HTTP requests without starting a real server:

```python
def test_get_settings(flask_client):
    resp = flask_client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "language" in data
```

No port binding, no threading, no network. Just fast, deterministic tests.

### `monkeypatch`

A built-in pytest fixture that lets you temporarily replace things during a test:

```python
def test_load_config_defaults_when_no_file(tmp_path, monkeypatch):
    # Make EXAMPLE_CONFIG_PATH point to a nonexistent file
    monkeypatch.setattr(config_module, "EXAMPLE_CONFIG_PATH", tmp_path / "nope.toml")
    cfg = load_config(tmp_path / "config.toml")
    assert cfg.language == "en"  # Falls back to default
```

The change is automatically undone after the test finishes.

### `pytest.approx`

For floating-point comparisons where exact equality might fail due to rounding:

```python
assert result.confidence == pytest.approx(0.2231, rel=1e-4)
```

This passes if the values are within 0.01% of each other.

### `pytest.importorskip`

Skips tests gracefully when a library isn't installed:

```python
lingua = pytest.importorskip("lingua", reason="lingua-language-detector not installed")
```

If `lingua` can't be imported, the entire test file is skipped (not failed).

## How to Write a New Test

### Step 1: Decide where it goes

- Testing a new function in `db.py`? Add to `tests/test_db.py`.
- Testing a new analytics query? Add to `tests/test_analytics.py`.
- Testing a completely new module? Create `tests/test_<module_name>.py`.

### Step 2: Write the test function

Every test function must start with `test_`:

```python
def test_my_new_feature(db):
    """Describe what this test verifies."""
    # Arrange — set up the data
    db.save_transcription("hello world", "en", 2000)

    # Act — call the function you're testing
    result = db.get_recent(limit=1)

    # Assert — check the result
    assert len(result) == 1
    assert result[0].text == "hello world"
```

The **Arrange-Act-Assert** pattern keeps tests readable:
1. **Arrange** — Set up whatever data or state the test needs.
2. **Act** — Call the function under test.
3. **Assert** — Verify the result is correct.

### Step 3: Run it

```bash
pytest tests/test_db.py::test_my_new_feature -v
```

### Step 4: Lint it

```bash
ruff check tests/
ruff format tests/
```

## Running Tests in CI

Tests run automatically on every push and pull request via GitHub Actions (`.github/workflows/ci.yml`). The pipeline:

1. Installs Python and dependencies (`pip install -e ".[dev]"`)
2. Runs `ruff check src/` and `ruff format --check src/`
3. Runs `pytest`

If any step fails, the CI check goes red on your PR. Fix the issue and push again.

## Troubleshooting

### "ModuleNotFoundError: No module named 'nexusvox'"

Install the package in development mode:

```bash
pip install -e ".[dev]"
```

### "1 skipped" for lang_detect tests

This is normal if `lingua-language-detector` isn't installed. Install it to run those tests:

```bash
pip install lingua-language-detector
```

### Tests pass locally but fail in CI

Check if the CI environment has all dependencies. Look at the GitHub Actions log for the exact error message.
