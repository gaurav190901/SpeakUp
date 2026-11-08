"""
Audio decoding that works across PyAV versions.

faster-whisper's own ``decode_audio`` calls ``av.open(..., metadata_errors=...)``,
an argument newer PyAV releases (16+) no longer accept, which makes every
file-path transcription fail. This module decodes with PyAV directly so the
app does not depend on that call.
"""

from __future__ import annotations

import gc
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000


def decode_audio(path: str | Path, sampling_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Decode any audio/video file to a mono float32 array in [-1, 1].

    Raises:
        ValueError: if the file has no decodable audio stream.
    """
    import av  # noqa: PLC0415 — heavy import, only needed here

    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=sampling_rate)
    chunks: list[np.ndarray] = []
    try:
        with av.open(str(path), mode="r") as container:
            if not container.streams.audio:
                raise ValueError(f"No audio stream in {Path(path).name}")
            for frame in container.decode(audio=0):
                frame.pts = None
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray().reshape(-1))
            for out in resampler.resample(None):      # flush
                chunks.append(out.to_ndarray().reshape(-1))
    except (av.error.FFmpegError, av.error.InvalidDataError) as exc:  # type: ignore[attr-defined]
        raise ValueError(f"Could not decode {Path(path).name}: {exc}") from exc
    finally:
        del resampler
        gc.collect()

    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0
