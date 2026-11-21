"""TTS interface, plus placing synthesised lines on the render timeline."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..errors import ValidationFailed
from ..rules import MAX_NARRATION_TEMPO
from ..types import Span, Word

LINE_GAP_S = 0.12  # minimum pause between narration lines
TAIL_S = 0.1  # narration must end this long before the render does


@dataclass
class TTSResult:
    path: Path
    duration: float
    words: list[Word]  # timings relative to the start of this line's audio
    usd: float
    chars: int


class TTSProvider(Protocol):
    name: str
    model: str

    def estimate_usd(self, text: str) -> float:
        """Price of synthesising `text`, known before the call so the cost cap can stop it."""

    def synthesize(self, text: str, out_path: Path, previous_text: str | None = None) -> TTSResult:
        """Write narration audio for `text` and return word timings."""


@dataclass
class NarrationLayout:
    starts: list[float]  # where each line begins on the render timeline
    tempo: float  # 1.0 unless narration had to be sped up to fit
    packed: bool  # True when target start times were dropped to make it fit

    def span(self, index: int, duration: float) -> Span:
        return Span(self.starts[index], self.starts[index] + duration / self.tempo)


def layout_narration(durations: list[float], targets: list[float], render_s: float) -> NarrationLayout:
    """Place lines at their target times, pushing later lines back so none overlap.

    If that runs past the end, pack the lines back to back and speed them up by at most
    MAX_NARRATION_TEMPO. Anything beyond that fails instead of producing a rushed video.
    """
    starts, cursor = [], 0.0
    for duration, target in zip(durations, targets, strict=True):
        start = max(target, cursor)
        starts.append(start)
        cursor = start + duration + LINE_GAP_S
    available = render_s - TAIL_S
    if starts[-1] + durations[-1] <= available:
        return NarrationLayout(starts=starts, tempo=1.0, packed=False)

    gaps = LINE_GAP_S * (len(durations) - 1)
    tempo = max(sum(durations) / (available - gaps), 1.0)
    if tempo > MAX_NARRATION_TEMPO:
        raise ValidationFailed(
            [
                f"narration runs {sum(durations):.1f}s against a {render_s:.1f}s clip; fitting it needs "
                f"{tempo:.2f}x speed, above the {MAX_NARRATION_TEMPO}x limit"
            ]
        )
    starts, cursor = [], 0.0
    for duration in durations:
        starts.append(cursor)
        cursor += duration / tempo + LINE_GAP_S
    return NarrationLayout(starts=starts, tempo=tempo, packed=True)


def timeline_words(line_words: list[list[Word]], layout: NarrationLayout) -> list[Word]:
    """Word timings of every line, moved onto the render timeline."""
    words = []
    for start, line in zip(layout.starts, line_words, strict=True):
        for word in line:
            words.append(Word(word.text, start + word.start / layout.tempo, start + word.end / layout.tempo))
    return words


def coverage(spans: list[Span], render_s: float) -> float:
    """Share of the render during which narration is speaking."""
    total, cursor = 0.0, 0.0
    for span in sorted(spans, key=lambda s: s.start):
        start, end = max(span.start, cursor), min(span.end, render_s)
        if end > start:
            total += end - start
            cursor = end
    return total / render_s
