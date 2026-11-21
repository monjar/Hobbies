"""Subject-aware cropping: find where the motion is, so a 9:16 crop can follow it."""

import subprocess
from pathlib import Path

import numpy as np

from ..errors import MediaError
from ..types import Span

ANALYSIS_W, ANALYSIS_H, ANALYSIS_FPS = 160, 90, 4
MIN_MOTION = 0.004  # mean frame difference (0..1) below which a segment counts as static
MIN_CONCENTRATION = 0.55  # share of motion that must fall inside the crop for it to be trusted


def motion_focus(source: Path, segment: Span, crop_fraction: float) -> float | None:
    """Horizontal centre (0..1) for a crop `crop_fraction` of the frame wide, or None.

    None means there is no clear subject: the segment is static, or motion is spread across
    the frame (a camera pan, a cut), so any crop would be a guess.
    """
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{segment.start:.3f}",
         "-t", f"{segment.length:.3f}", "-i", str(source), "-vf",
         f"fps={ANALYSIS_FPS},scale={ANALYSIS_W}:{ANALYSIS_H},format=gray", "-f", "rawvideo", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise MediaError(f"could not analyse motion in {source}: {proc.stderr.decode(errors='replace')[-300:]}")
    count = len(proc.stdout) // (ANALYSIS_W * ANALYSIS_H)
    if count < 2:
        return None
    frames = np.frombuffer(proc.stdout[: count * ANALYSIS_W * ANALYSIS_H], dtype=np.uint8)
    frames = frames.reshape(count, ANALYSIS_H, ANALYSIS_W).astype(np.float32) / 255.0
    diffs = np.abs(np.diff(frames, axis=0))
    if float(diffs.mean()) < MIN_MOTION:
        return None
    columns = diffs.sum(axis=(0, 1))
    width = max(int(round(crop_fraction * ANALYSIS_W)), 1)
    if width >= ANALYSIS_W:
        return 0.5
    inside = np.convolve(columns, np.ones(width), mode="valid")  # motion captured by each crop position
    peak = float(inside.max())
    if peak / float(columns.sum()) < MIN_CONCENTRATION:
        return None
    # Many crop positions can hold all the motion; take the middle one so the subject is centred.
    holding = np.flatnonzero(inside >= 0.98 * peak)
    best = int(holding[len(holding) // 2])
    return round((best + width / 2) / ANALYSIS_W, 4)


def crop_fraction(source_w: int, source_h: int, out_w: int, out_h: int) -> float:
    """Width of the output-shaped crop as a share of the source width."""
    return min(1.0, (source_h * out_w / out_h) / source_w)
