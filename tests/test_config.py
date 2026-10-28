"""Tests for config loading, saving, and default values."""

from __future__ import annotations

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
    assert cfg.language == "en"
    assert cfg.injection_delay_ms == 500
    assert cfg.auto_language_detection is False
    assert cfg.voice_commands.enabled is True
    assert "slash" in cfg.voice_commands.symbols
    assert "plus" not in cfg.voice_commands.symbols
    assert cfg.hotkey.modifiers == ["ctrl", "shift", "alt"]
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
        language="de",
        injection_delay_ms=200,
        auto_language_detection=True,
        voice_commands=VoiceCommandsConfig(enabled=False, symbols=["slash", "plus"]),
        hotkey=HotkeyConfig(modifiers=["ctrl", "alt"], mode="toggle"),
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
        def __init__(self, config):
            pass

        def run(self):
            raise KeyboardInterrupt

    # The real app module binds PortAudio at import, which CI runners on Linux do not have.
    monkeypatch.setitem(sys.modules, "nexusvox.app", types.SimpleNamespace(NexusVoxApp=App))
    monkeypatch.delitem(sys.modules, "nexusvox.__main__", raising=False)
    entry = importlib.import_module("nexusvox.__main__")
    seen = {}
    own = logging.getLogger("nexusvox")
    previous = own.level
    monkeypatch.setattr(entry, "load_config", lambda: Config(log_level="DEBUG"))
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


def test_unusable_hotkey_falls_back_to_default(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[hotkey]\nmodifiers = []\nkey = ""\n')
    assert load_config(path).hotkey.modifiers == ["ctrl", "shift", "alt"]
    path.write_text('[hotkey]\nmodifiers = ["ctrl"]\nkey = "enter"\n')
    cfg = load_config(path)
    assert cfg.hotkey.modifiers == ["ctrl", "shift", "alt"] and cfg.hotkey.key == ""
    path.write_text('[hotkey]\nmodifiers = ["hyper"]\n')
    assert load_config(path).hotkey.modifiers == ["ctrl", "shift", "alt"]


def test_validate_hotkey():
    assert validate_hotkey(["ctrl", "shift"], "a") is None
    assert validate_hotkey([], "F9") is None
    assert validate_hotkey(["ctrl", "shift", "alt"], "") is None
    assert validate_hotkey([], "") is not None
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

    assert loaded.voice_commands.structural == ["new paragraph", "new line", "tab", "all caps"]
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


def test_llm_and_polish_key_roundtrip_and_defaults(tmp_path):
    path = tmp_path / "config.toml"
    llm = LlmConfig(url="http://127.0.0.1:1234/v1", protocol="openai", model="m", api_key='a"b', keep_alive="1m")
    save_config(Config(llm=llm, hotkey=HotkeyConfig(polish_key="P")), path)
    loaded = load_config(path)
    assert loaded.llm == llm and loaded.hotkey.polish_key == "P"

    path.write_text('[llm]\nprotocol = "grpc"\n', encoding="utf-8")
    loaded = load_config(path)
    assert loaded.llm == LlmConfig() and loaded.hotkey.polish_key == ""


def test_cleanup_roundtrip_and_unknown_level(tmp_path):
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


def test_feedback_mode_defaults_to_sound_and_round_trips(tmp_path):
    path = tmp_path / "config.toml"
    cfg = load_config(path)
    assert cfg.feedback.mode == "sound"
    cfg.feedback.mode = "both"
    save_config(cfg, path)
    assert load_config(path).feedback.mode == "both"


def test_unknown_feedback_mode_falls_back_to_sound(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[feedback]\nmode = "disco"\n', encoding="utf-8")
    assert load_config(path).feedback.mode == "sound"
