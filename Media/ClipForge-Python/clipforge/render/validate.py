"""Render validation: the narration and segment rules before encoding, the output spec after."""

from dataclasses import dataclass
from pathlib import Path

from ..config import OutputCfg
from ..errors import ValidationFailed
from ..media import probe
from ..rules import MAX_SOURCE_SEGMENT_S, MIN_NARRATION_COVERAGE
from ..types import Span
from ..voice.tts import coverage
from .cut import longest_uninterrupted

DURATION_TOLERANCE_S = 0.15  # container and frame rounding


@dataclass(frozen=True)
class PlanReport:
    render_s: float
    narration_coverage: float
    max_source_segment_s: float


def check_plan(segments: list[Span], narration_spans: list[Span], out: OutputCfg) -> PlanReport:
    """PLAN.md constraint 3 plus the length range. Raises before any encoding happens."""
    if not segments:
        raise ValidationFailed(["no source segments to render"])
    render_s = sum(segment.length for segment in segments)
    report = PlanReport(render_s, coverage(narration_spans, render_s), longest_uninterrupted(segments))
    errors = []
    if not out.min_s <= render_s <= out.max_s:
        errors.append(f"render would be {render_s:.1f}s; allowed range is {out.min_s:.0f}-{out.max_s:.0f}s")
    if report.narration_coverage < MIN_NARRATION_COVERAGE:
        errors.append(
            f"narration covers {report.narration_coverage:.0%} of the render; "
            f"at least {MIN_NARRATION_COVERAGE:.0%} is required"
        )
    if report.max_source_segment_s > MAX_SOURCE_SEGMENT_S:
        errors.append(
            f"an uninterrupted source segment runs {report.max_source_segment_s:.1f}s; "
            f"the limit is {MAX_SOURCE_SEGMENT_S:.0f}s"
        )
    if errors:
        raise ValidationFailed(errors)
    return report


def check_output(path: Path, out: OutputCfg, expected_s: float) -> None:
    """The encoded file must match the output spec (PLAN.md section 6)."""
    info = probe(path)
    errors = []
    if "mp4" not in info.format_name:
        errors.append(f"container is {info.format_name}, expected mp4")
    if (info.width, info.height) != (out.width, out.height):
        errors.append(f"resolution is {info.width}x{info.height}, expected {out.width}x{out.height}")
    if info.fps is None or abs(info.fps - out.fps) > 0.01:
        errors.append(f"frame rate is {info.fps}, expected {out.fps}")
    if info.vcodec != "h264":
        errors.append(f"video codec is {info.vcodec}, expected h264")
    if info.acodec != "aac":
        errors.append(f"audio codec is {info.acodec}, expected aac")
    if abs(info.duration - expected_s) > DURATION_TOLERANCE_S:
        errors.append(f"duration is {info.duration:.2f}s, expected {expected_s:.2f}s")
    if not out.min_s - DURATION_TOLERANCE_S <= info.duration <= out.max_s + DURATION_TOLERANCE_S:
        errors.append(f"duration {info.duration:.2f}s is outside {out.min_s:.0f}-{out.max_s:.0f}s")
    if errors:
        raise ValidationFailed(errors)
