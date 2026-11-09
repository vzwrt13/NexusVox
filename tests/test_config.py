"""Tests for config loading, saving, and default values."""

from __future__ import annotations

import dataclasses
import logging
import tomllib
from pathlib import Path

import pytest

import nexusvox.config as config_module
from nexusvox.config import (
    EXAMPLE_CONFIG_PATH,
    AudioConfig,
    CleanupConfig,
    Config,
    DatabaseConfig,
    HotkeyConfig,
    InferenceConfig,
    LearnFromEditsConfig,
    LlmConfig,
    LlmServer,
    MeetingConfig,
    OSCommandsConfig,
    PolishConfig,
    VoiceCommandsConfig,
    load_config,
    resolve_compute_type,
    resolve_device,
    save_config,
    validate_hotkey,
)


def test_example_config_ships_inside_the_package():
    """The template must resolve next to the module, not relative to a checkout.

    A path walking up to the repository root works in a source tree and breaks in
    an installed wheel, where it silently leaves the user with no config.toml at
    all. Assert the layout the packaging depends on.
    """
    import nexusvox

    package_dir = Path(nexusvox.__file__).resolve().parent
    assert EXAMPLE_CONFIG_PATH.exists(), "config.example.toml is missing from the package"
    assert EXAMPLE_CONFIG_PATH.parent == package_dir, (
        f"config.example.toml must live inside the nexusvox package ({package_dir}), "
        f"but EXAMPLE_CONFIG_PATH points at {EXAMPLE_CONFIG_PATH.parent}"
    )


def test_config_dataclass_defaults():
    cfg = Config()
    assert cfg.injection_delay_ms == 500
    assert cfg.voice_commands.enabled is True
    assert "slash" in cfg.voice_commands.symbols
    assert "plus" not in cfg.voice_commands.symbols
    assert cfg.hotkey.modifiers == ["ctrl", "win"]
    assert cfg.hotkey.mode == "hold"
    assert cfg.audio.sample_rate == 16_000
    assert cfg.audio.chunk_size == 4096
    assert cfg.inference.server_url == "http://localhost:8002/v1/audio/transcriptions"
    assert cfg.inference.transcription_delay_ms == 480
    assert cfg.inference.model == "parakeet-tdt-0.6b"
    assert cfg.database.path == "nexusvox.db"
    assert cfg.database.audio_dir == "audio"
    assert cfg.os_commands.enabled is False
    assert cfg.os_commands.apps == {}


def test_load_config_defaults_when_no_file(tmp_path, monkeypatch):
    """When neither config nor example exist, return all defaults."""
    monkeypatch.setattr(config_module, "EXAMPLE_CONFIG_PATH", tmp_path / "nonexistent.toml")

    cfg = load_config(tmp_path / "config.toml")

    assert cfg.audio.sample_rate == 16_000
    assert cfg.hotkey.modifiers == ["ctrl", "win"]


def test_load_config_copies_example(tmp_path, monkeypatch):
    """When config is missing but example exists, copy example and load it."""
    example = tmp_path / "config.example.toml"
    example.write_text(
        "[general]\ninjection_delay_ms = 300\n"
        "[hotkey]\n"
        'modifiers = ["ctrl", "alt"]\n'
        "[audio]\n"
        "sample_rate = 48000\n"
        "chunk_size = 8192\n"
        "[inference]\n"
        'server_url = "ws://myhost:9000/v1/realtime"\n'
        "transcription_delay_ms = 160\n"
        "[database]\n"
        'path = "custom.db"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "EXAMPLE_CONFIG_PATH", example)

    config_path = tmp_path / "config.toml"
    cfg = load_config(config_path)

    assert config_path.exists()
    assert cfg.injection_delay_ms == 300
    assert cfg.audio.sample_rate == 48000


def test_load_config_from_valid_toml(tmp_path):
    """Full TOML with non-default values is parsed correctly."""
    toml_content = """\
[general]
injection_delay_ms = 200

[hotkey]
modifiers = ["ctrl", "alt"]

[audio]
sample_rate = 44100
chunk_size = 2048

[inference]
server_url = "http://gpu-server:8003/v1/audio/transcriptions"
transcription_delay_ms = 240
model = "whisper-large-v3-turbo"

[database]
path = "test.db"
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(toml_content, encoding="utf-8")

    cfg = load_config(config_path)

    assert cfg.injection_delay_ms == 200
    assert cfg.hotkey.modifiers == ["ctrl", "alt"]
    assert cfg.audio.sample_rate == 44100
    assert cfg.audio.chunk_size == 2048
    assert cfg.inference.server_url == "http://gpu-server:8003/v1/audio/transcriptions"
    assert cfg.inference.transcription_delay_ms == 240
    assert cfg.inference.model == "whisper-large-v3-turbo"
    assert cfg.database.path == "test.db"


def test_load_config_missing_sections_uses_defaults(tmp_path):
    """Partial TOML with only [general] — other sections get defaults."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("[general]\ninjection_delay_ms = 300\n", encoding="utf-8")

    cfg = load_config(config_path)

    assert cfg.injection_delay_ms == 300
    assert cfg.audio.sample_rate == 16_000
    assert cfg.audio.chunk_size == 4096
    assert cfg.hotkey.modifiers == ["ctrl", "win"]
    assert cfg.inference.server_url == "http://localhost:8002/v1/audio/transcriptions"


def test_load_config_missing_keys_in_section_uses_defaults(tmp_path):
    """Section present but with missing keys — missing keys get defaults."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("[audio]\nsample_rate = 48000\n", encoding="utf-8")

    cfg = load_config(config_path)

    assert cfg.audio.sample_rate == 48000
    assert cfg.audio.chunk_size == 4096  # default


def test_save_config_roundtrip(tmp_path):
    """save_config then load_config returns equivalent Config."""
    from nexusvox.config import VoiceCommandsConfig

    original = Config(
        injection_delay_ms=200,
        voice_commands=VoiceCommandsConfig(enabled=False, symbols=["slash", "plus"]),
        hotkey=HotkeyConfig(modifiers=["ctrl", "alt"], mode="toggle"),
        audio=AudioConfig(sample_rate=44100, chunk_size=2048),
        inference=InferenceConfig(
            server_url="http://test:8003/v1/audio/transcriptions",
            transcription_delay_ms=160,
            model="whisper-large-v3-turbo",
        ),
        database=DatabaseConfig(path="roundtrip.db", audio_dir="recordings"),
    )

    config_path = tmp_path / "config.toml"
    save_config(original, config_path)
    loaded = load_config(config_path)

    assert loaded.injection_delay_ms == original.injection_delay_ms
    assert loaded.voice_commands.enabled == original.voice_commands.enabled
    assert loaded.voice_commands.symbols == original.voice_commands.symbols
    assert loaded.hotkey.modifiers == original.hotkey.modifiers
    assert loaded.hotkey.mode == "toggle"
    assert loaded.audio.sample_rate == original.audio.sample_rate
    assert loaded.audio.chunk_size == original.audio.chunk_size
    assert loaded.inference.server_url == original.inference.server_url
    assert loaded.inference.transcription_delay_ms == original.inference.transcription_delay_ms
    assert loaded.inference.model == original.inference.model
    assert loaded.database.path == original.database.path
    assert loaded.database.audio_dir == original.database.audio_dir


def test_save_config_creates_valid_toml(tmp_path):
    """Saved file is valid TOML that tomllib can parse."""
    config_path = tmp_path / "config.toml"
    save_config(Config(), config_path)

    raw = config_path.read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))

    assert "general" in data
    assert "hotkey" in data
    assert "audio" in data
    assert "inference" in data
    assert "database" in data
    assert "os_commands" in data


def test_load_config_with_os_commands(tmp_path):
    """TOML with [os_commands] section loads correctly."""
    toml_content = """\
[general]
language = "en"

[os_commands]
enabled = true

[os_commands.apps]
chrome = "C:/Program Files/Google/Chrome/Application/chrome.exe"
terminal = "wt.exe"
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(toml_content, encoding="utf-8")

    cfg = load_config(config_path)

    assert cfg.os_commands.enabled is True
    assert cfg.os_commands.apps == {
        "chrome": "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "terminal": "wt.exe",
    }


def test_load_config_without_os_commands_uses_defaults(tmp_path):
    """Existing TOML without [os_commands] falls back to defaults."""
    config_path = tmp_path / "config.toml"
    config_path.write_text('[general]\nlanguage = "en"\n', encoding="utf-8")

    cfg = load_config(config_path)

    assert cfg.os_commands.enabled is False
    assert cfg.os_commands.apps == {}


def test_save_config_roundtrip_with_os_commands(tmp_path):
    """save_config then load_config preserves os_commands settings."""
    original = Config(
        os_commands=OSCommandsConfig(
            enabled=True,
            apps={"chrome": "chrome.exe", "terminal": "wt.exe"},
        ),
    )

    config_path = tmp_path / "config.toml"
    save_config(original, config_path)
    loaded = load_config(config_path)

    assert loaded.os_commands.enabled is True
    assert loaded.os_commands.apps == original.os_commands.apps


# ── Escaping of the strings save_config writes ───────────────────────

# An absolute Windows path with a non-ASCII user name, as a frozen build resolves `[database].path`.
FROZEN_DB_PATH = r"C:\Users\Jürgen Müller\AppData\Local\NexusVox\data\nexusvox.db"
ABSOLUTE_AUDIO_DIR = r"D:\Aufnahmen\Müller\audio"
AWKWARD = r'a"b\c\\d ä ß 日本語 😀 \u0041 \n'


def _flatten_strings(node, found: set[str]) -> set[str]:
    """Every string in a parsed TOML document: values, list items and table keys."""
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            _flatten_strings(value, found)
    elif isinstance(node, list):
        for item in node:
            _flatten_strings(item, found)
    elif isinstance(node, str):
        found.add(node)
    return found


def _fill_strings(obj, prefix: str, expected: list[str]) -> None:
    """Give every string, string list and string table of a config dataclass tree its own awkward value."""
    for f in dataclasses.fields(obj):
        value = getattr(obj, f.name)
        name = f"{prefix}.{f.name}"
        awkward = f'{name} "q" \\ \\\\ ä 日本 😀\t\n'
        if dataclasses.is_dataclass(value):
            _fill_strings(value, name, expected)
        elif isinstance(value, list) and not value and "LlmServer" in str(f.type):
            entry = LlmServer(url="")  # a list of saved servers: one entry, every string awkward
            _fill_strings(entry, name, expected)
            expected.remove(entry.protocol)  # the protocol is a fixed word, not free text
            entry.protocol = "openai"
            setattr(obj, f.name, [entry])
        elif isinstance(value, str):
            setattr(obj, f.name, awkward)
            expected.append(awkward)
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            setattr(obj, f.name, [awkward])
            expected.append(awkward)
        elif isinstance(value, dict):
            setattr(obj, f.name, {awkward: awkward})
            expected.append(awkward)


def test_save_config_writes_every_string_field_as_valid_toml(tmp_path):
    """Walk the whole Config: no string field, present or added later, may break the file when it holds
    quotes, backslashes, non-ASCII, an astral character or control characters."""
    cfg = Config()
    cfg.inference.compute_type = "int8"  # only written when set
    expected: list[str] = []
    _fill_strings(cfg, "config", expected)
    assert len(expected) > 25  # the walk really reached the sections

    config_path = tmp_path / "config.toml"
    save_config(cfg, config_path)
    written = _flatten_strings(tomllib.loads(config_path.read_text(encoding="utf-8")), set())

    assert [value for value in expected if value not in written] == []


def test_save_config_roundtrips_frozen_build_absolute_database_path(tmp_path):
    original = Config(database=DatabaseConfig(path=FROZEN_DB_PATH, audio_dir=ABSOLUTE_AUDIO_DIR))

    config_path = tmp_path / "config.toml"
    save_config(original, config_path)
    loaded = load_config(config_path)

    assert loaded.database.path == FROZEN_DB_PATH
    assert loaded.database.audio_dir == ABSOLUTE_AUDIO_DIR


def test_save_config_roundtrips_awkward_strings(tmp_path):
    """Free-form fields come back unchanged, including both secrets and the os_commands names."""
    original = Config(
        database=DatabaseConfig(path=AWKWARD, audio_dir=AWKWARD),
        inference=InferenceConfig(server_url=AWKWARD),
        llm=LlmConfig(api_key=AWKWARD, keep_alive=AWKWARD, model=AWKWARD),
        meeting=MeetingConfig(mic_device=AWKWARD, output_device=AWKWARD, own_voice_label=AWKWARD),
        os_commands=OSCommandsConfig(
            enabled=True,
            apps={
                "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                'my "app" ä': AWKWARD,
                "with space": "x.exe",
            },
        ),
    )
    original.remote_mic.token = AWKWARD

    config_path = tmp_path / "config.toml"
    save_config(original, config_path)
    loaded = load_config(config_path)

    assert loaded.database.path == AWKWARD
    assert loaded.database.audio_dir == AWKWARD
    assert loaded.inference.server_url == AWKWARD
    assert loaded.llm.api_key == AWKWARD
    assert loaded.llm.keep_alive == AWKWARD
    assert loaded.llm.model == AWKWARD
    assert loaded.meeting.mic_device == AWKWARD
    assert loaded.meeting.output_device == AWKWARD
    assert loaded.remote_mic.token == AWKWARD
    assert loaded.os_commands.apps == original.os_commands.apps


def test_save_config_escapes_control_characters(tmp_path):
    original = Config(database=DatabaseConfig(path="line1\nline2\ttab\x01\x7f"))

    config_path = tmp_path / "config.toml"
    save_config(original, config_path)

    assert load_config(config_path).database.path == "line1\nline2\ttab\x01\x7f"


def test_save_config_second_save_is_stable(tmp_path):
    """Saving a loaded config again writes the same file, so nothing is escaped twice."""
    config_path = tmp_path / "config.toml"
    save_config(Config(database=DatabaseConfig(path=FROZEN_DB_PATH, audio_dir=ABSOLUTE_AUDIO_DIR)), config_path)
    first = config_path.read_text(encoding="utf-8")

    save_config(load_config(config_path), config_path)

    assert config_path.read_text(encoding="utf-8") == first


def test_save_config_replaces_a_lone_surrogate_instead_of_failing(tmp_path):
    """JSON such as "a\\ud83d" yields a lone surrogate; it must not truncate the file by failing to encode."""
    config_path = tmp_path / "config.toml"
    save_config(Config(), config_path)
    cfg = load_config(config_path)
    cfg.meeting.own_voice_label = "a\ud83d"

    save_config(cfg)

    assert load_config(config_path).meeting.own_voice_label == "a\ufffd"


def test_save_config_leaves_the_file_alone_when_a_value_cannot_be_written(tmp_path):
    config_path = tmp_path / "config.toml"
    save_config(Config(), config_path)
    before = config_path.read_bytes()
    cfg = Config()
    cfg.hotkey.key = None  # type: ignore[assignment]

    with pytest.raises(TypeError):
        save_config(cfg, config_path)

    assert config_path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_save_config_is_atomic_when_the_swap_fails(tmp_path, monkeypatch):
    """A write that dies before the swap leaves the old file complete and no temporary file behind."""
    config_path = tmp_path / "config.toml"
    save_config(Config(), config_path)
    before = config_path.read_bytes()

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(config_module.os, "replace", broken_replace)
    with pytest.raises(OSError):
        save_config(Config(injection_delay_ms=1), config_path)

    assert config_path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_save_config_drops_entries_that_are_not_text(tmp_path):
    """Setters fed from JSON can hold None or numbers; they are skipped rather than written as "None"."""
    cfg = Config(os_commands=OSCommandsConfig(enabled=True, apps={"chrome": None, "n": 3, "ok": "ok.exe"}))
    cfg.voice_commands.symbols = ["slash", None, 4]

    config_path = tmp_path / "config.toml"
    save_config(cfg, config_path)
    loaded = load_config(config_path)

    assert loaded.os_commands.apps == {"ok": "ok.exe"}
    assert loaded.voice_commands.symbols == ["slash"]


def test_save_config_keeps_a_relative_database_path_relative_when_frozen(tmp_path, monkeypatch):
    """The installed app resolves a relative path into its data folder; saving must not pin that folder."""
    monkeypatch.setattr("nexusvox.system.paths.is_frozen", lambda: True)
    monkeypatch.setattr("nexusvox.system.paths.data_dir", lambda: tmp_path / "data")
    config_path = tmp_path / "config.toml"
    config_path.write_text('[database]\npath = "nexusvox.db"\n', encoding="utf-8")

    cfg = load_config(config_path)
    assert cfg.database.path == str(tmp_path / "data" / "nexusvox.db")
    save_config(cfg)

    assert tomllib.loads(config_path.read_text(encoding="utf-8"))["database"]["path"] == "nexusvox.db"

    cfg.database.path = FROZEN_DB_PATH  # a path set at run time is written as it is
    save_config(cfg)

    assert tomllib.loads(config_path.read_text(encoding="utf-8"))["database"]["path"] == FROZEN_DB_PATH


def test_load_config_sets_aside_a_file_the_old_writer_broke(tmp_path, caplog):
    """An unescaped Windows path made config.toml invalid; the app starts from the template and keeps the file."""
    config_path = tmp_path / "config.toml"
    broken = '[database]\npath = "C:\\Users\\x\\data\\nexusvox.db"\n'
    config_path.write_text(broken, encoding="utf-8")

    with caplog.at_level("WARNING", logger="nexusvox.config"):
        cfg = load_config(config_path)

    backups = list(tmp_path.glob("config.toml.broken-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == broken
    assert any("not valid TOML" in record.message for record in caplog.records)
    tomllib.loads(config_path.read_text(encoding="utf-8"))  # the file in place is valid again
    assert cfg.source_path == config_path.resolve()


# ── Device / compute_type ────────────────────────────────────────────


def test_inference_device_default():
    assert InferenceConfig().device == "auto"
    assert InferenceConfig().compute_type is None


def test_load_config_reads_device_and_compute_type(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[inference]\ndevice = "cpu"\ncompute_type = "int8"\n',
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg.inference.device == "cpu"
    assert cfg.inference.compute_type == "int8"


def test_save_config_roundtrip_with_device_and_compute_type(tmp_path):
    original = Config(
        inference=InferenceConfig(device="cuda", compute_type="float16"),
    )
    config_path = tmp_path / "config.toml"
    save_config(original, config_path)
    loaded = load_config(config_path)

    assert loaded.inference.device == "cuda"
    assert loaded.inference.compute_type == "float16"


def test_save_config_omits_compute_type_when_unset(tmp_path):
    config_path = tmp_path / "config.toml"
    save_config(Config(), config_path)

    raw = config_path.read_text(encoding="utf-8")
    assert 'device = "auto"' in raw
    assert "compute_type" not in raw


def test_resolve_device_explicit_values():
    assert resolve_device("cpu") == "cpu"
    assert resolve_device("cuda") == "cuda"


def test_resolve_device_auto_picks_cuda_when_available(monkeypatch):
    import sys
    import types

    fake_ct2 = types.SimpleNamespace(get_cuda_device_count=lambda: 1)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ct2)

    assert resolve_device("auto") == "cuda"


def test_resolve_device_auto_picks_cpu_when_no_cuda(monkeypatch):
    import sys
    import types

    fake_ct2 = types.SimpleNamespace(get_cuda_device_count=lambda: 0)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ct2)

    assert resolve_device("auto") == "cpu"


def test_resolve_device_auto_falls_back_on_import_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "ctranslate2":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert resolve_device("auto") == "cpu"


def test_resolve_compute_type_defaults():
    assert resolve_compute_type("cpu", None) == "int8"
    assert resolve_compute_type("cuda", None) == "float16"


def test_resolve_compute_type_override_wins():
    assert resolve_compute_type("cpu", "float16") == "float16"
    assert resolve_compute_type("cuda", "int8") == "int8"


def test_log_level_defaults_to_info(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[general]\nlanguage = "en"\n')
    assert load_config(path).log_level == "INFO"
    assert Config().log_level == "INFO"


def test_log_level_is_read_case_insensitively_and_saved(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[general]\nlog_level = "debug"\n')
    cfg = load_config(path)
    assert cfg.log_level == "DEBUG"
    save_config(cfg, path)
    assert tomllib.loads(path.read_text())["general"]["log_level"] == "DEBUG"
    assert load_config(path).log_level == "DEBUG"


def test_unknown_log_level_falls_back_to_info(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[general]\nlog_level = "chatty"\n')
    assert load_config(path).log_level == "INFO"


def test_main_applies_the_configured_log_level(monkeypatch):
    import importlib
    import logging
    import sys
    import types

    class App:
        def __init__(self, config, autostart=False):
            pass

        def run(self):
            raise KeyboardInterrupt

        def open_dashboard_from_second_start(self):
            pass

    # The real app module binds PortAudio at import, which CI runners on Linux do not have.
    monkeypatch.setitem(sys.modules, "nexusvox.app.core", types.SimpleNamespace(NexusVoxApp=App))
    monkeypatch.delitem(sys.modules, "nexusvox.__main__", raising=False)
    entry = importlib.import_module("nexusvox.__main__")
    seen = {}
    own = logging.getLogger("nexusvox")
    previous = own.level
    monkeypatch.setattr(entry, "load_config", lambda: Config(log_level="DEBUG"))
    # A NexusVox that really runs on this machine must not turn this test into a second start.
    monkeypatch.setattr(
        entry,
        "SingleInstance",
        lambda: types.SimpleNamespace(acquire=lambda: True, watch=lambda cb: None, release=lambda: None),
    )
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: seen.update(kwargs))
    try:
        with pytest.raises(SystemExit) as exit_:
            entry.main()
        own_level = own.level
    finally:
        own.setLevel(previous)
        sys.modules.pop("nexusvox.__main__", None)  # it holds the stub app
    assert exit_.value.code == 0
    # DEBUG reaches NexusVox's own loggers only, not comtypes or urllib3.
    assert own_level == logging.DEBUG
    assert seen["level"] == logging.INFO


def test_unknown_hotkey_mode_falls_back_to_hold(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[hotkey]\nmodifiers = ["ctrl", "alt"]\nmode = "double-tap"\n')
    assert load_config(path).hotkey.mode == "hold"


def test_hotkey_key_is_loaded_normalized_and_saved(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[hotkey]\nmodifiers = ["ctrl", "shift"]\nkey = "a"\nmode = "hold"\n')
    cfg = load_config(path)
    assert cfg.hotkey.modifiers == ["ctrl", "shift"] and cfg.hotkey.key == "A"
    save_config(cfg, path)
    assert 'key = "A"' in path.read_text()
    assert load_config(path).hotkey.key == "A"


def test_hotkey_key_defaults_to_none(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[hotkey]\nmodifiers = ["ctrl", "alt"]\n')
    assert load_config(path).hotkey.key == ""


def test_saved_hotkeys_survive_the_new_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[hotkey]\nmodifiers = ["ctrl", "shift", "alt"]\n[meeting]\nmodifiers = ["ctrl", "alt"]\nkey = "M"\n'
    )
    cfg = load_config(path)
    assert cfg.hotkey.modifiers == ["ctrl", "shift", "alt"]
    assert cfg.meeting.modifiers == ["ctrl", "alt"] and cfg.meeting.key == "M"


def test_partial_hotkey_sections_keep_their_old_meaning(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[hotkey]\nkey = "A"\n[meeting]\nkey = "F8"\n')
    cfg = load_config(path)
    assert cfg.hotkey.modifiers == ["ctrl", "shift", "alt"] and cfg.hotkey.key == "A"
    assert cfg.meeting.modifiers == ["ctrl", "alt"] and cfg.meeting.key == "F8"


def test_unusable_hotkey_falls_back_to_default(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[hotkey]\nmodifiers = []\nkey = ""\n')
    assert load_config(path).hotkey.modifiers == ["ctrl", "win"]
    path.write_text('[hotkey]\nmodifiers = ["ctrl"]\nkey = "enter"\n')
    cfg = load_config(path)
    assert cfg.hotkey.modifiers == ["ctrl", "win"] and cfg.hotkey.key == ""
    path.write_text('[hotkey]\nmodifiers = ["hyper"]\n')
    assert load_config(path).hotkey.modifiers == ["ctrl", "win"]


def test_validate_hotkey():
    assert validate_hotkey(["ctrl", "shift"], "a") is None
    assert validate_hotkey([], "F9") is None
    assert validate_hotkey(["ctrl", "shift", "alt"], "") is None
    assert validate_hotkey([], "") is not None
    assert validate_hotkey([], "   ") is not None
    assert validate_hotkey(["ctrl", "ctrl"], "") is not None
    assert validate_hotkey(["ctrl"], "enter") is not None
    assert validate_hotkey(["meta"], "a") is not None


def test_store_audio_defaults_on_and_roundtrips(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[database]\npath = "x.db"\n')
    assert load_config(path).database.store_audio is True
    path.write_text('[database]\npath = "x.db"\nstore_audio = false\n')
    cfg = load_config(path)
    assert cfg.database.store_audio is False
    save_config(cfg, path)
    assert "store_audio = false" in path.read_text()
    assert load_config(path).database.store_audio is False


def test_os_control_defaults_load_and_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[os_control]\nenabled = true\ncommand_key = "oem_102"\n')
    cfg = load_config(path)
    ws = cfg.os_control
    assert ws.enabled and ws.command_key == "OEM_102"
    assert (ws.host, ws.port, ws.service, ws.timeout_s) == ("127.0.0.1", 49730, "os-control", 5.0)
    save_config(cfg, path)
    assert load_config(path).os_control == ws


def test_os_control_unknown_command_key_falls_back(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[os_control]\ncommand_key = "tilde"\n')
    cfg = load_config(path)
    assert cfg.os_control.command_key == "Y" and not cfg.os_control.enabled


def test_os_control_command_key_defaults_to_y(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[os_control]\nenabled = true\n")
    assert load_config(path).os_control.command_key == "Y"


def test_voice_commands_structural_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    cfg = Config(voice_commands=VoiceCommandsConfig(structural=["tab"], bypass_structural=True))
    save_config(cfg, path)

    loaded = load_config(path)

    assert loaded.voice_commands.structural == ["tab"]
    assert loaded.voice_commands.bypass_structural is True


def test_voice_commands_press_enter_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    save_config(Config(voice_commands=VoiceCommandsConfig(press_enter=True)), path)

    assert load_config(path).voice_commands.press_enter is True


def test_voice_commands_press_enter_off_when_missing(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[voice_commands]\nenabled = true\n", encoding="utf-8")

    assert load_config(path).voice_commands.press_enter is False


def test_voice_commands_structural_defaults_when_missing(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[voice_commands]\nenabled = true\n", encoding="utf-8")

    loaded = load_config(path)

    assert loaded.voice_commands.structural == ["new paragraph", "new line", "all caps"]
    assert loaded.voice_commands.bypass_structural is False


def test_learn_from_edits_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    save_config(Config(learn_from_edits=LearnFromEditsConfig(enabled=True, window_s=12.5)), path)

    loaded = load_config(path).learn_from_edits
    assert (loaded.enabled, loaded.window_s) == (True, 12.5)


def test_learn_from_edits_window_defaults_to_20_seconds():
    assert LearnFromEditsConfig().window_s == 20.0
    template = tomllib.loads(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))
    assert template["learn_from_edits"]["window_s"] == 20.0


def test_learn_from_edits_off_when_missing(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[general]\n", encoding="utf-8")

    loaded = load_config(path).learn_from_edits
    assert (loaded.enabled, loaded.window_s) == (False, 20.0)


def test_paste_key_roundtrip_and_default(tmp_path):
    path = tmp_path / "config.toml"
    save_config(Config(hotkey=HotkeyConfig(paste_key="F8")), path)
    assert load_config(path).hotkey.paste_key == "F8"

    path.write_text('[hotkey]\nmode = "hold"\n', encoding="utf-8")
    assert load_config(path).hotkey.paste_key == "V"
    path.write_text('[hotkey]\npaste_key = "nope"\n', encoding="utf-8")
    assert load_config(path).hotkey.paste_key == ""


def test_llm_and_polish_key_roundtrip_and_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "cuda_available", lambda: True)
    monkeypatch.setattr(config_module, "gpu_vram_gb", lambda: 16.0)
    path = tmp_path / "config.toml"
    llm = LlmConfig(url="http://127.0.0.1:1234/v1", protocol="openai", model="m", api_key='a"b', keep_alive="1m")
    save_config(Config(llm=llm, hotkey=HotkeyConfig(polish_key="P")), path)
    loaded = load_config(path)
    assert loaded.llm == llm and loaded.hotkey.polish_key == "P"

    path.write_text('[llm]\nprotocol = "grpc"\n', encoding="utf-8")
    loaded = load_config(path)
    assert loaded.llm == LlmConfig() and loaded.hotkey.polish_key == ""


def test_llm_model_is_e2b_without_cuda_when_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "cuda_available", lambda: False)
    path = tmp_path / "config.toml"
    path.write_text("[llm]\nprotocol = 'ollama'\n", encoding="utf-8")
    assert load_config(path).llm.model == "gemma4:e2b"
    path.write_text("", encoding="utf-8")
    assert load_config(path).llm.model == "gemma4:e2b"


def test_llm_model_is_e4b_with_cuda_when_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "cuda_available", lambda: True)
    monkeypatch.setattr(config_module, "gpu_vram_gb", lambda: 16.0)
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    assert load_config(path).llm.model == "gemma4:e4b"


def test_llm_model_is_e2b_with_cuda_but_little_vram_when_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "cuda_available", lambda: True)
    monkeypatch.setattr(config_module, "gpu_vram_gb", lambda: 8.0)
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    assert load_config(path).llm.model == "gemma4:e2b"


@pytest.mark.parametrize("has_cuda", [False, True])
def test_llm_model_choice_is_kept(tmp_path, monkeypatch, has_cuda):
    monkeypatch.setattr(config_module, "cuda_available", lambda: has_cuda)
    path = tmp_path / "config.toml"
    path.write_text("[llm]\nmodel = 'qwen3.5:4b'\n", encoding="utf-8")
    assert load_config(path).llm.model == "qwen3.5:4b"
    path.write_text("[llm]\nmodel = 'gemma4:e4b'\n", encoding="utf-8")  # an explicit e4b stays on a CPU PC
    assert load_config(path).llm.model == "gemma4:e4b"


@pytest.mark.parametrize("has_cuda", [False, True])
def test_hardware_defaults_are_not_pinned_by_a_save(tmp_path, monkeypatch, has_cuda):
    monkeypatch.setattr(config_module, "cuda_available", lambda: has_cuda)
    monkeypatch.setattr(config_module, "gpu_vram_gb", lambda: 16.0)
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    save_config(load_config(path), path)
    saved = tomllib.loads(path.read_text(encoding="utf-8"))
    assert "model" not in saved["llm"] and "timeout_s" not in saved["cleanup"]
    # The same file moved to the other kind of machine follows that machine.
    monkeypatch.setattr(config_module, "cuda_available", lambda: not has_cuda)
    moved = load_config(path)
    assert moved.llm.model == ("gemma4:e4b" if not has_cuda else "gemma4:e2b")


def test_a_model_that_differs_from_the_default_is_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "cuda_available", lambda: False)
    path = tmp_path / "config.toml"
    config = Config()
    config.llm.model = "gemma4:e4b"
    save_config(config, path)
    assert load_config(path).llm.model == "gemma4:e4b"


def test_cleanup_timeout_is_longer_on_the_cpu_unless_set(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(config_module, "cuda_available", lambda: False)
    assert load_config(path).cleanup.timeout_s == 10.0
    monkeypatch.setattr(config_module, "cuda_available", lambda: True)
    assert load_config(path).cleanup.timeout_s == 3.0
    monkeypatch.setattr(config_module, "cuda_available", lambda: False)
    path.write_text("[cleanup]\ntimeout_s = 4.0\n", encoding="utf-8")
    assert load_config(path).cleanup.timeout_s == 4.0


def test_config_without_file_or_example_gets_the_hardware_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "cuda_available", lambda: False)
    monkeypatch.setattr(config_module, "EXAMPLE_CONFIG_PATH", tmp_path / "missing.toml")
    loaded = load_config(tmp_path / "config.toml")
    assert (loaded.llm.model, loaded.cleanup.timeout_s) == ("gemma4:e2b", 10.0)


def test_example_config_leaves_the_llm_model_unset():
    example = tomllib.loads(Path(config_module.__file__).with_name("config.example.toml").read_text(encoding="utf-8"))
    assert "model" not in example["llm"]


def test_cleanup_roundtrip_and_unknown_level(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "cuda_available", lambda: True)
    path = tmp_path / "config.toml"
    save_config(Config(cleanup=CleanupConfig(level="medium", timeout_s=4.5)), path)
    assert load_config(path).cleanup == CleanupConfig(level="medium", timeout_s=4.5)
    path.write_text('[cleanup]\nlevel = "heavy"\n', encoding="utf-8")
    assert load_config(path).cleanup == CleanupConfig()
    path.write_text('[cleanup]\nlevel = "Light"\ntimeout_s = 0\n', encoding="utf-8")
    assert load_config(path).cleanup == CleanupConfig(level="light", timeout_s=3.0)


def test_polish_in_terminals_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    save_config(Config(polish=PolishConfig(in_terminals=True)), path)
    assert load_config(path).polish.in_terminals is True
    path.write_text("[general]\n", encoding="utf-8")
    assert load_config(path).polish.in_terminals is False


def test_polish_key_equal_to_the_paste_key_is_dropped(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[hotkey]\npaste_key = "V"\npolish_key = "v"\n', encoding="utf-8")
    assert load_config(path).hotkey.polish_key == ""


def test_feedback_mode_round_trips_and_the_template_starts_on_visual(tmp_path):
    path = tmp_path / "config.toml"
    cfg = load_config(path)  # created from the template: a fresh install
    assert cfg.feedback.mode == "visual"
    cfg.feedback.mode = "both"
    save_config(cfg, path)
    assert load_config(path).feedback.mode == "both"


def test_unknown_feedback_mode_falls_back_to_sound(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[feedback]\nmode = "disco"\n', encoding="utf-8")
    assert load_config(path).feedback.mode == "sound"


def test_save_config_writes_back_to_the_file_that_was_loaded(tmp_path, monkeypatch):
    """The dashboard saves with no path: it must hit the loaded file even after the working directory moved."""
    loaded = tmp_path / "app" / "config.toml"
    loaded.parent.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(loaded.parent)
    config = load_config(Path("config.toml"))
    assert config.source_path == loaded.resolve()

    monkeypatch.chdir(elsewhere)
    config.cleanup.level = "medium"
    save_config(config)

    assert not (elsewhere / "config.toml").exists()
    assert load_config(loaded).cleanup.level == "medium"


def test_old_language_keys_are_ignored_and_not_written(tmp_path):
    """A config.toml from before the language setting was removed still loads, and is saved without the keys."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[general]\nlanguage = "de"\nauto_language_detection = true\ninjection_delay_ms = 300\n', encoding="utf-8"
    )

    cfg = load_config(config_path)
    save_config(cfg, config_path)

    saved = config_path.read_text(encoding="utf-8")
    assert cfg.injection_delay_ms == 300
    assert "language" not in saved


@pytest.mark.parametrize(
    "removed",
    [
        "voxtral-mini-4b",
        "cohere-transcribe",
        "distil-whisper-large-v3",
        "distil-whisper-medium-en",
        "whisper-small",
        "whisper-medium",
        "whisper-base",
    ],
)
def test_a_removed_model_falls_back_to_parakeet_with_one_log_line(tmp_path, caplog, removed):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""[inference]
server_url = "ws://localhost:8000/v1/realtime"
model = "{removed}"
""",
        encoding="utf-8",
    )

    with caplog.at_level("WARNING", logger="nexusvox.config"):
        cfg = load_config(config_path)

    assert cfg.inference.model == "parakeet-tdt-0.6b"
    assert cfg.inference.server_url == "http://localhost:8002/v1/audio/transcriptions"
    assert [r.getMessage() for r in caplog.records if removed in r.getMessage()] == [
        f"Model {removed!r} is no longer available; using parakeet-tdt-0.6b instead."
    ]

    caplog.clear()
    with caplog.at_level("WARNING", logger="nexusvox.config"):
        load_config(config_path)  # the file still names the old model: no second line
    assert not [r for r in caplog.records if removed in r.getMessage()]


def test_a_removed_model_keeps_a_deliberate_server_address(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """[inference]
server_url = "http://gpu-box.lan:9000/v1/audio/transcriptions"
model = "cohere-transcribe"
""",
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg.inference.model == "parakeet-tdt-0.6b"
    assert cfg.inference.server_url == "http://gpu-box.lan:9000/v1/audio/transcriptions"


def test_the_registry_keeps_only_parakeet_whisper_turbo_and_the_custom_slot():
    from nexusvox.config import MODEL_REGISTRY

    assert list(MODEL_REGISTRY) == ["parakeet-tdt-0.6b", "whisper-large-v3-turbo", "custom-server"]


def test_download_state_of_the_in_process_models(tmp_path):
    from nexusvox.config import model_download_state

    assert model_download_state("custom-server") == {"downloaded": None, "size": ""}
    assert model_download_state("parakeet-tdt-0.6b", tmp_path) == {"downloaded": False, "size": "~670 MB"}
    assert model_download_state("whisper-large-v3-turbo", tmp_path)["size"] == "~1.6 GB"

    snapshot = tmp_path / "models--istupakov--parakeet-tdt-0.6b-v3-onnx"
    (snapshot / "snapshots" / "abc").mkdir(parents=True)
    (snapshot / "snapshots" / "abc" / "encoder-model.int8.onnx").write_bytes(b"x")
    (snapshot / "refs").mkdir()
    (snapshot / "refs" / "main").write_text("abc")

    assert model_download_state("parakeet-tdt-0.6b", tmp_path)["downloaded"] is True


def test_numbers_as_digits_on_by_default():
    assert VoiceCommandsConfig().numbers_as_digits is True


def test_numbers_as_digits_on_when_key_missing(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[voice_commands]\nenabled = true\n", encoding="utf-8")

    assert load_config(path).voice_commands.numbers_as_digits is True


def test_numbers_as_digits_on_when_section_missing(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[general]\nvoice_commands_enabled = true\n", encoding="utf-8")

    assert load_config(path).voice_commands.numbers_as_digits is True


def test_numbers_as_digits_saved_false_is_kept(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[voice_commands]\nnumbers_as_digits = false\n", encoding="utf-8")

    assert load_config(path).voice_commands.numbers_as_digits is False


def test_numbers_as_digits_false_survives_save_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    save_config(Config(voice_commands=VoiceCommandsConfig(numbers_as_digits=False)), path)

    assert load_config(path).voice_commands.numbers_as_digits is False


def test_meeting_fallback_list_starts_with_the_default_and_stays_alt_free_first():
    from nexusvox import config as config_module

    first = config_module._MEETING_HOTKEY_FALLBACKS[0]
    assert first == (list(MeetingConfig().modifiers), MeetingConfig().key) and first[1] == "F9"
    clash = HotkeyConfig(modifiers=["ctrl", "shift"], key="F9")
    assert config_module.default_meeting_hotkey(clash) == (["ctrl", "shift"], "F10")


def test_meeting_fallback_last_resort_never_clashes_with_dictation():
    from nexusvox.config_sections.meeting import validate_meeting_hotkey

    # Every listed fallback contains this dictation chord, so only the extended search can find a valid one.
    dictation = HotkeyConfig(modifiers=["ctrl", "alt", "win"], key="")
    modifiers, key = config_module.default_meeting_hotkey(dictation)
    assert validate_meeting_hotkey(modifiers, key, dictation) is None
    assert (set(modifiers), key) != (set(dictation.modifiers), dictation.key)


def test_max_recording_s_defaults_to_ten_minutes_round_trips_and_ignores_junk(tmp_path):
    path = tmp_path / "config.toml"
    cfg = load_config(path)
    assert cfg.hotkey.max_recording_s == 600
    cfg.hotkey.max_recording_s = 120
    save_config(cfg, path)
    assert load_config(path).hotkey.max_recording_s == 120
    for bad in ("-5", '"soon"', "true", "inf", "nan", "0.5"):
        path.write_text(f"[hotkey]\nmax_recording_s = {bad}\n", encoding="utf-8")
        assert load_config(path).hotkey.max_recording_s == 600
    path.write_text("[hotkey]\nmax_recording_s = 0\n", encoding="utf-8")
    assert load_config(path).hotkey.max_recording_s == 0  # 0 turns the cap off


def test_plain_values_are_saved_as_before(tmp_path):
    path = tmp_path / "config.toml"
    config = Config()
    config.os_commands.apps = {"notepad": "notepad.exe"}

    save_config(config, path)

    text = path.read_text(encoding="utf-8")
    assert 'notepad = "notepad.exe"\n' in text and 'key = ""\n' in text and 'mode = "hold"\n' in text


def test_save_config_logs_the_names_of_the_settings_it_changed(tmp_path, caplog):
    config_path = tmp_path / "config.toml"
    config = Config()
    with caplog.at_level(logging.INFO, logger="nexusvox.config"):
        save_config(config, config_path)
        assert "Settings file created" in caplog.text
        caplog.clear()

        save_config(config, config_path)  # nothing changed: no line
        assert "Settings saved" not in caplog.text

        config.llm.model = "a-secret-model-name"
        save_config(config, config_path)
    assert "Settings saved: llm.model" in caplog.text
    assert "a-secret-model-name" not in caplog.text  # names only, never values


# ── Bad values in a hand-edited config.toml ───────────────────────────────────────────────────────
def test_a_config_saved_as_ansi_keeps_its_settings(tmp_path, caplog):
    """Notepad's "ANSI" is Windows-1252: an umlaut in it must not cost the user the whole config."""
    path = tmp_path / "config.toml"
    path.write_bytes('[audio]\ndevice = "Mikrofon (Jörg)"\n[meeting]\nstore_audio = false\n'.encode("cp1252"))

    with caplog.at_level("WARNING", logger="nexusvox.config"):
        cfg = load_config(path)

    assert cfg.audio.device == "Mikrofon (Jörg)" and cfg.meeting.store_audio is False
    assert list(tmp_path.glob("config.toml.broken-*")) == []
    assert any("not UTF-8" in record.message for record in caplog.records)


def test_a_config_with_a_byte_order_mark_is_read(tmp_path):
    path = tmp_path / "config.toml"
    path.write_bytes(b"\xef\xbb\xbf" + b'[audio]\ndevice = "Mikrofon"\n')
    assert load_config(path).audio.device == "Mikrofon"
    assert list(tmp_path.glob("config.toml.broken-*")) == []


def test_a_config_that_is_no_known_text_is_moved_aside(tmp_path):
    path = tmp_path / "config.toml"
    garbage = b'[audio]\ndevice = "\x81\x8d\x8f\x90\x9d"\n'  # bytes Windows-1252 does not define
    path.write_bytes(garbage)

    load_config(path)

    backups = list(tmp_path.glob("config.toml.broken-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == garbage


_SWITCHES = [
    ("general", "log_full_text", lambda c: c.log_full_text, False),
    ("general", "setup_done", lambda c: c.setup_done, True),
    ("database", "store_audio", lambda c: c.database.store_audio, False),
    ("meeting", "store_audio", lambda c: c.meeting.store_audio, False),
    ("meeting", "consent_hint", lambda c: c.meeting.consent_hint, True),
    ("meeting", "leave_dictation_out", lambda c: c.meeting.leave_dictation_out, True),
    ("meeting", "live_transcript", lambda c: c.meeting.live_transcript, False),
    ("remote_mic", "enabled", lambda c: c.remote_mic.enabled, False),
    ("assistant", "enabled", lambda c: c.assistant.enabled, False),
    ("os_control", "enabled", lambda c: c.os_control.enabled, False),
    ("translator", "enabled", lambda c: c.translator.enabled, False),
    ("mic_guard", "enabled", lambda c: c.mic_guard.enabled, False),
    ("os_commands", "enabled", lambda c: c.os_commands.enabled, False),
    ("learn_from_edits", "enabled", lambda c: c.learn_from_edits.enabled, False),
    ("polish", "in_terminals", lambda c: c.polish.in_terminals, False),
    ("voice_commands", "enabled", lambda c: c.voice_commands.enabled, True),
    ("voice_commands", "press_enter", lambda c: c.voice_commands.press_enter, False),
]


@pytest.mark.parametrize(("section", "key", "read", "garbled"), _SWITCHES)
def test_a_quoted_switch_is_never_read_as_true(tmp_path, caplog, section, key, read, garbled):
    """`"false"` is a non-empty string: it must not turn a setting (a privacy one above all) on."""
    path = tmp_path / "config.toml"
    for value, expected in (("true", True), ("false", False)):
        path.write_text(f"[{section}]\n{key} = {value}\n", encoding="utf-8")
        assert read(load_config(path)) is expected
    path.write_text(f'[{section}]\n{key} = "false"\n', encoding="utf-8")
    with caplog.at_level("WARNING", logger="nexusvox.config"):
        assert read(load_config(path)) is garbled
    assert any(f"[{section}].{key}" in record.message for record in caplog.records)


def test_privacy_switches_stay_off_when_garbled_and_keep_their_defaults_when_missing(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[general]\nlog_full_text = 1\n[database]\nstore_audio = "no"\n[meeting]\nstore_audio = 0\n')
    cfg = load_config(path)
    assert (cfg.log_full_text, cfg.database.store_audio, cfg.meeting.store_audio) == (False, False, False)
    path.write_text("")
    cfg = load_config(path)
    assert (cfg.log_full_text, cfg.database.store_audio, cfg.meeting.store_audio) == (False, True, True)


def test_a_bad_number_falls_back_to_the_default_with_a_warning(tmp_path, caplog):
    path = tmp_path / "config.toml"
    path.write_text(
        '[assistant]\nport = "49730a"\ntimeout_s = "60s"\n'
        "[os_control]\nport = 70000\ntimeout_s = -1\n"
        "[translator]\ntimeout_s = inf\n"
        '[mic_guard]\nwatchdog_s = "soon"\n'
        "[learn_from_edits]\nwindow_s = true\n"
        "[remote_mic]\nport = 0\n"
        '[llm]\ntimeout_s = "slow"\n'
        '[general]\ninjection_delay_ms = "fast"\n'
        '[audio]\nsample_rate = "16000a"\nchunk_size = 0\n'
        "[inference]\ntranscription_delay_ms = -4\n"
    )
    with caplog.at_level("WARNING", logger="nexusvox.config"):
        cfg = load_config(path)
    assert (cfg.assistant.port, cfg.assistant.timeout_s) == (49730, 5.0)
    assert (cfg.os_control.port, cfg.os_control.timeout_s) == (49730, 5.0)
    assert cfg.translator.timeout_s == 8.0
    assert cfg.mic_guard.watchdog_s == 30.0
    assert cfg.learn_from_edits.window_s == 20.0
    assert cfg.remote_mic.port == 47393
    assert cfg.llm.timeout_s == LlmConfig.timeout_s
    assert cfg.injection_delay_ms == 500
    assert (cfg.audio.sample_rate, cfg.audio.chunk_size) == (16_000, 4096)
    assert cfg.inference.transcription_delay_ms == 480
    assert any("[assistant].port" in record.message for record in caplog.records)


def test_a_number_written_as_text_is_read_as_a_number_not_kept_as_text(tmp_path):
    """`injection_delay_ms = "500"` used to reach `time.sleep("500" / 1000)` and fail every dictation."""
    path = tmp_path / "config.toml"
    path.write_text('[general]\ninjection_delay_ms = "250"\n[audio]\nsample_rate = "8000"\nchunk_size = 2048.0\n')
    cfg = load_config(path)
    assert cfg.injection_delay_ms == 250 and isinstance(cfg.injection_delay_ms, int)
    assert (cfg.audio.sample_rate, cfg.audio.chunk_size) == (8000, 2048)


@pytest.mark.parametrize("raw", ["47393.9", '"8080.5"', "-1"])
def test_a_port_with_a_fraction_is_refused_not_cut(tmp_path, caplog, raw):
    path = tmp_path / "config.toml"
    path.write_text(f"[remote_mic]\nport = {raw}\n")
    with caplog.at_level("WARNING", logger="nexusvox.config"):
        assert load_config(path).remote_mic.port == 47393
    assert any("[remote_mic].port" in record.message for record in caplog.records)


def test_a_wait_longer_than_the_platform_can_sleep_is_refused(tmp_path):
    """`threading.Timer(1e10, ...)` raises OverflowError in its thread: the mic watchdog would never fire."""
    import threading

    path = tmp_path / "config.toml"
    path.write_text("[mic_guard]\nwatchdog_s = 1e10\n[learn_from_edits]\nwindow_s = 1e10\n[llm]\ntimeout_s = 1e10\n")
    cfg = load_config(path)
    assert (cfg.mic_guard.watchdog_s, cfg.learn_from_edits.window_s) == (30.0, 20.0)
    assert cfg.llm.timeout_s == LlmConfig.timeout_s
    path.write_text(f"[mic_guard]\nwatchdog_s = {threading.TIMEOUT_MAX / 2}\n")
    assert load_config(path).mic_guard.watchdog_s == threading.TIMEOUT_MAX / 2


def test_a_bad_cleanup_timeout_falls_back_to_the_hardware_default(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('[cleanup]\nlevel = "light"\ntimeout_s = "30s"\n')
    monkeypatch.setattr(config_module, "cuda_available", lambda: False)
    assert load_config(path).cleanup.timeout_s == config_module.CLEANUP_TIMEOUT_CPU_S
    monkeypatch.setattr(config_module, "cuda_available", lambda: True)
    assert load_config(path).cleanup.timeout_s == CleanupConfig.timeout_s


def test_numbers_written_as_text_or_whole_are_still_read(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[assistant]\nport = "49731"\ntimeout_s = 2\n[mic_guard]\nwatchdog_s = 0\n')
    cfg = load_config(path)
    assert (cfg.assistant.port, cfg.assistant.timeout_s, cfg.mic_guard.watchdog_s) == (49731, 2.0, 0.0)


@pytest.mark.parametrize("raw", ["0", "-5", "inf", "nan", '"60s"', "true"])
def test_llm_timeout_must_be_a_positive_finite_number(tmp_path, raw):
    path = tmp_path / "config.toml"
    path.write_text(f"[llm]\ntimeout_s = {raw}\n")
    assert load_config(path).llm.timeout_s == LlmConfig.timeout_s
    path.write_text("[llm]\ntimeout_s = 12.5\n")
    assert load_config(path).llm.timeout_s == 12.5


def test_an_integer_beyond_64_bits_is_never_written(tmp_path):
    path = tmp_path / "config.toml"
    save_config(Config(), path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="64 bits"):
        save_config(Config(injection_delay_ms=2**63), path)
    assert path.read_bytes() == before  # the file is untouched
    save_config(Config(injection_delay_ms=2**63 - 1), path)
    assert load_config(path).injection_delay_ms == 2**63 - 1


def _servers(*urls):
    return Config(llm=LlmConfig(servers=[LlmServer(url, "ollama") for url in urls]))


def _logged_keys(tmp_path, caplog, before, after):
    path = tmp_path / "config.toml"
    save_config(before, path)
    caplog.clear()
    with caplog.at_level("INFO", logger="nexusvox.config"):
        save_config(after, path)
    return [record.getMessage() for record in caplog.records if record.getMessage().startswith("Settings saved")]


def test_every_llm_server_change_is_logged_on_its_own(tmp_path, caplog):
    logged = _logged_keys(tmp_path, caplog, _servers("http://a:1", "http://b:2"), _servers("http://a:9", "http://b:2"))
    assert logged == ["Settings saved: llm.servers[1].url"]


def test_removing_the_first_server_is_one_change_not_one_per_later_entry(tmp_path, caplog):
    before = _servers("http://a:1", "http://b:2", "http://c:3", "http://d:4")
    logged = _logged_keys(tmp_path, caplog, before, _servers("http://b:2", "http://c:3", "http://d:4"))
    assert logged == ["Settings saved: llm.servers[1] removed"]


def test_adding_a_server_in_the_middle_is_one_change(tmp_path, caplog):
    logged = _logged_keys(
        tmp_path, caplog, _servers("http://a:1", "http://c:3"), _servers("http://a:1", "http://b:2", "http://c:3")
    )
    assert logged == ["Settings saved: llm.servers[2] added"]


def test_setting_lines_keep_every_array_entry_apart():
    raw = b'[llm]\nurl = "x"\n[[llm.servers]]\nurl = "a"\n[[llm.servers]]\nurl = "b"\n[polish]\nin_terminals = true\n'
    assert config_module._setting_lines(raw) == (
        {"llm.url": '"x"', "polish.in_terminals": "true"},
        {"llm.servers": [{"url": '"a"'}, {"url": '"b"'}]},
    )


def test_a_cuda_yes_is_probed_once_per_process(monkeypatch):
    calls = []
    monkeypatch.setattr(config_module, "resolve_device", lambda requested: calls.append(requested) or "cuda")
    assert config_module.cuda_available() is True
    monkeypatch.setattr(config_module, "resolve_device", lambda requested: calls.append("again") or "cpu")
    assert config_module.cuda_available() is True
    assert calls == ["auto"]


def test_a_cuda_no_is_probed_again_later(monkeypatch):
    """A driver that is not ready at autostart must not keep the GPU off until the app restarts."""
    now = [1000.0]
    answers = ["cpu", "cuda"]
    calls = []
    monkeypatch.setattr(config_module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(config_module, "resolve_device", lambda requested: calls.append(1) or answers[len(calls) - 1])
    assert config_module.cuda_available() is False
    assert config_module.cuda_available() is False and len(calls) == 1  # the next call of a save: no second probe
    now[0] += config_module._CUDA_NO_TTL_S + 1
    assert config_module.cuda_available() is True and len(calls) == 2


def test_a_save_probes_the_gpu_once(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(config_module, "resolve_device", lambda requested: calls.append(requested) or "cpu")
    save_config(Config(), tmp_path / "config.toml")
    save_config(Config(), tmp_path / "config.toml")
    assert len(calls) == 1


def test_the_inference_defaults_come_from_the_model_registry():
    assert InferenceConfig().model == config_module.DEFAULT_MODEL
    assert InferenceConfig().server_url == config_module.MODEL_REGISTRY[config_module.DEFAULT_MODEL]["default_url"]


def test_main_logs_what_load_config_warns_about(monkeypatch, tmp_path):
    import importlib
    import sys
    import types

    class App:
        def __init__(self, config, autostart=False):
            pass

        def run(self):
            raise KeyboardInterrupt

        def open_dashboard_from_second_start(self):
            pass

    monkeypatch.setitem(sys.modules, "nexusvox.app.core", types.SimpleNamespace(NexusVoxApp=App))
    monkeypatch.delitem(sys.modules, "nexusvox.__main__", raising=False)
    entry = importlib.import_module("nexusvox.__main__")
    monkeypatch.setattr(
        entry,
        "SingleInstance",
        lambda: types.SimpleNamespace(acquire=lambda: True, watch=lambda cb: None, release=lambda: None),
    )
    monkeypatch.setattr(entry, "_log_code_revision", lambda: None)
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: None)

    def warn_while_loading():
        logging.getLogger("nexusvox.config").warning("moved the config aside")
        return Config()

    monkeypatch.setattr(entry, "load_config", warn_while_loading)
    own = logging.getLogger("nexusvox")
    previous = own.level
    own.setLevel(logging.INFO)  # the root logger is not configured here: basicConfig is stubbed
    try:
        with pytest.raises(SystemExit):
            entry.main()
    finally:
        own.setLevel(previous)
        sys.modules.pop("nexusvox.__main__", None)
    log = next((tmp_path / "localappdata" / "NexusVox" / "logs").glob("nexusvox.log*"))
    assert "moved the config aside" in log.read_text(encoding="utf-8")


def test_a_frozen_build_or_a_foreign_repository_runs_no_git(monkeypatch, tmp_path, caplog):
    import sys

    from nexusvox import __main__ as entry

    def no_git(*args, **kwargs):
        raise AssertionError("git must not run")

    package = tmp_path / "repo" / "src" / "nexusvox"
    package.mkdir(parents=True)
    assert entry._is_checkout(package) is False  # no .git two levels up: an installed copy
    (tmp_path / "repo" / ".git").mkdir()
    assert entry._is_checkout(package) is True
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert entry._is_checkout(package) is False

    monkeypatch.setattr(entry.subprocess, "run", no_git)
    monkeypatch.setattr(entry.threading, "Thread", no_git)
    monkeypatch.setattr(entry, "_is_checkout", lambda folder: False)
    with caplog.at_level("INFO", logger="nexusvox.__main__"):
        entry._log_code_revision()
    assert entry.NOT_A_CHECKOUT in caplog.text


def test_the_git_revision_is_logged_from_a_thread_in_a_checkout(monkeypatch):
    from nexusvox import __main__ as entry

    started = []

    class FakeThread:
        def __init__(self, **kwargs):
            started.append(kwargs)

        def start(self):
            pass

    monkeypatch.setattr(entry, "_is_checkout", lambda folder: True)
    monkeypatch.setattr(entry.threading, "Thread", FakeThread)
    entry._log_code_revision()
    assert started and started[0]["daemon"] is True
