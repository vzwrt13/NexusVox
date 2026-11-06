"""`[audio]` and `[database]`: how recordings are captured and where they are kept."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AudioConfig:
    sample_rate: int = 16_000
    chunk_size: int = 4096


@dataclass
class DatabaseConfig:
    path: str = "nexusvox.db"
    audio_dir: str = "audio"
    # Keep a WAV of every recording next to the database (the Review tab needs it).
    store_audio: bool = True
