"""`nexusvox.config` stays the one import path after its sections moved to `config_sections`."""

import ast
from pathlib import Path

import nexusvox.config as config_module
from nexusvox import config_sections

# Every name the module had before the split, private ones included: nexusvox-pro, the scripts and the tests
# import from here. A name may be added; dropping one breaks an import outside this repository.
EXPORTED = """
    AssistantConfig AudioConfig CLEANUP_LEVELS CLEANUP_TIMEOUT_CPU_S COMMAND_KEY_VKS CUSTOM_PROTOCOLS CleanupConfig
    Config CustomServerConfig DEFAULT_CONFIG_PATH DEFAULT_HOTKEY_MODIFIERS DEFAULT_MEETING_KEY
    DEFAULT_MEETING_MODIFIERS DEFAULT_MODEL DEFAULT_OWN_VOICE_LABEL DatabaseConfig EXAMPLE_CONFIG_PATH
    ExperimentalConfig FEEDBACK_MODES FeedbackConfig HOTKEY_KEY_VKS HOTKEY_MODES HOTKEY_MODIFIERS HotkeyConfig
    InferenceConfig LEGACY_HOTKEY_MODIFIERS LEGACY_MEETING_KEY LEGACY_MEETING_MODIFIERS LLM_E4B_MIN_VRAM_GB
    LLM_MODEL_CPU LLM_PROTOCOLS LOG_LEVELS LearnFromEditsConfig LlmConfig MAX_SPEAKER_LABEL_CHARS MODEL_REGISTRY
    MeetingConfig MicGuardConfig OSCommandsConfig OsControlConfig Path PolishConfig RemoteMicConfig SupportConfig
    TranslatorConfig VoiceCommandsConfig _DEFAULT_STRUCTURAL _DEFAULT_SYMBOLS _MEETING_HOTKEY_FALLBACKS
    _REMOVED_MODEL_URLS _WARNED_REMOVED_MODELS _cleanup_config _config_lock _experimental_flag _feedback_form_url
    _llm_model _log_level _max_recording_s _own_voice_label _polish_key _vram_gb _vram_probed annotations
    auto_cleanup_timeout auto_llm_model cuda_available dataclass default_config_path default_meeting_hotkey field
    gpu_vram_gb json llm_runs_fast load_config logger logging math meeting_hotkey_clash model_download_state
    model_needs_docker normalize_hotkey_key normalize_speaker_label numbered_speaker_label own_voice_label_problem
    resolve_compute_type resolve_data_path resolve_device save_config shutil subprocess threading tomllib
    validate_hotkey validate_meeting_hotkey with_hardware_defaults
""".split()


def test_every_name_is_still_exported():
    assert [name for name in EXPORTED if not hasattr(config_module, name)] == []


def test_a_section_never_imports_the_module_that_re_exports_it():
    for path in Path(config_sections.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert node.level <= 1 and not (node.module or "").startswith("nexusvox"), path.name
            if isinstance(node, ast.Import):
                assert not any(alias.name.startswith("nexusvox") for alias in node.names), path.name


def test_a_patched_hardware_probe_reaches_the_defaults(monkeypatch):
    monkeypatch.setattr(config_module, "cuda_available", lambda: False)
    assert config_module.auto_llm_model() == config_module.LLM_MODEL_CPU
    assert config_module.auto_cleanup_timeout() == config_module.CLEANUP_TIMEOUT_CPU_S

    monkeypatch.setattr(config_module, "cuda_available", lambda: True)
    monkeypatch.setattr(config_module, "gpu_vram_gb", lambda: 16.0)
    assert config_module.auto_llm_model() == config_module.LlmConfig.model
    assert config_module.auto_cleanup_timeout() == config_module.CleanupConfig.timeout_s
