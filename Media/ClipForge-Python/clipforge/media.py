"""Thin wrappers around the ffmpeg / ffprobe system binaries."""

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .errors import MediaError


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    format_name: str
    width: int | None
    height: int | None
    fps: float | None
    vcodec: str | None
    acodec: str | None

    @property
    def has_video(self) -> bool:
        return self.vcodec is not None

    @property
    def has_audio(self) -> bool:
        return self.acodec is not None


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise MediaError(f"{cmd[0]} is not installed or not on PATH") from exc
    if proc.returncode != 0:
        raise MediaError(f"{cmd[0]} failed ({proc.returncode}): {proc.stderr.strip()[-1500:]}")
    return proc


def ffmpeg(args: list, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return run(["ffmpeg", "-hide_banner", "-nostdin", "-y", *args], cwd=cwd)


def probe(path: Path | str) -> MediaInfo:
    proc = run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
    data = json.loads(proc.stdout)
    video = next((s for s in data.get("streams", []) if s["codec_type"] == "video"), None)
    audio = next((s for s in data.get("streams", []) if s["codec_type"] == "audio"), None)
    fps = None
    if video and video.get("avg_frame_rate") not in (None, "0/0"):
        fps = float(Fraction(video["avg_frame_rate"]))
    try:
        duration = float(data["format"]["duration"])
    except (KeyError, ValueError) as exc:
        raise MediaError(f"could not read the duration of {path}") from exc
    return MediaInfo(
        duration=duration,
        format_name=data["format"].get("format_name", ""),
        width=video["width"] if video else None,
        height=video["height"] if video else None,
        fps=fps,
        vcodec=video["codec_name"] if video else None,
        acodec=audio["codec_name"] if audio else None,
    )


def extract_frame(source: Path, at_s: float, dest: Path, width: int = 768) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(["-ss", f"{at_s:.3f}", "-i", source, "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", dest])
    return dest
