"""Final composition: cut segments, reframe to vertical, burn captions, mix audio, encode."""

from pathlib import Path

from ..config import OutputCfg
from ..media import ffmpeg
from ..types import Span
from .templates import SUBJECT_AWARE, TEMPLATES

_AUDIO_FORMAT = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"


def render_video(
    *,
    source: Path,
    source_has_audio: bool,
    segments: list[Span],
    narration: Path,
    captions_name: str,
    template: str,
    out: OutputCfg,
    game_gain_db: float,
    dest: Path,
    work_dir: Path,
    crop_centres: list[float] | None = None,
) -> None:
    """Encode the final MP4. `captions_name` is an .ass file inside work_dir (ffmpeg runs there).

    `crop_centres` gives, per segment, the horizontal centre (0..1) of an output-shaped crop;
    it is required by subject-aware templates and ignored by the others.
    """
    if template in SUBJECT_AWARE and (crop_centres is None or len(crop_centres) != len(segments)):
        raise ValueError(f"template {template} needs one crop centre per segment")
    render_s = sum(segment.length for segment in segments)
    inputs, chains = [], []
    for index, segment in enumerate(segments):
        inputs += ["-ss", f"{segment.start:.3f}", "-t", f"{segment.length:.3f}", "-i", source.resolve()]
        crop = ""
        if template in SUBJECT_AWARE:
            centre = crop_centres[index]
            crop = (
                f",crop='min(iw,ih*{out.width}/{out.height})':ih:'min(max(iw*{centre}-ow/2,0),iw-ow)':0"
                f",scale={out.width}:{out.height}:force_original_aspect_ratio=increase,crop={out.width}:{out.height}"
            )
        chains.append(f"[{index}:v]setpts=PTS-STARTPTS,fps={out.fps},setsar=1{crop}[v{index}]")
        if source_has_audio:
            chains.append(f"[{index}:a]asetpts=PTS-STARTPTS,{_AUDIO_FORMAT}[a{index}]")
    count = len(segments)
    narration_index = count
    inputs += ["-i", narration.resolve()]

    chains.append("".join(f"[v{i}]" for i in range(count)) + f"concat=n={count}:v=1:a=0[vcat]")
    if source_has_audio:
        chains.append("".join(f"[a{i}]" for i in range(count)) + f"concat=n={count}:v=0:a=1[game_raw]")
    else:
        inputs += ["-f", "lavfi", "-t", f"{render_s:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        chains.append(f"[{count + 1}:a]{_AUDIO_FORMAT}[game_raw]")

    chains.append(TEMPLATES[template]("vcat", "vtpl", out.width, out.height))
    chains.append(f"[vtpl]fps={out.fps},format=yuv420p,ass={captions_name}[vout]")
    # Game audio is lowered, then ducked further whenever the narration is speaking.
    chains.append(f"[{narration_index}:a]{_AUDIO_FORMAT},asplit=2[narr_key][narr_mix]")
    chains.append(f"[game_raw]volume={game_gain_db}dB[game]")
    chains.append("[game][narr_key]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[ducked]")
    chains.append("[ducked][narr_mix]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95[aout]")

    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            *inputs,
            "-filter_complex", ";".join(chains),
            "-map", "[vout]", "-map", "[aout]",
            "-t", f"{render_s:.3f}",
            "-c:v", "libx264", "-preset", out.preset, "-crf", str(out.crf), "-pix_fmt", "yuv420p", "-r", str(out.fps),
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-movflags", "+faststart",
            dest.resolve(),
        ],
        cwd=work_dir,
    )
