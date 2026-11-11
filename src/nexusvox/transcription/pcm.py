"""PCM helpers shared by the upload and meeting paths: downmix to mono and resample to 16 kHz."""

from __future__ import annotations

import numpy as np

TARGET_RATE = 16_000


def downmix(raw: bytes, channels: int) -> np.ndarray:
    """Little-endian PCM16 frames as one float32 mono array (channels averaged, a partial last frame cut)."""
    samples = np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float32)
    if channels > 1:
        samples = samples[: len(samples) // channels * channels]
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples


def resample(samples: np.ndarray, rate: int) -> np.ndarray:
    """`samples` at `rate` as 16 kHz int16."""
    if len(samples) == 0:
        return np.zeros(0, dtype=np.int16)
    if rate != TARGET_RATE:
        out_len = max(1, round(len(samples) * TARGET_RATE / rate))
        samples = np.interp(np.linspace(0, len(samples) - 1, out_len), np.arange(len(samples)), samples)
    return np.clip(np.rint(samples), -32768, 32767).astype(np.int16)
