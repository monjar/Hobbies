"""Per-second audio loudness of a source file, used as an action signal for highlight ranking."""

import subprocess
from pathlib import Path

import numpy as np

from ..errors import MediaError

SAMPLE_RATE = 8000


def energy_per_second(path: Path) -> list[float]:
    """RMS level (0..1) for each second of audio. Empty when the file has no audio stream."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        message = proc.stderr.decode(errors="replace").strip()
        if "does not contain any stream" in message or "matches no streams" in message:
            return []
        raise MediaError(f"could not read audio from {path}: {message[-300:]}")
    samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float64) / 32768.0
    seconds = len(samples) // SAMPLE_RATE
    if seconds == 0:
        return []
    chunks = samples[: seconds * SAMPLE_RATE].reshape(seconds, SAMPLE_RATE)
    return [round(float(v), 6) for v in np.sqrt((chunks**2).mean(axis=1))]


def mean_energy(energy: list[float], start: float, end: float) -> float:
    window = energy[int(start) : max(int(end), int(start) + 1)]
    return float(np.mean(window)) if window else 0.0
