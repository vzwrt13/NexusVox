"""Frozen (installed app) and source path resolution."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from nexusvox import config as config_module
from nexusvox import docker_ctl, paths
from nexusvox.config import load_config
from nexusvox.transcribers import local_whisper


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    return tmp_path


def test_source_mode_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.chdir(tmp_path)
    assert not paths.is_frozen()
    assert paths.default_config_path() == Path("config.toml")
    assert paths.config_dir() == tmp_path
    assert paths.data_dir() == tmp_path
    assert paths.models_dir() is None
    assert paths.resolve_data_path("nexusvox.db") == "nexusvox.db"
    paths.ensure_dirs()
    assert list(tmp_path.iterdir()) == []
    assert local_whisper._download_root() is None


def test_frozen_folders(frozen):
    assert paths.config_dir() == frozen / "roaming" / "NexusVox"
    assert paths.default_config_path() == frozen / "roaming" / "NexusVox" / "config.toml"
    assert paths.data_dir() == frozen / "local" / "NexusVox" / "data"
    assert paths.models_dir() == frozen / "local" / "NexusVox" / "models"
    assert local_whisper._download_root() == str(frozen / "local" / "NexusVox" / "models")


def test_frozen_first_start_creates_folders(frozen):
    paths.ensure_dirs()
    assert paths.config_dir().is_dir()
    assert paths.data_dir().is_dir()
    assert paths.models_dir().is_dir()


def test_frozen_apply_models_dir_sets_hf_cache(frozen, monkeypatch):
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    paths.apply_models_dir()
    assert os.environ["HF_HUB_CACHE"] == str(paths.models_dir())


def test_frozen_apply_models_dir_moves_the_xet_cache_next_to_the_models(frozen, monkeypatch):
    monkeypatch.delenv("HF_XET_CACHE", raising=False)
    paths.apply_models_dir()
    assert os.environ["HF_XET_CACHE"] == str(paths.models_dir().parent / "models-xet")


def test_from_source_apply_models_dir_leaves_the_xet_cache_alone(monkeypatch):
    monkeypatch.delenv("HF_XET_CACHE", raising=False)
    paths.apply_models_dir()
    assert "HF_XET_CACHE" not in os.environ


def test_frozen_load_config_creates_config_and_resolves_database(frozen):
    cfg = load_config()
    config_file = paths.config_dir() / "config.toml"
    assert config_file.is_file()
    assert cfg.source_path == config_file.resolve()
    assert Path(cfg.database.path) == paths.data_dir() / "nexusvox.db"


def test_frozen_absolute_database_path_is_kept(frozen):
    absolute = str(frozen / "elsewhere" / "my.db")
    assert paths.resolve_data_path(absolute) == absolute


def test_source_load_config_keeps_relative_database(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    cfg = load_config(tmp_path / "config.toml")
    assert cfg.database.path == "nexusvox.db"
    assert config_module.DEFAULT_CONFIG_PATH == Path("config.toml")


def test_frozen_docker_says_not_available(frozen):
    with pytest.raises(RuntimeError, match="not available in the installed app"):
        docker_ctl.start_profile("parakeet")
    with pytest.raises(RuntimeError, match="Advanced docs"):
        docker_ctl.stop_profile("parakeet")
