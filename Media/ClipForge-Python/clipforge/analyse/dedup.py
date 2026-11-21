"""Duplicate detection: perceptual hashes of keyframes from each window that reaches a render."""

import subprocess
from pathlib import Path

import numpy as np

from ..db import DB
from ..errors import MediaError
from ..types import Span

FRAMES_PER_WINDOW = 5
HAMMING_THRESHOLD = 10  # of 64 bits; frames this close count as the same picture
MATCH_FRACTION = 0.6  # share of a window's frames that must match for it to be a duplicate
# Jobs whose windows are "taken". Failed and rejected jobs free their footage for another try.
LIVE_STATES = ("analysed", "scripted", "voiced", "rendered", "pending_review", "approved", "published")


def frame_hash(source: Path, at_s: float) -> int:
    """64-bit difference hash of one frame: each bit says whether a pixel is brighter than its neighbour."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{at_s:.3f}", "-i", str(source),
         "-frames:v", "1", "-vf", "scale=9:8:flags=area,format=gray", "-f", "rawvideo", "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or len(proc.stdout) != 72:
        raise MediaError(f"could not hash a frame of {source} at {at_s:.1f}s")
    pixels = np.frombuffer(proc.stdout, dtype=np.uint8).reshape(8, 9).astype(np.int16)
    bits = (pixels[:, 1:] > pixels[:, :-1]).flatten()
    return int("".join("1" if bit else "0" for bit in bits), 2)


def window_hashes(source: Path, span: Span) -> list[int]:
    step = span.length / (FRAMES_PER_WINDOW + 1)
    return [frame_hash(source, span.start + step * (i + 1)) for i in range(FRAMES_PER_WINDOW)]


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def is_duplicate(hashes: list[int], known: list[int]) -> bool:
    matched = sum(1 for h in hashes if any(hamming(h, k) <= HAMMING_THRESHOLD for k in known))
    return matched >= MATCH_FRACTION * len(hashes)


def duplicate_of(db: DB, hashes: list[int], exclude_job: str) -> str | None:
    """ID of an existing job whose window shows the same footage, if any."""
    marks = ", ".join("?" for _ in LIVE_STATES)
    rows = db.all(
        f"SELECT k.job_id, k.hash FROM keyframe_hashes k JOIN jobs j ON j.id = k.job_id "
        f"WHERE j.state IN ({marks}) AND j.id != ? ORDER BY k.id",
        *LIVE_STATES, exclude_job,
    )
    by_job: dict[str, list[int]] = {}
    for row in rows:
        by_job.setdefault(row["job_id"], []).append(int(row["hash"], 16))
    return next((job_id for job_id, known in by_job.items() if is_duplicate(hashes, known)), None)


def store_hashes(db: DB, job_id: str, hashes: list[int]) -> None:
    db.conn.execute("DELETE FROM keyframe_hashes WHERE job_id = ?", (job_id,))
    for value in hashes:
        db.insert("keyframe_hashes", job_id=job_id, hash=f"{value:016x}")
