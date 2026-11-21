"""Offline stand-ins for every paid provider. Used by tests and by any run without --live."""

import re
from pathlib import Path

from ..errors import ProviderError
from ..media import ffmpeg
from ..types import Word
from ..voice.tts import TTSResult
from .base import LLMResult, VisionResult

_FILLER = "this mock narration only stands in for real script text while providers stay offline".split()


class MockLLM:
    name = "mock"

    def __init__(self, wps: float = 2.6):
        self.wps = wps

    def complete_json(self, *, task: str, system: str, user: str, schema: dict) -> LLMResult:
        if task == "pick_window":
            data = {"choice": 0, "reason": "mock provider always takes the top-scored window"}
        elif task == "spoiler":
            data = {"label": "none", "reason": "mock provider does not classify spoilers"}
        elif task == "grounding":
            data = {"ungrounded_claims": []}
        elif task == "script":
            data = self._script(user)
        else:
            raise ProviderError(f"mock LLM has no response for task {task!r}")
        return LLMResult(data=data, usd=0.0, model="mock")

    def _script(self, user: str) -> dict:
        match = re.search(r"Clip length: ([\d.]+) seconds", user)
        if not match:
            raise ProviderError("mock LLM could not find the clip length in the script prompt")
        duration = float(match.group(1))
        hook = "Mock hook for an offline test render"
        total_words = round(duration * self.wps * 0.97)
        remaining = max(total_words - len(hook.split()), 8)
        lines, per_line, spoken = [], 9, len(hook.split())
        while remaining > 0:
            count = min(per_line, remaining)
            words = [_FILLER[(len(lines) + i) % len(_FILLER)] for i in range(count)]
            lines.append({"text": " ".join(words).capitalize() + ".", "start": round(spoken / self.wps, 2)})
            spoken += count
            remaining -= count
        return {
            "hook": hook,
            "lines": lines,
            "title": "Mock render (providers offline)",
            "description": "Produced with mock providers. Not for publishing.",
            "hashtags": ["#mock"],
        }


class MockVision:
    name = "mock"

    def describe(self, *, frames: list[Path], prompt: str) -> VisionResult:
        return VisionResult(text=f"Mock description of {len(frames)} sampled frames.", usd=0.0, model="mock")


class MockTTS:
    name = "mock"
    model = "mock"

    def __init__(self, wps: float = 2.6):
        self.wps = wps

    def estimate_usd(self, text: str) -> float:
        return 0.0

    def synthesize(self, text: str, out_path: Path, previous_text: str | None = None) -> TTSResult:
        tokens = text.split()
        duration = max(len(tokens) / self.wps, 0.3)
        out_path = out_path.with_suffix(".wav")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # A tone, so the mix, ducking and loudness stages have real signal to work on.
        ffmpeg(["-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={duration:.3f}", out_path])
        step = duration / len(tokens)
        words = [Word(token, round(i * step, 3), round((i + 1) * step, 3)) for i, token in enumerate(tokens)]
        return TTSResult(path=out_path, duration=duration, words=words, usd=0.0, chars=len(text))
