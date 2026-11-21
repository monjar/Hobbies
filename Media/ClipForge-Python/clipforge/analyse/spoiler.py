"""Spoiler gate (PLAN.md constraint 4): endings, isolated cutscenes and major reveals never ship."""

from dataclasses import dataclass

from ..costs import CostTracker
from ..errors import ProviderError
from ..providers.base import LLMProvider

SAFE = "none"
BLOCKED = ("ending", "isolated_cutscene", "major_story_reveal")

SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": [SAFE, *BLOCKED]},
        "reason": {"type": "string"},
    },
    "required": ["label", "reason"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Verdict:
    label: str
    reason: str

    @property
    def blocked(self) -> bool:
        return self.label != SAFE


def classify(*, description: str, transcript: str, llm: LLMProvider, costs: CostTracker | None) -> Verdict:
    result = llm.complete_json(
        task="spoiler",
        system=(
            "You classify a stretch of video game footage for spoilers before it is published.\n"
            "- ending: the game's ending, final mission, or credits.\n"
            "- isolated_cutscene: mostly a non-interactive story cutscene rather than gameplay.\n"
            "- major_story_reveal: reveals a major plot point, character death, or twist.\n"
            "- none: ordinary gameplay, exploration, mechanics, or details.\n"
            "When the evidence points to a spoiler, choose the spoiler label."
        ),
        user=f"What the frames show: {description}\n\nTranscript: {transcript or '(no speech)'}",
        schema=SCHEMA,
    )
    if costs:
        costs.add(llm.name, "spoiler", result.model, result.usd, result.detail)
    label = result.data["label"]
    if label != SAFE and label not in BLOCKED:
        raise ProviderError(f"spoiler classifier returned an unknown label: {label!r}")
    return Verdict(label, result.data["reason"])
