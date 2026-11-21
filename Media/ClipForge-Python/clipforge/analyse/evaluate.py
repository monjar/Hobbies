"""Evaluation harnesses for M2: highlight ranking against hand labels, and the spoiler classifier."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..errors import ConfigError
from ..media import probe
from ..providers.base import LLMProvider
from ..types import Span
from . import spoiler
from .audio import energy_per_second
from .highlights import candidate_windows
from .scenes import detect_cuts
from .transcribe import Transcriber

MATCH_OVERLAP = 0.5  # share of the labelled highlight that the top window must cover
HIGHLIGHT_PASS_RATE = 0.7  # PLAN.md M2: 7 of 10


def covers(window: Span, highlight: Span) -> bool:
    shared = min(window.end, highlight.end) - max(window.start, highlight.start)
    return shared >= MATCH_OVERLAP * highlight.length


@dataclass(frozen=True)
class HighlightResult:
    file: str
    top_window: Span
    matched: bool


def load_highlight_labels(path: Path) -> list[tuple[Path, list[Span]]]:
    """Labels file: `clips: [{file: library/abc.mp4, highlights: [[12.0, 40.5], ...]}]`."""
    data = yaml.safe_load(path.read_text()) or {}
    clips = []
    for entry in data.get("clips", []):
        spans = [Span(float(a), float(b)) for a, b in entry.get("highlights", [])]
        if not spans or any(s.length <= 0 for s in spans):
            raise ConfigError(f"{path}: {entry.get('file')} needs at least one [start, end] highlight")
        clips.append((Path(entry["file"]), spans))
    if not clips:
        raise ConfigError(f"{path} lists no clips")
    return clips


def evaluate_highlights(
    clips: list[tuple[Path, list[Span]]], root: Path, transcriber: Transcriber, min_len: float, max_len: float
) -> list[HighlightResult]:
    """Rank windows for each clip with the heuristic score and compare the top one to the labels."""
    results = []
    for file, highlights in clips:
        source = file if file.is_absolute() else root / file
        top = candidate_windows(
            probe(source).duration, detect_cuts(source), transcriber.transcribe(source), min_len, max_len,
            top_k=1, energy=energy_per_second(source),
        )[0]
        results.append(HighlightResult(str(file), top.span, any(covers(top.span, h) for h in highlights)))
    return results


@dataclass(frozen=True)
class SpoilerResult:
    name: str
    expected: str
    predicted: str

    @property
    def correct(self) -> bool:
        return self.expected == self.predicted

    @property
    def leaked(self) -> bool:
        """A spoiler that the classifier would have let through. This is the failure that matters."""
        return self.expected != spoiler.SAFE and self.predicted == spoiler.SAFE


def load_spoiler_cases(path: Path) -> list[dict]:
    cases = (yaml.safe_load(path.read_text()) or {}).get("cases", [])
    for case in cases:
        if case.get("label") not in (spoiler.SAFE, *spoiler.BLOCKED) or not case.get("description"):
            raise ConfigError(f"{path}: case {case.get('name')!r} needs a description and a known label")
    if not cases:
        raise ConfigError(f"{path} lists no cases")
    return cases


def evaluate_spoilers(cases: list[dict], llm: LLMProvider) -> tuple[list[SpoilerResult], float]:
    results, usd = [], 0.0

    class _Meter:  # counts spend without a job to attach it to
        def add(self, provider, task, model, cost, detail=None):
            nonlocal usd
            usd += cost

    for case in cases:
        verdict = spoiler.classify(
            description=case["description"], transcript=case.get("transcript", ""), llm=llm, costs=_Meter()
        )
        results.append(SpoilerResult(case["name"], case["label"], verdict.label))
    return results, usd
