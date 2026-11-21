"""Find the best window of footage: heuristic candidates, then an LLM pick over transcript + frames."""

from dataclasses import dataclass
from pathlib import Path

from ..costs import CostTracker
from ..errors import ProviderError, ValidationFailed
from ..media import extract_frame
from ..providers.base import LLMProvider, VisionProvider
from ..types import Span, Word
from .audio import mean_energy
from .transcribe import excerpt

FRAME_POSITIONS = (0.2, 0.5, 0.8)
# (shot changes, speech, audio level)
WEIGHTS_WITH_AUDIO = (0.4, 0.3, 0.3)
WEIGHTS_NO_AUDIO = (0.5, 0.5, 0.0)
GRID_STEP_S = 5  # extra window start points, for footage with few or no shot changes

PICK_SCHEMA = {
    "type": "object",
    "properties": {"choice": {"type": "integer"}, "reason": {"type": "string"}},
    "required": ["choice", "reason"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Candidate:
    span: Span
    score: float
    cut_rate: float  # shot changes per second
    word_rate: float  # spoken words per second
    energy: float = 0.0  # mean audio level, 0..1


def candidate_windows(
    duration: float,
    cuts: list[float],
    words: list[Word],
    min_len: float,
    max_len: float,
    top_k: int = 3,
    energy: list[float] | None = None,
) -> list[Candidate]:
    """Windows of min_len..max_len seconds starting on a shot boundary or a 5 s grid point, best first.

    Score is action density: shot-change rate, audio level and speech rate, each normalised
    to the best window. `energy` is the per-second audio level; without it the score uses
    shot changes and speech only. Overlapping windows are dropped so the candidates show
    different moments.
    """
    if duration < min_len:
        raise ValidationFailed([f"source is {duration:.1f}s long; at least {min_len:.1f}s is needed"])
    target = min((min_len + max_len) / 2, duration)
    # Shot boundaries plus a regular grid, so long single-shot gameplay still gets several candidates.
    grid = [float(t) for t in range(0, int(duration), GRID_STEP_S)]
    boundaries = sorted({0.0, *[c for c in cuts if 0 < c < duration], *grid})
    ends = [c for c in cuts if 0 < c < duration] + [duration]

    spans = set()
    for start in boundaries:
        if duration - start < min_len:
            continue
        fitting = [end for end in ends if min_len <= end - start <= max_len]
        # Prefer ending on a shot boundary; otherwise cut at the target length.
        end = min(fitting, key=lambda e: abs((e - start) - target)) if fitting else min(start + target, duration)
        spans.add((round(start, 3), round(end, 3)))
    if not spans:  # only happens when every boundary is too close to the end
        spans.add((round(duration - target, 3), round(duration, 3)))

    raw = []
    for start, end in sorted(spans):
        length = end - start
        cut_rate = sum(1 for c in cuts if start < c < end) / length
        word_rate = sum(1 for w in words if start <= w.start < end) / length
        raw.append((Span(start, end), cut_rate, word_rate, mean_energy(energy, start, end) if energy else 0.0))
    max_cut = max(r[1] for r in raw) or 1.0
    max_word = max(r[2] for r in raw) or 1.0
    max_energy = max(r[3] for r in raw) or 1.0
    w_cut, w_word, w_energy = WEIGHTS_WITH_AUDIO if energy else WEIGHTS_NO_AUDIO
    scored = [
        Candidate(
            span,
            round(w_cut * cut / max_cut + w_word * word / max_word + w_energy * level / max_energy, 6),
            cut, word, round(level, 6),
        )
        for span, cut, word, level in raw
    ]
    scored.sort(key=lambda c: (-c.score, c.span.start))

    chosen: list[Candidate] = []
    for candidate in scored:
        if all(_overlap(candidate.span, other.span) <= 0.5 for other in chosen):
            chosen.append(candidate)
        if len(chosen) == top_k:
            break
    return chosen


def _overlap(a: Span, b: Span) -> float:
    shared = min(a.end, b.end) - max(a.start, b.start)
    return max(shared, 0.0) / min(a.length, b.length)


@dataclass(frozen=True)
class Pick:
    candidate: Candidate
    description: str
    transcript: str
    reason: str


def pick_window(
    *,
    source: Path,
    candidates: list[Candidate],
    words: list[Word],
    angle: str,
    llm: LLMProvider,
    vision: VisionProvider,
    costs: CostTracker,
    frames_dir: Path,
) -> Pick:
    """Describe sampled frames of each candidate, then let the LLM choose one."""
    described = []
    for index, candidate in enumerate(candidates):
        span = candidate.span
        frames = [
            extract_frame(source, span.start + span.length * pos, frames_dir / f"cand{index}_{n}.jpg")
            for n, pos in enumerate(FRAME_POSITIONS)
        ]
        result = vision.describe(
            frames=frames,
            prompt=(
                "These are three frames, in order, from one stretch of gameplay footage. In 3-5 sentences, "
                "describe only what is visible: setting, characters, actions, on-screen text, HUD. "
                "Do not guess the story context."
            ),
        )
        costs.add(vision.name, "describe_frames", result.model, result.usd, result.detail)
        described.append((candidate, result.text, excerpt(words, span.start, span.end)))

    listing = "\n\n".join(
        f"Candidate {i}: {c.span.start:.1f}s-{c.span.end:.1f}s, {c.cut_rate * 60:.0f} shot changes/min, "
        f"{c.word_rate * 60:.0f} words/min\nFrames: {description}\nTranscript: {transcript or '(no speech)'}"
        for i, (c, description, transcript) in enumerate(described)
    )
    result = llm.complete_json(
        task="pick_window",
        system=(
            "You pick the stretch of gameplay footage that will make the strongest narrated short video. "
            "Prefer visible action and concrete things a narrator can point at."
        ),
        user=f"Narration angle: {angle}\n\n{listing}\n\nReturn the index of the best candidate and a one-sentence reason.",
        schema=PICK_SCHEMA,
    )
    costs.add(llm.name, "pick_window", result.model, result.usd, result.detail)
    choice = result.data["choice"]
    if not 0 <= choice < len(described):
        raise ProviderError(f"LLM picked candidate {choice}, but only {len(described)} exist")
    candidate, description, transcript = described[choice]
    return Pick(candidate, description, transcript, result.data["reason"])
