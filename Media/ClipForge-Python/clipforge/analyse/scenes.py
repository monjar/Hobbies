"""Shot boundary detection (PySceneDetect)."""

from pathlib import Path

from ..errors import MediaError


def detect_cuts(path: Path) -> list[float]:
    """Times in seconds where a new shot starts, excluding the start of the file."""
    from scenedetect import ContentDetector, detect

    try:
        scenes = detect(str(path), ContentDetector())
    except Exception as exc:
        raise MediaError(f"scene detection failed for {path}: {exc}") from exc
    return sorted({round(start.seconds, 3) for start, _end in scenes if start.seconds > 0})
