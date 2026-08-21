"""Tests for config loading, saving, and default values."""

from __future__ import annotations

import tomllib

import nexusvox.config as config_module
from nexusvox.config import (
    AudioConfig,
    Config,
    DatabaseConfig,
    HotkeyConfig,
    InferenceConfig,
    OSCommandsConfig,
    load_config,
    resolve_compute_type,
    resolve_device,
    save_config,
)


def test_config_dataclass_defaults():
    cfg = Config()
    assert cfg.language == "en"
    assert cfg.injection_delay_ms == 500
    assert cfg.auto_language_detection is False
    assert cfg.voice_commands.enabled is True
    assert "slash" in cfg.voice_commands.symbols
    assert "plus" not in cfg.voice_commands.symbols
    assert cfg.hotkey.modifiers == ["ctrl", "shift", "alt"]
    assert cfg.audio.sample_rate == 16_000
    assert cfg.audio.chunk_size == 4096
    assert cfg.inference.server_url == "ws://localhost:8000/v1/realtime"
    assert cfg.inference.transcription_delay_ms == 480
    assert cfg.inference.model == "voxtral-mini-4b"
    assert cfg.database.path == "nexusvox.db"
    assert cfg.database.audio_dir == "audio"
    assert cfg.os_commands.enabled is False
    assert cfg.os_commands.apps == {}


def test_load_config_defaults_when_no_file(tmp_path, monkeypatch):
    """When neither config nor example exist, return all defaults."""
    monkeypatch.setattr(config_module, "EXAMPLE_CONFIG_PATH", tmp_path / "nonexistent.toml")

    cfg = load_config(tmp_path / "config.toml")

    assert cfg.language == "en"
    assert cfg.audio.sample_rate == 16_000
    assert cfg.hotkey.modifiers == ["ctrl", "shift", "alt"]


def test_load_config_copies_example(tmp_path, monkeypatch):
    """When config is missing but example exists, copy example and load it."""
    example = tmp_path / "config.example.toml"
    example.write_text(
        '[general]\nlanguage = "de"\ninjection_delay_ms = 300\nauto_language_detection = true\n'
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
    assert cfg.language == "de"
    assert cfg.injection_delay_ms == 300
    assert cfg.audio.sample_rate == 48000


def test_load_config_from_valid_toml(tmp_path):
    """Full TOML with non-default values is parsed correctly."""
    toml_content = """\
[general]
language = "de"
injection_delay_ms = 200
auto_language_detection = true

[hotkey]
modifiers = ["ctrl", "alt"]

[audio]
sample_rate = 44100
chunk_size = 2048

[inference]
server_url = "ws://gpu-server:8000/v1/realtime"
transcription_delay_ms = 240
model = "cohere-transcribe"

[database]
path = "test.db"
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(toml_content, encoding="utf-8")

    cfg = load_config(config_path)

    assert cfg.language == "de"
    assert cfg.injection_delay_ms == 200
    assert cfg.auto_language_detection is True
    assert cfg.hotkey.modifiers == ["ctrl", "alt"]
    assert cfg.audio.sample_rate == 44100
    assert cfg.audio.chunk_size == 2048
    assert cfg.inference.server_url == "ws://gpu-server:8000/v1/realtime"
    assert cfg.inference.transcription_delay_ms == 240
    assert cfg.inference.model == "cohere-transcribe"
    assert cfg.database.path == "test.db"


def test_load_config_missing_sections_uses_defaults(tmp_path):
    """Partial TOML with only [general] — other sections get defaults."""
    config_path = tmp_path / "config.toml"
    config_path.write_text('[general]\nlanguage = "de"\n', encoding="utf-8")

    cfg = load_config(config_path)

    assert cfg.language == "de"
    assert cfg.audio.sample_rate == 16_000
    assert cfg.audio.chunk_size == 4096
    assert cfg.hotkey.modifiers == ["ctrl", "shift", "alt"]
    assert cfg.inference.server_url == "ws://localhost:8000/v1/realtime"


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
        language="de",
        injection_delay_ms=200,
        auto_language_detection=True,
        voice_commands=VoiceCommandsConfig(enabled=False, symbols=["slash", "plus"]),
        hotkey=HotkeyConfig(modifiers=["ctrl", "alt"]),
        audio=AudioConfig(sample_rate=44100, chunk_size=2048),
        inference=InferenceConfig(
            server_url="ws://test:8000/v1/realtime", transcription_delay_ms=160, model="cohere-transcribe"
        ),
        database=DatabaseConfig(path="roundtrip.db", audio_dir="recordings"),
    )

    config_path = tmp_path / "config.toml"
    save_config(original, config_path)
    loaded = load_config(config_path)

    assert loaded.language == original.language
    assert loaded.injection_delay_ms == original.injection_delay_ms
    assert loaded.auto_language_detection == original.auto_language_detection
    assert loaded.voice_commands.enabled == original.voice_commands.enabled
    assert loaded.voice_commands.symbols == original.voice_commands.symbols
    assert loaded.hotkey.modifiers == original.hotkey.modifiers
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
