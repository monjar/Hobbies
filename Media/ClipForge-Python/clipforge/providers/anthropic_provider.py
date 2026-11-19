"""Claude as LLMProvider and VisionProvider. API shape verified per docs/integrations.md."""

import base64
import json
from pathlib import Path

import anthropic

from ..config import ModelCfg
from ..errors import ProviderError
from .base import LLMResult, VisionResult

# Lets the API re-run a request on another model if a safety classifier declines it.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, cfg: ModelCfg):
        self.cfg = cfg
        self.client = anthropic.Anthropic(api_key=api_key)

    def _create(self, *, system: str | None, content, output_config: dict):
        kwargs = {"system": system} if system else {}
        try:
            response = self.client.beta.messages.create(
                model=self.cfg.model,
                max_tokens=MAX_TOKENS,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                output_config={"effort": self.cfg.effort, **output_config},
                messages=[{"role": "user", "content": content}],
                **kwargs,
            )
        except anthropic.RateLimitError as exc:
            raise ProviderError(f"anthropic rate limit: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"anthropic connection error: {exc}") from exc
        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            raise ProviderError(f"anthropic declined the request (category: {category})")
        if response.stop_reason == "max_tokens":
            raise ProviderError("anthropic response was cut off at max_tokens")
        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            raise ProviderError("anthropic response contained no text")
        return response, text

    def _cost(self, response) -> tuple[float, dict]:
        usage = response.usage
        usd = (
            usage.input_tokens * self.cfg.price_in_per_mtok + usage.output_tokens * self.cfg.price_out_per_mtok
        ) / 1_000_000
        detail = {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}
        if response.model != self.cfg.model:
            # A fallback model served the request; its rates differ from the configured ones.
            detail["cost_is_estimate"] = f"served by {response.model}, priced as {self.cfg.model}"
        return usd, detail

    def complete_json(self, *, task: str, system: str, user: str, schema: dict) -> LLMResult:
        response, text = self._create(
            system=system,
            content=user,
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"anthropic returned invalid JSON for {task}: {exc}") from exc
        usd, detail = self._cost(response)
        return LLMResult(data=data, usd=usd, model=response.model, detail=detail)

    def describe(self, *, frames: list[Path], prompt: str) -> VisionResult:
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(frame.read_bytes()).decode("ascii"),
                },
            }
            for frame in frames
        ]
        content.append({"type": "text", "text": prompt})
        response, text = self._create(system=None, content=content, output_config={})
        usd, detail = self._cost(response)
        return VisionResult(text=text.strip(), usd=usd, model=response.model, detail=detail)
