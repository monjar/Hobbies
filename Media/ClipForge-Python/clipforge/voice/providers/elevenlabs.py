"""ElevenLabs TTS with character-level timestamps. API shape verified per docs/integrations.md."""

import base64
from pathlib import Path

import httpx

from ...config import TTSCfg
from ...errors import ProviderError
from ...media import probe
from ...types import Word
from ..tts import TTSResult

BASE_URL = "https://api.elevenlabs.io/v1"


def words_from_alignment(characters: list[str], starts: list[float], ends: list[float]) -> list[Word]:
    """Collapse per-character timings into per-word timings, splitting on whitespace."""
    words, current, word_start, word_end = [], "", 0.0, 0.0
    for char, start, end in zip(characters, starts, ends, strict=True):
        if char.isspace():
            if current:
                words.append(Word(current, word_start, word_end))
                current = ""
            continue
        if not current:
            word_start = start
        current += char
        word_end = end
    if current:
        words.append(Word(current, word_start, word_end))
    return words


class ElevenLabsTTS:
    name = "elevenlabs"

    def __init__(self, api_key: str, cfg: TTSCfg, timeout_s: float = 120.0, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self.model = cfg.model
        self.client = httpx.Client(
            base_url=BASE_URL, headers={"xi-api-key": api_key}, timeout=timeout_s, transport=transport
        )

    def estimate_usd(self, text: str) -> float:
        return len(text) / 1000 * self.cfg.price_per_1k_chars

    def synthesize(self, text: str, out_path: Path, previous_text: str | None = None) -> TTSResult:
        body = {"text": text, "model_id": self.cfg.model}
        if previous_text:
            body["previous_text"] = previous_text
        try:
            response = self.client.post(
                f"/text-to-speech/{self.cfg.voice_id}/with-timestamps",
                params={"output_format": self.cfg.output_format},
                json=body,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"elevenlabs error {exc.response.status_code}: {exc.response.text[:300]}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"elevenlabs connection error: {exc}") from exc

        payload = response.json()
        alignment = payload.get("alignment")
        if not alignment or not payload.get("audio_base64"):
            raise ProviderError("elevenlabs response is missing audio or alignment")
        codec = self.cfg.output_format.split("_")[0]
        out_path = out_path.with_suffix(f".{codec}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(payload["audio_base64"]))
        words = words_from_alignment(
            alignment["characters"],
            alignment["character_start_times_seconds"],
            alignment["character_end_times_seconds"],
        )
        return TTSResult(
            path=out_path,
            duration=probe(out_path).duration,
            words=words,
            usd=self.estimate_usd(text),
            chars=len(text),
        )
