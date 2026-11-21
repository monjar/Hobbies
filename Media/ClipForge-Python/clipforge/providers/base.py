"""Swappable provider interfaces for text and vision models. TTS lives in clipforge/voice/tts.py."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class LLMResult:
    data: dict
    usd: float
    model: str
    detail: dict = field(default_factory=dict)


@dataclass
class VisionResult:
    text: str
    usd: float
    model: str
    detail: dict = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str

    def complete_json(self, *, task: str, system: str, user: str, schema: dict) -> LLMResult:
        """Return JSON matching `schema`. `task` names the call for cost records."""


class VisionProvider(Protocol):
    name: str

    def describe(self, *, frames: list[Path], prompt: str) -> VisionResult:
        """Describe what the frames show, in plain text."""
