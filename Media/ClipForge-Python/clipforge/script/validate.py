"""Script validation: deterministic rules first, then an LLM grounding check."""

import re

from ..costs import CostTracker
from ..providers.base import LLMProvider
from ..rules import HOOK_MAX_WORDS, SPEECH_TOLERANCE

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "start": {"type": "number"}},
                "required": ["text", "start"],
                "additionalProperties": False,
            },
        },
        "title": {"type": "string"},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hook", "lines", "title", "description", "hashtags"],
    "additionalProperties": False,
}

GROUNDING_SCHEMA = {
    "type": "object",
    "properties": {"ungrounded_claims": {"type": "array", "items": {"type": "string"}}},
    "required": ["ungrounded_claims"],
    "additionalProperties": False,
}

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def narration_lines(script: dict) -> list[tuple[str, float]]:
    """Everything that is spoken, in order: the hook at 0, then each line at its target start."""
    return [(script["hook"], 0.0)] + [(line["text"], float(line["start"])) for line in script["lines"]]


def word_count(text: str) -> int:
    return len(text.split())


def estimate_speech_s(script: dict, wps: float) -> float:
    return sum(word_count(text) for text, _ in narration_lines(script)) / wps


def validate_script(script: dict, render_s: float, wps: float, grounding_text: str) -> list[str]:
    """Deterministic checks. Returns a list of problems; empty means the script passes."""
    errors = []
    hook = script["hook"].strip()
    if not hook:
        errors.append("hook is empty")
    elif word_count(hook) > HOOK_MAX_WORDS:
        errors.append(f"hook is {word_count(hook)} words; the limit is {HOOK_MAX_WORDS}")
    elif len(re.findall(r"[.!?]+(?=\s|$)", hook)) > 1:
        errors.append("hook must be a single sentence")

    if not script["lines"]:
        errors.append("script has no lines after the hook")
    if any(not line["text"].strip() for line in script["lines"]):
        errors.append("script has an empty line")
    starts = [start for _, start in narration_lines(script)]
    if any(later < earlier for earlier, later in zip(starts, starts[1:])):
        errors.append("line start times must not go backwards")
    if any(start < 0 or start >= render_s for start in starts):
        errors.append(f"every line must start inside the clip (0 to {render_s:.1f}s)")

    speech = estimate_speech_s(script, wps)
    low, high = render_s * (1 - SPEECH_TOLERANCE), render_s * (1 + SPEECH_TOLERANCE)
    if not low <= speech <= high:
        total = sum(word_count(text) for text, _ in narration_lines(script))
        errors.append(
            f"narration is {total} words (~{speech:.1f}s); it must take {low:.1f}-{high:.1f}s, "
            f"which is {round(low * wps)}-{round(high * wps)} words"
        )

    if not script["title"].strip():
        errors.append("title is empty")
    if any(not re.fullmatch(r"#\w+", tag) for tag in script["hashtags"]):
        errors.append("each hashtag must be a single word starting with #")

    spoken = " ".join(text for text, _ in narration_lines(script))
    known = set(_NUMBER.findall(grounding_text))
    unknown = sorted(set(_NUMBER.findall(spoken)) - known)
    if unknown:
        errors.append(f"numbers not present in the inputs: {', '.join(unknown)}")
    return errors


def ungrounded_claims(script: dict, grounding_text: str, llm: LLMProvider, costs: CostTracker) -> list[str]:
    """Ask the LLM to list factual claims about the game that the inputs do not support."""
    spoken = "\n".join(text for text, _ in narration_lines(script))
    result = llm.complete_json(
        task="grounding",
        system=(
            "You fact-check narration for a gaming video against the only evidence available. "
            "List every factual claim about the game or its footage that the evidence does not support. "
            "Opinions, questions and descriptions that match the evidence are fine. "
            "Return an empty list when everything is supported."
        ),
        user=f"Evidence:\n{grounding_text}\n\nNarration:\n{spoken}",
        schema=GROUNDING_SCHEMA,
    )
    costs.add(llm.name, "grounding", result.model, result.usd, result.detail)
    return [claim for claim in result.data["ungrounded_claims"] if claim.strip()]
