"""Narration track assembly and loudness normalisation."""

import json
import re
from pathlib import Path

from ..errors import MediaError
from ..media import ffmpeg
from ..rules import NARRATION_LUFS

_AUDIO_FORMAT = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
_LOUDNORM = f"loudnorm=I={NARRATION_LUFS}:TP=-1.5:LRA=11"


def _loudnorm_stats(stderr: str) -> dict:
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr)
    if not blocks:
        raise MediaError("ffmpeg loudnorm did not report measurements")
    return json.loads(blocks[-1])


def build_narration(lines: list[tuple[Path, float]], tempo: float, render_s: float, work_dir: Path) -> tuple[Path, float]:
    """Place each line's audio at its start time, then normalise the track to NARRATION_LUFS.

    Returns the narration wav (exactly render_s long) and its measured integrated loudness.
    """
    raw = work_dir / "narration_raw.wav"
    final = work_dir / "narration.wav"
    inputs, chains = [], []
    for index, (path, start) in enumerate(lines):
        inputs += ["-i", path]
        chains.append(f"[{index}:a]atempo={tempo:.4f},{_AUDIO_FORMAT},adelay={round(start * 1000)}:all=1[l{index}]")
    labels = "".join(f"[l{i}]" for i in range(len(lines)))
    merge = f"amix=inputs={len(lines)}:normalize=0:duration=longest," if len(lines) > 1 else ""
    chains.append(f"{labels}{merge}apad,atrim=duration={render_s:.3f}[out]")
    ffmpeg([*inputs, "-filter_complex", ";".join(chains), "-map", "[out]", "-c:a", "pcm_s16le", raw])

    # Two-pass loudnorm: measure, then apply a linear gain to the target.
    measured = _loudnorm_stats(ffmpeg(["-i", raw, "-af", f"{_LOUDNORM}:print_format=json", "-f", "null", "-"]).stderr)
    second = (
        f"{_LOUDNORM}:measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}:linear=true:print_format=json"
    )
    result = _loudnorm_stats(ffmpeg(["-i", raw, "-af", second, "-ar", "48000", "-c:a", "pcm_s16le", final]).stderr)
    return final, float(result["output_i"])
