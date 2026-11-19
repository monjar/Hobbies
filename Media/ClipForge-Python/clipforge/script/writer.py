"""Narration script generation with validation feedback."""

from pathlib import Path
from string import Template

from ..costs import CostTracker
from ..errors import ValidationFailed
from ..log import log
from ..providers.base import LLMProvider
from ..rules import HOOK_MAX_WORDS
from .validate import SCRIPT_SCHEMA, ungrounded_claims, validate_script

PROMPTS = Path(__file__).parent / "prompts"
MAX_ATTEMPTS = 3


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "(none yet)"


def write_script(
    *,
    job_id: str,
    angle: str,
    angle_brief: str,
    description: str,
    transcript: str,
    render_s: float,
    wps: float,
    llm: LLMProvider,
    costs: CostTracker,
    topic_context: str = "",
    winners: list[str] | None = None,
    rejections: list[str] | None = None,
) -> tuple[dict, int]:
    """Return a validated script and the number of attempts it took. Raises if none pass."""
    system = Template((PROMPTS / "system.md").read_text()).substitute(
        hook_max_words=HOOK_MAX_WORDS, wps=wps, target_words=round(render_s * wps * 0.97)
    )
    user_template = Template((PROMPTS / "user.md").read_text())
    # The validator may only accept claims found in these inputs.
    grounding_text = f"Footage description: {description}\nSpeech in the footage: {transcript or '(no speech)'}"

    feedback, errors = "", []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        user = user_template.substitute(
            render_s=f"{render_s:.1f}",
            angle=angle,
            angle_brief=angle_brief,
            description=description,
            transcript=transcript or "(no speech)",
            topic_context=topic_context or "(none)",
            winners=_bullets(winners or []),
            rejections=_bullets(rejections or []),
            feedback=feedback,
        )
        result = llm.complete_json(task="script", system=system, user=user, schema=SCRIPT_SCHEMA)
        costs.add(llm.name, "script", result.model, result.usd, result.detail)
        script = result.data
        errors = validate_script(script, render_s, wps, grounding_text)
        if not errors:
            errors = [f"unsupported claim: {claim}" for claim in ungrounded_claims(script, grounding_text, llm, costs)]
        if not errors:
            return script, attempt
        log("script_rejected", job_id, level="warning", attempt=attempt, errors=errors)
        feedback = "\nYour previous draft was rejected by the validator. Fix these problems:\n" + _bullets(errors)
    raise ValidationFailed([f"no valid script after {MAX_ATTEMPTS} attempts"] + errors)
